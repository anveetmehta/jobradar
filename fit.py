"""Force a rendered resume or cover letter onto one page, the same way this
project's author (a human, reviewing every output) insists on by hand:
render, measure, tighten, and only cut real content as an actual last
resort — never claim "one page" without having rendered and counted.

Order of operations: normal type density -> compact density -> trim the
least-essential content, re-checking compact after every cut. If no headless
browser is available at all (pdf.py.find_browser() returns None), page count
is never claimed — the caller gets an explicit warning and the compact
(safer) rendering, and must verify by hand.
"""
from pathlib import Path

import pdf
import render

MAX_CUTS = 8


def _write_and_measure(html, html_path: Path, browser):
    html_path.write_text(html)
    if not browser:
        return None
    pdf_path = html_path.with_suffix(".pdf")
    result = pdf.html_to_pdf(html_path, pdf_path, browser=browser)
    if not result:
        return None
    return pdf.count_pages(result)


def _cut_one_bullet(experience):
    """Remove one bullet from whichever role currently has the most, leaving
    at least one bullet per role. Returns True if a cut was made."""
    candidates = [(i, len(e.get("bullets", []))) for i, e in enumerate(experience)]
    candidates = [(i, n) for i, n in candidates if n > 1]
    if not candidates:
        return False
    idx = max(candidates, key=lambda t: t[1])[0]
    experience[idx]["bullets"].pop()
    return True


def fit_resume(profile, content, out_html: Path, title="tailored resume",
              start_density="normal", extra_notes=None):
    """-> dict: html_path, pdf_path (or None), pages (or None), density,
    cuts_made, warnings (list of str)."""
    browser = pdf.find_browser()
    warnings = []
    if not browser:
        warnings.append("No headless browser (Chrome/Chromium/Edge) found — page count was "
                        "NOT verified. Open the file and check it fits one page yourself "
                        "(print to PDF with margins set to None).")

    density = start_density
    html = render.render_resume_html(profile, content, density, title, extra_notes=extra_notes)
    pages = _write_and_measure(html, out_html, browser)

    if browser and pages != 1 and density != "compact":
        density = "compact"
        html = render.render_resume_html(profile, content, density, title, extra_notes=extra_notes)
        pages = _write_and_measure(html, out_html, browser)

    cuts = 0
    while browser and pages and pages > 1 and cuts < MAX_CUTS:
        if not _cut_one_bullet(content["experience"]):
            break
        cuts += 1
        html = render.render_resume_html(profile, content, "compact", title, extra_notes=extra_notes)
        pages = _write_and_measure(html, out_html, browser)

    if browser and pages and pages > 1:
        warnings.append(f"Could not fit to one page automatically after {cuts} bullet cuts — "
                        f"still {pages} pages. Trim manually or shorten profile.json entries.")
    elif cuts:
        warnings.append(f"Trimmed {cuts} lowest-priority bullet(s) to fit one page.")

    pdf_path = out_html.with_suffix(".pdf") if (browser and pages) else None
    return {"html_path": out_html, "pdf_path": pdf_path, "pages": pages,
            "density": density, "cuts_made": cuts, "warnings": warnings}


def fit_cover_letter(profile, content, job_rec, out_html: Path, date_str="",
                     start_density="normal", extra_notes=None):
    browser = pdf.find_browser()
    warnings = []
    if not browser:
        warnings.append("No headless browser (Chrome/Chromium/Edge) found — page count was "
                        "NOT verified. Open the file and check it fits one page yourself.")

    density = start_density
    html = render.render_cover_letter_html(profile, content, job_rec, density, date_str,
                                           extra_notes=extra_notes)
    pages = _write_and_measure(html, out_html, browser)

    if browser and pages != 1 and density != "compact":
        density = "compact"
        html = render.render_cover_letter_html(profile, content, job_rec, density, date_str,
                                               extra_notes=extra_notes)
        pages = _write_and_measure(html, out_html, browser)

    cuts = 0
    while browser and pages and pages > 1 and len(content.get("body", [])) > 1 and cuts < MAX_CUTS:
        content["body"].pop()
        cuts += 1
        html = render.render_cover_letter_html(profile, content, job_rec, "compact", date_str,
                                               extra_notes=extra_notes)
        pages = _write_and_measure(html, out_html, browser)

    if browser and pages and pages > 1:
        warnings.append(f"Could not fit to one page automatically — still {pages} pages. "
                        f"Shorten manually.")
    elif cuts:
        warnings.append(f"Dropped {cuts} paragraph(s) to fit one page.")

    pdf_path = out_html.with_suffix(".pdf") if (browser and pages) else None
    return {"html_path": out_html, "pdf_path": pdf_path, "pages": pages,
            "density": density, "cuts_made": cuts, "warnings": warnings}
