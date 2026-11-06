/* 单品种复盘(2026-10-18)：K线 + 主方案每一笔开平仓 + 逐年 + 单独只做它 vs 买入持有。数据 = /api/universe/<SYM> */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const fmt = (v, d = 2) => v == null || isNaN(v) ? '--' : (+v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? '--' : (v >= 0 ? '+' : '') + fmt(v, d) + '%';
const cls = v => v == null ? '' : v >= 0 ? 'up' : 'dn';
const px = v => v == null ? '--' : fmt(v, v >= 1000 ? 1 : v >= 10 ? 2 : v >= 1 ? 3 : 5);
const day = ms => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`; };
const dday = d => day(d * 86400000);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const _f0 = window.fetch.bind(window);
window.fetch = async (u, o) => { let e; for (let k = 0; k < 4; k++) { try { return await _f0(u, o); } catch (x) { e = x; await new Promise(r => setTimeout(r, 600 * (k + 1))); } } throw e; };
const SYM = decodeURIComponent(location.pathname.split('/').filter(Boolean).pop() || '').toUpperCase();
const symName = s => s.endsWith('USDT') ? s.slice(0, -4) : s;
const COLORS = ['#007AFF', '#AF52DE', '#FF9500', '#34C759', '#FF2D55', '#5AC8FA', '#FFCC00', '#5856D6'];
const avColor = s => COLORS[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORS.length];
const LEGC = { '日线EMA8/30': null, '8h超级趋势': '#AF52DE', '8h布林突破': '#FF9500' };
window.__errs = []; window.addEventListener('error', e => window.__errs.push(e.message));
const S = { log: localStorage.uq_sym_log !== '0', d: null, idx: [], leg: localStorage.uq_sym_leg || '日线EMA8/30', rng: +(localStorage.uq_sym_rng || 1095), res: '', theme: localStorage.uq_theme || 'dark', byDay: {} };
let kc, cs, l8, l30, rotS, posS, eqc, eqS = {};

function opts() {
  const dark = S.theme === 'dark';
  return {
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: dark ? '#AEAEB2' : '#6E6E73', fontFamily: '-apple-system, Inter, sans-serif' },
    grid: { vertLines: { color: dark ? 'rgba(255,255,255,.03)' : 'rgba(0,0,0,.04)' }, horzLines: { color: dark ? 'rgba(255,255,255,.05)' : 'rgba(0,0,0,.06)' } },
    rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, rightOffset: 6 }, crosshair: { mode: 0 }, autoSize: true,
  };
}
function setTheme(t) { S.theme = t; document.documentElement.dataset.theme = t; localStorage.uq_theme = t; if (kc) { kc.applyOptions(opts()); eqc.applyOptions(opts()); drawK(); } }

async function load() {
  document.documentElement.dataset.theme = S.theme;
  const [r, ri] = await Promise.all([fetch('/api/universe/' + encodeURIComponent(SYM)), fetch('/api/universe/index')]);
  const j = await r.json(), ji = await ri.json();
  if (j.error) { $('#title').textContent = SYM; $('#sub').textContent = j.error; return; }
  S.d = j.data || j; S.idx = (ji.data || ji).symbols || [];
  document.title = `${symName(SYM)} 复盘 · 独角兽量化`;
  for (const f of [head, charts, drawK, drawEq, years, trades, bind]) { try { f(); } catch (e) { console.warn(f.name, e); } }
  // 图表有了宽度再设显示范围(后台标签页/刚加载时宽度是0)
  let ranged = false;
  new ResizeObserver(() => { if (!ranged && $('#kc').clientWidth > 0) { ranged = true; try { setRange(); } catch (e) { } } }).observe($('#kc'));
}

function head() {
  const d = S.d, so = d.solo || {}, bh = d.bh || {}, sr = d.solo_rot;
  $('#av').textContent = symName(SYM).slice(0, 2); $('#av').style.background = avColor(SYM);
  $('#title').innerHTML = `${symName(SYM)} <span class="px">${px(d.price)}</span> <span class="${cls(d.chg30)} small">30天 ${pct(d.chg30)}</span> <span class="${cls(d.chg365)} small">一年 ${pct(d.chg365)}</span>`;
  $('#sub').textContent = `${d.sector}${d.kind === 'crypto' ? ' · 币圈（日线EMA8/30 + 8h超级趋势 + 8h布林突破 各 1/3）' : ' · 美股（行业前5轮动 + 日线EMA8/30只做多 + 3×ATR止损）'} · 数据到 ${day(d.t)}${d.tradable ? '' : ' · 币安没有它的永续，主方案不交易，只做对照'}`;
  const b = [];
  if (d.pos) b.push(`<span class="bd ${d.pos.side === 'LONG' ? 'l' : 's'}">持${d.pos.side === 'LONG' ? '多' : '空'} · ${day(d.pos.entry_t)} @ ${px(d.pos.entry)} · ${pct(d.pos.ret)}</span>`);
  if (d.n_open > 1) b.push(`<span class="bd l">共 ${d.n_open} 条子腿持仓</span>`);
  b.push(`<span class="bd ${d.trend === '多头' ? 'tu' : 'td'}">日线 ${d.trend}</span>`);
  if (d.kind === 'stock') b.push(d.in_rot ? '<span class="bd rot">本月行业入选前5</span>' : '<span class="bd">本月行业未入选</span>');
  $('#badges').innerHTML = b.join('');
  if (d.pending) { $('#pendl').hidden = false; $('#pendl').textContent = '待执行：' + d.pending; }
  const K = [
    ['主方案交易', `${d.n}笔`, `每年 ${fmt(d.n_year, 1)} 笔 · 胜率 ${d.win ?? '--'}%`],
    ['平均每笔 / 贡献', `<span class="${cls(d.avg_ret)}">${pct(d.avg_ret, 2)}</span>`, `对总资金贡献 <b class="${cls(d.contrib)}">${pct(d.contrib)}</b>`],
    ['单独只做它', `<span class="${cls(so.cagr)}">${pct(so.cagr)}</span>/年`, `回撤 ${fmt(so.max_dd, 1)}% · 比 ${fmt(so.ratio)} · ${so.years ?? '--'}年${so.years && so.years < 3 ? '（样本太短）' : ''}`],
    sr ? ['只在行业前5时做', `<span class="${cls(sr.cagr)}">${pct(sr.cagr)}</span>/年`, `回撤 ${fmt(sr.max_dd, 1)}% · 比 ${fmt(sr.ratio)} · 入选月份占 ${d.rot_share}%`] : null,
    ['买入持有', `<span class="${cls(bh.cagr)}">${pct(bh.cagr)}</span>/年`, `最大回撤 ${fmt(bh.max_dd, 1)}% · 比 ${fmt(bh.ratio)}`],
  ].filter(Boolean);
  $('#kpis').innerHTML = K.map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s mut small">${s}</div></div>`).join('');
  const i = S.idx.findIndex(x => x[0] === SYM);
  $('#jump').innerHTML = S.idx.map(x => `<option value="${x[0]}" ${x[0] === SYM ? 'selected' : ''}>${symName(x[0])} · ${x[1]}${x[3] ? '' : '（不可交易）'}</option>`).join('');
  $('#prevSym').onclick = () => S.idx.length && (location.href = '/u/' + S.idx[(i - 1 + S.idx.length) % S.idx.length][0]);
  $('#nextSym').onclick = () => S.idx.length && (location.href = '/u/' + S.idx[(i + 1) % S.idx.length][0]);
  const wb = d.kind === 'crypto' ? SYM : (d.tradable ? SYM + 'USDT' : null);
  if (!wb) $('#wbLink').hidden = true;
  else $('#wbLink').onclick = () => { try { localStorage.uq_sym = wb; localStorage.uq_itv = '1d'; localStorage.uq_st = d.kind === 'crypto' ? 'plan_f_crypto' : 'plan_f_stock'; } catch (e) { } };
  const legs = d.kind === 'crypto' ? ['全部', '日线EMA8/30', '8h超级趋势', '8h布林突破'] : [];
  if (!legs.includes(S.leg)) S.leg = legs.length ? '日线EMA8/30' : '全部';
  $('#legSeg').hidden = !legs.length;
  $('#legSeg').innerHTML = legs.map(l => `<button data-v="${l}" class="${l === S.leg ? 'on' : ''}">${l === '日线EMA8/30' ? '日线腿' : l}</button>`).join('');
  $$('#rngSeg button').forEach(b => b.classList.toggle('on', +b.dataset.v === S.rng));
}

