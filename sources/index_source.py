"""job-board-aggregator index: ~1.4M+ live postings from ~20k companies across
Greenhouse/Lever/Ashby/Workday/BambooHR/iCIMS/Paylocity, refreshed daily.

Credit: https://github.com/Feashliaa/job-board-aggregator (code MIT, data CC BY-NC 4.0).
This module only reads its public static JSON feed — it does not vendor that
project's code.

Covers ATS platforms jobradar has no direct adapter for (Workday in particular),
at the cost of no live description fetch for those rows (title/company/location
only) since most of those platforms gate descriptions behind a browser session.
"""
import gzip
import io
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import get_text, get_bytes, iso_to_date, job

INDEX_BASE = "https://feashliaa.github.io/job-board-data/data/chunks"


def load(cache_dir: Path, location_filter=None, fresh=False, quiet=False):
    """location_filter: list of lowercase substrings to match against job location,
    or None/[] to keep every row (not recommended — this index is large)."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / "index_filtered.json"

    manifest_raw = get_text(f"{INDEX_BASE}/jobs_manifest.json")
    try:
        manifest = json.loads(manifest_raw)
    except Exception:
        if not quiet:
            print("[index] manifest unreachable — skipping index source", file=sys.stderr)
        return []
    stamp = manifest.get("last_updated", "")

    if cache_file.exists() and not fresh:
        try:
            cached = json.loads(cache_file.read_text())
            if cached.get("stamp") == stamp and cached.get("filter") == (location_filter or []):
                return cached["jobs"]
        except Exception:
            pass

    chunks = manifest.get("chunks", [])
    if not quiet:
        print(f"[index] downloading {len(chunks)} chunks (refreshed {stamp[:10]})...",
              file=sys.stderr)

    def grab(name):
        raw = get_bytes(f"{INDEX_BASE}/{name}")
        if not raw:
            return []
        try:
            return json.load(gzip.GzipFile(fileobj=io.BytesIO(raw)))
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=10) as ex:
        pages = list(ex.map(grab, chunks))

    loc_filter = [w.lower() for w in (location_filter or [])]
    keep = []
    for page in pages:
        for j in page:
            loc = (j.get("location") or "").lower()
            if loc_filter and not any(w in loc for w in loc_filter):
                continue
            keep.append(job(j.get("company"), j.get("title"), j.get("location"),
                            j.get("url"), iso_to_date(j.get("first_seen")),
                            j.get("ats", ""), "index"))

    cache_file.write_text(json.dumps({"stamp": stamp, "filter": location_filter or [],
                                      "jobs": keep}))
    if not quiet:
        total = sum(len(p) for p in pages)
        print(f"[index] {total:,} postings -> {len(keep):,} matched your location filter",
              file=sys.stderr)
    return keep
