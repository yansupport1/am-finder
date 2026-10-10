"""
GET /api/search?q=<keyword>
Reliable multi-source discovery + per-video caption read + playable URLs.
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote
from urllib.request import Request, urlopen
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import time
from html import unescape

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)

# simple process cache (helps 2nd same query on warm instance)
_CACHE = {}
_CACHE_TTL = 120  # seconds

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
    r"(?:https?://)?(?:www\.)?tiktok\.com/@([\w.\-]+)/video/(\d+)",
    re.I,
)


_UAS = [UA, UA_MOBILE,
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

def http_get(url, ua=None, timeout=14, referer="https://www.google.com/"):
    last_err = None
    uas = [ua] if ua else _UAS
    # try primary then one fallback
    tried = []
    for i, u in enumerate(uas[:2] if ua else _UAS[:3]):
        if not u or u in tried:
            continue
        tried.append(u)
        try:
            req = Request(url, headers={
                "User-Agent": u,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
                "Referer": referer,
                "Cache-Control": "no-cache",
            })
            with urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "ignore"), r.geturl()
        except Exception as e:
            last_err = e
            continue
    if last_err:
        raise last_err
    raise RuntimeError("http_get failed")


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


def normalize_tt(url):
    url = unescape(url or "").split("#")[0].strip()
    m = TT_VIDEO_RE.search(url)
    if m:
        return f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}", m.group(1), m.group(2)
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("tiktok.com"):
        url = "https://www." + url
    elif url.startswith("www.tiktok.com"):
        url = "https://" + url
    return url.split("?")[0], "", ""


def proxy_play(url):
    if not url or not str(url).startswith("http"):
        return url or ""
    return "/api/video?url=" + quote(url, safe="")


def dig_detail(obj, out, depth=0):
    if depth > 15 or obj is None:
        return
    if isinstance(obj, list):
        for v in obj[:100]:
            dig_detail(v, out, depth + 1)
        return
    if not isinstance(obj, dict):
        return

    desc = obj.get("desc") or obj.get("description") or ""
    video = obj.get("video")
    author = obj.get("author") or obj.get("authorInfo")
    stats = obj.get("stats") or obj.get("statistics")

    interesting = isinstance(desc, str) and desc and (
        obj.get("id") or obj.get("aweme_id") or isinstance(video, dict)
    )

    if interesting:
        if not isinstance(author, dict):
            author = {}
        if not isinstance(video, dict):
            video = {}
        if not isinstance(stats, dict):
            stats = {}

        uid = author.get("uniqueId") or author.get("unique_id") or author.get("nickname") or ""
        cover = video.get("cover") or video.get("originCover") or video.get("dynamicCover") or ""
        if isinstance(cover, dict):
            ul = cover.get("url_list") or cover.get("UrlList") or []
            cover = ul[0] if ul else ""

        play = ""
        # deep play addr
        for key in ("playAddr", "downloadAddr", "play_addr", "download_addr", "playApi"):
            val = video.get(key)
            if isinstance(val, str) and val.startswith("http"):
                play = val
                break
            if isinstance(val, dict):
                ul = val.get("UrlList") or val.get("url_list") or val.get("urlList") or []
                if ul:
                    play = ul[0]
                    break
        if not play:
            for bi in (video.get("bitrateInfo") or video.get("bit_rate") or [])[:6]:
                if not isinstance(bi, dict):
                    continue
                pa = bi.get("PlayAddr") or bi.get("play_addr") or {}
                if isinstance(pa, dict):
                    ul = pa.get("UrlList") or pa.get("url_list") or []
                    if ul:
                        play = ul[0]
                        break

        views = as_int(stats.get("playCount") or stats.get("play_count") or stats.get("views"))
        likes = as_int(stats.get("diggCount") or stats.get("digg_count") or stats.get("likes"))
        comments = as_int(stats.get("commentCount") or stats.get("comment_count") or stats.get("comments"))
        shares = as_int(stats.get("shareCount") or stats.get("share_count"))

        if len(desc) > len(out.get("description") or ""):
            out["description"] = desc
        if uid:
            out["author"] = str(uid).lstrip("@")
        if cover:
            out["cover"] = cover if isinstance(cover, str) else out.get("cover", "")
        if play:
            out["playUrl"] = play
        st = out.setdefault("stats", {})
        if views is not None:
            st["views"] = views
        if likes is not None:
            st["likes"] = likes
        if comments is not None:
            st["comments"] = comments
        if shares is not None:
            st["shares"] = shares
        vid = str(obj.get("id") or obj.get("aweme_id") or "")
        if vid.isdigit():
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
        "stats": {},
    }
    nu, author, vid = normalize_tt(url)
    info["videoUrl"] = nu
    info["author"] = author
    info["id"] = vid

    # oembed often works for author/title
    try:
        oembed_url = "https://www.tiktok.com/oembed?url=" + quote(nu, safe="")
        raw, _ = http_get(oembed_url, timeout=10, referer="https://www.tiktok.com/")
        oe = json.loads(raw)
        if oe.get("title") and not info["description"]:
            info["description"] = oe["title"]
        if oe.get("author_name") and not info["author"]:
            info["author"] = str(oe["author_name"]).lstrip("@")
        if oe.get("thumbnail_url"):
            info["cover"] = oe["thumbnail_url"]
        if oe.get("author_url"):
            m = re.search(r"/@([\w.\-]+)", oe["author_url"])
            if m:
                info["author"] = m.group(1)
    except Exception:
        pass

    for ua in (UA_MOBILE, UA):
        try:
            html, final = http_get(nu, ua=ua, timeout=14, referer="https://www.tiktok.com/")
            if "tiktok.com" in final:
                info["videoUrl"] = final.split("?")[0]
            for pat in (
                r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
                r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            ):
                m = re.search(pat, html, re.S)
                if not m:
                    continue
                try:
                    dig_detail(json.loads(m.group(1)), info)
                except Exception:
                    pass
            if not info["description"]:
                om = re.search(r'property="og:description"\s+content="([^"]*)"', html, re.I)
                if not om:
                    om = re.search(r'content="([^"]*)"\s+property="og:description"', html, re.I)
                if om:
                    info["description"] = unescape(om.group(1))
            if not info["cover"]:
                om = re.search(r'property="og:image"\s+content="([^"]*)"', html, re.I)
                if om:
                    info["cover"] = unescape(om.group(1))
            # any mp4 in page as last resort play
            if not info.get("playUrl"):
                mp4s = re.findall(r"https?://[^\"'\s]+(?:tiktokcdn|musical\.ly|byte)[^\"'\s]+\.mp4[^\"'\s]*", html)
                if mp4s:
                    info["playUrl"] = mp4s[0].encode().decode("unicode_escape", "ignore") if "\\u" in mp4s[0] else mp4s[0]
            links = extract_preset_links(info.get("description") or "")
            links += [x for x in extract_preset_links(html) if x not in links]
            info["presetLinks"] = links[:8]
            if info.get("description") or info.get("playUrl") or info.get("stats"):
                break
        except Exception:
            continue

    if info.get("author") and info.get("id") and not info.get("videoUrl"):
        info["videoUrl"] = f"https://www.tiktok.com/@{info['author']}/video/{info['id']}"
    return info


def _add_url(bucket, seen, url):
    nu, a, v = normalize_tt(url)
    key = v or nu
    if not key or key in seen:
        return
    if "tiktok.com" not in nu:
        return
    seen.add(key)
    bucket.append(nu)



def discover(query):
    """Find TikTok video URLs for a keyword. Uses multiple public indexes."""
    urls, seen = [], set()
    q = (query or "").strip()
    if not q:
        return []

    variants = [
        f"{q} tiktok",
        f"{q} preset tiktok",
        f"{q} #preset tiktok",
        f"{q} alight motion tiktok",
        f'site:tiktok.com/video {q}',
        f'site:tiktok.com {q}',
        f'"{q}" site:tiktok.com',
    ]

    def pull(html):
        if not html:
            return
        for m in TT_VIDEO_RE.finditer(html):
            _add_url(urls, seen, m.group(0))
        for m in re.finditer(r"uddg=([^&\"']+)", html):
            try:
                link = unquote(m.group(1))
            except Exception:
                continue
            if "tiktok.com" in link:
                _add_url(urls, seen, link)
        for m in re.finditer(r"/url\?q=(https?://[^&]+)", html):
            try:
                link = unquote(m.group(1))
            except Exception:
                continue
            if "tiktok.com" in link:
                _add_url(urls, seen, link)

    # 1) DuckDuckGo lite + html (often works from cloud)
    for vq in variants:
        if len(urls) >= 15:
            break
        for base in (
            f"https://lite.duckduckgo.com/lite/?q={quote(vq)}",
            f"https://html.duckduckgo.com/html/?q={quote(vq)}",
        ):
            try:
                html, _ = http_get(base, timeout=12, referer="https://duckduckgo.com/")
                pull(html)
            except Exception:
                pass
        if len(urls) >= 8:
            break

    # 2) Bing
    if len(urls) < 8:
        for vq in variants[:4]:
            try:
                html, _ = http_get(
                    f"https://www.bing.com/search?q={quote(vq)}&count=20",
                    timeout=12,
                    referer="https://www.bing.com/",
                )
                pull(html)
            except Exception:
                pass
            if len(urls) >= 10:
                break

    # 3) TikTok search pages (SSR may be empty, but sometimes has ids)
    if len(urls) < 5:
        for kw in (q, f"{q} preset", f"{q} #preset"):
            for path in (
                f"https://www.tiktok.com/search/video?q={quote(kw)}",
                f"https://www.tiktok.com/search?q={quote(kw)}",
            ):
                try:
                    html, _ = http_get(path, ua=UA_MOBILE, timeout=12, referer="https://www.tiktok.com/")
                    pull(html)
                    for m in re.finditer(r'"uniqueId"\s*:\s*"([^"]+)".{0,240}?"id"\s*:\s*"(\d{10,})"', html, re.S):
                        _add_url(urls, seen, f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}")
                    for m in re.finditer(r'"id"\s*:\s*"(\d{10,})".{0,240}?"uniqueId"\s*:\s*"([^"]+)"', html, re.S):
                        _add_url(urls, seen, f"https://www.tiktok.com/@{m.group(2)}/video/{m.group(1)}")
                except Exception:
                    pass
            if len(urls) >= 8:
                break

    return urls[:20]



def caption_matches(desc, query):
    d = (desc or "").lower()
    q = (query or "").lower().strip()
    if not q or not d:
        return False
    if q in d:
        return True
    tokens = [t for t in re.split(r"\s+", q) if t]
    if not tokens:
        return False
    # any meaningful token (>=2 chars) in caption counts
    if any(t in d for t in tokens if len(t) >= 2):
        return True
    if all(t in d for t in tokens):
        return True
    hit = sum(1 for t in tokens if t in d)
    return hit >= max(1, (len(tokens) + 1) // 2)


def search_own(query):
    q = query.strip()
    if not q:
        return []

    key = q.lower()
    now = time.time()
    # Hanya pakai cache jika ADA hasil (supaya search bisa diulang terus)
    if key in _CACHE:
        ts, cached = _CACHE[key]
        if cached and (now - ts) < _CACHE_TTL:
            return [dict(x) for x in cached]  # copy
        # hapus cache kosong / expired
        if not cached or (now - ts) >= _CACHE_TTL:
            _CACHE.pop(key, None)

    candidates = discover(q)
    if not candidates:
        simple = re.sub(r"[#@]+", " ", q).strip()
        if simple and simple.lower() != key:
            candidates = discover(simple)
    if not candidates:
        # jangan cache kosong
        return []

    results = []
    # parallel read for speed + reliability
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(read_video_page, u): u for u in candidates}
        for fut in as_completed(futs):
            src_url = futs[fut]
            try:
                info = fut.result()
            except Exception:
                nu, a, v = normalize_tt(src_url)
                results.append({
                    "id": v,
                    "description": "Video TikTok ditemukan · ketuk untuk cari preset",
                    "author": a or "tiktok",
                    "cover": "",
                    "playUrl": "",
                    "videoUrl": nu,
                    "presetLinks": [],
                    "stats": {},
                    "_match": False,
                })
                continue
            desc = info.get("description") or ""
            matched = caption_matches(desc, q)
            tokens = [t for t in re.split(r"\s+", q.lower()) if t]
            any_token = any(t in desc.lower() for t in tokens) if tokens else False
            # SELALU tampilkan video yang berhasil ditemukan (meski caption gagal diload)
            if not (info.get("videoUrl") or info.get("id")):
                continue

            st = info.get("stats") or {}
            results.append({
                "id": info.get("id") or "",
                "description": desc or ("Video TikTok · @" + (info.get("author") or "user")),
                "author": info.get("author") or "",
                "cover": info.get("cover") or "",
                "playUrl": proxy_play(info.get("playUrl") or ""),
                "videoUrl": info.get("videoUrl") or "",
                "presetLinks": info.get("presetLinks") or [],
                "stats": {
                    "views": st.get("views"),
                    "likes": st.get("likes"),
                    "comments": st.get("comments"),
                    "shares": st.get("shares"),
                    "viewsText": fmt_num(st.get("views")),
                    "likesText": fmt_num(st.get("likes")),
                    "commentsText": fmt_num(st.get("comments")),
                },
                "_match": bool(matched or any_token),
            })

    # Jika enrich gagal semua, tetap kirim daftar URL yang ditemukan
    if not results and candidates:
        for u in candidates[:12]:
            nu, a, v = normalize_tt(u)
            results.append({
                "id": v,
                "description": "Video TikTok ditemukan · ketuk untuk cari preset",
                "author": a or "tiktok",
                "cover": "",
                "playUrl": "",
                "videoUrl": nu,
                "presetLinks": [],
                "stats": {"views": None, "likes": None, "comments": None, "shares": None,
                          "viewsText": None, "likesText": None, "commentsText": None},
                "_match": False,
            })

    # sort: caption match + preset first
    def score(r):

        s = 0
        if r.get("_match"):
            s += 100
        if r.get("presetLinks"):
            s += 50
        if r.get("playUrl"):
            s += 20
        st = r.get("stats") or {}
        if st.get("views"):
            s += min(int(st["views"]) // 10000, 30)
        return s

    results.sort(key=score, reverse=True)
    for r in results:
        r.pop("_match", None)

    out = results[:15]
    if out:
        _CACHE[key] = (time.time(), out)
    return out


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
            body = json.dumps({"ok": False, "results": [], "message": "kata kunci kosong"}).encode()
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
                "message": None if results else "Yah, tidak ada preset-nya",
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
                "message": "Yah, tidak ada preset-nya",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
