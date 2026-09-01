"""Fetchers for Lever, Greenhouse, Ashby, and SmartRecruiters public job-board APIs.

All four platforms publish job postings on unauthenticated, read-only JSON
endpoints meant to be consumed by third parties (their own "embed on your
careers page" widgets use the same endpoints). No login, no scraping.
"""
from concurrent.futures import ThreadPoolExecutor
from .common import get_json, iso_to_date, ms_to_date, job

ENDPOINTS = {
    "lever":           "https://api.lever.co/v0/postings/{slug}?mode=json",
    "greenhouse":      "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
    "ashby":           "https://api.ashbyhq.com/posting-api/job-board/{slug}",
    "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/{slug}/postings",
}
SR_PAGE, SR_MAX = 100, 500


def fetch_board(ats, slug):
    """-> (status, raw_data) for one company's board."""
    if ats not in ENDPOINTS:
        return f"unknown-ats:{ats}", None
    if ats != "smartrecruiters":
        return get_json(ENDPOINTS[ats].format(slug=slug))
    base, items, offset = ENDPOINTS[ats].format(slug=slug), [], 0
    while offset < SR_MAX:
        status, d = get_json(base, params={"limit": SR_PAGE, "offset": offset})
        if status != "200" or not d:
            return (status, {"content": items}) if items else (status, None)
        items += d.get("content", [])
        if len(items) >= d.get("totalFound", 0):
            break
        offset += SR_PAGE
    return "200", {"content": items}


def normalize(company, ats, data):
    if not data:
        return []
    if ats == "lever" and isinstance(data, list):
        return [job(company, j.get("text"), (j.get("categories") or {}).get("location"),
                    j.get("hostedUrl"), ms_to_date(j.get("createdAt")),
                    (j.get("categories") or {}).get("team"), "ats:lever") for j in data]
    if ats == "greenhouse" and isinstance(data, dict):
        return [job(company, j.get("title"), (j.get("location") or {}).get("name"),
                    j.get("absolute_url"), iso_to_date(j.get("updated_at")),
                    "", "ats:greenhouse") for j in data.get("jobs", [])]
    if ats == "ashby" and isinstance(data, dict):
        return [job(company, j.get("title"), j.get("location"),
                    j.get("jobUrl") or j.get("applyUrl"), iso_to_date(j.get("publishedAt")),
                    j.get("department") or j.get("team"), "ats:ashby")
                for j in data.get("jobs", []) if j.get("isListed") is not False]
    if ats == "smartrecruiters" and isinstance(data, dict):
        out = []
        for j in data.get("content", []):
            loc = j.get("location") or {}
            where = ", ".join(x for x in (loc.get("city"), loc.get("region"),
                                          loc.get("country")) if x)
            ident = (j.get("company") or {}).get("identifier", "")
            out.append(job(company, j.get("name"), where,
                           f"https://jobs.smartrecruiters.com/{ident}/{j.get('id','')}",
                           iso_to_date(j.get("releasedDate")),
                           (j.get("department") or {}).get("label"), "ats:smartrecruiters"))
        return out
    return []


def fetch_all(companies, max_workers=12):
    """companies: [{"name","slug","ats"}, ...] -> (jobs, board_errors)."""
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        raw = list(ex.map(lambda c: (c, fetch_board(c["ats"], c["slug"])), companies))
    jobs, errors = [], []
    for c, (status, data) in raw:
        if status != "200":
            errors.append(f"{c['name']}[{status}]")
        jobs += normalize(c["name"], c["ats"], data)
    return jobs, errors


def verify(companies, max_workers=12):
    """Health-check each configured board. Returns per-company (status, count)."""
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        raw = list(ex.map(lambda c: (c, fetch_board(c["ats"], c["slug"])), companies))
    return [(c, status, len(normalize(c["name"], c["ats"], data)))
            for c, (status, data) in raw]
