"""The local web app behind `jobradar.py serve`.

Static files (web/, data/, output/, docs/) plus three small JSON API routes
so the whole loop — set up your profile, browse matches, get a tailored
resume/cover letter — happens in the browser. `scan` and `watch` stay
CLI/cron operations on purpose: they can take minutes with a real AI
backend, which is a bad fit for a page waiting on one HTTP response.

  GET  /api/config   -> is a profile/config already set up, and (redacted) what's in it
  POST /api/setup    -> writes config.json + profile.json from the setup form
  POST /api/tailor   -> runs the tailor pipeline for one job, returns file URLs
"""
import json
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

import jobradar as jr

HERE = Path(__file__).parent
STATIC_ROOTS = ("web", "data", "output", "docs")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[serve] {self.address_string()} {fmt % args}\n")

    # ---- helpers -----------------------------------------------------
    def _json(self, status, payload):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode())

    def _serve_static(self):
        path = urlparse(self.path).path.lstrip("/")
        if not path or path == "index.html":
            path = "web/index.html"
        top = path.split("/", 1)[0]
        if top not in STATIC_ROOTS:
            self.send_error(404)
            return
        fs_path = (HERE / path).resolve()
        if HERE.resolve() not in fs_path.parents and fs_path != HERE.resolve():
            self.send_error(403)
            return
        if not fs_path.is_file():
            self.send_error(404)
            return
        ctype = {
            ".html": "text/html", ".js": "application/javascript",
            ".json": "application/json", ".css": "text/css",
            ".png": "image/png", ".pdf": "application/pdf",
        }.get(fs_path.suffix, "application/octet-stream")
        data = fs_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- routes --------------------------------------------------------
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/config":
            return self._get_config()
        self._serve_static()

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/setup":
                return self._post_setup()
            if route == "/api/tailor":
                return self._post_tailor()
        except Exception as e:  # noqa: BLE001 — surface it to the UI, don't crash the server
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})
        self.send_error(404)

    # ---- /api/config -----------------------------------------------------
    def _get_config(self):
        cfg_path, prof_path = HERE / "config.json", HERE / "profile.json"
        configured = cfg_path.exists() and prof_path.exists()
        out = {"configured": configured}
        if configured:
            cfg = json.loads(cfg_path.read_text())
            prof = json.loads(prof_path.read_text())
            out["summary"] = {
                "headline": prof.get("headline", ""),
                "location_filter": cfg.get("location_filter", []),
                "title_include": cfg.get("title_include", []),
                "target_companies": cfg.get("target_companies", []),
                "ai_backend": cfg.get("ai", {}).get("backend", ""),
                "ai_model": cfg.get("ai", {}).get("model", ""),
                "watched_companies": [c["name"] for c in cfg.get("ats_companies", [])],
            }
        self._json(200, out)

    # ---- /api/setup --------------------------------------------------
    def _post_setup(self):
        body = self._read_json_body()
        example_cfg = json.loads((HERE / "examples" / "config.example.json").read_text())

        target_companies = [s.strip() for s in body.get("target_companies", []) if s.strip()]
        known = {c["name"].lower(): c for c in example_cfg.get("ats_companies", [])}
        matched, unmatched = [], []
        for name in target_companies:
            hit = known.get(name.lower())
            if not hit:
                hit = next((c for c in known.values() if name.lower() in c["name"].lower()
                           or c["name"].lower() in name.lower()), None)
            (matched if hit else unmatched).append(hit or name)

        cfg = dict(example_cfg)
        for k in list(cfg.keys()):
            if k.startswith("_"):
                cfg.pop(k)
        cfg["location_filter"] = [s.strip() for s in body.get("location_filter", []) if s.strip()]
        cfg["title_include"] = [s.strip() for s in body.get("title_include", []) if s.strip()] \
            or cfg["title_include"]
        cfg["target_companies"] = target_companies
        cfg["ats_companies"] = matched
        cfg["ai"]["backend"] = body.get("ai_backend", cfg["ai"]["backend"])
        cfg["ai"]["model"] = body.get("ai_model", cfg["ai"]["model"])
        (HERE / "config.json").write_text(json.dumps(cfg, indent=2))

        profile = {
            "name": body.get("name", ""),
            "contact": body.get("contact", {}),
            "headline": body.get("headline", ""),
            "years_experience": body.get("years_experience"),
            "summary": body.get("summary", ""),
            "skills": [s.strip() for s in body.get("skills", []) if s.strip()],
            "experience": body.get("experience", []),
            "education": [s.strip() for s in body.get("education", []) if s.strip()],
            "certifications": [s.strip() for s in body.get("certifications", []) if s.strip()],
            "notes_for_ai": body.get("notes_for_ai", ""),
        }
        (HERE / "profile.json").write_text(json.dumps(profile, indent=2))

        self._json(200, {"ok": True, "unmatched_companies": unmatched,
                         "note": (f"{len(unmatched)} target compan{'y is' if len(unmatched)==1 else 'ies are'} "
                                 f"not wired to a pollable job board yet — edit config.json's "
                                 f"ats_companies to add slugs for them."
                                 if unmatched else "")})

    # ---- /api/tailor ---------------------------------------------------
    def _post_tailor(self):
        body = self._read_json_body()
        job_rec = body.get("job")
        if not job_rec or not job_rec.get("url"):
            return self._json(400, {"error": "no job (with a url) provided"})

        cfg = jr.load_config(str(HERE / "config.json"))
        profile = jr.load_profile(cfg, HERE)
        profile_text = jr.profile_to_text(profile)

        report = jr.tailor_job(job_rec, profile, profile_text, cfg, HERE)
        if report.get("error"):
            return self._json(502, {"error": report["error"]})

        def rel(p):
            return "/" + str(Path(p).relative_to(HERE)) if p else None

        self._json(200, {
            "ok": True,
            "resume_url": rel(report["resume_report"]["html_path"]),
            "resume_pages": report["resume_report"]["pages"],
            "resume_warnings": report["resume_report"]["warnings"],
            "cover_letter_url": rel(report["letter_report"]["html_path"]) if report["letter_report"] else None,
            "cover_letter_pages": report["letter_report"]["pages"] if report["letter_report"] else None,
            "cover_letter_warnings": report["letter_report"]["warnings"] if report["letter_report"] else [],
            "removed_skills": report["removed_skills"],
            "claim_flags": report["claim_flags"],
            "unmet_requirements": report["unmet_requirements"],
        })
