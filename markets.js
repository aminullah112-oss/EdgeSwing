/* EDGE NSE market explorer: markets pulse, stock pages, scans, sectors, watchlist.
 *
 * Reads the static files built by .github/scripts/build_market_data.py:
 *   data/market/snapshot.json   one row per stock (columns + rows arrays)
 *   data/market/breadth.json    per-session breadth history
 *   data/market/stocks/<key>.json  OHLCV bars for one symbol (fetched on demand)
 * No third-party scripts and no external market-data calls from the browser.
 * Navigation reuses app.js: views are opened by clicking the matching .nav-item.
 */
(() => {
  "use strict";
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = x => typeof x === "number" && Number.isFinite(x);
  const nf = (x, d = 2) => isNum(x) ? x.toLocaleString("en-IN", { maximumFractionDigits: d, minimumFractionDigits: d }) : "—";
  const pct = (x, d = 2) => isNum(x) ? `<span class="${x > 0 ? "up" : x < 0 ? "down" : ""}">${x > 0 ? "+" : ""}${nf(x, d)}%</span>` : "—";
  const cr = x => isNum(x) ? `₹${nf(x, x >= 100 ? 0 : x >= 10 ? 1 : 2)} cr` : "—";
  const median = arr => { const a = arr.filter(isNum).sort((x, y) => x - y); if (!a.length) return null; const m = a.length >> 1; return a.length % 2 ? a[m] : (a[m - 1] + a[m]) / 2; };
  const BASE = "data/market/";
  const LIQUID_CR = 1; // matches the screener's ₹1 crore 20-session average turnover floor
  const WATCH_KEY = "edgeNseWatchlistV1";
  const CUSTOM_KEY = "edgeNseCustomScansV1";
  const store = {
    get(k, fallback) { try { const v = JSON.parse(localStorage.getItem(k)); return v ?? fallback; } catch { return fallback; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* private mode: keep in memory only */ } },
  };

  const CAP_LABELS = { large: "Large cap", mid: "Mid cap", small: "Small cap", micro: "Micro cap", "": "Unclassified" };
  const EVENT_LABELS = {
    h52: "New 52-week high", l52: "New 52-week low", b20: "Closed above 20-session high", d20: "Closed below 20-session low",
    gx: "Golden cross (SMA50 > SMA200, last 5 sessions)", dx: "Death cross (SMA50 < SMA200, last 5 sessions)",
    macd_up: "MACD crossed above signal", macd_dn: "MACD crossed below signal", e20_up: "Closed back above EMA20",
    e20_dn: "Closed below EMA20", vw20_up: "Closed back above 20-session VWAP", vw20_dn: "Closed below 20-session VWAP", vol3: "Volume ≥ 3× 20-session average", nr7: "NR7: narrowest range in 7 sessions",
    inside: "Inside day", bull_engulf: "Bullish engulfing candle", bear_engulf: "Bearish engulfing candle",
    gap_up: "Gap up ≥ 2% above prior high", gap_dn: "Gap down ≥ 2% below prior low",
  };

  const S = { snap: null, rows: [], bySym: new Map(), breadth: [], bench: null, loading: null, error: null,
    watch: new Set(store.get(WATCH_KEY, [])), custom: store.get(CUSTOM_KEY, []),
    stock: null, bars: new Map(), range: 250, overlays: { s20: true, s50: true, s200: true, vw20: true },
    moversTab: "gainers", scanId: "hc_breakout", sector: null };

  // ------------------------------------------------------------------ data
  function load() {
    if (S.loading) return S.loading;
    S.loading = Promise.all([
      fetch(BASE + "snapshot.json", { cache: "no-cache" }).then(r => { if (!r.ok) throw new Error(`snapshot.json HTTP ${r.status}`); return r.json(); }),
      fetch(BASE + "breadth.json", { cache: "no-cache" }).then(r => r.ok ? r.json() : { rows: [] }).catch(() => ({ rows: [] })),
    ]).then(([snap, breadth]) => {
      S.snap = snap;
      S.rows = snap.rows.map(r => Object.fromEntries(snap.columns.map((c, i) => [c, r[i]])));
      S.rows.forEach(r => S.bySym.set(r.s, r));
      S.breadth = breadth.rows || [];
      S.bench = snap.benchmark;
      $$(".mx-asof").forEach(el => el.textContent = snap.meta.latest_market_date);
      return S;
    }).catch(err => { S.error = err; throw err; });
    return S.loading;
  }

  function loadBars(row) {
    if (S.bars.has(row.key)) return Promise.resolve(S.bars.get(row.key));
    return fetch(`${BASE}stocks/${encodeURIComponent(row.key)}.json`).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(j => { S.bars.set(row.key, j.bars); return j.bars; });
  }

  // Universe filter shared by movers, scans and sectors.
  function universe(rows, { cap = "all", liquid = true } = {}) {
    return rows.filter(r => !r.stale && (!liquid || (isNum(r.at) && r.at >= LIQUID_CR)) &&
      (cap === "all" || (cap === "n50" ? r.n50 : cap === "none" ? !r.cap : r.cap === cap)));
  }
  function universeControls(prefix) {
    return `<div class="filter-field"><label for="${prefix}Cap">UNIVERSE</label><select id="${prefix}Cap">
      <option value="all">All NSE EQ stocks</option><option value="n50">NIFTY 50</option><option value="large">Large cap (Nifty 100)</option>
      <option value="mid">Mid cap (Midcap 150)</option><option value="small">Small cap (Smallcap 250)</option>
      <option value="micro">Micro cap (Microcap 250)</option><option value="none">Unclassified listings</option></select></div>
      <label class="mx-check"><input type="checkbox" id="${prefix}Liquid" checked> Liquid only (avg turnover ≥ ₹${LIQUID_CR} cr)</label>`;
  }
  const readUniverse = prefix => ({ cap: $(`#${prefix}Cap`)?.value || "all", liquid: $(`#${prefix}Liquid`)?.checked ?? true });

  // ------------------------------------------------------------------ tables
  const COLS = {
    s: { label: "SYMBOL", html: r => `<button class="mx-sym" data-sym="${esc(r.s)}">${esc(r.s)}</button>${r.n ? `<small>${esc(r.n)}</small>` : ""}`, sort: r => r.s, text: r => r.s },
    ind: { label: "INDUSTRY", html: r => esc(r.ind), sort: r => r.ind, text: r => r.ind },
    c: { label: "CLOSE", html: r => nf(r.c), sort: r => r.c, num: true },
    ch: { label: "1D", html: r => pct(r.ch), sort: r => r.ch, num: true },
    r1w: { label: "1W", html: r => pct(r.r1w), sort: r => r.r1w, num: true },
    r1m: { label: "1M", html: r => pct(r.r1m), sort: r => r.r1m, num: true },
    r3m: { label: "3M", html: r => pct(r.r3m), sort: r => r.r3m, num: true },
    r1y: { label: "1Y", html: r => pct(r.r1y), sort: r => r.r1y, num: true },
    rsi: { label: "RSI 14", html: r => nf(r.rsi, 1), sort: r => r.rsi, num: true },
    rv: { label: "REL VOL", html: r => isNum(r.rv) ? nf(r.rv) + "×" : "—", sort: r => r.rv, num: true },
    dh: { label: "VS 52W HIGH", html: r => pct(r.dh, 1), sort: r => r.dh, num: true },
    at: { label: "AVG TURNOVER", html: r => cr(r.at), sort: r => r.at, num: true },
    gap: { label: "GAP", html: r => pct(r.gap), sort: r => r.gap, num: true },
    se: { label: "STOCKEDGE-STYLE", html: r => isNum(r.seb) ? `<span class="se-pill ${r.seb - r.ser >= 10 ? "se-strong" : r.seb > r.ser ? "se-pos" : r.seb < r.ser ? "se-neg" : ""}">${r.seb}▲ ${r.ser}▼</span>` : "—", sort: r => isNum(r.seb) ? r.seb - r.ser : null, num: true },
    cv: { label: "CONVICTION", html: r => isNum(r.cv) ? `<span class="conv-pill ${r.cv >= 6 ? "conv-high" : r.cv >= 5 ? "conv-mid" : "conv-low"}">${r.cv}/7</span>` : "—", sort: r => r.cv, num: true },
    vwd: { label: "VS VWAP", html: r => isNum(r.vw) ? pct((r.c / r.vw - 1) * 100, 1) : "—", sort: r => isNum(r.vw) ? r.c / r.vw : null, num: true },
    vw20d: { label: "VS VWAP20", html: r => isNum(r.vw20) ? pct((r.c / r.vw20 - 1) * 100, 1) : "—", sort: r => isNum(r.vw20) ? r.c / r.vw20 : null, num: true },
    mcap: { label: "MKT CAP", html: r => cr(r.mcap), sort: r => r.mcap, num: true },
    pe: { label: "P/E", html: r => isNum(r.pe) ? nf(r.pe, 1) : (isNum(r.eps) && r.eps <= 0 ? `<span class="down">Loss</span>` : "—"), sort: r => r.pe, num: true },
    pb: { label: "P/B", html: r => nf(r.pb, 2), sort: r => r.pb, num: true },
    roe: { label: "ROE", html: r => isNum(r.roe) ? nf(r.roe, 1) + "%" : "—", sort: r => r.roe, num: true },
    roce: { label: "ROCE", html: r => isNum(r.roce) ? nf(r.roce, 1) + "%" : "—", sort: r => r.roce, num: true },
    dy: { label: "DIV YIELD", html: r => isNum(r.dy) ? nf(r.dy, 2) + "%" : "—", sort: r => r.dy, num: true },
    de: { label: "D/E", html: r => nf(r.de, 2), sort: r => r.de, num: true },
  };
  const hasFund = () => (S.snap?.meta?.fundamentals?.stocks || 0) > 0;
  const withFund = cols => hasFund() ? [...cols.filter(c => c !== "at"), "mcap", "pe"] : cols;
  const tableState = new WeakMap();
  function renderTable(host, rows, cols, { sort = cols[1], dir = -1, limit = 200, empty = "No stocks match.", after } = {}) {
    const st = tableState.get(host) || { sort, dir };
    tableState.set(host, st);
    const def = COLS[st.sort];
    const sorted = [...rows].sort((a, b) => {
      const x = def.sort(a), y = def.sort(b);
      if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1;
      return (typeof x === "string" ? x.localeCompare(y) : x - y) * st.dir;
    });
    host._rows = sorted; host._cols = cols;
    if (!rows.length) { host.innerHTML = `<div class="empty-state">${esc(empty)}</div>`; after?.(host); return; }
    const shown = sorted.slice(0, limit);
    host.innerHTML = `<div class="table-wrap"><table class="mx-table"><thead><tr>${cols.map(c =>
      `<th class="${COLS[c].num ? "num" : ""}"><button data-sort="${c}">${COLS[c].label}${st.sort === c ? (st.dir > 0 ? " ▲" : " ▼") : ""}</button></th>`).join("")}</tr></thead>
      <tbody>${shown.map(r => `<tr>${cols.map(c => `<td class="${COLS[c].num ? "num" : ""}">${COLS[c].html(r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>
      <div class="mx-table-foot">${rows.length > limit ? `Showing top ${limit} of ${rows.length}` : `${rows.length} stock${rows.length === 1 ? "" : "s"}`}</div>`;
    $$("[data-sort]", host).forEach(b => b.addEventListener("click", () => {
      const k = b.dataset.sort; st.dir = st.sort === k ? -st.dir : (COLS[k].num ? -1 : 1); st.sort = k;
      renderTable(host, rows, cols, { limit, empty, after });
    }));
    after?.(host);
  }
  function exportTable(host, name) {
    if (!host._rows?.length) return;
    const cols = ["s", "n", "ind", "cap", "d", "c", "ch", "r1w", "r1m", "r3m", "r6m", "r1y", "dh", "rsi", "rv", "at", "s50", "s200",
      "mcap", "pe", "pb", "dy", "roe", "roce", "de", "fa"];
    const cell = v => { const s = Array.isArray(v) ? v.join(" ") : String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const csv = [cols.join(","), ...host._rows.map(r => cols.map(c => cell(r[c])).join(","))].join("\n");
    const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: name });
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // ------------------------------------------------------------------ charts
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  function setupCanvas(canvas) {
    const dpr = window.devicePixelRatio || 1, w = canvas.clientWidth, h = canvas.clientHeight;
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }
  // Rolling VWAP = sum(traded value) / sum(volume); null wherever a bar in the window lacks traded value.
  function vwapArr(bars, n) {
    return bars.map((_, i) => {
      if (i < n - 1) return null;
      let val = 0, vol = 0;
      for (let j = i - n + 1; j <= i; j++) { if (!bars[j].val) return null; val += bars[j].val; vol += bars[j].v; }
      return vol > 0 ? val / vol : null;
    });
  }
  function smaArr(vals, n) { const out = Array(vals.length).fill(null); let t = 0; vals.forEach((v, i) => { t += v; if (i >= n) t -= vals[i - n]; if (i >= n - 1) out[i] = t / n; }); return out; }

  function drawCandles(canvas, allBars, readout) {
    const full = allBars.map(b => ({ d: b[0], o: b[1], h: b[2], l: b[3], c: b[4], v: b[5], val: b[6] }));
    const closes = full.map(b => b.c);
    const ma = { s20: smaArr(closes, 20), s50: smaArr(closes, 50), s200: smaArr(closes, 200), vw20: vwapArr(full, 20) };
    const start = Math.max(0, full.length - S.range);
    const bars = full.slice(start);
    const { ctx, w, h } = setupCanvas(canvas);
    const padL = 8, padR = 62, padT = 10, volH = Math.round(h * 0.2), priceH = h - volH - padT - 24;
    const n = bars.length, step = (w - padL - padR) / Math.max(n, 1), bw = Math.max(1, Math.min(9, step * 0.7));
    let lo = Infinity, hi = -Infinity, vmax = 0;
    bars.forEach((b, i) => { lo = Math.min(lo, b.l); hi = Math.max(hi, b.h); vmax = Math.max(vmax, b.v);
      Object.keys(ma).forEach(k => { const v = ma[k][start + i]; if (S.overlays[k] && v != null) { lo = Math.min(lo, v); hi = Math.max(hi, v); } }); });
    const padP = (hi - lo) * 0.06 || 1; lo -= padP; hi += padP;
    const y = p => padT + (hi - p) / (hi - lo) * priceH, x = i => padL + step * i + step / 2;
    const up = css("--green") || "#47e0ac", down = css("--red") || "#ff7e8d", grid = "#25334c", muted = css("--muted2") || "#6f7f9b";
    const draw = hover => {
      ctx.clearRect(0, 0, w, h);
      ctx.font = "10px Inter, system-ui, sans-serif"; ctx.textBaseline = "middle";
      for (let k = 0; k <= 4; k++) { const p = lo + (hi - lo) * k / 4, yy = y(p); ctx.strokeStyle = grid; ctx.globalAlpha = .5; ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke(); ctx.globalAlpha = 1; ctx.fillStyle = muted; ctx.fillText(nf(p, p > 1000 ? 0 : 2), w - padR + 6, yy); }
      const months = new Set();
      bars.forEach((b, i) => { const m = b.d.slice(0, 7); if (!months.has(m) && (i === 0 || bars[i - 1].d.slice(0, 7) !== m)) { months.add(m); if (i > 0 && (n < 140 || +b.d.slice(5, 7) % 2 === 1)) { ctx.fillStyle = muted; ctx.fillText(new Date(b.d + "T00:00").toLocaleDateString("en-IN", { month: "short", year: "2-digit" }), x(i) - 14, h - 9); } } });
      const vy0 = h - 20;
      bars.forEach((b, i) => {
        const color = b.c >= b.o ? up : down;
        ctx.fillStyle = color; ctx.globalAlpha = .35; const vh = vmax ? b.v / vmax * (volH - 6) : 0; ctx.fillRect(x(i) - bw / 2, vy0 - vh, bw, vh); ctx.globalAlpha = 1;
        ctx.strokeStyle = color; ctx.beginPath(); ctx.moveTo(x(i), y(b.h)); ctx.lineTo(x(i), y(b.l)); ctx.stroke();
        const top = y(Math.max(b.o, b.c)), bot = y(Math.min(b.o, b.c)); ctx.fillRect(x(i) - bw / 2, top, bw, Math.max(1, bot - top));
      });
      const maColors = { s20: css("--amber") || "#f5bd68", s50: css("--blue") || "#7aa7ff", s200: css("--violet") || "#b7a2ff", vw20: "#5eead4" };
      const maLabels = { s20: "SMA20", s50: "SMA50", s200: "SMA200", vw20: "VWAP20" };
      Object.keys(ma).forEach(k => { if (!S.overlays[k]) return; ctx.strokeStyle = maColors[k]; ctx.lineWidth = 1.4; ctx.beginPath(); let on = false;
        bars.forEach((_, i) => { const v = ma[k][start + i]; if (v == null) { on = false; return; } on ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v)); on = true; }); ctx.stroke(); ctx.lineWidth = 1; });
      const i = hover ?? n - 1, b = bars[i];
      if (hover != null && b) { ctx.strokeStyle = muted; ctx.setLineDash([3, 3]); ctx.beginPath(); ctx.moveTo(x(i), padT); ctx.lineTo(x(i), vy0); ctx.stroke(); ctx.setLineDash([]); }
      if (b && readout) {
        const prev = full[start + i - 1]?.c, chg = prev ? (b.c / prev - 1) * 100 : null;
        readout.innerHTML = `<b>${b.d}</b> O ${nf(b.o)} H ${nf(b.h)} L ${nf(b.l)} C ${nf(b.c)} ${pct(chg)} · Vol ${b.v.toLocaleString("en-IN")}` +
          Object.keys(ma).filter(k => S.overlays[k] && ma[k][start + i] != null).map(k => ` · <i style="color:${maColors[k]}">${maLabels[k]} ${nf(ma[k][start + i])}</i>`).join("");
      }
    };
    draw(null);
    canvas.onmousemove = e => { const r = canvas.getBoundingClientRect(); const i = Math.floor((e.clientX - r.left - padL) / step); draw(Math.max(0, Math.min(n - 1, i))); };
    canvas.onmouseleave = () => draw(null);
    canvas.ontouchmove = e => { const t = e.touches[0]; if (!t) return; const r = canvas.getBoundingClientRect(); draw(Math.max(0, Math.min(n - 1, Math.floor((t.clientX - r.left - padL) / step)))); };
  }

  function drawBreadth(canvas) {
    const rows = S.breadth.filter(r => r.p50 != null);
    if (!rows.length) return;
    const { ctx, w, h } = setupCanvas(canvas);
    const padL = 34, padR = 58, padT = 10, padB = 22, n = rows.length;
    const x = i => padL + (w - padL - padR) * (n === 1 ? 0.5 : i / (n - 1));
    const yP = p => padT + (100 - p) / 100 * (h - padT - padB);
    const nv = rows.map(r => r.nifty).filter(isNum); const nlo = Math.min(...nv), nhi = Math.max(...nv);
    const yN = v => padT + (nhi - v) / ((nhi - nlo) || 1) * (h - padT - padB);
    const muted = css("--muted2") || "#6f7f9b";
    ctx.font = "10px Inter, system-ui, sans-serif"; ctx.textBaseline = "middle";
    [0, 25, 50, 75, 100].forEach(p => { ctx.strokeStyle = "#25334c"; ctx.globalAlpha = .5; ctx.beginPath(); ctx.moveTo(padL, yP(p)); ctx.lineTo(w - padR, yP(p)); ctx.stroke(); ctx.globalAlpha = 1; ctx.fillStyle = muted; ctx.fillText(p + "%", 4, yP(p)); });
    if (nv.length) [nlo, (nlo + nhi) / 2, nhi].forEach(v => { ctx.fillStyle = muted; ctx.fillText(nf(v, 0), w - padR + 6, yN(v)); });
    const line = (key, color, yf, dash) => { ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.setLineDash(dash || []); ctx.beginPath(); let on = false;
      rows.forEach((r, i) => { if (!isNum(r[key])) { on = false; return; } on ? ctx.lineTo(x(i), yf(r[key])) : ctx.moveTo(x(i), yf(r[key])); on = true; }); ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1; };
    line("nifty", "#91a0ba", yN, [4, 3]); line("p50", css("--blue") || "#7aa7ff", yP); line("p200", css("--green") || "#47e0ac", yP);
    let lastMonth = "";
    rows.forEach((r, i) => { const m = r.d.slice(0, 7); if (m !== lastMonth) { lastMonth = m; if (i > 0 && +r.d.slice(5, 7) % 2 === 1) { ctx.fillStyle = muted; ctx.fillText(new Date(r.d + "T00:00").toLocaleDateString("en-IN", { month: "short", year: "2-digit" }), x(i) - 14, h - 9); } } });
  }

  // ------------------------------------------------------------------ navigation & search
  function openView(view) { $(`.nav-item[data-view="${view}"]`)?.click(); }
  function openStock(sym) {
    if (location.hash !== `#stock/${encodeURIComponent(sym)}`) history.pushState(null, "", `#stock/${encodeURIComponent(sym)}`);
    S.stock = sym; openView("stock");
  }
  document.addEventListener("click", e => { const b = e.target.closest(".mx-sym[data-sym]"); if (b) { e.preventDefault(); openStock(b.dataset.sym); } });
  window.addEventListener("popstate", routeFromHash);
  function routeFromHash() { const m = location.hash.match(/^#stock\/(.+)$/); if (m) { S.stock = decodeURIComponent(m[1]); openView("stock"); } }

  function searchRows(q, limit = 10) {
    q = q.trim().toUpperCase(); if (!q) return [];
    const starts = [], contains = [];
    for (const r of S.rows) { if (r.s.startsWith(q)) starts.push(r); else if (r.s.includes(q) || r.n.toUpperCase().includes(q)) contains.push(r); }
    const rank = a => (a.stale ? 1e9 : 0) - (a.at || 0);
    return [...starts.sort((a, b) => rank(a) - rank(b) || a.s.length - b.s.length), ...contains.sort((a, b) => rank(a) - rank(b))].slice(0, limit);
  }
  function attachSearch(input, onPick) {
    const box = document.createElement("div"); box.className = "mx-suggest hidden"; input.parentElement.classList.add("mx-search-wrap"); input.after(box);
    let items = [], active = 0;
    const paint = () => { box.innerHTML = items.map((r, i) => `<button type="button" class="${i === active ? "active" : ""}" data-i="${i}"><b>${esc(r.s)}</b><span>${esc(r.n || r.ind)}</span><em>${nf(r.c)} ${pct(r.ch)}</em></button>`).join(""); box.classList.toggle("hidden", !items.length); };
    input.addEventListener("input", () => load().then(() => { items = searchRows(input.value); active = 0; paint(); }).catch(() => {}));
    input.addEventListener("keydown", e => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); active = (active + (e.key === "ArrowDown" ? 1 : -1) + items.length) % Math.max(items.length, 1); paint(); }
      else if (e.key === "Enter" && items[active]) { e.preventDefault(); pick(items[active]); }
      else if (e.key === "Escape") { items = []; paint(); }
    });
    box.addEventListener("mousedown", e => { const b = e.target.closest("[data-i]"); if (b) { e.preventDefault(); pick(items[+b.dataset.i]); } });
    input.addEventListener("blur", () => setTimeout(() => { items = []; paint(); }, 150));
    function pick(r) { input.value = ""; items = []; paint(); input.blur(); onPick(r.s); }
  }

  function withData(host, fn) {
    if (S.snap) return fn();
    host.innerHTML = `<div class="empty-state">Loading market data…</div>`;
    load().then(fn).catch(err => {
      host.innerHTML = `<div class="notice notice-warning"><span class="notice-symbol">!</span><div><strong>Market dataset not published yet</strong><p>Could not load <code>${BASE}snapshot.json</code> (${esc(err.message)}). It is built by the GitHub Actions workflow from data/history.csv. Locally, run <code>python .github/scripts/build_market_data.py</code> and serve the folder over HTTP.</p></div></div>`;
    });
  }

  // ------------------------------------------------------------------ Markets view
  function renderMarkets() {
    const host = $("#mxMarketsBody");
    withData(host, () => {
      const b = S.bench, last = S.breadth[S.breadth.length - 1] || {}, adr = last.dec ? last.adv / last.dec : null;
      host.innerHTML = `
        <div class="metric-grid">
          <article class="metric-card"><div class="metric-top"><span>NIFTY 50</span><span class="metric-icon green">◆</span></div><div class="metric-value">${b ? nf(b.c) : "—"}</div><div class="metric-foot"><span class="metric-muted">1D ${pct(b?.ch)} · 1M ${pct(b?.r1m)}</span><span class="metric-tag neutral">${b && b.c > b.s200 ? "Above" : "Below"} SMA200</span></div></article>
          <article class="metric-card"><div class="metric-top"><span>ADVANCE / DECLINE</span><span class="metric-icon blue">⇅</span></div><div class="metric-value">${last.adv ?? "—"}<small class="mx-slash">/</small>${last.dec ?? "—"}</div><div class="metric-foot"><span class="metric-muted">Ratio ${nf(adr)} · unchanged ${last.unch ?? "—"}</span><span class="metric-tag ${adr >= 1 ? "positive" : "neutral"}">${adr >= 1 ? "Broad gains" : "Broad losses"}</span></div></article>
          <article class="metric-card"><div class="metric-top"><span>ABOVE 200-SESSION SMA</span><span class="metric-icon violet">▲</span></div><div class="metric-value">${nf(last.p200, 1)}%</div><div class="metric-foot"><span class="metric-muted">Above 50-SMA: ${nf(last.p50, 1)}%</span><span class="metric-tag neutral">Participation</span></div></article>
          <article class="metric-card"><div class="metric-top"><span>NEW 52W HIGHS / LOWS</span><span class="metric-icon amber">⚑</span></div><div class="metric-value">${last.nh ?? "—"}<small class="mx-slash">/</small>${last.nl ?? "—"}</div><div class="metric-foot"><span class="metric-muted">Stocks with a full year of data</span><span class="metric-tag neutral">Session ${esc(last.d || "")}</span></div></article>
        </div>
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Market breadth</h2><p>Share of stocks above their 50- and 200-session averages vs NIFTY 50 (dashed, right axis)</p></div>
          <div class="mx-legend"><span><i style="background:var(--green)"></i>% above SMA200</span><span><i style="background:var(--blue)"></i>% above SMA50</span><span><i class="dash"></i>NIFTY 50</span></div></div>
          <div class="mx-chart-wrap short"><canvas id="mxBreadthChart" aria-label="Market breadth chart" role="img"></canvas></div></section>
        <section class="panel mx-panel">
          <div class="panel-toolbar"><div class="tab-group" id="mxMoverTabs">${[["gainers", "Top gainers"], ["losers", "Top losers"], ["volume", "Volume shockers"], ["highs", "52W highs"], ["lows", "52W lows"], ["gapup", "Gap ups"]].map(([k, l]) => `<button class="tab ${S.moversTab === k ? "active" : ""}" data-tab="${k}">${l}</button>`).join("")}</div>
            <button class="button button-small button-secondary" id="mxMoversExport">↓ CSV</button></div>
          <div class="filter-panel mx-inline-filters">${universeControls("mxMov")}</div>
          <div id="mxMoversTable"></div></section>`;
      drawBreadth($("#mxBreadthChart"));
      $("#mxMoverTabs").addEventListener("click", e => { const t = e.target.closest("[data-tab]"); if (!t) return; S.moversTab = t.dataset.tab; $$(".tab", $("#mxMoverTabs")).forEach(x => x.classList.toggle("active", x === t)); tableState.delete($("#mxMoversTable")); renderMovers(); });
      ["mxMovCap", "mxMovLiquid"].forEach(id => $("#" + id).addEventListener("change", renderMovers));
      $("#mxMoversExport").addEventListener("click", () => exportTable($("#mxMoversTable"), `movers-${S.moversTab}-${S.snap.meta.latest_market_date}.csv`));
      renderMovers();
    });
  }
  function renderMovers() {
    const rows = universe(S.rows, readUniverse("mxMov")), t = S.moversTab;
    const spec = {
      gainers: [rows.filter(r => r.ch > 0), ["s", "c", "ch", "r1w", "r1m", "rv", "at"], "ch", -1],
      losers: [rows.filter(r => r.ch < 0), ["s", "c", "ch", "r1w", "r1m", "rv", "at"], "ch", 1],
      volume: [rows.filter(r => r.ev.includes("vol3")), ["s", "c", "ch", "rv", "at", "rsi"], "rv", -1],
      highs: [rows.filter(r => r.ev.includes("h52")), ["s", "c", "ch", "r3m", "r1y", "rv", "at"], "r3m", -1],
      lows: [rows.filter(r => r.ev.includes("l52")), ["s", "c", "ch", "r3m", "r1y", "rv", "at"], "r3m", 1],
      gapup: [rows.filter(r => r.ev.includes("gap_up")), ["s", "c", "gap", "ch", "rv", "at"], "gap", -1],
    }[t];
    const host = $("#mxMoversTable");
    if (!tableState.has(host)) tableState.set(host, { sort: spec[2], dir: spec[3] });
    renderTable(host, spec[0], spec[1], { limit: 50 });
  }

  // ------------------------------------------------------------------ Stock view
  function renderStock() {
    const host = $("#mxStockBody");
    withData(host, () => {
      const r = S.bySym.get(S.stock) || S.bySym.get("RELIANCE") || S.rows[0];
      if (!r) { host.innerHTML = `<div class="empty-state">No stocks in the dataset.</div>`; return; }
      S.stock = r.s;
      const b = S.bench || {}, watched = S.watch.has(r.s);
      const atr = isNum(r.atrp) ? r.atrp * r.c / 100 : null;
      const rangePos = isNum(r.h52) && isNum(r.l52) && r.h52 > r.l52 ? (r.c - r.l52) / (r.h52 - r.l52) * 100 : null;
      const trend = isNum(r.s50) && isNum(r.s200) ? (r.c > r.s50 && r.s50 > r.s200 ? ["Uptrend", "Close > SMA50 > SMA200"] : r.c < r.s50 && r.s50 < r.s200 ? ["Downtrend", "Close < SMA50 < SMA200"] : ["Mixed", "Averages are not stacked"]) : ["Insufficient history", "Needs 200 sessions"];
      const rsiZone = !isNum(r.rsi) ? "—" : r.rsi >= 70 ? "Overbought (≥70)" : r.rsi <= 30 ? "Oversold (≤30)" : r.rsi >= 50 ? "Bullish zone (50–70)" : "Bearish zone (30–50)";
      const peers = r.cap ? S.rows.filter(x => x.ind === r.ind && x.s !== r.s && !x.stale && x.cap).sort((a, c) => (c.at || 0) - (a.at || 0)).slice(0, 12) : [];
      host.innerHTML = `
        ${r.stale ? `<div class="notice notice-warning"><span class="notice-symbol">!</span><div><strong>No trade on the latest session</strong><p>Last bar is ${esc(r.d)}, older than the market date ${esc(S.snap.meta.latest_market_date)}. The stock may be suspended, illiquid or moved out of the EQ series.</p></div></div>` : ""}
        ${r.ca ? `<div class="notice notice-warning"><span class="notice-symbol">!</span><div><strong>Unadjusted price jump on ${esc(r.ca)}</strong><p>A one-day move of 40% or more is in the stored history, often a split or bonus. Prices are not adjusted, so returns and the 52-week range spanning that date are left blank, and moving averages near it are distorted.</p></div></div>` : ""}
        <section class="panel mx-stock-head">
          <div><div class="eyebrow">${esc(r.ind)}${r.cap ? " · " + CAP_LABELS[r.cap] : ""}${r.n50 ? " · NIFTY 50" : ""}</div>
            <h1>${esc(r.s)} <small>${esc(r.n)}</small></h1>
            <div class="mx-price"><strong>₹${nf(r.c)}</strong> ${pct(r.ch)} <span>EOD ${esc(r.d)}</span></div></div>
          <div class="heading-actions"><button class="button button-secondary" id="mxWatchToggle">${watched ? "★ In watchlist" : "☆ Add to watchlist"}</button>
            <button class="button button-primary" id="mxPlanTrade" ${atr ? "" : "disabled"}>◈ Plan in risk calculator</button></div>
        </section>
        <section class="panel mx-panel">
          <div class="panel-toolbar"><div class="tab-group" id="mxRange">${[[63, "3M"], [126, "6M"], [250, "1Y"], [9999, "All"]].map(([n, l]) => `<button class="tab ${S.range === n ? "active" : ""}" data-n="${n}">${l}</button>`).join("")}</div>
            <div class="tab-group" id="mxOverlays">${[["s20", "SMA20"], ["s50", "SMA50"], ["s200", "SMA200"], ["vw20", "VWAP20"]].map(([k, l]) => `<button class="tab ${S.overlays[k] ? "active" : ""}" data-k="${k}">${l}</button>`).join("")}</div></div>
          <div class="mx-readout" id="mxReadout">Loading chart…</div>
          <div class="mx-chart-wrap"><canvas id="mxCandles" aria-label="${esc(r.s)} daily candlestick chart" role="img"></canvas></div>
        </section>
        <div class="mx-two-col">
          <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Performance</h2><p>Close-to-close, vs NIFTY 50</p></div></div>
            <div class="mx-stat-grid">${[["1D", "ch"], ["1W", "r1w"], ["1M", "r1m"], ["3M", "r3m"], ["6M", "r6m"], ["1Y", "r1y"]].map(([l, k]) =>
              `<div><span>${l}</span><strong>${pct(r[k])}</strong><small>NIFTY ${pct(b[k])}</small></div>`).join("")}</div>
            <div class="mx-range"><div class="mx-range-labels"><span>52W low ₹${nf(r.l52)}</span><span>${isNum(r.dh) ? nf(-r.dh, 1) + "% below high" : ""}</span><span>52W high ₹${nf(r.h52)}</span></div>
              <div class="mx-range-track">${isNum(rangePos) ? `<i style="left:${Math.max(0, Math.min(100, rangePos))}%"></i>` : ""}</div></div>
          </section>
          <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Technicals</h2><p>Latest session · descriptive, not a recommendation</p></div></div>
            <div class="mx-kv">
              <div><span>Trend</span><strong>${trend[0]}</strong><small>${trend[1]}</small></div>
              <div><span>RSI (14, Wilder)</span><strong>${nf(r.rsi, 1)}</strong><small>${rsiZone}</small></div>
              <div><span>MACD (12, 26, 9)</span><strong>${nf(r.macd, 2)}</strong><small>Histogram ${isNum(r.macdh) ? (r.macdh >= 0 ? "positive" : "negative") + " " + nf(r.macdh, 2) : "—"}</small></div>
              ${["s20", "s50", "s200"].map(k => `<div><span>${k.replace("s", "SMA ")}</span><strong>₹${nf(r[k])}</strong><small>Close ${isNum(r[k]) ? pct((r.c / r[k] - 1) * 100, 1) + " vs average" : "—"}</small></div>`).join("")}
              <div><span>VWAP (today)</span><strong>₹${nf(r.vw)}</strong><small>${isNum(r.vw) ? `Close ${pct((r.c / r.vw - 1) * 100, 1)} vs session VWAP` : "Traded value unavailable"}</small></div>
              <div><span>VWAP (20 sessions)</span><strong>₹${nf(r.vw20)}</strong><small>${isNum(r.vw20) ? `Close ${pct((r.c / r.vw20 - 1) * 100, 1)} vs 20-session VWAP` : "Needs 20 sessions of traded value"}</small></div>
              <div><span>EMA 20</span><strong>₹${nf(r.e20)}</strong><small>Close ${isNum(r.e20) ? pct((r.c / r.e20 - 1) * 100, 1) : "—"}</small></div>
              <div><span>ATR 14</span><strong>₹${nf(atr)}</strong><small>${nf(r.atrp)}% of price · 2×ATR stop ₹${nf(atr ? r.c - 2 * atr : null)}</small></div>
              <div><span>Volume</span><strong>${r.v.toLocaleString("en-IN")}</strong><small>${isNum(r.rv) ? nf(r.rv) + "× 20-session avg" : "—"}</small></div>
              <div><span>Avg turnover (20)</span><strong>${cr(r.at)}</strong><small>${isNum(r.at) && r.at >= LIQUID_CR ? "Above" : "Below"} ₹${LIQUID_CR} cr liquidity floor</small></div>
            </div>
            <div class="mx-events">${r.ev.length ? r.ev.map(e => `<span class="mx-chip">${esc(EVENT_LABELS[e] || e)}</span>`).join("") : `<span class="mx-muted">No pattern or crossover events on the latest session.</span>`}</div>
          </section>
        </div>
        ${convictionPanel(r)}
        ${sePanel(r)}
        ${fundamentalsPanel(r)}
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Industry peers</h2><p>${r.cap ? `${esc(r.ind)} · most traded classified stocks` : "Peers are available for Nifty 100/Midcap/Smallcap/Microcap members only"}</p></div></div>
          <div id="mxPeers"></div></section>`;
      if (peers.length) renderTable($("#mxPeers"), peers, hasFund() ? ["s", "c", "ch", "r1m", "r1y", "mcap", "pe", "pb", "roe"] : ["s", "c", "ch", "r1m", "r3m", "r1y", "rsi", "at"],
        { limit: 12, sort: hasFund() ? "mcap" : "at" });
      else $("#mxPeers").innerHTML = `<div class="empty-state">No classified peers.</div>`;
      $("#mxWatchToggle").addEventListener("click", () => { toggleWatch(r.s); renderStock(); });
      $("#mxPlanTrade").addEventListener("click", () => planTrade(r, atr));
      $("#mxRange").addEventListener("click", e => { const t = e.target.closest("[data-n]"); if (!t) return; S.range = +t.dataset.n; renderStock(); });
      $("#mxOverlays").addEventListener("click", e => { const t = e.target.closest("[data-k]"); if (!t) return; S.overlays[t.dataset.k] = !S.overlays[t.dataset.k]; renderStock(); });
      document.title = `${r.s} · EDGE NSE`;
      loadBars(r).then(bars => { if (S.stock === r.s && $("#mxCandles")) { S.lastBars = bars; drawCandles($("#mxCandles"), bars, $("#mxReadout")); } })
        .catch(err => { const el = $("#mxReadout"); if (el) el.textContent = `Chart data unavailable (${err.message}).`; });
    });
  }
  function fundamentalsPanel(r) {
    const meta = S.snap.meta.fundamentals || {};
    if (!meta.stocks) {
      return `<section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Fundamentals</h2><p>P/E, market cap, P/B, dividend yield, ROE, ROCE, debt/equity</p></div></div>
        <div class="empty-state">No fundamentals loaded yet. Commit a CSV export as <code>data/fundamentals.csv</code>; see “Fundamentals” in the README.</div></section>`;
    }
    const any = ["pe", "mcap", "pb", "dy", "roe", "roce", "de", "eps"].some(k => isNum(r[k]));
    const peers = r.cap ? S.rows.filter(x => x.ind === r.ind && !x.stale && isNum(x.pe)) : [];
    const indPe = median(peers.map(x => x.pe));
    const tile = (label, value, note) => `<div><span>${label}</span><strong>${value}</strong><small>${note || "&nbsp;"}</small></div>`;
    return `<section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Fundamentals</h2>
        <p>${r.fa ? `Inputs as of ${esc(r.fa)} · P/E, market cap, P/B and yield recomputed at the ${esc(r.d)} close` : "From data/fundamentals.csv"}</p></div></div>
      ${any ? `<div class="mx-stat-grid mx-fund-grid">
        ${tile("Market cap", cr(r.mcap), isNum(r.mcap) ? (r.mcap >= 100000 ? "≥ ₹1 lakh cr" : r.mcap >= 20000 ? "₹20k–1 lakh cr" : r.mcap >= 5000 ? "₹5k–20k cr" : "< ₹5k cr") : "")}
        ${tile("P/E (TTM)", isNum(r.pe) ? nf(r.pe, 1) : isNum(r.eps) && r.eps <= 0 ? `<span class="down">Loss-making</span>` : "—", isNum(indPe) ? `Industry median ${nf(indPe, 1)}` : "")}
        ${tile("EPS (TTM)", isNum(r.eps) ? "₹" + nf(r.eps) : "—", "Trailing 12 months")}
        ${tile("P/B", nf(r.pb, 2), "Price / book value")}
        ${tile("Dividend yield", isNum(r.dy) ? nf(r.dy, 2) + "%" : "—", "Trailing dividend / price")}
        ${tile("ROE", isNum(r.roe) ? nf(r.roe, 1) + "%" : "—", "As reported in export")}
        ${tile("ROCE", isNum(r.roce) ? nf(r.roce, 1) + "%" : "—", "As reported in export")}
        ${tile("Debt / equity", nf(r.de, 2), isNum(r.de) ? (r.de <= 0.1 ? "Nearly debt-free" : r.de <= 1 ? "Moderate leverage" : "High leverage") : "")}
      </div>` : `<div class="empty-state">${r.fa ? `Blanked: a ≥40% price jump after ${esc(r.fa)} (likely split or bonus) makes the exported EPS and share count stale. Re-export to refresh.` : `${esc(r.s)} is not in data/fundamentals.csv.`}</div>`}
    </section>`;
  }
  function planTrade(r, atr) {
    const entry = Math.round(r.c * 1.0025 * 20) / 20, stop = Math.round((r.c - 2 * atr) * 20) / 20, target = Math.round((entry + 2 * (entry - stop)) * 20) / 20;
    const set = (id, v) => { const el = $("#" + id); if (el) { el.value = v; el.dispatchEvent(new Event("input", { bubbles: true })); } };
    set("entryInput", entry.toFixed(2)); set("stopInput", stop.toFixed(2)); set("targetInput", target.toFixed(2));
    openView("risk"); $("#calculateRisk")?.click();
  }

  // ------------------------------------------------------------------ Scans
  const SCANS = [
    { group: "Price action", id: "h52", name: "New 52-week high", desc: "High exceeded the prior 250-session high", test: r => r.ev.includes("h52"), cols: ["s", "c", "ch", "r3m", "r1y", "rv", "at"], sort: "r3m" },
    { group: "Price action", id: "l52", name: "New 52-week low", desc: "Low broke the prior 250-session low", test: r => r.ev.includes("l52"), cols: ["s", "c", "ch", "r3m", "r1y", "rv", "at"], sort: "r3m", dir: 1 },
    { group: "Price action", id: "near52", name: "Within 5% of 52W high, above SMA200", desc: "Leaders consolidating near highs", test: r => isNum(r.dh) && r.dh >= -5 && r.c > r.s200, cols: ["s", "c", "ch", "dh", "r3m", "rsi", "at"], sort: "dh" },
    { group: "Price action", id: "b20v", name: "20-session breakout on volume", desc: "Close above prior 20-session high with ≥1.5× volume", test: r => r.ev.includes("b20") && r.rv >= 1.5, cols: ["s", "c", "ch", "rv", "rsi", "r1m", "at"], sort: "rv" },
    { group: "Price action", id: "d20", name: "20-session breakdown", desc: "Close below prior 20-session low", test: r => r.ev.includes("d20"), cols: ["s", "c", "ch", "rv", "rsi", "r1m", "at"], sort: "ch", dir: 1 },
    { group: "Price action", id: "gapup", name: "Gap up", desc: "Open ≥2% above previous close and above prior high", test: r => r.ev.includes("gap_up"), cols: ["s", "c", "gap", "ch", "rv", "at"], sort: "gap" },
    { group: "Price action", id: "gapdn", name: "Gap down", desc: "Open ≥2% below previous close and below prior low", test: r => r.ev.includes("gap_dn"), cols: ["s", "c", "gap", "ch", "rv", "at"], sort: "gap", dir: 1 },
    { group: "Moving averages", id: "gx", name: "Golden cross", desc: "SMA50 crossed above SMA200 in the last 5 sessions", test: r => r.ev.includes("gx"), cols: ["s", "c", "ch", "r1m", "r3m", "rsi", "at"], sort: "r1m" },
    { group: "Moving averages", id: "dx", name: "Death cross", desc: "SMA50 crossed below SMA200 in the last 5 sessions", test: r => r.ev.includes("dx"), cols: ["s", "c", "ch", "r1m", "r3m", "rsi", "at"], sort: "r1m", dir: 1 },
    { group: "Moving averages", id: "stack", name: "Above all key averages", desc: "Close > SMA20 > SMA50 > SMA200", test: r => r.c > r.s20 && r.s20 > r.s50 && r.s50 > r.s200, cols: ["s", "c", "ch", "r1m", "r3m", "rsi", "at"], sort: "r3m" },
    { group: "Moving averages", id: "e20up", name: "Reclaimed EMA20 in an uptrend", desc: "Crossed back above EMA20 with close > SMA200", test: r => r.ev.includes("e20_up") && r.c > r.s200, cols: ["s", "c", "ch", "rsi", "rv", "at"], sort: "ch" },
    { group: "Moving averages", id: "below200", name: "Below SMA200", desc: "Close under the 200-session average", test: r => isNum(r.s200) && r.c < r.s200, cols: ["s", "c", "ch", "r3m", "r1y", "rsi", "at"], sort: "r3m", dir: 1 },
    { group: "Momentum", id: "macdup", name: "MACD bullish crossover", desc: "MACD line crossed above its signal today", test: r => r.ev.includes("macd_up"), cols: ["s", "c", "ch", "rsi", "r1m", "at"], sort: "ch" },
    { group: "Momentum", id: "macddn", name: "MACD bearish crossover", desc: "MACD line crossed below its signal today", test: r => r.ev.includes("macd_dn"), cols: ["s", "c", "ch", "rsi", "r1m", "at"], sort: "ch", dir: 1 },
    { group: "Momentum", id: "rsios", name: "RSI oversold", desc: "RSI(14) ≤ 30", test: r => r.rsi <= 30, cols: ["s", "c", "ch", "rsi", "r1m", "at"], sort: "rsi", dir: 1 },
    { group: "Momentum", id: "rsiob", name: "RSI overbought", desc: "RSI(14) ≥ 70", test: r => r.rsi >= 70, cols: ["s", "c", "ch", "rsi", "r1m", "at"], sort: "rsi" },
    { group: "Momentum", id: "dipup", name: "Pullback in uptrend", desc: "RSI ≤ 40 while close > SMA200", test: r => r.rsi <= 40 && r.c > r.s200, cols: ["s", "c", "ch", "rsi", "r3m", "at"], sort: "rsi", dir: 1 },
    { group: "Momentum", id: "rs3m", name: "Outperforming NIFTY 50 (3M)", desc: "3-month return above the index, close > SMA200", test: r => isNum(r.r3m) && S.bench && r.r3m > S.bench.r3m && r.c > r.s200, cols: ["s", "c", "r3m", "r1m", "r1y", "rsi", "at"], sort: "r3m" },
    { group: "VWAP", id: "vwreclaim", name: "Reclaimed 20-session VWAP", desc: "Closed back above the 20-session VWAP today", test: r => r.ev.includes("vw20_up"), cols: ["s", "c", "ch", "vwd", "vw20d", "rv", "at"], sort: "rv" },
    { group: "VWAP", id: "vwstrong", name: "Strong close above VWAP on volume", desc: "Up day closing above session VWAP on ≥1.5× volume", test: r => r.ch > 0 && r.c > r.vw && r.rv >= 1.5, cols: ["s", "c", "ch", "vwd", "vw20d", "rv", "at"], sort: "rv" },
    { group: "VWAP", id: "vwtrend", name: "Above VWAP20 in an uptrend", desc: "Close > 20-session VWAP and > SMA200", test: r => r.c > r.vw20 && r.c > r.s200, cols: ["s", "c", "ch", "vw20d", "r1m", "rsi", "at"], sort: "vw20d" },
    { group: "VWAP", id: "vwlost", name: "Lost 20-session VWAP", desc: "Closed below the 20-session VWAP today", test: r => r.ev.includes("vw20_dn"), cols: ["s", "c", "ch", "vwd", "vw20d", "rv", "at"], sort: "ch", dir: 1 },
    { group: "Volume", id: "vol3", name: "Volume shockers", desc: "Volume ≥ 3× the 20-session average", test: r => r.ev.includes("vol3"), cols: ["s", "c", "ch", "rv", "at", "rsi"], sort: "rv" },
    { group: "Volume", id: "volup", name: "High-volume up day", desc: "Up ≥2% on ≥2× average volume", test: r => r.ch >= 2 && r.rv >= 2, cols: ["s", "c", "ch", "rv", "at", "rsi"], sort: "rv" },
    { group: "Candlesticks", id: "bullen", name: "Bullish engulfing", desc: "Green body engulfs the prior red body", test: r => r.ev.includes("bull_engulf"), cols: ["s", "c", "ch", "rsi", "rv", "at"], sort: "rv" },
    { group: "Candlesticks", id: "bearen", name: "Bearish engulfing", desc: "Red body engulfs the prior green body", test: r => r.ev.includes("bear_engulf"), cols: ["s", "c", "ch", "rsi", "rv", "at"], sort: "rv" },
    { group: "Candlesticks", id: "nr7", name: "NR7 volatility contraction", desc: "Narrowest high-low range of the last 7 sessions", test: r => r.ev.includes("nr7"), cols: ["s", "c", "ch", "rsi", "rv", "at"], sort: "at" },
    { group: "Candlesticks", id: "inside", name: "Inside day", desc: "High and low inside the prior session's range", test: r => r.ev.includes("inside"), cols: ["s", "c", "ch", "rsi", "rv", "at"], sort: "at" },
    { group: "Fundamentals", id: "value", name: "Value with quality", desc: "P/E below 15 and ROE of at least 15%", test: r => r.pe > 0 && r.pe < 15 && r.roe >= 15, cols: ["s", "c", "mcap", "pe", "pb", "roe", "de", "r1y"], sort: "pe", dir: 1 },
    { group: "Fundamentals", id: "compounder", name: "High ROCE, low debt", desc: "ROCE of at least 20% and debt/equity at most 0.5", test: r => r.roce >= 20 && isNum(r.de) && r.de <= 0.5, cols: ["s", "c", "mcap", "pe", "roce", "roe", "de", "r1y"], sort: "roce" },
    { group: "Fundamentals", id: "yield", name: "Dividend yield ≥ 3%", desc: "Trailing dividend yield at today's close", test: r => r.dy >= 3, cols: ["s", "c", "mcap", "dy", "pe", "roe", "r1y"], sort: "dy" },
    { group: "Fundamentals", id: "lowpb", name: "Below 1.5× book, profitable", desc: "P/B under 1.5 with ROE above 12%", test: r => r.pb > 0 && r.pb < 1.5 && r.roe > 12, cols: ["s", "c", "mcap", "pb", "pe", "roe", "r1y"], sort: "pb", dir: 1 },
    { group: "Fundamentals", id: "trendvalue", name: "Uptrend at a reasonable P/E", desc: "Close above SMA200 with P/E between 0 and 25", test: r => r.c > r.s200 && r.pe > 0 && r.pe <= 25, cols: ["s", "c", "ch", "r3m", "mcap", "pe", "roe"], sort: "r3m" },
  ];
  const FIELDS = [
    ["c", "Close (₹)"], ["ch", "1D change %"], ["r1w", "1W return %"], ["r1m", "1M return %"], ["r3m", "3M return %"], ["r6m", "6M return %"], ["r1y", "1Y return %"],
    ["dh", "% from 52W high"], ["dl", "% above 52W low"], ["rsi", "RSI 14"], ["rv", "Relative volume ×"], ["at", "Avg turnover (₹ cr)"], ["atrp", "ATR % of price"],
    ["gap", "Gap %"], ["vw", "VWAP (today)"], ["vw20", "VWAP (20 sessions)"], ["macdh", "MACD histogram"], ["s20", "SMA20"], ["s50", "SMA50"], ["s200", "SMA200"], ["e20", "EMA20"],
    ["mcap", "Market cap (₹ cr)"], ["pe", "P/E (TTM)"], ["eps", "EPS (TTM, ₹)"], ["pb", "P/B"], ["dy", "Dividend yield %"],
    ["roe", "ROE %"], ["roce", "ROCE %"], ["de", "Debt / equity"],
  ];
  const fieldLabel = k => (FIELDS.find(f => f[0] === k) || [k, k])[1];
  function customTest(scan) {
    return r => scan.conds.every(({ f, op, v, vf }) => {
      const a = r[f], b = vf ? r[vf] : +v;
      if (!isNum(a) || !isNum(b)) return false;
      return op === ">" ? a > b : op === "<" ? a < b : op === ">=" ? a >= b : a <= b;
    });
  }
  function allScans() {
    const hc = (S.snap?.meta?.conviction?.scans || []).map(sc => ({ group: "High conviction", id: sc.id, name: sc.name, desc: sc.desc, hc: true,
      test: r => (r.hc || []).includes(sc.id), cols: ["s", "c", "ch", "cv", "r1m", "rv", "vw20d", "at"], sort: "cv" }));
    return [...hc, ...SCANS.filter(sc => sc.group !== "Fundamentals" || hasFund()), ...S.custom.map(c => ({ group: "My scans", id: c.id, name: c.name, desc: c.conds.map(x => `${fieldLabel(x.f)} ${x.op} ${x.vf ? fieldLabel(x.vf) : x.v}`).join(" AND "),
      test: customTest(c), cols: withFund(["s", "c", "ch", "r1m", "rsi", "rv", "at"]), sort: hasFund() ? "mcap" : "at", custom: true }))];
  }
  function renderScans() {
    const host = $("#mxScansBody");
    withData(host, () => {
      host.innerHTML = `${regimeNotice()}<div class="filter-panel panel">${universeControls("mxScan")}</div>
        <div class="mx-scan-layout"><aside class="panel mx-scan-list" id="mxScanList"></aside>
          <section class="panel mx-panel"><div id="mxScanEvidence"></div><div class="panel-toolbar"><div><h2 id="mxScanTitle"></h2><p id="mxScanDesc"></p></div>
            <div class="heading-actions"><button class="button button-small button-secondary" id="mxScanDelete">Delete scan</button><button class="button button-small button-secondary" id="mxScanExport">↓ CSV</button></div></div>
            <div id="mxScanTable"></div></section></div>
        ${seFinder()}
        ${evidenceTable()}
        <section class="panel mx-panel" id="mxBuilder"><div class="panel-toolbar"><div><h2>Build a custom scan</h2><p>All conditions must hold. Compare a field with a number or with another field (e.g. Close > SMA200). Saved in this browser.</p></div></div>
          <div id="mxConds"></div>
          <div class="mx-builder-foot"><button class="button button-small button-secondary" id="mxAddCond">＋ Condition</button>
            <div class="input-field"><input id="mxScanName" placeholder="Scan name" maxlength="60"></div>
            <button class="button button-small button-secondary" id="mxPreviewScan">Preview</button><button class="button button-small button-primary" id="mxSaveScan">Save scan</button></div></section>`;
      ["mxScanCap", "mxScanLiquid"].forEach(id => $("#" + id).addEventListener("change", () => { paintScanList(); runScan(); }));
      $("#mxScanExport").addEventListener("click", () => exportTable($("#mxScanTable"), `scan-${S.scanId}-${S.snap.meta.latest_market_date}.csv`));
      $("#mxScanDelete").addEventListener("click", () => { S.custom = S.custom.filter(c => c.id !== S.scanId); store.set(CUSTOM_KEY, S.custom); S.scanId = SCANS[0].id; paintScanList(); runScan(); });
      setupBuilder();
      wireSeFinder();
      paintScanList(); runScan();
    });
  }
  // ---- conviction evidence (backtest published by build_market_data.py / conviction.py)
  const VERDICT = { supported: "Edge supported", weak: "Weak edge", not_supported: "No edge", insufficient: "Too few signals" };
  const fmtR = x => isNum(x) ? `${x > 0 ? "+" : ""}${nf(x, 2)}R` : "—";
  function tradeStat(t) {
    return t && t.trades ? `<b>${fmtR(t.avg_r)}</b> avg/trade · ${nf(t.win, 0)}% win · t ${isNum(t.t_days) ? nf(t.t_days, 1) : "—"} · ${t.trades} trades on ${t.days} days` : "no trades";
  }
  function regimeNotice() {
    const reg = S.snap?.meta?.conviction?.regime;
    if (!reg) return "";
    return `<div class="notice ${reg.on ? "notice-info" : "notice-warning"} regime-banner"><span class="notice-symbol">${reg.on ? "✓" : "!"}</span><div>
      <strong>Market regime ${reg.on ? "favourable" : "unfavourable"}: NIFTY 50 is ${nf(Math.abs(reg.pct_vs_sma50), 1)}% ${reg.on ? "above" : "below"} its 50-session average</strong>
      <p>${regimeEvidenceText()}</p></div></div>`;
  }
  function regimeEvidenceText() {
    const ev = S.snap?.meta?.conviction?.evidence;
    if (!ev) return "";
    const b = ev.baseline?.h10 || {}, st = Object.values(ev.scans || {});
    const supported = st.filter(x => x.verdict === "supported").map(x => x.name);
    const avg = h => isNum(h?.mean) ? `${h.mean > 0 ? "+" : ""}${nf(h.mean, 2)}%` : "—";
    return `Backtest ${esc(ev.period[0])} to ${esc(ev.period[1])}: the average stock returned ${avg(b.on)} over 10 sessions with NIFTY above SMA50 and ${avg(b.off)} below it. `
      + (supported.length ? `Statistically supported edge: ${supported.map(esc).join(", ")}.` : "No scan shows a statistically supported edge, so treat matches as a watchlist, not entries.");
  }
  function evStat(h) {
    return h && h.n ? `<b>${h.median > 0 ? "+" : ""}${nf(h.median, 2)}%</b> median · ${nf(h.hit, 0)}% hit · ${h.excess > 0 ? "+" : ""}${nf(h.excess, 2)} pp vs avg stock · ${h.n} signals` : "no signals";
  }
  function evidenceCard(id) {
    const ev = S.snap?.meta?.conviction?.evidence, st = ev?.scans?.[id];
    if (!st) return "";
    return `<div class="mx-evidence"><div><span class="verdict ${st.verdict}">${VERDICT[st.verdict] || st.verdict}</span>
      <small>Backtest ${esc(ev.period[0])} to ${esc(ev.period[1])}</small></div>
      <div class="mx-evidence-row"><span>Trade rules, NIFTY above SMA50</span><span>${tradeStat(st.trade?.on)}</span></div>
      <div class="mx-evidence-row"><span>Trade rules, NIFTY below SMA50</span><span>${tradeStat(st.trade?.off)}</span></div>
      <div class="mx-evidence-row"><span>10-session return, regime on</span><span>${evStat(st.h10.on)}</span></div>
      ${yearRow(st.by_year_on)}</div>`;
  }
  function yearRow(by) {
    const ys = Object.entries(by || {});
    return ys.length ? `<div class="mx-evidence-row"><span>By year (regime on)</span><span>${ys.map(([y, t]) =>
      `${esc(y)}: <b class="${(t.avg_r || 0) > 0 ? "up" : "down"}">${fmtR(t.avg_r)}</b> (${t.trades || 0})`).join(" · ")}</span></div>` : "";
  }
  function evidenceTable() {
    const ev = S.snap?.meta?.conviction?.evidence;
    if (!ev) return "";
    const rows = [...Object.entries(ev.scans), ["baseline", { name: "All liquid stocks (baseline)", ...ev.baseline }]];
    const cell = h => h && h.n ? `${h.median > 0 ? "+" : ""}${nf(h.median, 2)}% <small>${nf(h.hit, 0)}% hit · n ${h.n}</small>` : "—";
    const tcell = t => t && t.trades ? `${fmtR(t.avg_r)} <small>${nf(t.win, 0)}% win · ${t.days} days · t ${isNum(t.t_days) ? nf(t.t_days, 1) : "—"}</small>` : "—";
    const years = [...new Set(Object.values(ev.scans).flatMap(st => Object.keys(st.by_year_on || {})))].sort();
    return `<section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Does any of this work? Backtest evidence</h2>
      <p>${esc(ev.period[0])} to ${esc(ev.period[1])}${ev.symbols ? ` · ${ev.symbols.toLocaleString("en-IN")} stocks incl. later-delisted` : ""} · ${ev.regime_days.on} sessions with NIFTY above SMA50, ${ev.regime_days.off} below.</p></div></div>
      <div class="table-wrap"><table class="mx-table"><thead><tr><th>SCAN</th><th class="num">TRADE RULES · REGIME ON</th><th class="num">TRADE RULES · REGIME OFF</th><th class="num">10D RETURN · REGIME ON</th><th class="num">VS AVG STOCK</th><th>VERDICT</th></tr></thead>
      <tbody>${rows.map(([id, st]) => `<tr><td>${esc(st.name)}</td><td class="num">${id === "baseline" ? "—" : tcell(st.trade?.on)}</td>
        <td class="num">${id === "baseline" ? "—" : tcell(st.trade?.off)}</td><td class="num">${cell(st.h10.on)}</td>
        <td class="num">${id === "baseline" ? "—" : (st.h10.on?.n ? `${st.h10.on.excess > 0 ? "+" : ""}${nf(st.h10.on.excess, 2)} pp` : "—")}</td>
        <td>${st.verdict ? `<span class="verdict ${st.verdict}">${VERDICT[st.verdict]}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>
      ${years.length ? `<h3 class="mx-subhead">Average R per trade by year, regime on: does the edge hold across markets?</h3>
      <div class="table-wrap"><table class="mx-table"><thead><tr><th>SCAN</th>${years.map(y => `<th class="num">${esc(y)}</th>`).join("")}</tr></thead>
      <tbody>${Object.values(ev.scans).map(st => `<tr><td>${esc(st.name)}</td>${years.map(y => { const t = st.by_year_on?.[y];
        return `<td class="num">${t && t.trades ? `<span class="${t.avg_r > 0 ? "up" : "down"}">${fmtR(t.avg_r)}</span> <small>${t.trades}</small>` : "—"}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>` : ""}
      <p class="small-note">${esc(ev.method)} Verdict: "supported" needs ≥30 signal days, positive average R, t ≥ 2 and a positive 10-day excess; "weak" is positive R with t ≥ 1. Prices are unadjusted, so windows around splits/bonuses are skipped. Past results, not a forecast.</p></section>`;
  }
  // ---- StockEdge-style scan catalogue (se_scans.py): same scan IDs as web.stockedge.com
  const seCat = () => S.snap?.meta?.stockedge_scans?.catalogue || [];
  const seLink = c => `https://web.stockedge.com/scan/${encodeURIComponent(c.slug)}/${c.id}`;
  function sePanel(r) {
    const cat = seCat();
    if (!cat.length || !Array.isArray(r.se)) return "";
    const byId = new Map(cat.map(c => [c.id, c])), groups = new Map();
    r.se.map(id => byId.get(id)).filter(Boolean).forEach(c => { if (!groups.has(c.cat)) groups.set(c.cat, []); groups.get(c.cat).push(c); });
    return `<section class="panel mx-panel"><div class="panel-toolbar"><div><h2>StockEdge-style scans: ${r.seb ?? 0} bullish, ${r.ser ?? 0} bearish</h2>
      <p>${r.se.length} of ${cat.length} re-implemented StockEdge scans match the latest session. Links open StockEdge's page for the same scan ID to compare.</p></div>
      <span class="se-pill ${r.seb - r.ser >= 10 ? "se-strong" : r.seb > r.ser ? "se-pos" : r.seb < r.ser ? "se-neg" : ""}">${r.seb ?? 0}▲ ${r.ser ?? 0}▼</span></div>
      ${groups.size ? `<div class="mx-se-groups">${[...groups].map(([g, cs]) => `<div class="mx-se-group"><h4>${esc(g)}</h4>${cs.map(c =>
        `<a class="${c.bias}" href="${seLink(c)}" target="_blank" rel="noopener" title="${esc(c.note || "")}">${esc(c.name)}</a>`).join("")}</div>`).join("")}</div>` : `<div class="empty-state">No StockEdge-style scan matches today.</div>`}
      <p class="small-note">Counting scan hits measures how many single conditions agree, not expected return. Use alongside the conviction score and backtest evidence.</p></section>`;
  }
  function seFinder() {
    const cat = seCat();
    if (!cat.length) return "";
    const groups = [...new Set(cat.map(c => c.cat))];
    return `<section class="panel mx-panel"><div class="panel-toolbar"><div><h2>StockEdge-style scans (${cat.length})</h2>
      <p>StockEdge's scan definitions re-implemented on our NSE data. The other ${510 - cat.length} of their 510 scans need delivery, F&O, shareholding or financial-results data we don't have.</p></div></div>
      <div class="mx-se-finder"><select id="mxSeSelect"><option value="">Choose a scan…</option>${groups.map(g => `<optgroup label="${esc(g)}">${cat.filter(c => c.cat === g).map(c =>
        `<option value="${c.id}">${c.bias === "bullish" ? "▲" : c.bias === "bearish" ? "▼" : "•"} ${esc(c.name)}</option>`).join("")}</optgroup>`).join("")}</select>
      <a id="mxSeLink" class="text-button hidden" target="_blank" rel="noopener">Compare on StockEdge ↗</a><span id="mxSeNote" class="mx-muted"></span></div>
      <div id="mxSeTable"></div></section>`;
  }
  function wireSeFinder() {
    const sel = $("#mxSeSelect");
    if (!sel) return;
    sel.addEventListener("change", () => {
      const c = seCat().find(x => x.id === +sel.value), host = $("#mxSeTable");
      tableState.delete(host);
      if (!c) { host.innerHTML = ""; $("#mxSeLink").classList.add("hidden"); $("#mxSeNote").textContent = ""; return; }
      $("#mxSeLink").href = seLink(c); $("#mxSeLink").classList.remove("hidden"); $("#mxSeNote").textContent = c.note || "";
      const rows = universe(S.rows, readUniverse("mxScan")).filter(r => (r.se || []).includes(c.id));
      renderTable(host, rows, ["s", "c", "ch", "r1m", "se", "cv", "rv", "at"], { sort: "se", limit: 300, empty: "No stocks match this scan on the latest session." });
    });
  }
  function convictionPanel(r) {
    const factors = S.snap?.meta?.conviction?.factors || [];
    if (!factors.length || r.cv == null) return "";
    const passed = new Set(r.cvf || []);
    const names = (S.snap.meta.conviction.scans || []).filter(sc => (r.hc || []).includes(sc.id)).map(sc => sc.name);
    return `<section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Conviction ${r.cv}/7</h2>
      <p>Independent confirmations on the latest session${names.length ? ` · In: ${names.map(esc).join(", ")}` : ""}</p></div>
      <span class="conv-pill ${r.cv >= 6 ? "conv-high" : r.cv >= 5 ? "conv-mid" : "conv-low"}">${r.cv >= 6 ? "High" : r.cv >= 5 ? "Moderate" : "Low"}</span></div>
      <div class="mx-checklist">${factors.map(f => `<div class="${passed.has(f.id) ? "ok" : "no"}"><i>${passed.has(f.id) ? "✓" : "✗"}</i>${esc(f.label)}</div>`).join("")}</div>
      <p class="small-note">ADX ${nf(r.adx, 1)} · 55-session relative strength vs NIFTY ${isNum(r.rs55) ? pct(r.rs55, 1) : "—"}. A count of confirmations, not a probability.</p></section>`;
  }
  function paintScanList() {
    const rows = universe(S.rows, readUniverse("mxScan"));
    let group = "";
    $("#mxScanList").innerHTML = allScans().map(sc => {
      const head = sc.group !== group ? `<div class="workspace-label">${esc((group = sc.group).toUpperCase())}</div>` : "";
      const count = rows.filter(sc.test).length;
      return `${head}<button class="mx-scan-item ${sc.id === S.scanId ? "active" : ""}" data-scan="${esc(sc.id)}"><span>${esc(sc.name)}</span><b>${count}</b></button>`;
    }).join("");
    $$("[data-scan]", $("#mxScanList")).forEach(b => b.addEventListener("click", () => { S.scanId = b.dataset.scan; tableState.delete($("#mxScanTable")); paintScanList(); runScan(); }));
  }
  function runScan(previewScan) {
    const sc = previewScan || allScans().find(s => s.id === S.scanId) || SCANS[0];
    const rows = universe(S.rows, readUniverse("mxScan")).filter(sc.test);
    $("#mxScanTitle").textContent = sc.name; $("#mxScanDesc").textContent = sc.desc;
    $("#mxScanDelete").classList.toggle("hidden", !sc.custom);
    $("#mxScanEvidence").innerHTML = sc.hc ? evidenceCard(sc.id) : "";
    const host = $("#mxScanTable");
    if (!tableState.has(host)) tableState.set(host, { sort: sc.sort, dir: sc.dir || -1 });
    renderTable(host, rows, sc.cols, { limit: 300, empty: "No stocks match this scan on the latest session." });
  }
  function setupBuilder() {
    const conds = [{ f: "c", op: ">", vf: "s200" }, { f: "rsi", op: "<", v: "40" }];
    const opts = (sel, withNum) => (withNum ? `<option value="">Number…</option>` : "") + FIELDS.map(([k, l]) => `<option value="${k}" ${k === sel ? "selected" : ""}>${esc(l)}</option>`).join("");
    const paint = () => {
      $("#mxConds").innerHTML = conds.map((c, i) => `<div class="mx-cond" data-i="${i}">
        <select data-p="f">${opts(c.f)}</select>
        <select data-p="op">${[">", ">=", "<", "<="].map(o => `<option ${o === c.op ? "selected" : ""}>${o}</option>`).join("")}</select>
        <select data-p="vf">${opts(c.vf || "", true)}</select>
        <input data-p="v" type="number" step="any" value="${esc(c.v ?? "")}" ${c.vf ? "hidden" : ""} placeholder="Value">
        <button class="icon-button" data-del="${i}" aria-label="Remove condition">×</button></div>`).join("");
    };
    $("#mxConds").addEventListener("change", e => { const row = e.target.closest(".mx-cond"); if (!row) return; const c = conds[+row.dataset.i]; c[e.target.dataset.p] = e.target.value; if (e.target.dataset.p === "vf" && !c.vf) delete c.vf; paint(); });
    $("#mxConds").addEventListener("input", e => { if (e.target.dataset.p === "v") conds[+e.target.closest(".mx-cond").dataset.i].v = e.target.value; });
    $("#mxConds").addEventListener("click", e => { const d = e.target.closest("[data-del]"); if (d) { conds.splice(+d.dataset.del, 1); paint(); } });
    $("#mxAddCond").addEventListener("click", () => { conds.push({ f: "r1m", op: ">", v: "0" }); paint(); });
    const current = () => {
      const valid = conds.filter(c => c.f && (c.vf || (c.v !== "" && c.v != null && Number.isFinite(+c.v)))).map(c => ({ ...c }));
      return valid.length ? { id: "custom-" + Date.now().toString(36), name: $("#mxScanName").value.trim() || "Custom scan", conds: valid } : null;
    };
    $("#mxPreviewScan").addEventListener("click", () => { const c = current(); if (!c) return; tableState.delete($("#mxScanTable"));
      runScan({ name: c.name + " (preview)", desc: c.conds.map(x => `${fieldLabel(x.f)} ${x.op} ${x.vf ? fieldLabel(x.vf) : x.v}`).join(" AND "), test: customTest(c), cols: ["s", "c", "ch", "r1m", "rsi", "rv", "at"], sort: "at" });
      $("#mxScanTitle").scrollIntoView({ behavior: "smooth", block: "center" }); });
    $("#mxSaveScan").addEventListener("click", () => { const c = current(); if (!c) return; S.custom.push(c); store.set(CUSTOM_KEY, S.custom); S.scanId = c.id; tableState.delete($("#mxScanTable")); paintScanList(); runScan(); });
    paint();
  }

  // ------------------------------------------------------------------ Sectors
  function renderSectors() {
    const host = $("#mxSectorsBody");
    withData(host, () => {
      host.innerHTML = `<div class="notice notice-info"><span class="notice-symbol">i</span><div><strong>Coverage</strong><p>Industries come from NSE's Nifty 100, Midcap 150, Smallcap 250 and Microcap 250 constituent lists (${S.snap.meta.classified_symbols} stocks). Figures are medians of member returns, not official sector index values.</p></div></div>
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Industry performance</h2><p>Click an industry to list its stocks</p></div>
          <div class="filter-field mx-compact"><select id="mxSecCap"><option value="all">All sizes</option><option value="large">Large cap</option><option value="mid">Mid cap</option><option value="small">Small cap</option><option value="micro">Micro cap</option></select></div></div>
          <div id="mxSectorTable"></div></section>
        <section class="panel mx-panel hidden" id="mxSectorDetail"><div class="panel-toolbar"><div><h2 id="mxSectorName"></h2><p>Members on the latest session</p></div><button class="button button-small button-secondary" id="mxSectorExport">↓ CSV</button></div><div id="mxSectorStocks"></div></section>`;
      $("#mxSecCap").addEventListener("change", paintSectors);
      $("#mxSectorExport").addEventListener("click", () => exportTable($("#mxSectorStocks"), `industry-${(S.sector || "").replace(/\W+/g, "-")}.csv`));
      paintSectors();
    });
  }
  const SEC = { sort: "r1m", dir: -1 };
  function paintSectors() {
    const cap = $("#mxSecCap").value;
    const members = S.rows.filter(r => r.cap && !r.stale && (cap === "all" || r.cap === cap));
    const groups = new Map();
    members.forEach(r => { if (!groups.has(r.ind)) groups.set(r.ind, []); groups.get(r.ind).push(r); });
    const agg = [...groups].map(([ind, rs]) => ({ ind, n: rs.length, ch: median(rs.map(r => r.ch)), r1w: median(rs.map(r => r.r1w)), r1m: median(rs.map(r => r.r1m)),
      r3m: median(rs.map(r => r.r3m)), r1y: median(rs.map(r => r.r1y)), adv: rs.filter(r => r.ch > 0).length, dec: rs.filter(r => r.ch < 0).length,
      p50: rs.filter(r => isNum(r.s50)).length ? rs.filter(r => r.c > r.s50).length / rs.filter(r => isNum(r.s50)).length * 100 : null,
      pe: median(rs.map(r => r.pe).filter(x => x > 0)), mcap: rs.some(r => isNum(r.mcap)) ? rs.reduce((t, r) => t + (r.mcap || 0), 0) : null }));
    const keys = [["ind", "INDUSTRY"], ["n", "STOCKS"], ["ch", "1D"], ["r1w", "1W"], ["r1m", "1M"], ["r3m", "3M"], ["r1y", "1Y"], ["p50", "% > SMA50"], ["ad", "ADV / DEC"],
      ...(hasFund() ? [["pe", "MEDIAN P/E"], ["mcap", "TOTAL MKT CAP"]] : [])];
    agg.sort((a, b) => { const k = SEC.sort === "ad" ? "adv" : SEC.sort; const x = a[k], y = b[k]; if (x == null) return 1; if (y == null) return -1; return (typeof x === "string" ? x.localeCompare(y) : x - y) * SEC.dir; });
    const cell = (a, k) => k === "ind" ? `<button class="mx-sym" data-ind="${esc(a.ind)}">${esc(a.ind)}</button>` : k === "n" ? a.n : k === "p50" ? nf(a.p50, 0) + "%" : k === "ad" ? `<span class="up">${a.adv}</span> / <span class="down">${a.dec}</span>` : k === "pe" ? nf(a.pe, 1) : k === "mcap" ? cr(a.mcap) : pct(a[k]);
    $("#mxSectorTable").innerHTML = `<div class="table-wrap"><table class="mx-table"><thead><tr>${keys.map(([k, l]) => `<th class="${k === "ind" ? "" : "num"}"><button data-ssort="${k}">${l}${SEC.sort === k ? (SEC.dir > 0 ? " ▲" : " ▼") : ""}</button></th>`).join("")}</tr></thead>
      <tbody>${agg.map(a => `<tr class="${a.ind === S.sector ? "mx-selected" : ""}">${keys.map(([k]) => `<td class="${k === "ind" ? "" : "num"}">${cell(a, k)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
    $$("[data-ssort]").forEach(b => b.addEventListener("click", () => { const k = b.dataset.ssort; SEC.dir = SEC.sort === k ? -SEC.dir : (k === "ind" ? 1 : -1); SEC.sort = k; paintSectors(); }));
    $$("[data-ind]").forEach(b => b.addEventListener("click", e => { e.stopPropagation(); S.sector = b.dataset.ind; paintSectors(); }));
    if (S.sector && groups.has(S.sector)) {
      $("#mxSectorDetail").classList.remove("hidden"); $("#mxSectorName").textContent = S.sector;
      renderTable($("#mxSectorStocks"), groups.get(S.sector), hasFund() ? ["s", "c", "ch", "r1m", "r3m", "r1y", "mcap", "pe", "pb", "roe"] : ["s", "c", "ch", "r1w", "r1m", "r3m", "r1y", "rsi", "at"],
        { sort: hasFund() ? "mcap" : "at" });
    }
  }

  // ------------------------------------------------------------------ Watchlist
  function toggleWatch(sym) { S.watch.has(sym) ? S.watch.delete(sym) : S.watch.add(sym); store.set(WATCH_KEY, [...S.watch]); updateWatchCount(); }
  function updateWatchCount() { const el = $("#watchNavCount"); if (el) el.textContent = S.watch.size; }
  function renderWatchlist() {
    const host = $("#mxWatchBody");
    withData(host, () => {
      host.innerHTML = `<section class="panel mx-panel"><div class="panel-toolbar"><div class="input-field mx-watch-add"><input id="mxWatchSearch" placeholder="Add a stock: type a symbol or company name" autocomplete="off"></div>
          <div class="heading-actions"><button class="button button-small button-secondary" id="mxWatchExport">↓ CSV</button><button class="button button-small button-secondary" id="mxWatchClear">Clear</button></div></div>
          <div id="mxWatchTable"></div><p class="small-note">Stored in this browser only. Export the CSV to move it to another device.</p></section>`;
      attachSearch($("#mxWatchSearch"), sym => { if (!S.watch.has(sym)) toggleWatch(sym); paintWatch(); });
      $("#mxWatchExport").addEventListener("click", () => exportTable($("#mxWatchTable"), "watchlist.csv"));
      $("#mxWatchClear").addEventListener("click", () => { if (S.watch.size && confirm("Remove every stock from this browser's watchlist?")) { S.watch.clear(); store.set(WATCH_KEY, []); updateWatchCount(); paintWatch(); } });
      paintWatch();
    });
  }
  function paintWatch() {
    const host = $("#mxWatchTable");
    const rows = [...S.watch].map(s => S.bySym.get(s)).filter(Boolean);
    const missing = [...S.watch].filter(s => !S.bySym.has(s));
    renderTable(host, rows, ["s", "c", "ch", "r1w", "r1m", "r3m", "dh", "rsi", ...(hasFund() ? ["mcap", "pe"] : ["rv"])], { sort: "ch", empty: "Your watchlist is empty. Add stocks above or with ☆ on any stock page.", after: h => addRemoveButtons(h, missing) });
  }
  function addRemoveButtons(host, missing) {
    $$("tbody tr", host).forEach((tr, i) => { const sym = host._rows[i]?.s; if (!sym) return; const td = document.createElement("td"); td.className = "num"; td.innerHTML = `<button class="icon-button" aria-label="Remove ${esc(sym)}">×</button>`; td.firstChild.addEventListener("click", () => { toggleWatch(sym); paintWatch(); }); tr.append(td); });
    const head = $("thead tr", host); if (head) head.insertAdjacentHTML("beforeend", "<th></th>");
    if (missing.length) host.insertAdjacentHTML("beforeend", `<p class="small-note">Not in the latest dataset: ${missing.map(esc).join(", ")}</p>`);
  }

  // ------------------------------------------------------------------ Momentum portfolio
  let MOM = null, momLoading = null;
  function loadMomentum() {
    if (MOM) return Promise.resolve(MOM);
    momLoading = momLoading || fetch(BASE + "momentum.json", { cache: "no-cache" })
      .then(r => { if (!r.ok) throw new Error(`momentum.json HTTP ${r.status}`); return r.json(); }).then(j => (MOM = j));
    return momLoading;
  }
  function planCapital() {
    try { const s = JSON.parse(localStorage.getItem("edgeNsePlanSettingsV1")); if (s && Number(s.capital) > 0) return Number(s.capital); } catch { /* default */ }
    return 1000000;
  }
  function nextRebalance(asOf, formedOn) {
    const d = new Date(asOf + "T00:00");
    let y = d.getFullYear(), m = d.getMonth();
    if (formedOn === asOf) m += 1;  // the portfolio was formed tonight: next one is next month-end
    const last = new Date(y, m + 1, 0);
    while (last.getDay() === 0 || last.getDay() === 6) last.setDate(last.getDate() - 1);
    return last.toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
  }
  function drawCurves(canvas, series) {
    const { ctx, w, h } = setupCanvas(canvas);
    const padL = 46, padR = 12, padT = 10, padB = 22;
    const all = series.flatMap(s => s.curve.map(p => p[1]));
    if (!all.length) return;
    const lo = Math.log(Math.min(1, ...all)), hi = Math.log(Math.max(...all));
    const n = series[0].curve.length;
    const x = i => padL + (w - padL - padR) * (n === 1 ? 0.5 : i / (n - 1));
    const y = v => padT + (hi - Math.log(v)) / ((hi - lo) || 1) * (h - padT - padB);
    const muted = css("--muted2") || "#6f7f9b";
    ctx.font = "10px Inter, system-ui, sans-serif"; ctx.textBaseline = "middle";
    const ticks = [0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16].filter(t => Math.log(t) >= lo - 0.05 && Math.log(t) <= hi + 0.05);
    ticks.forEach(t => { ctx.strokeStyle = "#25334c"; ctx.globalAlpha = t === 1 ? 0.9 : 0.45; ctx.beginPath(); ctx.moveTo(padL, y(t)); ctx.lineTo(w - padR, y(t)); ctx.stroke(); ctx.globalAlpha = 1; ctx.fillStyle = muted; ctx.fillText(`${t}×`, 6, y(t)); });
    let lastYear = "";
    series[0].curve.forEach((p, i) => { const yr = p[0].slice(0, 4); if (yr !== lastYear) { lastYear = yr; ctx.fillStyle = muted; ctx.fillText(yr, x(i) - 10, h - 8); } });
    series.forEach(s => { ctx.strokeStyle = s.color; ctx.lineWidth = s.width || 1.6; ctx.setLineDash(s.dash || []); ctx.beginPath();
      s.curve.forEach((p, i) => i ? ctx.lineTo(x(i), y(p[1])) : ctx.moveTo(x(i), y(p[1]))); ctx.stroke(); ctx.setLineDash([]); ctx.lineWidth = 1; });
  }
  function renderMomentum() {
    const host = $("#mxMomentumBody");
    host.innerHTML = `<div class="empty-state">Loading momentum portfolio…</div>`;
    Promise.all([loadMomentum(), load().catch(() => null)]).then(([m]) => {
      const p = m.portfolio || {}, ev = m.evidence || {}, V = ev.variants || [];
      const primary = V.find(v => v.id === "12-1-30"), uni = V.find(v => v.name.startsWith("Equal-weight")), nifty = V.find(v => v.name.startsWith("NIFTY"));
      const regime = V.find(v => v.id === "12-1-30-regime");
      const cap = planCapital(), per = cap / (m.rules?.top_n || 30);
      const live = id => S.bySym?.get(id);
      const sgn = x => isNum(x) ? `${x > 0 ? "+" : ""}${nf(x, 1)}%` : "—";
      const chip = s => `<button class="mx-sym mx-chip" data-sym="${esc(s)}">${esc(s)}</button>`;
      host.innerHTML = `
        <div class="notice notice-info"><span class="notice-symbol">i</span><div><strong>How to use this</strong>
          <p>On the last trading day of each month the portfolio is re-formed from that day's close. Buy the listed stocks in equal amounts at the next session's open and hold until the next month-end. ${esc(m.rules?.universe || "")}. Costs of ${nf(m.rules?.cost_per_side_pct, 3)}% per side are included in all results.</p></div></div>
        <div class="metric-grid">
          <article class="metric-card"><div class="metric-top"><span>PORTFOLIO FORMED</span><span class="metric-icon green">↗</span></div><div class="metric-value mx-small-value">${esc(p.formed_on || "—")}</div><div class="metric-foot"><span class="metric-muted">Effective from ${esc(p.effective_from || "next session")}</span><span class="metric-tag neutral">${p.universe || 0} stocks eligible</span></div></article>
          <article class="metric-card"><div class="metric-top"><span>NEXT REBALANCE</span><span class="metric-icon blue">◷</span></div><div class="metric-value mx-small-value">${esc(p.as_of ? nextRebalance(p.as_of, p.formed_on) : "—")}</div><div class="metric-foot"><span class="metric-muted">Last trading day of the month</span><span class="metric-tag neutral">Data ${esc(p.as_of || "")}</span></div></article>
          <article class="metric-card"><div class="metric-top"><span>BACKTEST CAGR</span><span class="metric-icon violet">▲</span></div><div class="metric-value">${primary ? nf(primary.cagr, 1) + "%" : "—"}</div><div class="metric-foot"><span class="metric-muted">vs ${uni ? nf(uni.cagr, 1) : "—"}% equal-weight, ${nifty ? nf(nifty.cagr, 1) : "—"}% NIFTY</span><span class="metric-tag neutral">${esc(ev.period?.[0] || "")}–${esc(ev.period?.[1] || "")}</span></div></article>
          <article class="metric-card"><div class="metric-top"><span>WORST DRAWDOWN</span><span class="metric-icon amber">▼</span></div><div class="metric-value">${primary ? nf(primary.mdd, 1) + "%" : "—"}</div><div class="metric-foot"><span class="metric-muted">Edge vs universe ${primary ? sgn(primary.excess) : "—"}/yr · t ${primary?.t ?? "—"}</span><span class="metric-tag ${primary && primary.t >= 2 ? "positive" : "neutral"}">${primary && primary.t >= 2 ? "Significant" : "Not yet significant"}</span></div></article>
        </div>
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Current portfolio · ${(p.picks || []).length} stocks</h2>
          <p>Equal weight of ${cr(per / 1e7)} each from your ₹${nf(cap, 0)} capital setting (change it in Overview). "Since entry" runs from the ${esc(p.effective_from || "")} open to the latest close.</p></div>
          <button class="button button-small button-secondary" id="mxMomCsv">↓ CSV</button></div>
          <div class="mx-mom-changes"><span><b>New this month:</b> ${(p.adds || []).map(chip).join(" ") || "none"}</span><span><b>Dropped:</b> ${(p.drops || []).map(esc).join(", ") || "none"}</span></div>
          <div class="table-wrap"><table class="mx-table"><thead><tr><th class="num">#</th><th>STOCK</th><th>INDUSTRY</th><th class="num">12-1 RETURN</th><th class="num">6-1</th><th class="num">1Y VOL</th><th class="num">CLOSE</th><th class="num">SINCE ENTRY</th><th class="num">RANK TODAY</th><th class="num">QTY</th></tr></thead>
          <tbody>${(p.picks || []).map(r => `<tr><td class="num">${r.rank}</td><td><button class="mx-sym" data-sym="${esc(r.s)}">${esc(r.s)}</button><small>${esc(r.n || live(r.s)?.n || "")}</small></td>
            <td>${esc(r.ind || live(r.s)?.ind || "")}</td><td class="num">${pct(r.score, 1)}</td><td class="num">${pct(r.r6_1, 1)}</td><td class="num">${isNum(r.vol) ? nf(r.vol, 0) + "%" : "—"}</td>
            <td class="num">${nf(r.close)}</td><td class="num">${pct(r.since)}</td><td class="num">${r.rank_today ? (r.rank_today <= 30 ? r.rank_today : `<span class="down">${r.rank_today}</span>`) : `<span class="down">out</span>`}</td>
            <td class="num">${r.close ? Math.floor(per / r.close).toLocaleString("en-IN") : "—"}</td></tr>`).join("")}</tbody></table></div>
          <p class="small-note">If rebalanced on today's close (watch only; act at month-end): would add ${(p.watch_today?.adds || []).map(esc).join(", ") || "none"}; would drop ${(p.watch_today?.drops || []).map(esc).join(", ") || "none"}.</p></section>
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Backtest: growth of ₹1</h2><p>${esc(ev.period?.[0] || "")} to ${esc(ev.period?.[1] || "")} · ${ev.months || 0} monthly rebalances · log scale · stocks that later delisted included</p></div>
          <div class="mx-legend"><span><i style="background:var(--green)"></i>12-1 top 30</span><span><i style="background:var(--amber)"></i>with regime filter</span><span><i style="background:var(--blue)"></i>Equal-weight universe</span><span><i class="dash"></i>NIFTY 50</span></div></div>
          <div class="mx-chart-wrap"><canvas id="mxMomChart" aria-label="Momentum backtest equity curves" role="img"></canvas></div></section>
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Strategy comparison</h2><p>Monthly returns after costs. Edge = annualised average monthly return above the equal-weight universe; t below 2 means the edge is not statistically established.</p></div></div>
          <div class="table-wrap"><table class="mx-table"><thead><tr><th>STRATEGY</th><th class="num">CAGR</th><th class="num">VOLATILITY</th><th class="num">SHARPE</th><th class="num">MAX DRAWDOWN</th><th class="num">EDGE VS UNIVERSE</th><th class="num">T-STAT</th><th class="num">MONTHS BEATEN</th><th class="num">TURNOVER/MO</th></tr></thead>
          <tbody>${V.map(v => `<tr class="${v.id === "12-1-30" ? "mx-selected" : ""}"><td>${esc(v.name)}</td><td class="num">${nf(v.cagr, 1)}%</td><td class="num">${nf(v.vol, 1)}%</td><td class="num">${nf(v.sharpe, 2)}</td>
            <td class="num down">${nf(v.mdd, 1)}%</td><td class="num">${isNum(v.excess) ? pct(v.excess, 1) : "—"}</td><td class="num">${isNum(v.t) ? nf(v.t, 2) : "—"}</td>
            <td class="num">${isNum(v.beat) ? nf(v.beat, 0) + "%" : "—"}</td><td class="num">${isNum(v.turnover) ? nf(v.turnover, 0) + "%" : "—"}</td></tr>`).join("")}</tbody></table></div>
          ${ev.robustness ? `<p class="small-note">Timing check: the same 12-1 top-30 rule rebalanced 10 sessions after month-end returned ${nf(ev.robustness.cagr, 1)}% a year vs ${nf(ev.robustness.universe_cagr, 1)}% for the universe (edge ${pct(ev.robustness.excess, 1)}/yr, t ${nf(ev.robustness.t, 2)}).</p>` : ""}
          <h3 class="mx-subhead">Calendar-year returns</h3>
          <div class="table-wrap"><table class="mx-table"><thead><tr><th>STRATEGY</th>${Object.keys(primary?.by_year || {}).map(yr => `<th class="num">${esc(yr)}</th>`).join("")}</tr></thead>
          <tbody>${V.map(v => `<tr><td>${esc(v.name)}</td>${Object.keys(primary?.by_year || {}).map(yr => `<td class="num">${pct(v.by_year?.[yr], 1)}</td>`).join("")}</tr>`).join("")}</tbody></table></div></section>
        <section class="panel mx-panel"><div class="panel-toolbar"><div><h2>Paper-trading record</h2><p>Each month's portfolio is frozen to the repository on the day it is formed (from ${esc(m.rules?.paper_start || "")}) and never recomputed, so this is genuine out-of-sample evidence.</p></div></div>
          ${(m.paper || []).length ? `<div class="table-wrap"><table class="mx-table"><thead><tr><th>FORMED</th><th>STATUS</th><th class="num">PORTFOLIO</th><th class="num">EQUAL-WEIGHT UNIVERSE</th><th class="num">NIFTY 50</th><th class="num">VS UNIVERSE</th></tr></thead>
          <tbody>${m.paper.map(r => `<tr><td>${esc(r.formed_on)}</td><td>${r.complete ? "Closed" : "Open, through " + esc(r.through)}</td><td class="num">${pct(r.portfolio)}</td><td class="num">${pct(r.universe)}</td><td class="num">${pct(r.nifty)}</td>
            <td class="num">${isNum(r.portfolio) && isNum(r.universe) ? pct(r.portfolio - r.universe) : "—"}</td></tr>`).join("")}</tbody></table></div>`
            : `<div class="empty-state">The first frozen portfolio will be recorded at the ${esc(p.as_of ? nextRebalance(p.as_of, p.formed_on) : "next month-end")} close. Results appear here from then on.</div>`}</section>
        <div class="notice notice-warning"><span class="notice-symbol">!</span><div><strong>Before putting money into this</strong>
          <p>The edge over the equal-weight universe has a t-statistic of ${primary?.t ?? "—"} (below 2 = not statistically established). Expect drawdowns around ${primary ? nf(primary.mdd, 0) : "—"}% and losing years. Monthly turnover makes most gains short-term capital gains (20%) rather than long-term (12.5%), which narrows the after-tax edge. Results exclude dividends and assume delisted stocks could be sold at their last price. Paper trade it for several months first; this is research, not advice.</p></div></div>`;
      $("#mxMomCsv").addEventListener("click", () => {
        const head = ["rank", "symbol", "name", "industry", "score_12_1_pct", "r6_1_pct", "vol_pct", "close", "qty"];
        const csv = [head.join(","), ...(p.picks || []).map(r => [r.rank, r.s, `"${(r.n || "").replace(/"/g, '""')}"`, `"${(r.ind || "").replace(/"/g, '""')}"`, r.score, r.r6_1 ?? "", r.vol ?? "", r.close, Math.floor(per / r.close)].join(","))].join("\n");
        const a = Object.assign(document.createElement("a"), { href: URL.createObjectURL(new Blob([csv], { type: "text/csv" })), download: `momentum-${p.formed_on}.csv` });
        document.body.append(a); a.click(); a.remove();
      });
      const curves = [nifty && { curve: nifty.curve, color: "#91a0ba", dash: [4, 3] }, uni && { curve: uni.curve, color: css("--blue") || "#7aa7ff" },
        regime && { curve: regime.curve, color: css("--amber") || "#f5bd68" }, primary && { curve: primary.curve, color: css("--green") || "#47e0ac", width: 2.2 }].filter(Boolean);
      S.momCurves = curves;
      if (curves.length) drawCurves($("#mxMomChart"), curves);
    }).catch(err => {
      host.innerHTML = `<div class="notice notice-warning"><span class="notice-symbol">!</span><div><strong>Momentum data not published yet</strong><p>Could not load <code>${BASE}momentum.json</code> (${esc(err.message)}). It is built by the daily workflow from the multi-year archive.</p></div></div>`;
    });
  }

  // ------------------------------------------------------------------ wiring
  const RENDER = { momentum: renderMomentum, markets: renderMarkets, stock: renderStock, scans: renderScans, sectors: renderSectors, watchlist: renderWatchlist };
  function init() {
    updateWatchCount();
    $$(".nav-item").forEach(b => b.addEventListener("click", () => {
      const fn = RENDER[b.dataset.view];
      document.title = "EDGE NSE — Trading Research Terminal";
      if (b.dataset.view !== "stock" && location.hash.startsWith("#stock/")) history.replaceState(null, "", location.pathname + location.search);
      if (fn) fn();
    }));
    const global = $("#globalSearch");
    if (global) attachSearch(global, openStock);
    if ($("#mxStockSearch")) attachSearch($("#mxStockSearch"), openStock);
    let t; window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(() => {
      if ($("#view-stock.active") && S.lastBars && $("#mxCandles")) drawCandles($("#mxCandles"), S.lastBars, $("#mxReadout"));
      if ($("#view-markets.active") && $("#mxBreadthChart")) drawBreadth($("#mxBreadthChart"));
      if ($("#view-momentum.active") && $("#mxMomChart") && S.momCurves) drawCurves($("#mxMomChart"), S.momCurves);
    }, 150); });
    load().catch(() => {});
    routeFromHash();
  }
  document.readyState === "loading" ? document.addEventListener("DOMContentLoaded", init) : init();
})();
