"""Render tailored resume/cover-letter content (from resume.py) into a
single self-contained HTML file, in one of two type-density presets. Two
presets exist purely so fit.py can retry tighter before it starts cutting
actual content — see that module for the overflow policy.
"""
import html as _html

_DENSITY = {
    "normal":  dict(margin="13mm 15mm", body="10.1pt", lh="1.34", h1="20pt",
                    h2="10.8pt", h2margin="10pt 0 4pt", li="2.2pt", role="6pt"),
    "compact": dict(margin="10mm 14mm", body="9.6pt", lh="1.25", h1="17.5pt",
                    h2="10.2pt", h2margin="7pt 0 3pt", li="1.5pt", role="4.5pt"),
}


def _e(s):
    return _html.escape(str(s or ""), quote=False)


def _base_style(d):
    return f"""
  @page {{ size: A4; margin: {d['margin']}; }}
  :root {{ --ink:#000; --rule:#000; --muted:#333; }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: 'Latin Modern Roman','TeX Gyre Termes',Georgia,'Times New Roman',Times,serif;
    font-size: {d['body']}; line-height: {d['lh']}; color: var(--ink); background:#fff;
    max-width: 190mm; margin: 0 auto; padding: 6mm 6mm;
  }}
  header {{ text-align:center; margin-bottom: 6pt; }}
  h1 {{ font-size: {d['h1']}; font-weight: 700; letter-spacing: .06em; margin: 0 0 1pt; }}
  .subtitle {{ font-size: 10pt; font-weight: 700; margin-bottom: 2pt; }}
  .contact {{ font-size: 9.3pt; color: var(--muted); }}
  h2 {{ font-size: {d['h2']}; font-weight: 700; margin: {d['h2margin']};
        border-bottom: .8pt solid var(--rule); padding-bottom: 1.5pt; }}
  .role {{ display:flex; justify-content:space-between; align-items:baseline; gap:8pt;
           margin-top: {d['role']}; }}
  .org {{ font-weight:700; font-size:10pt; }}
  .dates {{ font-style:italic; font-size:9.4pt; white-space:nowrap; }}
  .title {{ font-style:italic; font-size:9.8pt; margin-bottom:2pt; }}
  ul {{ margin: 1.5pt 0 0; padding-left: 12pt; }}
  li {{ margin-bottom: {d['li']}; }}
  p.tight {{ margin: 2pt 0; }}
  .skills {{ margin: 2pt 0 0; }}
  b {{ font-weight: 700; }}
  @media print {{ body {{ padding:0; max-width:none; }} }}
"""


def render_resume_html(profile, content, density="normal", title="tailored resume"):
    d = _DENSITY[density]
    contact = profile.get("contact", {})
    contact_line = " &nbsp;—&nbsp; ".join(
        _e(x) for x in (contact.get("location"), contact.get("email"),
                        contact.get("phone"), contact.get("linkedin")) if x)

    exp_html = []
    for e in content.get("experience", []):
        bullets = "".join(f"<li>{_e(b)}</li>" for b in e.get("bullets", []))
        exp_html.append(f"""
<div class="role">
  <span class="org">{_e(e.get('company'))}</span>
  <span class="dates">{_e(e.get('dates'))}</span>
</div>
<div class="title">{_e(e.get('title'))}</div>
<ul>{bullets}</ul>""")

    edu = profile.get("education") or []
    edu_line = " &nbsp;·&nbsp; ".join(_e(x) for x in edu)
    certs = profile.get("certifications") or []
    certs_line = " &nbsp;·&nbsp; ".join(_e(x) for x in certs)

    return f"""<title>{_e(title)}</title>
<style>{_base_style(d)}</style>
<header>
  <h1>{_e(profile.get('name', '')).upper()}</h1>
  <div class="subtitle">{_e(content.get('headline'))}</div>
  <div class="contact">{contact_line}</div>
</header>
<h2>Summary</h2>
<p class="tight">{_e(content.get('summary'))}</p>
<h2>Core Skills</h2>
<p class="tight">{" &nbsp;·&nbsp; ".join(_e(s) for s in content.get('skills_line', []))}</p>
<h2>Experience</h2>
{"".join(exp_html)}
{f'<h2>Education</h2><p class="tight">{edu_line}</p>' if edu_line else ""}
{f'<h2>Certifications</h2><p class="tight">{certs_line}</p>' if certs_line else ""}
"""


def render_cover_letter_html(profile, content, job_rec, density="normal", date_str=""):
    d = _DENSITY[density]
    contact = profile.get("contact", {})
    contact_line = " &nbsp;·&nbsp; ".join(
        _e(x) for x in (contact.get("location"), contact.get("email"),
                        contact.get("phone"), contact.get("linkedin")) if x)
    body_paras = "".join(f"<p>{_e(p)}</p>" for p in content.get("body", []))
    gap = content.get("acknowledge_gap") or ""

    return f"""<title>cover letter — {_e(job_rec.get('company'))}</title>
<style>
{_base_style(d)}
  .meta {{ margin: 14pt 0 12pt; font-size: 10pt; }}
  .meta div {{ margin-bottom: 1pt; }}
  p {{ margin: 0 0 9pt; }}
</style>
<header style="text-align:left">
  <h1 style="letter-spacing:.05em">{_e(profile.get('name', '')).upper()}</h1>
  <div class="contact">{contact_line}</div>
</header>
<div class="meta">
  <div>{_e(date_str)}</div>
  <div>{_e(job_rec.get('company'))} — Hiring Team</div>
  <div><b>Re: {_e(job_rec.get('title'))}</b></div>
</div>
<p>Dear Hiring Team,</p>
<p>{_e(content.get('opening'))}</p>
{body_paras}
{f"<p>{_e(gap)}</p>" if gap else ""}
<p>{_e(content.get('closing'))}</p>
<p style="margin-top:14pt">Sincerely,<br><b>{_e(profile.get('name', ''))}</b></p>
"""
