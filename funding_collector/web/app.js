'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name="session-token"]').content;
let chosenAssets = new Set(), catalogStamp = '';
let state = null, filter = 'all', selectedKey = null, detailBusy = false, busy = false;
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v, digits=2) => v == null ? '—' : Number(v).toLocaleString('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits});
const price = v => v == null ? '—' : Number(v).toLocaleString('en-US', {maximumSignificantDigits:9});
const pct = v => v == null ? '—' : (v>=0?'+':'') + num(v*100,4)+'%';
const time = v => v ? new Date(v).toLocaleString('zh-CN',{hour12:false}) : '—';
const ago = v => v == null ? '—' : v<60 ? `${Math.floor(v)} 秒` : v<3600 ? `${Math.floor(v/60)} 分钟` : `${num(v/3600,1)} 小时`;
const names = {binance:'Binance',okx:'OKX',bybit:'Bybit',gate:'Gate'};
const states = {READY:'研究候选',WAIT:'等待',REJECT:'未达门槛',EMERGENCY:'数据受阻',HOLD:'持有',EXIT:'退出'};
const reasons = {
 'insufficient time for entry and settlement buffer':'剩余时间不足以完成入场及结算保护',
 'snapshot stale':'快照已过期，等待下一轮更新',
 'API latency exceeds limit':'请求耗时超过设定阈值',
 'no unambiguous forecast for target event':'目标事件的预测含义尚未明确，暂不筛选',
 'estimated net below research threshold':'扣除情景成本后，净收益未达到门槛',
 'spot-long / perp-short requires positive funding':'当前结构需要正资金费率',
 'waiting for updated funding schedule':'等待交易所更新结算时间',
 'insufficient sampled depth':'已采集的盘口深度不足',
 'positive estimate; outside research observation window':'收益情景为正，尚未进入观察窗口',
 'inside research settlement guard; eligibility unverified':'接近结算，尚不能确认收付资格',
 'research candidate only; execution risk checks not implemented':'通过研究筛选；执行与仓位风险尚未验证',
 'fee assumptions not configured':'尚未设置手续费假设'
};
function reason(r){if(reasons[r]) return reasons[r];if(r.includes('stale / clock skew'))return (r.includes('funding')?'Funding':r.includes('oi')?'OI':r.includes('mark')?'Mark':'源数据')+' 时间戳超出新鲜度或时钟偏差门槛';if(r.includes('response stale'))return '部分接口响应已过期';if(r.includes('HTTP_451')||r.includes('HTTP_403'))return '接口访问被限制，请检查网络出口';if(r.includes('NETWORK'))return '网络请求失败，等待后续采集恢复';return r;}
function badge(d){return d?`<span class="status ${esc(d.state)}">${esc(d.state)} · ${esc(states[d.state]||d.state)}</span>`:'<span class="status">等待采集</span>';}
function isCandidate(m){const d=m.decision;return d && (d.state==='READY'||d.state==='WAIT'&&d.net_bps>0);}
function toast(text){$('toast').textContent=text;$('toast').classList.remove('hidden');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.add('hidden'),4500);}
async function api(path, data){const r=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Session-Token':token},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw new Error(result.error||'请求失败');return result;}
function scanMessage(s){
 const scan=s.scanner;
 if(s.error)return '采集已中断：'+s.error;
 if(s.stopping)return '正在停止，等待在途请求结束。首轮未完成时可能尚无完整快照。';
 if(!s.running&&!scan.updated_at_ms)return `已停止；已读取 ${scan.observed} 个市场费率，但尚未取得完整快照。点击开始后可查看各交易所进度。`;
 if(!scan.updated_at_ms)return `正在进行首轮费率初筛，已读取 ${scan.observed} 个市场。各交易所独立扫描；有候选后即开始盘口核验，无需等待其他交易所。`;
 if(s.running&&s.cycles===0)return `初筛已完成，正在核验 ${scan.evaluated} 个候选的盘口和成本；完整快照会逐个保存。`;
 return '暂无通过完整核验的正净收益候选。可在下方观察列表查看成本、深度、数据过期等原因；空榜不等于没有采集数据。';
}
function render(){
 if(!state)return;
 $('auto-panel').classList.toggle('hidden',state.demo);
 renderScanner();
 const s=state, candidates=s.markets.filter(isCandidate).sort((a,b)=>(b.decision.net_bps||0)-(a.decision.net_bps||0)), alerts=s.markets.filter(m=>m.decision?.anomaly);
 $('mode').textContent=s.demo?'合成演示 · 无真实行情':'实时数据 · 只读研究';
 $('clock').textContent=new Date(s.now_ms).toLocaleTimeString('zh-CN',{hour12:false});
 $('run-state').textContent=s.stopping?'正在停止':s.running?'采集中':'已停止';
 $('run-caption').textContent=s.stopping?'等待在途请求完成并保存':s.running?(s.auto?'全市场费率初筛 → 前列完整采集':`常规 ${s.settings.poll_seconds}s / 事件期 ${s.settings.event_poll_seconds}s`):s.demo?'离线合成数据，点击开始演示':'一键扫描全市场，无需选择币种';
 $('toggle').textContent=s.stopping?'正在停止…':s.running?'Ⅱ 停止采集':s.demo?'▶ 开始合成演示':'▶ 开始全市场自动观察';
 $('toggle').disabled=busy||s.stopping;
 const fresh=s.markets.filter(m=>m.connection==='CONNECTED'&&m.age_seconds<=s.settings.max_age_seconds).length;
 $('connected').innerHTML=`${fresh}<span> / ${s.markets.length}</span>`;
 $('qualified').textContent=candidates.length;$('saved').textContent=num(s.total,0);
 $('saved-caption').textContent=`本次启动已完成 ${s.cycles} 轮 · 历史数据持续保留`;
 $('export').disabled=s.total===0;
 $('fatal').classList.toggle('hidden',!s.error);$('fatal').textContent=s.error?`采集已中断：${s.error}`:'';
 $('venues').innerHTML=s.selected.exchanges.map(e=>{
  const rows=s.markets.filter(m=>m.exchange===e), fresh=rows.filter(m=>m.connection==='CONNECTED'&&m.age_seconds<=s.settings.max_age_seconds).length;
  const failed=rows.some(m=>['DISCONNECTED','DEGRADED'].includes(m.connection));
  const label=!rows.length?(s.auto?'等待深入采集':'无匹配市场'):failed?'访问异常':fresh===rows.length?'近期连通':rows.some(m=>m.decision)?'等待刷新':'未检测';
  return `<div class="venue"><span class="venue-mark">${esc(names[e][0])}</span><strong>${esc(names[e])}</strong><span class="venue-status"><i class="small-dot ${failed?'amber':''}"></i>${label}</span></div>`;
 }).join('');
 $('op-count').textContent=candidates.length;
 $('opportunities').innerHTML=candidates.length?candidates.slice(0,5).map(m=>`<button class="op-item" data-key="${esc(m.key)}"><div><strong>${esc(m.symbol)} <span class="status ${esc(m.decision.state)}">${esc(m.decision.state)}</span></strong><small>${esc(names[m.exchange])} · 情景净收益</small></div><div class="op-return">+${num(m.decision.estimated_pnl)}<small>USDT · ${num(m.decision.net_bps)} bps</small></div></button>`).join(''):`<div class="empty"><div class="empty-icon">⌁</div><strong>${s.running?'等待值得研究的机会':s.total?'历史记录已保留，等待新一轮观察':'从第一轮市场观察开始'}</strong><p>${s.auto?esc(scanMessage(s)):(s.running?'暂无通过当前筛选的候选。请查看观察列表中的判断原因。':s.demo?'点击开始合成演示，查看判断、成本依据和原始快照。':'点击开始全市场自动观察。程序自动发现标的、选择候选并核验完整证据。')}</p></div>`;
 $('alert-count').textContent=alerts.length;
 $('alerts').innerHTML=alerts.length?alerts.slice(0,3).map(m=>`<button class="alert-item" data-key="${esc(m.key)}"><b>${esc(names[m.exchange])} · ${esc(m.symbol)}</b><span class="alert-label">${m.age_seconds>s.settings.max_age_seconds?'等待刷新':'需留意'}</span><p>${esc(reason(m.decision.reasons[0]||'数据异常'))}</p></button>`).join('')+(alerts.length>3?`<div class="panel-footer">另有 ${alerts.length-3} 个市场异常，在观察列表中查看</div>`:''):`<div class="empty"><div class="empty-icon">✓</div><strong>目前没有已识别的异常</strong><p>发生访问失败、数据过期或延迟时，会在这里显示。</p></div>`;
 const visible=s.markets.filter(m=>filter==='all'||filter==='focus'&&isCandidate(m)||filter==='anomaly'&&m.decision?.anomaly);
 $('market-rows').innerHTML=visible.length?visible.map(m=>{
   let countdown='—';if(m.next_funding_time_ms){const t=(m.next_funding_time_ms-s.now_ms)/1000;countdown=t>0?`${Math.floor(t/3600)}h ${Math.floor(t%3600/60)}m`:'等待更新';}
   return `<tr data-key="${esc(m.key)}"><td><strong>${esc(m.symbol)}</strong><small>${esc(names[m.exchange])}</small></td><td class="${m.current_rate>=0?'positive':'negative'}">${pct(m.current_rate)}</td><td>${countdown}<small>${m.interval_seconds?num(m.interval_seconds/3600,0)+' 小时周期':'周期未获取'}</small></td><td>${badge(m.decision)}</td><td>${ago(m.age_seconds)}</td><td><button class="row-button" data-key="${esc(m.key)}" ${m.decision?'':'disabled'} aria-label="查看 ${esc(names[m.exchange])} ${esc(m.symbol)} 详情">↗</button></td></tr>`;
 }).join(''):'<tr><td colspan="6" class="muted">当前筛选下没有市场。</td></tr>';
 $('last-update').textContent=s.last_cycle_ms?'最近完成一轮：'+time(s.last_cycle_ms):'启动后尚未完成采集 · 历史快照按实际年龄显示';
}
function renderScanner(){
 const s=state.scanner;
 if(state.running&&state.auto){for(const [id,key] of [['auto-capital','capital_budget_usdt'],['auto-annual','annual_cost_pct'],['auto-entry','entry_execution_seconds'],['auto-exit','exit_buffer_seconds']])$(id).value=s.options[key];$('auto-threshold').value=s.options.threshold_pct;$('auto-basis').value=s.options.basis;$('auto-limit').value=s.options.limit;}
 $('auto-start').disabled=state.running||state.catalog.loading||state.demo;
 for(const id of ['auto-threshold','auto-basis','auto-limit','auto-capital','auto-annual','auto-entry','auto-exit'])$(id).disabled=state.running;
 const net=s.options.basis==='net';
 $('scan-score-title').textContent=net?'情景净收益 ↓':'初筛费率 ↓';$('scan-time-title').textContent=net?'等待入场 / 占用':'下一结算';
 $('scan-status').textContent=state.auto?`${state.running?'自动观察中':'自动观察已停止'} · 本轮读取 ${s.observed} 个市场 · ${net?'深入候选 '+s.evaluated+' 个 / 当前合格 '+s.rows.length+' 个':'超过费率门槛 '+s.rows.length+' 个'}${s.updated_at_ms?' · 最近扫描 '+time(s.updated_at_ms):(state.running?' · 首轮初筛尚未完成':' · 首轮未完成即停止')}`:'启动后自动扫描。净收益需完成盘口核验，数据不足、成本不合格或入场时间不足不入榜。';
 if(state.auto&&s.venues)$('scan-status').textContent+=' · '+Object.entries(s.venues).map(([e,v])=>names[e]+({scanning:'扫描中',ready:'已返回',error:'部分失败'}[v]||v)).join(' / ');
 $('scan-errors').textContent=Object.entries(s.errors).map(([e,v])=>`${names[e]}：${v}`).join('；');
 $('scan-rows').innerHTML=s.rows.length?s.rows.map((r,i)=>`<tr ${net?`data-key="${esc(r.exchange+':'+r.symbol)}"`:''}><td><strong>#${i+1} ${esc(r.symbol)}</strong><small>${esc(names[r.exchange])}${r.semantics==='latest_unconfirmed'?' · 预测未确认':''}</small></td><td class="positive">${net?'+'+num(r.net_usdt)+' USDT':pct(r.score)+(s.options.basis==='hour'?'/h':'')}${net?`<small>资金时间成本 ${num(r.evidence.capital_time_cost_usdt,4)} USDT</small>`:''}</td><td>${pct(r.rate)}<small>${r.interval_seconds?num(r.interval_seconds/3600,1)+' 小时':'周期未知'}</small></td><td>${net?ago(r.evidence.entry_wait_seconds)+' / '+ago(r.evidence.occupied_seconds):esc(time(r.next_funding_time_ms))}${net?'<small>当前预测可能变化 · 点击查看成本</small>':''}</td><td>${ago((state.now_ms-r.observed_ms)/1000)}</td></tr>`).join(''):'<tr><td colspan="5" class="muted">'+esc(net?(state.auto?scanMessage(state):'点击开始全市场自动观察，先读取费率，再核验盘口和成本。'):'当前没有新鲜且超过门槛的数据。')+'</td></tr>';

}
async function refresh(){try{state=await api('/api/state');$('offline').classList.add('hidden');render();if($('settings').open)renderAssets();}catch(e){$('offline').classList.remove('hidden');$('toggle').disabled=true;}}
async function control(path,data={}){if(busy)return;busy=true;try{await api(path,data);await refresh();}catch(e){toast(e.message);}finally{busy=false;render();}}
function autoOptions(){return {threshold_pct:Number($('auto-threshold').value),basis:$('auto-basis').value,limit:Number($('auto-limit').value),capital_budget_usdt:Number($('auto-capital').value),annual_cost_pct:Number($('auto-annual').value),entry_execution_seconds:Number($('auto-entry').value),exit_buffer_seconds:Number($('auto-exit').value)};}
function startAutomatic(){if(state.demo)return control('/api/start',state.selected);if($('auto-form').reportValidity())return control('/api/auto/start',autoOptions());}
$('toggle').addEventListener('click',()=>state.running?control('/api/stop'):startAutomatic());
$('auto-form').addEventListener('submit',e=>{e.preventDefault();startAutomatic();});
$('export').addEventListener('click',()=>{window.location.href='/api/export';});
document.querySelectorAll('[data-filter]').forEach(b=>b.addEventListener('click',()=>{filter=b.dataset.filter;document.querySelectorAll('[data-filter]').forEach(el=>el.classList.toggle('active',el===b));render();}));
function field(label,value){return `<div class="detail-field"><small>${esc(label)}</small><b>${esc(value)}</b></div>`;}
async function detail(key){
 selectedKey=key;$('detail').showModal();$('detail-title').textContent='加载研究依据…';$('detail-body').textContent='正在读取最近一份快照';
 if(detailBusy)return;detailBusy=true;
 try{const {snapshot:s,decision:d}=await api('/api/detail?key='+encodeURIComponent(key));if(selectedKey!==key)return;
 $('detail-title').textContent=`${names[s.exchange]} · ${s.symbol}`;
 const ev=d.evidence||{};
 $('detail-body').innerHTML=`<div class="detail-state">${badge(d)}<p>${[...new Set(d.reasons.map(reason))].map(esc).join('<br>')}</p></div><p class="muted fine">快照 ${esc(time(s.collected_at_ms))} · 以下为打开详情时的静态证据</p><h3 class="section-label">Funding 与市场证据</h3><div class="detail-grid">${field('当前费率',pct(s.current_rate))}${field('目标事件预测',pct(s.predicted_rate))}${field('最近已结算费率',pct(s.realized_rate))}${field('下一结算时间',time(s.next_funding_time_ms))}${field('Mark / Index',price(s.mark)+' / '+price(s.index))}${field('现货中间价',price(s.spot)+' USDT')}${field('Open interest',num(s.open_interest_base)+' '+s.symbol.split('/')[0])}${field('周期及来源',(s.interval_seconds?num(s.interval_seconds/3600,1)+'h':'—')+' · '+s.interval_source)}</div><h3 class="section-label">成本情景 · 不代表可执行收益</h3><div class="detail-grid">${field('预计 Funding',num(ev.funding_estimate_usdt)+' USDT')}${field('假设手续费',num(ev.assumed_fee_cost_usdt)+' USDT')}${field('成交模型','双腿开平仓均按 Taker；不假设挂单成交')}${field('往返盘口成本',num(ev.round_trip_book_cost_usdt)+' USDT')}${field('额外成本缓冲',num(ev.extra_cost_usdt)+' USDT')}${field('资金时间成本',num(ev.capital_time_cost_usdt,4)+' USDT')}${field('时间调整前净额',num(ev.net_before_time_cost_usdt)+' USDT')}${field('时间调整后情景净额',num(d.estimated_pnl)+' USDT')}${field('预计等待入场',ago(ev.entry_wait_seconds))}${field('预计资金占用',ago(ev.occupied_seconds))}${field('总资金预算',num(ev.capital_budget_usdt)+' USDT')}${field('年资金成本假设',num(ev.annual_cost_pct)+'%')}${field('计划入场 / 退出',time(ev.planned_entry_ms)+' / '+time(ev.planned_exit_ms))}${field('两腿基础币数量',num(ev.base_quantity,6))}</div><p class="muted fine">空值表示本次无法评估，不是 0。未来基差、保证金、清算与实际成交风险尚未纳入。HOLD / EXIT 为后续持仓模块预留。</p><details><summary>四笔订单的手续费依据</summary><div class="detail-grid">${Object.entries(ev.fee_legs||{}).map(([k,v])=>field(({spot_entry:'现货开仓',spot_exit:'现货平仓',perp_entry:'永续开仓',perp_exit:'永续平仓'})[k],num(v.bps,4)+' bps · '+num(v.cost_usdt,4)+' USDT · '+(v.rule_id?v.source:'配置基准，未核实账户专属优惠'))).join('')}</div><p class="fine muted">活动需匹配交易对、市场、成交类型和预计成交时间；不会自动叠加折扣或将挂单零费率用于吃单。</p></details><details><summary>查看标准化快照</summary><pre id="normalized"></pre></details><details><summary>研究 / Debug：完整原始响应</summary><pre id="raw"></pre></details>`;
 const {raw,...normalized}=s;$('normalized').textContent=JSON.stringify(normalized,null,2);$('raw').textContent=JSON.stringify(raw,null,2);
 }catch(e){$('detail-body').textContent=e.message;}finally{detailBusy=false;}
}
document.addEventListener('click',e=>{const button=e.target.closest('[data-key]');if(button)detail(button.dataset.key);});
$('detail-close').addEventListener('click',()=>{$('detail').close();selectedKey=null;});
function renderAssets(){
 const c=state.catalog, stamp=JSON.stringify([c, $('asset-search').value, [...chosenAssets], [...document.querySelectorAll('#exchange-options input:checked')].map(x=>x.value), state.running]);
 if(stamp===catalogStamp)return;catalogStamp=stamp;
 const venues=[...document.querySelectorAll('#exchange-options input:checked')].map(x=>x.value);
 const query=$('asset-search').value.trim().toUpperCase();
 const available=state.available.assets.filter(a=>!query||a.includes(query));
 $('asset-count').textContent=`已选 ${chosenAssets.size} / 10 · 目录共 ${state.available.assets.length} 个资产`;
 $('catalog-refresh').disabled=state.running||c.loading;
 $('catalog-refresh').textContent=c.loading?'正在读取官方目录…':'刷新交易所标的';
 $('catalog-status').textContent=(c.updated_at_ms?'目录更新于 '+time(c.updated_at_ms):'获取目录后可选择更多币种；仅匹配 USDT 现货与永续。')+Object.entries(c.errors).map(([e,v])=>` · ${names[e]} 获取失败：${v}`).join('');
 $('asset-options').innerHTML=available.map(a=>{
  const supported=venues.filter(e=>(c.venues[e]||state.configured_assets).includes(a));
  return `<label><input type="checkbox" name="assets" value="${esc(a)}" ${chosenAssets.has(a)?'checked':''} ${state.running||(!supported.length&&!chosenAssets.has(a))?'disabled':''}><span>${esc(a)}<small>${esc(supported.map(e=>names[e]).join(' / ')||'所选交易所未确认支持')}</small></span></label>`;
 }).join('')||'<p class="muted">没有匹配的标的。</p>';
}
function openSettings(){
 if(!state)return;
 chosenAssets=new Set(state.selected.assets);catalogStamp='';$('asset-search').value='';
 $('exchange-options').innerHTML=state.available.exchanges.map(v=>`<label><input type="checkbox" name="exchanges" value="${esc(v)}" ${state.selected.exchanges.includes(v)?'checked':''} ${state.running?'disabled':''}>${esc(names[v])}</label>`).join('');
 $('settings-values').innerHTML=`<div class="detail-grid">${field('单腿情景金额',num(state.settings.notional_usdt)+' USDT')}${field('最低情景净收益',num(state.settings.min_net_bps)+' bps')}${field('常规采集间隔',state.settings.poll_seconds+' 秒')}${field('事件期采集间隔',state.settings.event_poll_seconds+' 秒')}</div>`;
 $('settings-start').disabled=state.running;renderAssets();$('settings').showModal();
}
$('catalog-refresh').addEventListener('click',()=>control('/api/catalog/refresh'));
$('asset-search').addEventListener('input',renderAssets);
$('exchange-options').addEventListener('change',renderAssets);
$('asset-options').addEventListener('change',e=>{
 const a=e.target.value;if(e.target.checked){if(chosenAssets.size>=10){e.target.checked=false;return toast('单次最多观察 10 个资产');}chosenAssets.add(a);}else chosenAssets.delete(a);renderAssets();
});
$('settings-open').addEventListener('click',openSettings);
$('settings-top').addEventListener('click',openSettings);
$('settings-close').addEventListener('click',()=>$('settings').close());
$('selection').addEventListener('submit',async e=>{e.preventDefault();const form=new FormData(e.target);const data={exchanges:form.getAll('exchanges'),assets:[...chosenAssets]};if(!data.exchanges.length||!data.assets.length)return toast('请至少选择一个交易所和一个资产');$('settings').close();await control('/api/start',data);});
refresh();setInterval(refresh,3000);
