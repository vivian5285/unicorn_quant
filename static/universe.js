/* 全品种复盘总览(2026-10-18)：数据 = /api/universe(主方案回测同一份交易) */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const fmt = (v, d = 2) => v == null || isNaN(v) ? '--' : (+v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const pct = (v, d = 1) => v == null ? '--' : (v >= 0 ? '+' : '') + fmt(v, d) + '%';
const cls = v => v == null ? '' : v >= 0 ? 'up' : 'dn';
const px = v => v == null ? '--' : fmt(v, v >= 1000 ? 1 : v >= 10 ? 2 : v >= 1 ? 3 : 5);
const day = ms => { const d = new Date(ms); return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`; };
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const _f0 = window.fetch.bind(window);
window.fetch = async (u, o) => { let e; for (let k = 0; k < 4; k++) { try { return await _f0(u, o); } catch (x) { e = x; await new Promise(r => setTimeout(r, 600 * (k + 1))); } } throw e; };
const S = { d: null, kind: '', sector: '', q: '', pos: false, rot: false, tr: true, sort: 'contrib', dir: -1, tab: 'list', rtag: '', theme: localStorage.uq_theme || 'dark' };
try { Object.assign(S, JSON.parse(localStorage.uq_univ || '{}'), { theme: localStorage.uq_theme || 'dark' }); } catch (e) { }
const save = () => { try { localStorage.uq_univ = JSON.stringify({ kind: S.kind, sector: S.sector, pos: S.pos, rot: S.rot, tr: S.tr, sort: S.sort, dir: S.dir, tab: S.tab }); } catch (e) { } };
const symName = s => s.endsWith('USDT') ? s.slice(0, -4) : s;
const COLORS = ['#007AFF', '#AF52DE', '#FF9500', '#34C759', '#FF2D55', '#5AC8FA', '#FFCC00', '#5856D6'];
const avColor = s => COLORS[[...s].reduce((a, c) => a + c.charCodeAt(0), 0) % COLORS.length];

function setTheme(t) { S.theme = t; document.documentElement.dataset.theme = t; localStorage.uq_theme = t; render(); }

async function load() {
  setTheme(S.theme);
  const r = await fetch('/api/universe'); const j = await r.json();
  if (j.error) { $('#uTbl').innerHTML = `<tr><td class="mut">${esc(j.error)}</td></tr>`; return; }
  S.d = j.data || j;
  // 2026-11-01 实盘 = 方案F + 突破叠加2%：叠加腿逐币统计(回测 2022-03 起) + 叠加后的整体数字
  try { S.ov = await (await fetch('/static/overlay_by_symbol.json?v=' + Math.floor(Date.now() / 3.6e6))).json(); } catch (e) { S.ov = null; }
  try { S.ovs = await (await fetch('/api/optimal/overlay')).json(); } catch (e) { S.ovs = null; }
  const secs = [...new Set(S.d.symbols.map(s => s.sector))];
  $('#fSector').innerHTML = '<option value="">全部行业</option>' + secs.map(s => `<option ${s === S.sector ? 'selected' : ''}>${esc(s)}</option>`).join('');
  $('#asof').textContent = '数据更新于 ' + new Date(S.d.asof * 1000).toLocaleString('zh-CN', { hour12: false });
  const syms = S.d.symbols, pos = syms.filter(s => s.n_open), tr = syms.filter(s => s.tradable);
  const F = S.d.plan;
  $('#kpis').innerHTML = [
    ['品种', `${syms.length}`, `其中币安可交易 ${tr.length}`],
    ['回测交易', `${S.d.n_trades.toLocaleString()}笔`, '2017 起，三条币圈子腿 + 美股腿'],
    ['当前持仓', `${pos.length}个`, pos.slice(0, 6).map(s => symName(s.sym)).join('、') + (pos.length > 6 ? '…' : '')],
    S.ovs && S.ovs.FO2 ? ['实盘方案(F+叠加2%)', pct(S.ovs.FO2.cagr), `回撤 ${fmt(S.ovs.FO2.max_dd, 1)}% · 比 ${fmt(S.ovs.FO2.ratio)} · 方案F本身 ${pct(F.cagr)}`]
               : ['主方案', pct(F.cagr), `回撤 ${fmt(F.max_dd, 1)}% · 比 ${fmt(F.ratio)}`],
  ].map(([k, v, s]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s mut small">${esc(s)}</div></div>`).join('');
  $('#rotLine').innerHTML = `<span class="mut small">${S.d.rotation.month} 美股行业前 5：</span>` + S.d.rotation.selected.map((s, i) => `<button class="rchip ${S.sector === s ? 'on' : ''}" data-s="${esc(s)}">${i + 1}. ${esc(s)}</button>`).join('');
  $('#resCnt').textContent = S.d.research.length;
  bind(); render();
}

