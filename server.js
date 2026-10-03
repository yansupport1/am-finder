/**
 * Local Node server (optional) — same API as Vercel
 * Run: node server.js
 * Open: http://127.0.0.1:8787
 */
const http = require("http");
const fs = require("fs");
const path = require("path");
const { URL } = require("url");

const findHandler = require("./api/find.js");
const trackHandler = require("./api/track.js");

const ROOT = __dirname;
const PORT = process.env.PORT || 8787;

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".svg": "image/svg+xml",
  ".ttf": "font/ttf",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".json": "application/json",
};

function sendFile(res, filePath) {
  const ext = path.extname(filePath).toLowerCase();
  res.writeHead(200, { "Content-Type": MIME[ext] || "application/octet-stream" });
  fs.createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, `http://${req.headers.host}`);

  // API
  if (u.pathname === "/api/find") {
    req.query = Object.fromEntries(u.searchParams);
    return findHandler(req, res);
  }
  if (u.pathname === "/api/track") {
    return trackHandler(req, res);
  }

  // Static
  let rel = decodeURIComponent(u.pathname);
  if (rel === "/") rel = "/index.html";
  const filePath = path.join(ROOT, rel);

  if (!filePath.startsWith(ROOT) || !fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    res.writeHead(404).end("Not found");
    return;
  }
  sendFile(res, filePath);
});

server.listen(PORT, () => {
  console.log("AM Preset Finder by Yanz");
  console.log("Open http://127.0.0.1:" + PORT);
});
