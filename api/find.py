"""
AM Preset Finder API — by Yanz
Vercel Python: GET /api/find?url=<tiktok_url>
SSE event "result" with JSON { ok, presetLinks, video, author, ... }
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote
import json
import re
import traceback

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError, URLError
except ImportError:
    pass

UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

AM_PATTERNS = [
    re.compile(r"https?://(?:www\.)?alight\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?am\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://link\.alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://[^\s\"'<>]*alight[^\s\"'<>]*preset[^\s\"'<>]*", re.I),
    re.compile(r"https?://[^\s\"'<>]*(?:am-preset|ampreset|alight-preset)[^\s\"'<>]*", re.I),
    re.compile(r"https?://(?:bit\.ly|t\.co|tinyurl\.com|cutt\.ly|s\.id)/[^\s\"'<>]+", re.I),
]

PRESET_HINT = re.compile(
    r"(alight\s*motion|am\s*preset|preset\s*am|link\s*preset|preset\s*link|"
    r"xml\s*preset|5\s*mb|5mb\s*preset|download\s*preset)",
    re.I,
)


def http_get(url, timeout=18, method="GET"):
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        },
        method=method,
    )
    with urlopen(req, timeout=timeout) as resp:
        final = resp.geturl()
        body = resp.read()
        try:
            text = body.decode("utf-8", errors="replace")
        except Exception:
            text = body.decode("latin-1", errors="replace")
        return final, text, resp.status


def extract_urls(text):
    if not text:
        return []
    found, seen = [], set()
    for pat in AM_PATTERNS:
        for m in pat.finditer(text):
            u = m.group(0).rstrip(".,);]'\"")
            if u not in seen:
                seen.add(u)
                found.append(u)
    return found


def classify(url, context=""):
    low = (url + " " + context).lower()
    ptype = "5mb" if ("5mb" in low or "5 mb" in low) else "xml"
    detail = None
    c = context.lower()
    if "caption" in c or "description" in c:
        detail = "Found in video caption"
    elif "comment" in c:
        detail = "Found in comments"
    elif "bio" in c:
        detail = "Found in account bio"
    elif "reply" in c:
        detail = "Found in comment replies"
    return {
        "url": url,
        "type": ptype,
        "title": "5MB preset" if ptype == "5mb" else "XML preset",
        "detail": detail,
        "byAuthor": "caption" in c or "bio" in c or "description" in c,
    }


def resolve_url(url):
    try:
        final, _, _ = http_get(url, timeout=12, method="GET")
        return final
    except Exception:
        return url


def parse_video_id(url):
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


def parse_username(url):
    m = re.search(r"tiktok\.com/@([^/?\s]+)", url)
    return m.group(1) if m else None


def fetch_oembed(url):
    try:
        api = "https://www.tiktok.com/oembed?url=" + quote(url, safe="")
        _, text, status = http_get(api, timeout=12)
        if status == 200:
            return json.loads(text)
    except Exception:
        pass
    return None


def extract_hydration(html):
    patterns = [
        r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script\s+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r"window\['SIGI_STATE'\]\s*=\s*(\{.*?\});",
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL | re.I)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return None


def walk_strings(obj, bag, depth=0):
    if depth > 12 or len(bag) > 500:
        return
    if isinstance(obj, str):
        if len(obj) > 8 and ("http" in obj or re.search(r"alight|preset", obj, re.I)):
            bag.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            walk_strings(v, bag, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:80]:
            walk_strings(v, bag, depth + 1)


def dig_detail(data):
    out = {
        "description": "",
        "author": "",
        "author_avatar": "",
        "cover": "",
        "play_url": "",
        "stats": {},
        "comments_text": [],
        "bio": "",
    }
    descs = []

    def collect(obj):
        if obj is None:
            return
        if isinstance(obj, list):
            for v in obj[:100]:
                collect(v)
            return
        if not isinstance(obj, dict):
            return
        if isinstance(obj.get("desc"), str):
            descs.append(obj["desc"])
        if isinstance(obj.get("description"), str):
            descs.append(obj["description"])
        uid = obj.get("uniqueId") or obj.get("unique_id")
        if uid:
            out["author"] = out["author"] or str(uid)
        if obj.get("nickname") and not out["author"]:
            out["author"] = str(obj["nickname"])
        av = obj.get("avatarLarger") or obj.get("avatarMedium") or obj.get("avatarThumb")
        if av:
            out["author_avatar"] = av
        if isinstance(obj.get("signature"), str):
            out["bio"] = out["bio"] or obj["signature"]
        st = obj.get("stats")
        if isinstance(st, dict):
            out["stats"] = {
                "views": st.get("playCount") or st.get("play_count") or st.get("views"),
                "likes": st.get("diggCount") or st.get("digg_count") or st.get("likes"),
                "comments": st.get("commentCount") or st.get("comment_count") or st.get("comments"),
            }
        vid = obj.get("video")
        if isinstance(vid, dict):
            out["cover"] = vid.get("cover") or vid.get("originCover") or out["cover"]
            out["play_url"] = vid.get("playAddr") or vid.get("downloadAddr") or out["play_url"]
        if isinstance(obj.get("text"), str) and len(obj["text"]) > 2:
            if obj.get("cid") or obj.get("aweme_id") or obj.get("comment_id"):
                out["comments_text"].append(obj["text"])
        for v in obj.values():
            collect(v)

    collect(data)
    if descs:
        descs.sort(key=len, reverse=True)
        out["description"] = descs[0]
    return out


def scrape_tiktok(url):
    preset_links = []
    seen = set()

    def add(urls, context):
        for u in urls:
            try:
                host = urlparse(u).netloc.lower()
            except Exception:
                host = ""
            if "tiktok.com" in host and "alight" not in u.lower():
                continue
            if u in seen:
                continue
            if any(x in host for x in ("bit.ly", "t.co", "tinyurl", "cutt.ly", "s.id")):
                if not PRESET_HINT.search(context or ""):
                    continue
            seen.add(u)
            preset_links.append(classify(u, context))

    final_url = resolve_url(url)
    video_id = parse_video_id(final_url)
    author = parse_username(final_url) or ""
    description = ""
    author_avatar = ""
    cover = ""
    play_url = ""
    stats = {}
    bio = ""
    comments_blob = ""

    # 1) oEmbed
    oembed = fetch_oembed(final_url)
    if oembed:
        description = oembed.get("title") or description
        author = oembed.get("author_name") or author
        author_avatar = oembed.get("thumbnail_url") or author_avatar
        cover = oembed.get("thumbnail_url") or cover

    # 2) Page HTML
    html = ""
    try:
        _, html, _ = http_get(final_url, timeout=18)
    except Exception as e:
        if not oembed:
            raise RuntimeError("Could not open TikTok link: " + str(e)[:120]) from e

    if html:
        add(extract_urls(html), "page html")
        data = extract_hydration(html)
        if data:
            d = dig_detail(data)
            description = d["description"] or description
            author = d["author"] or author
            author_avatar = d["author_avatar"] or author_avatar
            cover = d["cover"] or cover
            play_url = d["play_url"] or play_url
            stats = d["stats"] or stats
            bio = d["bio"] or bio
            if d["comments_text"]:
                comments_blob = "\n".join(d["comments_text"])
            bag = []
            walk_strings(data, bag)
            add(extract_urls("\n".join(bag)), "embedded data")

    add(extract_urls(description), "video caption / description")
    add(extract_urls(bio), "account bio")
    add(extract_urls(comments_blob), "comments")

    return {
        "ok": True,
        "presetLinks": preset_links,
        "author": author or "unknown",
        "authorDetail": {"avatar": author_avatar} if author_avatar else {},
        "video": {
            "description": description or "",
            "cover": cover or "",
            "playUrl": play_url or "",
            "playUrlNoWm": play_url or "",
            "width": 576,
            "height": 1024,
            "stats": {
                "views": stats.get("views"),
                "likes": stats.get("likes"),
                "comments": stats.get("comments"),
            },
        },
        "sourceUrl": final_url,
        "videoId": video_id,
    }


def sse(event, data):
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        url = unquote((qs.get("url") or [""])[0]).strip()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()

        try:
            if not url or len(url) < 8:
                self.wfile.write(sse("result", {"ok": False, "error": "Missing url parameter"}).encode())
                return
            if not re.search(r"tiktok\.com|vt\.tiktok|vm\.tiktok", url, re.I):
                self.wfile.write(
                    sse("result", {"ok": False, "error": "Please paste a valid TikTok video link"}).encode()
                )
                return

            result = scrape_tiktok(url)
            self.wfile.write(sse("result", result).encode())
        except Exception as e:
            traceback.print_exc()
            msg = str(e)[:180] or "scraper failed"
            self.wfile.write(sse("result", {"ok": False, "error": msg}).encode())