function rows() {
  let L = S.d.symbols.slice();
  if (S.kind) L = L.filter(s => s.kind === S.kind);
  if (S.sector) L = L.filter(s => s.sector === S.sector);
  if (S.pos) L = L.filter(s => s.n_open);
  if (S.rot) L = L.filter(s => s.kind === 'crypto' || s.in_rot);
  if (S.tr) L = L.filter(s => s.tradable);
  if (S.q) { const q = S.q.toUpperCase(); L = L.filter(s => s.sym.includes(q) || s.sector.includes(S.q)); }
  const key = {
    sym: s => s.sym, price: s => s.chg30, ov: s => ovOf(s.sym) ? ovOf(s.sym).contrib : -999, n: s => s.n, nyear: s => s.n_year, win: s => s.win ?? -1, avg: s => s.avg_ret ?? -99, contrib: s => s.contrib,
    solo: s => s.solo ? s.solo.cagr : -999, soloratio: s => s.solo ? s.solo.ratio ?? -9 : -9, bh: s => s.bh ? s.bh.cagr : -999, last: s => s.last ? s.last.entry_t : 0,
  }[S.sort] || (s => s.contrib);
  L.sort((a, b) => { const x = key(a), y = key(b); return (x > y ? 1 : x < y ? -1 : 0) * S.dir; });
  return L;
}

