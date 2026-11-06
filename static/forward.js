/* Public PAPER data only. There are deliberately no order / mutation controls. */
const $ = s => document.querySelector(s), esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let data, group='combo', selected='combo_base', chart, series=[];
const num = (v,d=2) => v == null ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d});
const sign = v => `<span class="${v>=0?'positive':'negative'}">${v>=0?'+':''}${num(v)}</span>`;
function render(){
  const rows=data.profiles.filter(p=>p.group===group).sort((a,b)=>(b.latest?.equity??-Infinity)-(a.latest?.equity??-Infinity));
  if(!rows.some(p=>p.id===selected)) selected=rows[0]?.id;
  $('#rows').innerHTML=rows.map((p,i)=>{const x=p.latest||{};return `<tr data-id="${esc(p.id)}" class="${p.id===selected?'selected':''}"><td>${i+1}. ${esc(p.label)}</td><td>${num(x.equity)}</td><td>${x.return_pct==null?'—':sign(x.return_pct)+'%'}</td><td>${num(x.max_dd_pct)}%</td><td>${num(x.fees,4)}</td><td>${x.funding==null?'—':sign(x.funding)}</td><td>${num(x.gross_multiple)} / ${p.gross_cap}×</td><td>${esc(p.sample)} · ${p.fill_events}笔成交</td></tr>`}).join('');
  $('#basis').textContent=group==='combo'?'同源非布林腿 · 净名义上限2倍':'仅价格策略 · 净名义上限1倍 · 无加仓';
  const active=rows.find(p=>p.id===selected), x=active?.latest;
  $('#selected').textContent=active?.label||'';
  const positions=x?.positions||{}, legs=x?.virtual_legs||[];
  $('#positions').innerHTML=Object.entries(positions).map(([s,p])=>`<tr><td>${esc(s)}</td><td>${num(p.qty,6)}</td><td>${num(p.entry,4)}</td><td>${legs.filter(l=>l.symbol===s).length}</td></tr>`).join('')||'<tr><td colspan="4">空仓</td></tr>';
  $('#skipped').textContent=(x?.skipped||[]).join(' · ');
  if(chart){series.forEach(s=>chart.removeSeries(s));series=[];rows.forEach((p,i)=>{const s=chart.addLineSeries({color:['#1187a3','#35a36e','#ba7644'][i%3],lineWidth:2,title:p.label,priceLineVisible:false});const points=new Map();p.curve.forEach(([t,v])=>points.set(Math.floor(t/1000),v));s.setData([...points].map(([time,value])=>({time,value})));series.push(s)});chart.timeScale().fitContent()}
}
document.addEventListener('click',e=>{const b=e.target.closest('[data-group]');if(b){group=b.dataset.group;document.querySelectorAll('[data-group]').forEach(x=>x.classList.toggle('on',x===b));render()}const r=e.target.closest('[data-id]');if(r){selected=r.dataset.id;render()}});
async function load(){try{const r=await fetch('/static/forward-data/forward.json',{cache:'no-store'});if(!r.ok)throw Error('前向数据尚未生成');data=await r.json();$('#born').textContent=new Date(data.started).toLocaleString('zh-CN',{hour12:false});$('#updated').textContent='更新 '+new Date(data.ts).toLocaleString('zh-CN',{hour12:false});$('#costs').textContent=`假设手续费${data.fee_per_side*10000}bp / 单边 · 盘口外滑点${data.slippage_per_side*10000}bp`;$('#health').textContent=data.health.join(' · ')||(Date.now()-data.ts>900000?'状态超过15分钟未更新':'观察中 · 非实盘收益');render()}catch(e){$('#health').textContent=e.message}}
if(typeof LightweightCharts!=='undefined')chart=LightweightCharts.createChart($('#curve'),{autoSize:true,layout:{background:{color:'#f6f7f8'},textColor:'#66727b'},grid:{vertLines:{color:'#e8ebed'},horzLines:{color:'#e8ebed'}},timeScale:{timeVisible:true},rightPriceScale:{borderVisible:false}});
load();setInterval(load,60000);
