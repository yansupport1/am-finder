"""python3 -m unittest tests/test_trending.py -v"""
import importlib.util, os, time, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("amf_trending", os.path.join(HERE, "..", "api", "trending.py"))
T = importlib.util.module_from_spec(spec); spec.loader.exec_module(T)

DEEZER = {"data": [{"title": "Long Title (feat. X)", "title_short": "Short", "artist": {"name": "Artist A"},
                    "album": {"cover_medium": "https://cdn/c1.jpg"}, "link": "https://deezer/1"},
                   {"title": "Two", "artist": {"name": "B"}, "album": {}, "link": ""}]}
APPLE = {"feed": {"results": [{"name": "Apple Song", "artistName": "Ap", "artworkUrl100": "https://a/1.jpg", "url": "https://a/u"}]}}


class Case(unittest.TestCase):
    def setUp(self):
        T._cache.update(ts=0, items=[], source="", want=0)
        self.orig = T.fetch_json

    def tearDown(self):
        T.fetch_json = self.orig

    def test_deezer_normalised(self):
        T.fetch_json = lambda url, timeout=8: DEEZER
        items, src, stale = T.get_trending(20)
        self.assertEqual(src, "Deezer Global Chart"); self.assertFalse(stale)
        self.assertEqual(items[0], {"rank": 1, "title": "Short", "artist": "Artist A", "cover": "https://cdn/c1.jpg", "url": "https://deezer/1"})
        self.assertEqual(items[1]["title"], "Two")

    def test_fallback_to_apple(self):
        def f(url, timeout=8):
            if "deezer" in url: raise OSError("down")
            return APPLE
        T.fetch_json = f
        items, src, _ = T.get_trending(10)
        self.assertEqual((src, items[0]["title"]), ("Apple Music Most Played", "Apple Song"))

    def test_cache_and_stale(self):
        n = {"c": 0}
        def f(url, timeout=8):
            n["c"] += 1; return DEEZER
        T.fetch_json = f
        T.get_trending(20); T.get_trending(20)
        self.assertEqual(n["c"], 1)                       # panggilan kedua dari cache
        T._cache["ts"] -= T.TTL + 5                       # kedaluwarsa
        T.fetch_json = lambda url, timeout=8: (_ for _ in ()).throw(OSError("down"))
        items, src, stale = T.get_trending(20)
        self.assertTrue(stale); self.assertEqual(items[0]["title"], "Short")

    def test_all_down_empty(self):
        T.fetch_json = lambda url, timeout=8: (_ for _ in ()).throw(OSError("down"))
        self.assertEqual(T.get_trending(20)[0], [])

if __name__ == "__main__":
    unittest.main()
