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
    # Official Alight Motion share (5MB / cloud)
    re.compile(r"https?://(?:www\.)?alightcreative\.com/am/share/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?alight\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?am\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://link\.alightmotion\.com/[^\s\"'<>]+", re.I),
    # Google Drive (very common for XML presets)
    re.compile(r"https?://(?:drive|docs)\.google\.com/(?:file/d/|open\?id=|uc\?[^\s\"']*id=)[^\s\"'<>]+", re.I),
    re.compile(r"https?://drive\.google\.com/[^\s\"'<>]+", re.I),
    # Other common hosts used for AM presets
    re.compile(r"https?://(?:www\.)?mediafire\.com/file/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?mega\.(?:nz|co\.nz)/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?dropbox\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?pixeldrain\.com/u/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?gofile\.io/d/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?anonfiles\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?workupload\.com/file/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?terabox\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?telegra\.ph/[^\s\"'<>]+", re.I),
    # Shorteners (kept if context mentions preset)
    re.compile(r"https?://(?:bit\.ly|t\.co|tinyurl\.com|cutt\.ly|s\.id|linktr\.ee|bio\.link)/[^\s\"'<>]+", re.I),
    # Generic alight / preset path
    re.compile(r"https?://[^\s\"'<>]*alight[^\s\"'<>]*preset[^\s\"'<>]*", re.I),
    re.compile(r"https?://[^\s\"'<>]*(?:am-preset|ampreset|alight-preset)[^\s\"'<>]*", re.I),
]

PRESET_HINT = re.compile(
    r"(alight\s*motion|am\s*preset|preset\s*am|link\s*preset|preset\s*link|"
    r"xml\s*preset|5\s*mb|5mb\s*preset|download\s*preset|preset\s*xml|"
    r"file\s*preset|preset\s*file|link\s*xml|drive\.google|gdrive|"
    r"#preset|#alight|#ampreset|presetalight)",
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



def extract_all_http(text):
    """Grab any http(s) URL from text."""
    if not text:
        return []
    return re.findall(r"https?://[^\s\"'<>\]\)\}]+", text)

def is_preset_host(url):
    host = ""
    try:
        from urllib.parse import urlparse as _up
        host = _up(url).netloc.lower()
    except Exception:
        host = url.lower()
    keys = (
        "alightcreative.com", "alight.link", "alightmotion.com", "am.link",
        "drive.google.com", "docs.google.com", "mediafire.com", "mega.nz",
        "mega.co.nz", "dropbox.com", "pixeldrain.com", "gofile.io",
        "anonfiles.com", "workupload.com", "terabox.com", "telegra.ph",
        "bit.ly", "tinyurl.com", "cutt.ly", "s.id", "linktr.ee", "bio.link",
    )
    return any(k in host for k in keys)

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
    if "alightcreative.com/am/share" in low or "/am/share/" in low:
        ptype = "5mb"
    elif "5mb" in low or "5 mb" in low:
        ptype = "5mb"
    else:
        ptype = "xml"  # drive, mediafire, etc. usually XML
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
    """Resolve vt.tiktok.com / vm.tiktok.com short links to full @user/video/id URL."""
    url = (url or "").strip()
    if not url:
        return url
    # already full?
    if re.search(r"tiktok\.com/@[^/]+/video/\d+", url, re.I):
        return url.split("?")[0].split("#")[0]

    uas = [
        UA,
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    ]
    last = url
    for ua in uas:
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Accept-Language": "id-ID,id;q=0.9,en;q=0.8",
                },
                method="GET",
            )
            with urlopen(req, timeout=14) as resp:
                final = resp.geturl()
                body = resp.read().decode("utf-8", "ignore")
            last = final or last
            if re.search(r"tiktok\.com/@[^/]+/video/\d+", last, re.I):
                return last.split("?")[0].split("#")[0]
            # sometimes canonical in HTML
            m = re.search(
                r'https?://(?:www\.)?tiktok\.com/@[^/\s"\']+/video/\d+',
                body,
                re.I,
            )
            if m:
                return m.group(0).split("?")[0]
            m = re.search(
                r'property="og:url"\s+content="(https?://[^"]+)"',
                body,
                re.I,
            )
            if m and "tiktok.com" in m.group(1):
                return m.group(1).split("?")[0]
        except Exception:
            continue
    return last.split("?")[0] if last else url


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



