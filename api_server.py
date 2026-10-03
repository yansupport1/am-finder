#!/usr/bin/env python3
"""
AM Preset Finder API — by Yanz
Compatible with the existing frontend EventSource protocol:
  GET /api/find?url=<tiktok_url>
  SSE events: "result" with JSON {ok, presetLinks, video, author, ...}
             "error" with message
"""

from __future__ import annotations

import asyncio
import json
import re
import traceback
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="AM Preset Finder API by Yanz")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- patterns for Alight Motion preset links ----------
AM_PATTERNS = [
    re.compile(r"https?://(?:www\.)?alight\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?am\.link/[^\s\"'<>]+", re.I),
    re.compile(r"https?://link\.alightmotion\.com/[^\s\"'<>]+", re.I),
    re.compile(r"https?://[^\s\"'<>]*alight[^\s\"'<>]*preset[^\s\"'<>]*", re.I),
    re.compile(r"https?://[^\s\"'<>]*(?:am-preset|ampreset|alight-preset)[^\s\"'<>]*", re.I),
    # common short share forms used by creators
    re.compile(r"https?://(?:bit\.ly|t\.co|tinyurl\.com|cutt\.ly|s\.id)/[^\s\"'<>]+", re.I),
]

PRESET_HINT = re.compile(
    r"(alight\s*motion|am\s*preset|preset\s*am|link\s*preset|preset\s*link|"
    r"xml\s*preset|5\s*mb|5mb\s*preset|download\s*preset)",
    re.I,
)

UA = (
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
)

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
}


def sse(event: str, data: Any) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def extract_urls_from_text(text: str) -> list[str]:
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for pat in AM_PATTERNS:
        for m in pat.finditer(text):
            u = m.group(0).rstrip(".,);]'\"")
            if u not in seen:
                seen.add(u)
                found.append(u)
    return found


def classify_preset(url: str, context: str = "") -> dict[str, Any]:
    low = (url + " " + context).lower()
    ptype = "5mb" if ("5mb" in low or "5 mb" in low) else "xml"
    title = "5MB preset" if ptype == "5mb" else "XML preset"
    detail = None
    if "caption" in context.lower() or "description" in context.lower():
        detail = "Found in video caption"
    elif "comment" in context.lower():
        detail = "Found in comments"
    elif "bio" in context.lower():
        detail = "Found in account bio"
    elif "reply" in context.lower():
        detail = "Found in comment replies"
    return {
        "url": url,
        "type": ptype,
        "title": title,
        "detail": detail,
        "byAuthor": "caption" in (context or "").lower() or "bio" in (context or "").lower(),
    }


async def resolve_short_url(client: httpx.AsyncClient, url: str) -> str:
    """Follow redirects for vt.tiktok.com / vm.tiktok.com etc."""
    try:
        r = await client.head(url, follow_redirects=True, timeout=15.0)
        return str(r.url)
    except Exception:
        try:
            r = await client.get(url, follow_redirects=True, timeout=15.0)
            return str(r.url)
        except Exception:
            return url


def parse_video_id(url: str) -> str | None:
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(\d+)", url)
    return m.group(1) if m else None


def parse_username(url: str) -> str | None:
    m = re.search(r"tiktok\.com/@([^/?\s]+)", url)
    return m.group(1) if m else None


