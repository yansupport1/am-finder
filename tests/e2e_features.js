const puppeteer = require('/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer');
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await puppeteer.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', headless: 'new', args: ['--no-sandbox'] });
  const p = await b.newPage(); await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  let calls = 0;
  await p.setRequestInterception(true);
  p.on('request', r => { if (r.url().includes('/api/search')) { calls++; return r.respond({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, status: 'ok', results: [{ id: '7312345678901234567', videoUrl: 'https://www.tiktok.com/@a/video/7312345678901234567', author: 'a', description: 'x', cover: '', stats: {}, presetLinks: [] }] }) }); } r.continue(); });
  const t0 = Date.now(); const samples = [];
  await p.goto('http://127.0.0.1:8765/index.html', { waitUntil: 'domcontentloaded' });
  let shot = false, doneAt = null;
  for (let i = 0; i < 40; i++) {
    await sleep(200);
    const st = await p.evaluate(() => { const l = document.getElementById('yanzLoader'); return { pct: (document.getElementById('yanzLoaderPct') || {}).textContent, done: l ? l.classList.contains('is-done') : null, bar: (document.getElementById('yanzLoaderBar') || {style:{}}).style.transform, tip: (document.getElementById('yanzLoaderTip') || {}).textContent }; });
    samples.push((Date.now() - t0) + 'ms ' + st.pct + ' ' + st.bar + ' | ' + st.tip + (st.done ? ' DONE' : ''));
    if (!shot && parseInt(st.pct) >= 40) { shot = true; await p.screenshot({ path: '/tmp/e2e/10_loader.png' }); }
    if (st.done) { doneAt = Date.now() - t0; break; }
  }
  console.log(samples.filter((_, i) => i % 3 === 0 || /DONE/.test(_)).join('\n'));
  console.log('loader done at', doneAt, 'ms');
  await sleep(800);
  await p.addStyleTag({ content: '#yanzJoinModal, .yanz-toast-wrap { display: none !important; }' }); await sleep(600);
  await p.screenshot({ path: '/tmp/e2e/11_home.png' });
  // fitur Auto Trace Vektor
  await p.evaluate(() => { window.__opened = []; window.open = (u) => { window.__opened.push(u); return null; }; });
  await p.click('#yanzFeatureTrace'); await sleep(700);
  const m = await p.evaluate(() => ({ shown: document.getElementById('yanzSoonModal').classList.contains('is-show'), text: document.getElementById('yanzSoonModal').innerText.replace(/\s+/g, ' ') }));
  console.log('MODAL', JSON.stringify(m));
  await p.screenshot({ path: '/tmp/e2e/12_soon.png' });
  await p.click('#yanzSoonJoin'); await sleep(200);
  console.log('OPENED', JSON.stringify(await p.evaluate(() => window.__opened)));
  await p.click('#yanzSoonClose'); await sleep(500);
  console.log('CLOSED', await p.evaluate(() => !document.getElementById('yanzSoonModal').classList.contains('is-show')));
  // cache perangkat: cari kata sama 2x -> server hanya dipanggil 1x
  await p.click('#yanzTabCaption'); await sleep(400);
  for (let i = 0; i < 2; i++) { await p.click('#yanzCaptionInput', { clickCount: 3 }); await p.type('#yanzCaptionInput', 'kata sama'); await p.click('#yanzCaptionBtn'); await p.waitForSelector('.yanz-result', { timeout: 8000 }); await sleep(500); }
  console.log('SEARCH_API_CALLS_FOR_2_SEARCHES', calls);
  await b.close();
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
