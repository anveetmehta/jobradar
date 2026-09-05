"""AI-assisted resume and cover letter content generation.

Same discipline as match.py's job-fit scoring: the model may select, reorder,
and lightly rephrase facts already present in profile.json for relevance to
one specific posting — it must never invent metrics, dates, employers,
titles, or skills the candidate didn't state. Any posting requirement the
profile doesn't support is surfaced explicitly, never smoothed over.

This module only produces structured content (JSON). Turning that into a
one-page HTML/PDF file, with automatic overflow handling, lives in fit.py.

Prompt instructions alone do not reliably stop a small local model from
fabricating under pressure — testing this against a 7B Ollama model produced
a cover letter claiming "I've used other AI tools" for a skill genuinely
absent from the profile, in the same response that correctly listed that gap
in "unmet_requirements". validate_resume() and scan_for_unverified_claims()
below are a second, code-level line of defense: one hard filter (skills_line
must be an actual subset of the profile) and one best-effort heuristic scan
that flags suspicious experience-claim phrasing for a human to check. Neither
is a substitute for reading the output before you send it.
"""
import re
import requests
from match import call_llm, extract_json

_CLAIM_PATTERN = re.compile(
    r"\b(?:I(?:'ve| have)|I am|I'm)\s+(?:used|worked with|familiar with|experienced (?:in|with)|"
    r"experience (?:in|with)|skilled (?:in|with))\s+([^.;,]{3,60})", re.I)
_STOPWORDS = {"the", "a", "an", "and", "or", "of", "in", "with", "for", "to", "this",
             "that", "role", "position", "team", "similar", "other", "related", "tools"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9+]+", text.lower()) if w not in _STOPWORDS}


def _in_profile(phrase, profile_vocab):
    ph = _tokens(phrase)
    return bool(ph) and bool(ph & profile_vocab)


def _profile_vocabulary(profile):
    vocab = set()
    for s in profile.get("skills", []):
        vocab |= _tokens(s)
    for e in profile.get("experience", []):
        for h in e.get("highlights", []):
            vocab |= _tokens(h)
        vocab |= _tokens(e.get("title", ""))
    for s in profile.get("side_projects", []):
        vocab |= _tokens(s)
    vocab |= _tokens(profile.get("summary", ""))
    return vocab


def validate_resume(content, profile):
    """Strips any skills_line entry that isn't actually present in the
    profile's own skills list. Mutates content in place. Returns the list of
    items removed, so the caller can tell the user what got cut and why."""
    allowed = _tokens(" ".join(profile.get("skills", [])))
    kept, removed = [], []
    for s in content.get("skills_line", []):
        (kept if _in_profile(s, allowed) else removed).append(s)
    content["skills_line"] = kept
    return removed


def scan_for_unverified_claims(text, profile):
    """Best-effort heuristic: finds 'I've used X' / 'I have experience with X'
    style phrasing and flags any where X doesn't share vocabulary with
    anything in the profile. Catches the failure mode seen in testing where a
    model claims mitigating experience while acknowledging a gap. Not a
    guarantee — always read generated cover letters before sending."""
    vocab = _profile_vocabulary(profile)
    flags = []
    for m in _CLAIM_PATTERN.finditer(text or ""):
        claim = m.group(1).strip()
        if not _in_profile(claim, vocab):
            flags.append(m.group(0).strip())
    return flags

