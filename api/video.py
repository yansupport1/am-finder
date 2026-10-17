"""
Proxy media TikTok supaya <video> bisa diputar (menghindari CORS / hotlink).
GET /api/video?url=<encoded_media_url>

Perbaikan: fungsi serverless Vercel membatasi ukuran respons (~4,5 MB), jadi video
yang lebih besar gagal total. Sekarang respons SELALU dipotong per potongan <= CHUNK
sebagai 206 Partial Content + Content-Range; browser otomatis meminta potongan
berikutnya saat memutar/seek.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import Request, urlopen
import re

UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

ALLOWED = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:tiktokcdn|tiktokv|musical\.ly|byteoversea|ibytedtos|tiktok)\.[\w.-]+/",
    re.I,
)

CHUNK = 3900000  # < 4,5 MB batas respons Vercel


def parse_range(h):
    """'bytes=a-b' -> (a, b|None); selain itu (0, None)."""
    m = re.match(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)", h or "")
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return 0, None
    if m.group(1) == "":  # suffix range: biarkan player minta ulang dari awal
        return 0, None
    return int(m.group(1)), (int(m.group(2)) if m.group(2) else None)


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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def _fail(self, code, msg):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.end_headers()
        self.wfile.write(msg.encode()[:200])

    def do_GET(self):
        if not guard_ok(self.headers):
            return deny(self)
        qs = parse_qs(urlparse(self.path).query)
        url = unquote((qs.get("url") or [""])[0]).strip()
        if not url or not url.startswith("http"):
            return self._fail(400, "missing url")
        if not ALLOWED.search(url):
            return self._fail(403, "host not allowed")

        start, end = parse_range(self.headers.get("Range"))
        want_end = start + CHUNK - 1
        if end is not None:
            want_end = min(end, want_end)

        try:
            req = Request(url, headers={
                "User-Agent": UA,
                "Referer": "https://www.tiktok.com/",
                "Accept": "*/*",
                "Range": "bytes=%d-%d" % (start, want_end),
            })
            with urlopen(req, timeout=25) as resp:
                ctype = resp.headers.get("Content-Type") or "video/mp4"
                if "text/html" in ctype.lower():
                    return self._fail(502, "upstream returned html")
                crange = resp.headers.get("Content-Range")
                total = None
                if crange:
                    m = re.search(r"/(\d+)\s*$", crange)
                    total = int(m.group(1)) if m else None
                    got_start = int(re.search(r"bytes\s+(\d+)-", crange).group(1)) if re.search(r"bytes\s+(\d+)-", crange) else start
                    body = resp.read(CHUNK)
                else:
                    # upstream mengabaikan Range (200 penuh): potong sendiri
                    cl = resp.headers.get("Content-Length")
                    total = int(cl) if cl and cl.isdigit() else None
                    skipped = 0
                    while skipped < start:
                        d = resp.read(min(65536, start - skipped))
                        if not d:
                            break
                        skipped += len(d)
                    got_start = start
                    body = resp.read(want_end - start + 1)
                if not body:
                    return self._fail(416, "range not satisfiable")
                last = got_start + len(body) - 1
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", "bytes %d-%d/%s" % (got_start, last, total if total else "*"))
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
                self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()
                self.wfile.write(body)
        except Exception as e:
            self._fail(502, "proxy error: " + str(e)[:150])
