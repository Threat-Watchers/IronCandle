let db = null;
let user = null;
const state = {
  scanner: { rows: [], meta: {} },
  scannerHistory: { items: [] },
  trades: [],
  filters: {
    strict: true,
    monthlyATH: true,
    dailyATH: true,
    emaAll: true,
    nearEMA: true,
    emaOrder: false,
    nearPct: Number(localStorage.getItem('ironCandleNearPct') || 3),
    leadMin: Number(localStorage.getItem('ironCandleLeadMin') || 1),
    rsMin: Number(localStorage.getItem('ironCandleRsMin') || 0),
    marketCapMinCr: Number(localStorage.getItem('ironCandleMcapMin') || 5000),
    marketCapMaxCr: Number(localStorage.getItem('ironCandleMcapMax') || 25000),
    profitGrowthMin: Number(localStorage.getItem('ironCandleProfitGrowth') || 25),
    debtEquityMax: Number(localStorage.getItem('ironCandleDebtEquity') || 0.5),
    roeMin: Number(localStorage.getItem('ironCandleRoeMin') || 15),
    salesGrowthMin: Number(localStorage.getItem('ironCandleSalesGrowthMin') || 10),
    requirePositiveOcf: localStorage.getItem('ironCandlePositiveOcf') !== 'false',
    fundamentalsMode: localStorage.getItem('ironCandleFundamentalsMode') || 'core',
    excludeFinancials: localStorage.getItem('ironCandleExcludeFinancials') !== 'false',
    sort: 'lead'
  },
  broker: 'Dhan'
};

const chargePresets = {
  Dhan: { deliveryBrokerage: 0, intradayRate: .0003, intradayCap: 20, txn: .000030699, sttDelivery: .001, sttIntradaySell: .00025, stampDelivery: .00015, stampIntraday: .00003, sebi: .000001, ipft: 1e-9, gst: .18, dpBase: 12.5 },
  Zerodha: { deliveryBrokerage: 0, intradayRate: .0003, intradayCap: 20, txn: .0000307, sttDelivery: .001, sttIntradaySell: .00025, stampDelivery: .00015, stampIntraday: .00003, sebi: .000001, ipft: 1e-9, gst: .18, dpBase: 13 },
};

const $ = (s, root=document) => root.querySelector(s);
const $$ = (s, root=document) => [...root.querySelectorAll(s)];
const money = n => `₹${Number(n || 0).toLocaleString('en-IN',{maximumFractionDigits:2})}`;
const pct = n => `${Number(n || 0).toFixed(2)}%`;
const num = v => Number(v || 0);

function toast(msg) { const el=$('#toast'); el.textContent=msg; el.classList.add('show'); clearTimeout(window.__toast); window.__toast=setTimeout(()=>el.classList.remove('show'),2800); }

async function loadSupabase() {
  const cfg = window.IRONCANDLE_CONFIG || {};
  if (!cfg.supabaseUrl || !cfg.supabaseAnonKey) return false;
  try {
    const mod = await import('https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/+esm');
    db = mod.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);
    const { data } = await db.auth.getSession();
    user = data?.session?.user || null;
    db.auth.onAuthStateChange((_e, session) => { user=session?.user || null; updateAuthBadge(); loadTrades(); });
    return true;
  } catch (e) {
    console.warn('Cloud sync unavailable; local-only journal mode enabled.', e);
    db = null;
    return false;
  }
}

async function loadHistory() {
  try {
    const r = await fetch(`public/data/scanner_history.json?v=${Date.now()}`, {cache:'no-store'});
    if (!r.ok) throw new Error(`history ${r.status}`);
    state.scannerHistory = await r.json();
  } catch (e) { state.scannerHistory = {items:[]}; }
}

async function loadScanner() {
  try {
    const bust = `?v=${Date.now()}`;
    const r = await fetch(`public/data/scanner.json${bust}`, {cache:'no-store'});
    if (!r.ok) throw new Error(`scanner ${r.status}`);
    state.scanner = await r.json();
    $('#marketAsOf').textContent = state.scanner.generatedAt ? `EOD: ${new Date(state.scanner.generatedAt).toLocaleString('en-IN')}` : 'Market: not loaded';
    $('#dataMode').textContent = state.scanner.demo ? 'DEMO SCANNER' : 'NSE EOD';
  } catch (e) {
    state.scanner = { rows: [], meta: {}, demo: false, error: String(e) };
    $('#marketAsOf').textContent = 'Market: refresh file unavailable';
  }
  renderAll();
}

function fundamentalCorePass(r) {
  const f=state.filters;
  const m=Number(r.marketCapCr), p=Number(r.netProfitGrowthYoYPct), d=Number(r.debtToEquity);
  return Boolean(r.fundamentalsAvailable)
    && Number.isFinite(m) && m >= f.marketCapMinCr && m <= f.marketCapMaxCr
    && Number.isFinite(p) && p >= f.profitGrowthMin
    && (!f.excludeFinancials || !r.isFinancialSector)
    && Number.isFinite(d) && d < f.debtEquityMax;
}

function fundamentalQualityPass(r) {
  const f=state.filters;
  const roeOk = Number(r.roePct) >= f.roeMin;
  const salesOk = Number(r.salesGrowthYoYPct) >= f.salesGrowthMin;
  const ocfOk = !f.requirePositiveOcf || r.ocfPositive === true;
  return roeOk && salesOk && ocfOk;
}

function fundamentalsPass(r) {
  if (state.filters.fundamentalsMode === 'off') return true;
  const core=fundamentalCorePass(r);
  if (!core) return false;
  return state.filters.fundamentalsMode === 'quality' ? fundamentalQualityPass(r) : true;
}