def fetch_comments_api(video_id, count=50):
    """Best-effort public comment list (may fail without cookies)."""
    if not video_id:
        return []
    texts = []
    try:
        api = (
            "https://www.tiktok.com/api/comment/list/"
            f"?aid=1988&aweme_id={video_id}&count={count}&cursor=0"
        )
        _, body, status = http_get(api, timeout=12)
        if status != 200:
            return texts
        data = json.loads(body)
        comments = data.get("comments") or data.get("comment_list") or []
        for c in comments:
            t = c.get("text") or c.get("share_info", {}).get("desc") or ""
            if t:
                texts.append(t)
            # replies if present
            for r in (c.get("reply_comment") or c.get("reply_list") or [])[:10]:
                rt = r.get("text") or ""
                if rt:
                    texts.append(rt)
    except Exception:
        pass
    return texts

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
    author_candidates = []  # (priority, uniqueId, nickname, avatar)

    def collect(obj, path=""):
        if obj is None:
            return
        if isinstance(obj, list):
            for v in obj[:100]:
                collect(v, path)
            return
        if not isinstance(obj, dict):
            return

        if isinstance(obj.get("desc"), str) and len(obj["desc"]) > 0:
            descs.append(obj["desc"])
        if isinstance(obj.get("description"), str) and len(obj["description"]) > 0:
            descs.append(obj["description"])

        uid = obj.get("uniqueId") or obj.get("unique_id")
        nick = obj.get("nickname")
        av = obj.get("avatarLarger") or obj.get("avatarMedium") or obj.get("avatarThumb")
        # Prefer author objects that look like the video owner (has both uniqueId + stats / verified)
        if uid and isinstance(uid, str) and len(uid) > 1:
            priority = 0
            pl = path.lower()
            if "author" in pl or "authorinfo" in pl or "userinfo" in pl:
                priority += 10
            if "comment" in pl or "reply" in pl:
                priority -= 20  # avoid commenter usernames
            if obj.get("verified") is not None or obj.get("secUid") or obj.get("sec_uid"):
                priority += 3
            if obj.get("signature") is not None:
                priority += 2
            author_candidates.append((priority, str(uid), str(nick) if nick else "", av or ""))

        if isinstance(obj.get("signature"), str) and obj["signature"] and not out["bio"]:
            # only take bio from high-priority later
            pass
        st = obj.get("stats")
        if isinstance(st, dict) and not out["stats"].get("views"):
            out["stats"] = {
                "views": st.get("playCount") or st.get("play_count") or st.get("views"),
                "likes": st.get("diggCount") or st.get("digg_count") or st.get("likes"),
                "comments": st.get("commentCount") or st.get("comment_count") or st.get("comments"),
            }
        vid = obj.get("video")
        if isinstance(vid, dict):
            out["cover"] = (
                vid.get("cover") or vid.get("originCover") or vid.get("dynamicCover")
                or out["cover"]
            )
            # play addresses — TikTok often nests UrlList
            for key in ("playAddr", "downloadAddr", "play_addr", "download_addr", "playApi", "downloadApi"):
                val = vid.get(key)
                if isinstance(val, str) and val.startswith("http") and not out["play_url"]:
                    out["play_url"] = val
                elif isinstance(val, dict):
                    url_list = val.get("UrlList") or val.get("url_list") or val.get("urlList") or []
                    if url_list and isinstance(url_list, list) and not out["play_url"]:
                        out["play_url"] = url_list[0]
                    uri = val.get("Uri") or val.get("uri") or val.get("url")
                    if isinstance(uri, str) and uri.startswith("http") and not out["play_url"]:
                        out["play_url"] = uri
            # bitrateInfo / playAddrArray
            for bi in (vid.get("bitrateInfo") or vid.get("bit_rate") or [])[:5]:
                if not isinstance(bi, dict):
                    continue
                pa = bi.get("PlayAddr") or bi.get("play_addr") or bi.get("PlayAddrStruct") or {}
                if isinstance(pa, dict):
                    ul = pa.get("UrlList") or pa.get("url_list") or []
                    if ul and not out["play_url"]:
                        out["play_url"] = ul[0]

        if isinstance(obj.get("text"), str) and len(obj["text"]) > 2:
            if obj.get("cid") or obj.get("aweme_id") or obj.get("comment_id"):
                out["comments_text"].append(obj["text"])
        for k, v in obj.items():
            collect(v, path + "." + str(k))

    collect(data)

    if author_candidates:
        author_candidates.sort(key=lambda x: -x[0])
        best = author_candidates[0]
        out["author"] = best[1]  # uniqueId = real @username
        if best[3]:
            out["author_avatar"] = best[3]
        # bio from same-ish objects
        for pr, uid, nick, av in author_candidates[:5]:
            if pr >= 10:
                break

    # bio: search again for signature under author path
    def find_bio(obj, path=""):
        if out["bio"]:
            return
        if isinstance(obj, dict):
            if ("author" in path.lower() or "userinfo" in path.lower()) and isinstance(obj.get("signature"), str):
                if obj["signature"]:
                    out["bio"] = obj["signature"]
                    return
            for k, v in obj.items():
                find_bio(v, path + "." + str(k))
        elif isinstance(obj, list):
            for v in obj[:50]:
                find_bio(v, path)
    find_bio(data)

    if descs:
        descs.sort(key=len, reverse=True)
        out["description"] = descs[0]
    return out



