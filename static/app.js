/* 独角兽量化 前端：Lightweight Charts 画图，数据/指标/复盘全部来自 Python 后端 */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const TZ = -new Date().getTimezoneOffset() * 60;
const sec = ms => Math.floor(ms / 1000) + TZ;
const PALETTE = ['#F5B83D', '#007AFF', '#4C82FB', '#34C759', '#FF8A4C', '#9B7BFF', '#2FD6C8', '#FF3B30'];
const COMMON = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'BNBUSDT', 'ZECUSDT', 'XRPUSDT', 'DOGEUSDT', 'SNDKUSDT'];
const INDS = [
  { k: 'ema', n: 'EMA', d: '指数均线', p: '7' }, { k: 'ma', n: 'MA', d: '简单均线', p: '20' },
  { k: 'boll', n: 'BOLL', d: '布林带', p: '20:2' }, { k: 'st', n: 'Supertrend', d: '超级趋势', p: '10:3' },
  { k: 'dc', n: 'Donchian', d: '唐奇安通道', p: '20' }, { k: 'vwap', n: 'VWAP', d: '日内VWAP', p: '' },
  { k: 'rsi', n: 'RSI', d: '相对强弱(副图)', p: '14' }, { k: 'macd', n: 'MACD', d: '指数平滑异同(副图)', p: '12:26:9' },
  { k: 'atr', n: 'ATR', d: '真实波幅(副图)', p: '14' },
];
const S = {
  symbol: localStorage.uq_sym || 'BTCUSDT', interval: localStorage.uq_itv || '4h', inds: JSON.parse(localStorage.uq_inds || '["ema:7","ema:25"]'),
  sub: null, bars: [], indData: [], side: 'LONG', mode: 'paper', tool: '', theme: localStorage.uq_theme || 'dark',
  strategies: [], bt: null, paper: null, symbols: [], tickers: {}, authed: false, viewer: false, rec: 'pp', lastPx: 0, drawings: [],
};
// 2026-10-17 网络容错：跨境网络偶尔断开(ERR_EMPTY_RESPONSE)，自动重试3次，仍失败给出"重新加载"提示
const netRetry = async (path, body) => {
  let err;
  for (let k = 0; k < 4; k++) {
    try { return await fetch('/api/' + path, body === undefined ? { cache: 'no-store' } : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }); }
    catch (e) { err = e; await new Promise(r => setTimeout(r, 600 * (k + 1))); }
  }
  if (!document.querySelector('.netbar')) { const d = document.createElement('div'); d.className = 'netbar'; d.innerHTML = '网络连接不稳定，部分数据没加载出来 <button onclick="location.reload()">重新加载</button>'; document.body.append(d); }
  throw err;
};
const api = async (path, body) => {
  const r = await netRetry(path, body);
  let j = {}; try { j = await r.json(); } catch (e) { }
  if (r.status === 401 && path !== 'login') { setAuth(false); }
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
};
const toast = (m, t = '') => { const d = document.createElement('div'); d.className = 'toast ' + t; d.textContent = m; $('#toasts').append(d); setTimeout(() => d.remove(), 4200); };
const fmt = (v, d) => { if (v == null || isNaN(v)) return '--'; d = d ?? (Math.abs(v) >= 1000 ? 2 : Math.abs(v) >= 1 ? 3 : 5); return (+v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d }); };
const pct = v => (v >= 0 ? '+' : '') + fmt(v, 2) + '%';
const cls = v => v >= 0 ? 'up' : 'dn';
const tstr = ms => { const d = new Date(ms); return `${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`; };
const base = s => s.replace(/USDT$/, '');
const isStock = s => !/USDT$/.test(s || '');
const STK_ITV = ['1h', '1d', '1w'];

/* ---------- 主题 ---------- */
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
function chartTheme() {
  const dark = S.theme === 'dark';
  return {
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: dark ? '#9B9B9B' : '#7D7D7D', fontFamily: 'Inter, sans-serif' },
    grid: { vertLines: { color: dark ? 'rgba(255,255,255,.035)' : 'rgba(0,0,0,.05)' }, horzLines: { color: dark ? 'rgba(255,255,255,.05)' : 'rgba(0,0,0,.06)' } },
    rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 6 },
    crosshair: { mode: 0, vertLine: { color: 'rgba(0,122,255,.5)', labelBackgroundColor: '#007AFF' }, horzLine: { color: 'rgba(0,122,255,.5)', labelBackgroundColor: '#007AFF' } },
  };
}
function setTheme(t) {
  S.theme = t; document.documentElement.dataset.theme = t; localStorage.uq_theme = t;
  if (main) { main.applyOptions(chartTheme()); sub && sub.applyOptions(chartTheme()); }
  drawAll();
}

/* ---------- 图表 ---------- */
let main, sub, candle, vol, overlays = {}, subSeries = [], busy = false;
function initCharts() {
  main = LightweightCharts.createChart($('#chartMain'), { ...chartTheme(), autoSize: true });
  candle = main.addCandlestickSeries({ upColor: '#34C759', downColor: '#FF3B30', borderVisible: false, wickUpColor: '#34C759', wickDownColor: '#FF3B30', priceLineColor: '#007AFF' });
  vol = main.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
  main.priceScale('vol').applyOptions({ scaleMargins: { top: .82, bottom: 0 } });
  main.priceScale('right').applyOptions({ scaleMargins: { top: .08, bottom: .2 } });
  main.timeScale().subscribeVisibleLogicalRangeChange(r => { if (sub && r) sub.timeScale().setVisibleLogicalRange(r); drawAll(); });
  main.subscribeCrosshairMove(p => legendMove(p));
  new ResizeObserver(() => { sizeCanvas(); drawAll(); }).observe($('.chart-wrap'));
  // 2026-10-18 价格轴自动缩放/全屏/上下拖动不会触发上面的事件，叠加层会用旧坐标把标签挤到边上：坐标一变就重绘
  let _ovSig = '';
  const _ovWatch = () => {
    try {
      const m = $('#chartMain'), r = main.timeScale().getVisibleLogicalRange(), last = S.bars[S.bars.length - 1];
      const p = last ? candle.priceToCoordinate(last.c) : 0, q = last ? candle.priceToCoordinate(last.c * 1.1) : 0;
      const sig = [m.clientWidth, m.clientHeight, r && r.from.toFixed(2), r && r.to.toFixed(2), p && p.toFixed(1), q && q.toFixed(1)].join('|');
      if (sig !== _ovSig) { _ovSig = sig; if (cv.width !== Math.round(m.clientWidth * (devicePixelRatio || 1))) sizeCanvas(); drawAll(); }
    } catch (e) { }
    requestAnimationFrame(_ovWatch);
  };
  requestAnimationFrame(_ovWatch);
}
function mkSub() {
  if (sub) { sub.remove(); sub = null; subSeries = []; }
  $('#chartSub').innerHTML = '';
  if (!S.sub) return;
  sub = LightweightCharts.createChart($('#chartSub'), { ...chartTheme(), autoSize: true, timeScale: { ...chartTheme().timeScale, visible: false } });
  sub.timeScale().subscribeVisibleLogicalRangeChange(r => { if (r) main.timeScale().setVisibleLogicalRange(r); });
}
const toLine = arr => arr.map(([t, v]) => ({ time: sec(t), value: v }));
function colorFor(spec) { const i = S.inds.indexOf(spec); return PALETTE[(i < 0 ? 0 : i) % PALETTE.length]; }

async function loadChart(keepRange) {
  if (busy) return; busy = true; $('#skel').classList.remove('off');
  try {
    const subSpec = S.inds.find(s => /^(rsi|macd|atr)/.test(s));
    S.sub = S.inds.includes(S.sub) ? S.sub : subSpec || null;
    const r = await api(`chart?symbol=${S.symbol}&interval=${S.interval}&limit=900&ind=${encodeURIComponent(S.inds.join(','))}`);
    S.bars = r.bars; S.indData = r.indicators;
    candle.setData(r.bars.map(b => ({ time: sec(b.t), open: b.o, high: b.h, low: b.l, close: b.c })));
    vol.setData(r.bars.map(b => ({ time: sec(b.t), value: b.v, color: b.c >= b.o ? 'rgba(64,182,107,.35)' : 'rgba(255,95,82,.35)' })));
    Object.values(overlays).flat().forEach(s => main.removeSeries(s)); overlays = {};
    mkSub();
    for (const ind of r.indicators) {
      const col = colorFor(ind.id);
      if (ind.pane === 'main') {
        const list = [];
        if (ind.dirs) {
          const up = candleLineSeg(ind, 1), dn = candleLineSeg(ind, -1);
          [[up, '#34C759'], [dn, '#FF3B30']].forEach(([d, c]) => { const s = main.addLineSeries({ color: c, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }); s.setData(d); list.push(s); });
        } else {
          const keys = Object.keys(ind.lines);
          keys.forEach((k, i) => {
            const s = main.addLineSeries({ color: ind.id.startsWith('boll') ? (k === 'mid' ? '#F5B83D' : '#4C82FB') : col, lineWidth: 1.6, lineStyle: ind.id.startsWith('boll') && k !== 'mid' ? 0 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
            s.setData(toLine(ind.lines[k])); list.push(s);
          });
        }
        overlays[ind.id] = list;
      } else if (sub && ind.id === S.sub) {
        if (ind.hist) { const h = sub.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false }); h.setData(ind.hist.map(([t, v]) => ({ time: sec(t), value: v, color: v >= 0 ? 'rgba(64,182,107,.6)' : 'rgba(255,95,82,.6)' }))); subSeries.push(h); }
        Object.keys(ind.lines).forEach((k, i) => { const s = sub.addLineSeries({ color: PALETTE[i % 2 ? 1 : 0], lineWidth: 1.6, priceLineVisible: false }); s.setData(toLine(ind.lines[k])); subSeries.push(s); });
        if (ind.id.startsWith('rsi')) { const s0 = subSeries[0]; [30, 70].forEach(v => s0.createPriceLine({ price: v, color: 'rgba(255,255,255,.2)', lineStyle: 2, lineWidth: 1, axisLabelVisible: false })); }
      }
    }
    if (!keepRange) { const n = r.bars.length; main.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - 130), to: n + 6 }); }
    if (sub) { const lr = main.timeScale().getVisibleLogicalRange(); lr && sub.timeScale().setVisibleLogicalRange(lr); }
    applyMarkers(); renderChips(); updateTop(); drawAll();
  } catch (e) { toast(e.message, 'err'); }
  finally { busy = false; $('#skel').classList.add('off'); }
}
function candleLineSeg(ind, dir) {
  const line = Object.values(ind.lines)[0], m = new Map(ind.dirs);
  return line.filter(([t]) => m.get(t) === dir).map(([t, v]) => ({ time: sec(t), value: v }));
}
function autoSignals() { if (S.authed || S.viewer) runBacktest(true); }
function setSig(on) { S.sig = on; localStorage.uq_sig = on ? '1' : '0'; $('#sigBtn').textContent = '策略信号 ' + (on ? '开' : '关'); $('#sigBtn').classList.toggle('act', on); drawAll(); }
function applyMarkers() { candle.setMarkers([]); drawAll(); }

