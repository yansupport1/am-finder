const puppeteer = require('/home/claude/.npm-global/lib/node_modules/@mermaid-js/mermaid-cli/node_modules/puppeteer');
const VID1='7312345678901234567', VID2='7312345678901234568';
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome', headless: 'new', args: ['--no-sandbox','--disable-setuid-sandbox'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  const ctx = browser.defaultBrowserContext();
  await ctx.overridePermissions('http://127.0.0.1:8765', ['clipboard-read','clipboard-write']).catch(()=>{});
  const log = []; let searchCalls = 0, findCalls = [], videoMode = process.env.VMODE || 'bad';
  page.on('console', m => { const t = m.text(); if (/yanz|error/i.test(t)) log.push('console: ' + t.slice(0,160)); });
  page.on('pageerror', e => log.push('PAGEERROR: ' + String(e).slice(0,200)));
  await page.setRequestInterception(true);
  page.on('request', async req => {
    const u = req.url();
    if (u.includes('/api/search')) {
      searchCalls++;
      const q = new URL(u).searchParams.get('q');
      await sleep(searchCalls === 1 ? 3500 : 300);
      let body;
      if (q === 'blokir') body = { ok:true, query:q, count:0, results:[], status:'blocked', blocked:true, message:'Pencarian otomatis sedang dibatasi (TEST). Buka tab "Cari via Link Video".' };
      else body = { ok:true, query:q, count:2, status:'ok', results:[
        { id:VID1, videoUrl:`https://www.tiktok.com/@zed/video/${VID1}`, author:'zed', description:'Negoro angin preset '+q, cover:'', playUrl:'', presetLinks:[], stats:{viewsText:'1.2K'} },
        { id:VID2, videoUrl:`https://www.tiktok.com/@amy/video/${VID2}`, author:'amy', description:'cap2', cover:'', playUrl:'', presetLinks:[], stats:{} } ] };
      return req.respond({ status:200, contentType:'application/json', headers:{'access-control-allow-origin':'*'}, body: JSON.stringify(body) });
    }
    if (u.includes('/api/find')) {
      findCalls.push(decodeURIComponent(u.split('url=')[1]||''));
      await sleep(3500);
      const data = { ok:true, presetLinks:[{url:'https://alight.link/AbC123', kind:'Alight Motion', source:'comments'}], author:'@zed', authorDetail:{}, video:{ id:VID1, description:'Negoro angin', cover:'', playUrl:'/api/video?url=x', playUrlNoWm:'/api/video?url=x', width:576, height:1024, stats:{views:10,likes:2,comments:1} }, sourceUrl:`https://www.tiktok.com/@zed/video/${VID1}`, videoId:VID1 };
      return req.respond({ status:200, contentType:'text/event-stream', headers:{'access-control-allow-origin':'*','cache-control':'no-cache'}, body:`event: result\ndata: ${JSON.stringify(data)}\n\n` });
    }
    if (u.includes('/api/video')) {
      if (videoMode === 'bad') return req.respond({ status:502, contentType:'text/plain', body:'proxy error' });
      return req.respond({ status:206, contentType:'video/mp4', headers:{'content-range':'bytes 0-1/100','accept-ranges':'bytes'}, body:Buffer.from([0,0]) });
    }
    if (u.startsWith('http://127.0.0.1:8765')) return req.continue();
    return req.respond({ status:200, contentType:'text/html', body:'<html><body style="background:#111;color:#5eead4">embed stub</body></html>' });
  });

  await page.goto('http://127.0.0.1:8765/index.html', { waitUntil: 'load' });
  await page.waitForSelector('#yanzRoot'); await sleep(6800);
  await page.evaluate(()=>{const b=document.getElementById('yanzSkipBtn'); if(b) b.click();}); await sleep(700);
  await page.screenshot({ path: '/tmp/e2e/01_home.png' });
  const R = {};
  // --- search #1 (lambat, untuk lihat animasi scroll TikTok)
  await page.click('#yanzTabCaption'); await sleep(600);
  await page.type('#yanzCaptionInput', 'negoro angin');
  await page.click('#yanzCaptionBtn'); await sleep(1500);
  R.scrollSceneVisible = await page.evaluate(() => document.getElementById('yanzSearching').classList.contains('is-show') && !!document.querySelector('.yz-phone') && document.getElementById('yanzSearchingText').textContent);
  await page.screenshot({ path: '/tmp/e2e/02_scroll_tiktok.png' });
  await page.waitForSelector('.yanz-result', { timeout: 8000 });
  R.search1 = await page.$$eval('.yanz-result', e => e.length);
  // --- search #2 & #3 (inti masalah: harus bisa berulang)
  for (const q of ['lagu dua', 'blokir', 'lagu tiga']) {
    await page.click('#yanzCaptionInput', { clickCount: 3 }); await page.type('#yanzCaptionInput', q);
    await page.click('#yanzCaptionBtn'); await sleep(2200);
    R['search_'+q] = await page.evaluate(() => ({ cards: document.querySelectorAll('.yanz-result').length, empty: (document.querySelector('.yanz-empty')||{}).textContent || '', btnDisabled: document.getElementById('yanzCaptionBtn').disabled, spinner: document.getElementById('yanzSearching').classList.contains('is-show'), goTab: !!document.getElementById('yanzGoUrlTab') }));
  }
  R.searchCalls = searchCalls;
  // --- klik video -> salin, pindah tab, tempel, cari
  await page.screenshot({ path: '/tmp/e2e/03_results.png' });
  console.log('PRE-PICK', JSON.stringify(R)); await page.screenshot({path:'/tmp/e2e/03b_prepick.png'}); await page.click('.yanz-result .yanz-pick-btn');
  await sleep(1800);
  R.afterPick = await page.evaluate(async () => ({ mode: document.body.className.match(/mode-\w+/g), tt: (document.getElementById('tt')||{}).value, toast: !!document.querySelector('.yz-toast'), clip: await navigator.clipboard.readText().catch(e => 'ERR '+e.message) }));
  await sleep(1500);
  R.overlay = await page.evaluate(() => ({ shown: document.getElementById('yanzLinkOverlay').classList.contains('is-show'), text: document.getElementById('yanzLinkText').textContent, hunt: !!document.querySelector('.yz-hunt') }));
  await page.screenshot({ path: '/tmp/e2e/04_mencari_preset.png' });
  await sleep(5500);
  R.findCalls = findCalls;
  R.afterFind = await page.evaluate(() => ({ overlay: document.getElementById('yanzLinkOverlay').classList.contains('is-show'), video: !!document.querySelector('main video'), embed: (document.querySelector('.yz-embed iframe')||{}).src || null, links: [...document.querySelectorAll('main a[href]')].map(a=>a.href).filter(h=>/alight/.test(h)) }));
  await page.screenshot({ path: '/tmp/e2e/05_result.png', fullPage: false });
  console.log(JSON.stringify(R, null, 1)); console.log(log.slice(0,12).join('\n'));
  await browser.close();
})().catch(e => { console.error('E2E FAIL', e); process.exit(1); });
