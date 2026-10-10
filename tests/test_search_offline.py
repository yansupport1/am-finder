"""Uji offline api/search.py (tanpa internet): python3 -m unittest tests/test_search_offline.py -v"""
import importlib.util, os, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("amf_search", os.path.join(HERE, "..", "api", "search.py"))
S = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S)

VID1, VID2, VID3 = "7312345678901234567", "7312345678901234568", "7312345678901234569"


def page(*urls):
    return "<html>" + "".join('<a href="%s">x</a>' % u for u in urls) + "</html>"


class Env:
    """Mock S.fetch. mode: dict engine-substring -> callable(url)->Resp"""
    def __init__(self, handler):
        self.handler, self.calls = handler, 0

    def __call__(self, url, **kw):
        self.calls += 1
        if "oembed" in url:
            return S.Resp(200, '{"title":"Negoro angin preset https://alight.link/abc","thumbnail_url":"https://x/c.jpg","author_unique_id":"zed"}', url, None)
        if "tiktok.com/@" in url:  # halaman video
            return S.Resp(403, "", url, "http 403")
        return self.handler(url, kw)


class T(unittest.TestCase):
    def setUp(self):
        S._CACHE.clear()
        os.environ["SEARCH_BUDGET"] = "8"
        self.orig = S.fetch

    def tearDown(self):
        S.fetch = self.orig

    # --- regex ---
    def test_regex_forms(self):
        cases = [
            "https://www.tiktok.com/@user/video/123",
            "http://tiktok.com/@user/video/123",
            "tiktok.com/@user/video/123",
            "https%3A%2F%2Fwww.tiktok.com%2F%40user%2Fvideo%2F123",
            "m.tiktok.com/@user/video/123?lang=id",
            "https://www.tiktok.com \u203a @user \u203a video \u203a 123",
            '<cite>www.tiktok.com</cite><span> \u203a </span>@user \u203a video \u203a 123',
        ]
        for c in cases:
            got = S.extract_tiktok_urls(c)
            self.assertEqual([g[1] for g in got], ["https://www.tiktok.com/@user/video/123"], c)

    def test_regex_negative(self):
        for c in ["faketiktok.com/@user/video/123", "tiktok.com/@user/photo/123", "tiktok.com/@user"]:
            self.assertEqual(S.extract_tiktok_urls(c), [], c)

    def test_bing_redirect(self):
        import base64
        u = base64.urlsafe_b64encode(b"https://www.tiktok.com/@a.b_c/video/" + VID1.encode()).decode().rstrip("=")
        html = '<a href="https://www.bing.com/ck/a?!&amp;&amp;p=zz&amp;u=a1%s&amp;ntb=1">t</a>' % u
        got = S.extract_tiktok_urls(html)
        self.assertEqual(got[0][1], "https://www.tiktok.com/@a.b_c/video/" + VID1)

    # --- behaviour ---
    def test_repeated_calls_with_ddg_blocked_after_first(self):
        n = {"ddg": 0}

        def h(url, kw):
            if "duckduckgo" in url:
                n["ddg"] += 1
                return S.Resp(202, "<html>anomaly-modal</html>", url, "http 202") if n["ddg"] > 2 else S.Resp(200, page("https://www.tiktok.com/@zed/video/" + VID1), url, None)
            if "bing.com" in url:
                return S.Resp(200, page("tiktok.com/@zed/video/" + VID2, "https://www.tiktok.com/@zed/video/" + VID3), url, None)
            return S.Resp(429, "", url, "http 429")
        S.fetch = Env(h)
        for q in ["Negoro angin", "Negoro angin", "lagu lain", "kata baru", "x y z"]:
            S._CACHE.clear()
            r = S.run_search(q)
            self.assertTrue(r["count"] >= 2, (q, r["status"]))
            for it in r["results"]:
                for k in ("id", "videoUrl", "author", "description", "cover", "stats", "presetLinks", "playUrl"):
                    self.assertIn(k, it)
                self.assertTrue(it["videoUrl"].startswith("https://www.tiktok.com/@"))
                self.assertTrue(it["description"] and it["author"])

    def test_all_blocked_message_and_no_negative_cache(self):
        def blocked(url, kw):
            return S.Resp(202, "anomaly-modal", url, "http 202")
        S.fetch = Env(blocked)
        r = S.run_search("negoro angin")
        self.assertEqual((r["status"], r["count"], r["blocked"]), ("blocked", 0, True))
        self.assertIn("Cari via Link Video", r["message"])
        self.assertNotIn("tidak ada preset", r["message"].lower())
        # begitu pulih, request berikutnya langsung berhasil (tidak ada cache kosong yang menempel)
        S.fetch = Env(lambda url, kw: S.Resp(200, page("https://www.tiktok.com/@zed/video/" + VID1), url, None) if "bing" in url else S.Resp(429, "", url, None))
        r2 = S.run_search("negoro angin")
        self.assertEqual(r2["status"], "ok")

    def test_genuine_empty_short_cache(self):
        S.fetch = Env(lambda url, kw: S.Resp(200, "<html>no results</html>", url, None))
        r = S.run_search("zzzqqq")
        self.assertEqual(r["status"], "empty")
        self.assertIn("tidak berarti", r["message"])
        self.assertLessEqual(S.TTL_EMPTY, 30)

    def test_stale_fallback_when_blocked(self):
        S.cache_put("kata", [S.make_item(VID1, "https://www.tiktok.com/@a/video/" + VID1, "a", {})])
        S._CACHE["kata"]["ts"] -= S.TTL_OK + 5  # kedaluwarsa tapi masih < TTL_STALE
        S.fetch = Env(lambda url, kw: S.Resp(202, "anomaly-modal", url, "http 202"))
        r = S.run_search("kata")
        self.assertEqual((r["status"], r["count"]), ("stale", 1))

    def test_deadline_respected(self):
        def slow(url, kw):
            time.sleep(3)
            return S.Resp(0, "", url, "Timeout")
        S.fetch = Env(slow)
        t = time.time()
        r = S.run_search("lambat")
        self.assertLess(time.time() - t, 8.5)
        self.assertEqual(r["count"], 0)
        self.assertIn(r["status"], ("blocked", "unreachable"))


if __name__ == "__main__":
    unittest.main()
