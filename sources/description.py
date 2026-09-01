"""Best-effort plain-text extraction of a job posting page, for AI scoring.

Not every posting yields real text this way — pages that render via
client-side JS (Workday chief among them) return a near-empty shell. Callers
should treat a short result as "no description available" and fall back to
title-only scoring rather than send junk to the model.
"""
import re
from .common import get_text

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)
_ENT = re.compile(r"&[a-z]+;|&#\d+;")
_WS = re.compile(r"\s+")

MIN_USABLE_LEN = 400


def fetch(url, max_chars=9000):
    if not url:
        return ""
    html = get_text(url)
    if not html:
        return ""
    text = _TAG.sub(" ", html)
    text = _ENT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return text[:max_chars]
