"""Render an HTML file to PDF and count its pages, using whatever headless
browser is already on the machine (Chrome, Chromium, or Edge). Deliberately
not a new pip dependency: browsers with print-to-pdf support are common
enough on a normal laptop that requiring one is a lighter ask than a native
PDF-rendering library (e.g. WeasyPrint) that needs system-level Cairo/Pango.

If no browser is found, callers get None back and must tell the user to
verify page count manually — never claim a page count that was never
measured.
"""
import platform
import re
import shutil
import subprocess
from pathlib import Path

_CANDIDATES = {
    "Darwin": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "Windows": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
}
_PATH_NAMES = ("google-chrome", "google-chrome-stable", "chromium",
              "chromium-browser", "microsoft-edge", "microsoft-edge-stable")


def find_browser():
    for c in _CANDIDATES.get(platform.system(), []):
        if Path(c).exists():
            return c
    for name in _PATH_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def html_to_pdf(html_path: Path, pdf_path: Path, browser=None, timeout=30):
    """-> pdf_path on success, None if no browser or the render failed."""
    browser = browser or find_browser()
    if not browser:
        return None
    try:
        subprocess.run([browser, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", f"file://{html_path.resolve()}"],
                       capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return pdf_path if pdf_path.exists() and pdf_path.stat().st_size > 0 else None


def count_pages(pdf_path: Path):
    data = pdf_path.read_bytes()
    return len(re.findall(rb"/Type\s*/Page[^s]", data))
