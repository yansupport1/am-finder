"""GET /api/version - cek cepat apakah deploy terbaru yang aktif (dan fitur apa yang menyala)."""
from http.server import BaseHTTPRequestHandler
import json
import os

VERSION = "3.1"
BUILD = "2026-10-10"


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
