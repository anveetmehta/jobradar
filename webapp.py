"""The local web app behind `jobradar.py serve`.

Static files (web/, data/, output/, docs/) plus a handful of JSON API routes
so the whole loop — set up your profile, run a scan, browse matches, get a
tailored resume/cover letter — happens in the browser, no CLI required.

  GET  /api/config          -> is a profile/config set up; redacted summary +
                               (when configured) the full config/profile for
                               the setup form to pre-fill from
  POST /api/setup           -> writes config.json + profile.json from the setup form
  POST /api/parse-resume    -> extracts text from an uploaded resume file and
                               asks the AI backend to structure it, for the
                               setup form to pre-fill from (never written to
                               disk directly — the human reviews it first)
  POST /api/resolve-companies -> merges hand-resolved {name,slug,ats} entries
                               into ats_companies, for target companies the
                               automatic match in /api/setup couldn't place
  POST /api/scan            -> starts a scan in the background ({"fast": bool})
  GET  /api/scan/status     -> poll while a scan runs: {status, message, count}
  POST /api/tailor          -> starts the tailor pipeline for one job in the background
  GET  /api/tailor/status   -> poll while it runs: {status, message, result, error}
  POST /api/test-ai         -> checks the configured AI backend is actually reachable
  POST /api/pull-model      -> starts `ollama pull <model>` in the background
  GET  /api/pull-model/status -> poll while a pull runs
  GET  /api/verify          -> health-checks every board in ats_companies,
                               the web equivalent of `jobradar.py verify`
  GET  /files/<path>        -> serves a generated resume/cover-letter/report
                               file from config.json's paths.output_dir,
                               which may live outside HERE entirely

`watch` stays a CLI/cron thing on purpose — it's meant to run for days
unattended, which isn't something a browser tab should be responsible for.
"""
import base64
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

import jobradar as jr
import match
import resume

HERE = Path(__file__).parent
STATIC_ROOTS = ("web", "data", "output", "docs")

_scan_lock = threading.Lock()
_scan_state = {"status": "idle", "message": "", "count": 0, "error": None}

_pull_lock = threading.Lock()
_pull_state = {"status": "idle", "message": "", "error": None}

_tailor_lock = threading.Lock()
_tailor_state = {"status": "idle", "message": "", "result": None, "error": None}


