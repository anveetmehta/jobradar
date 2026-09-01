#!/usr/bin/env python3
"""jobradar — local, AI-matched job discovery.

Pulls open roles from public job-board APIs and a large daily-refreshed
index, matches each candidate against YOUR profile using an LLM (a free
local model via Ollama, or your own Anthropic/OpenAI API key), and writes a
ranked, explained shortlist you browse in a local web page. No login, no
scraping by default, no auto-applying — ever.

Usage:
  python3 jobradar.py init                 copy the example config/profile to get started
  python3 jobradar.py scan                 fetch, AI-score, and rank — writes data/results.json
  python3 jobradar.py scan --fast          skip AI scoring (keyword prescreen only, instant)
  python3 jobradar.py scan --fresh         bypass the cached index download
  python3 jobradar.py verify               health-check the ATS boards in your config
  python3 jobradar.py serve                serve the local web UI at http://localhost:8765
"""
import argparse
import json
import os
import re
import sys
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sources import ats, index_source, jobspy_source, description  # noqa: E402
import match  # noqa: E402


def load_config(path):
    if not Path(path).exists():
        sys.exit(f"No config at {path}. Run `python3 jobradar.py init` first.")
    return json.loads(Path(path).read_text())


def load_profile(cfg, base_dir):
    pfile = base_dir / cfg.get("profile_file", "profile.json")
    if not pfile.exists():
        sys.exit(f"No profile at {pfile}. Run `python3 jobradar.py init` first.")
    return json.loads(pfile.read_text())


def profile_to_text(p):
    lines = [f"Name: {p.get('name','')}", f"Headline: {p.get('headline','')}",
             f"Years of experience: {p.get('years_experience','?')}", "",
             f"Summary: {p.get('summary','')}", ""]
    if p.get("skills"):
        lines.append("Skills: " + ", ".join(p["skills"]))
    for e in p.get("experience", []):
        lines.append(f"\n{e.get('title','')} — {e.get('company','')} "
                     f"({e.get('start','')} to {e.get('end','Present')})")
        for h in e.get("highlights", []):
            lines.append(f"  - {h}")
    if p.get("education"):
        lines.append("\nEducation: " + "; ".join(p["education"]))
    if p.get("notes_for_ai"):
        lines.append(f"\nCandidate notes: {p['notes_for_ai']}")
    return "\n".join(lines)


def dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        key = (re.sub(r"[^a-z0-9]", "", j["company"].lower()),
               re.sub(r"[^a-z0-9]", "", j["title"].lower()))
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def prescreen_filter(jobs, cfg):
    inc = [w.lower() for w in cfg.get("title_include", [])]
    exc = [w.lower() for w in cfg.get("title_exclude", [])]
    loc = [w.lower() for w in cfg.get("location_filter", [])]
    out = []
    for j in jobs:
        t = j["title"].lower()
        if inc and not any(w in t for w in inc):
            continue
        if exc and any(w in t for w in exc):
            continue
        if loc and not any(w in (j["location"] or "").lower() for w in loc):
            continue
        out.append(j)
    return out


def prescreen_score(job_rec, skills):
    text = f"{job_rec['title']} {job_rec['team']}".lower()
    return sum(1 for s in skills if s.lower() in text)


def is_target_company(job_rec, targets):
    name = job_rec["company"].lower()
    return any(t.lower() in name for t in targets)


def cmd_init(args):
    for src, dst in ((HERE / "examples" / "config.example.json", HERE / "config.json"),
                     (HERE / "examples" / "profile.example.json", HERE / "profile.json")):
        if dst.exists():
            print(f"skip (exists): {dst.name}")
            continue
        dst.write_text(src.read_text())
        print(f"created: {dst.name}")
    print("\nEdit config.json (target companies, location, AI backend) and profile.json "
          "(your real background), then run: python3 jobradar.py scan")


def cmd_verify(args):
    cfg = load_config(args.config)
    results = ats.verify(cfg.get("ats_companies", []))
    broken, empty = [], []
    for c, status, n in results:
        if status != "200":
            tag, broken = f"BROKEN({status})", broken + [f"{c['name']}[{status}]"]
        elif n == 0:
            tag, empty = "empty", empty + [c["name"]]
        else:
            tag = "ok"
        print(f"{tag:<14} {c['name']:<20} {c['ats']:<16} {n:>4} postings")
    print(f"\n{len(results)-len(broken)-len(empty)}/{len(results)} boards returning postings")
    if empty:
        print(f"live, no open roles: {', '.join(empty)}")
    if broken:
        print(f"bad slug / unreachable: {', '.join(broken)}")


