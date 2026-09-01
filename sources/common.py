"""Shared helpers: HTTP fetch, date parsing, the normalized job record."""
import datetime as dt
import requests

UA = "jobradar/0.1 (+https://github.com/anveetmehta/jobradar)"
TIMEOUT = 25


def get_json(url, params=None, headers=None):
    """-> (status_str, data). status is an HTTP code, 'timeout', or 'badjson'."""
    h = {"User-Agent": UA, "Accept": "application/json"}
    if headers:
        h.update(headers)
    try:
        r = requests.get(url, params=params, headers=h, timeout=TIMEOUT)
    except requests.exceptions.Timeout:
        return "timeout", None
    except requests.exceptions.RequestException as e:
        return f"error:{type(e).__name__}", None
    if r.status_code != 200:
        return str(r.status_code), None
    try:
        return "200", r.json()
    except ValueError:
        return "badjson", None


def get_bytes(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        return r.content if r.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None


def get_text(url):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
        return r.text if r.status_code == 200 else None
    except requests.exceptions.RequestException:
        return None


def iso_to_date(v):
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).date()
    except Exception:
        return None


def ms_to_date(v):
    try:
        return dt.datetime.fromtimestamp(int(v) / 1000, dt.timezone.utc).date()
    except Exception:
        return None


def job(company, title, location, url, posted, team="", source=""):
    return dict(company=company or "", title=title or "", location=location or "",
                team=team or "", url=url or "", posted=str(posted) if posted else None,
                source=source)