function scannerRows() {
  let rows = [...(state.scanner.rows || [])];
  const f=state.filters;
  if (f.strict) {
    rows = rows.filter(r =>
      r.monthlyAthConfirmed &&
      r.daily52wBreakoutToday &&
      r.aboveAllDailyEmas &&
      Math.abs(Number(r.weeklyEma30DistancePct ?? 99999)) <= f.nearPct &&
      Number(r.leadDays ?? -99999) >= f.leadMin &&
      Number(r.rs60Pct ?? -99999) >= f.rsMin &&
      (!f.emaOrder || r.emaOrdered) &&
      fundamentalsPass(r)
    );
  } else {
    if (f.monthlyATH) rows=rows.filter(r=>r.monthlyAthConfirmed);
    if (f.dailyATH) rows=rows.filter(r=>r.daily52wBreakoutToday);
    if (f.emaAll) rows=rows.filter(r=>r.aboveAllDailyEmas);
    if (f.nearEMA) rows=rows.filter(r=>Math.abs(Number(r.weeklyEma30DistancePct ?? 99999)) <= f.nearPct);
    if (f.emaOrder) rows=rows.filter(r=>r.emaOrdered);
    rows=rows.filter(r=>Number(r.leadDays ?? -99999) >= f.leadMin && Number(r.rs60Pct ?? -99999) >= f.rsMin);
    if (f.fundamentalsMode !== 'off') rows=rows.filter(fundamentalsPass);
  }
  if (f.sort==='lead') rows.sort((a,b)=>(b.leadDays||-999)-(a.leadDays||-999));
  if (f.sort==='ema') rows.sort((a,b)=>Math.abs(a.weeklyEma30DistancePct||999)-Math.abs(b.weeklyEma30DistancePct||999));
  if (f.sort==='rs') rows.sort((a,b)=>(b.rs60Pct||-999)-(a.rs60Pct||-999));
  if (f.sort==='fund') rows.sort((a,b)=>(Number(b.netProfitGrowthYoYPct)||-999)-(Number(a.netProfitGrowthYoYPct)||-999));
  if (f.sort==='price') rows.sort((a,b)=>(a.close||0)-(b.close||0));
  return rows;
}

function setupBadge(condition, label) { return `<span class="badge ${condition?'green':'red'}">${condition?'✓':'—'} ${label}</span>`; }
function yesDot(condition) { return `<span class="badge ${condition?'green':'red'}">${condition?'YES':'NO'}</span>`; }
function tradingViewUrl(symbol) { return `https://www.tradingview.com/chart/?symbol=NSE%3A${encodeURIComponent(symbol)}`; }

function renderCommand() {
  const rows=state.scanner.rows||[];
  const strict=scannerRows().length;
  const todayBreak=rows.filter(r=>r.daily52wBreakoutToday).length;
  const leaders=rows.filter(r=>r.leadDays>=state.filters.leadMin).length;
  const near=rows.filter(r=>Math.abs(r.weeklyEma30DistancePct||999)<=state.filters.nearPct).length;
  const fundamentals=rows.filter(r=>fundamentalCorePass(r)).length;
  const hist=(state.scannerHistory.items||[]).slice(0,8);
  const net=state.trades.reduce((s,t)=>s+num(t.net_pnl),0);
  const wins=state.trades.filter(t=>t.outcome==='WIN').length;
  const winRate=state.trades.length ? wins/state.trades.length*100 : 0;
  $('#view-command').innerHTML=`
    <div class="grid grid-4">
      ${kpi('Strict setups', strict, 'All rules satisfied')}
      ${kpi('52W breakouts today', todayBreak, 'Daily close above prior 252D high')}
      ${kpi('Nifty leaders', leaders, `Lead ≥ ${state.filters.leadMin} trading day`)}
      ${kpi('Near WEMA30', near, `Within ${state.filters.nearPct.toFixed(1)}%`)}
      ${kpi('Fundamental core', fundamentals, `MCap ₹${state.filters.marketCapMinCr}–${state.filters.marketCapMaxCr}Cr`)}
    </div>
    <div style="height:14px"></div>
    <div class="two-col">
      <div class="panel">
        <div class="section-head"><div><h2>Today's strict setup list</h2><p>Rule-based scanner from the latest published EOD snapshot.</p></div><button class="ghost-btn" data-view-jump="scanner">Open scanner</button></div>
        ${renderScannerTable(scannerRows().slice(0,8))}
      </div>
      <div class="panel">
        <div class="section-head"><div><h2>Journal pulse</h2><p>Net results from saved completed trades.</p></div><button class="ghost-btn" id="openTradeFromCommand">Log trade</button></div>
        <div class="metric-list">
          ${metric('Net P&L', money(net), net>=0?'text-green':'text-red')}
          ${metric('Win rate', pct(winRate), '')}
          ${metric('Trades logged', state.trades.length, '')}
          ${metric('Total charges', money(state.trades.reduce((s,t)=>s+num(t.total_charges),0)), '')}
        </div>
        <div class="panel soft-panel" style="margin-top:14px;box-shadow:none"><div class="section-head"><div><h3>Recent scanner history</h3><p>Daily snapshots are retained so you can review what entered the setup.</p></div></div>${hist.length?`<div class="metric-list">${hist.slice(0,5).map(x=>metric(x.tradeDate,`${x.strictCount} strict • ${x.breakoutCount} 52W • ${x.coreFundamentalsCount} core`,'' )).join('')}</div>`:'<div class="muted tiny">History starts after the first production refresh.</div>'}</div>
        <div class="alert info" style="margin-top:14px">Scanner is a rules engine, not an automatic buy/sell decision. Current fundamental mode: <b>${state.filters.fundamentalsMode==='quality'?'Core + Quality':state.filters.fundamentalsMode==='core'?'Core only':'Off'}</b>. You control the filters, risk and final trade.</div>
      </div>
    </div>
    <div style="height:14px"></div>
    <div class="panel">
      <div class="section-head"><div><h2>Rule stack</h2><p>Current defaults can be changed in Stock Scanner → Controls.</p></div></div>
      <div class="check-row">
        ${setupBadge(true,'NIFTY 500 universe')}
        ${setupBadge(true,'Monthly ATH confirmation')}
        ${setupBadge(true,'Daily 52W-high breakout')}
        ${setupBadge(true,'Above Daily EMA 10/25/50/100/200')}
        ${setupBadge(true,`Near Weekly EMA30 ≤ ${state.filters.nearPct.toFixed(1)}%`)}
        ${setupBadge(true,`Nifty lead ≥ ${state.filters.leadMin}d`)}
        ${setupBadge(true,`RS60 ≥ ${state.filters.rsMin.toFixed(1)}%`)}
        ${setupBadge(true,`PAT YoY ≥ ${state.filters.profitGrowthMin}%`)}
        ${setupBadge(true,`D/E < ${state.filters.debtEquityMax}`)}
        ${setupBadge(true,`MCap ₹${state.filters.marketCapMinCr}–${state.filters.marketCapMaxCr}Cr`)}
      </div>
    </div>`;
  $('#openTradeFromCommand')?.addEventListener('click',openTradeModal);
  $$('[data-view-jump]').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.viewJump)));
}

