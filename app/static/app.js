/* ETF Me in the ETN — front end. Vanilla JS + hand-rolled SVG charts, zero dependencies. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const PAGE = 200;
const state = { cfg: null, funds: [], filter: { lev: "", type: "", group: "", country: "", direction: "", age: "", tier: "", q: "" }, sort: { key: "drawdown", asc: true }, range: "5Y", log: true, cur: "USD", limit: PAGE, watch: new Set(), starOnly: false };

const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: { "Content-Type": "application/json", ...(opts.headers || {}) }, ...opts });
  if (!r.ok) throw new Error((await r.text()) || r.statusText);
  return r.json();
};
const tierOf = (k) => state.cfg.tiers.find((t) => t.key === k) || state.cfg.tiers[0];
const tc = (k) => `var(--t-${k})`;
const SYM = { USD: "$", CAD: "C$", AUD: "A$", HKD: "HK$", TWD: "NT$", SGD: "S$", GBP: "£", EUR: "€", JPY: "¥", CNY: "¥", KRW: "₩", INR: "₹", CHF: "CHF ", SEK: "kr ", NOK: "kr ", DKK: "kr ", ILS: "₪", ZAR: "R ", BRL: "R$" };
const money = (v, cur = state.cur) => {
  if (v == null) return "—";
  const sym = SYM[cur] ?? `${cur} `, big = v >= 1000 || cur === "KRW" || cur === "JPY";
  return sym + (big ? v.toLocaleString(undefined, { maximumFractionDigits: v >= 1000 ? 0 : 2 }) : v.toFixed(2));
};
const levTag = (x) => `<span class="lev ${x.direction === "short" ? "bear" : x.type === "ETN" ? "etn" : x.type === "ETP" ? "etp" : ""}">${x.lev}x ${x.direction === "short" ? "Bear " : ""}${x.type}</span>${x.country && x.country !== "US" ? `<span class="cc" title="${esc(x.exchange)}">${x.country}</span>` : ""}`;
const isNew = (x, days) => x.new_listing && x.first_seen && Date.now() - new Date(x.first_seen) < days * 86400e3;
const pct = (v, d = 1) => (v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(d)}%`);
const cls = (v) => (v == null ? "" : v >= 0 ? "up" : "down");
const ago = (iso) => {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return "just now"; if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`; return `${Math.round(s / 86400)} d ago`;
};

/* ------------------------------------------------------------------ boot */
async function boot() {
  state.cfg = await api("/api/config");
  $("#demo").hidden = !state.cfg.demo_mode;
  $("#ver").textContent = `v${state.cfg.version} · data: ${state.cfg.demo_mode ? "demo" : state.cfg.providers.join(" → ")}`;
  $("#refresh").onclick = async () => { await api("/api/refresh", { method: "POST" }).catch(alert); $("#updated").textContent = "refreshing…"; setTimeout(loadBoard, 4000); };
  $("#search").oninput = (e) => { state.filter.q = e.target.value.trim().toUpperCase(); state.limit = PAGE; renderBoard(); };
  $$(".controls select").forEach((sel) => (sel.onchange = (e) => { state.filter[sel.dataset.key] = e.target.value; state.limit = PAGE; renderBoard(); }));
  $("#more").onclick = () => { state.limit += PAGE * 2; renderBoard(); };
  $("#star-filter").onclick = (e) => { state.starOnly = !state.starOnly; e.currentTarget.classList.toggle("on", state.starOnly); e.currentTarget.textContent = `${state.starOnly ? "★" : "☆"} Watchlist`; state.limit = PAGE; renderBoard(); };
  $$(".seg").forEach((seg) => {
    const key = seg.dataset.key;
    seg.querySelector("button").classList.add("on");
    seg.onclick = (e) => {
      if (!e.target.dataset) return;
      $$("button", seg).forEach((b) => b.classList.toggle("on", b === e.target));
      state.filter[key] = e.target.dataset.v; state.limit = PAGE; renderBoard();
    };
  });
  $$("#board th[data-sort]").forEach((th) => (th.onclick = () => {
    const k = th.dataset.sort;
    state.sort = { key: k, asc: state.sort.key === k ? !state.sort.asc : k === "ticker" || k === "drawdown" || k === "change_1d" };
    renderBoard();
  }));
  window.onhashchange = route;
  await loadBoard();
  route();
  setInterval(loadBoard, 60_000);
}

