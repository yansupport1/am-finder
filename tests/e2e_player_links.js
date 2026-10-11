const puppeteer = require('/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const VID = '7676471469072796935';
(async () => {
  const b = await puppeteer.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', headless: 'new', args: ['--no-sandbox'] });
  const p = await b.newPage(); await p.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  const finds = [];
  await p.setRequestInterception(true);
  p.on('request', async r => {
    const u = r.url();
    if (u.includes('/api/find')) {
      finds.push(decodeURIComponent(u.split('url=')[1] || '').split('&')[0]);
      await sleep(1500);
      const data = { ok: true, presetLinks: [{ url: 'https://alight.link/AbC123', kind: 'Alight Motion', source: 'comments' }], author: '@zeereal85', authorDetail: {}, video: { id: VID, description: 'preset negoro angin', cover: '', playUrl: '', playUrlNoWm: '', width: 576, height: 1024, stats: { views: 10, likes: 2, comments: 1 } }, sourceUrl: 'https://www.tiktok.com/@zeereal85/video/' + VID, videoId: VID };
      return r.respond({ status: 200, contentType: 'text/event-stream', body: 'event: result\ndata: ' + JSON.stringify(data) + '\n\n' });
    }
    if (u.startsWith('http://127.0.0.1:8765')) return r.continue();
    return r.respond({ status: 200, contentType: 'text/html', body: '<html><body style="background:#111;color:#5eead4;font:14px sans-serif;display:grid;place-items:center;height:100vh">TikTok embed (stub)</body></html>' });
  });
  await p.goto('http://127.0.0.1:8765/index.html', { waitUntil: 'domcontentloaded' });
  await sleep(4500);
  await p.addStyleTag({ content: '#yanzJoinModal, .yanz-toast-wrap { display: none !important; }' });
  await p.screenshot({ path: '/tmp/e2e/30_tiles.png' });
  // C: tile premium
  await p.click('#yanzFeaturePremium'); await sleep(700);
  console.log('PREMIUM', JSON.stringify(await p.evaluate(() => ({ shown: document.getElementById('yanzSoonModal').classList.contains('is-show'), text: document.getElementById('yanzSoonModal').innerText.replace(/\s+/g, ' ') }))));
  await p.screenshot({ path: '/tmp/e2e/31_premium.png' });
  await p.click('#yanzSoonClose'); await sleep(400);
  await p.click('#yanzFeatureTrace'); await sleep(500);
  console.log('TRACE title:', await p.evaluate(() => document.getElementById('yanzSoonTitle').textContent));
  await p.click('#yanzSoonClose'); await sleep(400);
  // B: tempel link vt.tiktok.com di kotak kata kunci
  await p.click('#yanzTabCaption'); await sleep(400);
  await p.type('#yanzCaptionInput', 'https://vt.tiktok.com/ZSb3Nafsc/');
  await p.click('#yanzCaptionBtn'); await sleep(4500);
  console.log('FIND_CALLS', JSON.stringify(finds));
  console.log('TT_INPUT', await p.evaluate(() => document.getElementById('tt').value), '| mode:', await p.evaluate(() => (document.body.className.match(/mode-\w+/) || [])[0]));
  await sleep(3500);
  // A: pemutar harus ada walau playUrl & cover kosong
  console.log('PLAYER', JSON.stringify(await p.evaluate(() => ({ previewEl: !!document.querySelector('.video-preview'), embed: (document.querySelector('.content-head .yz-embed iframe') || {}).src || null, openLink: (document.querySelector('.yz-embed-link') || {}).href || null, links: [...document.querySelectorAll('main a[href]')].map(a => a.href).filter(h => /alight/.test(h)) }))));
  await p.evaluate(() => document.querySelector('.video-meta').scrollIntoView({ block: 'start' })); await sleep(500);
  await p.screenshot({ path: '/tmp/e2e/32_player.png' });
  await b.close(); process.exit(0);
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
