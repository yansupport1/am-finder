"""
GET /api/search?q=<keyword>
Own search pipeline: discover TikTok videos via public web indexes + read each video page caption/stats/presets.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote, urljoin
from urllib.request import Request, urlopen
import json
import re
from html import unescape

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
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
]

TT_VIDEO_RE = re.compile(
    r"https?://(?:www\.)?tiktok\.com/@([\w.\-]+)/video/(\d+)",
    re.I,
)
TT_ANY_RE = re.compile(
    r"https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[^\s\"'<>]+",
    re.I,
)


def http_get(url, ua=UA, timeout=16, headers=None):
    h = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://www.google.com/",
    }
    if headers:
        h.update(headers)
    req = Request(url, headers=h)
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


def normalize_tt_url(url):
    url = unescape(url).split("?")[0].split("#")[0]
    m = TT_VIDEO_RE.search(url)
    if m:
        return f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}", m.group(1), m.group(2)
    return url, "", ""


def dig_detail(obj, out, depth=0):
    if depth > 14 or obj is None:
        return
    if isinstance(obj, list):
        for v in obj[:80]:
            dig_detail(v, out, depth + 1)
        return
    if not isinstance(obj, dict):
        return

    desc = obj.get("desc") or obj.get("description") or ""
    if isinstance(desc, str) and desc and (obj.get("id") or obj.get("aweme_id") or obj.get("video")):
        stats = obj.get("stats") or obj.get("statistics") or {}
        author = obj.get("author") or obj.get("authorInfo") or {}
        video = obj.get("video") or {}
        if not isinstance(stats, dict):
            stats = {}
        if not isinstance(author, dict):
            author = {}
        if not isinstance(video, dict):
            video = {}

        uid = author.get("uniqueId") or author.get("unique_id") or author.get("nickname") or out.get("author") or ""
        cover = video.get("cover") or video.get("originCover") or out.get("cover") or ""
        if isinstance(cover, dict):
            ul = cover.get("url_list") or cover.get("UrlList") or []
            cover = ul[0] if ul else ""

        play = out.get("playUrl") or ""
        for key in ("playAddr", "downloadAddr", "play_addr"):
            val = video.get(key)
            if isinstance(val, str) and val.startswith("http") and not play:
                play = val
            elif isinstance(val, dict) and not play:
                ul = val.get("UrlList") or val.get("url_list") or []
                if ul:
                    play = ul[0]

        views = as_int(stats.get("playCount") or stats.get("play_count") or stats.get("views"))
        likes = as_int(stats.get("diggCount") or stats.get("digg_count") or stats.get("likes"))
        comments = as_int(stats.get("commentCount") or stats.get("comment_count") or stats.get("comments"))
        shares = as_int(stats.get("shareCount") or stats.get("share_count"))

        if len(desc) > len(out.get("description") or ""):
            out["description"] = desc
        if uid:
            out["author"] = str(uid).lstrip("@")
        if cover:
            out["cover"] = cover
        if play:
            out["playUrl"] = play
        if views is not None:
            out.setdefault("stats", {})["views"] = views
        if likes is not None:
            out.setdefault("stats", {})["likes"] = likes
        if comments is not None:
            out.setdefault("stats", {})["comments"] = comments
        if shares is not None:
            out.setdefault("stats", {})["shares"] = shares
        vid = str(obj.get("id") or obj.get("aweme_id") or out.get("id") or "")
        if vid:
            out["id"] = vid

    for v in obj.values():
        dig_detail(v, out, depth + 1)


def read_video_page(url):
    info = {
        "id": "",
        "description": "",
        "author": "",
        "cover": "",
        "playUrl": "",
        "videoUrl": url,
        "presetLinks": [],
        "stats": {"views": None, "likes": None, "comments": None, "shares": None},
    }
    nu, author, vid = normalize_tt_url(url)
    info["videoUrl"] = nu
    info["author"] = author
    info["id"] = vid

    for ua in (UA_MOBILE, UA):
        try:
            html, final = http_get(nu, ua=ua, timeout=15, headers={"Referer": "https://www.tiktok.com/"})
            info["videoUrl"] = final.split("?")[0] if "tiktok.com" in final else nu
            for pat in (
                r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
                r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            ):
                m = re.search(pat, html, re.S)
                if not m:
                    continue
                try:
                    data = json.loads(m.group(1))
                    dig_detail(data, info)
                except Exception:
                    pass
            # meta fallbacks
            if not info["description"]:
                om = re.search(r'<meta[^>]+property="og:description"[^>]+content="([^"]*)"', html, re.I)
                if om:
                    info["description"] = unescape(om.group(1))
            if not info["cover"]:
                om = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]*)"', html, re.I)
                if om:
                    info["cover"] = unescape(om.group(1))
            if not info["author"]:
                um = re.search(r"tiktok\.com/@([\w.\-]+)", info["videoUrl"])
                if um:
                    info["author"] = um.group(1)
            info["presetLinks"] = extract_preset_links(info.get("description") or "")
            # also scan whole html for preset links near caption
            extra = extract_preset_links(html)
            for u in extra:
                if u not in info["presetLinks"]:
                    info["presetLinks"].append(u)
            if info["description"] or info["presetLinks"] or info["stats"].get("views") is not None:
                break
        except Exception:
            continue

    return info


def discover_via_duckduckgo(query):
    """Discover TikTok video URLs using DuckDuckGo HTML results."""
    urls = []
    seen = set()
    variants = [
        f'site:tiktok.com/video {query} preset',
        f'site:tiktok.com {query} #preset',
        f'site:tiktok.com {query} alight preset',
        f'{query} #preset site:tiktok.com',
    ]
    for q in variants[:3]:
        try:
            # DuckDuckGo html
            ddg = f"https://html.duckduckgo.com/html/?q={quote(q)}"
            html, _ = http_get(ddg, timeout=14, headers={"Referer": "https://duckduckgo.com/"})
            # results often as uddg= encoded
            for m in re.finditer(r"uddg=([^&\"']+)", html):
                try:
                    from urllib.parse import unquote as uq
                    link = uq(m.group(1))
                except Exception:
                    continue
                if "tiktok.com" not in link:
                    continue
                nu, a, v = normalize_tt_url(link)
                if v and v not in seen:
                    seen.add(v)
                    urls.append(nu)
                elif "tiktok.com" in link and link not in seen:
                    seen.add(link)
                    urls.append(link.split("?")[0])
            for m in TT_VIDEO_RE.finditer(html):
                nu = f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
                if m.group(2) not in seen:
                    seen.add(m.group(2))
                    urls.append(nu)
        except Exception:
            continue
        if len(urls) >= 10:
            break
    return urls[:12]


def discover_via_bing(query):
    urls = []
    seen = set()
    variants = [
        f'site:tiktok.com {query} preset',
        f'site:tiktok.com/video {query} #preset',
    ]
    for q in variants[:2]:
        try:
            page = f"https://www.bing.com/search?q={quote(q)}&count=20"
            html, _ = http_get(page, timeout=14, headers={"Referer": "https://www.bing.com/"})
            for m in TT_VIDEO_RE.finditer(html):
                nu = f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
                if m.group(2) not in seen:
                    seen.add(m.group(2))
                    urls.append(nu)
            for m in re.finditer(r'href="(https?://(?:www\.)?tiktok\.com/@[^"]+/video/\d+)', html):
                nu, a, v = normalize_tt_url(m.group(1))
                if v and v not in seen:
                    seen.add(v)
                    urls.append(nu)
        except Exception:
            continue
        if len(urls) >= 10:
            break
    return urls[:12]


def discover_via_tiktok_pages(query):
    """Best-effort TikTok search pages as extra source."""
    urls = []
    seen = set()
    for kw in (f"{query} #preset", f"{query} preset", query):
        for path in (
            f"https://www.tiktok.com/search/video?q={quote(kw)}",
            f"https://www.tiktok.com/search?q={quote(kw)}",
        ):
            try:
                html, _ = http_get(path, ua=UA_MOBILE, timeout=14, headers={"Referer": "https://www.tiktok.com/"})
                for m in TT_VIDEO_RE.finditer(html):
                    nu = f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
                    if m.group(2) not in seen:
                        seen.add(m.group(2))
                        urls.append(nu)
                # json blobs may contain ids
                for m in re.finditer(r'"video"\s*:\s*\{[^}]*?"id"\s*:\s*"(\d+)"', html):
                    pass
                for m in re.finditer(r'"id"\s*:\s*"(\d{15,})".{0,200}?"uniqueId"\s*:\s*"([^"]+)"', html, re.S):
                    vid, uid = m.group(1), m.group(2)
                    if vid not in seen:
                        seen.add(vid)
                        urls.append(f"https://www.tiktok.com/@{uid}/video/{vid}")
                for m in re.finditer(r'"uniqueId"\s*:\s*"([^"]+)".{0,200}?"id"\s*:\s*"(\d{15,})"', html, re.S):
                    uid, vid = m.group(1), m.group(2)
                    if vid not in seen:
                        seen.add(vid)
                        urls.append(f"https://www.tiktok.com/@{uid}/video/{vid}")
            except Exception:
                continue
        if len(urls) >= 8:
            break
    return urls[:12]


def search_own(query):
    q = query.strip()
    if not q:
        return []

    # 1) Discover candidate TikTok video URLs from multiple sources
    candidates = []
    seen = set()
    for finder in (discover_via_duckduckgo, discover_via_bing, discover_via_tiktok_pages):
        try:
            for u in finder(q):
                nu, a, v = normalize_tt_url(u)
                key = v or nu
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(nu)
        except Exception:
            continue
        if len(candidates) >= 12:
            break

    candidates = candidates[:10]
    if not candidates:
        return []

    # 2) Read each video page for real caption + stats + preset links
    results = []
    ql = q.lower()
    tokens = [t for t in re.split(r"\s+", ql) if t]

    for url in candidates:
        try:
            info = read_video_page(url)
        except Exception:
            continue
        desc = (info.get("description") or "").lower()
        # relevance filter soft: keep if keyword matches OR has preset OR #preset
        rel = 0
        if info.get("presetLinks"):
            rel += 50
        if "#preset" in desc:
            rel += 25
        if "preset" in desc:
            rel += 10
        for t in tokens:
            if t and t in desc:
                rel += 15
        if rel < 10 and not info.get("presetLinks"):
            # still keep some results if discovery was strong but caption thin
            rel = 5
        info["_score"] = rel
        results.append(info)

    results.sort(key=lambda x: x.get("_score", 0), reverse=True)

    # Prefer items with actual preset links first, then high relevance
    final = []
    for r in results:
        r.pop("_score", None)
        st = r.get("stats") or {}
        pu = r.get("playUrl") or ""
        if pu.startswith("http"):
            pu = "/api/video?url=" + quote(pu, safe="")
        final.append({
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
    return final[:10]


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
            body = json.dumps({"ok": False, "error": "missing q", "results": []}).encode()
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            results = search_own(q)
            body = json.dumps({
                "ok": True,
                "query": q,
                "count": len(results),
                "results": results,
                "message": None if results else "yah gada preset nya",
            }, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            body = json.dumps({
                "ok": True,
                "query": q,
                "count": 0,
                "results": [],
                "message": "yah gada preset nya",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