async function loadBoard() {
  const [b, alerts, st] = await Promise.all([api("/api/funds"), api("/api/alerts?limit=60"), api("/api/status").catch(() => null)]);
  state.funds = b.funds;
  state.watch = new Set(b.watchlist || []);
  if (st) renderConn(st);
  fillFilters();
  const markets = new Set(state.funds.map((x) => x.country)).size;
  $("#count").textContent = `Tracking ${state.funds.length.toLocaleString()} funds in ${markets} market${markets === 1 ? "" : "s"}.`;
  $("#updated").textContent = b.refreshing ? "refreshing…" : `updated ${ago(b.last_refresh)}`;
  renderLadder(); renderBoard(); renderFeed(alerts);
}

function route() {
  const m = location.hash.match(/^#\/([A-Za-z0-9.\-]+)/);
  $("#board-view").hidden = !!m; $("#detail-view").hidden = !m;
  if (m) loadDetail(m[1].toUpperCase()); else window.scrollTo(0, 0);
}

/* ------------------------------------------------------------------ board */
function fillFilters() {
  const opts = (key, label, fmt = (v) => v, sort) => {
    const sel = $(`#${key}`), counts = {};
    state.funds.forEach((x) => { const v = String(x[key] ?? ""); if (v) counts[v] = (counts[v] || 0) + 1; });
    const keys = Object.keys(counts).sort(sort);
    sel.innerHTML = `<option value="">${label}</option>` + keys.map((k) => `<option value="${esc(k)}">${esc(fmt(k))} (${counts[k]})</option>`).join("");
    sel.value = keys.includes(state.filter[key]) ? state.filter[key] : "";
  };
  opts("lev", "Any leverage", (v) => `${v}x`, (a, b) => b - a);
  opts("group", "All groups");
  opts("country", "All markets");
}

function renderLadder() {
  const counts = Object.fromEntries(state.cfg.tiers.map((t) => [t.key, 0]));
  state.funds.forEach((f) => f.has_data && counts[f.tier]++);
  $("#ladder").innerHTML = state.cfg.tiers.map((t) => `
    <div class="rung ${state.filter.tier === t.key ? "active" : ""}" style="--c:${tc(t.key)}" data-k="${t.key}">
      <div class="count">${counts[t.key]}</div>
      <div class="lbl">${t.emoji} <span>${esc(t.label)}</span></div>
      <div class="sub">${t.min_dd ? `≥ ${t.min_dd}% off peak` : "within 20% of peak"}</div>
    </div>`).join("");
  $$(".rung").forEach((r) => (r.onclick = () => { state.filter.tier = state.filter.tier === r.dataset.k ? "" : r.dataset.k; renderLadder(); renderBoard(); }));
}

function renderBoard() {
  const f = state.filter;
  let rows = state.funds.filter((x) =>
    (!f.lev || String(x.lev) === f.lev) && (!f.type || x.type === f.type) && (!f.group || x.group === f.group) &&
    (!state.starOnly || state.watch.has(x.ticker)) && (!f.country || x.country === f.country) && (!f.direction || x.direction === f.direction) && (!f.age || isNew(x, +f.age)) &&
    (!f.tier || x.tier === f.tier) && (!f.q || x.ticker.includes(f.q) || (x.under || "").toUpperCase().includes(f.q) ||
      (x.full_name || "").toUpperCase().includes(f.q) || (x.issuer || "").toUpperCase().includes(f.q)));
  const { key, asc } = state.sort;
  rows.sort((a, b) => {
    if (!a.has_data !== !b.has_data) return a.has_data ? -1 : 1;
    const va = a[key] ?? (asc ? Infinity : -Infinity), vb = b[key] ?? (asc ? Infinity : -Infinity);
    return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * (asc ? 1 : -1);
  });
  $$("#board th").forEach((th) => { th.classList.toggle("sorted", th.dataset.sort === key); th.classList.toggle("asc", asc); });
  const total = rows.length;
  rows = rows.slice(0, state.limit);
  $("#shown").textContent = total > rows.length ? `Showing ${rows.length.toLocaleString()} of ${total.toLocaleString()}` : `${total.toLocaleString()} funds`;
  $("#more").hidden = total <= rows.length;
  $("#board tbody").innerHTML = rows.map((x) => {
    const m = (v) => money(v, x.currency);
    const head = `<td>${starTag(x.ticker)}<span class="tk">${x.ticker}</span>${levTag(x)}${isNew(x, 30) ? `<span class="newb">NEW</span>` : ""}<div class="under" title="${esc(x.full_name)}">${esc(x.under)}</div></td>`;
    if (!x.has_data) return `<tr class="nodata" data-t="${x.ticker}">${head}<td colspan="8">no data yet ${x.error ? `— <span title="${esc(x.error)}">provider error</span>` : ""}</td></tr>`;
    const t = tierOf(x.tier), nt = x.next_tier && tierOf(x.next_tier);
    return `<tr data-t="${x.ticker}" style="--c:${tc(x.tier)}">${head}
      <td><span class="badge">${t.emoji} <span class="bl">${esc(t.label)}</span></span></td>
      <td class="num">${m(x.price)}</td>
      <td class="num ${cls(x.change_1d)}">${pct(x.change_1d, 2)}</td>
      <td class="num">${m(x.ath)}<div class="under">${x.ath_date}</div></td>
      <td class="num"><span class="dd-bar"><i style="width:${Math.min(100, -x.drawdown)}%"></i></span><b class="down">${pct(x.drawdown)}</b></td>
      <td class="num">${nt ? `${nt.emoji} ${m(x.next_tier_price)}<div class="under">${pct(x.pct_to_next_tier)} away</div>` : "🚀 bottom rung"}</td>
      <td class="num ${cls(x.total_return)}">${pct(x.total_return, 0)}<div class="under">${x.since_label}${x.cagr != null ? ` · ${x.cagr.toFixed(0)}%/yr` : ""}</div></td>
      <td>${sparkline(x.spark, tc(x.tier))}</td></tr>`;
  }).join("") || `<tr><td colspan="9" class="empty">Nothing matches. Your filters are too picky.</td></tr>`;
  $$("#board tbody tr[data-t]").forEach((tr) => (tr.onclick = () => (location.hash = `#/${tr.dataset.t}`)));
  bindStars($("#board"));
}

/* ------------------------------------------------------------------ watchlist */
const starTag = (t) => `<span class="star ${state.watch.has(t) ? "on" : ""}" data-star="${t}" title="${state.watch.has(t) ? "Unstar" : "Star: always push this fund's alerts"}">${state.watch.has(t) ? "★" : "☆"}</span>`;
function bindStars(root) {
  $$("[data-star]", root).forEach((el) => (el.onclick = async (e) => {
    e.stopPropagation();
    const t = el.dataset.star, on = !state.watch.has(t);
    try { state.watch = new Set(await api(`/api/watchlist/${t}`, { method: on ? "PUT" : "DELETE" })); } catch (er) { return alert(er.message); }
    $$(`[data-star="${t}"]`).forEach((s) => { s.classList.toggle("on", on); s.textContent = on ? "★" : "☆"; });
    if (state.starOnly) renderBoard();
  }));
}

/* ------------------------------------------------------------------ connections */
function renderConn(st) {
  const d = st.discovery || {}, last = d.last_run, r = st.refresh;
  const prov = st.providers.filter((p) => p.enabled).map((p) => {
    const s = st.demo_mode ? `<span class="off">demo</span>` : !p.configured ? `<span class="warn" title="Set its API key in .env">no key</span>`
      : `<span class="ok">✓ ${p.funds ? p.funds.toLocaleString() + " funds" : "ready"}</span>`;
    return `<li><span>${esc(p.name)} <span class="muted small">${esc(p.markets)}</span></span>${s}</li>`;
  }).join("");
  const src = st.discovery_sources.filter((s) => s.enabled).map((s) => {
    const err = last?.errors?.[s.name];
    const n = last?.raw_counts?.[s.name];
    const v = st.demo_mode ? `<span class="off">off in demo</span>` : !s.configured ? `<span class="warn">no key</span>`
      : err && !err.startsWith("skipped") ? `<span class="warn" title="${esc(err)}">⚠ error</span>`
      : n != null ? `<span class="ok">✓ ${n.toLocaleString()} listings</span>` : `<span class="off">pending</span>`;
    return `<li><span>${esc(s.name)}${s.complete ? "" : ` <span class="muted small">search</span>`}</span>${v}</li>`;
  }).join("");
  const news = (last?.new_listings || []).slice(0, 8).map((t) => `<a href="#/${t}">${esc(t)}</a>`).join(", ");
  $("#conn").innerHTML = `<h3>📡 Data connections</h3>
    <div class="muted small">${r.with_data.toLocaleString()} of ${r.funds.toLocaleString()} funds have prices${r.errors ? ` · ${r.errors} with errors` : ""} · refresh every ${r.every_minutes} min</div>
    <h4>Prices (tried in order)</h4><ul>${prov}</ul>
    <h4>Fund discovery ${d.enabled ? `· every ${d.every_hours}h` : ""}</h4><ul>${src}</ul>
    <div class="muted small" style="margin-top:8px">${last ? `Last scan ${ago(last.ts)}: ${last.found.toLocaleString()} leveraged funds found${last.added ? `, ${last.added} added` : ""}${last.delisted?.length ? `, ${last.delisted.length} delisted` : ""}.` : d.enabled ? (d.running ? "Scanning…" : "First scan pending…") : "Discovery is off (demo mode)."}
    ${news ? `<br>🆕 New: ${news}` : ""}</div>
    ${d.enabled ? `<button class="btn ghost" id="scan" style="margin-top:10px;width:100%" ${d.running ? "disabled" : ""}>${d.running ? "Scanning…" : "🔎 Scan for new funds now"}</button>` : ""}`;
  const b = $("#scan");
  if (b) b.onclick = async () => { b.disabled = true; b.textContent = "Scanning…"; await api("/api/discovery/run", { method: "POST" }).catch((e) => alert(e.message)); setTimeout(loadBoard, 15000); };
}

function renderFeed(alerts) {
  $("#feed").innerHTML = alerts.map((a) => `
    <li style="--c:${tc(a.severity)}"><a href="#/${a.ticker}" style="text-decoration:none">
      <b>${esc(a.title)}</b><div class="m">${esc(a.message)}</div><div class="ts">${a.ts.slice(0, 16).replace("T", " ")} UTC</div></a></li>`).join("")
    || `<li class="empty">Quiet… too quiet. No alerts yet.</li>`;
}

/* ------------------------------------------------------------------ charts */
function sparkline(vals, color, w = 120, h = 32) {
  if (!vals || vals.length < 2) return "";
  const mn = Math.min(...vals), mx = Math.max(...vals), sx = w / (vals.length - 1), sy = (v) => h - 2 - ((v - mn) / (mx - mn || 1)) * (h - 4);
  const d = vals.map((v, i) => `${i ? "L" : "M"}${(i * sx).toFixed(1)},${sy(v).toFixed(1)}`).join("");
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="${d}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
}

const niceTicks = (mn, mx, n, log) => {
  if (log) {
    const out = [], lo = Math.floor(Math.log10(mn)), hi = Math.ceil(Math.log10(mx));
    for (let e = lo; e <= hi; e++) for (const m of [1, 2, 5]) { const v = m * 10 ** e; if (v >= mn && v <= mx) out.push(v); }
    return out.length >= 2 ? out : [mn, mx];
  }
  const step0 = (mx - mn) / n, mag = 10 ** Math.floor(Math.log10(step0)), step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = []; for (let v = Math.ceil(mn / step) * step; v <= mx; v += step) out.push(+v.toFixed(10)); return out;
};

/** Generic line chart. series: [{y:[], color, width, fill}], hlines: [{y, color, label, dash}] */
function lineChart({ x, series, hlines = [], log = false, height = 320, width = 1000, yFmt = money, bands = [], zeroLine = false }) {
  const W = Math.max(280, Math.round(width)), H = height, L = 8, R = 64, T = 10, B = 24;
  const all = series.flatMap((s) => s.y).concat(bands.flatMap((b) => [...b.lo, ...b.hi])).concat(hlines.map((h) => h.y)).filter((v) => v != null && isFinite(v) && (!log || v > 0));
  let mn = Math.min(...all), mx = Math.max(...all);
  if (!log) { const pad = (mx - mn) * 0.05 || 1; mn -= zeroLine ? 0 : pad; mx += pad; if (zeroLine) mx = Math.max(mx, 0); }
  const f = log ? Math.log : (v) => v, a = f(mn * (log ? 0.92 : 1)), b = f(mx * (log ? 1.06 : 1));
  const n = x.length, px = (i) => L + (i / Math.max(1, n - 1)) * (W - L - R), py = (v) => T + (1 - (f(v) - a) / (b - a)) * (H - T - B);
  const path = (ys) => ys.map((v, i) => (v == null ? "" : `${i && ys[i - 1] != null ? "L" : "M"}${px(i).toFixed(1)},${py(v).toFixed(1)}`)).join("");
  let g = "";
  for (const t of niceTicks(log ? mn * 0.92 : mn, log ? mx * 1.06 : mx, 5, log)) g += `<line x1="${L}" x2="${W - R}" y1="${py(t)}" y2="${py(t)}" stroke="#222831"/><text x="${W - R + 6}" y="${py(t) + 4}">${yFmt(t)}</text>`;
  const years = {}; if (typeof x[0] === "number") { const st = Math.ceil(n / 6); x.forEach((d, i) => { if (i % st === 0 && i) g += `<text x="${px(i) + 3}" y="${H - 6}">day ${d}</text>`; }); } else x.forEach((d, i) => { const yr = String(d).slice(0, 4); if (!(yr in years)) years[yr] = i; });
  const ys = Object.entries(years), every = Math.ceil(ys.length / 10);
  ys.forEach(([yr, i], k) => { if (k % every === 0 && i > 0) g += `<line x1="${px(i)}" x2="${px(i)}" y1="${T}" y2="${H - B}" stroke="#1d2229"/><text x="${px(i) + 3}" y="${H - 6}">${yr}</text>`; });
  for (const bd of bands) {
    const top = bd.hi.map((v, i) => `${i ? "L" : "M"}${px(i)},${py(v)}`).join(""), bot = bd.lo.map((v, i) => `L${px(bd.lo.length - 1 - i)},${py(bd.lo[bd.lo.length - 1 - i])}`).join("");
    g += `<path d="${top}${bot}Z" fill="${bd.color}" opacity="${bd.opacity ?? 0.18}"/>`;
  }
  for (const s of series) {
    if (s.fill) g += `<path d="${path(s.y)}L${px(n - 1)},${py(zeroLine ? 0 : mn)}L${px(0)},${py(zeroLine ? 0 : mn)}Z" fill="${s.color}" opacity=".18"/>`;
    g += `<path d="${path(s.y)}" fill="none" stroke="${s.color}" stroke-width="${s.width || 1.6}" ${s.dash ? `stroke-dasharray="${s.dash}"` : ""}/>`;
  }
  for (const h of hlines) {
    if (h.y < mn * (log ? 0.92 : 1) || h.y > mx * (log ? 1.06 : 1)) continue;
    g += `<line x1="${L}" x2="${W - R}" y1="${py(h.y)}" y2="${py(h.y)}" stroke="${h.color}" stroke-dasharray="${h.dash || "5 4"}" stroke-width="1.2" opacity=".9"/>`;
    if (h.label) g += `<text x="${L + 6}" y="${py(h.y) - 4}" style="fill:${h.color};font-weight:700">${esc(h.label)}</text>`;
  }
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" style="height:${H}px">${g}</svg>`;
}

/* ------------------------------------------------------------------ detail */
let current = null;
async function loadDetail(t) {
  $("#detail").innerHTML = `<div class="empty">Loading ${esc(t)}…</div>`;
  try { current = await api(`/api/funds/${t}`); } catch (e) { $("#detail").innerHTML = `<div class="empty">Couldn't load ${esc(t)}: ${esc(e.message)}</div>`; return; }
  renderDetail(); bindStars($("#detail")); window.scrollTo(0, 0);
}

function renderDetail() {
  const d = current, s = d.summary;
  state.cur = s.currency || "USD";
  if (!s.has_data) { $("#detail").innerHTML = `<div class="panel empty">No price data for ${s.ticker} yet. ${esc(s.error || "")}</div>`; return; }
  const t = tierOf(s.tier), nt = s.next_tier && tierOf(s.next_tier);
  const ladder = state.cfg.tiers.filter((x) => x.min_dd > 0).map((x) => ({ ...x, price: s.ath * (1 - x.min_dd / 100) }));
  $("#detail").innerHTML = `
    <div class="hero" style="--c:${tc(s.tier)}">
      <div><h2>${starTag(s.ticker)}${s.ticker} ${levTag(s)}</h2>
        <div class="muted">${esc(s.full_name || s.under)} · ${esc(s.issuer)} · ${esc(s.exchange)} (${s.currency}) · data since ${s.first_date}${s.provider ? ` · via ${esc(s.provider)}` : ""}</div></div>
      <div class="num"><div class="price">${money(s.price)}</div><div class="${cls(s.change_1d)}">${pct(s.change_1d, 2)} today · as of ${s.as_of}</div></div>
    </div>
    <div class="tierbox" style="--c:${tc(s.tier)}"><div class="emo">${t.emoji}</div>
      <div><b>${esc(t.label)}</b> — ${pct(s.drawdown)} from the ${s.peak_basis} of ${money(s.ath)} (${s.ath_date}).<br>
      <span class="muted">${esc(t.blurb)} ${nt ? `Next rung: <b style="color:${tc(nt.key)};font-size:inherit">${nt.emoji} ${esc(nt.label)}</b> at ${money(s.next_tier_price)} (${pct(s.pct_to_next_tier)} away).` : "This is the bottom rung. Godspeed."}</span></div></div>
    <div class="stats">
      ${stat("Peak (" + s.peak_basis + ")", money(s.ath), s.ath_date)}
      ${stat("Drawdown", `<span class="down">${pct(s.drawdown)}</span>`, `worst since peak ${pct(s.max_drawdown_since_ath)}`)}
      ${stat("Low since peak", money(s.low_since_ath), `bounce off low ${pct((s.price / s.low_since_ath - 1) * 100)}`)}
      ${stat("Total return " + s.since_label, `<span class="${cls(s.total_return)}">${pct(s.total_return, 0)}</span>`, s.cagr != null ? `${pct(s.cagr)} per year, crashes included` : "")}
      ${stat("1 month / YTD", `<span class="${cls(s.change_1m)}">${pct(s.change_1m)}</span>`, `YTD ${pct(s.change_ytd)}`)}
      ${stat("To double from here", money(s.price * 2), `peak is ${pct((s.ath / s.price - 1) * 100, 0)} away`)}
    </div>

    <div class="panel">
      <div class="chart-head"><div><h3>Price & the crash ladder</h3><p class="hint">Dashed lines = alert levels off the peak, plus your own targets.</p></div>
        <div style="display:flex;gap:8px"><div class="seg" id="rng">${["1Y", "3Y", "5Y", "10Y", "MAX"].map((r) => `<button data-v="${r}" class="${state.range === r ? "on" : ""}">${r}</button>`).join("")}</div>
        <div class="seg" id="scale"><button data-v="log" class="${state.log ? "on" : ""}">Log</button><button data-v="lin" class="${state.log ? "" : "on"}">Linear</button></div></div></div>
      <div id="pchart"></div><div id="ddchart" style="margin-top:6px"></div>
      <div class="legend">${ladder.map((x) => `<span><i style="background:${tc(x.key)}"></i>${x.emoji} ${esc(x.label)} ${money(x.price)}</span>`).join("")}
        ${d.targets.map((x) => `<span><i style="background:#fff"></i>🎯 ${money(x.price)} ${esc(x.label)}</span>`).join("")}</div>
    </div>

    <div class="grid2">
      <div class="panel"><h3>💩 Every time it pooped 50%+</h3><p class="hint">“Buy at −50%” = buying the first close that was 50% under the prior peak. “From bottom” is hindsight.</p>
        ${episodesTable(d.episodes)}</div>
      <div class="panel"><h3>🧾 Alert-level scorecard</h3><p class="hint">If you bought the first day each level was hit, what happened next? (total return, dividends incl.)</p>
        ${scorecard(d.scorecard)}</div>
    </div>

    <div class="grid2">
      <div class="panel"><h3>🔮 Monte Carlo — next ${d.forecast.horizon_days || 252} trading days</h3>
        <p class="hint">${d.forecast.available ? `${d.forecast.paths.toLocaleString()} paths, ${esc(d.forecast.method)}. Decay & fat tails included. Not a promise.` : esc(d.forecast.reason)}</p>
        ${d.forecast.available ? forecastBlock(d.forecast) : ""}</div>
      <div class="panel"><h3>🎯 Your price targets</h3><p class="hint">Get pinged when price crosses these. Also scored by the Monte Carlo.</p>
        ${targetsBlock(d)}</div>
    </div>

    <div class="grid2">
      <div class="panel"><h3>🤖 AI agents' take</h3><p class="hint">Forecasts posted by agents via <code>POST /api/agent/forecasts</code>${state.cfg.llm.enabled ? ` or asked of the local model (${esc(state.cfg.llm.model)})` : ""}.</p>
        ${askBlock()}<div id="agents">${agentsList(d.agent_forecasts)}</div></div>
      <div class="panel"><h3>📣 ${s.ticker} alert history</h3>${d.alerts.length ? `<ul id="feed" style="list-style:none;padding:0;margin:0">${d.alerts.map((a) => `<li class="agent"><b>${esc(a.title)}</b><div class="muted small">${a.ts.slice(0, 10)} · ${esc(a.message)}</div></li>`).join("")}</ul>` : `<div class="empty">No alerts yet.</div>`}</div>
    </div>`;
  drawPrice(); drawForecast(); wireDetail();
}

const stat = (k, v, s = "") => `<div class="stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;

function drawPrice() {
  const d = current, s = d.summary, n = { "1Y": 252, "3Y": 756, "5Y": 1260, "10Y": 2520, MAX: 1e9 }[state.range];
  const st = Math.max(0, d.history.dates.length - n), x = d.history.dates.slice(st), y = d.history.close.slice(st);
  const hl = state.cfg.tiers.filter((t) => t.min_dd > 0).map((t) => ({ y: s.ath * (1 - t.min_dd / 100), color: tc(t.key), label: `${t.emoji} -${t.min_dd}%` }));
  hl.push({ y: s.ath, color: "#5a6372", label: "peak", dash: "2 3" });
  d.targets.forEach((t) => hl.push({ y: t.price, color: "#ffffff", label: `🎯 ${money(t.price)}`, dash: "8 3" }));
  const w = $("#pchart").clientWidth;
  $("#pchart").innerHTML = lineChart({ width: w, x, series: [{ y, color: tc(s.tier), width: 1.8 }], hlines: hl, log: state.log, height: 340 });
  // drawdown from running peak
  let pk = 0; const dd = y.map((v) => ((pk = Math.max(pk, v)), (v / pk - 1) * 100));
  $("#ddchart").innerHTML = lineChart({ width: w, x, series: [{ y: dd, color: "#ff5c5c", fill: true, width: 1.2 }], height: 120, yFmt: (v) => `${v.toFixed(0)}%`, zeroLine: true,
    hlines: state.cfg.tiers.filter((t) => t.min_dd > 0).map((t) => ({ y: -t.min_dd, color: tc(t.key) })) });
}

function episodesTable(eps) {
  if (!eps.length) return `<div class="empty">Never fell 50% from a peak in the data we have. Lucky.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Peak</th><th class="num">Max fall</th><th>Bottom</th><th class="num">Buy at −50%: 1y</th><th class="num">2y</th><th class="num">From bottom: 1y</th><th>Recovered</th></tr></thead><tbody>
    ${eps.slice().reverse().map((e) => `<tr style="cursor:default"><td>${e.peak_date}<div class="under">${money(e.peak)}</div></td>
      <td class="num down"><b>${pct(e.max_drawdown)}</b></td><td>${e.trough_date}<div class="under">${money(e.trough)}</div></td>
      <td class="num ${cls(e.ret_1y_from_cross)}">${pct(e.ret_1y_from_cross, 0)}</td><td class="num ${cls(e.ret_2y_from_cross)}">${pct(e.ret_2y_from_cross, 0)}</td>
      <td class="num ${cls(e.ret_1y_from_trough)}">${pct(e.ret_1y_from_trough, 0)}</td>
      <td>${e.ongoing ? `<span class="badge" style="--c:var(--t-shock)">in progress</span>` : `${e.recovered_date}<div class="under">${e.trading_days_to_recover} trading days after bottom</div>`}</td></tr>`).join("")}
  </tbody></table></div>`;
}

function scorecard(rows) {
  return `<table><thead><tr><th>Level</th><th class="num">Times</th><th class="num">Median 1y</th><th class="num">Median 2y</th><th class="num">1y win rate</th><th class="num">Worst 1y</th></tr></thead><tbody>
    ${rows.map((r) => { const t = tierOf(r.tier); return `<tr style="cursor:default;--c:${tc(r.tier)}"><td><span class="badge">${t.emoji} ${esc(t.label)}</span></td><td class="num">${r.events}</td>
      <td class="num ${cls(r.median_1y)}">${pct(r.median_1y, 0)}</td><td class="num ${cls(r.median_2y)}">${pct(r.median_2y, 0)}</td>
      <td class="num">${r.win_rate_1y == null ? "—" : r.win_rate_1y + "%"}</td><td class="num ${cls(r.worst_1y)}">${pct(r.worst_1y, 0)}</td></tr>`; }).join("")}</tbody></table>`;
}

function forecastBlock(f) {
  return `<div id="fchart"></div><div class="legend"><span><i style="background:#ffb020"></i>median</span><span>bands: 25–75% and 5–95%</span></div>
    <div class="probs">
      ${stat("Median in 1y", money(f.terminal.p50), `5–95%: ${money(f.terminal.p5)} – ${money(f.terminal.p95)}`)}
      ${stat("P(higher in 1y)", f.prob_up + "%")}
      ${stat("P(touch old peak)", f.prob_reclaim_ath + "%")}
      ${stat("P(doubles at some point)", f.prob_double + "%")}
      ${stat("P(another −30% first)", f.prob_drop_another_30 + "%")}
      ${(f.targets || []).map((t) => stat(`P(touch 🎯 ${money(t.price)})`, t.prob_touch + "%", esc(t.label))).join("")}
    </div>`;
}

function drawForecast() {
  const f = current.forecast, el = $("#fchart");
  if (!el) return;
  el.innerHTML = lineChart({ width: el.clientWidth, x: f.fan_days, series: [{ y: f.fan.p50, color: "#ffb020", width: 2 }], log: true, height: 200,
    bands: [{ lo: f.fan.p5, hi: f.fan.p95, color: "#ffb020", opacity: 0.12 }, { lo: f.fan.p25, hi: f.fan.p75, color: "#ffb020", opacity: 0.22 }],
    hlines: [{ y: f.start_price, color: "#5a6372", label: "today", dash: "2 3" }, ...(f.targets || []).map((t) => ({ y: t.price, color: "#fff", label: `🎯 ${money(t.price)}` }))] });
}

function targetsBlock(d) {
  const rows = d.targets.map((t) => `<tr style="cursor:default"><td>🎯 <b>${money(t.price)}</b> <span class="muted">${t.direction}</span></td><td>${esc(t.label)}</td>
    <td class="num">${pct((t.price / d.summary.price - 1) * 100)} away</td><td>${t.triggered_at ? `<span class="badge" style="--c:var(--t-allin)">HIT</span>` : ""}</td>
    <td><button class="x" data-del="${t.id}" title="delete">×</button></td></tr>`).join("");
  return `${rows ? `<table><tbody>${rows}</tbody></table>` : `<div class="empty">No targets yet.</div>`}
    <form class="form" id="tform"><input name="price" type="number" step="0.01" min="0" placeholder="Price" required style="width:110px">
      <select name="direction"><option value="below">falls to</option><option value="above">rises to</option></select>
      <input name="label" placeholder="Note to future self (e.g. 'back up the truck')"><button class="btn primary">Add</button></form>`;
}

function askBlock() {
  if (!state.cfg.llm.enabled) return `<p class="hint">Set <code>LLM_PROVIDER=ollama</code> (offline) / <code>openai</code> / <code>anthropic</code> to enable “Ask the AI”.</p>`;
  return `<form class="form" id="askf" style="margin-bottom:8px"><input name="q" style="flex:1" value="Is this a good spot to start scaling in, and where would you add more?"><button class="btn primary">Ask ${esc(state.cfg.llm.provider)}</button></form>`;
}

function agentsList(list) {
  return list.length ? list.map((a) => `<div class="agent"><b>${esc(a.agent)}</b> <span class="muted small">${a.ts.slice(0, 16).replace("T", " ")}</span>
      ${a.stance && a.stance !== "note" ? `<span class="badge" style="--c:${a.stance === "accumulate" ? "var(--up)" : a.stance === "avoid" ? "var(--down)" : "var(--accent)"}">${esc(a.stance)}</span>` : ""}
      ${a.target_price ? ` → ${money(a.target_price)} in ${a.horizon_days}d` : ""}${a.probability != null ? ` (${a.probability}% conf.)` : ""}
      <pre>${esc(a.rationale)}</pre></div>`).join("")
    : `<div class="empty">No agent has weighed in yet. Point one at <code>/api/agent/manifest</code>.</div>`;
}

function wireDetail() {
  $("#rng").onclick = (e) => { if (e.target.dataset.v) { state.range = e.target.dataset.v; $$("#rng button").forEach((b) => b.classList.toggle("on", b === e.target)); drawPrice(); } };
  $("#scale").onclick = (e) => { if (e.target.dataset.v) { state.log = e.target.dataset.v === "log"; $$("#scale button").forEach((b) => b.classList.toggle("on", b === e.target)); drawPrice(); } };
  $("#tform").onsubmit = async (e) => {
    e.preventDefault(); const fd = new FormData(e.target);
    await api("/api/targets", { method: "POST", body: JSON.stringify({ ticker: current.summary.ticker, price: +fd.get("price"), direction: fd.get("direction"), label: fd.get("label") }) }).catch((er) => alert(er.message));
    loadDetail(current.summary.ticker);
  };
  $$("[data-del]").forEach((b) => (b.onclick = async () => { await api(`/api/targets/${b.dataset.del}`, { method: "DELETE" }).catch((er) => alert(er.message)); loadDetail(current.summary.ticker); }));
  const ask = $("#askf");
  if (ask) ask.onsubmit = async (e) => {
    e.preventDefault(); const btn = $("button", ask); btn.disabled = true; btn.textContent = "Thinking…";
    try { await api("/api/agent/ask", { method: "POST", body: JSON.stringify({ ticker: current.summary.ticker, question: new FormData(ask).get("q") }) });
      current.agent_forecasts = await api(`/api/agent/forecasts?ticker=${current.summary.ticker}`); $("#agents").innerHTML = agentsList(current.agent_forecasts);
    } catch (er) { alert(er.message); } finally { btn.disabled = false; btn.textContent = `Ask ${state.cfg.llm.provider}`; }
  };
}

window.addEventListener("resize", () => { if (current && !$("#detail-view").hidden) { drawPrice(); drawForecast(); } });
boot();