/* 进出场标记：canvas绘制 —— 带价格的标签、进出场连线(盈绿亏红)、硬止损线 */
function drawTrades(W) {
  if (!S.sig || !S.bt || S.bt.symbol !== S.symbol || S.bt.interval !== S.interval || !S.bars.length) return;
  const bmap = new Map(S.bars.map(b => [b.t, b])), ts = main.timeScale(), H = $('#chartMain').clientHeight - 30;
  const X = t => ts.timeToCoordinate(sec(t)), Y = p => candle.priceToCoordinate(p), used = {};
  const GREEN = '#34C759', RED = '#FF3B30', PINK = '#5856D6', ORANGE = '#FF9F1A';
  const pill = (x, y, label, price, color, up, key) => {
    if (x == null || y == null || x < 0 || x > W || y < 0 || y > H) return;   // 屏幕外的标签不画(以前会被挤到边上)
    const n = used[key] = (used[key] || 0) + 1, off = (n - 1) * 27;
    cx.save(); cx.font = '600 12px Inter, sans-serif';
    const w1 = cx.measureText(label).width; cx.font = '500 11px Inter, sans-serif';
    const t2 = fmt(price), w = w1 + cx.measureText(t2).width + 24, h = 22;
    const ay = up ? y - off : y + off, px = Math.min(Math.max(x - w / 2, 2), W - w);
    let py = up ? ay - 7 - h : ay + 7; py = Math.min(Math.max(py, 2), H - h);
    cx.fillStyle = color; cx.globalAlpha = .96;
    cx.beginPath(); cx.moveTo(x, ay); cx.lineTo(x - 5, up ? ay - 7 : ay + 7); cx.lineTo(x + 5, up ? ay - 7 : ay + 7); cx.closePath(); cx.fill();
    cx.shadowColor = 'rgba(0,0,0,.4)'; cx.shadowBlur = 8; cx.beginPath(); cx.roundRect(px, py, w, h, 11); cx.fill(); cx.shadowBlur = 0;
    cx.fillStyle = '#fff'; cx.globalAlpha = 1; cx.textBaseline = 'middle'; cx.font = '600 12px Inter, sans-serif'; cx.fillText(label, px + 10, py + h / 2 + .5);
    cx.globalAlpha = .88; cx.font = '500 11px Inter, sans-serif'; cx.fillText(t2, px + 14 + w1, py + h / 2 + .5); cx.restore();
  };
  // 2026-10-17 只画可见区域：裁剪到绘图区(不含右侧价格轴)，屏幕外的标记不画，避免往左拖时挤到右边重叠
  cx.save(); cx.beginPath(); cx.rect(0, 0, W, H + 30); cx.clip();
  const last = S.bars[S.bars.length - 1];
  const items = S.bt.trades.concat(S.bt.open ? [{ ...S.bt.open, open: true, pnl: S.bt.open.float_pnl, exit_t: last.t, exit: last.c, ret: null }] : []);
  for (const t of items) {
    const long = t.side === 'LONG', pre = t.entry_t < S.bars[0].t && t.exit_t >= S.bars[0].t;
    const eb = pre ? S.bars[0] : bmap.get(t.entry_t), xb = bmap.get(t.exit_t);
    const x1 = X(pre ? S.bars[0].t : t.entry_t), x2 = X(t.exit_t); if (x1 == null || x2 == null || !eb || !xb) continue;
    if (x2 < 0 || x1 > W) continue;
    const y1 = Y(t.entry), y2 = Y(t.exit), win = t.pnl > 0, stop = t.why === '硬止损';
    if (y1 == null || y2 == null) continue;
    cx.save(); cx.lineWidth = 1.8;
    // 硬止损线
    if (t.stop != null) { const ys = Y(t.stop); if (ys != null) { cx.strokeStyle = ORANGE; cx.globalAlpha = stop ? .95 : .45; cx.setLineDash([2, 4]); cx.beginPath(); cx.moveTo(x1, ys); cx.lineTo(t.open ? W : x2, ys); cx.stroke(); if (x2 - x1 > 70 || t.open) { cx.setLineDash([]); cx.globalAlpha = .9; cx.fillStyle = ORANGE; cx.font = '600 10px Inter'; cx.textBaseline = 'bottom'; cx.fillText('硬止损 ' + fmt(t.stop), x1 + 4, ys - 2); } } }
    // 进出场连线
    cx.globalAlpha = 1; cx.setLineDash([5, 4]); cx.strokeStyle = win ? GREEN : RED; cx.beginPath(); cx.moveTo(x1, y1); cx.lineTo(x2, y2); cx.stroke(); cx.setLineDash([]);
    [[x1, y1], [x2, y2]].forEach(([x, y]) => { cx.fillStyle = win ? GREEN : RED; cx.beginPath(); cx.arc(x, y, 3.4, 0, 7); cx.fill(); cx.strokeStyle = '#fff'; cx.lineWidth = 1; cx.stroke(); });
    if (t.ret != null && x2 - x1 > 46) { const mx = (x1 + x2) / 2, my = (y1 + y2) / 2, txt = pct(t.ret); cx.font = '600 11px Inter'; const tw = cx.measureText(txt).width; cx.fillStyle = win ? GREEN : RED; cx.beginPath(); cx.roundRect(mx - tw / 2 - 6, my - 18, tw + 12, 16, 8); cx.fill(); cx.fillStyle = '#fff'; cx.textBaseline = 'middle'; cx.fillText(txt, mx - tw / 2, my - 10); }
    cx.restore();
    // 开仓标签
    pill(x1, long ? Y(eb.l) + 4 : Y(eb.h) - 4, (long ? '开多' : '开空') + (pre ? '(' + new Date(t.entry_t).toISOString().slice(5, 10) + ' 正股)' : ''), t.entry, long ? GREEN : RED, !long, t.entry_t + (long ? 'b' : 'a'));
    // 平仓标签
    if (!t.open) pill(x2, long ? Y(xb.h) - 4 : Y(xb.l) + 4, stop ? '硬止损' : (long ? '平多' : '平空'), t.exit, stop ? ORANGE : PINK, long, t.exit_t + (long ? 'a' : 'b'));
  }
  cx.restore();
}
function renderChips() {
  $('#indChips').innerHTML = S.inds.map((s, i) => {
    const name = (S.indData.find(d => d.id === s) || {}).name || s;
    return `<span class="chip ${S.sub === s ? 'act' : ''}" data-i="${i}" data-s="${s}"><i style="background:${PALETTE[i % PALETTE.length]}"></i>${name}<u data-rm="${i}">✕</u></span>`;
  }).join('');
}
function legendMove(p) {
  const lg = $('#legend');
  const d = p && p.time ? p.seriesData.get(candle) : null;
  const b = d || (S.bars.length ? (() => { const x = S.bars[S.bars.length - 1]; return { open: x.o, high: x.h, low: x.l, close: x.c }; })() : null);
  if (!b) return;
  const c = b.close >= b.open ? 'up' : 'dn';
  lg.innerHTML = `<span>开 <b class="${c}">${fmt(b.open)}</b></span><span>高 <b class="${c}">${fmt(b.high)}</b></span><span>低 <b class="${c}">${fmt(b.low)}</b></span><span>收 <b class="${c}">${fmt(b.close)}</b></span><span class="${c}">${pct((b.close / b.open - 1) * 100)}</span>`
    + Object.entries(overlays).map(([id, ss]) => { const v = p && p.time ? ss[0] && p.seriesData.get(ss[0]) : null; return v ? `<span style="color:${colorFor(id)}">${(S.indData.find(x => x.id === id) || {}).name || id} ${fmt(v.value)}</span>` : ''; }).join('');
}
function updateTop() {
  const last = S.bars[S.bars.length - 1]; if (!last) return;
  const el = $('#px'); const prev = S.lastPx; S.lastPx = last.c;
  el.textContent = fmt(last.c); if (prev && prev !== last.c) { el.classList.remove('flash-up', 'flash-dn'); void el.offsetWidth; el.classList.add(last.c > prev ? 'flash-up' : 'flash-dn'); }
  const tk = S.tickers[S.symbol]; const ch = $('#chg');
  if (tk) { ch.textContent = pct(tk.chg) + ' 24h'; ch.className = 'chg ' + cls(tk.chg); }
  else if (S.bars.length > 1) { const pc = S.bars[S.bars.length - 2].c, g = (last.c / pc - 1) * 100; ch.textContent = pct(g) + ' 较上根'; ch.className = 'chg ' + cls(g); }
  legendMove(null); calcTrade(); if (S.tickers[S.symbol]) renderWidgets();
}
async function tick() {
  if ((!S.authed && !S.viewer) || busy || document.hidden) return;
  try {
    const t0 = performance.now(), r = await api(`bar?symbol=${S.symbol}&interval=${S.interval}`); $('#sbLat').textContent = '延迟 ' + Math.round(performance.now() - t0) + ' ms';
    for (const b of r.bars) {
      candle.update({ time: sec(b.t), open: b.o, high: b.h, low: b.l, close: b.c });
      vol.update({ time: sec(b.t), value: b.v, color: b.c >= b.o ? 'rgba(64,182,107,.35)' : 'rgba(255,95,82,.35)' });
      const i = S.bars.findIndex(x => x.t === b.t);
      if (i >= 0) S.bars[i] = b; else { S.bars.push(b); setTimeout(() => loadChart(true), 800); }
    }
    updateTop();
  } catch (e) { }
}

