"""python3 -m unittest tests/test_find_links.py -v   (tanpa internet)"""
import importlib.util, json, os, socket, threading, unittest, urllib.request
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("amf_find", os.path.join(HERE, "..", "api", "find.py"))
F = importlib.util.module_from_spec(spec); spec.loader.exec_module(F)

LONG = "https://www.tiktok.com/@burzpejeje/video/7591289001781349650"


class Normalize(unittest.TestCase):
    def test_tiktok_forms(self):
        cases = [
            LONG,
            LONG + "?is_from_webapp=1&sender_device=pc",
            "www.tiktok.com/@burzpejeje/video/7591289001781349650",
            "tiktok.com/@burzpejeje/video/7591289001781349650",
            "https://m.tiktok.com/v/7591289001781349650.html",
            "https://vt.tiktok.com/ZSb3Nafsc/",
            "https://vm.tiktok.com/ZMabc123/",
            "vt.tiktok.com/ZSb3Nafsc/",
            "https://www.tiktok.com/t/ZTabc123/",
            "Lihat video ini dari @zee! https://vt.tiktok.com/ZSb3Nafsc/ #fyp #preset",
            "  " + LONG + "  ",
        ]
        for c in cases:
            url, kind = F.normalize_input(c)
            self.assertEqual(kind, "tiktok", c)
            self.assertIn("tiktok.com", url, c)
        self.assertEqual(F.normalize_input("https://vt.tiktok.com/ZSb3Nafsc/")[0], "https://vt.tiktok.com/ZSb3Nafsc/")
        self.assertEqual(F.normalize_input("Cek https://vt.tiktok.com/ZSb3Nafsc/ #fyp")[0], "https://vt.tiktok.com/ZSb3Nafsc/")

    def test_bare_id(self):
        self.assertEqual(F.normalize_input("7591289001781349650"), ("https://www.tiktok.com/@_/video/7591289001781349650", "tiktok"))

    def test_other_domains_are_generic(self):
        for c, host in [("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"), ("https://youtu.be/dQw4w9WgXcQ", "youtu.be"),
                        ("https://www.instagram.com/reel/Cabc123/", "instagram"), ("linktr.ee/zee", "linktr.ee")]:
            url, kind = F.normalize_input(c)
            self.assertEqual(kind, "generic", c); self.assertIn(host, url)

    def test_not_a_link(self):
        for c in ("halo dunia", "", "negoro angin", "12345"):
            self.assertEqual(F.normalize_input(c), (None, None), c)

    def test_lookalike_hosts_are_not_tiktok(self):
        for c in ("https://faketiktok.com/@a/video/1234567890123456", "https://tiktok.com.evil.io/@a/video/1234567890123456"):
            self.assertEqual(F.normalize_input(c)[1], "generic", c)

    def test_video_id_variants(self):
        for u in (LONG, "https://www.tiktok.com/@a/photo/7591289001781349650", "https://m.tiktok.com/v/7591289001781349650.html",
                  "https://www.tiktok.com/embed/v2/7591289001781349650", "https://www.tiktok.com/x?share_item_id=7591289001781349650"):
            self.assertEqual(F.parse_video_id(u), "7591289001781349650", u)


class Safety(unittest.TestCase):
    def test_blocks_internal(self):
        for u in ("http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/",
                  "http://192.168.1.1/", "http://[::1]/", "https://user:pw@8.8.8.8/", "http://8.8.8.8:8080/", "file:///etc/passwd", "ftp://8.8.8.8/",
                  "http://127.0.0.1/?x=tiktok.com"):
            self.assertFalse(F.safe_public_url(u), u)

    def test_allows_public_literal(self):
        self.assertTrue(F.safe_public_url("https://8.8.8.8/"))


class Scrape(unittest.TestCase):
    def setUp(self):
        self.orig = (F.http_get, F.fetch_oembed, F.fetch_comments_api)

    def tearDown(self):
        F.http_get, F.fetch_oembed, F.fetch_comments_api = self.orig

    def test_long_tiktok_link_end_to_end(self):
        html = '<html><body>desc https://www.alightcreative.com/am/share/AbC123 </body></html>'
        F.http_get = lambda url, timeout=18, method="GET": (url, html, 200)
        F.fetch_oembed = lambda url: {"title": "negoro angin", "author_name": "burz", "thumbnail_url": "https://c/x.jpg"}
        F.fetch_comments_api = lambda vid, count=50: ["preset di https://alight.link/Zzz999 ya"]
        r = F.scrape_tiktok(LONG)
        self.assertTrue(r["ok"]); self.assertEqual(r["videoId"], "7591289001781349650")
        urls = [p["url"] for p in r["presetLinks"]]
        self.assertTrue(any("alightcreative.com/am/share/AbC123" in u for u in urls), urls)
        self.assertTrue(any("alight.link/Zzz999" in u for u in urls), urls)
        self.assertEqual(r["author"], "@burzpejeje")
        self.assertTrue(r["video"]["embedUrl"].endswith("7591289001781349650"))

    def test_generic_youtube(self):
        html = ('<html><head><meta property="og:title" content="Tutorial AM"><meta property="og:image" content="https://i/y.jpg"></head>'
                '<script>var d={"shortDescription":"Preset: https:\\/\\/alight.link\\/YtPre1 \\n5mb","ownerChannelName":"Zee Ch"};</script></html>')
        F.http_get = lambda url, timeout=18, method="GET": ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", html, 200)
        r = F.scrape_generic("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assertEqual((r["platform"], r["author"], r["videoId"]), ("YouTube", "@Zee Ch", "dQw4w9WgXcQ"))
        self.assertEqual(r["video"]["embedUrl"], "https://www.youtube.com/embed/dQw4w9WgXcQ")
        self.assertTrue(any("alight.link/YtPre1" in p["url"] for p in r["presetLinks"]), r["presetLinks"])
        self.assertEqual(r["video"]["cover"], "https://i/y.jpg")


class Endpoint(unittest.TestCase):
    def call(self, url):
        srv = HTTPServer(("127.0.0.1", 0), F.handler); threading.Thread(target=srv.serve_forever, daemon=True).start()
        with urllib.request.urlopen("http://127.0.0.1:%d/?url=%s" % (srv.server_port, urllib.request.quote(url, safe=""))) as r:
            body = r.read().decode()
        srv.shutdown()
        data = [l for l in body.splitlines() if l.startswith("data: ")][0][6:]
        return json.loads(data)

    def test_errors_and_routing(self):
        orig = (F.safe_public_url, F.scrape_tiktok, F.scrape_generic)
        try:
            F.safe_public_url = lambda u: True
            F.scrape_tiktok = lambda u: {"ok": True, "kind": "tiktok", "u": u, "presetLinks": []}
            F.scrape_generic = lambda u: {"ok": True, "kind": "generic", "u": u, "presetLinks": []}
            r = self.call("halo dunia ini bukan link")
            self.assertFalse(r["ok"]); self.assertIn("valid video or page link", r["error"])
            r = self.call("Cek " + LONG + " #fyp")
            self.assertEqual((r["kind"], r["u"], r["platform"]), ("tiktok", LONG, "TikTok"))
            r = self.call("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
            self.assertEqual(r["kind"], "generic")
        finally:
            F.safe_public_url, F.scrape_tiktok, F.scrape_generic = orig
        r = self.call("http://127.0.0.1/admin?x=tiktok.com")          # tanpa mock: harus ditolak
        self.assertFalse(r["ok"]); self.assertIn("not a public website", r["error"])


if __name__ == "__main__":
    unittest.main()
