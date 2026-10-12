"""
AM Preset Finder API — by Yanz
Vercel Python: GET /api/find?url=<tiktok_url>
SSE event "result" with JSON { ok, presetLinks, video, author, ... }
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote, quote
import ipaddress
import json
import os
import re
import socket
import threading
import time
import traceback

from html import unescape as _html_unescape
try:
    from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler
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


# Batas waktu TOTAL per request /api/find (detik). Log produksi menunjukkan satu scan pernah
# menggantung 300 dtk ("Task timed out after 300 seconds"): urlopen(timeout=...) hanya membatasi
# tiap operasi socket, bukan total, jadi respons yang menetes pelan bisa menahan fungsi sangat lama.
FIND_BUDGET = float(os.environ.get("FIND_BUDGET", "60"))
_tl = threading.local()   # per-thread: aman bila beberapa request jalan bersamaan di satu instance


# ---------------------------------------------------------------------------
# Keamanan: server hanya boleh mengambil alamat PUBLIK (bukan localhost / jaringan internal),
# termasuk setiap lompatan redirect. Validasi lama hanya mencari teks "tiktok.com" di mana saja
# di URL, sehingga alamat internal yang menyelipkan kata itu bisa lolos.
# ---------------------------------------------------------------------------
def is_public_host(host):
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            if not ipaddress.ip_address(info[4][0].split("%")[0]).is_global:
                return False
        except Exception:
            return False
    return True


def safe_public_url(url):
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https") or not p.hostname:
        return False
    if p.username or p.password:
        return False
    try:
        if p.port not in (None, 80, 443):
            return False
    except ValueError:
        return False
    h = p.hostname.lower()
    if h == "localhost" or h.endswith((".local", ".internal", ".localhost")):
        return False
    return is_public_host(h)


class _SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not safe_public_url(newurl):
            raise HTTPError(newurl, code, "redirect ke alamat non-publik diblokir", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


SAFE_OPENER = build_opener(_SafeRedirect)


def _remaining():
    d = getattr(_tl, "deadline", None)
    return None if d is None else d - time.time()


def http_get(url, timeout=18, method="GET"):
    rem = _remaining()
    if rem is not None:
        if rem <= 1.0:
            raise TimeoutError("scan melewati batas waktu")
        timeout = min(timeout, rem)
    started = time.time()
    hard = timeout * 1.5 + 2          # batas total termasuk membaca body
    req = Request(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
        },
        method=method,
    )
    with SAFE_OPENER.open(req, timeout=timeout) as resp:
        final = resp.geturl()
        chunks, size = [], 0
        while True:
            chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 4000000:
                break
            if time.time() - started > hard:
                raise TimeoutError("respons terlalu lambat")
        body = b"".join(chunks)
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
    try:
        final, _, _ = http_get(url, timeout=12, method="GET")
        return final
    except Exception:
        return url


def parse_video_id(url):
    for pat in (r"/(?:video|photo)/(\d+)", r"/v/(\d+)", r"/embed/v2/(\d+)", r"/embed/(\d+)",
                r"[?&](?:id|item_id|share_item_id)=(\d+)"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return None


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
            "id": video_id or "",
            "embedUrl": (("https://www.tiktok.com/embed/v2/" + str(video_id)) if video_id else ""),
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


# ---------------------------------------------------------------------------
# Normalisasi input: terima link TikTok bentuk apa pun + situs lain
# ---------------------------------------------------------------------------
_TT_HOST = re.compile(r"(^|\.)tiktok\.com$", re.I)
_TT_ANY = re.compile(r"(?:https?://)?(?:[\w-]+\.)*tiktok\.com/[^\s\"'<>]*", re.I)


def normalize_input(raw):
    """Return (url, kind) dengan kind 'tiktok' | 'generic', atau (None, None) bila bukan link."""
    t = (raw or "").strip()
    if re.fullmatch(r"\d{15,25}", t):                       # hanya ID video
        return "https://www.tiktok.com/@_/video/" + t, "tiktok"

    def host_of(u):
        try:
            return (urlparse(u).hostname or "").lower()
        except Exception:
            return ""

    cand = None
    full = [u.rstrip(".,);]'\"") for u in re.findall(r"https?://[^\s\"'<>]+", t, re.I)]
    for u in full:                                          # teks hasil Bagikan: utamakan link TikTok
        if _TT_HOST.search(host_of(u)):
            cand = u
            break
    if not cand and full:
        cand = full[0]
    if not cand:                                            # link TikTok tanpa https://
        m = re.search(r"(?<![\w.-])(?:[\w-]+\.)*tiktok\.com/[^\s\"'<>]*", t, re.I)
        if m:
            cand = "https://" + m.group(0).rstrip(".,);]'\"")
    if not cand:                                            # domain lain tanpa https://
        m = re.search(r"(?<![\w.@-])(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*", t, re.I)
        if m:
            cand = "https://" + m.group(0).rstrip(".,);]'\"")
    if not cand:
        return None, None
    host = host_of(cand)
    if not host:
        return None, None
    return cand, ("tiktok" if _TT_HOST.search(host) else "generic")


PLATFORMS = (
    ("youtube.com", "YouTube"), ("youtu.be", "YouTube"), ("instagram.com", "Instagram"),
    ("facebook.com", "Facebook"), ("fb.watch", "Facebook"), ("twitter.com", "X"), ("x.com", "X"),
    ("threads.net", "Threads"), ("pinterest.", "Pinterest"), ("t.me", "Telegram"),
    ("likee.video", "Likee"), ("snapchat.com", "Snapchat"), ("kwai", "Kwai"), ("reddit.com", "Reddit"),
    ("linktr.ee", "Linktree"), ("lynk.id", "Lynk"), ("bio.link", "Bio.link"), ("beacons.ai", "Beacons"),
)


def platform_name(host):
    h = (host or "").lower()
    for key, name in PLATFORMS:
        if key in h:
            return name
    return h.replace("www.", "") or "Web"


def _meta(html, *names):
    for n in names:
        for pat in (r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)["\']' % re.escape(n),
                    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(n)):
            m = re.search(pat, html, re.I)
            if m and m.group(1).strip():
                return _html_unescape(m.group(1)).strip()
    return ""


def _youtube_id(url):
    for pat in (r"[?&]v=([\w-]{6,})", r"youtu\.be/([\w-]{6,})", r"/(?:shorts|embed|live)/([\w-]{6,})"):
        m = re.search(pat, url)
        if m:
            return m.group(1)
    return ""


def scrape_generic(url):
    """Pindai halaman publik mana pun (YouTube, Instagram, Facebook, Linktree, blog, ...) untuk link preset."""
    found, seen = [], set()

    def add(urls, context):
        for u in urls:
            try:
                host = urlparse(u).netloc.lower()
            except Exception:
                host = ""
            if u in seen or not host:
                continue
            if any(x in host for x in ("bit.ly", "t.co", "tinyurl", "cutt.ly", "s.id")) and not PRESET_HINT.search(context or ""):
                continue
            seen.add(u)
            found.append(classify(u, context))

    final_url, html, _ = http_get(url, timeout=18)
    host = (urlparse(final_url).hostname or "").lower()
    plat = platform_name(host)
    # buka escape JSON/HTML supaya link di dalam skrip data (mis. YouTube) ikut terbaca
    norm = _html_unescape(html).replace("\\u0026", "&").replace("\\u003d", "=").replace("\\u002F", "/").replace("\\/", "/")

    title = _meta(html, "og:title", "twitter:title")
    if not title:
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = _html_unescape(m.group(1)).strip() if m else ""
    desc = _meta(html, "og:description", "description", "twitter:description")
    cover = _meta(html, "og:image", "twitter:image")
    author = _meta(html, "author", "article:author", "twitter:creator") or ""
    yt_desc = ""
    m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', html)
    if m:
        try:
            yt_desc = json.loads('"' + m.group(1) + '"')
        except Exception:
            yt_desc = ""
    m = re.search(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"', html)
    if m:
        try:
            author = json.loads('"' + m.group(1) + '"') or author
        except Exception:
            pass
    description = (yt_desc or desc or title or "").strip()

    add(extract_urls(yt_desc), "video caption / description")
    add([u for u in extract_all_http(yt_desc) if is_preset_host(u)], "video caption / description")
    add(extract_urls(desc), "video caption / description")
    add([u for u in extract_all_http(desc) if is_preset_host(u)], "video caption / description")
    add(extract_urls(norm), "page html")
    add([u for u in extract_all_http(norm) if is_preset_host(u) and "tiktok.com" not in u], "page html")

    yt = _youtube_id(final_url) if plat == "YouTube" else ""
    return {
        "ok": True,
        "presetLinks": found,
        "author": (("@" + author.lstrip("@")) if author else plat),
        "authorDetail": {},
        "platform": plat,
        "video": {
            "description": description[:2000],
            "cover": cover or "",
            "playUrl": "", "playUrlNoWm": "",
            "id": yt, "embedUrl": ("https://www.youtube.com/embed/" + yt) if yt else "",
            "width": 576, "height": 1024,
            "stats": {"views": None, "likes": None, "comments": None},
        },
        "sourceUrl": final_url,
        "videoId": yt,
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
        self.send_header("Connection", "close")   # tanpa Content-Length: koneksi harus ditutup setelah stream selesai
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()

        try:
            if not url or len(url) < 8:
                self.wfile.write(sse("result", {"ok": False, "error": "Missing url parameter"}).encode())
                return
            norm_url, kind = normalize_input(url)
            if not norm_url:
                self.wfile.write(sse("result", {"ok": False, "error": "Please paste a valid video or page link (TikTok, YouTube, Instagram, ...)"}).encode())
                return
            if not safe_public_url(norm_url):
                self.wfile.write(sse("result", {"ok": False, "error": "That address cannot be opened (not a public website)"}).encode())
                return

            _tl.deadline = time.time() + FIND_BUDGET
            result = scrape_tiktok(norm_url) if kind == "tiktok" else scrape_generic(norm_url)
            if kind == "tiktok":
                result.setdefault("platform", "TikTok")
            self.wfile.write(sse("result", result).encode())
        except Exception as e:
            traceback.print_exc()
            msg = str(e)[:180] or "scraper failed"
            self.wfile.write(sse("result", {"ok": False, "error": msg}).encode())
        finally:
            self.close_connection = True
