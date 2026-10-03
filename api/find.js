/**
 * AM Preset Finder API — by Yanz
 * Vercel Serverless: GET /api/find?url=<tiktok_url>
 * SSE event "result" → JSON { ok, presetLinks, video, author, ... }
 */

const AM_PATTERNS = [
  /https?:\/\/(?:www\.)?alight\.link\/[^\s"'<>]+/gi,
  /https?:\/\/(?:www\.)?alightmotion\.com\/[^\s"'<>]+/gi,
  /https?:\/\/(?:www\.)?am\.link\/[^\s"'<>]+/gi,
  /https?:\/\/link\.alightmotion\.com\/[^\s"'<>]+/gi,
  /https?:\/\/[^\s"'<>]*alight[^\s"'<>]*preset[^\s"'<>]*/gi,
  /https?:\/\/[^\s"'<>]*(?:am-preset|ampreset|alight-preset)[^\s"'<>]*/gi,
  /https?:\/\/(?:bit\.ly|t\.co|tinyurl\.com|cutt\.ly|s\.id)\/[^\s"'<>]+/gi,
];

const PRESET_HINT =
  /alight\s*motion|am\s*preset|preset\s*am|link\s*preset|preset\s*link|xml\s*preset|5\s*mb|5mb\s*preset|download\s*preset/i;

const UA =
  "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36";

function extractUrls(text) {
  if (!text) return [];
  const seen = new Set();
  const out = [];
  for (const pat of AM_PATTERNS) {
    pat.lastIndex = 0;
    let m;
    while ((m = pat.exec(text)) !== null) {
      const u = m[0].replace(/[.,);\]'"]+$/, "");
      if (!seen.has(u)) {
        seen.add(u);
        out.push(u);
      }
    }
  }
  return out;
}

function classify(url, context = "") {
  const low = (url + " " + context).toLowerCase();
  const type = low.includes("5mb") || low.includes("5 mb") ? "5mb" : "xml";
  let detail = null;
  const c = context.toLowerCase();
  if (c.includes("caption") || c.includes("description")) detail = "Found in video caption";
  else if (c.includes("comment")) detail = "Found in comments";
  else if (c.includes("bio")) detail = "Found in account bio";
  else if (c.includes("reply")) detail = "Found in comment replies";
  return {
    url,
    type,
    title: type === "5mb" ? "5MB preset" : "XML preset",
    detail,
    byAuthor: c.includes("caption") || c.includes("bio") || c.includes("description"),
  };
}

async function resolveUrl(url) {
  try {
    const r = await fetch(url, {
      method: "HEAD",
      redirect: "follow",
      headers: { "User-Agent": UA },
    });
    return r.url || url;
  } catch {
    try {
      const r = await fetch(url, {
        redirect: "follow",
        headers: { "User-Agent": UA },
      });
      return r.url || url;
    } catch {
      return url;
    }
  }
}

function parseVideoId(url) {
  const m = url.match(/\/video\/(\d+)/) || url.match(/[?&]id=(\d+)/);
  return m ? m[1] : null;
}

function parseUsername(url) {
  const m = url.match(/tiktok\.com\/@([^/?\s]+)/);
  return m ? m[1] : null;
}

async function fetchOembed(url) {
  try {
    const r = await fetch(
      "https://www.tiktok.com/oembed?url=" + encodeURIComponent(url),
      { headers: { "User-Agent": UA }, signal: AbortSignal.timeout(12000) }
    );
    if (r.ok) return await r.json();
  } catch {}
  return null;
}

async function fetchHtml(url) {
  const r = await fetch(url, {
    headers: {
      "User-Agent": UA,
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
    },
    signal: AbortSignal.timeout(20000),
  });
  if (!r.ok) throw new Error("TikTok returned " + r.status);
  return await r.text();
}

function extractHydration(html) {
  const patterns = [
    /<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>([\s\S]*?)<\/script>/i,
    /<script\s+id="SIGI_STATE"[^>]*>([\s\S]*?)<\/script>/i,
    /window\['SIGI_STATE'\]\s*=\s*(\{[\s\S]*?\});/i,
  ];
  for (const pat of patterns) {
    const m = html.match(pat);
    if (m) {
      try {
        return JSON.parse(m[1]);
      } catch {}
    }
  }
  return null;
}

function walkStrings(obj, bag, depth = 0) {
  if (depth > 12 || bag.length > 500) return;
  if (typeof obj === "string") {
    if (obj.length > 8 && (obj.includes("http") || /alight|preset/i.test(obj))) {
      bag.push(obj);
    }
  } else if (Array.isArray(obj)) {
    for (const v of obj.slice(0, 80)) walkStrings(v, bag, depth + 1);
  } else if (obj && typeof obj === "object") {
    for (const v of Object.values(obj)) walkStrings(v, bag, depth + 1);
  }
}

function digDetail(data) {
  const out = {
    description: "",
    author: "",
    author_avatar: "",
    cover: "",
    play_url: "",
    stats: {},
    comments_text: [],
    bio: "",
  };
  const descs = [];

  function collect(obj) {
    if (!obj || typeof obj !== "object") return;
    if (Array.isArray(obj)) {
      obj.slice(0, 100).forEach(collect);
      return;
    }
    if (typeof obj.desc === "string") descs.push(obj.desc);
    if (typeof obj.description === "string") descs.push(obj.description);
    if (obj.uniqueId || obj.unique_id) out.author = out.author || String(obj.uniqueId || obj.unique_id);
    if (obj.nickname && !out.author) out.author = String(obj.nickname);
    if (obj.avatarLarger || obj.avatarMedium || obj.avatarThumb) {
      out.author_avatar =
        obj.avatarLarger || obj.avatarMedium || obj.avatarThumb || out.author_avatar;
    }
    if (typeof obj.signature === "string") out.bio = out.bio || obj.signature;
    if (obj.stats && typeof obj.stats === "object") {
      const st = obj.stats;
      out.stats = {
        views: st.playCount ?? st.play_count ?? st.views ?? out.stats.views,
        likes: st.diggCount ?? st.digg_count ?? st.likes ?? out.stats.likes,
        comments: st.commentCount ?? st.comment_count ?? st.comments ?? out.stats.comments,
      };
    }
    if (obj.video && typeof obj.video === "object") {
      out.cover = obj.video.cover || obj.video.originCover || out.cover;
      out.play_url = obj.video.playAddr || obj.video.downloadAddr || out.play_url;
    }
    if (typeof obj.text === "string" && obj.text.length > 2 && (obj.cid || obj.aweme_id || obj.comment_id)) {
      out.comments_text.push(obj.text);
    }
    for (const v of Object.values(obj)) collect(v);
  }

  collect(data);
  if (descs.length) {
    descs.sort((a, b) => b.length - a.length);
    out.description = descs[0];
  }
  return out;
}

async function scrapeTikTok(url) {
  const presetLinks = [];
  const seen = new Set();

  function add(urls, context) {
    for (let u of urls) {
      const host = (() => {
        try {
          return new URL(u).hostname.toLowerCase();
        } catch {
          return "";
        }
      })();
      if (host.includes("tiktok.com") && !/alight/i.test(u)) continue;
      if (seen.has(u)) continue;
      if (/(bit\.ly|t\.co|tinyurl|cutt\.ly|s\.id)/.test(host)) {
        if (!PRESET_HINT.test(context)) continue;
      }
      seen.add(u);
      presetLinks.push(classify(u, context));
    }
  }

  const finalUrl = await resolveUrl(url);
  const videoId = parseVideoId(finalUrl);
  let author = parseUsername(finalUrl) || "";
  let description = "";
  let authorAvatar = "";
  let cover = "";
  let playUrl = "";
  let stats = {};
  let bio = "";
  let commentsBlob = "";

  // 1) oEmbed
  const oembed = await fetchOembed(finalUrl);
  if (oembed) {
    description = oembed.title || description;
    author = oembed.author_name || author;
    authorAvatar = oembed.thumbnail_url || authorAvatar;
    cover = oembed.thumbnail_url || cover;
  }

  // 2) Page HTML + hydration
  let html = "";
  try {
    html = await fetchHtml(finalUrl);
  } catch (e) {
    if (!oembed) throw e;
  }

  if (html) {
    add(extractUrls(html), "page html");
    const data = extractHydration(html);
    if (data) {
      const d = digDetail(data);
      description = d.description || description;
      author = d.author || author;
      authorAvatar = d.author_avatar || authorAvatar;
      cover = d.cover || cover;
      playUrl = d.play_url || playUrl;
      stats = d.stats || stats;
      bio = d.bio || bio;
      if (d.comments_text.length) commentsBlob = d.comments_text.join("\n");

      const bag = [];
      walkStrings(data, bag);
      add(extractUrls(bag.join("\n")), "embedded data");
    }
  }

  // 3–5) caption, bio, comments
  add(extractUrls(description), "video caption / description");
  add(extractUrls(bio), "account bio");
  add(extractUrls(commentsBlob), "comments");

  return {
    ok: true,
    presetLinks,
    author: author || "unknown",
    authorDetail: authorAvatar ? { avatar: authorAvatar } : {},
    video: {
      description: description || "",
      cover: cover || "",
      playUrl: playUrl || "",
      playUrlNoWm: playUrl || "",
      width: 576,
      height: 1024,
      stats: {
        views: stats.views ?? null,
        likes: stats.likes ?? null,
        comments: stats.comments ?? null,
      },
    },
    sourceUrl: finalUrl,
    videoId,
  };
}

function sse(event, data) {
  const payload = typeof data === "string" ? data : JSON.stringify(data);
  return `event: ${event}\ndata: ${payload}\n\n`;
}

module.exports = async function handler(req, res) {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    res.statusCode = 204;
    res.end();
    return;
  }

  if (req.method !== "GET") {
    res.statusCode = 405;
    res.end("Method Not Allowed");
    return;
  }

  const url = (req.query?.url || "").toString().trim();
  if (!url || url.length < 8) {
    res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
    res.setHeader("Cache-Control", "no-cache, no-transform");
    res.setHeader("Connection", "keep-alive");
    res.write(sse("result", { ok: false, error: "Missing url parameter" }));
    res.end();
    return;
  }

  res.setHeader("Content-Type", "text/event-stream; charset=utf-8");
  res.setHeader("Cache-Control", "no-cache, no-transform");
  res.setHeader("Connection", "keep-alive");
  res.setHeader("X-Accel-Buffering", "no");

  // Vercel needs flush-friendly streaming
  if (typeof res.flushHeaders === "function") res.flushHeaders();

  try {
    if (!/tiktok\.com|vt\.tiktok|vm\.tiktok/i.test(url)) {
      res.write(
        sse("result", { ok: false, error: "Please paste a valid TikTok video link" })
      );
      res.end();
      return;
    }

    const result = await scrapeTikTok(url);
    res.write(sse("result", result));
  } catch (e) {
    const msg = (e && e.message ? e.message : "scraper failed").slice(0, 180);
    res.write(sse("result", { ok: false, error: msg }));
  }

  res.end();
};
