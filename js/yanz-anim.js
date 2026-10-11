/* AM Preset Finder - animasi + perbaikan pemutar video (by Yanz) */
(function () {
  "use strict";
  var reduce = false;
  try { reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

  /* ---------------- util ---------------- */
  function toast(msg) {
    var old = document.querySelector(".yz-toast");
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var t = document.createElement("div");
    t.className = "yz-toast";
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 2700);
  }

  var lastConfetti = 0;
  function confetti() {
    if (reduce || Date.now() - lastConfetti < 8000) return;
    lastConfetti = Date.now();
    var box = document.createElement("div");
    box.className = "yz-confetti";
    var colors = ["#5eead4", "#38bdf8", "#fbbf24", "#fb7185", "#a78bfa"];
    for (var i = 0; i < 26; i++) {
      var p = document.createElement("i");
      var ang = (Math.PI * 2 * i) / 26, dist = 90 + Math.random() * 110;
      p.style.setProperty("--x", Math.round(Math.cos(ang) * dist) + "px");
      p.style.setProperty("--y", Math.round(Math.sin(ang) * dist - 40) + "px");
      p.style.setProperty("--r", Math.round(Math.random() * 540 - 270) + "deg");
      p.style.setProperty("--c", colors[i % colors.length]);
      box.appendChild(p);
    }
    document.body.appendChild(box);
    setTimeout(function () { if (box.parentNode) box.parentNode.removeChild(box); }, 1400);
  }

  function embedHTML(vid) {
    return '<iframe src="https://www.tiktok.com/embed/v2/' + vid + '" allow="autoplay; fullscreen; encrypted-media; picture-in-picture" ' +
      'allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin" title="Video TikTok"></iframe>';
  }

  window.YzFX = { toast: toast, confetti: confetti, embedHTML: embedHTML };

  /* ---------------- ripple ---------------- */
  document.addEventListener("pointerdown", function (e) {
    if (reduce) return;
    var b = e.target && e.target.closest && e.target.closest("main button, .yanz-chrome button, .yanz-modal-card button, .btn-primary");
    if (!b || b.disabled) return;
    var r = b.getBoundingClientRect();
    var size = Math.max(r.width, r.height) * 1.6;
    var s = document.createElement("span");
    s.className = "yz-ripple";
    s.style.width = s.style.height = size + "px";
    s.style.left = (e.clientX - r.left - size / 2) + "px";
    s.style.top = (e.clientY - r.top - size / 2) + "px";
    b.appendChild(s);
    setTimeout(function () { if (s.parentNode) s.parentNode.removeChild(s); }, 650);
  }, { passive: true });

  /* ---------------- reveal saat scroll ---------------- */
  function setupReveal() {
    var els = document.querySelectorAll(".how-list li, .how-title, .footer-top, .footer-col, .footer-bottom, .yanz-promo, .yanz-clock");
    if (!els.length) return;
    var io = null;
    if ("IntersectionObserver" in window) {
      io = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (en.isIntersecting) { en.target.classList.add("is-in"); io.unobserve(en.target); }
        });
      }, { threshold: 0.12 });
    }
    Array.prototype.forEach.call(els, function (el, i) {
      if (el.getAttribute("data-yz")) return;
      el.setAttribute("data-yz", "1");
      el.classList.add("yz-reveal");
      el.style.transitionDelay = ((i % 4) * 70) + "ms";
      if (io) io.observe(el); else el.classList.add("is-in");
    });
  }

  /* ---------------- video: fallback ke pemutar embed resmi TikTok ---------------- */
  function currentVideoId() {
    if (window.__yanzVid) return String(window.__yanzVid);
    var cands = [window.__yanzLastUrl || ""];
    var tt = document.getElementById("tt");
    if (tt) cands.push(tt.value || "");
    for (var i = 0; i < cands.length; i++) {
      var m = String(cands[i]).match(/video\/(\d+)/);
      if (m) return m[1];
    }
    return "";
  }

  function useEmbed(v, why) {
    if (!v || v.getAttribute("data-yz-embed")) return;
    v.setAttribute("data-yz-embed", "1");
    var vid = currentVideoId();
    var host = document.createElement("div");
    if (vid) {
      host.className = "yz-embed";
      host.innerHTML = embedHTML(vid);
    } else {
      host.className = "yz-embed-note";
      var link = (window.__yanzLastUrl || (document.getElementById("tt") || {}).value || "https://www.tiktok.com/");
      host.innerHTML = 'Video tidak bisa diputar di sini. <a href="' + String(link).replace(/"/g, "") + '" target="_blank" rel="noopener" style="color:#5eead4">Buka di TikTok</a>';
    }
    var wrap = v.parentElement;
    if (wrap && !wrap.classList.contains("content-head") && wrap.tagName !== "MAIN") {
      wrap.style.display = "none";
      wrap.parentNode.insertBefore(host, wrap.nextSibling);
    } else {
      v.style.display = "none";
      v.parentNode.insertBefore(host, v);
    }
    try { console.info("[yanz] embed fallback:", why); } catch (e) {}
  }

  function watchVideo(v) {
    if (!v || v.getAttribute("data-yz-watch")) return;
    v.setAttribute("data-yz-watch", "1");
    v.addEventListener("error", function () { useEmbed(v, "video error"); });
    var checks = 0;
    (function probe() {
      checks++;
      var src = v.currentSrc || v.getAttribute("src") || "";
      if (!src) {
        var s = v.querySelector("source");
        src = s ? (s.getAttribute("src") || "") : "";
      }
      if (!src) {
        if (checks < 6) return setTimeout(probe, 400);
        return useEmbed(v, "no src");
      }
      // cek cepat apakah proxy/CDN benar-benar mengirim video
      try {
        fetch(src, { headers: { Range: "bytes=0-1" }, cache: "no-store" }).then(function (r) {
          var ct = (r.headers.get("Content-Type") || "").toLowerCase();
          if (!(r.status === 200 || r.status === 206) || (ct && ct.indexOf("video") < 0 && ct.indexOf("octet") < 0)) {
            useEmbed(v, "probe status " + r.status);
          }
        }).catch(function () { useEmbed(v, "probe failed"); });
      } catch (e) {}
    })();
    v.addEventListener("play", function () {
      setTimeout(function () {
        if (v.readyState < 3 && v.paused === false) useEmbed(v, "stalled");
      }, 9000);
    });
  }

  /* ---------------- animasi untuk elemen yang dirender dinamis ---------------- */
  var PRESET_HOST = /alight\.link|alightcreative\.com|alightmotion\.com|am\.link|drive\.google\.com|mediafire\.com|mega\.nz/i;

  function onAdded(nodes) {
    var n = 0;
    nodes.forEach(function (node) {
      if (node.nodeType !== 1) return;
      if (node.closest && node.closest("#yanzRoot, .yz-toast, .yz-confetti, .yz-embed")) return;
      var depth = 0, p = node;
      while (p && p.tagName !== "MAIN") { p = p.parentElement; depth++; if (depth > 6) break; }
      if (p && depth <= 4 && !node.classList.contains("yz-enter") && !/^(SPAN|SVG|PATH|I|B|BR|IMG)$/i.test(node.tagName)) {
        node.classList.add("yz-enter");
        if (!reduce) node.style.animationDelay = (Math.min(n, 8) * 60) + "ms";
        n++;
      }
      var vids = node.tagName === "VIDEO" ? [node] : (node.querySelectorAll ? node.querySelectorAll("video") : []);
      Array.prototype.forEach.call(vids, watchVideo);
      var as = node.tagName === "A" ? [node] : (node.querySelectorAll ? node.querySelectorAll("a[href]") : []);
      for (var i = 0; i < as.length; i++) {
        if (PRESET_HOST.test(as[i].getAttribute("href") || "")) { confetti(); break; }
      }
    });
  }

  function observeMain() {
    var main = document.querySelector("main");
    if (!main || main.getAttribute("data-yz-obs")) return;
    main.setAttribute("data-yz-obs", "1");
    if (!("MutationObserver" in window)) return;
    new MutationObserver(function (muts) {
      var added = [];
      muts.forEach(function (m) { Array.prototype.forEach.call(m.addedNodes, function (x) { added.push(x); }); });
      if (added.length) onAdded(added);
    }).observe(main, { childList: true, subtree: true });
    Array.prototype.forEach.call(main.querySelectorAll("video"), watchVideo);
  }

  function boot() { setupReveal(); observeMain(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
  setTimeout(boot, 500); setTimeout(boot, 1500); setTimeout(boot, 3500);
})();
