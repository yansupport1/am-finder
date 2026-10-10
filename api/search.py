"""
GET /api/search?q=<kata kunci>

Cari video TikTok untuk sebuah kata/judul lagu, lalu kembalikan daftar video
(videoUrl, author, description, cover, stats). Klik video di frontend tetap
memanggil GET /api/find?url=<videoUrl> (SSE) seperti sebelumnya.

Perubahan utama dibanding versi lama:
  * Semua mesin pencari dipanggil PARALEL dengan batas waktu total (deadline),
    bukan puluhan request berurutan yang bisa melewati maxDuration Vercel.
  * Halaman "diblok" (HTTP 202/403/429, captcha, anomaly) dibedakan dari
    "memang tidak ada hasil" -> pesan ke user jujur.
  * Cache kosong tidak pernah dipakai lama (15 dtk, dan tidak ada sama sekali
    untuk kasus diblok). Cache hasil bagus 10 menit + dipakai sebagai cadangan
    (stale) saat pencarian live sedang diblok.
  * Regex URL TikTok: dengan/tanpa scheme, m./vm./www., %40, %2F, bentuk
    breadcrumb "tiktok.com > @user > video > 123", dan link redirect Bing.
  * Setiap item selalu punya: id, videoUrl, author, description, cover,
    playUrl, presetLinks, stats.
  * Hook opsional RAPIDAPI_KEY (lihat rapidapi_search).

Env opsional:
  SEARCH_BUDGET        total detik per request (default 25). Kalau batas
                       fungsi Vercel kamu hanya 10 dtk, set 8.
  RAPIDAPI_KEY         aktifkan layer RapidAPI (dicoba pertama).
  RAPIDAPI_HOST        default tiktok-scraper7.p.rapidapi.com
  RAPIDAPI_SEARCH_PATH default /feed/search
  RAPIDAPI_QUERY_PARAM default keywords
"""
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote, unquote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from concurrent.futures import ThreadPoolExecutor, wait
from html import unescape
import base64
import json
import os
import random
import re
import sys
import threading
import time
import traceback

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
UA_MOBILE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1"
)
_UAS = [
    UA,
    UA_MOBILE,
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

MSG_BLOCKED = (
    "Pencarian otomatis sedang dibatasi dari sisi server (mesin pencari/TikTok "
    "memblokir IP server). Ini BUKAN berarti videonya tidak ada. "
    "Buka TikTok, salin link videonya, lalu tempel di tab \"Cari via Link Video\"."
)
MSG_EMPTY = (
    "Belum ada video yang cocok dari pencarian otomatis untuk kata ini. "
    "Itu tidak berarti tidak ada di TikTok. Coba kata lain (judul lagu + nama "
    "pembuat), atau tempel link video di tab \"Cari via Link Video\"."
)
MSG_STALE = (
    "Pencarian live sedang dibatasi, jadi ini hasil tersimpan sebelumnya untuk kata yang sama."
)

# ---------------------------------------------------------------------------
# Preset links (menandai video yang captionnya sudah berisi link)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Regex URL video TikTok
# ---------------------------------------------------------------------------
# Cocok untuk:
#   https://www.tiktok.com/@user/video/123
#   http://tiktok.com/@user/video/123
#   tiktok.com/@user/video/123                  (tanpa scheme)
#   m. / vm. / www.tiktok.com
#   https%3A%2F%2Fwww.tiktok.com%2F%40user%2Fvideo%2F123   (url-encoded)
#   tiktok.com > @user > video > 123 (breadcrumb mesin pencari, tanda U+203A)
# (?<![\w.\-]) mencegah match di tengah domain lain, mis. "faketiktok.com".
_SEP = r"(?:/|%2[fF]|\s*\u203a\s*)"
TT_VIDEO_RE = re.compile(
    r"(?<![\w.\-])(?:https?:(?:/|%2[fF]){2})?(?:(?:www|m|vm)\.)?tiktok\.com"
    + _SEP + r"(?:@|%40)([\w.\-]+)" + _SEP + r"video" + _SEP + r"(\d{1,25})(?!\d)",
    re.I,
)
_PAIR1 = re.compile(r'"uniqueId"\s*:\s*"([^"]+)".{0,240}?"id"\s*:\s*"(\d{10,})"', re.S)
_PAIR2 = re.compile(r'"id"\s*:\s*"(\d{10,})".{0,240}?"uniqueId"\s*:\s*"([^"]+)"', re.S)
# Bing membungkus link hasil: /ck/a?...&u=a1<base64url dari URL tujuan>
_BING_U = re.compile(r"[?&;]u=a1([A-Za-z0-9_\-]{12,})")


def canon_url(author, vid):
    return "https://www.tiktok.com/@%s/video/%s" % (author, vid)


def _text_views(text):
    """Beberapa 'sudut pandang' atas teks yang sama supaya regex tidak luput."""
    t = unescape(text)
    views = [t]
    if "%" in t:
        u1 = unquote(t)
        views.append(u1)
        if "%" in u1:
            views.append(unquote(u1))
    if "<" in t:
        views.append(re.sub(r"<[^>]{0,400}>", "", t))
    extra = []
    for m in _BING_U.finditer(t):
        s = m.group(1)
        try:
            extra.append(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode("utf-8", "ignore"))
        except Exception:
            pass
    if extra:
        views.append("\n".join(extra))
    return views


def extract_tiktok_urls(text, pairs=False):
    """Return list of (video_id, canonical_url, author), unik per video_id, urut kemunculan."""
    out = {}
    if not text:
        return []
    for v in _text_views(text):
        for m in TT_VIDEO_RE.finditer(v):
            author, vid = m.group(1), m.group(2)
            if vid not in out:
                out[vid] = (vid, canon_url(author, vid), author)
    if pairs:
        for m in _PAIR1.finditer(text):
            vid = m.group(2)
            if vid not in out:
                out[vid] = (vid, canon_url(m.group(1), vid), m.group(1))
        for m in _PAIR2.finditer(text):
            vid = m.group(1)
            if vid not in out:
                out[vid] = (vid, canon_url(m.group(2), vid), m.group(2))
    return list(out.values())


# ---------------------------------------------------------------------------
# HTTP (tidak pernah melempar exception; status dikembalikan apa adanya)
# ---------------------------------------------------------------------------
class Resp(object):
    __slots__ = ("status", "text", "url", "error")

    def __init__(self, status, text, url, error):
        self.status = status
        self.text = text
        self.url = url
        self.error = error


def fetch(url, ua=None, timeout=7.0, referer=None, data=None, headers=None):
    h = {
        "User-Agent": ua or random.choice(_UAS),
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache",
    }
    if referer:
        h["Referer"] = referer
    body = None
    if data is not None:
        body = urlencode(data).encode("utf-8")
        h["Content-Type"] = "application/x-www-form-urlencoded"
    if headers:
        h.update(headers)
    try:
        req = Request(url, data=body, headers=h)
        with urlopen(req, timeout=max(0.5, timeout)) as r:
            raw = r.read(1500000)
            return Resp(r.getcode(), raw.decode("utf-8", "ignore"), r.geturl(), None)
    except HTTPError as e:
        try:
            raw = e.read(200000).decode("utf-8", "ignore")
        except Exception:
            raw = ""
        return Resp(e.code, raw, url, "http %s" % e.code)
    except Exception as e:  # timeout, DNS, SSL, reset, ...
        return Resp(0, "", url, type(e).__name__)


_BLOCK_MARKERS = (
    "anomaly-modal", "unusual traffic", "captcha", "are you a robot",
    "are you a human", "verify you are", "cf-chl", "just a moment",
    "access denied", "too many requests", "temporarily blocked",
)


def classify(resp, has_urls):
    """'ok' (halaman valid, ada/tidak ada hasil), 'blocked', atau 'error'."""
    if has_urls:
        return "ok"
    if resp.status == 0:
        return "error"
    if resp.status in (202, 403, 429, 503):
        return "blocked"
    if resp.status >= 400:
        return "error"
    low = (resp.text or "").lower()
    for mk in _BLOCK_MARKERS:
        if mk in low:
            return "blocked"
    return "ok"


# ---------------------------------------------------------------------------
# Cache (per instance serverless, jadi hanya bonus, bukan syarat kerja)
# ---------------------------------------------------------------------------
_CACHE = {}
_CACHE_LOCK = threading.Lock()
TTL_OK = 600        # hasil bagus
TTL_EMPTY = 15      # kosong asli (bukan diblok) - sengaja singkat
TTL_STALE = 3600    # cadangan bila live diblok
MAX_CACHE = 64


def cache_get(key, allow_stale=False):
    with _CACHE_LOCK:
        e = _CACHE.get(key)
    if not e:
        return None
    age = time.time() - e["ts"]
    if e["results"]:
        limit = TTL_STALE if allow_stale else TTL_OK
    else:
        if allow_stale:
            return None
        limit = TTL_EMPTY
    return e if age < limit else None


def cache_put(key, results):
    with _CACHE_LOCK:
        _CACHE[key] = {"ts": time.time(), "results": results}
        if len(_CACHE) > MAX_CACHE:
            oldest = sorted(_CACHE.items(), key=lambda kv: kv[1]["ts"])[: len(_CACHE) - MAX_CACHE]
            for k, _ in oldest:
                _CACHE.pop(k, None)


# ---------------------------------------------------------------------------
# Util kecil
# ---------------------------------------------------------------------------
def as_int(v):
    try:
        if v is None or v == "":
            return None
        return int(v)
    except Exception:
        return None


def fmt_num(n):
    n = as_int(n)
    if n is None:
        return None
    if n >= 1000000:
        return "%.1fM" % (n / 1000000.0)
    if n >= 1000:
        return "%.1fK" % (n / 1000.0)
    return str(n)


def proxy_play(url):
    if not url or not str(url).startswith("http"):
        return ""
    return "/api/video?url=" + quote(url, safe="")


def budget():
    try:
        return max(4.0, float(os.environ.get("SEARCH_BUDGET", "25")))
    except Exception:
        return 25.0


def clean_query(q):
    return re.sub(r"\s+", " ", (q or "")).strip()[:120]


# ---------------------------------------------------------------------------
# Layer 0 (opsional): RapidAPI, dicoba pertama bila RAPIDAPI_KEY diset.
# CATATAN: skema respons tiap penyedia berbeda; parser sengaja longgar dan
# belum diuji dengan key asli. Kalau gagal, otomatis jatuh ke mesin pencari.
# ---------------------------------------------------------------------------
def _item_from_dict(d):
    vid = d.get("video_id") or d.get("aweme_id") or d.get("id")
    vid = str(vid) if vid is not None else ""
    if not vid.isdigit() or len(vid) < 10:
        return None
    st = d.get("statistics") or d.get("stats")
    if not (isinstance(st, dict) or d.get("create_time") or d.get("play_count") is not None
            or d.get("digg_count") is not None):
        return None  # bukan objek video (mis. objek musik/author)
    a = d.get("author")
    uid = ""
    if isinstance(a, dict):
        uid = a.get("unique_id") or a.get("uniqueId") or ""
    desc = d.get("title") or d.get("desc") or d.get("description") or ""
    if not isinstance(desc, str):
        desc = ""
    if not isinstance(st, dict):
        st = {}
    cover = d.get("cover") or d.get("origin_cover") or ""
    if isinstance(cover, dict):
        ul = cover.get("url_list") or []
        cover = ul[0] if ul else ""
    play = d.get("play") or d.get("wmplay") or ""
    return {
        "id": vid,
        "author": re.sub(r"[^\w.\-]", "", str(uid)) or "user",
        "description": desc,
        "cover": cover if isinstance(cover, str) else "",
        "playUrl": play if isinstance(play, str) else "",
        "views": as_int(d.get("play_count", st.get("playCount", st.get("play_count")))),
        "likes": as_int(d.get("digg_count", st.get("diggCount", st.get("digg_count")))),
        "comments": as_int(d.get("comment_count", st.get("commentCount", st.get("comment_count")))),
        "shares": as_int(d.get("share_count", st.get("shareCount", st.get("share_count")))),
    }


def _harvest(obj, out, depth=0):
    if depth > 8 or len(out) >= 30:
        return
    if isinstance(obj, list):
        for v in obj[:60]:
            _harvest(v, out, depth + 1)
    elif isinstance(obj, dict):
        it = _item_from_dict(obj)
        if it and it["id"] not in out:
            out[it["id"]] = it
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _harvest(v, out, depth + 1)


def rapidapi_search(q, deadline):
    key = (os.environ.get("RAPIDAPI_KEY") or "").strip()
    if not key:
        return []
    host = os.environ.get("RAPIDAPI_HOST", "tiktok-scraper7.p.rapidapi.com")
    path = os.environ.get("RAPIDAPI_SEARCH_PATH", "/feed/search")
    param = os.environ.get("RAPIDAPI_QUERY_PARAM", "keywords")
    url = "https://%s%s?%s" % (host, path, urlencode({param: q, "count": 20}))
    r = fetch(url, timeout=min(10.0, deadline - time.time()),
              headers={"X-RapidAPI-Key": key, "X-RapidAPI-Host": host})
    if r.status != 200:
        return []
    try:
        j = json.loads(r.text)
    except Exception:
        return []
    found = {}
    _harvest(j, found)
    return list(found.values())


# ---------------------------------------------------------------------------
# Layer 1-3: discovery lewat indeks publik (paralel, dengan deadline)
# ---------------------------------------------------------------------------
def _eng_ddg_html(q):
    return "https://html.duckduckgo.com/html/", {"q": q, "kl": "id-id"}, "https://html.duckduckgo.com/"


def _eng_ddg_lite(q):
    return "https://lite.duckduckgo.com/lite/?q=" + quote(q), None, "https://lite.duckduckgo.com/"


def _eng_bing(q):
    return "https://www.bing.com/search?q=" + quote(q) + "&count=30&setlang=id", None, "https://www.bing.com/"


def _eng_yahoo(q):
    return "https://search.yahoo.com/search?p=" + quote(q) + "&n=30", None, "https://search.yahoo.com/"


def _eng_brave(q):
    return "https://search.brave.com/search?q=" + quote(q) + "&source=web", None, "https://search.brave.com/"


def _eng_tiktok(q):
    return "https://www.tiktok.com/search/video?q=" + quote(q), None, "https://www.tiktok.com/"


ENGINES = [
    ("ddg_html", _eng_ddg_html),
    ("ddg_lite", _eng_ddg_lite),
    ("bing", _eng_bing),
    ("yahoo", _eng_yahoo),
    ("brave", _eng_brave),
    ("tiktok", _eng_tiktok),  # SSR sering kosong dari datacenter; hanya pelengkap
]


def query_variants(q, round_no):
    simple = re.sub(r"[#@\"']+", " ", q)
    simple = re.sub(r"\s+", " ", simple).strip() or q
    if round_no == 1:
        return ["site:tiktok.com %s" % q, "%s tiktok video" % simple]
    return ["%s alight motion preset tiktok" % simple, "tiktok.com %s" % simple]


def _engine_worker(name, build, variants, deadline, sink, health, lock):
    pairs = (name == "tiktok")
    last_ua = None
    for vq in variants:
        if deadline - time.time() < 0.8:
            return
        for attempt in range(2):
            remaining = deadline - time.time()
            if remaining < 0.8:
                return
            ua = random.choice([u for u in _UAS if u != last_ua] or _UAS)
            if name == "tiktok":
                ua = UA_MOBILE
            last_ua = ua
            url, data, ref = build(vq)
            r = fetch(url, ua=ua, timeout=min(7.0, remaining), referer=ref, data=data)
            found = extract_tiktok_urls(r.text, pairs=pairs) if r.text else []
            kind = classify(r, bool(found))
            with lock:
                for vid, canon, author in found:
                    sink.setdefault(vid, (canon, author))
                if name != "tiktok":  # halaman SSR TikTok kosong bukan bukti indeks terjangkau
                    health[kind] = health.get(kind, 0) + 1
            if kind == "ok":
                break
            # blokir/timeout: tunggu sebentar lalu coba sekali lagi dengan UA lain
            time.sleep(min(max(remaining / 4.0, 0.0), 0.5 + random.random() * 0.7))
        with lock:
            if len(sink) >= 8:
                return


def discover(q, round_no, deadline, sink, health, lock):
    variants = query_variants(q, round_no)
    ex = ThreadPoolExecutor(max_workers=len(ENGINES))
    futs = [ex.submit(_engine_worker, n, b, variants, deadline, sink, health, lock) for n, b in ENGINES]
    start = time.time()
    while True:
        if all(f.done() for f in futs):
            break
        now = time.time()
        if now >= deadline:
            break
        with lock:
            n = len(sink)
        if n >= 12 and now - start > 1.5:
            break
        time.sleep(0.1)
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        ex.shutdown(wait=False)


# ---------------------------------------------------------------------------
# Enrich: oEmbed (publik) + baca halaman video (opsional, best effort)
# ---------------------------------------------------------------------------
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

    if isinstance(desc, str) and desc and (
        obj.get("id") or obj.get("aweme_id") or isinstance(video, dict)
    ):
        if not isinstance(author, dict):
            author = {}
        if not isinstance(video, dict):
            video = {}
        if not isinstance(stats, dict):
            stats = {}
        uid = author.get("uniqueId") or author.get("unique_id") or ""
        cover = video.get("cover") or video.get("originCover") or video.get("dynamicCover") or ""
        if isinstance(cover, dict):
            ul = cover.get("url_list") or cover.get("UrlList") or []
            cover = ul[0] if ul else ""
        play = ""
        for k in ("playAddr", "downloadAddr", "play_addr", "download_addr", "playApi"):
            val = video.get(k)
            if isinstance(val, str) and val.startswith("http"):
                play = val
                break
            if isinstance(val, dict):
                ul = val.get("UrlList") or val.get("url_list") or val.get("urlList") or []
                if ul:
                    play = ul[0]
                    break
        if len(desc) > len(out.get("description") or ""):
            out["description"] = desc
        if uid:
            out["author"] = str(uid).lstrip("@")
        if isinstance(cover, str) and cover:
            out["cover"] = cover
        if play:
            out["playUrl"] = play
        for dst, keys in (("views", ("playCount", "play_count")), ("likes", ("diggCount", "digg_count")),
                          ("comments", ("commentCount", "comment_count")), ("shares", ("shareCount", "share_count"))):
            for k in keys:
                if as_int(stats.get(k)) is not None:
                    out[dst] = as_int(stats.get(k))
                    break

    for v in obj.values():
        if isinstance(v, (dict, list)):
            dig_detail(v, out, depth + 1)


def _oembed(canon, timeout):
    r = fetch("https://www.tiktok.com/oembed?url=" + quote(canon, safe=""),
              ua=UA, timeout=timeout, referer="https://www.tiktok.com/")
    if r.status != 200:
        return {}
    try:
        j = json.loads(r.text)
    except Exception:
        return {}
    out = {}
    if j.get("title"):
        out["description"] = str(j["title"])
    if j.get("thumbnail_url"):
        out["cover"] = j["thumbnail_url"]
    uid = j.get("author_unique_id")
    if not uid and j.get("author_url"):
        m = re.search(r"/@([\w.\-]+)", str(j["author_url"]))
        uid = m.group(1) if m else ""
    if uid:
        out["author"] = str(uid).lstrip("@")
    return out


def _page(canon, timeout):
    r = fetch(canon, ua=UA_MOBILE, timeout=timeout, referer="https://www.tiktok.com/")
    if r.status != 200 or not r.text:
        return {}
    html = r.text
    out = {}
    for pat in (
        r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    ):
        m = re.search(pat, html, re.S)
        if m:
            try:
                dig_detail(json.loads(m.group(1)), out)
            except Exception:
                pass
    if not out.get("description"):
        om = re.search(r'property="og:description"\s+content="([^"]*)"', html, re.I)
        if om:
            out["description"] = unescape(om.group(1))
    if not out.get("cover"):
        om = re.search(r'property="og:image"\s+content="([^"]*)"', html, re.I)
        if om:
            out["cover"] = unescape(om.group(1))
    links = extract_preset_links(out.get("description") or "")
    links += [x for x in extract_preset_links(html) if x not in links]
    out["presetLinks"] = links[:8]
    return out


def _enrich_one(canon, deadline, with_page):
    info = {}
    left = deadline - time.time()
    if left < 1.0:
        return info
    info.update(_oembed(canon, min(5.0, left)))
    left = deadline - time.time()
    if with_page and left > 3.0:
        page = _page(canon, min(7.0, left))
        for k, v in page.items():
            if v is not None and v != "" and k not in info:
                info[k] = v
    return info


def make_item(vid, canon, author, info):
    author = (info.get("author") or author or "").lstrip("@") or "tiktok"
    desc = (info.get("description") or "").strip()
    if not desc:
        desc = "Video TikTok \u00b7 @%s \u00b7 ketuk untuk cari preset" % author
    links = info.get("presetLinks") or extract_preset_links(desc)
    views, likes = info.get("views"), info.get("likes")
    comments, shares = info.get("comments"), info.get("shares")
    return {
        "id": vid,
        "videoUrl": canon or canon_url(author, vid),
        "author": author,
        "description": desc[:600],
        "cover": info.get("cover") or "",
        "playUrl": proxy_play(info.get("playUrl") or ""),
        "presetLinks": links[:8],
        "stats": {
            "views": views, "likes": likes, "comments": comments, "shares": shares,
            "viewsText": fmt_num(views), "likesText": fmt_num(likes), "commentsText": fmt_num(comments),
        },
    }


def enrich_all(cands, deadline):
    """cands: list of (vid, (canon, author)). Hasil parsial tetap dipakai bila waktu habis."""
    pool = ThreadPoolExecutor(max_workers=12)
    futs = {}
    for i, (vid, (canon, author)) in enumerate(cands):
        futs[pool.submit(_enrich_one, canon, deadline, i < 5)] = (vid, canon, author)
    wait(list(futs.keys()), timeout=max(0.2, deadline - time.time()))
    items = []
    for f, (vid, canon, author) in futs.items():
        info = {}
        if f.done():
            try:
                info = f.result() or {}
            except Exception:
                info = {}
        items.append(make_item(vid, canon, author, info))
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        pool.shutdown(wait=False)
    return items


def relevance(item, q):
    d = (item.get("description") or "").lower()
    ql = q.lower().strip()
    s = 0
    if ql and ql in d:
        s += 120
    toks = [t for t in re.split(r"[\s,]+", ql) if len(t) >= 2]
    if toks:
        s += int(80 * sum(1 for t in toks if t in d) / float(len(toks)))
    if item.get("presetLinks"):
        s += 50
    if item.get("cover"):
        s += 5
    v = (item.get("stats") or {}).get("views")
    if v:
        s += min(int(v) // 10000, 30)
    return s


def rank(items, q):
    scored = [(-relevance(it, q), i, it) for i, it in enumerate(items)]
    scored.sort(key=lambda x: (x[0], x[1]))
    return [it for _, _, it in scored][:15]


# ---------------------------------------------------------------------------
# Orkestrasi
# ---------------------------------------------------------------------------
def _body(q, results, status, source, message=None, took=0.0):
    return {
        "ok": True,
        "query": q,
        "count": len(results),
        "results": results,
        "message": message,
        "status": status,            # ok | empty | blocked | unreachable | stale
        "blocked": status in ("blocked", "unreachable"),
        "source": source,
        "tookMs": int(took * 1000),
    }


def run_search(raw_q):
    t0 = time.time()
    q = clean_query(raw_q)
    key = q.lower()

    hit = cache_get(key)
    if hit:
        res = hit["results"]
        return _body(q, res, "ok" if res else "empty", "cache",
                     None if res else MSG_EMPTY, time.time() - t0)

    B = budget()
    t_r1, t_r2, t_end = t0 + B * 0.50, t0 + B * 0.70, t0 + B * 0.92
    health = {"ok": 0, "blocked": 0, "error": 0}
    results, source = [], ""

    # Layer 0: RapidAPI (opsional)
    try:
        api_items = rapidapi_search(q, t0 + B * 0.5)
    except Exception:
        api_items = []
    if api_items:
        results = rank([make_item(i["id"], "", i["author"], i) for i in api_items], q)
        source = "rapidapi"

    # Layer 1-3: mesin pencari publik, paralel + putaran ke-2 bila kosong
    if not results:
        sink, lock = {}, threading.Lock()
        discover(q, 1, t_r1, sink, health, lock)
        if not sink and time.time() < t_r2 - 3.0:
            time.sleep(min(1.5, max(0.0, t_r2 - time.time() - 3.0)))
            discover(q, 2, t_r2, sink, health, lock)
        cands = list(sink.items())[:16]
        if cands:
            results = rank(enrich_all(cands, t_end), q)
            source = "search-engine"

    if results:
        status, msg = "ok", None
        cache_put(key, results)
    elif health.get("ok", 0) > 0:
        status, msg = "empty", MSG_EMPTY
        cache_put(key, [])            # singkat (TTL_EMPTY)
    else:
        status = "blocked" if health.get("blocked", 0) > 0 else "unreachable"
        msg = MSG_BLOCKED             # TIDAK di-cache
        stale = cache_get(key, allow_stale=True)
        if stale:
            results, status, msg, source = stale["results"], "stale", MSG_STALE, "cache-stale"

    try:
        sys.stderr.write("[search] q=%r status=%s n=%d health=%s src=%s took=%.1fs\n" % (
            q, status, len(results), health, source, time.time() - t0))
    except Exception:
        pass
    return _body(q, results, status, source, msg, time.time() - t0)


# ---------------------------------------------------------------------------
# Vercel handler
# ---------------------------------------------------------------------------
class handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
            self._send(400, {"ok": False, "results": [], "message": "kata kunci kosong"})
            return
        try:
            self._send(200, run_search(q))
        except Exception:
            traceback.print_exc()
            self._send(200, {
                "ok": True, "query": q, "count": 0, "results": [],
                "message": "Terjadi gangguan di server saat mencari. " + MSG_BLOCKED,
                "status": "unreachable", "blocked": True,
            })