function kpi(label,value,foot){return `<div class="panel"><div class="kpi-label">${label}</div><div class="kpi-value">${value}</div><div class="kpi-foot">${foot}</div></div>`;}
function metric(label,value,cls){return `<div class="metric-row"><div class="name">${label}</div><div class="value ${cls||''}">${value}</div></div>`;}

function fundBadge(ok,label){return `<span class="badge ${ok?'green':'red'}">${ok?'✓':'—'} ${label}</span>`;}
function renderScannerTable(rows) {
  if (!rows.length) return `<div class="empty">No symbols satisfy the current rules. Try Breakout Radar, reduce the thresholds, or switch fundamentals to Core/Off.
  <div class="muted tiny" style="margin-top:8px">Fundamental rules require a connected fundamentals data provider in the market-refresh workflow; IronCandle never fabricates missing financial data.</div></div>`;
  return `<div class="table-wrap"><table><thead><tr>
    <th>Symbol</th><th>Price</th><th>MCap</th><th>Monthly ATH</th><th>Daily 52W</th><th>WEMA30</th><th>Lead</th><th>RS60</th><th>PAT YoY</th><th>D/E</th><th>ROE</th><th>EMA stack</th><th>Status</th><th>Chart</th>
  </tr></thead><tbody>${rows.map(r=>`<tr>
    <td><b>${r.symbol}</b><div class="muted tiny">${r.companyName||''}</div></td>
    <td>${money(r.close)}</td>
    <td>${r.marketCapCr!=null?money(r.marketCapCr)+'Cr':'—'}</td>
    <td>${yesDot(r.monthlyAthConfirmed)}</td>
    <td>${r.daily52wBreakoutToday?'<span class="badge gold">52W BREAK</span>':'<span class="badge blue">No</span>'}</td>
    <td class="${Math.abs(r.weeklyEma30DistancePct||999)<=state.filters.nearPct?'text-green':''}">${pct(r.weeklyEma30DistancePct)}</td>
    <td class="${r.leadDays>=state.filters.leadMin?'text-green':'text-red'}">${r.leadDays}d</td>
    <td class="${r.rs60Pct>=state.filters.rsMin?'text-green':''}">${pct(r.rs60Pct)}</td>
    <td class="${Number(r.netProfitGrowthYoYPct)>=state.filters.profitGrowthMin?'text-green':'text-red'}">${pct(r.netProfitGrowthYoYPct)}</td>
    <td class="${Number(r.debtToEquity)<state.filters.debtEquityMax?'text-green':'text-red'}">${r.isFinancialSector?'N/A':(r.debtToEquity!=null?Number(r.debtToEquity).toFixed(2):'—')}</td>
    <td>${r.roePct!=null?pct(r.roePct):'—'}</td>
    <td>${[10,25,50,100,200].map(n=>`<span title="EMA ${n}" class="badge ${r[`closeAboveEma${n}`]?'green':'red'}">${n}</span>`).join(' ')}</td>
    <td>${fundBadge(fundamentalCorePass(r),'CORE')} ${fundBadge(fundamentalQualityPass(r),'Q')}</td>
    <td><a class="pill-link" target="_blank" rel="noreferrer" href="${tradingViewUrl(r.symbol)}">TradingView ↗</a></td>
  </tr>`).join('')}</tbody></table></div>`;
}