const COLS = [
  ['sym', '品种'], ['', '近一年走势 · 开平仓'], ['price', '现价 / 30天'], ['', '状态'],
  ['n', '交易笔数'], ['nyear', '每年'], ['win', '胜率'], ['avg', '平均每笔'], ['contrib', '对总资金贡献'], ['ov', '突破叠加贡献<div class="mut small">2022-03起</div>'],
  ['solo', '单独只做它 年化'], ['soloratio', '回撤 · 收益/回撤'], ['bh', '买入持有'], ['last', '最近一笔'],
];
function stateBadges(s) {
  const b = [];
  if (s.pos) b.push(`<span class="bd ${s.pos.side === 'LONG' ? 'l' : 's'}">持${s.pos.side === 'LONG' ? '多' : '空'} ${pct(s.pos.ret)}</span>`);
  else if (s.n_open) b.push(`<span class="bd l">持仓${s.n_open}</span>`);
  b.push(`<span class="bd ${s.trend === '多头' ? 'tu' : 'td'}">${s.trend || '--'}</span>`);
  if (s.kind === 'stock') b.push(s.in_rot ? '<span class="bd rot">本月入选</span>' : '');
  if (s.pending) b.push('<span class="bd pend" title="' + esc(s.pending) + '">待执行</span>');
  return b.join('');
}
function render() {
  if (!S.d) return;
  $$('#tabSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === S.tab));
  $('#pList').hidden = S.tab !== 'list'; $('#pRes').hidden = S.tab !== 'research';
  $$('#kindSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === S.kind));
  $$('#rotLine .rchip').forEach(b => b.classList.toggle('on', b.dataset.s === S.sector));
  $('#fPos').checked = S.pos; $('#fRot').checked = S.rot; $('#fTr').checked = S.tr; $('#fSector').value = S.sector;
  if (S.tab === 'research') return renderRes();
  const L = rows();
  $('#cnt').textContent = `${L.length} 个品种 · ${L.reduce((a, s) => a + s.n, 0).toLocaleString()} 笔`;
  const head = '<thead><tr>' + COLS.map(([k, n]) => `<th ${k ? `data-k="${k}" class="srt ${S.sort === k ? (S.dir > 0 ? 'asc' : 'desc') : ''}"` : ''}>${n}</th>`).join('') + '</tr></thead>';
  const body = L.map(s => {
    const so = s.solo || {}, bh = s.bh || {}, last = s.last;
    return `<tr class="${s.tradable ? '' : 'dim'}" data-s="${s.sym}">
      <td><a class="symc" href="/u/${s.sym}"><i class="av" style="background:${avColor(s.sym)}">${symName(s.sym).slice(0, 2)}</i><span><b>${symName(s.sym)}</b><em>${esc(s.sector)}${s.kind === 'crypto' ? ' · 币圈' : ''}</em></span></a></td>
      <td><canvas class="spk" width="150" height="38" data-s="${s.sym}"></canvas></td>
      <td><b>${px(s.price)}</b><div class="${cls(s.chg30)} small">${pct(s.chg30)}</div></td>
      <td class="bds">${stateBadges(s)}</td>
      <td class="num">${s.n}</td><td class="num mut">${fmt(s.n_year, 1)}</td>
      <td class="num">${s.win == null ? '--' : s.win + '%'}</td>
      <td class="num ${cls(s.avg_ret)}">${pct(s.avg_ret, 2)}</td>
      <td class="num"><div class="cbar"><i class="${s.contrib >= 0 ? 'p' : 'n'}" style="width:${Math.min(100, Math.abs(s.contrib) / maxC() * 100)}%"></i><b class="${cls(s.contrib)}">${pct(s.contrib)}</b></div></td>
      <td class="num">${ovCell(s.sym)}</td>
      <td class="num ${cls(so.cagr)}">${so.cagr == null ? '--' : pct(so.cagr)}${so.years && so.years < 3 ? `<div class="warn small">仅${so.years}年</div>` : ''}</td>
      <td class="num mut">${so.max_dd == null ? '--' : fmt(so.max_dd, 1) + '% · ' + fmt(so.ratio)}</td>
      <td class="num ${cls(bh.cagr)}">${bh.cagr == null ? '--' : pct(bh.cagr)}<div class="mut small">回撤${bh.max_dd == null ? '--' : fmt(bh.max_dd, 0) + '%'}</div></td>
      <td class="small">${last ? `${day(last.entry_t)}<div class="${last.open ? '' : cls(last.ret)}">${last.side === 'LONG' ? '多' : '空'} ${last.open ? '持仓中 ' : ''}${pct(last.ret)}</div>` : '<span class="mut">无</span>'}</td></tr>`;
  }).join('');
  $('#uTbl').innerHTML = head + '<tbody>' + body + '</tbody>';
  requestAnimationFrame(() => $$('#uTbl canvas.spk').forEach(c => spark(c, bySym(c.dataset.s), false)));
}
let _maxC = null; const maxC = () => _maxC ?? (_maxC = Math.max(1, ...S.d.symbols.map(s => Math.abs(s.contrib))));
const bySym = sym => S.d.symbols.find(s => s.sym === sym);

/* 迷你走势：收盘价线 + 开仓▲▼ + 平仓● */
function spark(cv, s, big) {
  const r = devicePixelRatio || 1, W = cv.clientWidth || cv.width, H = cv.clientHeight || cv.height;
  cv.width = W * r; cv.height = H * r; const cx = cv.getContext('2d'); cx.setTransform(r, 0, 0, r, 0, 0); cx.clearRect(0, 0, W, H);
  const P = s.spark; if (!P || P.length < 2) return;
  const t0 = P[0][0], t1 = P[P.length - 1][0], vs = P.map(p => p[1]).concat(s.marks.map(m => m[2]));
  let lo = Math.min(...vs), hi = Math.max(...vs); if (hi === lo) hi = lo * 1.01 + 1e-9;
  const pad = big ? 10 : 4, X = t => pad + (t - t0) / (t1 - t0 || 1) * (W - 2 * pad), Y = v => H - pad - (v - lo) / (hi - lo) * (H - 2 * pad);
  const dark = document.documentElement.dataset.theme === 'dark', up = P[P.length - 1][1] >= P[0][1];
  const g = cx.createLinearGradient(0, 0, 0, H); g.addColorStop(0, up ? 'rgba(52,199,89,.28)' : 'rgba(255,59,48,.22)'); g.addColorStop(1, 'rgba(0,0,0,0)');
  cx.beginPath(); P.forEach((p, i) => i ? cx.lineTo(X(p[0]), Y(p[1])) : cx.moveTo(X(p[0]), Y(p[1])));
  cx.lineTo(X(t1), H); cx.lineTo(X(t0), H); cx.closePath(); cx.fillStyle = g; cx.fill();
  cx.beginPath(); P.forEach((p, i) => i ? cx.lineTo(X(p[0]), Y(p[1])) : cx.moveTo(X(p[0]), Y(p[1])));
  cx.strokeStyle = up ? '#34C759' : '#FF3B30'; cx.lineWidth = big ? 1.8 : 1.3; cx.stroke();
  const ms = big ? s.marks : s.marks.filter(m => m[3] === '日线' || s.kind === 'stock');
  for (const [t, k, v, sub] of ms) {
    if (t < t0) continue;
    const x = X(t), y = Y(v), sz = big ? 5 : 3.4, daily = sub === '日线';
    cx.globalAlpha = daily || s.kind === 'stock' ? 1 : .55;
    if (k === 0) { cx.beginPath(); cx.arc(x, y, sz * .7, 0, 7); cx.fillStyle = dark ? '#E5E5EA' : '#3A3A3C'; cx.fill(); }
    else { cx.beginPath(); if (k > 0) { cx.moveTo(x, y - sz); cx.lineTo(x - sz, y + sz * .8); cx.lineTo(x + sz, y + sz * .8); } else { cx.moveTo(x, y + sz); cx.lineTo(x - sz, y - sz * .8); cx.lineTo(x + sz, y - sz * .8); } cx.closePath(); cx.fillStyle = k > 0 ? '#34C759' : '#FF3B30'; cx.fill(); }
  }
  cx.globalAlpha = 1;
}

/* 悬停卡片 */
let hovSym = null, hovT = null;
function showHov(sym, e) {
  const s = bySym(sym); if (!s) return; const el = $('#hov');
  if (hovSym !== sym) {
    hovSym = sym;
    const so = s.solo || {}, sr = s.solo_rot, bh = s.bh || {};
    const yrs = Object.entries(s.years || {}).slice(-8), mx = Math.max(1, ...yrs.map(([, v]) => Math.abs(v.contrib)));
    const legs = Object.entries(s.legs || {}).map(([k, v]) => `<span>${esc(k)} <b>${v.n}</b>笔 <em class="${cls(v.contrib)}">${pct(v.contrib)}</em></span>`).join('');
    el.innerHTML = `<div class="hh"><i class="av" style="background:${avColor(s.sym)}">${symName(s.sym).slice(0, 2)}</i><div><b>${symName(s.sym)}</b> <span class="mut small">${esc(s.sector)}</span><div class="small"><b>${px(s.price)}</b> <span class="${cls(s.chg30)}">30天 ${pct(s.chg30)}</span> · <span class="${cls(s.chg365)}">一年 ${pct(s.chg365)}</span></div></div><span class="sp"></span><div class="bds">${stateBadges(s)}</div></div>
      <canvas class="bigspk"></canvas>
      <div class="lgd small mut"><span><i class="tri u"></i>开多</span><span><i class="tri d"></i>开空</span><span><i class="dot"></i>平仓</span>${s.kind === 'crypto' ? '<span>半透明 = 8h 子腿</span>' : ''}</div>
      <div class="hg">
        <div><em>主方案交易</em><b>${s.n} 笔</b><span>胜率 ${s.win ?? '--'}% · 平均 ${pct(s.avg_ret, 2)}</span></div>
        <div><em>对总资金贡献</em><b class="${cls(s.contrib)}">${pct(s.contrib)}</b><span>最好 ${pct(s.best)} · 最差 ${pct(s.worst)}</span></div>
        <div><em>单独只做它</em><b class="${cls(so.cagr)}">${so.cagr == null ? '--' : pct(so.cagr) + '/年'}</b><span>回撤 ${fmt(so.max_dd, 1)}% · 比 ${fmt(so.ratio)}${so.years ? ' · ' + so.years + '年' : ''}</span></div>
        <div><em>${sr ? '只在行业前5时做' : '买入持有'}</em><b class="${cls((sr || bh).cagr)}">${pct((sr || bh).cagr)}/年</b><span>回撤 ${fmt((sr || bh).max_dd, 1)}%${sr ? ' · 买入持有 ' + pct(bh.cagr) : ''}</span></div>
      </div>
      ${legs ? `<div class="legs small">${legs}</div>` : ''}
      <div class="yb">${yrs.map(([y, v]) => `<div title="${y}：${v.n}笔，贡献 ${pct(v.contrib)}"><i class="${v.contrib >= 0 ? 'p' : 'n'}" style="height:${Math.max(2, Math.abs(v.contrib) / mx * 34)}px"></i><span>${String(y).slice(2)}</span></div>`).join('')}<em class="mut small">逐年贡献</em></div>
      ${s.pending ? `<div class="pendl small">待执行：${esc(s.pending)}</div>` : ''}
      <div class="go small">点击查看完整 K 线和每笔开平仓原因 →</div>`;
    el.hidden = false;
    spark($('canvas.bigspk', el), s, true);
  }
  el.hidden = false;
  const W = el.offsetWidth, H = el.offsetHeight; let x = e.clientX + 18, y = e.clientY + 14;
  if (x + W > innerWidth - 8) x = e.clientX - W - 18; if (y + H > innerHeight - 8) y = Math.max(8, innerHeight - H - 8);
  el.style.left = Math.max(8, x) + 'px'; el.style.top = y + 'px';
}
function hideHov() { $('#hov').hidden = true; hovSym = null; }

/* 研究结论 */
const VCLS = { '采用': 'ok', '维持现行': 'keep', '不改': 'keep', '放弃': 'no', '否决': 'no', '不采用': 'no', '观察': 'watch' };
function renderRes() {
  $$('#resSeg button').forEach(b => b.classList.toggle('on', b.dataset.v === S.rtag));
  const L = S.d.research.map((r, i) => [r, i]).sort((a, b) => String(b[0].date).localeCompare(String(a[0].date)) || b[1] - a[1]).map(x => x[0]).filter(r => !S.rtag || r.tag === S.rtag);  // 按日期倒序, 同一天后写的在前
  $('#resBody').innerHTML = L.map(r => `<article class="rcard">
    <div class="rh"><span class="mut small">${esc(r.date)}</span><span class="tagc">${esc(r.tag)}</span><span class="vd ${VCLS[r.verdict] || (Object.keys(VCLS).find(k => String(r.verdict).startsWith(k)) ? VCLS[Object.keys(VCLS).find(k => String(r.verdict).startsWith(k))] : 'keep')}">${esc(r.verdict)}</span></div>
    <h3>${esc(r.title)}</h3>
    <p class="rq"><b>问题</b>${esc(r.q)}</p>
    <p class="ra"><b>结论</b>${esc(r.a)}</p>
    ${r.rows && r.rows.length ? `<div class="tbl-wrap"><table class="t rt"><thead><tr>${r.rows[0].map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${r.rows.slice(1).map((row, i) => `<tr class="${i === 0 ? 'base' : ''}">${row.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>` : ''}
  </article>`).join('') || '<div class="mut">没有</div>';
}

function bind() {
  $('#themeBtn').onclick = () => setTheme(S.theme === 'dark' ? 'light' : 'dark');
  $('#tabSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.tab = b.dataset.v; save(); render(); };
  $('#kindSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.kind = b.dataset.v; save(); render(); };
  $('#resSeg').onclick = e => { const b = e.target.closest('button'); if (!b) return; S.rtag = b.dataset.v; renderRes(); };
  $('#fSector').onchange = e => { S.sector = e.target.value; save(); render(); };
  $('#rotLine').onclick = e => { const b = e.target.closest('.rchip'); if (!b) return; S.sector = S.sector === b.dataset.s ? '' : b.dataset.s; S.kind = S.sector ? 'stock' : S.kind; save(); render(); };
  $('#fPos').onchange = e => { S.pos = e.target.checked; save(); render(); };
  $('#fRot').onchange = e => { S.rot = e.target.checked; save(); render(); };
  $('#fTr').onchange = e => { S.tr = e.target.checked; save(); render(); };
  $('#fQ').oninput = e => { S.q = e.target.value.trim(); render(); };
  $('#uTbl').addEventListener('click', e => {
    const th = e.target.closest('th[data-k]');
    if (th) { const k = th.dataset.k; S.dir = S.sort === k ? -S.dir : (k === 'sym' ? 1 : -1); S.sort = k; save(); render(); return; }
    const tr = e.target.closest('tr[data-s]'); if (tr && !e.target.closest('a')) location.href = '/u/' + tr.dataset.s;
  });
  $('#uTbl').addEventListener('mousemove', e => {
    const tr = e.target.closest('tr[data-s]'); if (!tr) return hideHov();
    clearTimeout(hovT); const sym = tr.dataset.s, ev = { clientX: e.clientX, clientY: e.clientY };
    if (hovSym === sym) showHov(sym, ev); else hovT = setTimeout(() => showHov(sym, ev), 120);
  });
  $('#uTbl').addEventListener('mouseover', e => { const tr = e.target.closest('tr[data-s]'); if (tr && hovSym !== tr.dataset.s) { clearTimeout(hovT); const ev = { clientX: e.clientX, clientY: e.clientY }; hovT = setTimeout(() => showHov(tr.dataset.s, ev), 120); } });
  $('#uTbl').addEventListener('mouseleave', () => { clearTimeout(hovT); hideHov(); });
  addEventListener('scroll', hideHov, { passive: true });
  document.addEventListener('keydown', e => { if (e.key === '/' && document.activeElement !== $('#fQ')) { e.preventDefault(); $('#fQ').focus(); } });
}
load();


/* 2026-11-01 突破叠加2%(真假突破4h两条 + 布林擠壓8h)，只在主流5币上跑；小账户(<5000U)实盘不跟 BTC */
const ovOf = sym => (S.ov && S.ov.by_symbol && S.ov.by_symbol[sym]) || null;
function ovCell(sym) {
  const o = ovOf(sym); if (!o) return '<span class="mut">--</span>';
  const tip = Object.entries(o.legs).map(([k, v]) => `${k}: ${v.n}笔 胜率${v.win}% 贡献${v.contrib > 0 ? '+' : ''}${v.contrib}%`).join('\n');
  return `<span title="${esc(tip)}"><b class="${cls(o.contrib)}">${pct(o.contrib)}</b><div class="mut small">${o.n}笔 · 胜率${o.win}%${sym === 'BTCUSDT' ? ' · 小账户不跟' : ''}</div></span>`;
}
