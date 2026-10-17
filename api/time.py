"""GET /api/time — current server time ISO + unix."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone
import json

# --- Penjaga domain: salinan situs di domain lain tidak boleh memakai API ini (lihat juga js/dl.js) ---
ALLOWED_HOSTS = ("amfin.my.id", "www.amfin.my.id")
ALLOW_ORIGIN = "https://amfin.my.id"


def _host_of(value):
    try:
        from urllib.parse import urlparse as _up
        return (_up(value).hostname or "").lower()
    except Exception:
        return ""


def guard_ok(headers):
    origin = headers.get("Origin") or ""
    if origin:
        return _host_of(origin) in ALLOWED_HOSTS
    if (headers.get("Sec-Fetch-Site") or "").lower() in ("cross-site", "same-site"):
        return False
    ref = headers.get("Referer") or ""
    if ref and _host_of(ref) not in ALLOWED_HOSTS:
        return False
    return True


def deny(h):
    body = b'{"ok":false,"error":"forbidden"}'
    h.send_response(403)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    h.end_headers()
    h.wfile.write(body)


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        if not guard_ok(self.headers):
            return deny(self)
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if not guard_ok(self.headers):
            return deny(self)
        now = datetime.now(timezone.utc)
        # WIB = UTC+7
        from datetime import timedelta
        wib = now + timedelta(hours=7)
        body = json.dumps({
            "ok": True,
            "utc": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "wib": wib.strftime("%Y-%m-%d %H:%M:%S WIB"),
            "unix": int(now.timestamp()),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
