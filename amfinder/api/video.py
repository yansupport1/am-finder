"""
Proxy TikTok media so <video> can play (avoids CORS / hotlink blocks).
GET /api/video?url=<encoded_media_url>
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


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Range")
        self.end_headers()

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        url = unquote((qs.get("url") or [""])[0]).strip()

        if not url or not url.startswith("http"):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"missing url")
            return

        if not ALLOWED.search(url):
            self.send_response(403)
            self.end_headers()
            self.wfile.write(b"host not allowed")
            return

        try:
            headers = {
                "User-Agent": UA,
                "Referer": "https://www.tiktok.com/",
                "Accept": "*/*",
            }
            range_h = self.headers.get("Range")
            if range_h:
                headers["Range"] = range_h

            req = Request(url, headers=headers)
            with urlopen(req, timeout=30) as resp:
                status = resp.status
                content_type = resp.headers.get("Content-Type") or "video/mp4"
                content_len = resp.headers.get("Content-Length")
                accept_ranges = resp.headers.get("Accept-Ranges")
                content_range = resp.headers.get("Content-Range")

                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges")
                if content_len:
                    self.send_header("Content-Length", content_len)
                if accept_ranges:
                    self.send_header("Accept-Ranges", accept_ranges)
                if content_range:
                    self.send_header("Content-Range", content_range)
                self.send_header("Cache-Control", "public, max-age=3600")
                self.end_headers()

                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        except Exception as e:
            self.send_response(502)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(("proxy error: " + str(e)[:150]).encode())