function renderScanner() {
  const f=state.filters; const rows=scannerRows();
  $('#view-scanner').innerHTML=`
    <div class="section-head"><div><h2>NIFTY 500 breakout + fundamental scanner</h2><p>Daily EOD engine: monthly all-time-high confirmation, daily 52-week breakout, EMA stack, weekly EMA30 location, NIFTY leadership and fundamental quality.</p></div><div class="links"><span class="badge gold">${rows.length} match</span><span class="badge blue">${state.scanner.meta?.universeCount||500} universe</span><span class="badge ${state.scanner.meta?.fundamentalsProvider && state.scanner.meta?.fundamentalsProvider!=='NOT_CONFIGURED'?'green':'red'}">Fundamentals: ${state.scanner.meta?.fundamentalsProvider||'NOT CONFIGURED'}</span></div></div>
    <div class="panel">
      <div class="filter-bar">
        <label>Mode<select id="scanMode"><option value="strict" ${f.strict?'selected':''}>Strict Setup</option><option value="radar" ${!f.strict?'selected':''}>Breakout Radar</option></select></label>
        <label>Fundamentals<select id="fundMode"><option value="quality" ${f.fundamentalsMode==='quality'?'selected':''}>Core + Quality</option><option value="core" ${f.fundamentalsMode==='core'?'selected':''}>Core only</option><option value="off" ${f.fundamentalsMode==='off'?'selected':''}>Off</option></select></label>
        <label>Weekly EMA30 ≤ %<input id="nearPct" type="number" step="0.5" min="0.5" max="20" value="${f.nearPct}"></label>
        <label>Lead ≥ days<input id="leadMin" type="number" step="1" min="0" max="60" value="${f.leadMin}"></label>
        <label>RS60 ≥ %<input id="rsMin" type="number" step="0.5" min="-50" max="100" value="${f.rsMin}"></label>
        <label>Sort<select id="scanSort"><option value="lead" ${f.sort==='lead'?'selected':''}>Lead first</option><option value="ema" ${f.sort==='ema'?'selected':''}>Closest WEMA30</option><option value="rs" ${f.sort==='rs'?'selected':''}>Strongest RS60</option><option value="fund" ${f.sort==='fund'?'selected':''}>Highest PAT YoY</option><option value="price" ${f.sort==='price'?'selected':''}>Price</option></select></label>
        <label>EMA order<select id="emaOrder"><option value="no" ${!f.emaOrder?'selected':''}>Not required</option><option value="yes" ${f.emaOrder?'selected':''}>Require EMA10&gt;25&gt;50&gt;100&gt;200</option></select></label>
      </div>
      ${!f.strict?`<div class="check-row" style="margin:0 0 12px">
        <label class="check"><input type="checkbox" id="monthlyATH" ${f.monthlyATH?'checked':''}/> Monthly ATH</label>
        <label class="check"><input type="checkbox" id="dailyATH" ${f.dailyATH?'checked':''}/> Daily 52-week breakout</label>
        <label class="check"><input type="checkbox" id="emaAll" ${f.emaAll?'checked':''}/> Above all daily EMAs</label>
        <label class="check"><input type="checkbox" id="nearEMA" ${f.nearEMA?'checked':''}/> Near weekly EMA30</label>
      </div>`:''}
      <div class="alert warn"><b>Exact definitions:</b> Monthly ATH = last completed monthly close above prior all-time monthly high. Daily breakout = latest daily close above the prior 252-trading-day (52-week) high. Fundamentals Core = market cap ₹5,000–₹25,000Cr + latest annual net profit growth ≥25% YoY + D/E &lt;0.5 for non-financial sectors. Quality overlay = ROE ≥15% + sales growth ≥10% + positive latest annual operating cash flow.</div>
      <div style="height:14px"></div>${renderScannerTable(rows)}
    </div>`;
  $('#scanMode').addEventListener('change',e=>{f.strict=e.target.value==='strict'; renderScanner(); renderCommand();});
  $('#fundMode').addEventListener('change',e=>{f.fundamentalsMode=e.target.value; localStorage.setItem('ironCandleFundamentalsMode',f.fundamentalsMode); renderScanner(); renderCommand();});
  $('#nearPct').addEventListener('change',e=>{f.nearPct=num(e.target.value); localStorage.setItem('ironCandleNearPct',f.nearPct); renderScanner(); renderCommand();});
  $('#leadMin').addEventListener('change',e=>{f.leadMin=num(e.target.value); localStorage.setItem('ironCandleLeadMin',f.leadMin); renderScanner(); renderCommand();});
  $('#rsMin').addEventListener('change',e=>{f.rsMin=num(e.target.value); localStorage.setItem('ironCandleRsMin',f.rsMin); renderScanner(); renderCommand();});
  $('#scanSort').addEventListener('change',e=>{f.sort=e.target.value; renderScanner();});
  $('#emaOrder').addEventListener('change',e=>{f.emaOrder=e.target.value==='yes'; renderScanner();});
  ['monthlyATH','dailyATH','emaAll','nearEMA'].forEach(id=>$('#'+id)?.addEventListener('change',e=>{f[id]=e.target.checked; renderScanner();}));
}

