"""Uji proxy video: python3 -m unittest tests/test_video_proxy.py -v"""
import importlib.util, os, re, threading, unittest, urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("amf_video", os.path.join(HERE, "..", "api", "video.py"))
V = importlib.util.module_from_spec(spec); spec.loader.exec_module(V)
DATA = bytes(range(256)) * 40000  # ~10 MB


def make_upstream(support_range):
    class U(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            rng = self.headers.get("Range")
            m = re.match(r"bytes=(\d+)-(\d+)?", rng or "")
            if support_range and m:
                a = int(m.group(1)); b = min(int(m.group(2) or len(DATA) - 1), len(DATA) - 1)
                body = DATA[a:b + 1]
                self.send_response(206)
                self.send_header("Content-Range", "bytes %d-%d/%d" % (a, b, len(DATA)))
            else:
                body = DATA; self.send_response(200)
            self.send_header("Content-Type", "video/mp4"); self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
    s = HTTPServer(("127.0.0.1", 0), U); threading.Thread(target=s.serve_forever, daemon=True).start(); return s


class T(unittest.TestCase):
    def run_case(self, support_range, rng):
        V.ALLOWED = re.compile(r"http://127\.0\.0\.1")
        up = make_upstream(support_range)
        px = HTTPServer(("127.0.0.1", 0), V.handler); threading.Thread(target=px.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d/v.mp4" % up.server_port
        req = urllib.request.Request("http://127.0.0.1:%d/?url=%s" % (px.server_port, urllib.request.quote(url, safe="")))
        if rng: req.add_header("Range", rng)
        with urllib.request.urlopen(req) as r:
            body = r.read(); return r.status, r.headers, body

    def test_no_range_header_is_capped_206(self):
        for support in (True, False):
            st, h, body = self.run_case(support, None)
            self.assertEqual(st, 206); self.assertLessEqual(len(body), 4500000)
            self.assertTrue(h["Content-Range"].startswith("bytes 0-")); self.assertTrue(h["Content-Range"].endswith("/%d" % len(DATA)))
            self.assertEqual(body, DATA[:len(body)])

    def test_mid_range(self):
        for support in (True, False):
            st, h, body = self.run_case(support, "bytes=5000000-")
            self.assertEqual(st, 206); self.assertEqual(body, DATA[5000000:5000000 + len(body)])
            self.assertIn("bytes 5000000-", h["Content-Range"])

    def test_small_range(self):
        st, h, body = self.run_case(True, "bytes=10-19")
        self.assertEqual((st, len(body)), (206, 10))

if __name__ == "__main__":
    unittest.main()
