const POLL_MS = 30000;

const state = {
  tab: "picks",
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

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function fmtEt(ts) {
  return new Date(ts).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) + " ET";
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

// Green "BUY" / red "BREAKDOWN" tag next to a symbol, computed server-side
// by the same function that decides whether to send the matching email
// (app/scan/buy_setup.py / app/scan/breakdown_setup.py) - the badge and
// the email can never disagree.
function signalBadge(r) {
  if (r.buy_signal) return '<span class="buy-badge">BUY</span>';
  if (r.breakdown_signal) return '<span class="breakdown-badge">BREAKDOWN</span>';
  return "";
}

function signalRowClass(r) {
  if (r.buy_signal) return " buy-row";
  if (r.breakdown_signal) return " breakdown-row";
  return "";
}

function fundamentalsBadge(r) {
  if (r.fundamentals_status === "pass") return '<span class="fund-badge pass">Fund OK</span>';
  if (r.fundamentals_status === "fail") return '<span class="fund-badge fail">Fund Fail</span>';
  return '<span class="fund-badge unknown">Fund N/A</span>';
}

function compactContract(c) {
  if (!c) return "&mdash;";
  return `${c.option_type.toUpperCase()} $${fmtNum(c.strike)} exp ${c.expiration} &middot; $${fmtNum(c.price)}`;
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
    <tr class="clickable${signalRowClass(r)}" data-symbol="${r.symbol}">
      <td data-label="Symbol" class="symbol-cell">${r.symbol}${signalBadge(r)}</td>
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

function pickOptionLine(p) {
  if (!p.contract_symbol) {
    const reason = p.contract_note || "no liquid 30-45 day call near 0.65 delta";
    return `<div class="pick-option none">Option idea: none (${esc(reason)}) &mdash; stock-only idea</div>`;
  }
  const perContract = p.option_price === null ? null : Math.round(p.option_price * 100);
  const move = p.breakeven_move_pct === null ? "" : ` (${p.breakeven_move_pct >= 0 ? "+" : ""}${fmtNum(p.breakeven_move_pct, 1)}% from here)`;
  return `
    <div class="pick-option">
      <div><strong>Option idea: CALL $${fmtNum(p.strike)} exp ${esc(p.expiration)}</strong> (${p.days_to_expiration} days)
        &middot; ~$${fmtNum(p.option_price)}/share${perContract === null ? "" : ` (~$${perContract.toLocaleString()} per contract)`}</div>
      <div class="pick-option-detail">delta ${fmtNum(p.delta)} &middot; time decay ~$${p.theta === null ? "&mdash;" : fmtNum(Math.abs(p.theta))}/share per day
        &middot; breakeven $${fmtNum(p.breakeven)} at expiration${move}</div>
    </div>`;
}

function renderPicks(data) {
  const list = el("picks-list");
  const run = data.run;
  if (!run) {
    list.innerHTML = `<div class="empty">No scan has run yet. Picks appear after the first scan (every 5 minutes, 7:00am-4:00pm ET on weekdays).</div>`;
    return;
  }
  if (!data.rows.length) {
    list.innerHTML = `<div class="empty">No stock cleared the bar at the ${fmtEt(run.scan_ts)} scan (${run.candidates || 0} passed the basic filters, none scored high enough). On a weak or choppy day, no pick is the right answer. Updates every 5 minutes.</div>`;
    return;
  }
  list.innerHTML =
    `<div class="picks-updated">As of the ${fmtEt(run.scan_ts)} scan</div>` +
    data.rows
      .map((p) => {
        const reasons = (p.reasons || "").split("\n").filter(Boolean);
        const risks = (p.risks || "").split("\n").filter(Boolean);
        return `
      <div class="pick-card" data-symbol="${esc(p.symbol)}">
        <div class="pick-head">
          <span class="pick-rank">#${p.rank}</span>
          <span class="pick-symbol">${esc(p.symbol)}</span>
          <span class="pick-price">$${fmtNum(p.price)}</span>
          <span class="pick-score">${scoreBar(p.pick_score)}</span>
        </div>
        ${reasons.length ? `<div class="pick-label">Why</div><ul class="pick-reasons">${reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
        ${risks.length ? `<div class="pick-label risk">Risks</div><ul class="pick-risks">${risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
        ${pickOptionLine(p)}
      </div>`;
      })
      .join("");
  list.querySelectorAll(".pick-card[data-symbol]").forEach((card) => {
    card.addEventListener("click", () => openDrilldown(card.dataset.symbol));
  });
}

function renderSignals(rows, tradesBySymbol) {
  const tbody = el("signals-body");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="empty">No qualifying BUY or BREAKDOWN setups right now. These require a confirmed breakout (held, not just triggered) plus a high Alpha Score - and for BUY, a real-financials quality gate. Check the Top Alpha tab to see everything being tracked.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map((r) => {
      const direction = r.buy_signal ? "Bullish" : "Bearish";
      const directionClass = r.buy_signal ? "up" : "down";
      return `
    <tr class="clickable${signalRowClass(r)}" data-symbol="${r.symbol}">
      <td data-label="Symbol" class="symbol-cell">${r.symbol}${signalBadge(r)}</td>
      <td data-label="Direction" class="${directionClass}">${direction}</td>
      <td data-label="Alpha Score">${scoreBar(r.alpha_score)}</td>
      <td data-label="Hold Time">${holdBadge(r)}</td>
      <td data-label="Fundamentals">${fundamentalsBadge(r)}</td>
      <td data-label="Recommended Contract">${
        r.contract_note ? `<span class="contract-note">${esc(r.contract_note)}</span>` : compactContract(tradesBySymbol[r.symbol])
      }</td>
    </tr>`;
    })
    .join("");

  tbody.querySelectorAll("tr[data-symbol]").forEach((tr) => {
    tr.addEventListener("click", () => openDrilldown(tr.dataset.symbol));
  });
}

function renderOptionsTrades(rows) {
  const tbody = el("trades-body");
  if (!rows.length) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty">No recommended contracts yet this session.</td></tr>`;
    return;
  }
  tbody.innerHTML = rows
    .map(
      (r) => `
    <tr class="clickable" data-symbol="${r.symbol}">
      <td data-label="Symbol">${r.symbol}</td>
      <td data-label="Direction" class="${r.direction === "bullish" ? "up" : "down"}">${r.direction}</td>
      <td data-label="Type" class="${r.option_type === "call" ? "up" : "down"}">${r.option_type}</td>
      <td data-label="Strike">$${fmtNum(r.strike)}</td>
      <td data-label="Expiration">${r.expiration}</td>
      <td data-label="DTE">${r.days_to_expiration}d</td>
      <td data-label="Price">$${fmtNum(r.price)}</td>
      <td data-label="Delta">${fmtNum(r.delta)}</td>
      <td data-label="Daily Theta">$${fmtNum(r.theta)}</td>
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
    const f = data.fundamentals;
    const rec = data.trade_recommendation;
    const t = data.trend;
    const upside = f && f.target_mean_price && l.price ? (f.target_mean_price / l.price - 1) * 100 : null;
    el("drilldown-symbol").innerHTML = symbol + signalBadge(l);
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
        <h3>Fundamentals ${fundamentalsBadge(l)}</h3>
        ${
          f
            ? `
        <div class="metric-row"><span>Trailing P/E</span><span>${f.trailing_pe === null ? "n/a" : fmtNum(f.trailing_pe)}</span></div>
        <div class="metric-row"><span>Revenue Growth YoY</span><span>${fmtPct(f.revenue_growth_yoy)}</span></div>
        <div class="metric-row"><span>Profitable</span><span>${f.is_profitable === null ? "n/a" : f.is_profitable ? "yes" : "no"}</span></div>
        <div class="metric-row"><span>Profit Margin</span><span>${fmtPct(f.profit_margin)}</span></div>
        <div class="metric-row"><span>Analyst Avg Target</span><span>${f.target_mean_price === null ? "n/a" : "$" + fmtNum(f.target_mean_price) + (upside === null ? "" : ` (${upside >= 0 ? "+" : ""}${fmtNum(upside, 0)}%)`)}</span></div>
        <div class="metric-row"><span>Next Earnings</span><span>${f.next_earnings_date ? esc(f.next_earnings_date) : "n/a"}</span></div>
        `
            : "<div>No fundamentals data yet - refreshed once daily after close.</div>"
        }
      </div>
      <div class="drilldown-section">
        <h3>Trend</h3>
        ${
          t
            ? `
        <div class="metric-row"><span>Pick Score</span><span>${l.pick_score === null ? "n/a" : fmtNum(l.pick_score, 0)}</span></div>
        <div class="metric-row"><span>50-day Average</span><span>${t.sma50 === null ? "n/a" : "$" + fmtNum(t.sma50)}</span></div>
        <div class="metric-row"><span>200-day Average</span><span>${t.sma200 === null ? "n/a" : "$" + fmtNum(t.sma200)}</span></div>
        <div class="metric-row"><span>From 52-week High</span><span>${t.pct_from_high === null ? "n/a" : fmtNum(t.pct_from_high, 1) + "%"}</span></div>
        <div class="metric-row"><span>3-month Return vs S&amp;P 500</span><span class="${signClass(t.rs_3m_pct)}">${t.rs_3m_pct === null ? "n/a" : (t.rs_3m_pct >= 0 ? "+" : "") + fmtNum(t.rs_3m_pct, 1) + " pts"}</span></div>
        `
            : "<div>No trend data yet - computed from daily history at startup and after each close.</div>"
        }
      </div>
      ${
        rec
          ? `
      <div class="drilldown-section">
        <h3>Recommended Contract</h3>
        <div class="metric-row"><span>${rec.option_type.toUpperCase()} $${fmtNum(rec.strike)}</span><span>exp ${rec.expiration} (${rec.days_to_expiration}d)</span></div>
        <div class="metric-row"><span>Price</span><span>$${fmtNum(rec.price)}</span></div>
        <div class="metric-row"><span>Delta</span><span>${fmtNum(rec.delta)}</span></div>
        <div class="metric-row"><span>Est. Daily Theta</span><span>$${fmtNum(rec.theta)}</span></div>
      </div>
      `
          : ""
      }
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
  el("picks-view").hidden = tab !== "picks";
  el("signals-view").hidden = tab !== "signals";
  el("table-view").hidden = !["all", "premarket", "regular"].includes(tab);
  el("options-view").hidden = tab !== "options";
  el("trades-view").hidden = tab !== "trades";
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
      ? `Market open (${s.current_session}) \u00b7 ${s.tickers_tracked} tickers tracked`
      : `Market closed \u00b7 ${s.tickers_tracked} tickers tracked`;
    el("status-time").textContent =
      new Date(s.server_time_et).toLocaleTimeString("en-US", { timeZone: "America/New_York" }) + " ET";
  } catch (e) {
    el("status-text").textContent = "status unavailable";
  }
}

async function refresh() {
  if (state.tab === "picks") {
    renderPicks(await fetchJSON("/api/picks"));
  } else if (state.tab === "signals") {
    const [rankings, trades] = await Promise.all([
      fetchJSON("/api/rankings?session=all&only_signals=true&sort=alpha_score&limit=100"),
      fetchJSON("/api/options-trades?limit=100"),
    ]);
    const tradesBySymbol = {};
    trades.rows.forEach((t) => (tradesBySymbol[t.symbol] = t));
    renderSignals(rankings.rows, tradesBySymbol);
  } else if (state.tab === "options") {
    const data = await fetchJSON("/api/options?limit=50");
    renderOptions(data.rows);
  } else if (state.tab === "trades") {
    const data = await fetchJSON("/api/options-trades?limit=100");
    renderOptionsTrades(data.rows);
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
