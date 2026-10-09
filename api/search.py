"""
GET /api/search?q=<keyword>
Returns TikTok videos related to query + preset context with full public stats when available.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen
import json
import re

UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
UA_WEB = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
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


def http_get(url, ua=UA_MOBILE, timeout=20):
    req = Request(url, headers={
        "User-Agent": ua,
        "Accept": "text/html,application/json,application/xhtml+xml,*/*",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.tiktok.com/",
        "Cookie": "tt_csrf_token=tt; tt_chain_token=tt",
    })
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore"), r.geturl()


def extract_preset_links(text):
    if not text:
        return []
    found, seen = [], set()
    for pat in AM_PATTERNS:
        for m in pat.findall(text):
            u = m.rstrip(".,);]'\"<>\\")
            if u not in seen:
                seen.add(u)
                found.append(u)
    return found


def as_int(v):
    try:
        if v is None:
            return None
        return int(v)
    except Exception:
        return None


def dig_items(obj, bag, depth=0):
    if depth > 16 or obj is None:
        return
    if isinstance(obj, list):
        for v in obj[:120]:
            dig_items(v, bag, depth + 1)
        return
    if not isinstance(obj, dict):
        return

    desc = obj.get("desc") or obj.get("description") or obj.get("title") or ""
    stats = obj.get("stats") or obj.get("statistics") or {}
    author = obj.get("author") or obj.get("authorInfo") or obj.get("authorStats") or {}
    video = obj.get("video") or {}
    vid_id = obj.get("id") or obj.get("aweme_id") or obj.get("video_id") or obj.get("itemId")

    has_desc = isinstance(desc, str) and len(desc) > 0
    has_id = vid_id is not None and str(vid_id).isdigit() or (isinstance(vid_id, str) and len(str(vid_id)) > 5)

    if has_desc and (has_id or isinstance(stats, dict) and stats):
        if not isinstance(author, dict):
            author = {}
        if not isinstance(video, dict):
            video = {}
        if not isinstance(stats, dict):
            stats = {}

        unique = (
            author.get("uniqueId")
            or author.get("unique_id")
            or author.get("unique_id")
            or author.get("nickname")
            or ""
        )
        cover = (
            video.get("cover")
            or video.get("originCover")
            or video.get("dynamicCover")
            or author.get("avatarThumb")
            or author.get("avatarMedium")
            or ""
        )
        if isinstance(cover, dict):
            ul = cover.get("url_list") or cover.get("UrlList") or []
            cover = ul[0] if ul else ""

        play = ""
        for key in ("playAddr", "downloadAddr", "play_addr", "download_addr"):
            val = video.get(key)
            if isinstance(val, str) and val.startswith("http"):
                play = val
                break
            if isinstance(val, dict):
                ul = val.get("UrlList") or val.get("url_list") or []
                if ul:
                    play = ul[0]
                    break

        views = as_int(
            stats.get("playCount")
            or stats.get("play_count")
            or stats.get("views")
            or obj.get("playCount")
        )
        likes = as_int(
            stats.get("diggCount")
            or stats.get("digg_count")
            or stats.get("likes")
            or obj.get("diggCount")
        )
        comments = as_int(
            stats.get("commentCount")
            or stats.get("comment_count")
            or stats.get("comments")
            or obj.get("commentCount")
        )
        shares = as_int(stats.get("shareCount") or stats.get("share_count") or stats.get("shares"))

        vid_id = str(vid_id or "")
        item = {
            "id": vid_id,
            "description": desc if isinstance(desc, str) else "",
            "author": str(unique or "").lstrip("@"),
            "cover": cover if isinstance(cover, str) else "",
            "playUrl": play if isinstance(play, str) else "",
            "stats": {
                "views": views,
                "likes": likes,
                "comments": comments,
                "shares": shares,
            },
            "videoUrl": (
                f"https://www.tiktok.com/@{str(unique).lstrip('@')}/video/{vid_id}"
                if unique and vid_id
                else (f"https://www.tiktok.com/video/{vid_id}" if vid_id else "")
            ),
            "presetLinks": extract_preset_links(desc if isinstance(desc, str) else ""),
        }
        key = vid_id or (item["description"][:60] + item["author"])
        if key and key not in bag["_seen"]:
            bag["_seen"].add(key)
            bag["items"].append(item)

    for v in obj.values():
        dig_items(v, bag, depth + 1)


def parse_json_blobs(html):
    items = []
    bag = {"items": [], "_seen": set()}
    patterns = [
        r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r'window\["SIGI_STATE"\]\s*=\s*(\{.*?\});',
        r'<script[^>]*>\s*window\.__UNIVERSAL_DATA_FOR_REHYDRATION__\s*=\s*(\{.*?\});\s*</script>',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, re.S):
            raw = m.group(1).strip()
            try:
                data = json.loads(raw)
                dig_items(data, bag)
            except Exception:
                continue
    # Also try to find embedded itemStruct JSON fragments
    for m in re.finditer(r'"desc"\s*:\s*"((?:\\.|[^"\\])*)"', html):
        try:
            desc = json.loads('"' + m.group(1) + '"')
        except Exception:
            continue
        if not desc or len(desc) < 3:
            continue
        # surrounding window for id/author roughly
        start = max(0, m.start() - 800)
        chunk = html[start:m.end() + 800]
        id_m = re.search(r'"id"\s*:\s*"(\d{5,})"', chunk)
        uid_m = re.search(r'"uniqueId"\s*:\s*"([^"]+)"', chunk)
        vid = id_m.group(1) if id_m else ""
        uid = uid_m.group(1) if uid_m else ""
        key = vid or (desc[:60] + uid)
        if key in bag["_seen"]:
            continue
        bag["_seen"].add(key)
        bag["items"].append({
            "id": vid,
            "description": desc,
            "author": uid,
            "cover": "",
            "playUrl": "",
            "stats": {"views": None, "likes": None, "comments": None, "shares": None},
            "videoUrl": f"https://www.tiktok.com/@{uid}/video/{vid}" if uid and vid else "",
            "presetLinks": extract_preset_links(desc),
        })
    return bag["items"]


def enrich_from_video_page(item):
    """Open video page to read full caption + stats when thin."""
    url = item.get("videoUrl") or ""
    if not url:
        return item
    try:
        html, final = http_get(url, ua=UA_MOBILE, timeout=14)
        bag = {"items": [], "_seen": set()}
        for pat in [
            r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
            r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        ]:
            m = re.search(pat, html, re.S)
            if not m:
                continue
            try:
                data = json.loads(m.group(1))
                dig_items(data, bag)
            except Exception:
                pass
        if bag["items"]:
            best = bag["items"][0]
            # merge richer fields
            if best.get("description") and len(best["description"]) >= len(item.get("description") or ""):
                item["description"] = best["description"]
            if best.get("author"):
                item["author"] = best["author"]
            if best.get("cover"):
                item["cover"] = best["cover"]
            if best.get("playUrl"):
                item["playUrl"] = best["playUrl"]
            st = best.get("stats") or {}
            cur = item.get("stats") or {}
            item["stats"] = {
                "views": st.get("views") if st.get("views") is not None else cur.get("views"),
                "likes": st.get("likes") if st.get("likes") is not None else cur.get("likes"),
                "comments": st.get("comments") if st.get("comments") is not None else cur.get("comments"),
                "shares": st.get("shares") if st.get("shares") is not None else cur.get("shares"),
            }
            links = extract_preset_links(item.get("description") or "")
            if links:
                item["presetLinks"] = links
            if best.get("videoUrl"):
                item["videoUrl"] = best["videoUrl"]
    except Exception:
        pass
    return item


def search_tiktok(query):
    q = query.strip()
    if not q:
        return []

    queries = []
    ql = q.lower()
    if "#preset" not in ql:
        queries.append(f"{q} #preset")
        queries.append(f"{q} preset")
    queries.append(q)
    if "alight" not in ql:
        queries.append(f"{q} alight motion")

    all_items = []
    seen = set()

    for kw in queries[:3]:
        pages = [
            f"https://www.tiktok.com/search?q={quote(kw)}",
            f"https://www.tiktok.com/search/video?q={quote(kw)}",
        ]
        for page in pages:
            for ua in (UA_MOBILE, UA_WEB):
                try:
                    html, _ = http_get(page, ua=ua, timeout=18)
                    for it in parse_json_blobs(html):
                        key = it.get("id") or (it.get("description", "")[:50] + it.get("author", ""))
                        if key in seen:
                            continue
                        seen.add(key)
                        all_items.append(it)
                    if len(all_items) >= 10:
                        break
                except Exception:
                    continue
            if len(all_items) >= 10:
                break
        if len(all_items) >= 10:
            break

    # Score by relevance to caption + preset signals
    tokens = [t for t in re.split(r"\s+", ql) if t]

    def score(it):
        d = (it.get("description") or "").lower()
        s = 0
        if it.get("presetLinks"):
            s += 80
        if "#preset" in d:
            s += 30
        if "preset" in d:
            s += 15
        if "alight" in d:
            s += 10
        for t in tokens:
            if t in d:
                s += 12
        # prefer items with stats
        st = it.get("stats") or {}
        if st.get("views"):
            s += 5
        return s

    all_items.sort(key=score, reverse=True)
    top = all_items[:12]

    # Enrich top results by opening video pages (caption accuracy + stats)
    enriched = []
    for it in top[:8]:
        enriched.append(enrich_from_video_page(it))
    # keep rest without enrich to save time
    enriched.extend(top[8:])

    # Re-sort after enrich
    enriched.sort(key=score, reverse=True)

    # Prefer those with preset links or strong caption match
    return enriched[:12]


def fmt_num(n):
    if n is None:
        return None
    try:
        n = int(n)
    except Exception:
        return None
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


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
            out_results = []
            for r in results:
                st = r.get("stats") or {}
                pu = r.get("playUrl") or ""
                if pu.startswith("http"):
                    pu = "/api/video?url=" + quote(pu, safe="")
                out_results.append({
                    "id": r.get("id") or "",
                    "description": r.get("description") or "",
                    "author": r.get("author") or "",
                    "cover": r.get("cover") or "",
                    "playUrl": pu,
                    "videoUrl": r.get("videoUrl") or "",
                    "presetLinks": r.get("presetLinks") or [],
                    "stats": {
                        "views": st.get("views"),
                        "likes": st.get("likes"),
                        "comments": st.get("comments"),
                        "shares": st.get("shares"),
                        "viewsText": fmt_num(st.get("views")),
                        "likesText": fmt_num(st.get("likes")),
                        "commentsText": fmt_num(st.get("comments")),
                    },
                })
            body = json.dumps({
                "ok": True,
                "query": q,
                "count": len(out_results),
                "results": out_results,
                "message": None if out_results else "yah gada preset nya",
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            body = json.dumps({"ok": False, "error": "search failed", "message": "yah gada preset nya"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
