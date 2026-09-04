# jobradar — User Manual

A complete usage reference: installation, the setup wizard, the daily
scan/tailor workflow, `watch`, every config field, the CLI, the web API, and
troubleshooting. For the product pitch and quick overview, see the main
[README](../README.md) — this document assumes you've already decided to use
jobradar and want to know exactly how.

## Contents

1. [Requirements](#requirements)
2. [Installing and starting jobradar](#installing-and-starting-jobradar)
3. [First-time setup](#first-time-setup)
4. [Choosing an AI backend](#choosing-an-ai-backend)
5. [The main board](#the-main-board)
6. [Tailoring a resume and cover letter](#tailoring-a-resume-and-cover-letter)
7. [Your profile.json](#your-profilejson)
8. [config.json reference](#configjson-reference)
9. [The radar: `watch`](#the-radar-watch)
10. [Checking your company boards](#checking-your-company-boards)
11. [CLI reference](#cli-reference)
12. [Web API reference](#web-api-reference)
13. [Troubleshooting](#troubleshooting)
14. [What jobradar deliberately does not do](#what-jobradar-deliberately-does-not-do)

---

## Requirements

- Python 3.9+
- A headless-capable Chrome, Chromium, or Edge already installed (used only
  to measure and verify that generated resumes/cover letters fit one page —
  jobradar never opens a visible browser window for this)
- One AI backend: either [Ollama](https://ollama.com) running locally (free),
  or an Anthropic or OpenAI API key (pay per token)
- **Windows users:** `install.sh` is a bash script and won't run under plain
  `cmd`/PowerShell. Either use Git Bash/WSL, or run the three setup commands
  by hand — see [Installing and starting jobradar](#installing-and-starting-jobradar).

## Installing and starting jobradar

```bash
git clone https://github.com/anveetmehta/jobradar.git
cd jobradar
./install.sh
```

`install.sh` creates a virtual environment if one doesn't exist, installs
dependencies (quiet on success; if it fails, it automatically re-runs
verbosely so you can see why), and starts the server at
`http://localhost:8765`. It's safe to run again any time — that's the normal
way to start jobradar day to day; it won't redo setup work if nothing's
changed. If it says "permission denied," run `chmod +x install.sh` once.

**By hand (any OS, including Windows PowerShell — substitute `.venv\Scripts\`
for `.venv/bin/`):**

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python jobradar.py serve
```

`serve` accepts `--port <N>` if 8765 is already in use — the server tells you
this explicitly and suggests the next port rather than crashing with a raw
traceback.

## First-time setup

With no `config.json`/`profile.json` present, `serve` redirects you straight
to the setup page. Nothing you enter is sent anywhere until you actually run
a scan or tailor a document — both files are gitignored, so your job search
is never committed even if you fork the repo.

**1. Your profile.** At the top of the page, you can optionally **upload an
existing resume** (`.pdf`, `.docx`, or `.txt`, 8MB max) — jobradar extracts
its text and asks your currently-selected AI backend (pick one further down
first if you're not using the default, Ollama) to structure it into the
fields below. This is extraction, not generation: the model is instructed to
leave a field blank rather than guess, and nothing is ever written to disk
from this step — you're asked to confirm before it overwrites anything
you've already typed, and every field stays editable afterward. **Always
review what got extracted before saving** — a misread PDF or an unusual
resume layout can produce a wrong or incomplete field, same caution as
tailored output elsewhere in this tool. If parsing fails (backend
unreachable, unsupported file, no text layer), you get a plain-language
reason and can just fill the form in by hand instead.

Below that: name, contact details (used only to render the resume/cover-letter
header — never sent to an AI backend), headline, years of experience, skills,
work history with concrete highlights, education, certifications, and a
free-text notes field the AI weighs directly (state dealbreakers, seniority
preference, "flag it if the role wants N+ years more than I have," etc.). If
you leave an experience entry with highlights but no company/title filled
in, submission is blocked with an inline error rather than silently dropping
that entry.

**2. What you're looking for.** Location filter, job-title include/exclude
terms, and a list of target companies. Target companies are matched
case-insensitively as substrings and pinned to the top of your results,
badged — but pinning in the results list is separate from `watch` actually
alerting on them (see next point).

**3. AI backend.** Three cards:

- **Ollama (recommended to start)** — free, runs entirely on your machine,
  nothing about your search leaves it. Downloads a one-time model (a few GB)
  the first time.
- **Anthropic (Claude)** / **OpenAI (ChatGPT)** — a few cents per job scanned
  or tailored, faster and more accurate than a free local model. Requires a
  developer API key (not the same as a normal ChatGPT/Claude.ai
  subscription).

Not sure? Pick Ollama — you can switch later from this same page.

For Ollama, pick a model from the curated dropdown. For Anthropic/OpenAI,
**there is no key field in the form on purpose** — jobradar never stores an
API key in a file. Instead you get OS-detected terminal commands:

```bash
# macOS / Linux
export ANTHROPIC_API_KEY=sk-...      # or add to ~/.zshrc / ~/.bashrc to persist
```
```powershell
# Windows PowerShell
$env:ANTHROPIC_API_KEY="sk-..."      # or: setx ANTHROPIC_API_KEY "sk-..." to persist
```

**You must restart jobradar after setting this** — a server already running
in one terminal doesn't pick up an environment variable set in a different
terminal.

**4. Test connection.** Click it before you save.

- Ollama not reachable → OS-specific install instructions (download link or
  `brew install ollama` on macOS, `curl -fsSL https://ollama.com/install.sh
  | sh` on Linux, download + auto-run on Windows), then `ollama serve`.
- Ollama reachable, model not pulled → a **Pull this model** button. Clicking
  it shows a confirm dialog first (it's a multi-GB download) before it
  starts; progress streams live. The equivalent manual command
  (`ollama pull <model>`) is always shown too as a fallback.
- Ollama reachable and model present → green, ready to go.
- Anthropic/OpenAI, key missing or rejected → a message telling you which,
  including a note about the subscription-vs-API-key confusion.
- Anthropic/OpenAI, verified → green.

The primary submit button is soft-gated on a passing test (a hint, not a
hard block) — a **"Save anyway"** option is always available, since a
legitimately offline-first setup (Ollama not started yet) is a normal case.

**5. Unmatched companies.** jobradar doesn't fetch an external company
dataset live during setup (that would add a network dependency and mean
trusting a third-party JSON blob sight-unseen). Any target company it
couldn't automatically wire to a job board shows up in an inline panel:
pick an ATS platform (Greenhouse/Lever/Ashby/SmartRecruiters/"not sure") and
paste the slug from the company's careers-page URL, then **Add**. There's
also a **Check your company boards →** button right there to confirm
everything you've configured so far is actually reachable (see
[Checking your company boards](#checking-your-company-boards)).

**6. Get alerted on new roles (optional).** A copy-paste-ready `watch`
command for this install, plus links to platform scheduler docs
(launchd/cron/Task Scheduler) — see [The radar](#the-radar-watch).

**7. Where to save tailored files (optional).** Defaults to an `output`
folder next to jobradar. Enter an absolute path (or one starting with `~`,
e.g. `~/Documents/Resume/jobradar`) to have every tailored resume and cover
letter land there directly instead, organized into a subfolder per company
and role — see [Tailoring a resume and cover letter](#tailoring-a-resume-and-cover-letter).

Saving writes `config.json` and `profile.json` and takes you to the main
board, which kicks off an instant keyword-only preview scan automatically so
you're never staring at an empty page.

## Choosing an AI backend

| backend | cost | privacy | setup |
|---|---|---|---|
| `ollama` | free | runs entirely on your machine, nothing leaves it | install [Ollama](https://ollama.com), `ollama pull <model>` |
| `anthropic` | pay per token | job descriptions + your profile go to Anthropic | `export ANTHROPIC_API_KEY=...` |
| `openai` | pay per token | job descriptions + your profile go to OpenAI | `export OPENAI_API_KEY=...` |

**Never put an API key in `config.json`** — it's read from your shell
environment only, so it can't end up committed by accident.

Recommended local models (no GPU or paid API required):

| model | size | notes |
|---|---|---|
| `mistral-nemo` | ~7GB | best structured-output reliability in testing; the default |
| `qwen2.5:7b` | ~4.7GB | strong reasoning for its size, smaller download |
| `llama3.1:8b` | ~4.7GB | solid general baseline, widely used |
| `gemma2:9b` | ~5.4GB | good alternative if the others are already busy |

Local models are slower and occasionally return malformed JSON — jobradar
retries the extraction and, on total failure, falls back to a
keyword-prescreen rank for that one posting rather than crashing the run
(check `ai_error` in `data/results.json` for any that fell back).

## The main board

- **Quick preview scan (instant, keyword-only)** — runs automatically after
  setup, and any time you want a fast pass with no AI cost.
- **Full scan (a few minutes)** — real AI scoring and reasoning for each
  posting, up to `ai.max_jobs_to_score` postings (ranked into that budget by
  a cheap keyword prescreen first). Progress updates live; the button text
  shows the actual phase instead of a static time guess.
- Each card shows the AI's numeric score, a visible verdict label (strong /
  possible / weak / unscored — not color-only, for accessibility), the
  reasoning, and any gaps the AI identified. A `~` before the score means it
  was never AI-reviewed (quick preview, or the AI call failed for that
  posting) — a rough keyword guess only.
- Jobs scored below your `ai.min_score` cutoff are counted, not silently
  hidden: a line near the top tells you how many and reminds you where to
  change the cutoff.
- Filters: free-text search, minimum score, target-companies-only. **Clear
  filters** resets all three if a filter leaves you with nothing to see.
- Lost server connection or a failed scan both show a specific message plus
  a **Retry** button — scan failures link to
  [company-board verification](#checking-your-company-boards) when relevant.
- Fully responsive down to phone width; works fine as a browser tab you
  check from your phone if `serve` is reachable on your network.

## Tailoring a resume and cover letter

Click **Tailor resume & cover letter** on any card — optionally fill in the
hiring manager's name first if you know it (used in the cover letter
salutation; leaving it blank keeps the default "Dear Hiring Team,"). This
calls the same pipeline the CLI uses via `/api/tailor`, with live progress
("Generating resume content…", "Generating cover letter…", "Checking it
fits one page…").

The CLI equivalent:

```bash
jobradar.py tailor 3                  # tailor for result #3 from your last `scan`
jobradar.py tailor https://...        # or tailor directly against any posting URL
jobradar.py tailor 3 --out-dir DIR    # override paths.output_dir for this run only
```

The AI reads your `profile.json` and the real job description, then
selects, reorders, and lightly tightens your **existing** bullets and
skills — it does not invent new ones. Output goes to
`<output_dir>/<company>/<role>/resume.html` and `cover_letter.html`,
organized into a subfolder per company and role, plus a sidecar
`resume_report.json` in that same folder with the full structured report
(removed skills, flagged claims, unmet requirements, density used) — a
durable record independent of the transient UI, useful if you're reviewing
files later, e.g. right before an interview.

`<output_dir>` defaults to `output`, next to jobradar itself, and is
configurable via `config.json`'s `paths.output_dir` or the setup page's
"Where to save tailored files" field — set it to an absolute path (or one
starting with `~`) to save directly into, say, `~/Documents/Resume/jobradar`
instead. In the web UI, generated files are served from wherever this
points at via `/files/<company>/<role>/...`, so moving it outside the app's
own folder doesn't break the download links.

**One page, verified, not asserted.** Each file is rendered with a headless
browser and its actual page count measured. If it doesn't fit, jobradar
retries at a tighter type density (or starts there directly if you've set
`render.preferred_density` in `config.json`), then trims the
lowest-priority bullet or paragraph one at a time — re-measuring after every
change — until it fits or a small cut budget runs out. If no browser is
found at all, jobradar says so explicitly rather than silently shipping an
unverified file.

**Read the output before you send it.** Two automated checks run after
generation:
- `skills_line` is hard-filtered against your actual `profile.json` skills —
  anything the model added that isn't really there is stripped and reported.
- Cover-letter text is scanned for "I've used X" / "I have experience with
  X" phrasing where X doesn't trace back to your profile, flagged as a
  warning.

Both flags — plus which type density was used — appear as a small
on-screen-only note at the bottom of the generated HTML file
(`@media print { display: none }`, so it never appears in a printed page or
exported PDF, and never eats into page-fitting budget). It's a heuristic,
not a guarantee: treat the output as a strong first draft, not ground truth
ready to submit unread.

Download links in the web UI are pre-filled with a friendly suggested
filename (`Your Name - Company - Title.html`) rather than the plain
`resume.html`/`cover_letter.html` names used on disk (the company/role are
already encoded in the folder path there instead).

## Your profile.json

Filled in via setup, or hand-edited (`jobradar.py init` creates it from
`examples/profile.example.json`, stripped of the example's inline `_note`
fields — see that file for the annotated schema if editing by hand):

| field | purpose |
|---|---|
| `name`, `contact` | resume/cover-letter header only — never sent to the AI backend |
| `headline`, `years_experience`, `summary` | top-of-resume framing the AI works from |
| `skills` | the ground truth `tailor` hard-filters generated content against |
| `experience[]` | `company`, `title`, `start`, `end`, `highlights[]` — your real bullets; `tailor` selects/reorders/tightens these, never invents new ones |
| `education`, `certifications` | as printed |
| `notes_for_ai` | free text the AI weighs directly — dealbreakers, seniority preference, how to treat an experience-requirement gap, etc. |

**Match quality is only as honest as what you put here** — the model is
instructed not to invent anything beyond it, so a thin profile produces
thin, low-confidence scoring.

## config.json reference

Every field is documented inline via `_..._note` keys in
`examples/config.example.json` (stripped automatically when you run `init`
or use the setup form — a live `config.json` never carries them). Full
annotated shape:

```jsonc
{
  "profile_file": "profile.json",

  "location_filter": ["bengaluru", "bangalore", "blr"],   // substring match, case-insensitive; [] disables it
  "recency_days": 45,

  "title_include": ["product manager", "..."],
  "title_exclude": ["intern", "junior", "..."],

  "sources": { "index": true, "ats": true, "jobspy": false },
  // index: daily-refreshed multi-ATS index (always on, recommended)
  // ats: the ats_companies list below, queried live (minutes, not a day)
  // jobspy: Indeed/LinkedIn scraping — off by default, read the README before enabling

  "jobspy": { "search_terms": ["senior product manager"], "location": "Bangalore, India" },

  "ai": {
    "backend": "ollama",          // "ollama" | "anthropic" | "openai"
    "model": "mistral-nemo",
    "ollama_host": "http://localhost:11434",
    "max_jobs_to_score": 60,      // cap on real AI calls per scan, ranked by keyword prescreen first
    "min_score": 50,              // final-list cutoff
    "workers": 4
  },

  "target_companies": ["Stripe", "Razorpay"],   // pinned + badged in results; needs a matching ats_companies entry for `watch` to see it

  "watch": {
    "poll_interval_minutes": 60,
    "auto_tailor": true,           // auto-run `tailor` the moment a new target-company role appears
    "notify": { "desktop": true, "webhook_url": "" }   // webhook_url: Slack/Discord incoming-webhook URL
  },

  "ats_companies": [
    { "name": "Stripe", "slug": "stripe", "ats": "greenhouse" }
    // ats: "greenhouse" | "lever" | "ashby" | "smartrecruiters"
  ],

  "render": { "preferred_density": "normal" },  // or "compact" — skip the normal-density attempt if your resume always runs long

  "paths": { "output_dir": "output" }
  // relative (default) stays next to jobradar; an absolute path, or one
  // starting with "~", saves tailored files directly there instead —
  // organized into a subfolder per company and role either way. Overridable
  // per-run with `tailor --out-dir`/`watch --out-dir` on the CLI.
}
```

### Worked example

A Bengaluru-based senior PM who only wants payments/fintech roles at a
specific shortlist of companies, free local AI, and a resume that always
starts at compact density:

```json
{
  "location_filter": ["bengaluru", "bangalore", "blr"],
  "title_include": ["product manager", "senior product manager", "principal product"],
  "title_exclude": ["intern", "junior", "new grad"],
  "ai": { "backend": "ollama", "model": "mistral-nemo", "min_score": 55 },
  "target_companies": ["Razorpay", "Stripe", "Nium"],
  "ats_companies": [
    { "name": "Razorpay", "slug": "razorpaysoftwareprivatelimited", "ats": "greenhouse" },
    { "name": "Stripe",   "slug": "stripe",   "ats": "greenhouse" },
    { "name": "Nium",     "slug": "nium",     "ats": "lever" }
  ],
  "render": { "preferred_density": "compact" },
  "paths": { "output_dir": "~/Documents/Resume/jobradar" }
}
```

The fields that matter most day to day: `location_filter` + `title_include`
decide what you even see; `ai.min_score` decides the hidden-below-cutoff
line; `target_companies` + `ats_companies` together are what makes `watch`
actually alert on a company (the first without the second does nothing);
`paths.output_dir` decides where tailored files land, if "next to the app"
isn't where you actually keep your resumes.

## The radar: `watch`

```bash
jobradar.py watch               # polls, alerts, auto-tailors — runs until Ctrl+C
jobradar.py watch --once        # a single pass, then exit (for your own cron/launchd/Task Scheduler)
jobradar.py watch --interval 30 # override config.json's watch.poll_interval_minutes
```

`watch` is CLI-only, on purpose — it's meant to run unattended for days,
which a browser tab shouldn't be responsible for. The setup page and
main-board scan-error path both surface a ready-to-copy command and links to
platform scheduler docs, but jobradar never writes to your system scheduler
itself.

It polls `ats_companies` for postings at your `target_companies` and never
re-alerts on the same URL twice (`data/seen_urls.json`). `auto_tailor: true`
runs `tailor` automatically the moment a new target-company role appears, so
materials are ready when you see the alert. Alerts print to the console and,
per `watch.notify`, can also fire a native desktop notification and/or a
Slack/Discord webhook. **A target company only gets watched if it also has
an `ats_companies` entry** — typing a name into `target_companies` alone is
not enough; `watch` polls `ats_companies` live and does not re-download the
whole daily index on every pass.

`watch` never applies to anything on its own — it only alerts and prepares
files for you to review and send.

## Checking your company boards

A broken or mistyped ATS slug is a silent failure otherwise — the company
simply never shows postings. `jobradar.py verify` (CLI) and the
**Check your company boards →** button (in setup's unmatched-company panel,
and in the main board's scan-error state) both run the same check: fetch
every configured `ats_companies` entry live and report per board whether
it's reachable and how many postings came back.

```bash
jobradar.py verify
```

```
ok             Stripe               greenhouse        587 postings
ok             Razorpay             greenhouse         26 postings
empty          SomeCo               lever               0 postings
BROKEN(404)    Typo Inc             greenhouse          0 postings
```

`empty` means the board is reachable but has no open roles right now —
that's fine. `BROKEN` means the slug or ATS platform is wrong; fix it in
`config.json` (or the setup page's resolve panel) and re-run.

## CLI reference

```bash
jobradar.py init                  # create config.json + profile.json from the examples
jobradar.py scan [--fast] [--fresh] [--out PATH]
                                   # fetch + (optionally AI-)score + rank -> data/results.json
                                   # --fast: skip AI scoring (keyword-only, instant)
                                   # --fresh: bypass the cached daily index
jobradar.py verify                # health-check every board in ats_companies
jobradar.py tailor <ref>          # one-page resume + cover letter for a result number or URL
                                   # [--out-dir DIR] overrides paths.output_dir for this run
                                   # [--results PATH]
jobradar.py watch [--once] [--interval MIN] [--out-dir DIR]
                                   # radar: alert + auto-tailor on new target_companies roles
jobradar.py serve [--port N]      # the local web app (default port 8765)
```

`--config PATH` is a top-level flag and must precede the subcommand, e.g.
`jobradar.py --config /path/to/config.json scan`.

## Web API reference

Everything the browser UI calls is a plain local JSON API — useful if you
want to script against a running `serve` instance yourself.

| route | method | purpose |
|---|---|---|
| `/api/config` | GET | is a profile/config set up; full config/profile when configured |
| `/api/setup` | POST | writes config.json + profile.json from the setup form |
| `/api/parse-resume` | POST | extracts an uploaded resume (`.pdf`/`.docx`/`.txt`) and structures it via the AI backend, for the setup form to pre-fill from |
| `/api/resolve-companies` | POST | merges hand-resolved `{name, slug, ats}` entries into `ats_companies` |
| `/api/scan` | POST | starts a scan in the background (`{"fast": bool}`) |
| `/api/scan/status` | GET | poll while a scan runs: `{status, message, count, error}` |
| `/api/tailor` | POST | starts the tailor pipeline for one job (`{"job": {...}}`) in the background |
| `/api/tailor/status` | GET | poll while it runs: `{status, message, result, error}` |
| `/api/test-ai` | POST | checks the configured (or given) AI backend is actually reachable |
| `/api/pull-model` | POST | starts `ollama pull <model>` in the background (`{"model": "..."}`) |
| `/api/pull-model/status` | GET | poll while a pull runs |
| `/api/verify` | GET | health-checks every board in `ats_companies` — `{boards: [{name, ats, status, count}]}` |
| `/files/<path>` | GET | serves a generated resume/cover-letter/report file from `paths.output_dir` — works even when that's outside the app's own folder |

All background operations (`/api/scan`, `/api/tailor`, `/api/pull-model`)
follow the same shape: the `POST` returns `202` immediately with the current
state, and the matching `/status` route is polled until `status` is `done`
or `error`. A `409` means one of that kind is already running.

## Troubleshooting

**"Could not start on port 8765 — it's already in use."**
Something else is already running there (maybe jobradar itself, from
another terminal). Run `jobradar.py serve --port 8766` or close the other
instance.

**Config file won't load / "invalid JSON at line N".**
You likely hand-edited `config.json` or `profile.json` and broke a comma or
brace — the exact line/column is reported. Fix it, or delete the file and
re-run `jobradar.py init` (or the setup page) to start clean.

**Scan gets stuck on "running" forever.**
This was a real bug fixed in this pass — a broken `config.json` used to
leave a background scan silently stuck with no error surfaced. If you still
see this, check the terminal `serve` is running in for a traceback and open
an issue.

**AI scoring fails / falls back to keyword-only for some jobs.**
Check `ai_error` per job in `data/results.json`. For Ollama, confirm
`ollama serve` is running and the model is pulled (`ollama list`). For
Anthropic/OpenAI, confirm the API key env var is set **in the same terminal
`serve` is running in** — a key exported in a different terminal tab has no
effect on an already-running server; restart it.

**A target company never shows up in `watch` alerts.**
It needs an entry in `ats_companies`, not just `target_companies` — see
[config.json reference](#configjson-reference). Run
`jobradar.py verify` or click **Check your company boards →** to confirm the
slug is actually correct.

**Tailored resume/cover letter didn't fit one page and jobradar says so.**
Check the warnings shown in the UI (and in the sidecar
`*_resume_report.json`) — it will have trimmed what it safely could within
its cut budget. Shorten the source content in `profile.json` for a cleaner
result, or set `render.preferred_density: "compact"` if this is a pattern
for you.

**No headless browser found.**
Install Chrome, Chromium, or Edge — jobradar looks for whichever is already
on your machine to verify page counts and will say explicitly if none is
found rather than silently shipping an unverified file.

**Resume upload says it can't be parsed.**
Same causes and fixes as any other AI call: for Ollama, is `ollama serve`
running and the model pulled; for Anthropic/OpenAI, is the API key set in
the terminal `serve` is running in. A "no text could be extracted" message
usually means the PDF is a scanned image with no real text layer — try a
text-based export, or just fill the form in by hand.

## What jobradar deliberately does not do

- **No auto-applying.** It produces a ranked, explained list and prepared
  materials; a human decides what to submit.
- **No credential-based scraping or bot-detection evasion.** Every default
  source is a public, unauthenticated API. `jobspy` is the one exception,
  off by default.
- **No telemetry.** Nothing about your search, profile, or results leaves
  your machine except the API calls you explicitly configure.
- **No OS package-manager automation.** jobradar will show you the
  `brew`/`apt`/`ollama pull` command to run — it never runs one on your
  system's behalf.
- **`tailor` and `watch` never submit anything.** They write files to
  `output/` for you to review and send yourself.
