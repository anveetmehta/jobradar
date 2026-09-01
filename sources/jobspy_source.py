"""Optional: Indeed + LinkedIn via python-jobspy (https://github.com/speedyapply/JobSpy).

Off by default. This is the one source in jobradar that scrapes job-board
pages rather than calling a public API — see the README's "Sources & risk"
section before enabling it. Naukri is supported by JobSpy but commonly
returns 406/recaptcha without a residential proxy, so it is skipped here.
"""
import sys
from .common import iso_to_date, job

SITES = ("indeed", "linkedin")


def load(search_terms, location, hours_old=1080, results_per_query=40, quiet=False):
    try:
        from jobspy import scrape_jobs
    except ImportError:
        if not quiet:
            print("[jobspy] python-jobspy not installed — run "
                  "`pip install python-jobspy` to enable this source", file=sys.stderr)
        return []

    out = []
    for site in SITES:
        kw = {"location": location}
        if site == "indeed":
            kw["country_indeed"] = "worldwide"
        for term in search_terms:
            try:
                df = scrape_jobs(site_name=[site], search_term=term,
                                 results_wanted=results_per_query, hours_old=hours_old, **kw)
            except Exception as e:
                if not quiet:
                    print(f"[jobspy] {site}/{term!r}: {type(e).__name__} {str(e)[:100]}",
                          file=sys.stderr)
                continue
            for _, r in df.iterrows():
                d = r.get("date_posted")
                out.append(job(str(r.get("company") or ""), str(r.get("title") or ""),
                               str(r.get("location") or ""), str(r.get("job_url") or ""),
                               iso_to_date(str(d)[:10]) if d and str(d) != "NaT" else None,
                               "", f"jobspy:{site}"))
    return out