def _proxy_url(media_url):
    """Route TikTok CDN media through our proxy for playback."""
    if not media_url or not media_url.startswith("http"):
        return media_url or ""
    # relative proxy path works on same origin (Vercel)
    from urllib.parse import quote
    return "/api/video?url=" + quote(media_url, safe="")

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
        author = author or oembed.get("author_name") or author  # uniqueId from page wins
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
            author = d["author"] or author  # prefer uniqueId
            author_avatar = d["author_avatar"] or author_avatar
            cover = d["cover"] or cover
            play_url = d["play_url"] or play_url
            if not play_url:
                _bag2 = []
                walk_strings(data, _bag2)
                mp4s = re.findall(r"https?://[^\s\"\'<>]+(?:\.mp4|media[^\s\"\'<>]*)", "\n".join(_bag2))
                mp4s = [u for u in mp4s if "tiktok" in u or "musical" in u or "byte" in u]
                if mp4s:
                    play_url = mp4s[0]

            stats = d["stats"] or stats
            bio = d["bio"] or bio
            if d["comments_text"]:
                comments_blob = "\n".join(d["comments_text"])
            bag = []
            walk_strings(data, bag)
            add(extract_urls("\n".join(bag)), "embedded data")

    add(extract_urls(description), "video caption / description")
    add([u for u in extract_all_http(description) if is_preset_host(u)], "video caption / description")
    add(extract_urls(bio), "account bio")
    add([u for u in extract_all_http(bio) if is_preset_host(u)], "account bio")
    
    # 6) Try TikTok comment API
    try:
        api_comments = fetch_comments_api(video_id, count=80)
        if api_comments:
            comments_blob = (comments_blob + "\n" + "\n".join(api_comments)).strip()
    except Exception:
        pass

    add(extract_urls(comments_blob), "comments")
    add([u for u in extract_all_http(comments_blob) if is_preset_host(u)], "comments")

    # Final author: prefer clean uniqueId (no spaces); else URL @handle; else oembed
    url_user = parse_username(final_url)
    if author and " " in str(author):
        author = url_user or author
    if not author:
        author = url_user or author
    if url_user and author and author.lstrip("@").lower() != url_user.lower():
        # dig uniqueId wins if it looks like a handle (no spaces)
        if " " in str(author):
            author = url_user

    return {
        "ok": True,
        "presetLinks": preset_links,
        "author": (("@" + author.lstrip("@")) if author else "unknown"),
        "authorDetail": {"avatar": author_avatar} if author_avatar else {},
        "video": {
            "description": description or "",
            "cover": cover or "",
            "playUrl": (_proxy_url(play_url) if play_url else ""),
            "playUrlNoWm": (_proxy_url(play_url) if play_url else ""),
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
