"""AI-based profile-to-job matching.

Three interchangeable backends — pick one in config.json under "ai.backend":

  ollama      Free, runs entirely on your machine via https://ollama.com.
              No API key, no data leaves your computer. Slower and less
              reliable at strict JSON output than a frontier cloud model;
              jobradar retries and falls back to a neutral score on failure.
  anthropic   Claude via the Anthropic API. Needs ANTHROPIC_API_KEY in your
              environment (never put it in config.json).
  openai      GPT via the OpenAI API. Needs OPENAI_API_KEY in your environment.

The prompt asks for a skeptical, evidence-based fit assessment — not a
keyword-overlap score. It's told explicitly not to invent qualifications the
profile doesn't state, and to flag genuine gaps rather than smooth over them.
"""
import json
import re
import requests

TIMEOUT = 90

SYSTEM_PROMPT = """You are a skeptical, evidence-based hiring-fit evaluator. You are given a \
candidate's profile and one job posting. Score how well the candidate genuinely fits this \
specific role — not how many keywords overlap.

Rules:
- Base the score ONLY on what the profile actually states. Never invent or infer skills, \
years, or achievements the candidate did not claim.
- If the posting states a minimum years-of-experience and the candidate falls short, say so \
explicitly in "gaps" and score accordingly — a 4-year shortfall is a real problem, not a \
rounding error.
- List concrete gaps, not vague hedges. "No stated experience with X, which the posting \
requires" beats "could be a stronger fit."
- A generic senior title match with no real domain overlap should score low, not medium.

Respond with ONLY a JSON object, no other text, no markdown fences:
{"score": <0-100 integer>, "verdict": "<strong|possible|weak|poor>", \
"why": "<one or two sentences>", "gaps": ["<gap 1>", "<gap 2>", ...]}"""


def _build_user_prompt(profile_text, job_rec, description):
    parts = [
        "CANDIDATE PROFILE:", profile_text.strip(), "",
        "JOB POSTING:",
        f"Title: {job_rec.get('title','')}",
        f"Company: {job_rec.get('company','')}",
        f"Location: {job_rec.get('location','')}",
    ]
    if description and len(description) >= 400:
        parts += ["", "Description:", description]
    else:
        parts += ["", "(No usable description was retrievable for this posting — "
                       "score conservatively on title/company/location alone and say so "
                       "in \"why\".)"]
    return "\n".join(parts)


def _extract_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _call_ollama(prompt, model, host):
    r = requests.post(f"{host.rstrip('/')}/api/generate", timeout=TIMEOUT, json={
        "model": model, "system": SYSTEM_PROMPT, "prompt": prompt,
        "format": "json", "stream": False, "options": {"temperature": 0.1},
    })
    r.raise_for_status()
    return r.json().get("response", "")


def _call_anthropic(prompt, model, api_key):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=TIMEOUT,
                      headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                              "content-type": "application/json"},
                      json={"model": model, "max_tokens": 500, "system": SYSTEM_PROMPT,
                           "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json().get("content", []))


def _call_openai(prompt, model, api_key):
    r = requests.post("https://api.openai.com/v1/chat/completions", timeout=TIMEOUT,
                      headers={"Authorization": f"Bearer {api_key}",
                              "content-type": "application/json"},
                      json={"model": model, "temperature": 0.1,
                           "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                       {"role": "user", "content": prompt}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def score_job(profile_text, job_rec, description, ai_config, api_key=None):
    """-> dict with score/verdict/why/gaps, plus "error" set on failure (score
    then defaults to None so the caller can fall back to keyword-only ranking)."""
    prompt = _build_user_prompt(profile_text, job_rec, description)
    backend = ai_config.get("backend", "ollama")
    model = ai_config.get("model", "")

    try:
        if backend == "ollama":
            raw = _call_ollama(prompt, model, ai_config.get("ollama_host",
                                                            "http://localhost:11434"))
        elif backend == "anthropic":
            if not api_key:
                return {"error": "ANTHROPIC_API_KEY not set", "score": None}
            raw = _call_anthropic(prompt, model, api_key)
        elif backend == "openai":
            if not api_key:
                return {"error": "OPENAI_API_KEY not set", "score": None}
            raw = _call_openai(prompt, model, api_key)
        else:
            return {"error": f"unknown backend {backend!r}", "score": None}
    except requests.exceptions.RequestException as e:
        return {"error": f"{type(e).__name__}: {e}", "score": None}

    parsed = _extract_json(raw)
    if not parsed or "score" not in parsed:
        return {"error": "model did not return parseable JSON", "score": None,
                "raw": raw[:300]}

    try:
        parsed["score"] = max(0, min(100, int(parsed["score"])))
    except (TypeError, ValueError):
        parsed["score"] = None
    parsed.setdefault("verdict", "")
    parsed.setdefault("why", "")
    parsed.setdefault("gaps", [])
    return parsed