def _friendly_error(e):
    """Translate an exception into something a non-technical user can read
    without losing the original for the terminal (callers still log the raw
    exception to stderr alongside this)."""
    if isinstance(e, jr.ConfigError):
        return str(e)
    if isinstance(e, requests.exceptions.ConnectionError):
        return ("Could not reach your AI backend. If you're using Ollama, is "
                "`ollama serve` running? If Anthropic/OpenAI, check your internet "
                "connection.")
    if isinstance(e, requests.exceptions.Timeout):
        return "The AI backend took too long to respond. Try again, or check it's not overloaded."
    if isinstance(e, json.JSONDecodeError):
        return ("One of your config files has invalid JSON — see the terminal running "
                "jobradar for details, or re-run setup to regenerate it.")
    if isinstance(e, KeyError):
        return (f"Your config.json is missing an expected field: {e}. Try re-running "
                f"setup, or compare against examples/config.example.json.")
    return f"Something went wrong ({type(e).__name__}). See the terminal running jobradar for details."


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

    def _send_file(self, fs_path):
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
        self._send_file(fs_path)

    def _serve_output_file(self, rel_path):
        """Serves a generated resume/cover-letter/report file from
        config.json's paths.output_dir — which may live entirely outside
        HERE (an absolute path like ~/Documents/Resume), so this can't reuse
        _serve_static's HERE-rooted containment check. Resolves both sides
        and confirms the requested file is actually under that directory
        before serving, to block ../ traversal outside it."""
        try:
            cfg = jr.load_config(str(HERE / "config.json"))
        except jr.ConfigError:
            return self.send_error(404)
        base = jr._resolve_output_dir(cfg, HERE).resolve()
        fs_path = (base / rel_path).resolve()
        if base not in fs_path.parents:
            return self.send_error(403)
        if not fs_path.is_file():
            return self.send_error(404)
        self._send_file(fs_path)

    # ---- routes --------------------------------------------------------
    def do_GET(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/config":
                return self._get_config()
            if route == "/api/scan/status":
                return self._json(200, dict(_scan_state))
            if route == "/api/pull-model/status":
                return self._json(200, dict(_pull_state))
            if route == "/api/tailor/status":
                return self._json(200, dict(_tailor_state))
            if route == "/api/verify":
                return self._get_verify()
            if route.startswith("/files/"):
                return self._serve_output_file(unquote(route[len("/files/"):]))
        except Exception as e:  # noqa: BLE001 — surface it to the UI, don't crash the server
            import traceback
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": _friendly_error(e)})
        self._serve_static()

    def do_POST(self):
        route = urlparse(self.path).path
        try:
            if route == "/api/setup":
                return self._post_setup()
            if route == "/api/resolve-companies":
                return self._post_resolve_companies()
            if route == "/api/scan":
                return self._post_scan()
            if route == "/api/tailor":
                return self._post_tailor()
            if route == "/api/test-ai":
                return self._post_test_ai()
            if route == "/api/pull-model":
                return self._post_pull_model()
            if route == "/api/parse-resume":
                return self._post_parse_resume()
        except Exception as e:  # noqa: BLE001 — surface it to the UI, don't crash the server
            import traceback
            traceback.print_exc(file=sys.stderr)
            return self._json(500, {"error": _friendly_error(e)})
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
                "ats_companies": [c["name"] for c in cfg.get("ats_companies", [])],
            }
            # Full contents for the setup form to pre-fill from (Phase 3). Neither file
            # has ever held a secret — API keys stay in the environment, never written
            # here — so there's nothing newly sensitive about returning this.
            out["config"] = cfg
            out["profile"] = prof
        self._json(200, out)

    # ---- /api/verify -------------------------------------------------------
    def _get_verify(self):
        """The web equivalent of `jobradar.py verify` — same underlying
        ats.verify() call cmd_verify already makes, so a broken/mistyped ATS
        slug is diagnosable from the browser instead of only the CLI.
        ats.verify() already parallelizes its own requests internally
        (ThreadPoolExecutor), so this stays synchronous rather than the
        background-thread+polling pattern used for scan/tailor."""
        cfg = jr.load_config(str(HERE / "config.json"))
        results = jr.ats.verify(cfg.get("ats_companies", []))
        boards = [{"name": c["name"], "ats": c["ats"], "status": status, "count": n}
                  for c, status, n in results]
        self._json(200, {"boards": boards})

    # ---- /api/scan -------------------------------------------------------
    def _post_scan(self):
        body = self._read_json_body()
        fast = bool(body.get("fast", False))

        with _scan_lock:
            if _scan_state["status"] == "running":
                return self._json(409, dict(_scan_state))
            _scan_state.update(status="running", message="starting...", count=0, error=None)

        def worker():
            try:
                cfg = jr.load_config(str(HERE / "config.json"))
                profile = jr.load_profile(cfg, HERE)

                def on_progress(msg):
                    with _scan_lock:
                        _scan_state["message"] = msg

                result = jr.run_scan(cfg, HERE, profile, fast=fast, progress=on_progress)
                with _scan_lock:
                    _scan_state.update(status="done", message="done",
                                       count=len(result["kept"]), error=None)
            except Exception as e:  # noqa: BLE001 — surface it to the UI, don't crash the server
                import traceback
                traceback.print_exc(file=sys.stderr)
                with _scan_lock:
                    _scan_state.update(status="error", error=_friendly_error(e))

        threading.Thread(target=worker, daemon=True).start()
        self._json(202, dict(_scan_state))

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
            if hit:
                matched.append(hit)
            else:
                unmatched.append({"name": name, "needs_slug": True})

        # A previous setup may already have hand-resolved some of these via
        # /api/resolve-companies — carry those forward instead of losing them
        # if the user re-submits the setup form (e.g. editing an unrelated field).
        prior_output_dir = None
        if (HERE / "config.json").exists():
            try:
                prior = json.loads((HERE / "config.json").read_text())
                prior_by_name = {c["name"].lower(): c for c in prior.get("ats_companies", [])}
                still_unmatched = []
                for u in unmatched:
                    hit = prior_by_name.get(u["name"].lower())
                    (matched if hit else still_unmatched).append(hit or u)
                unmatched = still_unmatched
                prior_output_dir = prior.get("paths", {}).get("output_dir")
            except (json.JSONDecodeError, OSError):
                pass  # a broken existing config.json shouldn't block writing a fresh one

        cfg = jr._strip_notes(example_cfg)
        cfg["location_filter"] = [s.strip() for s in body.get("location_filter", []) if s.strip()]
        cfg["title_include"] = [s.strip() for s in body.get("title_include", []) if s.strip()] \
            or cfg["title_include"]
        cfg["target_companies"] = target_companies
        cfg["ats_companies"] = matched
        cfg["ai"]["backend"] = body.get("ai_backend", cfg["ai"]["backend"])
        cfg["ai"]["model"] = body.get("ai_model", cfg["ai"]["model"])
        cfg.setdefault("paths", {})["output_dir"] = (
            body.get("output_dir", "").strip() or prior_output_dir or cfg["paths"]["output_dir"])
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
                         # kept for CI / non-JS consumers; the setup page's real UX for this
                         # is the inline per-company resolve panel, not this string.
                         "note": (f"{len(unmatched)} target compan{'y is' if len(unmatched)==1 else 'ies are'} "
                                 f"not wired to a pollable job board yet — resolve them below, "
                                 f"or edit config.json's ats_companies by hand."
                                 if unmatched else "")})

    # ---- /api/parse-resume -------------------------------------------------
    def _post_parse_resume(self):
        """Extracts text from an uploaded resume (.pdf/.docx/.txt) and asks
        the currently-selected AI backend to structure it into profile.json's
        schema, so the setup form can pre-fill from it. The frontend treats
        the result as a starting point to review and edit, never as a final
        write — no file is ever saved to disk from this endpoint, uploaded
        bytes are held in memory only for the duration of the request."""
        body = self._read_json_body()
        filename = body.get("filename", "")
        file_b64 = body.get("file_b64", "")
        if not filename or not file_b64:
            return self._json(400, {"error": "no file provided"})
        try:
            raw_bytes = base64.b64decode(file_b64, validate=True)
        except Exception:
            return self._json(400, {"error": "could not decode the uploaded file"})
        if len(raw_bytes) > 8 * 1024 * 1024:
            return self._json(200, {"ok": False, "error": "That file is over 8MB — resumes "
                                    "shouldn't need to be this large. Try a plain-text export."})

        text, err = resume.extract_resume_text(filename, raw_bytes)
        if err:
            return self._json(200, {"ok": False, "error": err})

        backend = body.get("ai_backend", "ollama")
        ai_cfg = {"backend": backend, "model": body.get("ai_model", ""),
                 "ollama_host": body.get("ollama_host") or "http://localhost:11434"}

        # Same reachability pre-checks as /api/test-ai, so a disconnected backend
        # degrades to the same plain-language message rather than a raw
        # ConnectionError/RuntimeError string leaking through parse_resume_text.
        if backend == "ollama":
            try:
                requests.get(f"{ai_cfg['ollama_host'].rstrip('/')}/api/tags", timeout=3).raise_for_status()
            except requests.exceptions.RequestException:
                return self._json(200, {"ok": False, "error": "Ollama isn't reachable. Is it "
                                        "installed and running (`ollama serve`)?"})
        elif backend in ("anthropic", "openai"):
            if not jr._ai_api_key(ai_cfg):
                env_var = "ANTHROPIC_API_KEY" if backend == "anthropic" else "OPENAI_API_KEY"
                return self._json(200, {"ok": False, "error": f"{env_var} isn't set in the "
                                        f"environment jobradar is running in."})

        api_key = jr._ai_api_key(ai_cfg)
        parsed = resume.parse_resume_text(text, ai_cfg, api_key)
        if parsed.get("error"):
            return self._json(200, {"ok": False, "error": parsed["error"]})
        return self._json(200, {"ok": True, "profile": parsed})

    # ---- /api/resolve-companies -------------------------------------------
    def _post_resolve_companies(self):
        """Accepts hand-resolved {name, slug, ats} entries for target companies
        /api/setup's automatic match couldn't place, and merges them into
        config.json's ats_companies. Companion to the per-company panel
        /api/setup's `unmatched_companies` response drives."""
        body = self._read_json_body()
        resolved = body.get("companies", [])
        if not resolved:
            return self._json(400, {"error": "no companies provided"})

        cfg = jr.load_config(str(HERE / "config.json"))
        existing = {c["name"].lower() for c in cfg.get("ats_companies", [])}
        added = []
        for c in resolved:
            name, slug, ats = c.get("name", "").strip(), c.get("slug", "").strip(), c.get("ats", "")
            if not (name and slug and ats):
                continue
            if name.lower() in existing:
                continue
            cfg.setdefault("ats_companies", []).append({"name": name, "slug": slug, "ats": ats})
            existing.add(name.lower())
            added.append(name)
        (HERE / "config.json").write_text(json.dumps(cfg, indent=2))
        self._json(200, {"ok": True, "added": added})

    # ---- /api/tailor ---------------------------------------------------
    def _post_tailor(self):
        """Background-thread + polling, same shape as /api/scan (Phase 4c) —
        a real tailor run is two sequential LLM calls plus PDF page-fitting,
        genuinely "slow" by this codebase's own definition, so the old
        synchronous 200/502 response gave the browser nothing to show while
        waiting. Returns 202 immediately; poll GET /api/tailor/status."""
        body = self._read_json_body()
        job_rec = body.get("job")
        if not job_rec or not job_rec.get("url"):
            return self._json(400, {"error": "no job (with a url) provided"})

        with _tailor_lock:
            if _tailor_state["status"] == "running":
                return self._json(409, dict(_tailor_state))
            _tailor_state.update(status="running", message="starting...", result=None, error=None)

        def worker():
            try:
                cfg = jr.load_config(str(HERE / "config.json"))
                profile = jr.load_profile(cfg, HERE)
                profile_text = jr.profile_to_text(profile)

                def on_progress(msg):
                    with _tailor_lock:
                        _tailor_state["message"] = msg

                report = jr.tailor_job(job_rec, profile, profile_text, cfg, HERE,
                                       progress=on_progress)
                if report.get("error"):
                    with _tailor_lock:
                        _tailor_state.update(status="error", error=report["error"])
                    return

                out_base = jr._resolve_output_dir(cfg, HERE).resolve()

                def rel(p):
                    # Not necessarily under HERE — paths.output_dir may point
                    # anywhere (e.g. ~/Documents/Resume) — so this is served
                    # via /files/, not the HERE-rooted static routes.
                    return "/files/" + str(Path(p).resolve().relative_to(out_base)) if p else None

                result = {
                    "ok": True,
                    "company_name": job_rec.get("company", ""),
                    "job_title": job_rec.get("title", ""),
                    "resume_url": rel(report["resume_report"]["html_path"]),
                    "resume_pages": report["resume_report"]["pages"],
                    "resume_warnings": report["resume_report"]["warnings"],
                    "cover_letter_url": rel(report["letter_report"]["html_path"]) if report["letter_report"] else None,
                    "cover_letter_pages": report["letter_report"]["pages"] if report["letter_report"] else None,
                    "cover_letter_warnings": report["letter_report"]["warnings"] if report["letter_report"] else [],
                    "removed_skills": report["removed_skills"],
                    "claim_flags": report["claim_flags"],
                    "unmet_requirements": report["unmet_requirements"],
                }
                with _tailor_lock:
                    _tailor_state.update(status="done", message="done", result=result, error=None)
            except Exception as e:  # noqa: BLE001 — surface it to the UI, don't crash the server
                import traceback
                traceback.print_exc(file=sys.stderr)
                with _tailor_lock:
                    _tailor_state.update(status="error", error=_friendly_error(e))

        threading.Thread(target=worker, daemon=True).start()
        self._json(202, dict(_tailor_state))

    # ---- /api/test-ai ------------------------------------------------------
    def _post_test_ai(self):
        """Checks whether the chosen AI backend is actually usable right now.
        Synchronous, not background-thread+polling like /api/scan — a single
        minimal LLM call is bounded (a few seconds, well under match.py's own
        90s TIMEOUT), so it doesn't need that shape."""
        body = self._read_json_body()
        backend = body.get("backend", "ollama")
        model = body.get("model", "")

        if backend == "ollama":
            host = body.get("ollama_host", "http://localhost:11434")
            try:
                r = requests.get(f"{host.rstrip('/')}/api/tags", timeout=3)
                r.raise_for_status()
            except requests.exceptions.RequestException:
                return self._json(200, {"ok": False, "reachable": False, "model_found": False,
                                        "message": "Ollama isn't reachable. Is it installed and "
                                                   "running (`ollama serve`)?"})
            tags = [t.get("name", "") for t in r.json().get("models", [])]
            model_found = any(t == model or t.split(":")[0] == model.split(":")[0] for t in tags)
            if not model_found:
                return self._json(200, {"ok": False, "reachable": True, "model_found": False,
                                        "message": f"Ollama is running, but `{model}` isn't "
                                                   f"downloaded yet."})
            return self._json(200, {"ok": True, "reachable": True, "model_found": True,
                                    "message": "Connected — ready to go."})

        if backend in ("anthropic", "openai"):
            api_key = jr._ai_api_key({"backend": backend})
            if not api_key:
                env_var = "ANTHROPIC_API_KEY" if backend == "anthropic" else "OPENAI_API_KEY"
                return self._json(200, {"ok": False, "reason": "missing_key",
                                        "message": f"{env_var} isn't set in the environment "
                                                   f"jobradar is running in. Set it in your "
                                                   f"terminal, then restart jobradar."})
            try:
                match.call_llm("Reply with only the single word: ok", "ok",
                               {"backend": backend, "model": model}, api_key)
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 401:
                    return self._json(200, {"ok": False, "reason": "rejected",
                                            "message": "That API key was rejected. Double-check "
                                                       "it was copied correctly, and that your "
                                                       "account has API access enabled (this is "
                                                       "separate from a normal ChatGPT/Claude.ai "
                                                       "subscription)."})
                return self._json(200, {"ok": False, "reason": "error", "message": str(e)})
            except requests.exceptions.RequestException as e:
                return self._json(200, {"ok": False, "reason": "network",
                                        "message": f"Could not reach the API: {e}"})
            return self._json(200, {"ok": True, "message": "Connected."})

        return self._json(400, {"error": f"unknown backend {backend!r}"})

    # ---- /api/pull-model -----------------------------------------------
    def _post_pull_model(self):
        """Starts `ollama pull <model>` in the background. The setup page shows
        an explicit confirm dialog before ever calling this — it is never
        triggered silently. Mirrors _post_scan's background-thread+polling shape."""
        body = self._read_json_body()
        model = body.get("model", "").strip()
        if not model:
            return self._json(400, {"error": "no model specified"})

        with _pull_lock:
            if _pull_state["status"] == "running":
                return self._json(409, dict(_pull_state))
            _pull_state.update(status="running", message=f"starting pull of {model}...", error=None)

        def worker():
            try:
                proc = subprocess.Popen(["ollama", "pull", model], stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, bufsize=1)
                for line in proc.stdout:
                    line = line.strip()
                    if line:
                        with _pull_lock:
                            _pull_state["message"] = line
                code = proc.wait()
                with _pull_lock:
                    if code == 0:
                        _pull_state.update(status="done", message=f"{model} is ready.", error=None)
                    else:
                        _pull_state.update(status="error",
                                           error=f"`ollama pull {model}` exited with code {code}.")
            except FileNotFoundError:
                with _pull_lock:
                    _pull_state.update(status="error",
                                       error="not_installed: the `ollama` command isn't on PATH.")
            except Exception as e:  # noqa: BLE001
                import traceback
                traceback.print_exc(file=sys.stderr)
                with _pull_lock:
                    _pull_state.update(status="error", error=_friendly_error(e))

        threading.Thread(target=worker, daemon=True).start()
        self._json(202, dict(_pull_state))