function calculateRisk() {
  const capital=num($('#riskCapital')?.value||1000000), riskPct=num($('#riskPct')?.value||1)/100, entry=num($('#riskEntry')?.value), stop=num($('#riskStop')?.value), alloc=num($('#riskAlloc')?.value||20)/100, dir=$('#riskDir')?.value||'Long';
  const perShare=Math.abs(entry-stop);
  const riskRupees=capital*riskPct;
  const qtyByRisk=perShare?Math.floor(riskRupees/perShare):0;
  const qtyByAlloc=entry?Math.floor((capital*alloc)/entry):0;
  const qty=Math.max(0,Math.min(qtyByRisk,qtyByAlloc||qtyByRisk));
  const invest=qty*entry, riskAmt=qty*perShare;
  const targets=[1,2,3].map(r=> dir==='Short'?entry-perShare*r:entry+perShare*r);
  $('#riskOutputs').innerHTML=`
    ${output('Risk / trade',money(riskRupees))}
    ${output('SL / share',money(perShare))}
    ${output('Quantity',qty.toLocaleString('en-IN'))}
    ${output('Investment',money(invest))}
    ${output('Risk at SL',money(riskAmt))}
    ${output('Max allocation',pct(alloc*100))}`;
  $('#riskTargets').innerHTML=targets.map((p,i)=>`<div class="target"><div class="r">TARGET ${i+1} • ${i+1}R</div><div class="price">${money(p)}</div></div>`).join('');
}
function output(label,value){return `<div class="output-box"><div class="label">${label}</div><div class="value">${value}</div></div>`;}
function renderRisk(){
  $('#view-risk').innerHTML=`<div class="section-head"><div><h2>Risk Desk</h2><p>Adapted from your uploaded quantity calculator: 1% risk → SL distance → position size → 1R/2R/3R.</p></div><span class="badge gold">Risk first</span></div>
  <div class="two-col"><div class="panel"><div class="risk-inputs">
    <label>Portfolio capital (₹)<input id="riskCapital" type="number" value="1000000" step="1000"></label>
    <label>Risk per trade (%)<input id="riskPct" type="number" value="1" step="0.1" min="0.1" max="5"></label>
    <label>Entry price (₹)<input id="riskEntry" type="number" value="92" step="0.05"></label>
    <label>Stop loss (₹)<input id="riskStop" type="number" value="70" step="0.05"></label>
    <label>Max allocation (%)<input id="riskAlloc" type="number" value="20" step="1" min="1" max="100"></label>
    <label>Direction<select id="riskDir"><option>Long</option><option>Short</option></select></label>
  </div><div class="risk-output" id="riskOutputs"></div><div class="targets" id="riskTargets"></div></div>
  <div class="panel"><div class="section-head"><div><h2>Guardrails</h2><p>These don't place orders; they size your idea before you trade.</p></div></div>
    <div class="metric-list">
      ${metric('1R', 'Risk at the stop', '')}
      ${metric('2R', 'Twice planned risk', '')}
      ${metric('3R', 'Three times planned risk', '')}
    </div>
    <div class="alert info" style="margin-top:14px">Quantity is capped by both risk-per-trade and the max allocation setting. You can set max allocation to 100% to reproduce the original pure risk-quantity calculation.</div>
  </div></div>`;
  ['riskCapital','riskPct','riskEntry','riskStop','riskAlloc','riskDir'].forEach(id=>$('#'+id).addEventListener('input',calculateRisk));
  calculateRisk();
}

function calculateCharges(t){
  const p=chargePresets[t.broker]||chargePresets.Dhan;
  const entry=num(t.entry_price), exit=num(t.exit_price), qty=num(t.qty);
  const entryVal=entry*qty, exitVal=exit*qty;
  const intraday=t.direction!=='Long Delivery';
  const buyVal=t.direction==='Short Intraday'?exitVal:entryVal;
  const sellVal=t.direction==='Short Intraday'?entryVal:exitVal;
  const brokerage=intraday ? Math.min(buyVal*p.intradayRate,p.intradayCap)+Math.min(sellVal*p.intradayRate,p.intradayCap) : p.deliveryBrokerage;
  const stt=intraday ? sellVal*p.sttIntradaySell : (buyVal+sellVal)*p.sttDelivery;
  const txn=(buyVal+sellVal)*p.txn;
  const sebi=(buyVal+sellVal)*p.sebi;
  const ipft=(buyVal+sellVal)*p.ipft;
  const stamp=(intraday?buyVal*p.stampIntraday:buyVal*p.stampDelivery);
  const dp=intraday?0:(sellVal>0?p.dpBase*(1+p.gst):0);
  const gst=(brokerage+txn+sebi+ipft)*p.gst;
  const total=brokerage+stt+txn+sebi+ipft+stamp+gst+dp;
  const gross=t.direction==='Short Intraday'?(entry-exit)*qty:(exit-entry)*qty;
  const risk=Math.abs(entry-num(t.stop_price))*qty;
  const net=gross-total;
  return {brokerage,stt,txn,sebi,ipft,stamp,gst,dp,total,gross,net,risk,r:risk?net/risk:0,outcome:net>0?'WIN':net<0?'LOSS':'BE'};
}

function updateTradePreview(){
  const form=$('#tradeForm'); if(!form) return;
  const fd=new FormData(form); const t=Object.fromEntries(fd.entries()); const c=calculateCharges(t);
  $('#tradePreview').innerHTML=`<div class="preview-grid">${['gross','total','net','r'].map(k=>`<div class="preview-item"><div class="k">${k==='gross'?'Gross P&L':k==='total'?'Charges':k==='net'?'Net P&L':'R Multiple'}</div><div class="v ${k==='net'?(c.net>=0?'text-green':'text-red'):''}">${k==='r'?c.r.toFixed(2)+'R':money(c[k])}</div></div>`).join('')}</div>`;
}

function renderJournal(){
  const rows=[...state.trades].sort((a,b)=>String(b.exit_date||'').localeCompare(String(a.exit_date||'')));
  $('#view-journal').innerHTML=`<div class="section-head"><div><h2>Trading Journal</h2><p>Every completed trade gets a full P&L and charge breakdown.</p></div><div class="links"><button class="ghost-btn" id="exportCsv">Export backup</button><button class="primary-btn" id="openTrade">+ Log trade</button></div></div>
  <div class="grid grid-4" style="margin-bottom:14px">${kpi('Trades',rows.length,'Completed trades')}${kpi('Net P&L',money(rows.reduce((s,t)=>s+num(t.net_pnl),0)),'After charges')}${kpi('Win rate',pct(rows.length?rows.filter(t=>t.outcome==='WIN').length/rows.length*100:0),'Based on net P&L')}${kpi('Total charges',money(rows.reduce((s,t)=>s+num(t.total_charges),0)),'Broker + statutory')}
  </div><div class="panel">${rows.length?`<div class="table-wrap"><table><thead><tr><th>Date</th><th>Symbol</th><th>Direction</th><th>Setup</th><th>Entry</th><th>Exit</th><th>Qty</th><th>Gross</th><th>Charges</th><th>Net</th><th>R</th><th>Outcome</th><th>Broker</th></tr></thead><tbody>${rows.map(t=>`<tr><td>${t.exit_date||t.entry_date}</td><td><b>${t.symbol}</b></td><td>${t.direction}</td><td>${t.setup||'—'}</td><td>${money(t.entry_price)}</td><td>${money(t.exit_price)}</td><td>${t.qty}</td><td>${money(t.gross_pnl)}</td><td>${money(t.total_charges)}</td><td class="${num(t.net_pnl)>=0?'text-green':'text-red'}">${money(t.net_pnl)}</td><td>${num(t.r_multiple).toFixed(2)}R</td><td><span class="badge ${t.outcome==='WIN'?'green':t.outcome==='LOSS'?'red':'blue'}">${t.outcome}</span></td><td>${t.broker}</td></tr>`).join('')}</tbody></table></div>`:'<div class="empty">No trades yet. Your first job is to log the trade exactly as executed — not how you wished it executed.</div>'}</div>`;
  $('#openTrade').addEventListener('click',openTradeModal); $('#exportCsv').addEventListener('click',exportCsv);
}