function charts() {
  kc = LightweightCharts.createChart($('#kc'), opts());
  rotS = kc.addHistogramSeries({ priceScaleId: 'rot', priceLineVisible: false, lastValueVisible: false, base: 0 });
  kc.priceScale('rot').applyOptions({ scaleMargins: { top: 0, bottom: 0 }, visible: false });
  posS = kc.addHistogramSeries({ priceScaleId: 'pos', priceLineVisible: false, lastValueVisible: false, base: 0 });
  kc.priceScale('pos').applyOptions({ scaleMargins: { top: 0.955, bottom: 0 }, visible: false });
  cs = kc.addCandlestickSeries({ upColor: '#34C759', downColor: '#FF3B30', borderVisible: false, wickUpColor: '#34C759', wickDownColor: '#FF3B30', priceLineVisible: false });
  l8 = kc.addLineSeries({ color: '#F5B83D', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  l30 = kc.addLineSeries({ color: '#4C82FB', lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
  kc.subscribeCrosshairMove(p => crossInfo(p));
  kc.priceScale('right').applyOptions({ mode: S.log ? 1 : 0 }); $('#logBtn').classList.toggle('on', S.log);
  eqc = LightweightCharts.createChart($('#eqc'), { ...opts(), rightPriceScale: { borderVisible: false, mode: 1 } });
}

function legTrades() { return S.d.trades.filter(t => S.leg === '全部' || t.sub === S.leg); }

function drawK() {
  const d = S.d, R = d.rows, dark = S.theme === 'dark';
  cs.setData(R.map(r => ({ time: day(r[0]), open: r[1], high: r[2], low: r[3], close: r[4] })));
  l8.setData(R.map(r => ({ time: day(r[0]), value: r[5] })));
  l30.setData(R.map(r => ({ time: day(r[0]), value: r[6] })));
  const rm = new Set(d.rot_months || []);
  rotS.setData(R.map(r => { const t = day(r[0]); return rm.has(t.slice(0, 7)) ? { time: t, value: 1, color: dark ? 'rgba(10,132,255,.13)' : 'rgba(0,122,255,.08)' } : { time: t }; }));
  const T = legTrades(), held = {};
  S.byDay = {};
  const dayIdx = R.map(r => r[0] - r[0] % 86400000);
  for (const t of T) {
    const a = t.entry_t - t.entry_t % 86400000, z = t.exit_t ? t.exit_t - t.exit_t % 86400000 : dayIdx[dayIdx.length - 1];
    if (t.sub === '日线EMA8/30' || d.kind === 'stock') for (const x of dayIdx) if (x >= a && x < z + (t.exit_t ? 0 : 1)) held[x] = t.side;
    (S.byDay[day(t.entry_t)] = S.byDay[day(t.entry_t)] || []).push({ k: 'in', t });
    if (t.exit_t && !t.open) (S.byDay[day(t.exit_t)] = S.byDay[day(t.exit_t)] || []).push({ k: 'out', t });
  }
  posS.setData(R.map((r, i) => { const s = held[dayIdx[i]]; return s ? { time: day(r[0]), value: 1, color: s === 'LONG' ? 'rgba(52,199,89,.65)' : 'rgba(255,59,48,.65)' } : { time: day(r[0]) }; }));
  const mk = [];
  for (const t of T) {
    const lc = LEGC[t.sub], L = t.side === 'LONG', daily = !lc;
    mk.push({ time: day(t.entry_t), position: L ? 'belowBar' : 'aboveBar', shape: L ? 'arrowUp' : 'arrowDown', color: lc || (L ? '#34C759' : '#FF3B30'), text: daily ? (L ? '多' : '空') : '', size: daily ? 1.2 : 0.8 });
    if (t.exit_t && !t.open) mk.push({ time: day(t.exit_t), position: L ? 'aboveBar' : 'belowBar', shape: 'circle', color: lc || ((t.ret || 0) > 0 ? '#30B0C7' : '#8E8E93'), text: daily ? pct(t.ret, 0) : '', size: daily ? 0.9 : 0.6 });
  }
  mk.sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0);
  cs.setMarkers(mk);
  $('#klgd').innerHTML = `<span><i class="ln k8"></i>EMA8</span><span><i class="ln k30"></i>EMA30</span><span><i class="tri u"></i>开多</span><span><i class="tri d"></i>开空</span><span><i class="dot c"></i>平仓(赚)</span><span><i class="dot g"></i>平仓(亏)</span>`
    + (d.kind === 'crypto' ? '<span><i class="dot" style="background:#AF52DE"></i>8h超级趋势</span><span><i class="dot" style="background:#FF9500"></i>8h布林突破</span>' : '<span><i class="box rot"></i>行业入选前5的月份</span>')
    + '<span><i class="box hold"></i>底部色条=持仓中</span>';
  $('#kSub').textContent = `${T.length} 笔${S.leg !== '全部' && d.kind === 'crypto' ? '（' + S.leg + '）' : ''}`;
  setRange();
}
function setRange() {
  const R = S.d.rows; if (!R.length || !$('#kc').clientWidth) return;
  if (!S.rng) { kc.timeScale().fitContent(); return; }
  const z = R[R.length - 1][0], a = z - S.rng * 86400000, i0 = R.findIndex(r => r[0] >= a);
  try { kc.timeScale().setVisibleLogicalRange({ from: Math.max(0, i0), to: R.length + 4 }); } catch (e) { kc.timeScale().fitContent(); }
}
function crossInfo(p) {
  if (!p || !p.time) { $('#kinfo').innerHTML = ''; return; }
  const b = p.seriesData.get(cs), e8 = p.seriesData.get(l8), e30 = p.seriesData.get(l30);
  if (b) $('#kinfo').innerHTML = `<b>${p.time}</b> 开 ${px(b.open)} 高 ${px(b.high)} 低 ${px(b.low)} 收 <b class="${b.close >= b.open ? 'up' : 'dn'}">${px(b.close)}</b> · <span style="color:#F5B83D">EMA8 ${px(e8 && e8.value)}</span> <span style="color:#4C82FB">EMA30 ${px(e30 && e30.value)}</span>`;
  const ev = S.byDay[p.time];
  if (ev) $('#evt').innerHTML = ev.map(evHtml).join('');
}
function evHtml({ k, t }) {
  const L = t.side === 'LONG';
  return k === 'in'
    ? `<div class="ev"><span class="bd ${L ? 'l' : 's'}">${day(t.entry_t)} 开${L ? '多' : '空'} @ ${px(t.entry)}</span><span class="mut small">${esc(t.sub)}</span> ${esc(t.entry_reason)}</div>`
    : `<div class="ev"><span class="bd ${(t.ret || 0) > 0 ? 'tu' : 'td'}">${day(t.exit_t)} 平${L ? '多' : '空'} @ ${px(t.exit)} · ${pct(t.ret, 2)} · ${t.days}天</span><span class="mut small">${esc(t.sub)}</span> ${esc(t.exit_reason)}</div>`;
}

function drawEq() {
  const d = S.d;
  for (const s of Object.values(eqS)) eqc.removeSeries(s); eqS = {};
  const add = (k, data, color, w, name) => { if (!data || !data.length) return; eqS[k] = eqc.addLineSeries({ color, lineWidth: w, priceLineVisible: false, lastValueVisible: true, title: '' }); const seen = new Set(); eqS[k].setData(data.filter(([x]) => !seen.has(x) && seen.add(x)).map(([x, v]) => ({ time: dday(x), value: Math.max(v, 1e-4) }))); };
  add('bh', d.bh_curve, '#8E8E93', 1.4); add('solo', d.solo_curve, '#007AFF', 2.4); if (d.solo_rot_curve) add('rot', d.solo_rot_curve, '#AF52DE', 1.8);
  eqc.timeScale().fitContent();
  const so = d.solo || {}, bh = d.bh || {}, sr = d.solo_rot;
  $('#eqLgd').innerHTML = `<span><i class="ln" style="background:#007AFF"></i>单独只做它 ${pct(so.cagr)}/年 · 回撤${fmt(so.max_dd, 1)}% · ${fmt(so.mult)}倍</span>`
    + (sr ? `<span><i class="ln" style="background:#AF52DE"></i>只在行业前5时做 ${pct(sr.cagr)}/年 · 回撤${fmt(sr.max_dd, 1)}%</span>` : '')
    + `<span><i class="ln" style="background:#8E8E93"></i>买入持有 ${pct(bh.cagr)}/年 · 回撤${fmt(bh.max_dd, 1)}% · ${fmt(bh.mult)}倍</span>`;
  $('#eqSub').textContent = `${dday(d.first_day)} 起，起点 = 1，对数刻度`;
}
function years() {
  const d = S.d, ys = [...new Set([...Object.keys(d.years || {}), ...Object.keys(d.solo_years || {}), ...Object.keys(d.bh_years || {})])].sort().reverse();
  $('#yTbl').innerHTML = '<thead><tr><th>年份</th><th>主方案笔数</th><th>胜率</th><th>对总资金贡献</th><th>单独只做它</th><th>买入持有</th></tr></thead><tbody>'
    + ys.map(y => { const v = (d.years || {})[y] || { n: 0, w: 0, contrib: 0 }, so = (d.solo_years || {})[y], bh = (d.bh_years || {})[y];
      return `<tr><td><b>${y}</b></td><td class="num">${v.n}</td><td class="num">${v.n ? Math.round(100 * v.w / v.n) + '%' : '--'}</td><td class="num ${cls(v.contrib)}">${v.n ? pct(v.contrib) : '--'}</td><td class="num ${cls(so)}">${pct(so)}</td><td class="num ${cls(bh)}">${pct(bh)}</td></tr>`; }).join('') + '</tbody>';
}
function trades() {
  const all = S.d.trades.slice().sort((a, b) => b.entry_t - a.entry_t);
  const L = all.filter(t => S.leg === '全部' || t.sub === S.leg).filter(t => !S.res || (S.res === 'o' ? t.open : S.res === 'w' ? !t.open && t.ret > 0 : !t.open && t.ret <= 0));
  const w = L.filter(t => !t.open && t.ret > 0).length, c = L.filter(t => !t.open).length;
  $('#tSub').textContent = `${L.length} 笔 · 胜率 ${c ? Math.round(100 * w / c) : '--'}% · 点任意一行在 K 线上定位`;
  $('#tTbl').innerHTML = '<thead><tr><th>#</th><th>子腿</th><th>方向</th><th>开仓</th><th>平仓</th><th>收益</th><th>贡献</th><th>天数</th><th>开仓原因</th><th>平仓原因</th></tr></thead><tbody>'
    + L.map((t, i) => `<tr data-i="${all.indexOf(t)}" class="${t.open ? 'open' : ''}"><td class="mut">${L.length - i}</td><td class="small">${esc(t.sub === '日线EMA8/30' ? '日线' : t.sub)}</td>
      <td><span class="bd ${t.side === 'LONG' ? 'l' : 's'}">${t.side === 'LONG' ? '多' : '空'}</span></td>
      <td class="small">${day(t.entry_t)}<div><b>${px(t.entry)}</b></div></td>
      <td class="small">${t.open ? '<span class="bd l">持仓中</span>' : day(t.exit_t)}<div>${t.open ? '现价 ' : ''}<b>${px(t.exit)}</b></div></td>
      <td class="num ${cls(t.ret)}"><b>${pct(t.ret, 2)}</b></td><td class="num ${cls(t.contrib)}">${pct(t.contrib, 2)}</td><td class="num">${t.days ?? '--'}</td>
      <td class="why">${esc(t.entry_reason)}</td><td class="why">${esc(t.exit_reason)}</td></tr>`).join('') + '</tbody>';
  S.all = all;
}
function focusTrade(t) {
  const a = t.entry_t - 25 * 86400000, z = (t.exit_t || S.d.t) + 25 * 86400000;
  const R = S.d.rows, i0 = R.findIndex(r => r[0] >= a), i1 = R.findIndex(r => r[0] >= z);
  try { kc.timeScale().setVisibleLogicalRange({ from: Math.max(0, i0), to: i1 < 0 ? R.length + 4 : i1 }); } catch (e) { }
  $('#evt').innerHTML = [{ k: 'in', t }].concat(t.exit_t && !t.open ? [{ k: 'out', t }] : []).map(evHtml).join('');
  $('#kCard').scrollIntoView({ behavior: 'smooth', block: 'start' });
}
function toggleFs(force) {
  const el = $('#kCard'), on = force === undefined ? !el.classList.contains('fs-on') : force;
  el.classList.toggle('fs-on', on); document.body.style.overflow = on ? 'hidden' : '';
  try { if (on && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => { }); if (!on && document.fullscreenElement) document.exitFullscreen().catch(() => { }); } catch (e) { }
  $('#fsBtn').textContent = on ? '✕ 退出全屏' : '⛶ 全屏';
}
function bind() {
  $('#themeBtn').onclick = () => setTheme(S.theme === 'dark' ? 'light' : 'dark');
  $('#jump').onchange = e => location.href = '/u/' + e.target.value;
  $('#legSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.leg = b.dataset.v; localStorage.uq_sym_leg = S.leg; $$('#legSeg button').forEach(x => x.classList.toggle('on', x === b)); drawK(); trades(); };
  $('#rngSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.rng = +b.dataset.v; localStorage.uq_sym_rng = S.rng; $$('#rngSeg button').forEach(x => x.classList.toggle('on', x === b)); setRange(); };
  $('#fRes').onchange = e => { S.res = e.target.value; trades(); };
  $('#tTbl').addEventListener('click', e => { const tr = e.target.closest('tr[data-i]'); if (!tr) return; $$('#tTbl tr.sel').forEach(x => x.classList.remove('sel')); tr.classList.add('sel'); focusTrade(S.all[+tr.dataset.i]); });
  $('#fsBtn').onclick = () => toggleFs();
  $('#logBtn').onclick = () => { S.log = !S.log; localStorage.uq_sym_log = S.log ? '1' : '0'; kc.priceScale('right').applyOptions({ mode: S.log ? 1 : 0 }); $('#logBtn').classList.toggle('on', S.log); };
  // 全屏只用 F 键或按钮退出(Esc 留给截图工具，不退出)
  document.addEventListener('keydown', e => {
    if (/INPUT|SELECT|TEXTAREA/.test(document.activeElement.tagName)) return;
    if (e.key === 'f' || e.key === 'F') toggleFs();
    if (e.key === 'ArrowLeft' && e.altKey) $('#prevSym').click();
    if (e.key === 'ArrowRight' && e.altKey) $('#nextSym').click();
  });
}
load();
