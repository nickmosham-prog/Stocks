const POLL_MS = 30000;

const state = {
  tab: "all",
  sort: "alpha_score",
  rows: [],
};

const el = (id) => document.getElementById(id);

function fmtNum(v, digits = 2) {
  if (v === null || v === undefined) return "&mdash;";
  return Number(v).toFixed(digits);
}

function fmtVolume(v) {
  if (v === null || v === undefined) return "&mdash;";
  const n = Number(v);
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "K";
  return String(Math.round(n));
}

function signClass(v) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "up" : v < 0 ? "down" : "";
}

async function fetchJSON(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

function scoreBar(value) {
  const pct = value === null || value === undefined ? 0 : Math.max(0, Math.min(100, value));
  return `<div class="score-bar-wrap"><div class="score-bar"><div class="score-bar-fill" style="width:${pct}%"></div></div><span>${fmtNum(value, 0)}</span></div>`;
}

function fmtPct(v, digits = 0) {
  if (v === null || v === undefined) return "&mdash;";
  return (Number(v) * 100).toFixed(digits) + "%";
}

// A level that just triggered this cycle is a weaker signal than one that's
// held for a while - show how long, and a "held" badge once it clears the
// configured confirmation threshold (config/settings.yaml: hold_confirm_minutes).
function holdBadge(r) {
  if (r.breakout_hold_minutes === null || r.breakout_hold_minutes === undefined) return "";
  const minutes = Math.round(r.breakout_hold_minutes);
  if (r.breakout_confirmed) {
    return `<div class="hold-badge confirmed">held ${minutes}m</div>`;
  }
  return `<div class="hold-badge">${minutes}m</div>`;
}

function renderRankings(rows) {
  const tbody = el("rankings-body");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">No scan data yet. The scheduler runs Mon-Fri during market hours - check back once the market opens, or see the status bar above.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map(
      (r) => `
    <tr class="clickable" data-symbol="${r.symbol}">
      <td data-label="Symbol" class="symbol-cell">${r.symbol}</td>
      <td data-label="Price">$${fmtNum(r.price)}</td>
      <td data-label="Gap %" class="${signClass(r.gap_pct)}">${r.gap_pct === null ? "&mdash;" : fmtNum(r.gap_pct) + "%"}</td>
      <td data-label="RVOL">${r.rvol === null ? "&mdash;" : fmtNum(r.rvol) + "x"}</td>
      <td data-label="Breakout">${scoreBar(r.breakout_score)}${holdBadge(r)}</td>
      <td data-label="Options">${r.options_score === null ? "&mdash;" : scoreBar(r.options_score)}</td>
      <td data-label="News">${r.has_recent_news ? '<span class="news-dot">&#9679;</span>' : ""}</td>
      <td data-label="Alpha Score">${scoreBar(r.alpha_score)}</td>
    </tr>`
    )
    .join("");

  tbody.querySelectorAll("tr[data-symbol]").forEach((tr) => {
    tr.addEventListener("click", () => openDrilldown(tr.dataset.symbol));
  });
}

function renderOptions(rows) {
  const tbody = el("options-body");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty">No unusual options activity detected yet this session.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map(
      (r) => `
    <tr class="clickable" data-symbol="${r.symbol}">
      <td data-label="Symbol">${r.symbol}</td>
      <td data-label="Expiration">${r.expiration}</td>
      <td data-label="Type" class="${r.option_type === "call" ? "up" : "down"}">${r.option_type}</td>
      <td data-label="Strike">$${fmtNum(r.strike)}</td>
      <td data-label="Volume">${fmtVolume(r.volume)}</td>
      <td data-label="Open Interest">${fmtVolume(r.open_interest)}</td>
      <td data-label="Vol/OI">${fmtNum(r.vol_oi_ratio)}x</td>
      <td data-label="IV">${fmtPct(r.implied_volatility)}</td>
    </tr>`
    )
    .join("");
  tbody.querySelectorAll("tr[data-symbol]").forEach((tr) => {
    tr.addEventListener("click", () => openDrilldown(tr.dataset.symbol));
  });
}

function renderNews(rows) {
  const list = el("news-list");
  if (!rows.length) {
    list.innerHTML = `<li class="empty">No news fetched yet.</li>`;
    return;
  }
  list.innerHTML = rows
    .map((n) => {
      const tags = (n.tags || "")
        .split(",")
        .filter(Boolean)
        .map((t) => `<span class="tag">${t.replace(/_/g, " ")}</span>`)
        .join("");
      return `
      <li>
        <a class="headline" href="${n.link}" target="_blank" rel="noopener">${n.symbol}: ${n.headline}</a>
        <div class="meta">${n.publisher || ""} ${n.published_at ? "&middot; " + new Date(n.published_at).toLocaleString() : ""}</div>
        ${tags ? `<div class="tags">${tags}</div>` : ""}
      </li>`;
    })
    .join("");
}