RESUME_SYSTEM = """You are a careful, honest resume editor helping a candidate tailor their \
existing resume to ONE specific job posting.

Hard rules:
- Use ONLY facts, numbers, employers, titles, and achievements already present in the \
candidate profile below. Never invent or embellish a metric, skill, or claim that isn't \
already there — not even a plausible-sounding one.
- You MAY select a relevant subset of each role's bullets, reorder them by relevance to this \
posting, and lightly tighten the wording. You may NOT add new bullets, new numbers, or new \
skills not present in the source.
- Keep every company name, job title, and date range in "experience" EXACTLY as given in the \
profile — do not rephrase or invent a title the candidate did not hold.
- Keep the experience entries in the SAME ORDER as the candidate profile (reverse \
chronological — do not reorder which role comes first). Only reorder BULLETS within a role.
- Order bullets within each role most-relevant-to-this-posting first.
- In "unmet_requirements", list concrete, specific requirements from the posting the profile \
does not support — e.g. "posting asks for 12+ years, profile states 8" or "posting requires \
Stripe API experience, not present in profile" — not vague hedges.
- "headline" and "summary" must be a truthful characterization of the candidate's real \
background, angled toward this posting — never claim a seniority, title, or specialization \
the candidate hasn't actually held.
- "skills_line" may ONLY contain items copied from the candidate profile's own skills list \
(verbatim or a trivial rewording, e.g. "kyc" -> "KYC"). Do not add a skill the posting wants \
just because it's relevant — if the posting needs a skill absent from the profile, that \
belongs in "unmet_requirements", never in "skills_line".

Respond with ONLY a JSON object, no other text, no markdown fences:
{"headline": "<one line, e.g. 'Senior Product Manager — Payments & Platform'>",
 "summary": "<3-4 sentences, first person implied, no 'I' — resume style>",
 "skills_line": ["<skill 1>", "<skill 2>", "..."],
 "experience": [{"company": "<from profile, verbatim>", "title": "<from profile, verbatim>",
                 "dates": "<from profile, verbatim>",
                 "bullets": ["<most relevant bullet first>", "..."]}],
 "unmet_requirements": ["<specific gap>", "..."]}"""

COVER_LETTER_SYSTEM = """You are helping a candidate write a short, honest cover letter for \
ONE specific job posting, grounded strictly in their real background.

Hard rules:
- Use ONLY facts already present in the candidate profile. Never invent an achievement, \
number, or qualification.
- No generic filler ("I am excited to apply for this position..."). Open with something \
specific to the actual overlap between the candidate's real work and this posting.
- Three short paragraphs total (opening + 1-2 body + implicit closing via "closing"). Total \
length under 300 words.
- Never claim the candidate has used, worked with, or is familiar with a tool, skill, or \
technology that is not in the candidate profile — not even a softened or approximate version \
of that claim (e.g. do NOT write "I've used other similar tools" as a way of half-covering a \
gap). If a skill isn't in the profile, the candidate does not have it; say so plainly instead.
- If "acknowledge_gap" is non-empty in your judgment, name the single most significant gap \
between the posting's stated requirements and the candidate's background plainly and briefly, \
then pivot to a real, profile-grounded strength — framed as candor, not an apology, and \
without claiming any mitigating experience the profile doesn't state. Only do this for a gap \
that is genuinely material (e.g. a stated minimum years-of-experience the candidate falls \
short of) — leave it empty ("") if there is no such material gap.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"opening": "<1-2 sentences>", "body": ["<paragraph>", "<paragraph>"],
 "closing": "<1 sentence>", "acknowledge_gap": "<sentence, or empty string>"}"""

PARSE_RESUME_SYSTEM = """You are extracting structured data from a candidate's own resume \
text, to pre-fill a setup form the candidate will review and correct before saving. This is \
extraction, not generation — a fabricated field here is a serious error, not a stylistic one.

Hard rules:
- Extract ONLY what is explicitly stated in the resume text below. Never invent, infer, \
estimate, or embellish a name, date, employer, title, achievement, or skill that isn't there.
- If a field isn't present in the text, use an empty string "" or empty list [] for it — never \
guess a plausible-sounding value to fill a gap.
- "years_experience" is your best count of total professional years from the STATED dates only \
(earliest start date to latest end date/"present"). If dates are missing or unclear, use 0 \
rather than guessing.
- "skills" lists only skills/technologies explicitly named in the text, not ones you infer \
from job titles or descriptions.
- Keep every "experience" entry's company, title, and dates EXACTLY as written in the source — \
do not standardize, correct, or rephrase them.
- "highlights" per role: the actual bullet points/achievements as stated, lightly cleaned of \
bullet characters and line-wrap artifacts, but never rewritten or summarized.
- List experience entries in the order they appear in the source text.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"name": "", "contact": {"location": "", "email": "", "phone": "", "linkedin": ""},
 "headline": "", "years_experience": 0, "summary": "", "skills": [],
 "experience": [{"company": "", "title": "", "start": "", "end": "", "highlights": []}],
 "education": [], "certifications": []}"""