function openTradeModal(){ const m=$('#tradeModal'); m.classList.add('open'); m.setAttribute('aria-hidden','false'); const form=$('#tradeForm'); const d=new Date().toISOString().slice(0,10); form.entry_date.value=d; form.exit_date.value=d; updateTradePreview(); }
function closeTradeModal(){ const m=$('#tradeModal'); m.classList.remove('open'); m.setAttribute('aria-hidden','true'); }

async function saveTrade(e){
  e.preventDefault(); const form=e.currentTarget; const raw=Object.fromEntries(new FormData(form).entries()); const c=calculateCharges(raw);
  const record={...raw, user_id:user?.id || null, broker:raw.broker, entry_price:num(raw.entry_price),stop_price:num(raw.stop_price),target_price:num(raw.target_price),exit_price:num(raw.exit_price),qty:num(raw.qty),brokerage:c.brokerage,stt:c.stt,exchange_txn:c.txn,sebi:c.sebi,ipft:c.ipft,stamp_duty:c.stamp,gst:c.gst,dp_charge:c.dp,total_charges:c.total,gross_pnl:c.gross,net_pnl:c.net,r_multiple:c.r,outcome:c.outcome};
  if(db && user){ const {error}=await db.from('trades').insert(record); if(error){toast(`Save failed: ${error.message}`); return;} }
  else { record.id=crypto.randomUUID(); state.trades=[record,...state.trades]; localStorage.setItem('ironCandleTrades',JSON.stringify(state.trades)); }
  closeTradeModal(); form.reset(); await loadTrades(); renderAll(); toast('Trade saved with charges and R multiple.');
}

async function loadTrades(){
  if(db && user){ const {data,error}=await db.from('trades').select('*').order('exit_date',{ascending:false}); if(!error) state.trades=data||[]; else state.trades=[]; }
  else state.trades=JSON.parse(localStorage.getItem('ironCandleTrades')||'[]');
  updateAuthBadge(); renderAll();
}
function updateAuthBadge(){ const el=$('#dataMode'); if(!el) return; el.textContent = (db && user) ? `CLOUD • ${user.email}` : (db ? 'CLOUD READY' : (state.scanner.demo ? 'DEMO • LOCAL DATA' : 'NSE EOD')); }

function exportCsv(){
  const cols=['entry_date','exit_date','symbol','direction','setup','timeframe','entry_price','stop_price','target_price','exit_price','qty','broker','brokerage','stt','exchange_txn','sebi','ipft','stamp_duty','gst','dp_charge','total_charges','gross_pnl','net_pnl','risk_amount','r_multiple','outcome','notes'];
  const csv=[cols.join(','),...state.trades.map(r=>cols.map(c=>`"${String(r[c]??'').replaceAll('"','""')}"`).join(','))].join('\n');
  const blob=new Blob([csv],{type:'text/csv'}); const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='ironcandle-trading-journal.csv'; a.click(); setTimeout(()=>URL.revokeObjectURL(a.href),1000);
}

function renderAnalytics(){
  const rows=state.trades; const net=rows.reduce((s,t)=>s+num(t.net_pnl),0); const pos=rows.filter(t=>num(t.net_pnl)>0).reduce((s,t)=>s+num(t.net_pnl),0); const neg=Math.abs(rows.filter(t=>num(t.net_pnl)<0).reduce((s,t)=>s+num(t.net_pnl),0)); const pf=neg?pos/neg:0; const avgR=rows.length?rows.reduce((s,t)=>s+num(t.r_multiple),0)/rows.length:0;
  $('#view-analytics').innerHTML=`<div class="section-head"><div><h2>Trading analytics</h2><p>Focus on process metrics: net outcome, R, charges and setup behavior.</p></div></div><div class="grid grid-4">${kpi('Net P&L',money(net),'After all tracked charges')}${kpi('Profit factor',pf.toFixed(2)+'x','Gross profits ÷ gross losses')}${kpi('Average R',avgR.toFixed(2)+'R','Per completed trade')}${kpi('Avg charges',money(rows.length?rows.reduce((s,t)=>s+num(t.total_charges),0)/rows.length:0),'Per trade')}
  </div>
    <div class="grid grid-2"><div class="panel"><div class="section-head"><div><h2>Setup outcomes</h2><p>Net P&L by setup label.</p></div></div>${setupAnalysis()}</div><div class="panel"><div class="section-head"><div><h2>R distribution</h2><p>Count of wins, losses and break-even trades.</p></div></div>${rDistribution()}</div></div>`;
}
function setupAnalysis(){ const map={}; state.trades.forEach(t=>{const k=t.setup||'Unlabelled'; map[k]=(map[k]||0)+num(t.net_pnl);}); const entries=Object.entries(map).sort((a,b)=>b[1]-a[1]); if(!entries.length) return '<div class="empty">No trade data yet.</div>'; return `<div class="metric-list">${entries.map(([k,v])=>`<div><div class="metric-row"><div class="name">${k}</div><div class="value ${v>=0?'text-green':'text-red'}">${money(v)}</div></div><div class="progress"><span style="width:${Math.min(100,Math.abs(v)/(Math.max(...entries.map(e=>Math.abs(e[1])))||1)*100)}%"></span></div></div>`).join('')}</div>`; }
function rDistribution(){const w=state.trades.filter(t=>t.outcome==='WIN').length,l=state.trades.filter(t=>t.outcome==='LOSS').length,b=state.trades.filter(t=>t.outcome==='BE').length; const max=Math.max(1,w,l,b); return `<div class="metric-list">${metric('Wins',w,'text-green')}<div class="progress"><span style="width:${w/max*100}%"></span></div>${metric('Losses',l,'text-red')}<div class="progress"><span style="width:${l/max*100}%"></span></div>${metric('Break-even',b,'')}<div class="progress"><span style="width:${b/max*100}%"></span></div></div>`; }

