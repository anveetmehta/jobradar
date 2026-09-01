"""Best-effort notifications for `jobradar watch`. Every path degrades to a
console print — a missed desktop notification or a bad webhook URL should
never be the reason an alert goes unseen.
"""
import platform
import shutil
import subprocess
import sys

import requests


def desktop(title, message):
    """-> True if a native OS notification was actually shown."""
    system = platform.system()
    try:
        if system == "Darwin":
            script = f'display notification {_osa_quote(message)} with title {_osa_quote(title)}'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            return True
        if system == "Linux" and shutil.which("notify-send"):
            subprocess.run(["notify-send", title, message], capture_output=True, timeout=5)
            return True
        if system == "Windows" and shutil.which("powershell"):
            ps = (f"[System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms') | "
                 f"Out-Null; $n = New-Object System.Windows.Forms.NotifyIcon; "
                 f"$n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
                 f"$n.ShowBalloonTip(5000, '{_ps_escape(title)}', '{_ps_escape(message)}', "
                 f"[System.Windows.Forms.ToolTipIcon]::Info)")
            subprocess.run(["powershell", "-Command", ps], capture_output=True, timeout=5)
            return True
    except (subprocess.TimeoutExpired, OSError):
        pass
    return False


def _osa_quote(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _ps_escape(s):
    return str(s).replace("'", "''")


def webhook(url, text):
    """POST {"text": ...} — the shape Slack and Discord incoming webhooks both
    accept. -> True on a 2xx response."""
    try:
        r = requests.post(url, json={"text": text}, timeout=10)
        return 200 <= r.status_code < 300
    except requests.exceptions.RequestException:
        return False


def alert(cfg, title, message):
    """Always prints. Additionally fires desktop/webhook per config['watch']['notify']."""
    print(f"\n*** {title} ***\n{message}\n", file=sys.stderr)
    ncfg = cfg.get("watch", {}).get("notify", {})
    if ncfg.get("desktop", True):
        desktop(title, message)
    url = ncfg.get("webhook_url", "")
    if url:
        if not webhook(url, f"*{title}*\n{message}"):
            print("[watch] webhook notification failed", file=sys.stderr)