def extract_resume_text(filename, raw_bytes):
    """Best-effort text extraction from an uploaded resume file (.pdf/.docx/.txt).
    -> (text, error) — error is a plain-language string on failure, else None.
    Never raises; the caller (webapp.py) has no other way to degrade a bad
    upload gracefully."""
    import io
    from pathlib import Path
    ext = Path(filename).suffix.lower()
    try:
        if ext == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(raw_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        elif ext == ".docx":
            import docx
            text = "\n".join(p.text for p in docx.Document(io.BytesIO(raw_bytes)).paragraphs)
        elif ext == ".txt":
            text = raw_bytes.decode("utf-8", errors="ignore")
        else:
            return None, f"Unsupported file type {ext or '(none)'} — upload a .pdf, .docx, or .txt file."
    except Exception as e:  # noqa: BLE001 — any parsing library failure degrades the same way
        return None, f"Could not read this file: {e}"
    text = text.strip()
    if not text:
        return None, ("No text could be extracted from this file — it might be a scanned image "
                      "with no text layer. Try a text-based export, or fill the form in by hand.")
    return text, None


def parse_resume_text(raw_text, ai_config, api_key=None):
    """-> dict per the profile.json schema, or {"error": "..."} on failure.
    Extraction only, per PARSE_RESUME_SYSTEM — the web UI pre-fills the setup
    form from this for the user to review and correct. It never writes
    profile.json directly from it; a human always confirms first."""
    text = raw_text[:20000]  # bound what gets sent, rather than truncate silently mid-word later
    try:
        raw = call_llm(PARSE_RESUME_SYSTEM, text, ai_config, api_key)
    except (RuntimeError, requests.exceptions.RequestException) as e:
        return {"error": str(e)}
    parsed = extract_json(raw)
    if not parsed or "experience" not in parsed:
        return {"error": "model did not return parseable resume JSON", "raw": raw[:300]}
    return parsed


def _profile_block(profile_text):
    return f"CANDIDATE PROFILE:\n{profile_text.strip()}"


def _posting_block(job_rec, description):
    parts = ["JOB POSTING:", f"Title: {job_rec.get('title','')}",
             f"Company: {job_rec.get('company','')}", f"Location: {job_rec.get('location','')}"]
    if description and len(description) >= 400:
        parts += ["", "Description:", description]
    else:
        parts += ["", "(No usable full description was retrievable — work from title/company/"
                       "location only, and say so is not needed here since this is a resume, "
                       "just be conservative about which posting-specific details you assume.)"]
    return "\n".join(parts)


def build_resume(profile_text, job_rec, description, ai_config, api_key=None):
    """-> dict per RESUME_SYSTEM's schema, or {"error": "..."} on failure."""
    prompt = f"{_profile_block(profile_text)}\n\n{_posting_block(job_rec, description)}"
    try:
        raw = call_llm(RESUME_SYSTEM, prompt, ai_config, api_key)
    except (RuntimeError, requests.exceptions.RequestException) as e:
        return {"error": str(e)}
    parsed = extract_json(raw)
    if not parsed or "experience" not in parsed:
        return {"error": "model did not return parseable resume JSON", "raw": raw[:300]}
    parsed.setdefault("unmet_requirements", [])
    return parsed


def build_cover_letter(profile_text, job_rec, description, ai_config, api_key=None):
    """-> dict per COVER_LETTER_SYSTEM's schema, or {"error": "..."} on failure."""
    prompt = f"{_profile_block(profile_text)}\n\n{_posting_block(job_rec, description)}"
    try:
        raw = call_llm(COVER_LETTER_SYSTEM, prompt, ai_config, api_key)
    except (RuntimeError, requests.exceptions.RequestException) as e:
        return {"error": str(e)}
    parsed = extract_json(raw)
    if not parsed or "body" not in parsed:
        return {"error": "model did not return parseable cover-letter JSON", "raw": raw[:300]}
    parsed.setdefault("acknowledge_gap", "")
    return parsed