function renderSettings(){
  $('#view-settings').innerHTML=`<div class="section-head"><div><h2>Settings & cloud access</h2><p>Your dashboard is the source of truth. Cloud sync is the layer that makes the same journal available from phone, laptop and desktop.</p></div></div>
    <div class="alert ${state.scanner.status==='NOT_BOOTSTRAPPED'?'warn':'info'}" style="margin-bottom:14px"><b>Production setup:</b> ${state.scanner.status==='NOT_BOOTSTRAPPED'?'Run the one-time historical bootstrap, configure Google Drive secrets and run the GitHub refresh workflow before relying on the scanner.':'Market snapshot is published. Check the timestamp above for the latest EOD refresh.'}</div>
    <div class="grid grid-2">
      <div class="panel"><div class="section-head"><div><h2>Cloud journal</h2><p>Sign in to keep trades, charges, notes and analytics synced across devices.</p></div></div><div class="auth-card">
        <div id="authStatus" class="alert info">${db?(user?`Signed in as ${user.email}`:'Supabase connected — sign in to sync'):'Cloud connection is not configured yet — this browser is in local-only demo mode. Configure Supabase before relying on cross-device access.'}</div>
        ${user?`<button id="signOutBtn" class="ghost-btn">Sign out</button>`:`<label>Email<input id="authEmail" type="email" placeholder="you@example.com"></label><label>Password<input id="authPassword" type="password" placeholder="At least 6 characters"></label><div class="form-actions"><button id="signUpBtn" class="ghost-btn">Create account</button><button id="signInBtn" class="primary-btn">Sign in</button></div>`}
      </div></div>
      <div class="panel"><div class="section-head"><div><h2>Scanner controls</h2><p>All thresholds are dashboard-editable; the market snapshot remains read-only.</p></div></div>
        <div class="form-grid">
          <label>Weekly EMA30 threshold (%)<input id="setNearPct" type="number" step="0.5" value="${state.filters.nearPct}"></label>
          <label>Nifty lead days<input id="setLeadMin" type="number" value="${state.filters.leadMin}"></label>
          <label>RS60 threshold (%)<input id="setRsMin" type="number" step="0.5" value="${state.filters.rsMin}"></label>
          <label>Market cap min (₹Cr)<input id="setMcapMin" type="number" value="${state.filters.marketCapMinCr}"></label>
          <label>Market cap max (₹Cr)<input id="setMcapMax" type="number" value="${state.filters.marketCapMaxCr}"></label>
          <label>Net profit YoY min (%)<input id="setProfitGrowth" type="number" value="${state.filters.profitGrowthMin}"></label>
          <label>Debt / Equity max<input id="setDebtEquity" type="number" step="0.05" value="${state.filters.debtEquityMax}"></label>
          <label>ROE quality min (%)<input id="setRoeMin" type="number" value="${state.filters.roeMin}"></label>
          <label>Sales growth quality min (%)<input id="setSalesGrowth" type="number" value="${state.filters.salesGrowthMin}"></label>
          <label>Default fundamentals<select id="setFundMode"><option value="quality" ${state.filters.fundamentalsMode==='quality'?'selected':''}>Core + Quality</option><option value="core" ${state.filters.fundamentalsMode==='core'?'selected':''}>Core only</option><option value="off" ${state.filters.fundamentalsMode==='off'?'selected':''}>Off</option></select></label>
          <label>Financial sector D/E<select id="setFinancials"><option value="exclude" ${state.filters.excludeFinancials?'selected':''}>Exclude from strict</option><option value="allow" ${!state.filters.excludeFinancials?'selected':''}>Do not exclude</option></select></label>
          <label>Require positive latest OCF<select id="setOcf"><option value="yes" ${state.filters.requirePositiveOcf?'selected':''}>Yes</option><option value="no" ${!state.filters.requirePositiveOcf?'selected':''}>No</option></select></label>
          <label>Default mode<select id="setStrict"><option value="true" ${state.filters.strict?'selected':''}>Strict Setup</option><option value="false" ${!state.filters.strict?'selected':''}>Breakout Radar</option></select></label>
        </div><div style="height:12px"></div><button class="primary-btn" id="saveScannerSettings">Save scanner settings</button>
      </div>
      <div class="panel"><div class="section-head"><div><h2>Data & methodology</h2><p>What the updater does each trading day.</p></div></div><div class="metric-list"><div class="metric-row"><div class="name">Universe</div><div class="value">NIFTY 500</div></div><div class="metric-row"><div class="name">Daily price data</div><div class="value">NSE official UDiFF final bhavcopy</div></div><div class="metric-row"><div class="name">History bootstrap</div><div class="value">Yahoo Finance / yfinance — one time only</div></div><div class="metric-row"><div class="name">Fundamentals</div><div class="value">${state.scanner.meta?.fundamentalsProvider||'NOT CONFIGURED'}</div></div><div class="metric-row"><div class="name">Benchmark</div><div class="value">NIFTY 50</div></div><div class="metric-row"><div class="name">Market refresh</div><div class="value">Weekdays 18:30 IST</div></div><div class="metric-row"><div class="name">Fundamental refresh</div><div class="value">Saturday 10:00 IST</div></div><div class="metric-row"><div class="name">Fundamental as-of</div><div class="value">${state.scanner.meta?.latestFundamentalRefresh||'Pending first refresh'}</div></div></div></div>
      <div class="panel"><div class="section-head"><div><h2>Broker presets</h2><p>Rates are editable in the source code and should be reconciled to your contract note.</p></div></div><div class="metric-list">${metric('Dhan delivery brokerage','₹0','')} ${metric('Dhan intraday brokerage','0.03% or ₹20 / order','')} ${metric('Dhan delivery STT','0.1% buy + sell','')} ${metric('Dhan intraday STT','0.025% sell side','')}</div><div class="links" style="margin-top:12px"><a class="pill-link" href="https://dhan.co/pricing/" target="_blank" rel="noreferrer">Dhan pricing ↗</a><a class="pill-link" href="https://www.zerodha.com/charges" target="_blank" rel="noreferrer">Zerodha charges ↗</a></div></div>
    </div>`;
  $('#saveScannerSettings').addEventListener('click',()=>{
    state.filters.nearPct=num($('#setNearPct').value); state.filters.leadMin=num($('#setLeadMin').value); state.filters.rsMin=num($('#setRsMin').value);
    state.filters.marketCapMinCr=num($('#setMcapMin').value); state.filters.marketCapMaxCr=num($('#setMcapMax').value); state.filters.profitGrowthMin=num($('#setProfitGrowth').value);
    state.filters.debtEquityMax=num($('#setDebtEquity').value); state.filters.roeMin=num($('#setRoeMin').value); state.filters.salesGrowthMin=num($('#setSalesGrowth').value);
    state.filters.fundamentalsMode=$('#setFundMode').value; state.filters.excludeFinancials=$('#setFinancials').value==='exclude'; state.filters.requirePositiveOcf=$('#setOcf').value==='yes'; state.filters.strict=$('#setStrict').value==='true';
    localStorage.setItem('ironCandleNearPct',state.filters.nearPct); localStorage.setItem('ironCandleLeadMin',state.filters.leadMin); localStorage.setItem('ironCandleRsMin',state.filters.rsMin);
    localStorage.setItem('ironCandleMcapMin',state.filters.marketCapMinCr); localStorage.setItem('ironCandleMcapMax',state.filters.marketCapMaxCr); localStorage.setItem('ironCandleProfitGrowth',state.filters.profitGrowthMin);
    localStorage.setItem('ironCandleDebtEquity',state.filters.debtEquityMax); localStorage.setItem('ironCandleRoeMin',state.filters.roeMin); localStorage.setItem('ironCandleSalesGrowthMin',state.filters.salesGrowthMin);
    localStorage.setItem('ironCandleFundamentalsMode',state.filters.fundamentalsMode); localStorage.setItem('ironCandleExcludeFinancials',state.filters.excludeFinancials); localStorage.setItem('ironCandlePositiveOcf',state.filters.requirePositiveOcf);
    renderAll(); toast('Scanner + fundamental settings saved.');
  });
  $('#signInBtn')?.addEventListener('click',authSignIn); $('#signUpBtn')?.addEventListener('click',authSignUp); $('#signOutBtn')?.addEventListener('click',async()=>{await db.auth.signOut();toast('Signed out.');});
}
async function authSignIn(){if(!db)return; const {error}=await db.auth.signInWithPassword({email:$('#authEmail').value,password:$('#authPassword').value}); if(error)toast(error.message); else toast('Signed in.');}
async function authSignUp(){if(!db)return; const {error}=await db.auth.signUp({email:$('#authEmail').value,password:$('#authPassword').value}); if(error)toast(error.message); else toast('Account created. Check your email if confirmation is enabled.');}

