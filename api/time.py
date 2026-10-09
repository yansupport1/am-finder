"""GET /api/time — current server time ISO + unix."""
from http.server import BaseHTTPRequestHandler
from datetime import datetime, timezone
import json

class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
