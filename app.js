(() => {
  "use strict";
  const $ = (sel, root=document) => root.querySelector(sel);
  const $$ = (sel, root=document) => [...root.querySelectorAll(sel)];
  const fmtINR = (n, digits=0) => new Intl.NumberFormat("en-IN",{style:"currency",currency:"INR",maximumFractionDigits:digits,minimumFractionDigits:digits}).format(Number.isFinite(n)?n:0);
  const fmtNum = (n,d=2) => Number.isFinite(n) ? n.toLocaleString("en-IN",{maximumFractionDigits:d,minimumFractionDigits:d}) : "—";
  const escapeHtml = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const todayISO = () => new Date().toISOString().slice(0,10);
  const state = { bars: [], candidates: [], source:"Synthetic demo data", demo:true, autoMode:false, autoMeta:null, lastDate:"—", strategy:"all", ledger:[], gates:{}, currentPlan:null, settings:{capital:1000000,riskPct:0.25,costPct:0.15,maxHold:10} };
  const STORAGE_LEDGER = "edgeNsePaperLedgerV1";
  const STORAGE_GATES = "edgeNseResearchGatesV1";
  const STORAGE_SETTINGS = "edgeNsePlanSettingsV1";
  let toastTimer;
  function toast(message) {
    const el=$("#toast"); el.textContent=message; el.classList.add("show");
    clearTimeout(toastTimer); toastTimer=setTimeout(()=>el.classList.remove("show"),3200);
  }
  function loadLocal() {
    try { state.ledger=JSON.parse(localStorage.getItem(STORAGE_LEDGER)||"[]"); } catch { state.ledger=[]; }
    try { state.gates=JSON.parse(localStorage.getItem(STORAGE_GATES)||"{}"); } catch { state.gates={}; }
    try { state.settings={...state.settings,...JSON.parse(localStorage.getItem(STORAGE_SETTINGS)||"{}")}; } catch { /* keep defaults */ }
    $$("[data-gate]").forEach(el=>el.checked=!!state.gates[el.dataset.gate]);
    syncSettingsUI(); updateGateUI();
  }
  function safeStore(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch(err){toast("Browser storage is unavailable or full. Export your data and check browser privacy settings.");return false;}}
  function saveLedger(){ safeStore(STORAGE_LEDGER,state.ledger); renderLedger(); renderOverviewTracker(); }
  function syncSettingsUI(){
    $$('[data-setting="capital"]').forEach(el=>el.value=state.settings.capital);
    $$('[data-setting="riskPct"]').forEach(el=>el.value=state.settings.riskPct);
    $$('[data-setting="costPct"]').forEach(el=>el.value=state.settings.costPct);
    $$('[data-setting="maxHold"]').forEach(el=>el.value=String(state.settings.maxHold));
    const c=$("#capitalInput"),r=$("#riskInput"),cost=$("#costInput");
    if(c)c.value=state.settings.capital;if(r)r.value=state.settings.riskPct;if(cost)cost.value=state.settings.costPct;
    const profile=$("#profileSummary");if(profile)profile.textContent=`${fmtINR(state.settings.capital)} · ${fmtNum(state.settings.riskPct,2)}% risk`;
    const badge=$("#scannerCapitalBadge");if(badge)badge.textContent=fmtINR(state.settings.capital);
    const budget=$("#scannerRiskBudget");if(budget)budget.textContent=`${fmtINR(state.settings.capital*state.settings.riskPct/100)} per-proposal risk budget`;
    const hold=$("#scanHoldPeriod");if(hold)hold.textContent=`Up to ${state.settings.maxHold} sessions`;
  }
  function updateSettingsFromElement(el){
    const key=el.dataset.setting;if(!key)return;
    let value=Number(el.value);
    if(key==="capital")value=Math.max(0,value);
    if(key==="riskPct")value=Math.min(2,Math.max(0.01,value));
    if(key==="costPct")value=Math.min(5,Math.max(0,value));
    if(key==="maxHold")value=[2,5,7,10].includes(value)?value:10;
    if(!Number.isFinite(value))return;
    state.settings[key]=value;safeStore(STORAGE_SETTINGS,state.settings);syncSettingsUI();
    if($("#entryInput"))calcRisk();renderScanner();renderOverviewTracker();
  }
  function normalizeDate(value){
    const v=String(value||"").trim();
    let m=v.match(/^(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?$/);
    if(m){const y=+m[1],mo=+m[2]-1,d=+m[3],dt=new Date(Date.UTC(y,mo,d));if(dt.getUTCFullYear()===y&&dt.getUTCMonth()===mo&&dt.getUTCDate()===d)return dt.toISOString().slice(0,10);return null;}
    m=v.match(/^(\d{1,2})[- ]([A-Za-z]{3})[- ](\d{4})$/);
    if(m){const months={jan:0,feb:1,mar:2,apr:3,may:4,jun:5,jul:6,aug:7,sep:8,oct:9,nov:10,dec:11};const mo=months[m[2].toLowerCase()],d=+m[1],y=+m[3];if(mo!==undefined){const dt=new Date(Date.UTC(y,mo,d));if(dt.getUTCFullYear()===y&&dt.getUTCMonth()===mo&&dt.getUTCDate()===d)return dt.toISOString().slice(0,10);}return null;}
    // Slash-separated dates are accepted only where DD/MM is unambiguous.
    // When both numbers are <= 12, reject rather than silently guessing DD/MM or MM/DD.
    m=v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
    if(m){const d=+m[1],mo=+m[2],y=+m[3];if(d<=12&&mo<=12)return null;if(d<1||d>31||mo<1||mo>12)return null;const dt=new Date(Date.UTC(y,mo-1,d));if(dt.getUTCFullYear()===y&&dt.getUTCMonth()===mo-1&&dt.getUTCDate()===d)return dt.toISOString().slice(0,10);return null;}
    // Numeric DD-MM-YYYY is accepted only when the day/month order is unambiguous.
    // ISO (YYYY-MM-DD) is handled above and remains preferred.
    m=v.match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/);
    if(m){const d=+m[1],mo=+m[2],y=+m[3];if(d<=12&&mo<=12)return null;if(d<1||d>31||mo<1||mo>12)return null;const dt=new Date(Date.UTC(y,mo-1,d));if(dt.getUTCFullYear()===y&&dt.getUTCMonth()===mo-1&&dt.getUTCDate()===d)return dt.toISOString().slice(0,10);}
    return null;
  }
  function parseCSV(text, sourceName="CSV") {
    const lines=String(text||"").replace(/^\uFEFF/,"").split(/\r?\n/).map((line,index)=>({line,index:index+1})).filter(x=>x.line.trim());
    if(lines.length<2) throw new Error("CSV must contain a header and at least one data row.");
    const parseLine = line => { const out=[]; let cur="", quoted=false; for(let i=0;i<line.length;i++){const ch=line[i];if(ch==='"'&&line[i+1]==='"'&&quoted){cur+='"';i++;}else if(ch==='"'){quoted=!quoted;}else if(ch===","&&!quoted){out.push(cur.trim());cur="";}else cur+=ch;}out.push(cur.trim());return out; };
    const headerInfo=parseLine(lines[0].line).map(h=>h.toLowerCase().trim().replace(/[\s-]+/g,"_").replace(/[^a-z0-9_]/g,""));
    const headers=headerInfo.map(h=>h.replace(/_/g,""));
    const aliases={
      date:["date","timestamp","datetime","trad_dt","traddt","biz_dt","bizdt","trad_date","tradedate","marketdate"],
      symbol:["symbol","ticker","stock","tradingsymbol","scname","sc_name","tckrsymb","securityname","ticker_symbol"],
      open:["open","o","opnpric","openprice","openingprice"],
      high:["high","h","hghpric","highprice"],
      low:["low","l","lwpric","lowprice"],
      close:["close","c","adjustedclose","clspric","closeprice","closingprice"],
      volume:["volume","vol","v","tottrdqty","ttltradgvol","totaltradedquantity","totaltradedqty","tradedqty","totaltradedvolume"]
    };
    const ix={};
    for(const [key,opts] of Object.entries(aliases)){
      ix[key]=headers.findIndex(h=>opts.map(x=>x.replace(/_/g,"")).includes(h));
      if(ix[key]<0)throw new Error("Missing required column: "+key+". Accepted NSE aliases include SYMBOL/TckrSymb, TIMESTAMP/TradDt, OPEN/OpnPric, CLOSE/ClsPric and TOTTRDQTY/TtlTradgVol. Standard template: date,symbol,open,high,low,close,volume.");
    }
    const seriesIdx=headers.findIndex(h=>["series","sctysrs","securityseries"].includes(h));
    const rows=[],issues=[];
    for(let i=1;i<lines.length;i++){
      const sourceLine=lines[i].index,cells=parseLine(lines[i].line);
      // NSE bhavcopy can contain multiple series for one symbol/date. This cash-equity screener imports EQ only.
      if(seriesIdx>=0&&String(cells[seriesIdx]||"").trim().toUpperCase()!=="EQ")continue;
      const rawDate=cells[ix.date]||"",date=normalizeDate(rawDate),symbol=String(cells[ix.symbol]||"").trim().toUpperCase();
      const lineIssues=[];
      if(cells.length>headers.length)lineIssues.push(`found ${cells.length} fields but the header has ${headers.length}; check for unquoted commas inside numeric values (quote values like "1,50,000")`);
      if(!date){const numericAmbiguous=/^\s*\d{1,2}[\/-]\d{1,2}[\/-]\d{4}\s*$/.test(rawDate)&&(()=>{const a=rawDate.trim().split(/[\/-]/).map(Number);return a[0]<=12&&a[1]<=12;})();lineIssues.push(numericAmbiguous?`ambiguous numeric date "${rawDate}" (when both day and month are 01–12, convert it to YYYY-MM-DD or DD-MMM-YYYY)`:`invalid date "${rawDate||"(blank)"}" (use YYYY-MM-DD, DD-MMM-YYYY, or an unambiguous DD/MM/YYYY / DD-MM-YYYY)`);}
      if(!symbol)lineIssues.push("missing symbol");
      const parseNumber=raw=>{const v=String(raw??"").trim();if(!v)return NaN;const cleaned=v.replace(/[₹,\s]/g,"").replace(/^\((.*)\)$/,"-$1");return cleaned?Number(cleaned):NaN;};
      const r={date,symbol,sourceLine,sourceFile:sourceName};
      for(const k of ["open","high","low","close","volume"]){r[k]=parseNumber(cells[ix[k]]);if(!Number.isFinite(r[k]))lineIssues.push(`invalid or blank ${k} value "${cells[ix[k]]??""}"`);}
      if(!lineIssues.length){
        if(r.open<=0||r.high<=0||r.low<=0||r.close<=0||r.volume<0||r.high<Math.max(r.open,r.close,r.low)||r.low>Math.min(r.open,r.close,r.high))lineIssues.push("impossible OHLC or negative volume values");
      }
      if(lineIssues.length)issues.push(`${sourceName} · line ${sourceLine}: ${lineIssues.join("; ")}.`);
      else rows.push(r);
    }
    if(issues.length){const err=new Error(`CSV rejected: ${issues.length} row issue(s). Fix the listed lines; no partial import was made.`);err.details=issues;throw err;}
    if(!rows.length){
      if(seriesIdx>=0)throw new Error("No EQ-series rows were found. This screener imports the NSE cash-equity EQ series; non-EQ series are excluded.");
      throw new Error("No valid OHLCV rows found.");
    }
    rows.sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.date.localeCompare(b.date));
    return rows;
  }
  function validateBars(bars) {
    const errors=[],warnings=[],seen=new Map(),counts=new Map(),invalidRows=[];
    for(const r of bars){
      const key=r.symbol+"|"+r.date;
      const rowLocation=row=>`${row.sourceFile||"CSV"} line ${row.sourceLine??"unknown"}`;
      if(seen.has(key))errors.push(`Duplicate symbol/date row ${r.symbol} ${r.date} (${rowLocation(seen.get(key))} and ${rowLocation(r)}).`);
      else seen.set(key,r);
      counts.set(r.symbol,(counts.get(r.symbol)||0)+1);
      if(!Number.isFinite(r.open)||!Number.isFinite(r.high)||!Number.isFinite(r.low)||!Number.isFinite(r.close)||!Number.isFinite(r.volume)||r.open<=0||r.high<=0||r.low<=0||r.close<=0||r.volume<0||r.high<Math.max(r.open,r.close,r.low)||r.low>Math.min(r.open,r.close,r.high))invalidRows.push(`Line ${r.sourceLine??"unknown"}: invalid OHLCV values for ${r.symbol} ${r.date}.`);
    }
    errors.push(...invalidRows);
    const symbols=[...counts.keys()],dates=bars.map(r=>r.date).sort();
    if(!bars.length)errors.push("Dataset is empty.");
    const short=symbols.filter(s=>counts.get(s)<200);
    if(short.length)warnings.push(`${short.length} symbol(s) have fewer than 200 rows; the SMA200 trend filter cannot qualify them. Symbols: ${short.slice(0,8).join(", ")}${short.length>8?", …":""}.`);
    if(!symbols.some(s=>["NIFTY50","NIFTY_50"].includes(s)))warnings.push("NIFTY50 benchmark is missing. Relative-strength signals are disabled until a NIFTY50 or NIFTY_50 series is included.");
    // Flag abrupt close-to-close discontinuities as a corporate-action/data-quality review item.
    // The website does not adjust prices or infer that a split/bonus definitely occurred.
    const bySymbol=new Map();
    for(const r of bars){if(!bySymbol.has(r.symbol))bySymbol.set(r.symbol,[]);bySymbol.get(r.symbol).push(r);}
    const jumps=[];
    for(const [symbol,rows] of bySymbol){
      rows.sort((a,b)=>a.date.localeCompare(b.date));
      for(let i=1;i<rows.length;i++){
        const prev=rows[i-1].close,cur=rows[i].close;
        if(prev>0&&cur>0&&Math.abs(cur/prev-1)>=0.4){
          const pct=(cur/prev-1)*100;
          jumps.push(`${symbol} ${rows[i].date} (${pct>0?"+":""}${pct.toFixed(1)}%)`);
        }
      }
    }
    if(jumps.length){const shown=jumps.slice(0,10);warnings.push(`${jumps.length} possible corporate-action/data discontinuity event(s): ${shown.join(", ")}${jumps.length>shown.length?`, and ${jumps.length-shown.length} more`:""}. Review adjusted prices and source data before screening; this warning does not adjust bars.`);}
    warnings.push("Source authenticity, corporate actions and point-in-time universe membership are not verified by this browser.");
    return {passed:errors.length===0,errors,warnings,issues:[...errors,...warnings],symbols,dates};
  }
  function indicators(rows) {
    const a=rows.slice().sort((x,y)=>x.date.localeCompare(y.date));
    const close=a.map(x=>x.close), high=a.map(x=>x.high), low=a.map(x=>x.low), volume=a.map(x=>x.volume);
    const prefixCache=new WeakMap();
    const sma=(arr,n,i)=>{if(i+1<n)return NaN;let prefix=prefixCache.get(arr);if(!prefix){prefix=[0];for(const value of arr)prefix.push(prefix[prefix.length-1]+value);prefixCache.set(arr,prefix);}return (prefix[i+1]-prefix[i+1-n])/n;};
    const ema=(arr,n)=>{const out=[];const k=2/(n+1);arr.forEach((v,i)=>out.push(i===0?v:v*k+out[i-1]*(1-k)));return out;};
    const ema20=ema(close,20), rsiSeries=Array(a.length).fill(NaN), tr=Array(a.length).fill(NaN), atr=Array(a.length).fill(NaN);
    let avgG=0,avgL=0;
    for(let i=1;i<a.length;i++){const d=close[i]-close[i-1];if(i<=14){avgG+=Math.max(0,d)/14;avgL+=Math.max(0,-d)/14;}else{avgG=(avgG*13+Math.max(0,d))/14;avgL=(avgL*13+Math.max(0,-d))/14;}
      if(i>=14){const g=i===14?Array.from({length:14},(_,j)=>Math.max(0,close[j+1]-close[j])).reduce((x,y)=>x+y,0)/14:avgG;const l=i===14?Array.from({length:14},(_,j)=>Math.max(0,close[j]-close[j+1])).reduce((x,y)=>x+y,0)/14:avgL; rsiSeries[i]=l===0?(g>0?100:50):100-100/(1+g/l);}}
    for(let i=0;i<a.length;i++){const prev=i?close[i-1]:close[i];tr[i]=Math.max(high[i]-low[i],Math.abs(high[i]-prev),Math.abs(low[i]-prev));if(i>=13)atr[i]=tr.slice(i-13,i+1).reduce((x,y)=>x+y,0)/14;}
    const turnover=close.map((c,j)=>c*volume[j]);
    return a.map((r,i)=>({...r,sma20:sma(close,20,i),sma50:sma(close,50,i),sma200:sma(close,200,i),ema20:ema20[i],rsi:rsiSeries[i],priorHigh20:i>=20?Math.max(...high.slice(i-20,i)):NaN,priorHigh55:i>=55?Math.max(...high.slice(i-55,i)):NaN,avgVol:sma(volume,20,i),avgTurn:sma(turnover,20,i),atr:atr[i],relVol:sma(volume,20,i)>0?volume[i]/sma(volume,20,i):NaN,momentum60:i>=60?close[i]/close[i-60]-1:NaN,prevClose:close[i-1],prevEma:ema20[i-1]}));
  }
  function computeCandidates(bars) {
    const all=[],bySymbol=new Map();
    bars.forEach(r=>{if(!bySymbol.has(r.symbol))bySymbol.set(r.symbol,[]);bySymbol.get(r.symbol).push(r);});
    const benchmark=bySymbol.get("NIFTY50")||bySymbol.get("NIFTY_50")||[];
    const benchInd=benchmark.length?indicators(benchmark):[],benchMom=new Map(benchInd.map(r=>[r.date,r.momentum60]));
    const datasetEnd=bars.reduce((latest,r)=>r.date>latest?r.date:latest,"");
    for(const [symbol,raw] of bySymbol.entries()){
      if(/^NIFTY/.test(symbol)||symbol==="DEMO_INDEX")continue;
      const ind=indicators(raw); if(!ind.length)continue;
      // Only evaluate the latest available bar per symbol. Never surface a historical setup as a current signal.
      const r=ind[ind.length-1];
      // Ignore symbols whose latest bar is materially behind the dataset's latest session.
      if(!r.date||!datasetEnd||((Date.parse(datasetEnd)-Date.parse(r.date))/86400000)>10)continue;
      if(!Number.isFinite(r.atr)||!Number.isFinite(r.sma200)||!Number.isFinite(r.sma50)||!Number.isFinite(r.rsi)||!Number.isFinite(r.avgTurn)||r.avgTurn<10000000)continue;
      const trend=r.close>r.sma50&&r.sma50>r.sma200;
      const breakout=r.close>r.priorHigh20&&r.relVol>=1.5&&r.rsi>=52&&r.rsi<=72&&trend;
      const nearEma=Number.isFinite(r.ema20)&&Math.abs(r.close-r.ema20)/r.ema20<=.025;
      const pullback=trend&&nearEma&&r.close>r.ema20&&r.prevClose<=r.prevEma&&r.rsi>=42&&r.rsi<=62;
      const benchmarkReturn=benchMom.get(r.date),relative=Number.isFinite(benchmarkReturn)&&Number.isFinite(r.momentum60)&&(r.momentum60-benchmarkReturn)>0&&trend&&r.close>r.priorHigh20;
      const types=[];if(breakout)types.push("breakout");if(pullback)types.push("pullback");if(relative)types.push("relative_strength");if(!types.length)continue;
      const type=types[0],trendScore=Math.max(0,Math.min(100,(r.close/r.sma50-1)*500)),volumeScore=Math.max(0,Math.min(100,(r.relVol||0)*30)),rsiScore=Math.max(0,100-Math.abs(r.rsi-60)*3),momentumScore=Math.max(0,Math.min(100,((r.momentum60||0)+.1)*500));
      const score=Math.round(.25*trendScore+.2*volumeScore+.25*rsiScore+.3*momentumScore);
      all.push({...r,symbol,strategy:type,score,stop:r.close-2*r.atr,strategies:types});
    }
    return all.sort((a,b)=>b.score-a.score||b.date.localeCompare(a.date));
  }
  function setupLabel(s){return s==="breakout"?"Breakout":s==="pullback"?"Pullback":s==="relative_strength"?"Relative strength":"Manual plan";}
  function setupClass(s){return "setup-"+s;}
  function money(n){return fmtINR(n,2);}
  function qualityTier(score){const n=Number(score)||0;return n>=70?"High":n>=50?"Medium":"Low";}
  function tierClass(score){return "tier-"+qualityTier(score).toLowerCase();}
  function activeRiskInLedger(){return state.ledger.filter(x=>(x.status==="Planned"||x.status==="Open")&&!x.reviewRequired).reduce((sum,x)=>sum+Math.max(0,Number(x.qty)||0)*Math.max(0,(Number(x.entry)||Number(x.entryTrigger)||0)-(Number(x.stop)||0)),0);}
  function buildTradePlan(r){
    const s=state.settings,capital=Number(s.capital)||0,riskPct=Number(s.riskPct)||0,costPct=Number(s.costPct)||0;
    const close=Number(r.close)||0,atr=Number(r.atr??r.atr14_sma)||((close-Number(r.stop||0))/2);
    const entryTrigger=close*1.0025;
    const stop=Number(r.stop)>0&&Number(r.stop)<entryTrigger?Number(r.stop):(atr>0?close-2*atr:close*.96);
    const riskPerShare=entryTrigger-stop,perTradeBudget=capital*riskPct/100;
    const remainingPortfolioRisk=Math.max(0,capital*.015-activeRiskInLedger());
    const riskBudget=Math.min(perTradeBudget,remainingPortfolioRisk);
    const qty=riskPerShare>0?Math.max(0,Math.min(Math.floor(riskBudget/riskPerShare),Math.floor(capital*.15/entryTrigger))):0;
    const positionValue=qty*entryTrigger,estimatedCost=positionValue*costPct/100;
    const target1=entryTrigger+riskPerShare,target2=entryTrigger+2*riskPerShare;
    return {symbol:r.symbol,strategy:r.strategy||"breakout",score:Number(r.score)||0,tier:qualityTier(r.score),signalDate:r.date||state.lastDate,signalClose:close,entry:entryTrigger,entryTrigger,stop,target1,target2,target:target2,qty,capitalAtProposal:capital,riskPctAtProposal:riskPct,costPctAtProposal:costPct,riskBudget,riskPerShare,positionValue,estimatedCost,plannedRisk:qty*riskPerShare,rewardRisk:2,relVol:Number(r.relVol??r.rel_vol)||0,rsi:Number(r.rsi)||0,atr,validForSessions:3,maxHoldSessions:Number(s.maxHold)||10,quantityAvailable:qty>0,remainingPortfolioRisk};
  }
  function proposalReason(r){
    const parts=[];
    if(r.strategy==="breakout")parts.push("20-session breakout and volume confirmation");
    else if(r.strategy==="pullback")parts.push("trend pullback reclaiming EMA20");
    else parts.push("relative strength versus NIFTY 50");
    if(Number(r.relVol)>=1.5)parts.push("elevated relative volume");
    if(Number(r.rsi)>=70)parts.push("RSI is overbought; size and entry need extra scrutiny");
    return parts.join(" · ");
  }
  function renderCandidateRows(rows,target,withRank=false){
    const tbody=$(target);if(!tbody)return;
    if(!rows.length){tbody.innerHTML="";return;}
    tbody.innerHTML=rows.map((r,i)=>`<tr>
      ${withRank?`<td>${String(i+1).padStart(2,"0")}</td>`:""}
      <td><div class="symbol-cell"><span class="symbol-avatar">${escapeHtml(String(r.symbol||"??").slice(0,2))}</span><span>${escapeHtml(r.symbol)}<span class="symbol-sub">${escapeHtml(r.date||"")}</span></span></div></td>
      <td><span class="setup-pill ${setupClass(r.strategy)}">${setupLabel(r.strategy)}</span></td>
      <td>${money(Number(r.close)||0)}</td><td>${fmtNum(Number(r.rsi),1)}</td><td>${fmtNum(Number(r.relVol??r.rel_vol),2)}×</td>
      <td><div class="score-cell"><span class="score-number">${Number(r.score)||0}</span><span class="score-bar"><span style="width:${Math.max(0,Math.min(100,Number(r.score)||0))}%"></span></span><span class="tier-pill ${tierClass(r.score)}">${qualityTier(r.score)}</span></div></td>
      ${withRank?`<td><button class="action-link" data-propose-symbol="${escapeHtml(r.symbol)}">＋ Proposal</button></td>`:`<td><span class="state-pill">Screened</span></td>`}
    </tr>`).join("");
  }
  function renderOverview(){
    const latestBySymbol=new Map();for(const r of state.candidates){const old=latestBySymbol.get(r.symbol);if(!old||r.date>old.date)latestBySymbol.set(r.symbol,r);}
    const latest=[...latestBySymbol.values()].sort((a,b)=>b.score-a.score);
    const rows=latest.filter(r=>state.strategy==="all"||r.strategy===state.strategy).slice(0,8);
    renderCandidateRows(rows,"#candidateTable",false);
    $("#overviewEmpty").classList.toggle("hidden",rows.length>0);
    $("#overviewEmpty").textContent=state.autoMode&&state.candidates.length===0
      ?"The automated pipeline has no qualified setups to show for the latest completed session, or the data-readiness gate is not satisfied. This can be a valid outcome. Review Data & research for freshness, history length, benchmark status and corporate-action warnings."
      :state.demo&&state.candidates.length===0
        ?"No candidates qualify on the latest synthetic bar. Demo data is for interface testing, not signal generation."
        :"No candidates qualify for this overview filter on the latest available bar. Open the scanner, adjust filters, or upload more validated history.";
    $("#setupsMetric").textContent=String(latest.length).padStart(2,"0");
    $("#positionsMetric").textContent=String(state.ledger.filter(x=>x.status==="Planned"||x.status==="Open").length).padStart(2,"0");
    $("#candidateCount").textContent=`Showing ${rows.length} of ${latest.length}`;
    $("#tableFootnote").textContent=state.autoMode?`Automated NSE EOD screen · ${state.autoMeta?.latest_market_date||state.lastDate} · technical rules only`:state.demo?"Synthetic demo data · not a recommendation":"Uploaded CSV · technical rules only";
    renderOverviewTracker();drawEquityChart();
  }
  function proposalCard(r){
    const p=buildTradePlan(r),unavailable=!p.quantityAvailable;
    const score=Math.max(0,Math.min(100,p.score));
    return `<article class="proposal-card">
      <div class="proposal-card-top"><div class="proposal-identity"><span class="symbol-avatar">${escapeHtml(p.symbol.slice(0,2))}</span><div><div class="proposal-symbol">${escapeHtml(p.symbol)}</div><div class="proposal-subtitle">${escapeHtml(p.signalDate)} · ${escapeHtml(setupLabel(p.strategy))}</div></div></div><div class="score-ring ${tierClass(score)}"><strong>${score}</strong><small>/100</small></div></div>
      <div class="proposal-card-badges"><span class="setup-pill ${setupClass(p.strategy)}">${escapeHtml(setupLabel(p.strategy))}</span><span class="tier-pill ${tierClass(score)}">${escapeHtml(p.tier)} setup quality</span><span class="holding-pill">2–${p.maxHoldSessions} sessions</span></div>
      <div class="proposal-reason">${escapeHtml(proposalReason({...r,relVol:p.relVol,rsi:p.rsi}))}</div>
      <div class="proposal-price-grid"><div><span>ENTRY TRIGGER</span><strong>${money(p.entryTrigger)}</strong><small>Next-session stop-entry</small></div><div class="stop-rail"><span>STOP LOSS</span><strong>${money(p.stop)}</strong><small>${fmtNum((p.entryTrigger-p.stop)/p.entryTrigger*100,2)}% below entry</small></div><div class="target-rail"><span>TAKE PROFIT 1</span><strong>${money(p.target1)}</strong><small>1R reference</small></div><div class="target-rail target-rail-strong"><span>TAKE PROFIT 2</span><strong>${money(p.target2)}</strong><small>2R · proposal target</small></div></div>
      <div class="proposal-metrics"><div><span>SUGGESTED QTY</span><strong>${p.qty.toLocaleString("en-IN")}</strong></div><div><span>POSITION VALUE</span><strong>${fmtINR(p.positionValue)}</strong></div><div><span>PLANNED RISK</span><strong>${fmtINR(p.plannedRisk)}</strong></div><div><span>EST. ROUND-TRIP COST</span><strong>${fmtINR(p.estimatedCost,2)}</strong></div></div>
      <div class="proposal-card-bottom"><div class="proposal-validity"><span class="pulse-dot"></span><span>Entry valid for ${p.validForSessions} sessions · max hold ${p.maxHoldSessions}</span></div><button class="button button-primary proposal-button" data-propose-symbol="${escapeHtml(p.symbol)}" ${unavailable?"disabled":""}>${unavailable?"Risk capacity unavailable":"Create proposal & track"}</button></div>
      ${unavailable?`<div class="proposal-warning">The aggregate open-risk cap or 15% notional cap leaves no room for this position. Do not increase the risk limit automatically.</div>`:""}
    </article>`;
  }
  function renderScanner(){
    const strategy=$("#strategyFilter").value,min=Number($("#minScore").value),sort=$("#sortSignals").value;
    const latestBySymbol=new Map();for(const r of state.candidates){const old=latestBySymbol.get(r.symbol);if(!old||r.date>old.date)latestBySymbol.set(r.symbol,r);}
    let rows=[...latestBySymbol.values()].filter(r=>(strategy==="all"||r.strategy===strategy)&&(Number(r.score)||0)>=min);
    rows.sort((a,b)=>sort==="volume"?(Number(b.relVol??b.rel_vol)||0)-(Number(a.relVol??a.rel_vol)||0):sort==="rsi"?(Number(b.rsi)||0)-(Number(a.rsi)||0):sort==="symbol"?a.symbol.localeCompare(b.symbol):(Number(b.score)||0)-(Number(a.score)||0));
    $("#scanMatches").textContent=rows.length;$("#scanAsOf").textContent=state.lastDate;
    const grid=$("#proposalGrid");if(grid)grid.innerHTML=rows.map(proposalCard).join("");
    $("#scannerEmpty").classList.toggle("hidden",rows.length>0);
    $("#scannerEmpty").textContent=state.autoMode&&state.candidates.length===0
      ?"The automated screen has no qualified candidates or the data-readiness gate is not satisfied. Zero signals can be a legitimate result."
      :state.demo&&state.candidates.length===0
        ?"The scanner is working. Synthetic demo data does not intentionally manufacture latest-bar signals."
        :state.candidates.length===0
          ?"No setup qualifies on the latest bar. Check data freshness, SMA200 history and the ₹1 crore 20-session average-turnover floor."
          :"No candidates meet these filters. Try a lower setup-score threshold or a different strategy.";
    syncSettingsUI();
  }
  function decodeRecentBars(payload){
    if(!Array.isArray(payload?.recent_eod_bars))return [];
    return payload.recent_eod_bars.map(row=>Array.isArray(row)?({date:row[0],symbol:row[1],open:Number(row[2]),high:Number(row[3]),low:Number(row[4]),close:Number(row[5]),ema20:row[6]==null?NaN:Number(row[6]),movePct:row[7]==null?NaN:Number(row[7])}):row).filter(r=>r.date&&r.symbol&&Number.isFinite(r.close)&&Number.isFinite(r.high)&&Number.isFinite(r.low));
  }
  function setTradeMonitorState(item,label,action=false){item.monitorStatus=label;item.exitActionRequired=!!action;}
  function finalizePaperTrade(item,bar,exitPrice,outcome,reason){
    const qty=Number(item.qty)||0,entry=Number(item.fillPrice||item.entry)||0,cost=Number(item.estimatedCost)||0;
    item.status="Closed";item.outcome=outcome;item.exitPrice=Number(exitPrice);item.exitDate=bar?.date||item.lastPriceDate||state.lastDate;
    item.exitReason=reason;item.realizedPnl=(item.exitPrice-entry)*qty-cost;item.lastPrice=Number(bar?.close)||item.exitPrice;item.lastPriceDate=item.exitDate;
    item.closedAt=item.exitDate;item.manualClosed=false;setTradeMonitorState(item,`${outcome.replaceAll("_"," ")} · ${reason}`,true);
  }
  function reconcileLedgerWithEod(payload){
    const bars=decodeRecentBars(payload);
    if(!bars.length){state.eodMonitoringMessage="EOD history snapshot not published yet. The pipeline must complete one refresh after this release.";renderLedger();renderOverviewTracker();return;}
    const dates=Array.isArray(payload.recent_market_dates)?payload.recent_market_dates.slice().sort():[...new Set(bars.map(b=>b.date))].sort();
    const firstDate=dates[0],barMap=new Map();
    bars.forEach(b=>{const key=`${b.date}|${b.symbol}`;barMap.set(key,b);});
    let changed=false;
    for(const item of state.ledger){
      if(item.monitorEnabled===false||!["Planned","Open"].includes(item.status))continue;
      if(item.reviewRequired){
        const latest=bars.filter(b=>b.symbol===item.symbol).at(-1);if(latest){item.lastPrice=latest.close;item.lastPriceDate=latest.date;}continue;
      }
      const lastEval=item.lastEvaluatedDate||item.createdMarketDate||item.createdAt||"0000-00-00";
      const calendarGapDays=lastEval<firstDate?(Date.parse(firstDate)-Date.parse(lastEval))/86400000:0;
      if(lastEval<firstDate&&calendarGapDays>7){
        item.reviewRequired=true;item.lastPriceDate=payload.latest_market_date||dates.at(-1);const latest=bars.filter(b=>b.symbol===item.symbol).at(-1);if(latest)item.lastPrice=latest.close;
        setTradeMonitorState(item,"DATA GAP · recent EOD window no longer covers this proposal; manual review required",true);changed=true;continue;
      }
      const nextDates=dates.filter(d=>d>lastEval);
      for(const date of nextDates){
        const bar=barMap.get(`${date}|${item.symbol}`);
        if(!bar){item.lastEvaluatedDate=date;if(item.status==="Planned"){item.sessionsSinceSignal=(Number(item.sessionsSinceSignal)||0)+1;if(item.sessionsSinceSignal>=Number(item.validForSessions||3)){item.status="Closed";item.outcome="EXPIRED";item.exitDate=date;item.exitReason="No valid bar/entry trigger within entry window";setTradeMonitorState(item,"EXPIRED · no entry trigger",false);}}else{setTradeMonitorState(item,"MISSING BAR · price could not be verified",true);}changed=true;continue;}
        item.lastPrice=bar.close;item.lastPriceDate=date;
        if(Number.isFinite(bar.movePct)&&Math.abs(bar.movePct)>=40){item.reviewRequired=true;item.corporateActionDate=date;item.corporateActionMove=bar.movePct;item.lastEvaluatedDate=date;setTradeMonitorState(item,`DATA REVIEW · ${fmtNum(bar.movePct,1)}% one-day move on ${date}; automated exit logic paused`,true);changed=true;break;}
        if(item.status==="Planned"){
          item.sessionsSinceSignal=(Number(item.sessionsSinceSignal)||0)+1;
          if(bar.high>=Number(item.entryTrigger||item.entry)){
            const trigger=Number(item.entryTrigger||item.entry),fill=bar.open>=trigger?bar.open:trigger;
            const oldQty=Number(item.qty)||0,capital=Number(item.capitalAtProposal)||Number(state.settings.capital),riskBudget=Number(item.riskBudget)||capital*(Number(item.riskPctAtProposal)||state.settings.riskPct)/100;
            const newQty=Math.min(oldQty,Math.floor(riskBudget/Math.max(.000001,fill-Number(item.stop))),Math.floor(capital*.15/fill));
            if(newQty<1){item.status="Closed";item.outcome="EXPIRED";item.exitDate=date;item.exitReason="Opening gap invalidated the planned risk/notional limits; no paper fill recorded";setTradeMonitorState(item,"EXPIRED · gap invalidated sizing",false);item.lastEvaluatedDate=date;changed=true;continue;}
            item.status="Open";item.fillPrice=fill;item.entry=fill;item.entryDate=date;item.qty=newQty;item.holdingSessions=0;item.emaBreakCount=0;
            const r=fill-Number(item.stop);item.target1=fill+r;item.target2=fill+2*r;item.target=item.target2;
            item.positionValue=fill*newQty;item.plannedRisk=r*newQty;item.estimatedCost=item.positionValue*(Number(item.costPctAtProposal)||0)/100;
          }else if(item.sessionsSinceSignal>=Number(item.validForSessions||3)){
            item.status="Closed";item.outcome="EXPIRED";item.exitDate=date;item.exitReason="Entry trigger was not reached within the proposal-validity window";setTradeMonitorState(item,"EXPIRED · entry not triggered",false);item.lastEvaluatedDate=date;changed=true;continue;
          }else{
            setTradeMonitorState(item,`WAITING ENTRY · session ${item.sessionsSinceSignal}/${item.validForSessions||3}`,false);item.lastEvaluatedDate=date;changed=true;continue;
          }
        }
        if(item.status==="Open"){
          item.holdingSessions=(Number(item.holdingSessions)||0)+1;
          const stop=Number(item.stop),tp1=Number(item.target1),tp2=Number(item.target2);
          const stopHit=bar.low<=stop,target2Hit=bar.high>=tp2;
          if(stopHit){const exitPrice=bar.open<stop?bar.open:stop;finalizePaperTrade(item,bar,exitPrice,"FAIL",target2Hit?"Stop and target touched on same daily bar; stop assumed first":"Initial stop-loss reached");}
          else if(target2Hit){finalizePaperTrade(item,bar,tp2,"PASS","Target 2 reached before stop-loss");}
          else{
            if(bar.high>=tp1&&!item.target1Reached){item.target1Reached=true;item.target1Date=date;}
            if(Number.isFinite(bar.ema20)&&bar.close<bar.ema20)item.emaBreakCount=(Number(item.emaBreakCount)||0)+1;else item.emaBreakCount=0;
            if(Number(item.emaBreakCount)>=2){finalizePaperTrade(item,bar,bar.close,"FAIL","Two consecutive EOD closes below EMA20");}
            else if(Number(item.holdingSessions)>=Number(item.maxHoldSessions||10)){finalizePaperTrade(item,bar,bar.close,"TIME_EXIT","Maximum holding window reached before target 2");}
            else if(item.target1Reached){setTradeMonitorState(item,"TARGET 1 HIT · review partial profit / protect gains; target 2 pending",true);}
            else if(Number(item.emaBreakCount)===1){setTradeMonitorState(item,"WATCH · first EOD close below EMA20; confirmation required",true);}
            else{setTradeMonitorState(item,`OPEN · day ${item.holdingSessions}/${item.maxHoldSessions||10}`,false);}
          }
        }
        item.lastEvaluatedDate=date;changed=true;
      }
    }
    state.eodMonitoringMessage=`EOD tracker reconciled through ${payload.latest_market_date||dates.at(-1)} using ${dates.length} recent sessions.`;
    if(changed)safeStore(STORAGE_LEDGER,state.ledger);
    renderLedger();renderOverviewTracker();
  }
  function ledgerPnl(item){
    const qty=Number(item.qty)||0,entry=Number(item.fillPrice||item.entry)||0;
    const price=item.status==="Closed"&&Number.isFinite(Number(item.exitPrice))?Number(item.exitPrice):Number(item.lastPrice);
    if(!Number.isFinite(price)||!entry||!qty||item.status==="Planned")return 0;
    return (price-entry)*qty-(Number(item.estimatedCost)||0);
  }
  function trackerStatusClass(item){
    if(item.monitorEnabled===false)return "monitor-muted";
    if(item.reviewRequired)return "monitor-review";
    if(item.outcome==="PASS")return "monitor-pass";
    if(item.outcome==="FAIL")return "monitor-fail";
    if(["TIME_EXIT","EXPIRED","MANUAL_EXIT"].includes(item.outcome)||item.status==="Cancelled")return "monitor-muted";
    if(item.status==="Open"&&item.exitActionRequired)return "monitor-action";
    return item.status==="Open"?"monitor-open":"monitor-wait";
  }
  function trackerCard(item){
    const pnl=ledgerPnl(item),price=Number(item.lastPrice),hasPrice=Number.isFinite(price)&&price>0;
    const entry=Number(item.fillPrice||item.entryTrigger||item.entry)||0;
    const pnlClass=pnl>0?"pnl-positive":pnl<0?"pnl-negative":"";
    const label=item.reviewRequired?"MANUAL DATA REVIEW":item.outcome==="PASS"?(item.exitActionRequired?"PASS · EXIT SIGNAL":"PASSED"):item.outcome==="FAIL"?(item.exitActionRequired?"FAIL · EXIT SIGNAL":"FAILED"):item.outcome==="TIME_EXIT"?(item.exitActionRequired?"TIME EXIT · EXIT SIGNAL":"TIME EXIT"):item.outcome==="MANUAL_EXIT"?"MANUAL EXIT":item.status==="Cancelled"?"CANCELLED":item.outcome==="EXPIRED"?"EXPIRED":item.monitorEnabled===false?"MANUAL PLAN":item.status==="Open"?"OPEN · TRACKING":"WAITING FOR ENTRY";
    const holding=item.entryDate?`${Number(item.holdingSessions)||0}/${Number(item.maxHoldSessions)||10} sessions held`:`Entry valid ${item.validForSessions||3} sessions`;
    const action=item.reviewRequired?"Manual review needed; automated rule evaluation is paused.":item.monitorStatus||"Awaiting EOD update.";
    return `<article class="tracker-card ${item.reviewRequired?"tracker-card-review":""}">
      <div class="tracker-card-head"><div class="proposal-identity"><span class="symbol-avatar">${escapeHtml(String(item.symbol||"??").slice(0,2))}</span><div><div class="proposal-symbol">${escapeHtml(item.symbol||"MANUAL")}</div><div class="proposal-subtitle">${escapeHtml(item.createdMarketDate||item.createdAt||"")} · ${escapeHtml(setupLabel(item.strategy||"breakout"))}</div></div></div><span class="tracker-status ${trackerStatusClass(item)}">${label}</span></div>
      <div class="tracker-price-grid"><div><span>${item.entryDate?"FILLED ENTRY":"ENTRY TRIGGER"}</span><strong>${money(entry)}</strong><small>${item.entryDate?escapeHtml(item.entryDate):"Not filled"}</small></div><div><span>STOP LOSS</span><strong>${money(Number(item.stop)||0)}</strong><small>Initial risk rail</small></div><div><span>TARGET 1</span><strong>${money(Number(item.target1)||0)}</strong><small>${item.target1Reached?"Reached · "+escapeHtml(item.target1Date||""):"1R"}</small></div><div><span>TARGET 2</span><strong>${money(Number(item.target2||item.target)||0)}</strong><small>2R proposal target</small></div></div>
      <div class="tracker-bottom-grid"><div><span>LAST EOD CLOSE</span><strong>${hasPrice?money(price):"Waiting for data"}</strong><small>${escapeHtml(item.lastPriceDate||"—")}</small></div><div><span>PAPER EXIT PRICE</span><strong>${item.status==="Closed"&&Number.isFinite(Number(item.exitPrice))?money(Number(item.exitPrice)):"—"}</strong><small>${escapeHtml(item.exitDate||item.entryDate||"Pending")}</small></div><div><span>QUANTITY</span><strong>${(Number(item.qty)||0).toLocaleString("en-IN")}</strong><small>${fmtINR(Number(item.positionValue)||0)} planned value</small></div><div><span>MARK-TO-MARKET / REALIZED</span><strong class="${pnlClass}">${pnl>0?"+":""}${fmtINR(pnl,2)}</strong><small>${hasPrice&&entry?fmtNum((price/entry-1)*100,2)+"% move":"Not marked"}</small></div><div><span>TIME WINDOW</span><strong>${holding}</strong><small>Setup score ${Number(item.score)||"—"} · ${escapeHtml(item.qualityTier||qualityTier(item.score))}</small></div></div>
      <div class="tracker-action ${trackerStatusClass(item)}"><span class="tracker-action-icon">${item.exitActionRequired||item.reviewRequired?"⚑":"↻"}</span><div><strong>${item.exitActionRequired?"ACTION / REVIEW":item.status==="Open"?"DAILY STATUS":"PROPOSAL STATUS"}</strong><p>${escapeHtml(action)}</p></div></div>
      <div class="tracker-card-actions">${item.exitActionRequired?`<button class="button button-small button-secondary" data-ack-action="${escapeHtml(item.id)}">Acknowledge exit action</button>`:""}${item.status==="Open"&&hasPrice?`<button class="button button-small button-secondary" data-manual-close="${escapeHtml(item.id)}">Mark exited at latest close</button>`:""}${item.status==="Planned"?`<button class="button button-small button-secondary" data-cancel-proposal="${escapeHtml(item.id)}">Cancel proposal</button>`:""}<button class="action-link danger-link" data-delete-id="${escapeHtml(item.id)}">Delete</button></div>
    </article>`;
  }
  function renderTrackerCards(target,items,limit=Infinity){const el=$(target);if(!el)return;const list=items.slice().reverse().slice(0,limit);el.innerHTML=list.map(trackerCard).join("");}
  function renderOverviewTracker(){
    const active=state.ledger.filter(x=>x.status==="Planned"||x.status==="Open");
    const exits=state.ledger.filter(x=>x.exitActionRequired||x.reviewRequired);
    const passed=state.ledger.filter(x=>x.outcome==="PASS").length,failed=state.ledger.filter(x=>x.outcome==="FAIL").length;
    const set=(id,value)=>{const el=$(id);if(el)el.textContent=String(value);};
    set("#monitorActive",active.length);set("#monitorExit",exits.length);set("#monitorPassed",passed);set("#monitorFailed",failed);
    renderTrackerCards("#overviewTrackerList",state.ledger.filter(x=>x.status==="Planned"||x.status==="Open"||x.reviewRequired||x.exitActionRequired),3);
    const empty=$("#overviewTrackerEmpty");if(empty)empty.classList.toggle("hidden",state.ledger.some(x=>x.status==="Planned"||x.status==="Open"||x.reviewRequired||x.exitActionRequired));
  }
  function drawEquityChart(){
    const canvas=$("#equityChart");if(!canvas)return;
    const rect=canvas.getBoundingClientRect();const dpr=window.devicePixelRatio||1;
    canvas.width=Math.max(1,rect.width*dpr);canvas.height=Math.max(1,rect.height*dpr);
    const ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);
    const w=rect.width,h=rect.height,pad={l:10,r:8,t:10,b:18};
    ctx.clearRect(0,0,w,h);
    const values=Array.from({length:54},(_,i)=>1000000+Math.sin(i*.28)*15000+i*2100+Math.sin(i*.7)*8000);
    const min=Math.min(...values)-10000,max=Math.max(...values)+10000;
    ctx.strokeStyle="#24334a";ctx.lineWidth=1;
    for(let i=0;i<4;i++){const y=pad.t+(h-pad.t-pad.b)*i/3;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();}
    const baseY=pad.t+(h-pad.t-pad.b)*(max-1000000)/(max-min);
    ctx.setLineDash([4,5]);ctx.strokeStyle="#63748d";ctx.beginPath();ctx.moveTo(pad.l,baseY);ctx.lineTo(w-pad.r,baseY);ctx.stroke();ctx.setLineDash([]);
    const pts=values.map((v,i)=>({x:pad.l+(w-pad.l-pad.r)*i/(values.length-1),y:pad.t+(h-pad.t-pad.b)*(max-v)/(max-min)}));
    const grad=ctx.createLinearGradient(0,pad.t,0,h-pad.b);grad.addColorStop(0,"rgba(71,224,172,.22)");grad.addColorStop(1,"rgba(71,224,172,0)");
    ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.lineTo(pts[pts.length-1].x,h-pad.b);ctx.lineTo(pts[0].x,h-pad.b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
    ctx.beginPath();pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.strokeStyle="#47e0ac";ctx.lineWidth=2;ctx.stroke();
    ctx.fillStyle="#70829d";ctx.font="9px system-ui";ctx.fillText("Illustrative only",pad.l,h-3);
  }
  function clearUploadFeedback(){const el=$("#uploadFeedback");if(el){el.innerHTML="";el.classList.add("hidden");}}
  function renderUploadFeedback(message,details=[]){
    const el=$("#uploadFeedback");if(!el)return;
    const safeDetails=Array.isArray(details)?details:[];
    el.innerHTML=`<div class="validation-item"><span class="validation-icon">×</span><span>${escapeHtml(message)}</span><small>Import rejected</small></div>`+
      (safeDetails.length?`<details><summary>Show all ${safeDetails.length.toLocaleString("en-IN")} issue(s)</summary><pre>${safeDetails.map(escapeHtml).join("\n")}</pre></details>`:"");
    el.classList.remove("hidden");
  }
  function renderDataHealth(bars){
    const v=validateBars(bars);$("#dataRows").textContent=bars.length.toLocaleString("en-IN");$("#dataSymbols").textContent=v.symbols.length;
    $("#dataStart").textContent=v.dates[0]||"—";$("#dataEnd").textContent=v.dates[v.dates.length-1]||"—";
    $("#dataHealthBadge").textContent=v.passed?"STRUCTURE OK":"CHECK DATA";
    $("#dataHealthBadge").style.color=v.passed?"var(--green)":"var(--amber)";
    $("#validationList").innerHTML=[...v.errors.map(msg=>({msg,kind:"Error"})),...v.warnings.map(msg=>({msg,kind:"Warning"}))].map(item=>`<div class="validation-item"><span class="validation-icon">${item.kind==="Error"?"×":"!"}</span><span>${escapeHtml(item.msg)}</span><small>${item.kind}</small></div>`).join("");
    return v;
  }
  function setBars(bars,source,demo=false){
    clearUploadFeedback();
    state.autoMode=false;state.autoMeta=null;
    state.bars=bars;state.source=source;state.demo=demo;
    const dates=bars.map(r=>r.date).sort();state.lastDate=dates[dates.length-1]||"—";
    state.candidates=computeCandidates(bars);
    $("#dataStatusText").textContent=demo?"Demo dataset loaded":"CSV loaded · "+bars.length.toLocaleString("en-IN")+" rows";
    $("#sourceFooter").textContent=source;
    $("#universeMetric").textContent=new Set(bars.map(r=>r.symbol).filter(s=>!s.startsWith("NIFTY")&&s!=="DEMO_INDEX")).size;
    $("#scannerNavCount").textContent=String(state.candidates.length);
    renderDataHealth(bars);renderOverview();renderScanner();
    toast(demo?"Synthetic demo loaded. Do not use as market evidence.":"CSV loaded and browser screening completed.");
  }
  function renderAutomatedPipelineNotice(payload, loadError=""){
    const statusEl=$("#autoPipelineState"),detailsEl=$("#autoPipelineDetails");
    if(!statusEl||!detailsEl)return;
    if(loadError){statusEl.textContent="Feed unavailable";detailsEl.textContent="Could not load data/latest_screen.json. The interface remains usable in demo or manual CSV mode. Check GitHub Pages publication and the Actions run.";return;}
    const status=payload?.status||"awaiting_first_run";
    const date=payload?.latest_market_date||"not available yet";
    const candidates=Number(payload?.qualified_count)||0;
    if(status==="ready"){
      statusEl.textContent=`Ready · ${candidates} candidate(s)`;
      detailsEl.textContent=`NSE EOD data through ${date}. Last pipeline build: ${payload.generated_at_utc||"time unavailable"}. ${Array.isArray(payload.recent_eod_bars)?`${payload.recent_eod_bars.length.toLocaleString("en-IN")} recent EOD bars are available for paper proposal review.`:"EOD proposal-tracking bars will appear after the updated pipeline has run."} Sources: NSE cash-equity bhavcopy and NIFTY 50 history where available. Weekday refresh is scheduled for 20:30 India time; Actions can be delayed. No live orders.`;
      $("#demoWarning")?.classList.add("hidden");
    }else if(status==="warming_up"){
      statusEl.textContent="Building history";
      detailsEl.textContent=`Stored equity history through ${date}; ${payload.trading_sessions||0} sessions available. The SMA200-based screen stays in warming-up state until the data gate is met. Review warnings below.`;
      $("#demoWarningTitle").textContent="Research only — automated history is incomplete";
      $("#demoWarningText").textContent="Qualified setups are suppressed until the required historical-data gate is met. No broker connection or orders are enabled.";
      $("#demoWarning")?.classList.remove("hidden");
    }else if(status==="stale"){
      statusEl.textContent="Data stale · check feed";
      detailsEl.textContent=`Latest stored equity session is ${date}. Results are suppressed until data freshness is restored. Open Data & research for source warnings.`;
      $("#demoWarningTitle").textContent="Data freshness check failed";
      $("#demoWarningText").textContent="Do not interpret the last stored scan as a current shortlist. This site still has no broker connection or order execution.";
      $("#demoWarning")?.classList.remove("hidden");
    }else if(status==="source_unavailable"){
      statusEl.textContent="Source unavailable";
      detailsEl.textContent="The pipeline has not acquired a usable history yet. Inspect the GitHub Actions log, verify source access, and manually rerun the workflow. Synthetic values are not market evidence.";
      $("#demoWarning")?.classList.remove("hidden");
    }else{
      statusEl.textContent="Waiting for first run";
      detailsEl.textContent="After you publish this repository, GitHub Actions downloads official NSE end-of-day bhavcopy files on weekdays, builds a rolling history, calculates indicators and publishes the latest qualifying setups. First run builds the historical lookback; allow it to complete.";
    }
  }
  function renderAutomatedHealth(payload){
    $("#dataRows").textContent=Number(payload.bars_count||0).toLocaleString("en-IN");
    $("#dataSymbols").textContent=Number(payload.symbols_count||0).toLocaleString("en-IN");
    $("#dataStart").textContent=payload.history_start||"—";
    $("#dataEnd").textContent=payload.latest_market_date||"—";
    const ready=payload.status==="ready";
    $("#dataHealthBadge").textContent=ready?"AUTO · READY":payload.status==="stale"?"AUTO · STALE":"AUTO · WARMING";
    $("#dataHealthBadge").style.color=ready?"var(--green)":"var(--amber)";
    const warnings=Array.isArray(payload.warnings)?payload.warnings:[];
    const counts=[
      `${Number(payload.trading_sessions||0).toLocaleString("en-IN")} stored equity sessions.`,
      `${Number(payload.eligible_symbols||0).toLocaleString("en-IN")} current symbols evaluated; ${Number(payload.symbols_with_200_sessions||0).toLocaleString("en-IN")} have at least 200 sessions.`,
      `${Number(payload.symbols_over_turnover_floor||0).toLocaleString("en-IN")} pass the ₹1 crore 20-session average-turnover floor.`,
      `NIFTY 50 benchmark latest session: ${payload.benchmark_latest_date||"missing"}.`,
      `${Number(payload.etf_symbols_excluded_from_history||0).toLocaleString("en-IN")} ETF symbol(s) excluded from history; list source: ${payload.etf_exclusion_source||"not reported"}.`,
      `${Number(payload.qualified_count||0).toLocaleString("en-IN")} qualified setup(s) in the published output.`,
      `${Number(payload.recent_eod_bars?.length||0).toLocaleString("en-IN")} recent EOD bars available for paper proposal reconciliation.`
    ];
    const all=[...counts,...warnings];
    $("#validationList").innerHTML=all.map((msg,i)=>`<div class="validation-item"><span class="validation-icon">${i<counts.length?"i":"!"}</span><span>${escapeHtml(msg)}</span><small>${i<counts.length?"Info":"Review"}</small></div>`).join("");
  }
  async function loadAutomatedScreen(){
    try{
      const response=await fetch(`data/latest_screen.json?refresh=${Date.now()}`,{cache:"no-store"});
      if(!response.ok)throw new Error(`HTTP ${response.status}`);
      const payload=await response.json();
      renderAutomatedPipelineNotice(payload);
      state.eodMonitoringMessage=Array.isArray(payload.recent_eod_bars)&&payload.recent_eod_bars.length?`Recent EOD history available through ${payload.latest_market_date||"latest session"}.`:`The updated EOD monitoring feed will be available after the next successful pipeline run.`;
      if(!["ready","warming_up","stale"].includes(payload.status))return;
      state.autoMode=true;state.autoMeta=payload;state.demo=false;state.source="Automated NSE EOD pipeline";state.bars=[];
      state.lastDate=payload.latest_market_date||"—";
      const sourceCandidates=payload.status==="ready"&&Array.isArray(payload.candidates)?payload.candidates:[];
      state.candidates=sourceCandidates.map(r=>({...r,relVol:Number(r.rel_vol),atr:Number(r.atr14_sma),avgTurn:Number(r.avg_turnover_20d),momentum60:Number(r.return_60d)/100}));
      $("#dataStatusText").textContent=payload.status==="ready"?`NSE EOD · ${payload.latest_market_date||"date unavailable"}`:`Pipeline · ${payload.status}`;
      $("#sourceFooter").textContent="Automated NSE EOD pipeline";
      $("#universeMetric").textContent=Number(payload.eligible_symbols||0).toLocaleString("en-IN");
      $("#scannerNavCount").textContent=String(state.candidates.length);
      $("#setupsMetric").textContent=String(state.candidates.length).padStart(2,"0");
      renderAutomatedHealth(payload);renderOverview();renderScanner();
      if(payload.status==="ready")reconcileLedgerWithEod(payload);
      else {state.eodMonitoringMessage=`EOD tracker waiting: pipeline status is ${payload.status}.`;renderLedger();renderOverviewTracker();}
    }catch(error){
      renderAutomatedPipelineNotice(null,String(error?.message||error));
    }
  }
  function syntheticData(){
    const bars=[];const start=new Date("2023-01-02T00:00:00Z");
    const defs=[["DEMO_A",100,.0005,.012],["DEMO_B",250,.00025,.016],["DEMO_C",80,.00015,.01],["NIFTY50",18000,.00022,.008]];
    let seed=20261015;const rand=()=>{seed=(seed*1664525+1013904223)>>>0;return seed/4294967296;};
    let dates=[],d=new Date(start);while(dates.length<320){const day=d.getUTCDay();if(day!==0&&day!==6)dates.push(d.toISOString().slice(0,10));d.setUTCDate(d.getUTCDate()+1);}
    for(const [symbol,startPrice,drift,vol] of defs){let close=startPrice;for(let i=0;i<dates.length;i++){close*=Math.max(.85,1+drift+(rand()*2-1)*vol);const open=close*(1+(rand()*2-1)*vol/3),high=Math.max(open,close)*(1+rand()*vol/2),low=Math.min(open,close)*(1-rand()*vol/2);bars.push({date:dates[i],symbol,open,high,low,close,volume:Math.floor(100000+rand()*500000)});}}
    return bars;
  }
  function renderLedger(){
    const total=state.ledger.length,active=state.ledger.filter(x=>x.status==="Planned"||x.status==="Open").length;
    const exits=state.ledger.filter(x=>x.exitActionRequired||x.reviewRequired).length;
    const passed=state.ledger.filter(x=>x.outcome==="PASS").length,failed=state.ledger.filter(x=>x.outcome==="FAIL").length;
    const set=(id,value)=>{const el=$(id);if(el)el.textContent=String(value);};
    set("#ledgerOpen",active);set("#ledgerExitActions",exits);set("#ledgerPassed",passed);set("#ledgerFailed",failed);
    renderTrackerCards("#ledgerCards",state.ledger);
    const empty=$("#ledgerEmpty");if(empty)empty.classList.toggle("hidden",total>0);
    const updated=$("#trackerUpdatedText");if(updated)updated.textContent=state.eodMonitoringMessage||`Showing ${total} local paper proposal(s).`;
    const positions=$("#positionsMetric");if(positions)positions.textContent=String(active).padStart(2,"0");
  }
  function addLedger(item){
    const entryTrigger=Number(item.entryTrigger??item.entry)||0,stop=Number(item.stop)||0;
    if(!item.symbol||entryTrigger<=stop||stop<=0){toast("Cannot save plan: check symbol, entry trigger and stop-loss values.");return;}
    const capital=Number(item.capitalAtProposal)||Number(state.settings.capital)||0;
    const riskPct=Number(item.riskPctAtProposal)||Number(state.settings.riskPct)||0;
    const riskBudget=Number(item.riskBudget)||capital*riskPct/100;
    const qty=Number.isFinite(Number(item.qty))&&Number(item.qty)>0?Math.floor(Number(item.qty)):Math.max(0,Math.min(Math.floor(riskBudget/(entryTrigger-stop)),capital>0?Math.floor(capital*.15/entryTrigger):0));
    if(qty<1){toast("Proposal blocked: no shares fit within the available risk budget and 15% position cap.");return;}
    const marketDate=/^\d{4}-\d{2}-\d{2}$/.test(state.lastDate)?state.lastDate:todayISO();
    const target1=Number(item.target1)||(entryTrigger+(entryTrigger-stop));
    const target2=Number(item.target2??item.target)||(entryTrigger+2*(entryTrigger-stop));
    const record={
      id:String(Date.now())+Math.random().toString(16).slice(2,7),createdAt:todayISO(),createdMarketDate:marketDate,
      symbol:item.symbol,strategy:item.strategy||"breakout",score:Number(item.score)||0,qualityTier:item.qualityTier||qualityTier(item.score),
      signalClose:Number(item.signalClose)||entryTrigger,entry:entryTrigger,entryTrigger,fillPrice:null,entryDate:null,
      stop,target1,target2,target:target2,qty,status:"Planned",outcome:"",realizedPnl:0,
      capitalAtProposal:capital,riskPctAtProposal:riskPct,costPctAtProposal:Number(item.costPctAtProposal??state.settings.costPct)||0,
      riskBudget,plannedRisk:qty*(entryTrigger-stop),positionValue:qty*entryTrigger,
      estimatedCost:qty*entryTrigger*(Number(item.costPctAtProposal??state.settings.costPct)||0)/100,
      validForSessions:Number(item.validForSessions)||3,maxHoldSessions:Number(item.maxHoldSessions)||Number(state.settings.maxHold)||10,
      sessionsSinceSignal:0,holdingSessions:0,lastEvaluatedDate:marketDate,lastPrice:Number(item.signalClose)||entryTrigger,lastPriceDate:marketDate,
      monitorEnabled:item.monitorEnabled!==false,monitorStatus:item.monitorEnabled===false?"MANUAL PLAN · no symbol EOD linkage":"WAITING ENTRY · entry trigger valid for 3 sessions",
      exitActionRequired:false,reviewRequired:false,notes:item.notes||"Paper only; no order placed"
    };
    state.ledger.push(record);saveLedger();toast("Proposal saved to this browser’s paper tracker. No order was placed.");navigate("ledger");
  }
  function calcRisk(){
    const capital=Number($("#capitalInput").value),riskPct=Number($("#riskInput").value),entry=Number($("#entryInput").value),stop=Number($("#stopInput").value),target=Number($("#targetInput").value),costPct=Number($("#costInput").value);
    if(!(capital>0&&riskPct>0&&entry>stop&&stop>0&&costPct>=0)){toast("Enter valid capital, risk, entry and stop values.");return null;}
    const perTradeBudget=capital*riskPct/100,portfolioRemaining=Math.max(0,capital*.015-activeRiskInLedger()),budget=Math.min(perTradeBudget,portfolioRemaining);
    const perShare=entry-stop,rawQty=Math.max(0,Math.floor(budget/perShare)),notionalCapQty=Math.floor((capital*.15)/entry),qty=Math.min(rawQty,notionalCapQty),value=qty*entry,cost=value*costPct/100,netLoss=qty*perShare+cost,rr=target>entry?(target-entry)/perShare:NaN;
    $("#riskBudget").textContent=fmtINR(budget);$("#riskPercentLabel").textContent=`${fmtNum(riskPct,2)}% of equity · aggregate cap applied`;
    $("#riskPerShare").textContent=money(perShare);$("#riskQty").textContent=qty.toLocaleString("en-IN")+" shares";$("#positionValue").textContent=fmtINR(value);
    $("#estimatedCosts").textContent=fmtINR(cost,2);$("#rrRatio").textContent=Number.isFinite(rr)?fmtNum(rr,2)+"×":"Not set";
    $("#netStopLoss").textContent="−"+fmtINR(netLoss,2);
    $("#riskAlert").textContent=qty===0?"Risk capacity is unavailable for one share under the remaining aggregate risk and notional limits. Do not increase risk automatically.":rawQty>notionalCapQty?`Quantity reduced from ${rawQty.toLocaleString("en-IN")} to ${qty.toLocaleString("en-IN")} shares by the 15% per-position notional cap.`:budget<perTradeBudget?`Risk budget reduced to ${fmtINR(budget)} because planned/open risk has used part of the 1.5% aggregate cap.`:"Estimate only. Also check correlated positions, liquidity, gaps and broker-specific charges.";
    state.currentPlan={symbol:"MANUAL",strategy:"breakout",entry,stop,target,target1:entry+perShare,target2:target,qty,riskBudget:budget,capitalAtProposal:capital,riskPctAtProposal:riskPct,costPctAtProposal:costPct,monitorEnabled:false};
    return state.currentPlan;
  }
  function updateGateUI(){
    const boxes=$$("[data-gate]"),done=boxes.filter(x=>x.checked).length;
    $("#gateCount").textContent=`${done} / ${boxes.length}`;$("#gateProgress").style.width=(done/boxes.length*100)+"%";
    boxes.forEach(el=>{const small=el.closest("label").querySelector("small");small.textContent=el.checked?"Checked":"Pending";});
  }
  function navigate(view){
    $$(".view").forEach(el=>el.classList.toggle("active",el.id==="view-"+view));
    $$(".nav-item").forEach(el=>el.classList.toggle("active",el.dataset.view===view));
    const labels={overview:"Overview",scanner:"Stock scanner",risk:"Risk calculator",ledger:"Paper ledger",data:"Data & research"};
    $("#pageCrumb").textContent=labels[view]||"Overview";$("#sidebar").classList.remove("open");
    if(view==="scanner")renderScanner();if(view==="ledger")renderLedger();if(view==="overview")setTimeout(drawEquityChart,50);
    window.scrollTo({top:0,behavior:"smooth"});
  }
  function download(name,content,type="text/csv;charset=utf-8"){
    const blob=new Blob([content],{type}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();URL.revokeObjectURL(url);
  }
  function csvFor(rows){if(!rows.length)return "";const cols=Object.keys(rows[0]);const cell=v=>{const s=String(v??"");return /[",\n]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s;};return [cols.join(","),...rows.map(r=>cols.map(c=>cell(r[c])).join(","))].join("\n");}
  function init(){
    loadLocal();renderLedger();setBars(syntheticData(),"Synthetic demo data",true);calcRisk();loadAutomatedScreen();
    $$(".nav-item").forEach(b=>b.addEventListener("click",()=>navigate(b.dataset.view)));
    $$("[data-go]").forEach(b=>b.addEventListener("click",()=>navigate(b.dataset.go)));
    $("#mobileMenu").addEventListener("click",()=>$("#sidebar").classList.toggle("open"));
    $("#helpBtn").addEventListener("click",()=>$("#helpDialog").showModal());
    $("#closeHelp").addEventListener("click",()=>$("#helpDialog").close());$("#closeHelpBottom").addEventListener("click",()=>$("#helpDialog").close());
    $("#dismissNotice").addEventListener("click",()=>$(".notice-warning").classList.add("hidden"));
    $("#overviewTabs").addEventListener("click",e=>{const b=e.target.closest("[data-filter]");if(!b)return;$$(".tab",$("#overviewTabs")).forEach(x=>x.classList.toggle("active",x===b));state.strategy=b.dataset.filter;renderOverview();});
    $("#applyFilters").addEventListener("click",renderScanner);
    ["strategyFilter","minScore","sortSignals"].forEach(id=>$("#"+id).addEventListener("change",renderScanner));
    $("#exportSignals").addEventListener("click",()=>{const latest=new Map();state.candidates.forEach(r=>{if(!latest.has(r.symbol)||latest.get(r.symbol).date<r.date)latest.set(r.symbol,r);});const rows=[...latest.values()].map(r=>{const p=buildTradePlan(r);return {symbol:r.symbol,date:r.date,strategy:r.strategy,signal_close:r.close,quality_tier:p.tier,setup_score:r.score,entry_trigger:p.entryTrigger,stop_loss:p.stop,take_profit_1:p.target1,take_profit_2:p.target2,holding_sessions:`2-${p.maxHoldSessions}`,suggested_quantity:p.qty,position_value:p.positionValue,risk_budget:p.riskBudget,estimated_cost:p.estimatedCost,source:state.source};});download("edge-nse-proposals.csv",csvFor(rows));});
    const ingestCSV=async fileListLike=>{
      const files=Array.from(fileListLike||[]).filter(Boolean);
      if(!files.length)return;
      const merged=[],parseErrors=[];
      // Read and validate each file independently, then merge all bars into one chronological dataset.
      // Any failed file rejects the entire batch so the active dataset remains internally consistent.
      for(const file of files){
        try{const parsed=parseCSV(await file.text(),file.name);for(const row of parsed)merged.push(row);}
        catch(err){
          const details=Array.isArray(err.details)&&err.details.length?err.details.map(d=>String(d)): [`${file.name}: ${err.message||"CSV import failed."}`];
          parseErrors.push(...details);
        }
      }
      if(parseErrors.length){
        renderUploadFeedback(`Upload rejected: ${parseErrors.length} issue(s) found across ${files.length} file(s). The active dataset and Data health panel are unchanged.`,parseErrors);
        toast(`CSV batch rejected: ${parseErrors.length} issue(s). Review the import feedback.`);return;
      }
      merged.sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.date.localeCompare(b.date));
      const v=validateBars(merged);
      if(!v.passed){renderUploadFeedback("Upload rejected. The active dataset and its Data health panel are unchanged.",v.errors);toast(`CSV rejected: ${v.errors.length} structural issue(s). Expand the import feedback to review all affected lines.`);return;}
      setBars(merged,files.length===1?files[0].name:`${files.length} CSV files combined`,false);
    };
    $("#csvUpload").addEventListener("change",async e=>{await ingestCSV(e.target.files);e.target.value="";});
    const dz=$("#dropzone");["dragenter","dragover"].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add("dragover");}));["dragleave","drop"].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove("dragover");}));dz.addEventListener("drop",async e=>{await ingestCSV(e.dataTransfer.files);});
    $("#loadDemo").addEventListener("click",()=>setBars(syntheticData(),"Synthetic demo data",true));
    $("#downloadTemplate").addEventListener("click",()=>download("edge-nse-ohlcv-template.csv","date,symbol,open,high,low,close,volume\n2025-01-02,ABC,100,103,99,102,150000\n","text/csv;charset=utf-8"));
    $$("[data-setting]").forEach(el=>el.addEventListener("change",()=>updateSettingsFromElement(el)));
    $("#calculateRisk").addEventListener("click",calcRisk);
    ["capitalInput","riskInput","costInput"].forEach(id=>$("#"+id).addEventListener("change",e=>{
      const el=e.currentTarget,key=id==="capitalInput"?"capital":id==="riskInput"?"riskPct":"costPct";
      let value=Number(el.value);if(!Number.isFinite(value))return;
      if(key==="capital")value=Math.max(0,value);if(key==="riskPct")value=Math.min(2,Math.max(.01,value));if(key==="costPct")value=Math.min(5,Math.max(0,value));
      state.settings[key]=value;safeStore(STORAGE_SETTINGS,state.settings);syncSettingsUI();calcRisk();renderScanner();
    }));
    ["entryInput","stopInput","targetInput"].forEach(id=>$("#"+id).addEventListener("change",calcRisk));
    $("#saveRiskPlan").addEventListener("click",()=>{const p=calcRisk();if(!p)return;addLedger({...p,symbol:"MANUAL",strategy:"manual",monitorEnabled:false,notes:"Manual risk-calculator plan; not linked to an NSE EOD symbol."});});
    document.addEventListener("click",e=>{
      const propose=e.target.closest("[data-propose-symbol]");
      if(propose){const candidate=state.candidates.find(x=>x.symbol===propose.dataset.proposeSymbol);if(!candidate){toast("Candidate is no longer in the current screen. Refresh the data first.");return;}addLedger(buildTradePlan(candidate));return;}
      const del=e.target.closest("[data-delete-id]");if(del){state.ledger=state.ledger.filter(x=>x.id!==del.dataset.deleteId);saveLedger();toast("Proposal removed from this browser.");return;}
      const ack=e.target.closest("[data-ack-action]");if(ack){const item=state.ledger.find(x=>x.id===ack.dataset.ackAction);if(item){item.exitActionRequired=false;item.actionAcknowledgedAt=state.lastDate;item.monitorStatus=item.status==="Open"?"Open · exit review acknowledged; EOD tracking continues":`${item.outcome||"Closed"} · exit action acknowledged`;saveLedger();toast("Exit action acknowledged locally; the paper outcome is unchanged.");}return;}
      const cancel=e.target.closest("[data-cancel-proposal]");if(cancel){const item=state.ledger.find(x=>x.id===cancel.dataset.cancelProposal);if(item&&item.status==="Planned"){item.status="Cancelled";item.outcome="EXPIRED";item.exitReason="Cancelled manually";setTradeMonitorState(item,"CANCELLED BY USER",false);saveLedger();toast("Proposal cancelled locally.");}return;}
      const close=e.target.closest("[data-manual-close]");if(close){const item=state.ledger.find(x=>x.id===close.dataset.manualClose);if(item&&item.status==="Open"&&Number.isFinite(Number(item.lastPrice))){const px=Number(item.lastPrice),qty=Number(item.qty)||0,entry=Number(item.fillPrice||item.entry)||0;item.status="Closed";item.outcome="MANUAL_EXIT";item.exitPrice=px;item.exitDate=item.lastPriceDate||state.lastDate;item.exitReason="Manually marked exited at latest available EOD close";item.realizedPnl=(px-entry)*qty-(Number(item.estimatedCost)||0);item.exitActionRequired=false;item.manualClosed=true;item.monitorStatus="MANUAL EXIT · user-recorded at latest EOD close";saveLedger();toast("Paper trade marked manually exited.");}return;}
    });
    $("#refreshEod").addEventListener("click",async()=>{const btn=$("#refreshEod");btn.disabled=true;btn.textContent="↻ Refreshing…";await loadAutomatedScreen();btn.disabled=false;btn.textContent="↻ Refresh EOD";toast("EOD feed checked. Review the tracker status and pipeline date.");});
    $("#exportLedger").addEventListener("click",()=>download("edge-nse-proposal-tracker.csv",csvFor(state.ledger)));
    $("#clearLedger").addEventListener("click",()=>{if(confirm("Delete all local paper ledger entries? Export a backup first if needed.")){state.ledger=[];saveLedger();toast("Local ledger cleared.");}});
    $$("[data-gate]").forEach(el=>el.addEventListener("change",()=>{state.gates[el.dataset.gate]=el.checked;safeStore(STORAGE_GATES,state.gates);updateGateUI();toast("Research checklist updated locally. This does not approve live trading.");}));
    window.addEventListener("resize",drawEquityChart);
  }
  document.addEventListener("DOMContentLoaded",init);
})();