"""Analytics stub — frontend POSTs here"""
from http.server import BaseHTTPRequestHandler
import json
import re
import sys
from urllib.parse import urlparse, parse_qs


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

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        if not guard_ok(self.headers):
            return deny(self)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        q = parse_qs(urlparse(self.path).query)
        if (q.get("lock") or [""])[0] == "1":
            # beacon dari halaman pengunci domain di salinan tidak sah: hanya catat (dipangkas & dibersihkan)
            clean = lambda v: re.sub(r"[^\w.:/\-]", "", (v or [""])[0])[:120]
            try:
                sys.stderr.write("[lock] salinan tidak sah host=%s url=%s\n" % (clean(q.get("h")), clean(q.get("u"))))
            except Exception:
                pass
            self.send_response(204)
            self.end_headers()
            return
        if not guard_ok(self.headers):
            return deny(self)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())

    def do_GET(self):
        if not guard_ok(self.headers):
            return deny(self)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True}).encode())