async function openDrilldown(symbol) {
  const panel = el("drilldown");
  const body = el("drilldown-body");
  el("drilldown-symbol").textContent = symbol;
  body.innerHTML = "Loading...";
  panel.hidden = false;
  try {
    const data = await fetchJSON(`/api/ticker/${symbol}`);
    const l = data.latest;
    body.innerHTML = `
      <div class="drilldown-section">
        <h3>Snapshot</h3>
        <div class="metric-row"><span>Price</span><span>$${fmtNum(l.price)}</span></div>
        <div class="metric-row"><span>Gap %</span><span class="${signClass(l.gap_pct)}">${fmtNum(l.gap_pct)}%</span></div>
        <div class="metric-row"><span>RVOL</span><span>${fmtNum(l.rvol)}x</span></div>
        <div class="metric-row"><span>Cum. Volume Today</span><span>${fmtVolume(l.cum_volume_today)}</span></div>
        <div class="metric-row"><span>Breakout Score</span><span>${fmtNum(l.breakout_score, 0)} (${l.breakout_direction || "n/a"})</span></div>
        <div class="metric-row"><span>Level holding</span><span>${l.breakout_hold_minutes === null ? "not broken" : Math.round(l.breakout_hold_minutes) + "m" + (l.breakout_confirmed ? " (confirmed)" : "")}</span></div>
        <div class="metric-row"><span>Options Score</span><span>${l.options_score === null ? "n/a" : fmtNum(l.options_score, 0)}</span></div>
        <div class="metric-row"><span>Call/Put Ratio</span><span>${l.call_put_ratio === null ? "n/a" : fmtNum(l.call_put_ratio)}</span></div>
        <div class="metric-row"><span>Avg Implied Volatility</span><span>${fmtPct(l.avg_implied_volatility)}</span></div>
        <div class="metric-row"><span>Alpha Score</span><span>${fmtNum(l.alpha_score, 0)}</span></div>
        <div class="metric-row"><span>Last scan</span><span>${new Date(l.scan_ts).toLocaleTimeString("en-US", { timeZone: "America/New_York" })} ET</span></div>
      </div>
      <div class="drilldown-section">
        <h3>Recent News</h3>
        ${
          data.news.length
            ? data.news
                .slice(0, 5)
                .map((n) => `<div class="metric-row" style="display:block"><a href="${n.link}" target="_blank" rel="noopener">${n.headline}</a></div>`)
                .join("")
            : "<div>No recent headlines.</div>"
        }
      </div>
      <div class="drilldown-section">
        <h3>Options Contracts</h3>
        ${
          data.options.length
            ? data.options
                .slice(0, 8)
                .map(
                  (o) =>
                    `<div class="metric-row"><span>${o.option_type} $${fmtNum(o.strike)} (${o.expiration})</span><span>${fmtNum(o.vol_oi_ratio)}x vol/OI &middot; ${fmtPct(o.implied_volatility)} IV</span></div>`
                )
                .join("")
            : "<div>No unusual contracts flagged.</div>"
        }
      </div>
    `;
  } catch (e) {
    body.innerHTML = `<div class="empty">Could not load details for ${symbol}.</div>`;
  }
}

el("drilldown-close").addEventListener("click", () => {
  el("drilldown").hidden = true;
});

function setActiveTab(tab) {
  state.tab = tab;
  document.querySelectorAll(".tab").forEach((btn) => btn.classList.toggle("active", btn.dataset.tab === tab));
  el("table-view").hidden = tab === "options" || tab === "news";
  el("options-view").hidden = tab !== "options";
  el("news-view").hidden = tab !== "news";
  refresh();
}

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => setActiveTab(btn.dataset.tab));
});

document.querySelectorAll("th[data-sort]").forEach((th) => {
  th.addEventListener("click", () => {
    state.sort = th.dataset.sort;
    refresh();
  });
});

async function refreshStatus() {
  try {
    const s = await fetchJSON("/api/status");
    const dot = el("status-dot");
    dot.className = "status-dot " + (s.market_open ? "open" : "closed");
    el("status-text").textContent = s.market_open
      ? `Market open (${s.current_session}) &middot; ${s.tickers_tracked} tickers tracked`
      : `Market closed &middot; ${s.tickers_tracked} tickers tracked`;
    el("status-time").textContent =
      new Date(s.server_time_et).toLocaleTimeString("en-US", { timeZone: "America/New_York" }) + " ET";
  } catch (e) {
    el("status-text").textContent = "status unavailable";
  }
}

async function refresh() {
  if (state.tab === "options") {
    const data = await fetchJSON("/api/options?limit=50");
    renderOptions(data.rows);
  } else if (state.tab === "news") {
    const data = await fetchJSON("/api/news?limit=50");
    renderNews(data.rows);
  } else {
    const session = state.tab === "all" ? "all" : state.tab;
    const data = await fetchJSON(`/api/rankings?session=${session}&sort=${state.sort}&limit=100`);
    renderRankings(data.rows);
  }
  refreshStatus();
}

refresh();
setInterval(refresh, POLL_MS);