def cmd_scan(args):
    cfg = load_config(args.config)
    base_dir = Path(args.config).parent
    profile = load_profile(cfg, base_dir)
    profile_text = profile_to_text(profile)
    src_cfg = cfg.get("sources", {})
    cache_dir = base_dir / ".cache"

    jobs, board_errors = [], []
    if src_cfg.get("index", True):
        jobs += index_source.load(cache_dir, cfg.get("location_filter"), fresh=args.fresh)
    if src_cfg.get("ats", True) and cfg.get("ats_companies"):
        found, board_errors = ats.fetch_all(cfg["ats_companies"])
        jobs += found
    if src_cfg.get("jobspy", False):
        jcfg = cfg.get("jobspy", {})
        jobs += jobspy_source.load(jcfg.get("search_terms", []),
                                   jcfg.get("location", ""))

    print(f"[scan] {len(jobs)} raw postings from enabled sources", file=sys.stderr)
    candidates = dedupe(prescreen_filter(jobs, cfg))
    print(f"[scan] {len(candidates)} after title/location filter + dedupe", file=sys.stderr)

    skills = profile.get("skills", [])
    for j in candidates:
        j["_pre"] = prescreen_score(j, skills)
    candidates.sort(key=lambda j: -j["_pre"])

    ai_cfg = cfg.get("ai", {})
    max_score = 0 if args.fast else ai_cfg.get("max_jobs_to_score", 60)
    to_score, rest = candidates[:max_score], candidates[max_score:]

    api_key = None
    if ai_cfg.get("backend") == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    elif ai_cfg.get("backend") == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")

    def score_one(j):
        desc = description.fetch(j["url"]) if j["url"] else ""
        r = match.score_job(profile_text, j, desc, ai_cfg, api_key)
        j["ai_score"] = r.get("score")
        j["ai_verdict"] = r.get("verdict", "")
        j["ai_why"] = r.get("why", "")
        j["ai_gaps"] = r.get("gaps", [])
        j["ai_error"] = r.get("error")
        return j

    if to_score:
        print(f"[scan] AI-scoring {len(to_score)} candidates via "
              f"{ai_cfg.get('backend','ollama')}/{ai_cfg.get('model','?')}...", file=sys.stderr)
        with ThreadPoolExecutor(max_workers=ai_cfg.get("workers", 4)) as ex:
            to_score = list(ex.map(score_one, to_score))

    for j in rest:
        j["ai_score"], j["ai_verdict"], j["ai_why"], j["ai_gaps"], j["ai_error"] = (
            None, "", "", [], None)

    all_jobs = to_score + rest
    targets = cfg.get("target_companies", [])
    for j in all_jobs:
        j["is_target"] = is_target_company(j, targets)
        j["rank_score"] = (j["ai_score"] if j["ai_score"] is not None
                          else min(95, j["_pre"] * 12))
        j.pop("_pre", None)

    min_score = ai_cfg.get("min_score", 0)
    kept = [j for j in all_jobs if (j["ai_score"] is None or j["ai_score"] >= min_score)]
    kept.sort(key=lambda j: (-j["is_target"], -j["rank_score"]))

    errs = sum(1 for j in to_score if j.get("ai_error"))
    if errs:
        print(f"[scan] {errs}/{len(to_score)} AI calls failed and fell back to "
              f"prescreen ranking — see ai_error in results.json", file=sys.stderr)
    if board_errors:
        print(f"[scan] ATS boards that didn't respond: {', '.join(board_errors)}",
              file=sys.stderr)

    out_path = base_dir / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "profile_headline": profile.get("headline", ""),
        "ai_backend": f"{ai_cfg.get('backend','ollama')}/{ai_cfg.get('model','')}"
                     if not args.fast else "none (--fast)",
        "count": len(kept),
        "jobs": kept,
    }, indent=2))
    print(f"\n[scan] wrote {len(kept)} ranked roles -> {out_path}")

    for j in kept[:15]:
        badge = " [TARGET]" if j["is_target"] else ""
        score = f"{j['ai_score']}" if j["ai_score"] is not None else f"~{j['rank_score']:.0f}*"
        print(f"  {score:>4}  {j['title'][:55]:<55} {j['company']:<20}{badge}")
    print("\nRun `python3 jobradar.py serve` to browse the full ranked list with reasoning.")


def cmd_serve(args):
    os.chdir(HERE)
    handler = SimpleHTTPRequestHandler
    with ThreadingHTTPServer(("localhost", args.port), handler) as httpd:
        url = f"http://localhost:{args.port}/web/index.html"
        print(f"Serving jobradar at {url}  (Ctrl+C to stop)")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(HERE / "config.json"))
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    sp = sub.add_parser("scan")
    sp.add_argument("--out", default="data/results.json")
    sp.add_argument("--fast", action="store_true", help="skip AI scoring")
    sp.add_argument("--fresh", action="store_true", help="bypass the cached index")

    sub.add_parser("verify")

    se = sub.add_parser("serve")
    se.add_argument("--port", type=int, default=8765)

    args = ap.parse_args()
    {"init": cmd_init, "scan": cmd_scan, "verify": cmd_verify, "serve": cmd_serve}[args.cmd](args)


if __name__ == "__main__":
    main()
