const puppeteer = require('/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const LONG = 'https://www.tiktok.com/@burzpejeje/video/7591289001781349650';
const VT = 'https://vt.tiktok.com/ZSb3Nafsc/';
const YT = 'https://www.youtube.com/watch?v=dQw4w9WgXcQ';
(async () => {
  const b = await puppeteer.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', headless: 'new', args: ['--no-sandbox'] });
  const ctx = b.defaultBrowserContext(); await ctx.overridePermissions('http://127.0.0.1:8765', ['clipboard-read', 'clipboard-write']).catch(() => {});
  const p = await b.newPage(); await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  const finds = [], searches = []; const R = {};
  const C = async (sel, o) => { try { await p.click(sel, o); } catch (e) { console.log('CLICK FAIL', sel, JSON.stringify(R).slice(0, 600)); await p.screenshot({ path: '/tmp/e2e/fail.png' }); throw e; } };
  await p.setRequestInterception(true);
  p.on('request', async r => {
    const u = r.url();
    if (u.includes('/api/trending')) {
      const items = Array.from({ length: 20 }, (_, i) => ({ rank: i + 1, title: 'Song ' + (i + 1), artist: 'Artist ' + (i + 1), cover: '', url: '' }));
      return r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, source: 'Deezer Global Chart', count: 20, items }) });
    }
    if (u.includes('/api/search')) {
      searches.push(decodeURIComponent(new URL(u).searchParams.get('q')));
      return r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, status: 'ok', results: [{ id: '7591289001781349650', videoUrl: LONG, author: 'burzpejeje', description: 'preset', cover: '', stats: {}, presetLinks: [] }] }) });
    }
    if (u.includes('/api/find')) {
      const raw = decodeURIComponent(u.split('url=')[1] || '').split('&')[0]; finds.push(raw); await sleep(900);
      const yt = /youtube/.test(raw);
      const d = yt ? { ok: true, platform: 'YouTube', presetLinks: [{ url: 'https://alight.link/YtPre1', type: 'xml', title: 'XML preset', detail: 'Found in video caption', byAuthor: true }], author: '@Zee Ch', authorDetail: {}, video: { id: 'dQw4w9WgXcQ', description: 'tutorial', cover: '', playUrl: '', playUrlNoWm: '', embedUrl: 'https://www.youtube.com/embed/dQw4w9WgXcQ', width: 576, height: 1024, stats: {} }, sourceUrl: raw, videoId: 'dQw4w9WgXcQ' }
        : { ok: true, platform: 'TikTok', presetLinks: [{ url: 'https://alight.link/AbC123', type: 'xml', title: 'XML preset', detail: 'Found in comments', byAuthor: false }], author: '@burzpejeje', authorDetail: {}, video: { id: '7591289001781349650', description: 'x', cover: '', playUrl: '', playUrlNoWm: '', embedUrl: 'https://www.tiktok.com/embed/v2/7591289001781349650', width: 576, height: 1024, stats: {} }, sourceUrl: raw, videoId: '7591289001781349650' };
      return r.respond({ status: 200, contentType: 'text/event-stream', body: 'event: result\ndata: ' + JSON.stringify(d) + '\n\n' });
    }
    if (u.startsWith('http://127.0.0.1:8765')) return r.continue();
    return r.respond({ status: 200, contentType: 'text/html', body: '<html><body style="background:#111;color:#5eead4">stub</body></html>' });
  });
  await p.goto('http://127.0.0.1:8765/index.html', { waitUntil: 'domcontentloaded' });
  for (let i = 0; i < 60; i++) { await sleep(200); if (await p.evaluate(() => { const l = document.getElementById('yanzLoader'); return l && l.classList.contains('is-done'); })) break; }
  await sleep(800);
  await p.evaluate(() => { window.__opened = []; window.open = u => { window.__opened.push(u); return {}; }; });
  // 1) popup promo + Support Admin
  await p.evaluate(() => document.getElementById('yanzJoinModal').classList.add('is-open')); await sleep(500);
  R.promoSupportBtn = await p.evaluate(() => (document.querySelector('#yanzJoinModal [data-support]') || {}).textContent);
  await p.screenshot({ path: '/tmp/e2e/50_promo.png' });
  await C('#yanzJoinModal [data-support]'); await sleep(700);
  R.qrFromPromo = await p.evaluate(() => { const m = document.getElementById('yanzQrModal'), i = m.querySelector('img'); return { shown: m.classList.contains('is-show'), imgOk: i.complete && i.naturalWidth > 0, w: i.naturalWidth, text: m.innerText.replace(/\s+/g, ' ').slice(0, 80) }; });
  await p.screenshot({ path: '/tmp/e2e/51_qr.png' });
  await C('#yanzQrClose'); await sleep(400);
  await p.evaluate(() => document.getElementById('yanzJoinModal').classList.remove('is-open')); await sleep(300);
  // 2) tab + tombol fitur
  R.tabLabel = await p.evaluate(() => document.getElementById('yanzTabCaption').textContent);
  R.premiumTile = await p.evaluate(() => document.getElementById('yanzFeaturePremium').innerText.replace(/\s+/g, ' '));
  R.heights = await p.evaluate(() => ['yanzTabCaption', 'yanzFeatureTrace', 'yanzFeaturePremium'].map(id => Math.round(document.getElementById(id).getBoundingClientRect().height)));
  await p.screenshot({ path: '/tmp/e2e/52_tiles.png', clip: { x: 0, y: 0, width: 390, height: 300 } });
  await C('#yanzFeaturePremium'); await sleep(700);
  R.premiumModal = await p.evaluate(() => ({ title: document.getElementById('yanzSoonTitle').textContent, tag: document.getElementById('yanzSoonTag').textContent, desc: document.getElementById('yanzSoonDesc').innerText, join: document.getElementById('yanzSoonJoin').textContent, support: !!document.querySelector('#yanzSoonModal [data-support]') }));
  await p.screenshot({ path: '/tmp/e2e/53_premium.png' });
  await C('#yanzSoonJoin'); await sleep(200);
  R.channelOpened = await p.evaluate(() => window.__opened.slice(-1)[0]);
  await C('#yanzSoonModal [data-support]'); await sleep(600);
  R.qrFromModal = await p.evaluate(() => document.getElementById('yanzQrModal').classList.contains('is-show'));
  await C('#yanzQrClose'); await sleep(700);
  await C('#yanzSoonClose'); await sleep(700);
  await C('#yanzFeatureTrace'); await sleep(500);
  R.traceModal = await p.evaluate(() => ({ title: document.getElementById('yanzSoonTitle').textContent, tag: document.getElementById('yanzSoonTag').textContent }));
  await C('#yanzSoonClose'); await sleep(700);
  // 3) chart lagu global
  await C('#yanzTabCaption'); await sleep(1200);
  R.trend = await p.evaluate(() => ({ items: document.querySelectorAll('.yz-trend-item').length, first: (document.querySelector('.yz-trend-item .yz-trend-txt b') || {}).textContent, sub: document.getElementById('yanzTrendSub').textContent }));
  await p.screenshot({ path: '/tmp/e2e/54_trend.png' });
  await C('#yanzTrendMore'); await sleep(300);
  R.trendExpanded = await p.evaluate(() => document.querySelectorAll('.yz-trend-item').length);
  await C('.yz-trend-item'); await sleep(2800);
  R.afterTrendClick = { input: await p.evaluate(() => document.getElementById('yanzCaptionInput').value), searches: searches.slice(), cards: await p.evaluate(() => document.querySelectorAll('.yanz-result').length), trendHidden: await p.evaluate(() => getComputedStyle(document.getElementById('yanzTrend')).display === 'none') };
  await p.screenshot({ path: '/tmp/e2e/55_cards.png' });
  // 4) link pendek
  R.cardButtons = await p.evaluate(() => [...document.querySelectorAll('.yanz-result .yz-card-actions button')].map(b => b.textContent));
  await p.evaluate(v => { navigator.clipboard.readText = () => Promise.resolve(v); }, LONG);
  await C('.yz-short-btn'); await sleep(600);
  R.sheet = await p.evaluate(() => ({ shown: document.getElementById('yanzSheet').classList.contains('is-show'), opened: window.__opened.slice(-1)[0] }));
  await p.screenshot({ path: '/tmp/e2e/56_sheet.png' });
  await sleep(1800);
  R.stillWaitingWhenClipboardUnchanged = await p.evaluate(() => ({ ttEmpty: !(document.getElementById('tt') || {}).value, sheet: document.getElementById('yanzSheet').classList.contains('is-show') }));
  await p.evaluate(v => { navigator.clipboard.readText = () => Promise.resolve('Lihat video ini ' + v + ' #fyp'); }, VT);
  await p.evaluate(() => window.dispatchEvent(new Event('focus'))); await sleep(4200);
  R.afterShort = { tt: await p.evaluate(() => document.getElementById('tt').value), mode: await p.evaluate(() => (document.body.className.match(/mode-\w+/) || [])[0]), sheetClosed: await p.evaluate(() => !document.getElementById('yanzSheet').classList.contains('is-show')), finds: finds.slice() };
  await sleep(2500);
  // 5) link panjang & situs lain di tab Link Video
  for (const link of [LONG, YT, 'Lihat video seru ini https://vt.tiktok.com/ZSb3Nafsc/ #fyp #preset']) {
    await p.evaluate(() => { const i = document.getElementById('tt'); const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value'); d.set.call(i, ''); i.dispatchEvent(new Event('input', { bubbles: true })); });
    await p.type('#tt', link); await p.keyboard.press('Enter'); await sleep(3600);
  }
  R.finds = finds.slice();
  R.ytPlayer = await p.evaluate(() => ({ iframe: (document.querySelector('.content-head .yz-embed iframe') || {}).src || null, platformNote: !!document.querySelector('.yz-embed-note') }));
  await p.evaluate(() => { const i = document.getElementById('tt'); const d = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value'); d.set.call(i, ''); i.dispatchEvent(new Event('input', { bubbles: true })); });
  await p.type('#tt', 'halo dunia'); await p.keyboard.press('Enter'); await sleep(1800);
  R.badInput = await p.evaluate(() => ({ found: /does not look like a link/i.test(document.body.innerText), errEls: [...document.querySelectorAll('[role=alert], .error, .search-error, [class*=error]')].map(e => (e.className || e.tagName) + ': ' + e.innerText.slice(0, 90)) }));
  await p.screenshot({ path: '/tmp/e2e/57_bad.png' });
  console.log(JSON.stringify(R, null, 1));
  await b.close(); process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
