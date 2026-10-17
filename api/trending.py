"""
GET /api/trending?limit=20
Lagu yang paling banyak diputar secara global, dari chart publik (tanpa API key):
  1) Deezer Global Chart  (api.deezer.com/chart/0/tracks)
  2) cadangan: Apple Music "Most Played" (rss.applemarketingtools.com)
Catatan: ini chart PEMUTARAN global, bukan jumlah pencarian (tidak ada API publik resmi untuk volume
pencarian). Hasil di-cache 30 menit per instance + header CDN supaya sumber tidak dipukul berulang.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen
import json
import threading
import time

UA = "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
TTL = 1800
STALE_TTL = 6 * 3600
_cache = {"ts": 0, "items": [], "source": "", "want": 0}
_lock = threading.Lock()


def fetch_json(url, timeout=8):
    req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2000000).decode("utf-8", "ignore"))


def from_deezer(limit):
    j = fetch_json("https://api.deezer.com/chart/0/tracks?limit=%d" % limit)
    out = []
    for i, t in enumerate(j.get("data") or []):
        title = (t.get("title_short") or t.get("title") or "").strip()
        artist = ((t.get("artist") or {}).get("name") or "").strip()
        if not title:
            continue
        alb = t.get("album") or {}
        out.append({"rank": i + 1, "title": title, "artist": artist,
                    "cover": alb.get("cover_medium") or alb.get("cover") or "", "url": t.get("link") or ""})
    return out, "Deezer Global Chart"


def from_apple(limit):
    j = fetch_json("https://rss.applemarketingtools.com/api/v2/us/music/most-played/%d/songs.json" % limit)
    out = []
    for i, t in enumerate((j.get("feed") or {}).get("results") or []):
        if not t.get("name"):
            continue
        out.append({"rank": i + 1, "title": t["name"].strip(), "artist": (t.get("artistName") or "").strip(),
                    "cover": t.get("artworkUrl100") or "", "url": t.get("url") or ""})
    return out, "Apple Music Most Played"


def get_trending(limit=20):
    limit = max(5, min(int(limit or 20), 50))
    now = time.time()
    with _lock:
        if _cache["items"] and now - _cache["ts"] < TTL and _cache["want"] >= min(limit, 20):
            return _cache["items"][:limit], _cache["source"], False
    for fn in (from_deezer, from_apple):
        try:
            items, source = fn(max(limit, 20))
            if items:
                with _lock:
                    _cache.update(ts=now, items=items, source=source, want=max(limit, 20))
                return items[:limit], source, False
        except Exception:
            continue
    with _lock:   # semua sumber gagal -> pakai cache lama bila masih wajar
        if _cache["items"] and now - _cache["ts"] < STALE_TTL:
            return _cache["items"][:limit], _cache["source"], True
    return [], "", False


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
        qs = parse_qs(urlparse(self.path).query)
        try:
            limit = int((qs.get("limit") or ["20"])[0])
        except Exception:
            limit = 20
        items, source, stale = get_trending(limit)
        body = json.dumps({"ok": bool(items), "source": source, "stale": stale, "count": len(items), "items": items},
                          ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "public, max-age=600, s-maxage=1800, stale-while-revalidate=3600" if items else "no-store")
        self.send_header("Access-Control-Allow-Origin", ALLOW_ORIGIN)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