/* ---------- 绘图（水平线/趋势线/矩形，存浏览器） ---------- */
const cv = $('#draw'), cx = cv.getContext('2d'); let pending = null, hover = null;
const dkey = () => 'uq_draw_' + S.symbol;
const loadDraw = () => { try { S.drawings = JSON.parse(localStorage[dkey()] || '[]'); } catch (e) { S.drawings = []; } };
const saveDraw = () => { try { localStorage[dkey()] = JSON.stringify(S.drawings); } catch (e) { } };
function sizeCanvas() { const w = $('#chartMain'), r = devicePixelRatio || 1; cv.width = w.clientWidth * r; cv.height = w.clientHeight * r; cv.style.width = w.clientWidth + 'px'; cv.style.height = w.clientHeight + 'px'; cx.setTransform(r, 0, 0, r, 0, 0); }
const xy = p => ({ x: main.timeScale().timeToCoordinate(p.t), y: candle.priceToCoordinate(p.p) });
function drawAll() {
  if (!main) return; cx.clearRect(0, 0, cv.width, cv.height);
  const W = $('#chartMain').clientWidth - 62;
  const items = [...S.drawings, ...(pending && hover ? [{ ...pending, p2: hover, ghost: 1 }] : [])];
  for (const d of items) {
    cx.save(); cx.strokeStyle = '#007AFF'; cx.fillStyle = 'rgba(0,122,255,.12)'; cx.lineWidth = 1.6; if (d.ghost) cx.setLineDash([5, 4]);
    const a = xy(d.p1), b = d.p2 ? xy(d.p2) : null;
    if (d.type === 'h') { if (a.y != null) { cx.beginPath(); cx.moveTo(0, a.y); cx.lineTo(W, a.y); cx.stroke(); cx.fillStyle = '#007AFF'; cx.font = '11px Inter'; cx.fillText(fmt(d.p1.p), 8, a.y - 5); } }
    else if (a.x != null && a.y != null && b && b.x != null && b.y != null) {
      if (d.type === 't') { cx.beginPath(); cx.moveTo(a.x, a.y); cx.lineTo(b.x, b.y); cx.stroke(); }
      else { cx.fillRect(a.x, a.y, b.x - a.x, b.y - a.y); cx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y); }
    }
    cx.restore();
  }
  drawTrades(W);
}
cv.addEventListener('click', e => {
  const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
  const t = main.timeScale().coordinateToTime(x), p = candle.coordinateToPrice(y); if (t == null || p == null) return;
  const pt = { t, p };
  if (S.tool === 'h') { S.drawings.push({ type: 'h', p1: pt }); saveDraw(); setTool(''); }
  else if (!pending) pending = { type: S.tool, p1: pt };
  else { S.drawings.push({ type: pending.type, p1: pending.p1, p2: pt }); pending = null; hover = null; saveDraw(); setTool(''); }
  drawAll();
});
cv.addEventListener('mousemove', e => { if (!pending) return; const r = cv.getBoundingClientRect(); const t = main.timeScale().coordinateToTime(e.clientX - r.left), p = candle.coordinateToPrice(e.clientY - r.top); if (t != null && p != null) { hover = { t, p }; drawAll(); } });
function setTool(t) {
  if (t === 'clear') { S.drawings = []; saveDraw(); pending = null; drawAll(); t = ''; }
  S.tool = t; pending = null; $$('#drawTools button').forEach(b => b.classList.toggle('on', b.dataset.tool === t)); cv.classList.toggle('on', !!t);
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') { setTool(''); $$('.modal').forEach(m => m.hidden = true); } });

/* ---------- 品种/周期 ---------- */
function renderItv() {
  const L = { '1m': '1分', '5m': '5分', '15m': '15分', '30m': '30分', '1h': '1时', '2h': '2时', '4h': '4时', '6h': '6时', '8h': '8时', '12h': '12时', '1d': '1天', '1w': '1周' };
  const stk = isStock(S.symbol);
  $('#itvSeg').innerHTML = Object.keys(L).map(k => `<button data-i="${k}" class="${k === S.interval ? 'on' : ''} ${stk && !STK_ITV.includes(k) ? 'off' : ''}" ${stk && !STK_ITV.includes(k) ? 'title="美股只支持1时/1天/1周"' : ''}>${L[k]}</button>`).join('');
}
function setPairUI() {
  const _em = $('.pair em'); if (_em) { _em.textContent = isStock(S.symbol) ? '美股' : '永续'; $('#pairBtn').classList.toggle('stk', isStock(S.symbol)); }
  $('#pairName').textContent = S.symbol; $('#pairIco').textContent = base(S.symbol)[0]; $('#outSym').textContent = base(S.symbol); $('#outIco').textContent = base(S.symbol)[0];
  document.title = `${S.symbol} · 独角兽量化`; if (typeof updateGo === 'function') updateGo();
}
async function setSymbol(sym) {
  S.symbol = sym; localStorage.uq_sym = sym; if (isStock(sym) && !STK_ITV.includes(S.interval)) { S.interval = '1d'; localStorage.uq_itv = '1d'; } renderItv(); setPairUI(); loadDraw(); S.bt = null; S.cmp = null; S.cmpDone = false; btLoading();
  await loadChart(); renderCurPos(); renderRec(); autoSignals();
}
S.pairTab = 'crypto'; S.stockQ = null;
const STK_POP = ['AAPL', 'NVDA', 'MSFT', 'TSLA', 'AMZN', 'META', 'GOOGL', 'AMD'];
function openPair() {
  $('#mdPair').hidden = false; $('#pairQ').value = ''; S.pairTab = isStock(S.symbol) ? 'stock' : 'crypto'; renderPairs(''); setTimeout(() => $('#pairQ').focus(), 50);
}
async function loadStockQ() { if (S.stockQ) return; S.stockQ = {}; try { const r = await api('stocks'); r.stocks.forEach(x => S.stockQ[x.symbol] = x); renderPairs($('#pairQ').value); } catch (e) { S.stockQ = null; } }
function renderPairs(q) {
  q = q.trim().toUpperCase(); const stk = S.pairTab === 'stock';
  $$('#pairTabs button').forEach(b => b.classList.toggle('on', b.dataset.pt === S.pairTab));
  if (stk) loadStockQ();
  $('#popular').innerHTML = (stk ? STK_POP : COMMON).map(s => `<span class="chip" data-sym="${s}"><i class="ico sm">${base(s)[0]}</i>${base(s)}</span>`).join('');
  let list = S.symbols.filter(s => !!s.stock === stk && (!q || s.symbol.includes(q) || (s.name || '').toUpperCase().includes(q)));
  if (!stk) list.sort((a, b) => ((S.tickers[b.symbol] || {}).vol || 0) - ((S.tickers[a.symbol] || {}).vol || 0));
  $('#pairList').innerHTML = list.slice(0, 80).map(s => { const t = stk ? ((S.stockQ || {})[s.symbol] || {}) : (S.tickers[s.symbol] || {}); return `<div class="pitem" data-sym="${s.symbol}"><i class="ico">${s.base[0]}</i><div class="nm"><b>${s.base}${s.tradfi ? ' <span class="tag s" style="background:var(--pink-bg);color:var(--pink)">TradFi</span>' : ''}${s.name ? ' <span style="font-weight:400;color:var(--t2)">' + s.name + '</span>' : ''}</b><span>${stk ? (s.sector || '美股') : s.symbol}</span></div><div class="pr">${t.price ? fmt(t.price) : ''}<div class="${cls(t.chg || 0)}" style="font-size:12px">${t.chg != null ? pct(t.chg) : ''}</div></div></div>`; }).join('') || '<div class="empty">没有匹配的交易对</div>';
}

/* ---------- 指标弹窗 ---------- */
function openInd() {
  $('#mdInd').hidden = false;
  $('#indGrid').innerHTML = INDS.map(i => `<div class="ind" data-k="${i.k}"><b>${i.n}</b><span>${i.d}</span><input value="${i.p}" placeholder="参数" ${i.p ? '' : 'disabled'} onclick="event.stopPropagation()"></div>`).join('');
}

/* ---------- 交易卡 ---------- */
const lev = () => +$('#lev').value, amt = () => parseFloat($('#amt').value) || 0;
function calcTrade() {
  const px = S.lastPx, a = amt(), l = lev(), notional = a * l;
  $('#lvLbl').textContent = '杠杆 ' + l + 'x'; $('#amtUsd').textContent = '杠杆后名义 $' + fmt(notional, 2);
  $('#outQty').textContent = px ? fmt(notional / px, px > 1000 ? 5 : px > 10 ? 3 : 2) : '0'; $('#infPx').textContent = fmt(px); $('#infFee').textContent = '$' + fmt(notional * 0.0005, 3);
}
function setSide(s) { S.side = s; $$('#sideSeg button').forEach(b => b.classList.toggle('on', b.dataset.s === s)); updateGo(); }
function setMode(m) { S.mode = m; $$('#modeSeg button').forEach(b => b.classList.toggle('on', b.dataset.m === m)); $('#infMode').textContent = m === 'paper' ? '模拟盘' : '实盘验证单'; $('#modeHint').textContent = m === 'paper' ? '模拟盘用币安实时价撮合，不动真钱。' : '只向交易所提交"验证单"，校验账户/精度/规则，不会成交。真实下单需服务器端显式开启。'; updateGo(); }
function updateGo() { const b = $('#goBtn'); if (isStock(S.symbol)) { b.className = 'btn-pink big'; b.disabled = true; b.textContent = '美股仅支持看图与回测'; return; } b.disabled = false; b.className = 'btn-pink big ' + (S.side === 'LONG' ? 'long' : 'short'); b.textContent = S.authed ? (S.mode === 'paper' ? (S.side === 'LONG' ? '开多 ' : '开空 ') + base(S.symbol) : '提交验证单') : '连接后交易'; }
async function submitOrder() {
  if (!S.authed) return openLogin();
  const body = { symbol: S.symbol, side: S.side, usdt: amt(), leverage: lev() };
  try {
    if (S.mode === 'paper') { const r = await api('paper/open', body); toast(`已${S.side === 'LONG' ? '开多' : '开空'} ${S.symbol} @ ${fmt(r.price)}`, 'ok'); await loadPaper(); }
    else { const r = await api('exchange/order', body); toast(r.note || '已完成', 'ok'); }
  } catch (e) { toast(e.message, 'err'); }
}
async function loadPaper() {
  if (!S.authed) return;
  try { S.paper = await api('paper'); $('#cashLbl').textContent = '可用 ' + fmt(S.paper.cash, 2) + ' USDT'; const d = S.paper.equity - S.paper.start; $('#acctLine').innerHTML = '权益 ' + fmt(S.paper.equity, 2) + ` <b class="${cls(d)}">${d >= 0 ? '+' : ''}${fmt(d, 2)}</b>`; renderCurPos(); renderRec(); } catch (e) { }
}
function renderCurPos() {
  const p = (S.paper?.positions || []).find(x => x.symbol === S.symbol);
  $('#curPos').innerHTML = p ? `<div class="pos"><div><span class="tag ${p.side === 'LONG' ? 'l' : 's'}">${p.side === 'LONG' ? '多' : '空'} ${p.lev}x</span> 开仓 ${fmt(p.entry)}<small>浮盈 <b class="${cls(p.pnl)}">${fmt(p.pnl, 2)} (${pct(p.pnl_pct)})</b></small></div><button class="xbtn" data-close-pos="${p.symbol}">平仓</button></div>` : '';
}

/* ---------- 回测工作台 ---------- */
const FAV = ['plan_f_crypto', 'plan_f_st8h', 'plan_f_bb8h', 'plan_f_stock'];   // 2026-10-18：工作台只保留最优方案两条腿
let btSeq = 0;
const stTitle = id => (S.strategies.find(x => x.id === id) || {}).title || id;
async function loadStrategies() {
  try {
    const r = await api('strategies'); S.strategies = r.strategies.filter(s => s.source !== 'error');
    $('#stSel').innerHTML = S.strategies.map(s => `<option value="${s.id}">${s.title}${s.source === 'repo' ? '' : ' ★'}</option>`).join('');
    $('#stCount').textContent = S.strategies.length + ' 个策略'; S.fav = FAV.filter(id => S.strategies.some(s => s.id === id));
    $('#stSel').value = localStorage.uq_st && S.strategies.some(s => s.id === localStorage.uq_st) ? localStorage.uq_st : (isStock(S.symbol) ? 'plan_f_stock' : 'plan_f_crypto');
    showStDesc(); renderPills();
  } catch (e) { }
}
function renderPills() { $('#stPills').innerHTML = (S.fav || []).map(id => `<a data-st="${id}" class="${id === $('#stSel').value ? 'on' : ''}">${stTitle(id)}</a>`).join('') + `<a href="/best" style="color:var(--pink);border-color:var(--pink);text-decoration:none">看最优方案复盘 →</a>`; }
function syncPills() { localStorage.uq_st = $('#stSel').value; renderPills(); }
function showStDesc() { const s = S.strategies.find(x => x.id === $('#stSel').value); $('#stDesc').textContent = s ? (s.desc || '').slice(0, 90) : ''; $('#stParams').value = s && s.params && Object.keys(s.params).length ? JSON.stringify(s.params) : ''; }
function selectStrategy(id) { $('#stSel').value = id; showStDesc(); syncPills(); return runBacktest(); }
function btLoading() { const b = $('#wbBody'); if (b && !S.bt) b.innerHTML = '<div class="empty">回测中…</div>'; b && b.classList.add('loading'); $('#btCtx').textContent = `${S.symbol} · ${S.interval} · 回测中…`; }
async function runBacktest(silent) {
  if (!S.authed && !S.viewer) return silent ? 0 : openLogin();
  // 方案F 是日线策略：美股自动用美股腿，周期自动切到1天
  if (isStock(S.symbol) && $('#stSel').value === 'plan_f_crypto') { $('#stSel').value = 'plan_f_stock'; showStDesc(); syncPills(); }
  // 方案F：日线腿用1天周期(3000根≈8年)，两条8h腿用8时周期(6000根≈5.5年)
  if (isStock(S.symbol) && /^plan_f_(st8h|bb8h)$/.test($('#stSel').value)) { $('#stSel').value = 'plan_f_stock'; showStDesc(); syncPills(); }
  if (/^plan_f_/.test($('#stSel').value)) {
    const want = /8h$/.test($('#stSel').value) ? '8h' : '1d', bars = want === '8h' ? '6000' : '3000';
    if (+$('#stBars').value < +bars) $('#stBars').value = bars;
    if (S.interval !== want) { S.interval = want; localStorage.uq_itv = want; renderItv(); S.bt = null; await loadChart(); }
  }
  const my = ++btSeq, b = $('#btBtn'); b.disabled = true; b.textContent = '回测中…'; btLoading();
  const done = () => { if (my === btSeq) { b.disabled = false; b.textContent = '运行回测'; $('#wbBody').classList.remove('loading'); } };
  let params = null; try { params = $('#stParams').value.trim() ? JSON.parse($('#stParams').value) : null; } catch (e) { done(); return toast('参数不是合法 JSON', 'err'); }
  try {
    const r = await api('backtest', { symbol: S.symbol, interval: S.interval, strategy: $('#stSel').value, limit: +$('#stBars').value, leverage: +$('#stLev').value || 1, params, long_only: $('#stLong').checked });
    if (my !== btSeq || r.symbol !== S.symbol || r.interval !== S.interval) return;
    S.bt = r; $('#btCtx').textContent = `${r.symbol} · ${r.interval} · ${r.stats.bars}根 · ${r.stats.days}天 · ${stTitle(r.strategy)}${$('#stLong').checked ? ' · 仅做多' : ''}`;
    renderBT(); drawAll(); done();
    if (!silent) toast(`回测完成：${r.stats.trades} 笔，收益 ${pct(r.stats.total_return)}`, 'ok');
  } catch (e) { done(); $('#wbBody').innerHTML = `<div class="empty">${e.message}</div>`; if (!silent) toast(e.message, 'err'); }
}
const nn = (v, d = 2, suf = '') => v == null ? '--' : fmt(v, d) + suf;
const barMs = () => S.bt && S.bt.equity.length > 1 ? S.bt.equity[1].t - S.bt.equity[0].t : 14400000;
const dur = n => { const h = n * barMs() / 3600000; return h >= 48 ? (h / 24).toFixed(1) + '天' : h.toFixed(0) + '小时'; };
function renderBT() {
  const r = S.bt, s = r.stats, T = (l, v, c = '', sub = '', hero) => `<div class="tile ${hero ? 'hero' : ''}"><span>${l}</span><b class="${c}">${v}</b>${sub ? `<small>${sub}</small>` : ''}</div>`;
  const tab = S.wbTab || 'trades';
  $('#wbBody').innerHTML = `<div class="kpi">${T('总收益', pct(s.total_return), cls(s.total_return), '期末权益 ' + fmt(s.final_equity, 0), 1)}${T('年化收益', s.annualized == null ? '--' : pct(s.annualized), s.annualized >= 0 ? 'up' : 'dn', '样本 ' + s.days + ' 天')}${T('最大回撤', '-' + fmt(s.max_drawdown, 2) + '%', 'dn')}${T('夏普比率', nn(s.sharpe), s.sharpe >= 0 ? 'up' : 'dn', '按K线收益年化')}${T('索提诺', nn(s.sortino), s.sortino >= 0 ? 'up' : 'dn')}${T('卡玛比率', nn(s.calmar), s.calmar >= 0 ? 'up' : 'dn', '年化/最大回撤')}</div>
  <div class="kpi2">${T('买入持有', pct(s.buy_hold), cls(s.buy_hold))}${T('胜率', fmt(s.win_rate, 1) + '%', '', s.trades + ' 笔')}${T('盈亏因子 PF', nn(s.profit_factor))}${T('单笔期望', fmt(s.expectancy, 2), cls(s.expectancy), 'USDT')}${T('平均盈 / 亏', `<span class="up">${fmt(s.avg_win, 0)}</span> / <span class="dn">${fmt(s.avg_loss, 0)}</span>`, '', '盈亏比 ' + nn(s.payoff))}${T('平均持仓', dur(s.avg_hold_bars), '', s.avg_hold_bars + ' 根K线')}
  ${T('市场暴露', fmt(s.exposure, 1) + '%')}${T('最大连赢 / 连亏', `<span class="up">${s.max_win_streak}</span> / <span class="dn">${s.max_loss_streak}</span>`)}${T('硬止损触发', s.stop_hits + ' 次', s.stop_hits ? 'dn' : '')}${T('最佳 / 最差一笔', `<span class="up">${fmt(s.best, 0)}</span> / <span class="dn">${fmt(s.worst, 0)}</span>`)}${T('手续费合计', fmt(s.fees, 2), '', '0.05%单边')}${T('当前持仓', r.open ? `${r.open.side === 'LONG' ? '多' : '空'} <span class="${cls(r.open.float_pnl)}">${fmt(r.open.float_pnl, 0)}</span>` : '空仓', '', r.open ? '@ ' + fmt(r.open.entry) : '')}</div>
  <canvas id="eqCanvas"></canvas>
  <div class="wb-tabs" id="wbTabs"><a data-wt="trades" class="${tab === 'trades' ? 'on' : ''}">成交明细 (${r.trades.length})</a><a data-wt="ls" class="${tab === 'ls' ? 'on' : ''}">多空分解</a><a data-wt="cmp" class="${tab === 'cmp' ? 'on' : ''}">策略对比</a></div>
  <div class="wb-pane" id="wbPane"></div>`;
  eqDraw(); renderPane();
}
function renderPane() {
  const r = S.bt, tab = S.wbTab || 'trades', el = $('#wbPane'); if (!el || !r) return;
  if (tab === 'trades') {
    const L = x => x === 'LONG' ? '<span class="tag l">多</span>' : '<span class="tag s">空</span>';
    el.innerHTML = r.trades.length ? `<table><tr><th>#</th><th>方向</th><th>开仓时间</th><th>开仓价</th><th>硬止损价</th><th>平仓时间</th><th>平仓价</th><th>盈亏(USDT)</th><th>收益率</th><th>持仓</th><th>离场</th><th>开仓原因</th><th>平仓原因</th></tr>${r.trades.map((x, i) => [x, i]).reverse().map(([x, i]) => `<tr data-trade="${i}"><td>${i + 1}</td><td>${L(x.side)}</td><td>${tstr(x.entry_t)}</td><td>${fmt(x.entry)}</td><td>${x.stop ? fmt(x.stop) : '--'}</td><td>${tstr(x.exit_t)}</td><td>${fmt(x.exit)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)}</td><td class="${cls(x.ret)}">${pct(x.ret)}</td><td>${dur(x.bars)}</td><td>${x.why === '硬止损' ? '<span class="tag" style="background:rgba(255,159,26,.16);color:#FF9F1A">硬止损</span>' : x.why}</td><td class="rsn" title="${(x.open_reason || '').replace(/"/g, '')}">${x.open_reason || ''}</td><td class="rsn" title="${(x.close_reason || '').replace(/"/g, '')}">${x.close_reason || ''}</td></tr>`).join('')}</table>` : '<div class="empty">该区间没有成交</div>';
  } else if (tab === 'ls') {
    const B = (n, o) => `<div class="tile"><span>${n}</span><b class="${cls(o.pnl)}">${fmt(o.pnl, 2)} USDT</b><small>${o.trades} 笔 · 胜率 ${fmt(o.win_rate, 1)}% · 平均 ${fmt(o.avg, 2)}</small></div>`;
    el.innerHTML = `<div class="ls">${B('做多', r.long)}${B('做空', r.short)}</div>`;
  } else {
    const rows = S.cmp;
    el.innerHTML = `<div class="cmp-bar"><button class="btn-pink sm" id="cmpBtn">${rows && S.cmpDone ? '重新对比' : rows ? '对比中…' : '运行对比'}</button><span class="mut small">同品种同周期同根数，按总收益排序；点击一行切换策略</span></div>` + (rows ? `<table><tr><th>策略</th><th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th><th>胜率</th><th>PF</th><th>笔数</th></tr>${rows.slice().sort((a, b) => (b.stats?.total_return ?? -1e9) - (a.stats?.total_return ?? -1e9)).map(x => x.error ? `<tr><td>${stTitle(x.id)}</td><td colspan="7" class="mut">${x.error}</td></tr>` : `<tr data-cmp="${x.id}" style="cursor:pointer"><td>${stTitle(x.id)}</td><td class="${cls(x.stats.total_return)}">${pct(x.stats.total_return)}</td><td>${x.stats.annualized == null ? '--' : pct(x.stats.annualized)}</td><td class="dn">-${fmt(x.stats.max_drawdown, 1)}%</td><td>${nn(x.stats.sharpe)}</td><td>${fmt(x.stats.win_rate, 1)}%</td><td>${nn(x.stats.profit_factor)}</td><td>${x.stats.trades}</td></tr>`).join('')}</table>` : '');
  }
}
async function runCompare(ids) {
  ids = ids || S.fav; const b = $('#cmpBtn'); if (b) b.disabled = true; S.cmp = []; S.cmpDone = false; const tag = S.symbol + S.interval;
  for (let i = 0; i < ids.length; i++) {
    if (tag !== S.symbol + S.interval) return;
    const id = ids[i], bb = $('#cmpBtn'); if (bb) bb.textContent = `对比中 ${i + 1}/${ids.length}…`;
    const st = S.strategies.find(x => x.id === id);
    try { const r = await api('backtest', { symbol: S.symbol, interval: S.interval, strategy: id, limit: +$('#stBars').value, leverage: 1, params: st && st.params && Object.keys(st.params).length ? st.params : null }); S.cmp.push({ id, stats: r.stats }); }
    catch (e) { S.cmp.push({ id, error: e.message.slice(0, 60) }); }
    if (S.wbTab === 'cmp') renderPane();
  }
  S.cmpDone = true; renderPane();
}
function focusTrade(i) {
  const t = S.bt.trades[i], a = S.bars.findIndex(b => b.t === t.entry_t), z = S.bars.findIndex(b => b.t === t.exit_t);
  if (a < 0) return toast('该笔早于图上加载的900根K线，请在更大范围查看', '');
  main.timeScale().setVisibleLogicalRange({ from: Math.max(0, a - 30), to: Math.min(S.bars.length + 5, (z < 0 ? a : z) + 30) });
  $('#sec-chart').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function eqDraw() {
  const c = $('#eqCanvas'); if (!c || !S.bt) return; const r = devicePixelRatio || 1, w = c.clientWidth, h = 240; c.width = w * r; c.height = h * r;
  const g = c.getContext('2d'); g.setTransform(r, 0, 0, r, 0, 0); const E = S.bt.equity; if (E.length < 2) return;
  const dark = S.theme === 'dark', grid = dark ? 'rgba(255,255,255,.06)' : 'rgba(0,0,0,.07)', txt = dark ? '#9B9B9B' : '#7D7D7D';
  const L = 52, R = 12, topH = 160, ddT = 180, ddH = 44, n = E.length;
  const all = E.flatMap(x => [x.v, x.bh]), mn = Math.min(...all), mx = Math.max(...all), pad = (mx - mn) * .08 || 1;
  const X = i => L + i / (n - 1) * (w - L - R), Y = v => 12 + (1 - (v - (mn - pad)) / ((mx - mn) + 2 * pad)) * (topH - 12);
  g.font = '11px Inter'; g.fillStyle = txt; g.strokeStyle = grid; g.lineWidth = 1;
  for (let k = 0; k <= 4; k++) { const v = mn - pad + (mx - mn + 2 * pad) * k / 4, y = Y(v); g.beginPath(); g.moveTo(L, y); g.lineTo(w - R, y); g.stroke(); g.fillText(fmt(v, 0), 6, y + 4); }
  const line = (key, color, wd, dash) => { g.beginPath(); E.forEach((x, i) => i ? g.lineTo(X(i), Y(x[key])) : g.moveTo(X(i), Y(x[key]))); g.strokeStyle = color; g.lineWidth = wd; g.setLineDash(dash || []); g.stroke(); g.setLineDash([]); };
  const gr = g.createLinearGradient(0, 0, 0, topH); gr.addColorStop(0, 'rgba(0,122,255,.28)'); gr.addColorStop(1, 'rgba(0,122,255,0)');
  g.beginPath(); E.forEach((x, i) => i ? g.lineTo(X(i), Y(x.v)) : g.moveTo(X(i), Y(x.v))); g.lineTo(X(n - 1), topH); g.lineTo(X(0), topH); g.fillStyle = gr; g.fill();
  line('bh', dark ? '#7A7A7A' : '#B0B0B0', 1.4, [4, 3]); line('v', '#007AFF', 2);
  const mxd = Math.max(...E.map(x => x.dd), .1); g.fillStyle = txt; g.fillText('回撤', 6, ddT + 12); g.fillText('-' + fmt(mxd, 1) + '%', 6, ddT + ddH);
  g.beginPath(); g.moveTo(X(0), ddT); E.forEach((x, i) => g.lineTo(X(i), ddT + x.dd / mxd * ddH)); g.lineTo(X(n - 1), ddT); g.closePath(); g.fillStyle = 'rgba(255,95,82,.35)'; g.fill();
  g.beginPath(); E.forEach((x, i) => i ? g.lineTo(X(i), ddT + x.dd / mxd * ddH) : g.moveTo(X(i), ddT + x.dd / mxd * ddH)); g.strokeStyle = '#FF3B30'; g.lineWidth = 1.2; g.stroke();
  g.fillStyle = txt; g.fillText(tstr(E[0].t), L, h - 4); const lt = tstr(E[n - 1].t); g.fillText(lt, w - R - g.measureText(lt).width, h - 4);
  g.fillStyle = '#007AFF'; g.fillRect(w - 250, 6, 10, 3); g.fillStyle = txt; g.fillText('策略权益', w - 236, 11); g.fillStyle = dark ? '#7A7A7A' : '#B0B0B0'; g.fillRect(w - 170, 6, 10, 3); g.fillStyle = txt; g.fillText('买入持有', w - 156, 11);
}

/* ---------- 策略库浏览 / 全库扫描 ---------- */
S.pick = new Set(); S.cat = '全部'; S.q = ''; S.stTab = 'browse';
function openSt(tab) { $('#mdSt').hidden = false; $('#stLibCount').textContent = S.strategies.length + ' 个'; setStTab(tab || 'browse'); renderStList(); }
function setStTab(t) { S.stTab = t; $$('#stTabs a').forEach(a => a.classList.toggle('on', a.dataset.t === t)); $('#stBrowse').hidden = t !== 'browse'; $('#stScan').hidden = t !== 'scan'; if (t === 'scan') { $('#scanCtx').textContent = `${S.symbol} · ${S.interval}`; pollSweep(); } }
function stBadges(x) { return (x.flag === 'ext' ? '<span class="bdg w" title="依赖资金费率/持仓量历史，回测引擎暂未提供">需外部数据·不可回测</span>' : x.flag === 'uni' ? '<span class="bdg w" title="需要币种池横截面排名">横截面·不适用</span>' : '') + (x.hint ? `<span class="bdg p">建议周期 ${x.hint}</span>` : '') + `<span class="bdg">${x.cat}</span>`; }
function renderStList() {
  const cats = ['全部', ...new Set(S.strategies.map(s => s.cat))];
  $('#stCats').innerHTML = cats.map(c => `<a data-cat="${c}" class="${c === S.cat ? 'on' : ''}">${c}</a>`).join('');
  const q = S.q.toLowerCase(), list = S.strategies.filter(x => (S.cat === '全部' || x.cat === S.cat) && (!q || x.id.toLowerCase().includes(q) || (x.desc || '').toLowerCase().includes(q) || x.title.toLowerCase().includes(q)));
  $('#stList').innerHTML = list.map(x => `<div class="strow"><input type="checkbox" data-pick="${x.id}" ${S.pick.has(x.id) ? 'checked' : ''} ${x.flag ? 'disabled' : ''}><div class="nm"><b>${x.title}</b>${stBadges(x)}<span>${x.desc || ''}</span></div><button class="chip" data-run="${x.id}">回测</button></div>`).join('') || '<div class="empty">没有匹配的策略</div>';
  $('#cmpPick').disabled = !S.pick.size; $('#pickInfo').textContent = S.pick.size ? `已选 ${S.pick.size} 个` : '勾选后可一次对比（最多16个）';
}
let sweepTimer = null;
async function pollSweep(start) {
  clearTimeout(sweepTimer); const n = +$('#scanBars').value;
  try {
    const r = start ? await api('sweep/start', { symbol: S.symbol, interval: S.interval, limit: n }) : await api(`sweep/status?symbol=${S.symbol}&interval=${S.interval}&limit=${n}`);
    renderSweep(r);
    if (r.status === 'running' || r.status === 'starting') sweepTimer = setTimeout(() => { if (!$('#mdSt').hidden || true) pollSweep(); }, 3500);
  } catch (e) { $('#scanStat').textContent = e.message; }
}
function renderSweep(r) {
  const run = r.status === 'running' || r.status === 'starting', pctDone = r.total ? r.done / r.total * 100 : 0;
  $('#scanBar').style.width = pctDone + '%'; $('#scanGo').disabled = run; $('#scanGo').textContent = run ? '扫描中…' : r.status === 'done' ? '重新扫描' : '开始扫描';
  const st = { none: '未开始', stalled: '扫描进程已中断，可重新开始', done: `完成：${r.done}/${r.total}，跳过 ${(r.skipped || []).length} 个不适用策略` }[r.status] || `扫描中 ${r.done}/${r.total}（约 ${Math.max(1, Math.round((r.total - r.done) * (r.done ? (Date.now() / 1000 - r.started) / r.done : 8) / 60))} 分钟）`;
  $('#scanStat').textContent = st;
  const min = +$('#scanMin').value, rows = (r.rows || []).filter(x => !x.err && x.trades >= min).sort((a, b) => b.ret - a.ret), errs = (r.rows || []).filter(x => x.err).length;
  $('#scanTbl').innerHTML = rows.length ? `<table><tr><th>策略</th><th>分类</th><th>总收益</th><th>超额(对买入持有)</th><th>年化</th><th>最大回撤</th><th>夏普</th><th>胜率</th><th>PF</th><th>笔数</th></tr>${rows.map(x => { const m = S.strategies.find(s => s.id === x.id) || {}; return `<tr data-run="${x.id}"><td>${stTitle(x.id)}</td><td>${m.cat || ''}</td><td class="${cls(x.ret)}">${pct(x.ret)}</td><td class="${cls(x.ret - x.bh)}">${pct(x.ret - x.bh)}</td><td>${x.ann == null ? '--' : pct(x.ann)}</td><td class="dn">-${fmt(x.dd, 1)}%</td><td>${nn(x.sharpe)}</td><td>${fmt(x.win, 1)}%</td><td>${nn(x.pf)}</td><td>${x.trades}</td></tr>`; }).join('')}</table>` + (errs ? `<div class="hint" style="text-align:left">另有 ${errs} 个策略运行出错/超时，未列入。</div>` : '') : `<div class="empty">${run ? '等待第一批结果…' : '没有满足条件的结果'}</div>`;
}

/* ---------- 美股研究 ---------- */
S.mx = { d: null, tab: 'rec', mode: 'lo', metric: 'sh', min: 8, robust: true, sector: '全部', heat: 'r' };
const MX_ITV = '1d', MX_LIM = 1260;
let mxTimer = null, mxLast = 0;
async function openMx() { $('#mdMx').hidden = false; $('#mxBody').innerHTML = '<div class="empty">加载中…</div>'; if (!S.symbols.length) { try { const sy = await api('symbols'); S.symbols = sy.symbols; } catch (e) { } } await loadMx(true); }
async function loadMx(full) {
  clearTimeout(mxTimer);
  try {
    const running = S.mx.d && S.mx.d.status === 'running', lite = !full && running && Date.now() - mxLast < 12000;
    const r = await api(`matrix/status?interval=${MX_ITV}&limit=${MX_LIM}${lite ? '&lite=1' : ''}`);
    if (lite) Object.assign(S.mx.d, r); else { S.mx.d = r; mxLast = Date.now(); }
    renderMx();
    if (r.status === 'running' || r.status === 'starting') mxTimer = setTimeout(() => loadMx(false), 4000);
  } catch (e) { $('#mxStat').textContent = e.message; }
}
async function startMx() {
  try { const r = await api('matrix/start', { interval: MX_ITV, limit: MX_LIM }); S.mx.d = { ...r, rows: [], bh: {} }; renderMx(); mxTimer = setTimeout(() => loadMx(true), 3000); } catch (e) { toast(e.message, 'err'); }
}
const stkMeta = s => S.symbols.find(x => x.symbol === s) || {};
function mxRows() {
  const d = S.mx.d; if (!d || !d.rows) return [];
  return d.rows.filter(x => x.m === S.mx.mode && !x.err && x.n != null).map(x => { const b = (d.bh || {})[x.s] || {}; return { ...x, bhr: b.r, bhd: b.d, ex: x.r - (b.r || 0), rd: x.r / Math.max(x.d, 1) }; });
}
const mxOK = x => x.n >= S.mx.min && (!S.mx.robust || (x.h1 > 0 && x.h2 > 0));
const mxKey = x => S.mx.metric === 'sh' ? (x.sh ?? -9) : x[S.mx.metric];
function renderMx() {
  const d = S.mx.d; if (!d) return;
  const run = d.status === 'running' || d.status === 'starting', p = d.total ? d.done / d.total * 100 : 0;
  $('#mxBar').style.width = p + '%'; $('#mxGo').disabled = run; $('#mxGo').textContent = run ? '扫描中…' : d.status === 'none' ? '开始扫描' : '重新扫描';
  const left = run && d.done ? Math.max(1, Math.round((d.total - d.done) * ((Date.now() / 1000 - d.started) / d.done) / 60)) : 0;
  $('#mxStat').textContent = d.status === 'none' ? '还没有扫描结果，点右上角「开始扫描」（约 30-50 分钟，后台运行，可关闭页面）' : run ? `扫描中 ${d.done}/${d.total}（约剩 ${left} 分钟）· 结果会边扫边出现` : `完成：${d.done}/${d.total} 个组合 · ${(d.symbols || []).length} 只标的 × ${(d.strategies || []).length} 个策略 × 两种模式${d.errors ? ' · ' + d.errors + ' 个出错/超时' : ''}`;
  $('#mxSub').textContent = d.bh && Object.keys(d.bh).length ? `日线 · 约${Math.round((Object.values(d.bh)[0].days || 0) / 365 * 10) / 10}年` : '';
  const secs = ['全部', ...new Set((d.symbols || []).map(s => stkMeta(s).sector).filter(Boolean))];
  $('#mxSectors').innerHTML = secs.map(c => `<a data-sec="${c}" class="${c === S.mx.sector ? 'on' : ''}">${c}</a>`).join('');
  $$('#mxTabs a').forEach(a => a.classList.toggle('on', a.dataset.mt === S.mx.tab));
  const el = $('#mxBody');
  if (!d.rows || !d.rows.length) { el.innerHTML = '<div class="empty">暂无结果</div>'; return; }
  const rows = mxRows(), syms = (d.symbols || []).filter(s => S.mx.sector === '全部' || stkMeta(s).sector === S.mx.sector);
  if (S.mx.tab === 'rec') {
    el.innerHTML = '<div class="mxgrid">' + syms.map(s => {
      const m = stkMeta(s), b = (d.bh || {})[s] || {}, v = rows.filter(x => x.s === s && mxOK(x)).sort((a, b2) => mxKey(b2) - mxKey(a)).slice(0, 3);
      return `<div class="mxc"><div class="mxh"><b>${s}</b><span>${m.name || ''} · ${m.sector || ''}</span><span class="bh">买入持有 <b class="${cls(b.r)}" style="font-size:13px">${b.r == null ? '--' : pct(b.r)}</b> · 回撤 <span class="dn">-${fmt(b.d || 0, 0)}%</span></span></div>`
        + (v.length ? v.map(x => `<div class="mxr" data-open="${x.s}|${x.i}|${x.m}"><div class="t">${stTitle(x.i)}<small>${x.h1 > 0 && x.h2 > 0 ? '<span class="ok">前后半段均盈利</span>' : '<span class="no">非稳健</span>'} · ${x.n}笔 · 胜率 ${fmt(x.w, 0)}%</small></div><div class="${cls(x.r)}">${pct(x.r)}<small class="${cls(x.ex)}">${x.ex >= 0 ? '跑赢' : '跑输'}持有 ${fmt(Math.abs(x.ex), 0)}%</small></div><div class="dn">-${fmt(x.d, 1)}%<small>最大回撤</small></div><div>${nn(x.sh)}<small>夏普</small></div><div>${nn(x.rd, 1)}<small>回报比</small></div></div>`).join('') : '<div class="mut small" style="padding:10px 4px">没有满足条件的策略（可放宽「最少笔数」或取消「仅稳健」）</div>') + '</div>';
    }).join('') + '</div>';
  } else if (S.mx.tab === 'heat') {
    const strs = d.strategies || [], idx = new Map(rows.map(x => [x.s + '|' + x.i, x])), key = S.mx.heat;
    const val = x => key === 'r' ? x.r : key === 'ex' ? x.ex : (x.sh ?? 0), sc = key === 'sh' ? 2 : 80;
    el.innerHTML = `<div class="mx-ctl" style="margin:0 0 6px"><div class="seg" id="mxHeatSeg"><button data-h="r" class="${key === 'r' ? 'on' : ''}">总收益%</button><button data-h="ex" class="${key === 'ex' ? 'on' : ''}">超额%</button><button data-h="sh" class="${key === 'sh' ? 'on' : ''}">夏普</button></div><span class="mut small">颜色=好坏，暗淡=笔数不足；点格子打开该组合</span></div><table class="heat"><tr><th></th>${strs.map(i => `<th><div>${stTitle(i)}</div></th>`).join('')}</tr>${syms.map(s => `<tr><td class="rn">${s}</td>${strs.map(i => { const x = idx.get(s + '|' + i); if (!x) return '<td></td>'; const v = val(x), a = Math.min(Math.abs(v) / sc, 1) * .85; return `<td class="${x.n < S.mx.min ? 'dim' : ''}" style="background:${v >= 0 ? `rgba(64,182,107,${a})` : `rgba(255,95,82,${a})`}" data-open="${s}|${i}|${x.m}" title="${s} · ${stTitle(i)}\n收益 ${pct(x.r)} / 超额 ${pct(x.ex)} / 夏普 ${nn(x.sh)} / ${x.n}笔 / 回撤 -${fmt(x.d, 1)}%">${key === 'sh' ? fmt(v, 1) : fmt(v, 0)}</td>`; }).join('')}</tr>`).join('')}</table>`;
  } else {
    const by = {}; rows.filter(x => syms.includes(x.s)).forEach(x => (by[x.i] ||= []).push(x));
    const mean = a => a.reduce((t, x) => t + x, 0) / a.length, med = a => { const b = [...a].sort((x, y) => x - y); return b[Math.floor(b.length / 2)]; };
    const agg = Object.entries(by).map(([id, arr]) => { const v = arr.filter(x => x.n >= S.mx.min); if (v.length < 3) return null; const rb = v.filter(x => x.h1 > 0 && x.h2 > 0); return { id, nsym: v.length, mr: mean(v.map(x => x.r)), md: med(v.map(x => x.r)), dd: mean(v.map(x => x.d)), sh: mean(v.map(x => x.sh ?? 0)), win: v.filter(x => x.r > 0).length / v.length * 100, beat: v.filter(x => x.ex > 0).length / v.length * 100, rob: rb.length, best: v.filter(x => !S.mx.robust || (x.h1 > 0 && x.h2 > 0)).sort((a, b) => mxKey(b) - mxKey(a)).slice(0, 4) }; }).filter(Boolean).sort((a, b) => b.rob - a.rob || b.sh - a.sh);
    el.innerHTML = `<table class="agg"><tr><th>策略</th><th>分类</th><th>样本股数</th><th>平均收益</th><th>中位收益</th><th>平均回撤</th><th>平均夏普</th><th>赚钱占比</th><th>跑赢持有占比</th><th>稳健股数</th><th>最适配个股</th></tr>${agg.map(a => `<tr><td>${stTitle(a.id)}</td><td>${(S.strategies.find(x => x.id === a.id) || {}).cat || ''}</td><td>${a.nsym}</td><td class="${cls(a.mr)}">${pct(a.mr)}</td><td class="${cls(a.md)}">${pct(a.md)}</td><td class="dn">-${fmt(a.dd, 1)}%</td><td>${nn(a.sh)}</td><td>${fmt(a.win, 0)}%</td><td>${fmt(a.beat, 0)}%</td><td>${a.rob}/${a.nsym}</td><td>${a.best.map(x => `<span class="chp" data-open="${x.s}|${x.i}|${x.m}">${x.s} ${pct(x.r)}</span>`).join('')}</td></tr>`).join('')}</table>`;
  }
}
async function openCombo(sym, id, mode) {
  $('#mdMx').hidden = true; S.interval = MX_ITV; localStorage.uq_itv = MX_ITV; $('#stLong').checked = mode === 'lo';
  $('#stSel').value = id; showStDesc(); syncPills(); await setSymbol(sym); $('#sec-chart').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- 美股趋势突破扫描器 ---------- */
S.sc = { d: null, tab: 'sig' }; let scTimer = null;
async function openSc() { $('#mdSc').hidden = false; $('#scBody').innerHTML = '<div class="empty">加载中…</div>'; await loadSc(); }
async function loadSc() {
  clearTimeout(scTimer);
  try { S.sc.d = await api('scanner'); renderSc(); if (S.sc.d.running) scTimer = setTimeout(loadSc, 4000); }
  catch (e) { $('#scBody').innerHTML = `<div class="empty">${e.message}</div>`; }
}
const stag = r => `<span class="stag ${r.signal ? 'new' : r.days_since != null ? 'rec' : (!r.trend_up && r.ema_fast < r.ema_slow) ? 'bear' : ''}">${r.status}</span>`;
function scRow(r, extra) {
  return `<tr data-scopen="${r.sym}"><td class="l"><b>${r.sym}</b> <span class="mut small">${r.name}</span></td><td class="l mut">${r.sector}</td><td>${fmt(r.close)}</td><td class="${cls(r.chg)}">${pct(r.chg)}</td><td class="${r.rs >= 0 ? 'up' : 'dn'}">${r.rs == null ? '--' : pct(r.rs)}<small class="mut"> ${r.rs_pct == null ? '' : 'P' + r.rs_pct}</small></td><td>${r.htf_up ? '<span class="up">✓</span>' : '<span class="mut">✗</span>'}</td><td>${r.res == null ? '无' : fmt(r.res) + ' <small class="mut">+' + fmt(r.res_gap, 1) + '%</small>'}</td><td>${r.sup == null ? '--' : fmt(r.sup) + ' <small class="mut">-' + fmt(r.sup_gap, 1) + '%</small>'}</td><td class="${r.ext > 15 ? 'sc-warn' : ''}">${pct(r.ext)}</td>${extra || ''}</tr>`;
}
function renderSc() {
  const d = S.sc.d; if (!d) return; const scan = d.scan, tab = S.sc.tab;
  $$('#scTabs a').forEach(a => a.classList.toggle('on', a.dataset.st2 === tab));
  $('#scGo').disabled = d.running; $('#scGo').textContent = d.running ? '扫描中…' : '立即刷新';
  const nerr = scan ? Object.keys(scan.errors || {}).length : 0;
  $('#scStat').textContent = scan ? `数据日期 ${scan.bar_date} · 更新于 ${new Date(scan.updated * 1000).toLocaleString()} · ${scan.rows.length} 只${nerr ? ' · ' + nerr + ' 只数据缺失' : ''} · 每个美股交易日收盘后自动更新` : '还没有扫描结果，点右上角「立即刷新」(约1-2分钟)';
  $('#scSub').textContent = scan ? `日线 EMA${scan.spec.pair[0]}/${scan.spec.pair[1]} + 周线多头 + 突破压力位` : '';
  const el = $('#scBody');
  if (tab === 'rules') {
    el.innerHTML = `<div class="sc-note"><b>入场(今日信号)</b>：周线多头(EMA10>EMA30 且收盘>EMA30) + 日线EMA7>EMA25且收盘>EMA25 + 今日收盘突破上方最近压力位(周线分形高低点聚类)，且前一日收盘还在压力位下方。<br><b>出场</b>：EMA7 跌破 EMA25(趋势反转)；硬止损：入场价 -10%(表中另给"结构止损"=最近支撑下方，夹在 -5%~-15%)。<br><b>仓位</b>：10个等权槽位、只做多、不换手；候选按 63 日相对强度(涨幅)排序，强者优先。<br><b>操作时点</b>：收盘后出信号，次日开盘价执行(纸面账户按此成交)。<br><b>"距25日线"</b>：标橙色表示已离均线较远(>15%)，追高风险更大。<br><b>唐奇安55确认</b>：收盘同时突破55日高点且 EMA20>EMA100，是更严格的第二重确认。<br><br><b>研究依据与局限</b>：日线触发、周线大级别，51只美股9.4年1296个组合里，这个结构样本内年化约23.7%、最大回撤约18.9%(对照QQQ 20.0%/-36%)，胜率约41%、盈亏比约2.4——大多数交易是小亏，靠少数大赢家。<span class="sc-warn">股票池是"现在知名"的公司，有幸存者偏差；不含税、分红、真实滑点；样本内结果会高估未来表现；全池等权买入持有的收益更高，这套的价值主要是把回撤降低一半左右。</span>本页仅为研究与纸面跟踪，不构成投资建议，不会下单。</div>`;
    return;
  }
  if (!scan) { el.innerHTML = '<div class="empty">暂无数据</div>'; return; }
  const H = '<tr><th class="l">代码</th><th class="l">板块</th><th>现价</th><th>日涨跌</th><th>63日相对强度</th><th>周线多头</th><th>上方压力位</th><th>下方支撑位</th><th>距25日线</th>';
  if (tab === 'sig') {
    const rows = scan.rows.filter(r => r.signal);
    el.innerHTML = rows.length ? `<table class="sc-tbl">${H}<th>突破的压力位</th><th>建议止损(-10%)</th><th>结构止损</th><th>ATR%</th><th>唐奇安55</th></tr>${rows.map(r => scRow(r, `<td>${fmt(r.broken)}</td><td>${fmt(r.stop_fixed)}</td><td>${r.stop_struct ? fmt(r.stop_struct) : '--'}</td><td>${fmt(r.atr_pct, 1)}%</td><td>${r.don55 ? '<span class="up">✓</span>' : '<span class="mut">—</span>'}</td>`)).join('')}</table><div class="sc-note">共 ${rows.length} 只今日新突破，按相对强度从强到弱排序；点一行打开日线图和回测。收盘后出信号，次日开盘执行。</div>` : `<div class="empty">今天(${scan.bar_date})没有新突破信号。信号本来就稀少(10个槽位的组合约每周1~3个)，可以看「近期突破」和「全池状态」。</div>`;
  } else if (tab === 'recent') {
    const rows = scan.rows.filter(r => !r.signal && r.days_since != null && r.trend_up);
    el.innerHTML = rows.length ? `<table class="sc-tbl">${H}<th>状态</th></tr>${rows.map(r => scRow(r, `<td>${stag(r)}</td>`)).join('')}</table><div class="sc-note">近 ${scan.spec.recent_days} 个交易日内突破过、且趋势仍成立的标的。追入要看"距25日线"，离得太远不要追。</div>` : '<div class="empty">近5日没有仍然有效的突破</div>';
  } else if (tab === 'all') {
    el.innerHTML = `<table class="sc-tbl">${H}<th>状态</th></tr>${scan.rows.slice().sort((a, b) => (b.rs ?? -999) - (a.rs ?? -999)).map(r => scRow(r, `<td>${stag(r)}</td>`)).join('')}</table>`;
  } else {
    const p = d.paper, ret = (p.equity / p.equity0 - 1) * 100, wins = p.hist.filter(h => h.ret > 0).length;
    const T = (l, v, c = '') => `<div class="tile"><span>${l}</span><b class="${c}">${v}</b></div>`;
    const cv = p.curve, mn = Math.min(...cv.map(x => x.eq), p.equity0), mx = Math.max(...cv.map(x => x.eq), p.equity0);
    const pts = cv.length > 1 ? cv.map((x, i) => `${(i / (cv.length - 1) * 596 + 2).toFixed(1)},${(116 - (x.eq - mn) / ((mx - mn) || 1) * 108).toFixed(1)}`).join(' ') : '';
    el.innerHTML = `<div class="kpi2" style="grid-template-columns:repeat(6,1fr)">${T('纸面权益', fmt(p.equity, 0))}${T('累计收益', pct(ret), cls(ret))}${T('现金', fmt(p.cash, 0))}${T('持仓数', p.positions.length + ' / ' + scan.spec.slots)}${T('已平仓', p.hist.length + ' 笔')}${T('胜率', p.hist.length ? fmt(wins / p.hist.length * 100, 0) + '%' : '--')}</div>
      ${pts ? `<svg class="sc-curve" viewBox="0 0 600 120" preserveAspectRatio="none"><polyline fill="none" stroke="#007AFF" stroke-width="2" points="${pts}"/></svg>` : ''}
      <div class="sc-note">起始日 ${p.start || '--'}，初始 ${fmt(p.equity0, 0)}，已处理到 ${p.last_date || '--'}。严格按规则自动跟踪：信号次日开盘买(每槽=权益/10)，趋势反转或 -10% 次日开盘卖，不手工干预。<a data-screset class="lnk">重置账户</a></div>
      <table class="sc-tbl"><tr><th class="l">持仓</th><th>买入日</th><th>成本</th><th>现价</th><th>浮盈</th><th>止损价</th><th>明日动作</th></tr>${p.positions.map(x => `<tr data-scopen="${x.sym}"><td class="l"><b>${x.sym}</b> <span class="mut small">${x.name}</span></td><td>${x.date}</td><td>${fmt(x.entry)}</td><td>${fmt(x.close)}</td><td class="${cls(x.pnl || 0)}">${x.pnl == null ? '--' : pct(x.pnl)}</td><td>${fmt(x.stop)}</td><td>${x.exit_flag ? '<span class="stag bear">卖出：' + x.exit_flag + '</span>' : '持有'}</td></tr>`).join('') || '<tr><td colspan="7" class="empty">暂无持仓（等新突破信号，按次日开盘买入）</td></tr>'}</table>
      ${p.pend_buy.length ? `<div class="sc-note">明日开盘计划买入：<b>${p.pend_buy.join('、')}</b></div>` : ''}
      ${p.hist.length ? `<table class="sc-tbl" style="margin-top:8px"><tr><th class="l">已平仓</th><th>买入日</th><th>卖出日</th><th>成本</th><th>卖价</th><th>收益</th><th>原因</th></tr>${p.hist.map(h => `<tr><td class="l"><b>${h.sym}</b></td><td>${h.t0}</td><td>${h.t1}</td><td>${fmt(h.entry)}</td><td>${fmt(h.exit)}</td><td class="${cls(h.ret)}">${pct(h.ret)}</td><td>${h.why}</td></tr>`).join('')}</table>` : ''}`;
  }
}
async function refreshSc() { try { await api('scanner/refresh', {}); toast('已开始扫描，约1-2分钟', 'ok'); setTimeout(loadSc, 1500); } catch (e) { toast(e.message, 'err'); } }

/* ---------- 行情组件：跑马灯/自选/涨跌统计/状态栏 ---------- */
function renderWidgets() {
  const tk = S.tickers, top = Object.entries(tk).filter(([k]) => k.endsWith('USDT')).sort((a, b) => b[1].vol - a[1].vol).slice(0, 16);
  $('#tapeIn').innerHTML = top.map(([k, v]) => `<a data-sym="${k}">${base(k)} <b>${fmt(v.price)}</b><span class="${cls(v.chg)}">${pct(v.chg)}</span></a>`).join('');
  $('#wlBody').innerHTML = COMMON.map(k => { const v = tk[k]; if (!v) return ''; return `<div class="wli ${k === S.symbol ? 'on' : ''}" data-sym="${k}"><i class="ico sm">${base(k)[0]}</i><span class="n">${base(k)}</span><span class="p">${fmt(v.price)}<br><span class="c ${cls(v.chg)}">${pct(v.chg)}</span></span></div>`; }).join('');
  const t = tk[S.symbol]; $('#mstats').innerHTML = t ? `<div><span>24h 最高</span><b>${fmt(t.hi)}</b></div><div><span>24h 最低</span><b>${fmt(t.lo)}</b></div><div><span>24h 成交额</span><b>${t.vol >= 1e9 ? fmt(t.vol / 1e9, 2) + 'B' : fmt(t.vol / 1e6, 1) + 'M'}</b></div>` : '';
}
function clockTick() { const d = new Date(), z = n => String(n).padStart(2, '0'), off = -d.getTimezoneOffset() / 60; $('#sbClock').textContent = `${z(d.getHours())}:${z(d.getMinutes())}:${z(d.getSeconds())}  UTC${off >= 0 ? '+' : ''}${off}`; $('#sbSym').textContent = `${S.symbol} · ${S.interval}`; $('#sbMode').textContent = S.mode === 'paper' ? '模拟盘' : '实盘验证'; }

/* ---------- 记录表 ---------- */
function renderRec(which) {
  if (which) { S.rec = which; $$('#recTabs a').forEach(a => a.classList.toggle('on', a.dataset.r === which)); }
  const el = $('#recBody'); let h = '';
  if (S.rec === 'pp') {
    const p = S.paper?.positions || [];
    h = (S.paper ? `<div class="hint" style="text-align:left;margin:0 0 8px">模拟盘权益 <b>${fmt(S.paper.equity, 2)}</b> USDT（初始 ${fmt(S.paper.start, 0)}）　<a data-reset style="color:var(--pink)">重置账户</a></div>` : '')
      + (p.length ? `<table><tr><th>品种</th><th>方向</th><th>开仓价</th><th>标记价</th><th>保证金</th><th>浮动盈亏</th><th></th></tr>${p.map(x => `<tr><td>${x.symbol}</td><td><span class="tag ${x.side === 'LONG' ? 'l' : 's'}">${x.side === 'LONG' ? '多' : '空'} ${x.lev}x</span></td><td>${fmt(x.entry)}</td><td>${fmt(x.mark)}</td><td>${fmt(x.margin, 2)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)} (${pct(x.pnl_pct)})</td><td><a data-close-pos="${x.symbol}" class="tag l" style="background:var(--pink-bg);color:var(--pink)">平仓</a></td></tr>`).join('')}</table>` : '<div class="empty">暂无模拟持仓</div>');
  } else {
    const hs = S.paper?.history || [];
    h = hs.length ? `<table><tr><th>品种</th><th>方向</th><th>开仓价</th><th>平仓价</th><th>盈亏</th><th>平仓时间</th></tr>${hs.map(x => `<tr><td>${x.symbol}</td><td><span class="tag ${x.side === 'LONG' ? 'l' : 's'}">${x.side === 'LONG' ? '多' : '空'}</span></td><td>${fmt(x.entry)}</td><td>${fmt(x.exit)}</td><td class="${cls(x.pnl)}">${fmt(x.pnl, 2)}</td><td>${tstr(x.t_close)}</td></tr>`).join('')}</table>` : '<div class="empty">暂无历史</div>';
  }
  el.innerHTML = h;
}

/* ---------- 登录 ---------- */
function setAuth(ok) { S.authed = ok; $('#authBtn').textContent = ok ? '已登录' : '交易登录'; $('#authBtn').style.opacity = ok ? .75 : 1; updateGo(); }
function openLogin() { $('#mdLogin').hidden = false; $('#loginErr').textContent = ''; setTimeout(() => $('#pw').focus(), 50); }
async function doLogin() {
  try { await api('login', { password: $('#pw').value }); $('#mdLogin').hidden = true; $('#pw').value = ''; await boot(); }
  catch (e) { $('#loginErr').textContent = e.message; }
}

/* ---------- 事件 ---------- */
function bind() {
  document.addEventListener('click', async e => {
    const t = e.target;
    const c = t.closest('[data-close]'); if (c || t.classList.contains('modal')) { (t.closest('.modal') || {}).hidden = true; if (c) c.closest('.modal').hidden = true; return; }
    const ptb = t.closest('#pairTabs button'); if (ptb) { S.pairTab = ptb.dataset.pt; return renderPairs($('#pairQ').value); }
    if (t.closest('#pairBtn') || t.closest('#navSearch') || t.closest('#tokenBtn2')) return openPair();
    const sym = t.closest('[data-sym]'); if (sym) { $('#mdPair').hidden = true; return setSymbol(sym.dataset.sym); }
    const itv = t.closest('#itvSeg button'); if (itv && itv.classList.contains('off')) return toast('美股只支持 1时 / 1天 / 1周', ''); if (itv) { S.interval = itv.dataset.i; localStorage.uq_itv = S.interval; renderItv(); S.bt = null; S.cmp = null; S.cmpDone = false; btLoading(); await loadChart(); return autoSignals(); }
    const rm = t.closest('[data-rm]'); if (rm) { S.inds.splice(+rm.dataset.rm, 1); localStorage.uq_inds = JSON.stringify(S.inds); return loadChart(true); }
    const chip = t.closest('#indChips .chip'); if (chip && /^(rsi|macd|atr)/.test(chip.dataset.s)) { S.sub = chip.dataset.s; return loadChart(true); }
    if (t.closest('#addInd')) return openInd();
    const ind = t.closest('.ind'); if (ind) { const inp = ind.querySelector('input'); const spec = ind.dataset.k + (inp && inp.value ? ':' + inp.value : ''); if (!S.inds.includes(spec)) S.inds.push(spec); if (/^(rsi|macd|atr)/.test(spec)) S.sub = spec; localStorage.uq_inds = JSON.stringify(S.inds); $('#mdInd').hidden = true; return loadChart(true); }
    const tool = t.closest('#drawTools button'); if (tool) return setTool(tool.dataset.tool);
    const sd = t.closest('#sideSeg button'); if (sd) return setSide(sd.dataset.s);
    const md = t.closest('#modeSeg button'); if (md) return setMode(md.dataset.m);
    const q = t.closest('.quick a'); if (q) { const cash = S.paper?.cash || 10000; $('#amt').value = Math.floor(cash * +q.dataset.p * 100) / 100; return calcTrade(); }
    if (t.closest('#goBtn')) return submitOrder();
    const cp = t.closest('[data-close-pos]'); if (cp) { try { const r = await api('paper/close', { symbol: cp.dataset.closePos }); toast(`已平仓，盈亏 ${fmt(r.pnl, 2)} USDT`, r.pnl >= 0 ? 'ok' : ''); loadPaper(); } catch (er) { toast(er.message, 'err'); } return; }
    if (t.closest('[data-reset]')) { if (confirm('重置模拟账户为 10,000 USDT？')) { await api('paper/reset', {}); loadPaper(); } return; }
    if (t.closest('#btBtn')) return runBacktest();
    if (t.closest('#sigBtn')) return setSig(!S.sig);
    const op = t.closest('[data-open]'); if (op) { const [a, b2, c2] = op.dataset.open.split('|'); return openCombo(a, b2, c2); }
    const mmode = t.closest('#mxMode button'); if (mmode) { S.mx.mode = mmode.dataset.mode; $$('#mxMode button').forEach(b => b.classList.toggle('on', b === mmode)); return renderMx(); }
    const mt = t.closest('#mxTabs a'); if (mt) { S.mx.tab = mt.dataset.mt; return renderMx(); }
    const msec = t.closest('#mxSectors a'); if (msec) { S.mx.sector = msec.dataset.sec; return renderMx(); }
    const mh = t.closest('#mxHeatSeg button'); if (mh) { S.mx.heat = mh.dataset.h; return renderMx(); }
    if (t.closest('#mxGo')) return startMx();
    const sct = t.closest('#scTabs a'); if (sct) { S.sc.tab = sct.dataset.st2; return renderSc(); }
    if (t.closest('#scGo')) return refreshSc();
    if (t.closest('[data-screset]')) { if (confirm('重置纸面账户？(清空持仓和历史，重新开始)')) { await api('scanner/reset', { confirm: true }); loadSc(); } return; }
    const sco = t.closest('[data-scopen]'); if (sco) { $('#mdSc').hidden = true; S.interval = '1d'; localStorage.uq_itv = '1d'; return setSymbol(sco.dataset.scopen); }
    if (t.closest('[data-more]')) return openSt('browse');
    const sp = t.closest('[data-st]'); if (sp) return selectStrategy(sp.dataset.st);
    const stt = t.closest('#stTabs a'); if (stt) return setStTab(stt.dataset.t);
    const sc = t.closest('#stCats a'); if (sc) { S.cat = sc.dataset.cat; return renderStList(); }
    const pk = t.closest('[data-pick]'); if (pk) { pk.checked ? S.pick.add(pk.dataset.pick) : S.pick.delete(pk.dataset.pick); if (S.pick.size > 16) { S.pick.delete(pk.dataset.pick); pk.checked = false; toast('一次最多对比16个', ''); } return renderStList(); }
    if (t.closest('#pickCat')) { S.strategies.filter(x => (S.cat === '全部' || x.cat === S.cat) && !x.flag).slice(0, 16).forEach(x => S.pick.add(x.id)); return renderStList(); }
    if (t.closest('#cmpPick')) { const ids = [...S.pick]; $('#mdSt').hidden = true; S.wbTab = 'cmp'; if (S.bt) renderBT(); $('#sec-bt').scrollIntoView({ behavior: 'smooth', block: 'start' }); return runCompare(ids); }
    if (t.closest('#scanGo')) return pollSweep(true);
    const rn = t.closest('[data-run]'); if (rn) { $('#mdSt').hidden = true; return selectStrategy(rn.dataset.run); }
    const wt = t.closest('#wbTabs a'); if (wt) { S.wbTab = wt.dataset.wt; $$('#wbTabs a').forEach(a => a.classList.toggle('on', a === wt)); return renderPane(); }
    const tr = t.closest('tr[data-trade]'); if (tr) return focusTrade(+tr.dataset.trade);
    const cm = t.closest('tr[data-cmp]'); if (cm) return selectStrategy(cm.dataset.cmp);
    if (t.closest('#cmpBtn')) return runCompare();
    const rt = t.closest('#recTabs a'); if (rt) return renderRec(rt.dataset.r);
    if (t.closest('#authBtn')) return S.authed ? (await api('logout', {}), setAuth(false), toast('已断开')) : openLogin();
    if (t.closest('#loginGo')) return doLogin();
    if (t.closest('#themeBtn')) return setTheme(S.theme === 'dark' ? 'light' : 'dark');
    const nv = t.closest('#mainNav a'); if (nv && nv.dataset.go === 'us') return openMx();
    if (nv && nv.dataset.go === 'scan') return openSc();
    if (nv) { $$('#mainNav a').forEach(a => a.classList.toggle('on', a === nv)); const go = nv.dataset.go; const el = go === 'records' ? $('#sec-records') : go === 'chart' ? $('#sec-chart') : go === 'strategy' ? $('#sec-bt') : $('#sec-trade'); el.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
  });
  $('#mxMetric').addEventListener('change', e => { S.mx.metric = e.target.value; renderMx(); });
  $('#mxMin').addEventListener('change', e => { S.mx.min = +e.target.value; renderMx(); });
  $('#mxRobust').addEventListener('change', e => { S.mx.robust = e.target.checked; renderMx(); });
  $('#stQ').addEventListener('input', e => { S.q = e.target.value; renderStList(); });
  $('#scanMin').addEventListener('change', () => pollSweep());
  $('#pairQ').addEventListener('input', e => renderPairs(e.target.value));
  $('#pw').addEventListener('keydown', e => e.key === 'Enter' && doLogin());
  $('#amt').addEventListener('input', calcTrade); $('#lev').addEventListener('input', calcTrade);
  $('#stSel').addEventListener('change', () => { showStDesc(); syncPills(); autoSignals(); });
  $('#stBars').addEventListener('change', autoSignals);
  $('#stLong').addEventListener('change', autoSignals);
  window.addEventListener('resize', () => S.bt && eqDraw());
  document.addEventListener('keydown', e => { if (e.key === '/' && !/INPUT|SELECT/.test(document.activeElement.tagName)) { e.preventDefault(); openPair(); } });
}

/* ---------- 启动 ---------- */
async function boot() {
  const me = await (await fetch('/api/me')).json();
  S.viewer = Boolean(me.viewer || me.ok);
  setAuth(me.ok);
  if (!S.viewer) { $('#skel').classList.add('off'); if (me.configured) openLogin(); return; }
  try {
    const [sy, tk] = await Promise.all([api('symbols'), api('tickers')]); S.symbols = sy.symbols; S.tickers = tk;
    await Promise.all([loadChart(), loadStrategies(), loadPaper()]); autoSignals();
  } catch (e) { toast(e.message, 'err'); }
  renderWidgets(); $('#sbConn').textContent = '已连接';
  setInterval(() => api('tickers').then(t => { S.tickers = t; updateTop(); renderWidgets(); }).catch(() => { }), 20000);
}
document.documentElement.dataset.theme = S.theme;
initCharts(); S.sig = localStorage.uq_sig !== '0'; setSig(S.sig); sizeCanvas(); loadDraw(); renderItv(); setPairUI(); setSide('LONG'); setMode('paper'); bind(); calcTrade(); renderRec('pp'); clockTick(); setInterval(clockTick, 1000);
setInterval(tick, 4000); setInterval(loadPaper, 8000);
boot();


/* ---------- 2026-10-18 K线全屏(复盘用)：图表卡片铺满窗口，并尽量请求浏览器原生全屏；F 切换，Esc 退出 ---------- */
function fsFit() {
  const on = $('#sec-chart').classList.contains('fs-on');
  const m = $('#chartMain');
  if (on) {
    const sub = $('#chartSub').children.length ? $('#chartSub').offsetHeight : 0;
    m.style.height = Math.max(300, window.innerHeight - m.getBoundingClientRect().top - sub - 16) + 'px';
  } else m.style.height = '';
  $('#fsBtn').textContent = on ? '✕ 退出全屏' : '⛶ 全屏';
  setTimeout(() => { sizeCanvas(); drawAll(); }, 60);
}
function toggleFs(force) {
  const el = $('#sec-chart'), on = force === undefined ? !el.classList.contains('fs-on') : force;
  el.classList.toggle('fs-on', on); document.body.style.overflow = on ? 'hidden' : '';
  try { if (on && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {}); if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => {}); } catch (e) { }
  el.scrollTop = 0; requestAnimationFrame(fsFit);
}
$('#fsBtn').addEventListener('click', e => { e.stopPropagation(); toggleFs(); });
document.addEventListener('fullscreenchange', fsFit);   // 2026-10-17 截图工具按Esc会退出浏览器原生全屏，但图表继续铺满窗口；退出只用按钮或F键
window.addEventListener('resize', () => { if ($('#sec-chart').classList.contains('fs-on')) fsFit(); });
document.addEventListener('keydown', e => {
  if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
  if (e.key === 'f' || e.key === 'F') { e.preventDefault(); toggleFs(); }
});
