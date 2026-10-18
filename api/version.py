"""GET /api/version - cek cepat apakah deploy terbaru yang aktif (dan fitur apa yang menyala)."""
from http.server import BaseHTTPRequestHandler
import json
import os

VERSION = "4.1"
BUILD = "2026-10-14"


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

    def do_GET(self):
        if not guard_ok(self.headers):
            return deny(self)
        env = os.environ.get
        body = json.dumps({
            "version": VERSION,
            "build": BUILD,
            "layers": {
                "tikwm": (env("TIKWM_ENABLED", "1") or "1").strip() != "0",
                "rapidapi": bool((env("RAPIDAPI_KEY") or "").strip()),
                "google_cse": bool((env("GOOGLE_CSE_KEY") or "").strip() and (env("GOOGLE_CSE_CX") or "").strip()),
                "brave_api": bool((env("BRAVE_API_KEY") or "").strip()),
            },
            "searchBudgetSec": env("SEARCH_BUDGET", "25"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