async def fetch_oembed(client: httpx.AsyncClient, url: str) -> dict | None:
    try:
        r = await client.get(
            "https://www.tiktok.com/oembed",
            params={"url": url},
            timeout=12.0,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


async def fetch_page_html(client: httpx.AsyncClient, url: str) -> str:
    r = await client.get(url, timeout=20.0)
    r.raise_for_status()
    return r.text


def extract_hydration(html: str) -> dict | None:
    """Pull TikTok's __UNIVERSAL_DATA_FOR_REHYDRATION__ or SIGI_STATE style blob."""
    patterns = [
        r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script\s+id="SIGI_STATE"[^>]*>(.*?)</script>',
        r"window\['SIGI_STATE'\]\s*=\s*(\{.*?\});",
        r'<script\s+type="application/json"\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL | re.I)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return None


def walk_find_strings(obj: Any, bag: list[str], depth: int = 0) -> None:
    if depth > 12:
        return
    if isinstance(obj, str):
        if len(obj) > 8 and ("http" in obj or "alight" in obj.lower() or "preset" in obj.lower()):
            bag.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            walk_find_strings(v, bag, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:80]:
            walk_find_strings(v, bag, depth + 1)


def dig_video_detail(data: dict) -> dict:
    """Best-effort extract video description, author, stats from hydration JSON."""
    out: dict[str, Any] = {
        "description": "",
        "author": "",
        "author_avatar": "",
        "cover": "",
        "play_url": "",
        "stats": {},
        "comments_text": [],
        "bio": "",
    }

    # common paths in recent TikTok web payloads
    candidates = []
    def collect(obj, path=""):
        if isinstance(obj, dict):
            if "desc" in obj and isinstance(obj.get("desc"), str):
                candidates.append(("desc", obj["desc"], obj))
            if "description" in obj and isinstance(obj.get("description"), str):
                candidates.append(("description", obj["description"], obj))
            if "uniqueId" in obj or "unique_id" in obj:
                uid = obj.get("uniqueId") or obj.get("unique_id")
                if uid:
                    out["author"] = out["author"] or str(uid)
            if "nickname" in obj and not out["author"]:
                out["author"] = str(obj["nickname"])
            if "avatarThumb" in obj or "avatarLarger" in obj or "avatarMedium" in obj:
                out["author_avatar"] = (
                    obj.get("avatarLarger")
                    or obj.get("avatarMedium")
                    or obj.get("avatarThumb")
                    or out["author_avatar"]
                )
            if "signature" in obj and isinstance(obj["signature"], str):
                out["bio"] = out["bio"] or obj["signature"]
            if "stats" in obj and isinstance(obj["stats"], dict):
                st = obj["stats"]
                out["stats"] = {
                    "views": st.get("playCount") or st.get("play_count") or st.get("views"),
                    "likes": st.get("diggCount") or st.get("digg_count") or st.get("likes"),
                    "comments": st.get("commentCount") or st.get("comment_count") or st.get("comments"),
                } or out["stats"]
            if "video" in obj and isinstance(obj["video"], dict):
                v = obj["video"]
                out["cover"] = v.get("cover") or v.get("originCover") or out["cover"]
                out["play_url"] = v.get("playAddr") or v.get("downloadAddr") or out["play_url"]
            if "text" in obj and isinstance(obj.get("text"), str) and len(obj["text"]) > 2:
                # possible comment text
                if "comment" in path.lower() or "cid" in obj or "aweme_id" in str(obj.keys()):
                    out["comments_text"].append(obj["text"])
            for k, v in obj.items():
                collect(v, path + "." + str(k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj[:100]):
                collect(v, path + f"[{i}]")

    collect(data)

    # pick longest desc-like string
    if candidates:
        candidates.sort(key=lambda x: len(x[1]), reverse=True)
        out["description"] = candidates[0][1]

    return out


async def scrape_tiktok(url: str) -> dict[str, Any]:
    """Main scraper: resolve → oembed → page hydration → scan texts for AM links."""
    preset_links: list[dict] = []
    seen_urls: set[str] = set()

    def add_presets(urls: list[str], context: str):
        for u in urls:
            # skip pure tiktok links unless they look like preset hosts
            host = urlparse(u).netloc.lower()
            if "tiktok.com" in host and "alight" not in u.lower():
                continue
            if u in seen_urls:
                continue
            # for short links, keep them — user can open; optional resolve later
            if any(x in host for x in ("bit.ly", "t.co", "tinyurl", "cutt.ly", "s.id")):
                # only keep if surrounding text hints preset
                if not PRESET_HINT.search(context):
                    continue
            seen_urls.add(u)
            preset_links.append(classify_preset(u, context))

    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=25.0) as client:
        final_url = await resolve_short_url(client, url)
        video_id = parse_video_id(final_url)
        username = parse_username(final_url)

        description = ""
        author = username or ""
        author_avatar = ""
        cover = ""
        play_url = ""
        stats: dict = {}
        bio = ""
        comments_blob = ""

        # 1) oEmbed — title often contains caption snippet
        oembed = await fetch_oembed(client, final_url)
        if oembed:
            description = oembed.get("title") or description
            author = oembed.get("author_name") or author
            author_avatar = oembed.get("thumbnail_url") or author_avatar
            cover = oembed.get("thumbnail_url") or cover

        # 2) Full page hydration
        html = ""
        try:
            html = await fetch_page_html(client, final_url)
        except Exception as e:
            # if page fails but we have oembed, continue
            if not oembed:
                raise RuntimeError(f"Could not open TikTok link: {e}") from e

        if html:
            # raw link scan on whole HTML (catches some embedded share urls)
            add_presets(extract_urls_from_text(html), "page html")

            data = extract_hydration(html)
            if data:
                detail = dig_video_detail(data)
                description = detail["description"] or description
                author = detail["author"] or author
                author_avatar = detail["author_avatar"] or author_avatar
                cover = detail["cover"] or cover
                play_url = detail["play_url"] or play_url
                stats = detail["stats"] or stats
                bio = detail["bio"] or bio
                if detail["comments_text"]:
                    comments_blob = "\n".join(detail["comments_text"])

                # deep string walk for any AM urls buried in JSON
                bag: list[str] = []
                walk_find_strings(data, bag)
                add_presets(extract_urls_from_text("\n".join(bag)), "embedded data")

        # 3) Scan caption / description
        add_presets(extract_urls_from_text(description), "video caption / description")

        # 4) Scan bio
        add_presets(extract_urls_from_text(bio), "account bio")

        # 5) Scan comments text we managed to extract
        add_presets(extract_urls_from_text(comments_blob), "comments")

        # Also scan description for plain text that might contain partial links
        if PRESET_HINT.search(description or "") and not preset_links:
            # hint present but no link captured — still return empty with ok
            pass

        result = {
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
        return result


@app.get("/api/find")
async def api_find(url: str = Query(..., min_length=8)):
    async def event_stream():
        try:
            url_clean = unquote(url).strip()
            if not re.search(r"tiktok\.com|vt\.tiktok|vm\.tiktok", url_clean, re.I):
                yield sse(
                    "result",
                    {"ok": False, "error": "Please paste a valid TikTok video link"},
                )
                return

            # small progress-friendly delay so UI shows "Searching"
            await asyncio.sleep(0.3)

            result = await scrape_tiktok(url_clean)

            if not result.get("presetLinks"):
                # still ok — frontend shows empty / not found via length
                # keep ok True so UI can show video meta + "no presets"
                pass

            yield sse("result", result)
        except Exception as e:
            traceback.print_exc()
            msg = str(e)[:180] or "scraper failed"
            yield sse("result", {"ok": False, "error": msg})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/track")
async def api_track(request: Request):
    # no-op analytics stub (frontend still posts here)
    try:
        await request.json()
    except Exception:
        pass
    return {"ok": True}


@app.get("/api/health")
async def health():
    return {"ok": True, "by": "Yanz"}


# Serve the static frontend from the same process
app.mount("/", StaticFiles(directory="/home/workdir/artifacts/amfinder", html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    print("AM Preset Finder API by Yanz")
    print("Open http://127.0.0.1:8787")
    uvicorn.run(app, host="0.0.0.0", port=8787, log_level="info")