function showView(view){
  $$('.view').forEach(v=>v.classList.remove('active')); $(`#view-${view}`).classList.add('active');
  $$('.nav-btn').forEach(b=>b.classList.toggle('active',b.dataset.view===view));
  const titles={command:'Good trading requires a repeatable process.',scanner:'Find the exact market structure you defined.',journal:'Capture what actually happened, including charges.',risk:'Position size before emotion sizes it for you.',analytics:'Measure R, P&L and process quality.',settings:'Control data, sync and scanner defaults.'};
  $('#pageTitle').textContent=titles[view]||titles.command;
}
function renderAll(){renderCommand();renderScanner();renderJournal();renderRisk();renderAnalytics();renderSettings();}

$$('.nav-btn').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));
$('#refreshScannerBtn').addEventListener('click',async()=>{await loadScanner(); await loadHistory(); renderAll(); toast('Latest published scanner snapshot loaded.');});
$('#closeTradeModal').addEventListener('click',closeTradeModal); $('#cancelTrade').addEventListener('click',closeTradeModal);
$('#tradeForm').addEventListener('submit',saveTrade); $('#tradeForm').addEventListener('input',updateTradePreview); $('#tradeForm').addEventListener('change',updateTradePreview);
$('#tradeModal').addEventListener('click',e=>{if(e.target.id==='tradeModal')closeTradeModal();});

await loadSupabase();
await loadTrades();
await loadScanner();
await loadHistory();
showView('command');
