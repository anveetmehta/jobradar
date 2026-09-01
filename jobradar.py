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
  python3 jobradar.py tailor 3             tailor a one-page resume + cover letter for
                                            result #3 from your last scan (or pass a URL)
  python3 jobradar.py watch                poll target_companies for new postings, notify,
                                            and auto-tailor materials the moment one appears
  python3 jobradar.py watch --once         a single poll pass — for your own cron/launchd
"""
import argparse
import datetime
import errno
import json
import os
import re
import sys
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from sources import ats, index_source, jobspy_source, description  # noqa: E402
import match  # noqa: E402
import resume  # noqa: E402
import fit  # noqa: E402
import notify  # noqa: E402


class ConfigError(Exception):
    """Raised by load_config/load_profile instead of calling sys.exit directly.

    Both functions are called not just from CLI commands but from webapp.py's
    request handlers and its background scan-worker thread. sys.exit() raises
    SystemExit, which does NOT subclass Exception — it would silently slip past
    every `except Exception` handler in webapp.py instead of being reported,
    leaving a background scan stuck on "running" forever with no error shown.
    Raising a plain Exception subclass here lets webapp.py catch it normally;
    main() converts it to sys.exit(str(e)) for the CLI path, so CLI behavior
    is unchanged.
    """


def _json_error(path, e):
    return ConfigError(
        f"{path} has invalid JSON at line {e.lineno}, column {e.colno}: {e.msg}\n"
        f"If you hand-edited this file, check for a missing comma or unmatched brace "
        f"near that line. Or delete it and re-run `python3 jobradar.py init` (or the "
        f"setup page) to start clean.")


def load_config(path):
    if not Path(path).exists():
        raise ConfigError(f"No config at {path}. Run `python3 jobradar.py init` first.")
    try:
        return json.loads(Path(path).read_text())
    except json.JSONDecodeError as e:
        raise _json_error(path, e) from e


def load_profile(cfg, base_dir):
    pfile = base_dir / cfg.get("profile_file", "profile.json")
    if not pfile.exists():
        raise ConfigError(f"No profile at {pfile}. Run `python3 jobradar.py init` first.")
    try:
        return json.loads(pfile.read_text())
    except json.JSONDecodeError as e:
        raise _json_error(pfile, e) from e


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


def run_scan(cfg, base_dir, profile, fast=False, fresh=False, out_path="data/results.json",
            progress=None):
    """The whole fetch -> filter -> score -> rank -> write pipeline, usable from
    the CLI (cmd_scan) or a background thread (webapp.py's /api/scan). `progress`,
    if given, is called with a short status string at each phase — cmd_scan wires
    it to a stderr print, webapp.py wires it to a pollable state dict."""
    def report(msg):
        if progress:
            progress(msg)

    profile_text = profile_to_text(profile)
    src_cfg = cfg.get("sources", {})
    cache_dir = base_dir / ".cache"

    report("fetching sources...")
    jobs, board_errors = [], []
    if src_cfg.get("index", True):
        jobs += index_source.load(cache_dir, cfg.get("location_filter"), fresh=fresh)
    if src_cfg.get("ats", True) and cfg.get("ats_companies"):
        found, board_errors = ats.fetch_all(cfg["ats_companies"])
        jobs += found
    if src_cfg.get("jobspy", False):
        jcfg = cfg.get("jobspy", {})
        jobs += jobspy_source.load(jcfg.get("search_terms", []), jcfg.get("location", ""))

    report(f"{len(jobs)} raw postings — filtering...")
    candidates = dedupe(prescreen_filter(jobs, cfg))

    skills = profile.get("skills", [])
    for j in candidates:
        j["_pre"] = prescreen_score(j, skills)
    candidates.sort(key=lambda j: -j["_pre"])

    ai_cfg = cfg.get("ai", {})
    max_score = 0 if fast else ai_cfg.get("max_jobs_to_score", 60)
    to_score, rest = candidates[:max_score], candidates[max_score:]
    api_key = _ai_api_key(ai_cfg)

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
        report(f"Scoring {len(to_score)} roles against your profile using "
              f"{ai_cfg.get('backend','ollama')}/{ai_cfg.get('model','?')} — this can take "
              f"a few minutes on a local model, faster on a cloud API...")
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

    out = base_dir / out_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "profile_headline": profile.get("headline", ""),
        "ai_backend": f"{ai_cfg.get('backend','ollama')}/{ai_cfg.get('model','')}"
                     if not fast else "none (--fast)",
        "count": len(kept),
        "jobs": kept,
    }, indent=2))
    report(f"wrote {len(kept)} ranked roles")
    return {"kept": kept, "out_path": out, "ai_errors": errs, "board_errors": board_errors}


def cmd_scan(args):
    cfg = load_config(args.config)
    base_dir = Path(args.config).parent
    profile = load_profile(cfg, base_dir)

    result = run_scan(cfg, base_dir, profile, fast=args.fast, fresh=args.fresh,
                      out_path=args.out, progress=lambda m: print(f"[scan] {m}", file=sys.stderr))

    if result["ai_errors"]:
        print(f"[scan] {result['ai_errors']} AI call(s) failed and fell back to prescreen "
              f"ranking — see ai_error in results.json", file=sys.stderr)
    if result["board_errors"]:
        print(f"[scan] ATS boards that didn't respond: {', '.join(result['board_errors'])}",
              file=sys.stderr)
    print(f"\n[scan] wrote {len(result['kept'])} ranked roles -> {result['out_path']}")

    for j in result["kept"][:15]:
        badge = " [TARGET]" if j["is_target"] else ""
        score = f"{j['ai_score']}" if j["ai_score"] is not None else f"~{j['rank_score']:.0f}*"
        print(f"  {score:>4}  {j['title'][:55]:<55} {j['company']:<20}{badge}")
    print("\nRun `python3 jobradar.py serve` to browse the full ranked list with reasoning.")


def _slug(s):
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-") or "role"


def _resolve_job(ref, base_dir, results_path):
    """ref is either an http(s) URL, or a 1-based index into the last scan's
    ranked results.json."""
    if ref.startswith("http://") or ref.startswith("https://"):
        return {"title": "", "company": "", "location": "", "url": ref, "source": "manual"}
    rpath = base_dir / results_path
    if not rpath.exists():
        sys.exit(f"No {rpath} yet — run `python3 jobradar.py scan` first, or pass a full URL.")
    jobs = json.loads(rpath.read_text()).get("jobs", [])
    try:
        idx = int(ref) - 1
        assert 0 <= idx < len(jobs)
    except (ValueError, AssertionError):
        sys.exit(f"'{ref}' isn't a valid result number (1-{len(jobs)}) or a URL.")
    return jobs[idx]


def _ai_api_key(ai_cfg):
    if ai_cfg.get("backend") == "anthropic":
        return os.environ.get("ANTHROPIC_API_KEY")
    if ai_cfg.get("backend") == "openai":
        return os.environ.get("OPENAI_API_KEY")
    return None


def tailor_job(job_rec, profile, profile_text, cfg, base_dir, out_dir_name="output"):
    """Shared by `tailor` and `watch --auto-tailor`. Returns a report dict;
    never raises — a failure is reported, not a crash, since `watch` must
    keep polling regardless of one bad AI call."""
    ai_cfg = cfg.get("ai", {})
    api_key = _ai_api_key(ai_cfg)
    desc = description.fetch(job_rec["url"]) if job_rec.get("url") else ""

    resume_content = resume.build_resume(profile_text, job_rec, desc, ai_cfg, api_key)
    if resume_content.get("error"):
        return {"error": resume_content["error"]}

    removed_skills = resume.validate_resume(resume_content, profile)
    letter_content = resume.build_cover_letter(profile_text, job_rec, desc, ai_cfg, api_key)
    claim_flags = []
    if not letter_content.get("error"):
        claim_flags = resume.scan_for_unverified_claims(
            " ".join([letter_content.get("opening", "")] + letter_content.get("body", [])
                    + [letter_content.get("closing", ""), letter_content.get("acknowledge_gap", "")]),
            profile)

    out_dir = base_dir / out_dir_name
    out_dir.mkdir(parents=True, exist_ok=True)
    stub = f"{_slug(job_rec.get('company', 'company'))}_{_slug(job_rec.get('title', 'role'))}"

    resume_report = fit.fit_resume(profile, resume_content, out_dir / f"{stub}_resume.html",
                                   title=f"{profile.get('name','')} — resume")
    letter_report = None
    if not letter_content.get("error"):
        today = datetime.date.today().strftime("%d %B %Y")
        letter_report = fit.fit_cover_letter(profile, letter_content, job_rec,
                                             out_dir / f"{stub}_cover_letter.html",
                                             date_str=today)

    return {"resume_report": resume_report, "letter_content_error": letter_content.get("error"),
            "letter_report": letter_report, "removed_skills": removed_skills,
            "claim_flags": claim_flags,
            "unmet_requirements": resume_content.get("unmet_requirements", [])}


def _print_tailor_report(report):
    if report.get("error"):
        print(f"Could not generate resume content: {report['error']}\n"
             f"Check your ai.backend config — for Ollama, is `ollama serve` running and "
             f"the model pulled? For Anthropic/OpenAI, is the API key env var set?")
        return
    if report["removed_skills"]:
        print(f"[resume] removed {len(report['removed_skills'])} skill(s) the model added "
             f"that aren't in your profile: {', '.join(report['removed_skills'])}")
    if report["claim_flags"]:
        print("[cover letter] WARNING — possibly unverified experience claim(s), check "
              "before sending:")
        for c in report["claim_flags"]:
            print(f'    "{c}"')

    r = report["resume_report"]
    cuts_note = f", {r['cuts_made']} cut(s)" if r["cuts_made"] else ""
    print(f"\n[resume] {r['pages'] or '?'} page(s), {r['density']} density{cuts_note} "
         f"-> {r['html_path']}")
    for w in r["warnings"]:
        print(f"  ! {w}")

    if report["letter_content_error"]:
        print(f"\n[cover letter] generation failed: {report['letter_content_error']}")
    elif report["letter_report"]:
        c = report["letter_report"]
        cuts_note = f", {c['cuts_made']} cut(s)" if c["cuts_made"] else ""
        print(f"\n[cover letter] {c['pages'] or '?'} page(s), {c['density']} density"
             f"{cuts_note} -> {c['html_path']}")
        for w in c["warnings"]:
            print(f"  ! {w}")

    if report["unmet_requirements"]:
        print("\n[unmet requirements — the posting asks for these, your profile doesn't "
              "support them]")
        for g in report["unmet_requirements"]:
            print(f"  - {g}")

    print("\nOpen the .html file(s) in a browser to review before sending. If a .pdf was "
         "produced alongside it, that's your verified one-page output; otherwise print to "
         "PDF yourself with margins set to None.")


def cmd_tailor(args):
    cfg = load_config(args.config)
    base_dir = Path(args.config).parent
    profile = load_profile(cfg, base_dir)
    profile_text = profile_to_text(profile)
    ai_cfg = cfg.get("ai", {})

    job_rec = _resolve_job(args.ref, base_dir, args.results)
    if not job_rec.get("title") or not job_rec.get("company"):
        print("[tailor] fetching posting to identify role/company...", file=sys.stderr)
    print(f"[tailor] {job_rec.get('title') or '(unknown title)'} @ "
          f"{job_rec.get('company') or '(unknown company)'} — generating with "
          f"{ai_cfg.get('backend','ollama')}/{ai_cfg.get('model','')}...", file=sys.stderr)

    report = tailor_job(job_rec, profile, profile_text, cfg, base_dir, args.out_dir)
    _print_tailor_report(report)
    if report.get("error"):
        sys.exit(1)


def _load_seen(path):
    if not path.exists():
        return set()
    try:
        return set(json.loads(path.read_text()).get("urls", []))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_seen(path, urls):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"updated": datetime.datetime.now().isoformat(timespec="seconds"),
                                "urls": sorted(urls)}, indent=1))


def cmd_watch(args):
    cfg = load_config(args.config)
    base_dir = Path(args.config).parent
    profile = load_profile(cfg, base_dir)
    profile_text = profile_to_text(profile)
    targets = cfg.get("target_companies", [])
    ats_companies = cfg.get("ats_companies", [])
    wcfg = cfg.get("watch", {})
    auto_tailor = wcfg.get("auto_tailor", True)
    interval_min = args.interval or wcfg.get("poll_interval_minutes", 60)
    seen_path = base_dir / "data" / "seen_urls.json"

    if not targets:
        sys.exit("config.json's target_companies is empty — `watch` has nothing to alert on. "
                 "Add the companies you're waiting on there.")
    watched_ats = [c for c in ats_companies if is_target_company(
        {"company": c["name"]}, targets)]
    if not watched_ats:
        print("[watch] WARNING: none of your target_companies appear in ats_companies — "
             "watch polls ats_companies directly (fast, live), not the daily index. Add each "
             "target company's board there or these companies will never be checked.",
             file=sys.stderr)

    seen = _load_seen(seen_path)

    def one_pass():
        jobs, errors = ats.fetch_all(ats_companies)
        candidates = dedupe(prescreen_filter(jobs, cfg))
        new_hits = [j for j in candidates
                   if j["url"] and j["url"] not in seen and is_target_company(j, targets)]
        for j in new_hits:
            msg = f"{j['title']} — {j['company']} ({j['location'] or 'location n/a'})\n{j['url']}"
            notify.alert(cfg, "jobradar: new role at a company you're watching", msg)
            if auto_tailor:
                print(f"[watch] auto-tailoring for {j['company']}...", file=sys.stderr)
                report = tailor_job(j, profile, profile_text, cfg, base_dir, args.out_dir)
                _print_tailor_report(report)
        seen.update(j["url"] for j in candidates if j["url"])
        _save_seen(seen_path, seen)
        if errors:
            print(f"[watch] boards not responding: {', '.join(errors)}", file=sys.stderr)
        return new_hits

    if args.once:
        hits = one_pass()
        print(f"[watch] done — {len(hits)} new target-company match(es).")
        return

    print(f"[watch] polling every {interval_min} min for new roles at: {', '.join(targets)}. "
         f"Ctrl+C to stop.")
    try:
        while True:
            hits = one_pass()
            if not hits:
                print(f"[watch] {datetime.datetime.now().strftime('%H:%M')} — nothing new.")
            time.sleep(interval_min * 60)
    except KeyboardInterrupt:
        print("\n[watch] stopped")


def cmd_serve(args):
    os.chdir(HERE)
    import webapp  # local import: webapp imports this module back, safe once main() is running
    try:
        httpd = ThreadingHTTPServer(("localhost", args.port), webapp.Handler)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            sys.exit(f"Could not start on port {args.port} — it's already in use (maybe "
                    f"jobradar is already running?). Try a different port:\n"
                    f"  python3 jobradar.py serve --port {args.port + 1}")
        sys.exit(f"Could not start the server on port {args.port}: {e}")
    with httpd:
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

    sub.add_parser("init", help="create config.json + profile.json from the examples")

    sp = sub.add_parser("scan")
    sp.add_argument("--out", default="data/results.json")
    sp.add_argument("--fast", action="store_true", help="skip AI scoring")
    sp.add_argument("--fresh", action="store_true", help="bypass the cached index")

    sub.add_parser("verify", help="health-check the job boards listed in ats_companies")

    se = sub.add_parser("serve")
    se.add_argument("--port", type=int, default=8765)

    ta = sub.add_parser("tailor", help="generate a tailored one-page resume + cover letter")
    ta.add_argument("ref", help="a result number from the last `scan` (e.g. 3), or a full "
                                "job posting URL")
    ta.add_argument("--out-dir", default="output")
    ta.add_argument("--results", default="data/results.json")

    wa = sub.add_parser("watch", help="poll target_companies and alert + auto-tailor on new roles")
    wa.add_argument("--once", action="store_true", help="one poll pass, then exit (for cron/launchd)")
    wa.add_argument("--interval", type=int, default=None,
                    help="minutes between polls (default: config.json watch.poll_interval_minutes)")
    wa.add_argument("--out-dir", default="output")

    args = ap.parse_args()
    try:
        {"init": cmd_init, "scan": cmd_scan, "verify": cmd_verify, "serve": cmd_serve,
         "tailor": cmd_tailor, "watch": cmd_watch}[args.cmd](args)
    except ConfigError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
