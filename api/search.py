"""
Search TikTok videos by caption keyword + #preset, extract AM preset links.
GET /api/search?q=<keyword>
SSE or JSON: { ok, query, results: [ { videoUrl, description, author, cover, presetLinks[] } ] }
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen
import json
import re
import time

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Mobile/15E148 Safari/604.1"
)

AM_PATTERNS = [
    re.compile(r"https?://(?:www\.)?alightcreative\.com/am/share/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?alight\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?am\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://link\.alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?[^\s\"']*id=)[^\s\"'<>]+", re.I),
    re.compile(r"https?://drive\.google\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?mediafire\.com/file/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?mega\.nz/[^\s\"'<>]+", re.I),
    re.compile(r"https?://t\.me/[^\s\"'<>]+", re.I),
]

PRESET_HINT = re.compile(r"(preset|alight|#preset|#am|#alightmotion|xml|5mb)", re.I)


def http_get(url, timeout=18):
    req = Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/json,*/*",
        "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
        "Referer": "https://www.tiktok.com/",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore"), r.geturl()


def extract_preset_links(text):
    if not text:
        return []
    found = []
    seen = set()
    for pat in AM_PATTERNS:
        for m in pat.findall(text):
            u = m.rstrip(".,);]'\"")
            if u not in seen:
                seen.add(u)
                found.append(u)
    return found


def dig_videos(obj, out, depth=0):
    if depth > 14 or obj is None:
        return
    if isinstance(obj, list):
        for v in obj[:80]:
            dig_videos(v, out, depth + 1)
        return
    if not isinstance(obj, dict):
        return

    # item structure
    desc = obj.get("desc") or obj.get("description") or ""
    if isinstance(desc, str) and desc:
        author = ""
        cover = ""
        vid_id = str(obj.get("id") or obj.get("aweme_id") or obj.get("video_id") or "")
        auth = obj.get("author") or obj.get("authorInfo") or {}
        if isinstance(auth, dict):
            author = auth.get("uniqueId") or auth.get("unique_id") or auth.get("nickname") or ""
            cover = (
                auth.get("avatarThumb")
                or auth.get("avatarMedium")
                or ""
            )
        video = obj.get("video") or {}
        if isinstance(video, dict):
            cover = video.get("cover") or video.get("originCover") or cover or ""
            play = ""
            for key in ("playAddr", "downloadAddr", "play_addr"):
                val = video.get(key)
                if isinstance(val, str) and val.startswith("http"):
                    play = val
                    break
                if isinstance(val, dict):
                    ul = val.get("UrlList") or val.get("url_list") or []
                    if ul:
                        play = ul[0]
                        break
        else:
            play = ""

        if vid_id or ("#preset" in desc.lower() or PRESET_HINT.search(desc)):
            item = {
                "id": vid_id,
                "description": desc,
                "author": author,
                "cover": cover if isinstance(cover, str) else "",
                "playUrl": play if isinstance(play, str) else "",
                "videoUrl": f"https://www.tiktok.com/@{author}/video/{vid_id}" if author and vid_id else (
                    f"https://www.tiktok.com/video/{vid_id}" if vid_id else ""
                ),
                "presetLinks": extract_preset_links(desc),
            }
            # dedupe by id or desc
            key = vid_id or desc[:80]
            if key and not any(x.get("id") == vid_id and vid_id for x in out):
                if not any(x.get("description") == desc for x in out):
                    out.append(item)

    for v in obj.values():
        dig_videos(v, out, depth + 1)


def parse_hydration(html):
    videos = []
    for pat in [
        r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'window\["SIGI_STATE"\]\s*=\s*({.*?});',
    ]:
        m = re.search(pat, html, re.S)
        if not m:
            continue
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
            dig_videos(data, videos)
        except Exception:
            continue
    return videos


def search_tiktok(query):
    """Best-effort TikTok search for videos matching query + preset context."""
    q = query.strip()
    if not q:
        return []

    # Prefer query that includes #preset so results are preset-related
    variants = []
    if "#preset" not in q.lower():
        variants.append(f"{q} #preset")
        variants.append(f"{q} preset alight")
    variants.append(q)

    all_videos = []
    seen_ids = set()

    for kw in variants[:2]:
        # 1) Search page HTML
        for path in (
            f"https://www.tiktok.com/search?q={quote(kw)}",
            f"https://www.tiktok.com/search/video?q={quote(kw)}",
        ):
            try:
                html, _ = http_get(path, timeout=16)
                vids = parse_hydration(html)
                for v in vids:
                    vid = v.get("id") or ""
                    if vid and vid in seen_ids:
                        continue
                    if vid:
                        seen_ids.add(vid)
                    all_videos.append(v)
                if all_videos:
                    break
            except Exception:
                continue
        if len(all_videos) >= 8:
            break

        # 2) Try public search item API (often blocked without cookie)
        try:
            api = (
                "https://www.tiktok.com/api/search/item/full/"
                f"?keyword={quote(kw)}&offset=0&count=12&search_id=&search_source=normal_search"
            )
            raw, _ = http_get(api, timeout=12)
            data = json.loads(raw)
            dig_videos(data, all_videos)
        except Exception:
            pass

    # Rank: has preset links first, then has #preset in caption, then keyword match
    ql = q.lower()

    def score(v):
        d = (v.get("description") or "").lower()
        s = 0
        if v.get("presetLinks"):
            s += 50
        if "#preset" in d:
            s += 20
        if ql in d:
            s += 10
        for part in ql.split():
            if part and part in d:
                s += 3
        return s

    all_videos.sort(key=score, reverse=True)

    # Keep top results; still return videos without links so user can open TikTok
    return all_videos[:15]


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        q = unquote((qs.get("q") or qs.get("query") or [""])[0]).strip()

        if not q:
            body = json.dumps({"ok": False, "error": "missing q"}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return

        try:
            results = search_tiktok(q)
            # Proxy play urls
            for r in results:
                pu = r.get("playUrl") or ""
                if pu.startswith("http"):
                    r["playUrl"] = "/api/video?url=" + quote(pu, safe="")
            out = {
                "ok": True,
                "query": q,
                "count": len(results),
                "results": results,
                "message": None if results else "yah gada preset nya / video tidak ketemu",
            }
            body = json.dumps(out, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"ok": False, "error": str(e)[:200]}).encode()
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
