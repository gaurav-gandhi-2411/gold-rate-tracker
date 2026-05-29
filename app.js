// app.js — Buyer-focused gold rate tracker.

const DATA_URL      = "data/prices.json";
const FORECAST_URL  = "data/forecast.json";
const BACKTEST_URL  = "data/backtest.json";
const COMMENTARY_URL = "data/commentary.json";
const DRIFT_URL     = "data/drift_metrics.json";
const METRICS_URL   = "data/metrics_history.json";

const fmtINR = (n) =>
  typeof n === "number"
    ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

function rupee(n) {
  if (typeof n !== "number") return "—";
  return `<span class="rupee">₹</span>${fmtINR(n)}`;
}

function fmtRelative(iso) {
  const d    = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60)    return "just now";
  if (diff < 3600)  return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleString("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function fmtIST(iso) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "numeric", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: true,
    }).format(new Date(iso));
  } catch (_) { return iso; }
}

let chart       = null;
let allReadings = [];

async function loadJSON(url) {
  const res = await fetch(`${url}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return res.json();
}

async function load() {
  const data = await loadJSON(DATA_URL);
  data.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  return data;
}

// ─── VERDICT ──────────────────────────────────────────────────────────────────

/**
 * Compute a buyer-facing verdict from price history and forecast.
 * Returns { type, headline, reason, icon } where type is:
 *   "down"    — prices falling, no rush
 *   "up"      — prices rising, consider acting sooner
 *   "flat"    — no strong signal
 *   "unknown" — insufficient data
 *
 * THREE-BUCKET RULES (deterministic):
 *
 * TRENDING_DOWN → "Trending down — no rush to buy"
 *   Condition: 7-day slope < −₹100
 *              AND (forecast below current  OR  current below 30d avg)
 *   Why two signals: a single 7-day slope can be noisy (festival spikes,
 *   weekend data gaps). A confirming signal from forecast or 30d mean
 *   reduces false alarms that would mislead buyers.
 *   Interpretation: prices are falling; buyer benefits from waiting.
 *
 * TRENDING_UP → "Trending up — consider buying sooner"
 *   Condition: 7-day slope > +₹100
 *              AND (forecast above current  OR  current above 30d avg)
 *   Interpretation: prices are rising; better to act before further increases.
 *
 * FLAT → "Roughly flat — buy when convenient"  (DEFAULT)
 *   Condition: slope within ±₹100, OR the two signals conflict.
 *   Interpretation: no directional pressure; timing is not critical.
 */
function computeVerdict(prices, forecast) {
  const SLOPE_THRESHOLD = 100; // ₹ change over 7 days to count as a trend

  if (!prices || prices.length < 2) {
    return {
      type: "unknown",
      icon: "○",
      headline: "Not enough data yet",
      reason: "Check back once more price readings are collected.",
    };
  }

  const now     = Date.now();
  const current = prices[prices.length - 1]["22k"];

  // 7-day slope: oldest reading within the last 7 days vs current.
  const ms7d    = 7 * 24 * 3600 * 1000;
  const within7d = prices.filter(p => now - new Date(p.timestamp).getTime() <= ms7d);
  const ref7d    = within7d.length > 1
    ? within7d[0]
    : prices[Math.max(0, prices.length - 5)];
  const slope7d  = current - ref7d["22k"];

  // 30-day average.
  const within30d = prices.filter(p => now - new Date(p.timestamp).getTime() <= 30 * 24 * 3600 * 1000);
  const avg30d    = within30d.length > 0
    ? Math.round(within30d.reduce((s, p) => s + p["22k"], 0) / within30d.length)
    : current;
  const vsAvg30d  = current - avg30d;

  // Forecast direction (0 if unavailable).
  const forecastDelta = (forecast && typeof forecast.predicted_22k === "number")
    ? forecast.predicted_22k - current
    : 0;

  // ── Classify ──
  if (slope7d < -SLOPE_THRESHOLD && (forecastDelta < 0 || vsAvg30d < 0)) {
    const absDelta = fmtINR(Math.abs(Math.round(slope7d)));
    const avgNote  = vsAvg30d < 0
      ? ` and ₹${fmtINR(Math.abs(vsAvg30d))} below the 30-day average`
      : "";
    return {
      type: "down",
      icon: "✓",
      headline: "Trending down — no rush to buy",
      reason: `Prices have slipped ₹${absDelta} over the last 7 days${avgNote}.`,
    };
  }

  if (slope7d > SLOPE_THRESHOLD && (forecastDelta > 0 || vsAvg30d > 0)) {
    const delta   = fmtINR(Math.round(slope7d));
    const avgNote = vsAvg30d > 0
      ? `, now ₹${fmtINR(Math.abs(vsAvg30d))} above the 30-day average`
      : "";
    return {
      type: "up",
      icon: "⚡",
      headline: "Trending up — consider buying sooner",
      reason: `Prices have risen ₹${delta} over the last 7 days${avgNote}.`,
    };
  }

  // Flat — describe magnitude of stability.
  const absSlope    = Math.abs(Math.round(slope7d));
  const dirWord     = slope7d > 0 ? "edged up" : slope7d < 0 ? "edged down" : "unchanged";
  const stableDesc  = absSlope < 20
    ? "virtually flat"
    : `${dirWord} ₹${fmtINR(absSlope)}`;
  return {
    type: "flat",
    icon: "◉",
    headline: "Roughly flat — buy when convenient",
    reason: `Prices are ${stableDesc} over the last 7 days. No strong signal either way.`,
  };
}

// ─── TODAY'S CHANGE ────────────────────────────────────────────────────────────

function computeTodayChange(readings) {
  if (readings.length < 2) return null;
  const latest   = readings[readings.length - 1];
  const istDay   = (iso) =>
    new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  const todayKey = istDay(latest.timestamp);

  // Walk backward to find the index of the earliest reading of today (IST).
  let earliestTodayIdx = -1;
  for (let i = readings.length - 1; i >= 0; i--) {
    if (istDay(readings[i].timestamp) === todayKey) {
      earliestTodayIdx = i;
    } else {
      break;
    }
  }

  const earliestToday = earliestTodayIdx >= 0 ? readings[earliestTodayIdx] : null;

  // If today has only one reading or we couldn't find an earlier one, use readings[-2].
  if (!earliestToday || earliestToday === latest) {
    return latest["22k"] - readings[readings.length - 2]["22k"];
  }

  // Sanity guard: if today's first reading differs from the reading immediately before
  // it (yesterday's close) by more than 3%, treat it as a potential scraper anomaly —
  // a bad opening reading would make "today's change" wildly misleading. Fall back to
  // comparing latest against readings[-2] (the reading right before latest), which is
  // recent and not subject to the same day-boundary artifact.
  if (earliestTodayIdx > 0) {
    const prevClose = readings[earliestTodayIdx - 1];
    const pctChange = Math.abs(earliestToday["22k"] - prevClose["22k"]) / prevClose["22k"];
    if (pctChange > 0.03) {
      return latest["22k"] - readings[readings.length - 2]["22k"];
    }
  }

  return latest["22k"] - earliestToday["22k"];
}

// ─── COMPARISON CARD VALUES ───────────────────────────────────────────────────

function computeComparisons(readings) {
  if (readings.length === 0) return null;
  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];
  const avg     = (arr) => Math.round(arr.reduce((s, v) => s + v, 0) / arr.length);
  const p22     = (r) => r["22k"];

  const prices7d  = readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3).map(p22);
  const prices30d = readings.filter(r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3).map(p22);
  const pricesAll = readings.map(p22);
  const spanDays  = Math.round((now - new Date(readings[0].timestamp).getTime()) / 86400e3);

  return {
    vs7d:     prices7d.length  > 1 ? current - avg(prices7d)       : null,
    vs30d:    prices30d.length > 1 ? current - avg(prices30d)      : null,
    vsLow:    pricesAll.length > 0 ? current - Math.min(...pricesAll) : null,
    spanDays,
  };
}

// ─── RENDERERS ────────────────────────────────────────────────────────────────

function renderStaleBanner(forecast) {
  const banner = document.getElementById("stale-banner");
  if (!banner) return;
  if (!forecast || !forecast.predicted_at) return;
  const ageH = (Date.now() - new Date(forecast.predicted_at).getTime()) / 3_600_000;
  if (ageH > 18) {
    const hours = Math.round(ageH);
    banner.textContent = `Forecast is ${hours}h old — last updated ${fmtRelative(forecast.predicted_at)}. Predictions may be outdated.`;
    banner.hidden = false;
  }
}

function renderFreshness(readings) {
  const pill = document.getElementById("freshness-pill");
  if (!pill) return;
  if (readings.length === 0) {
    pill.textContent = "Awaiting first reading";
    pill.className   = "freshness-pill";
    return;
  }
  const latest = readings[readings.length - 1];
  const ageH   = (Date.now() - new Date(latest.timestamp).getTime()) / 3_600_000;
  const rel    = fmtRelative(latest.timestamp);
  pill.classList.remove("freshness--ok", "freshness--warn", "freshness--stale");
  if (ageH >= 18) {
    pill.className   = "freshness-pill freshness--stale";
    pill.textContent = `Stuck — ${rel}`;
    pill.setAttribute("aria-label", `Data stuck, last updated ${rel}`);
  } else if (ageH >= 8) {
    pill.className   = "freshness-pill freshness--warn";
    pill.textContent = `Stale — ${rel}`;
    pill.setAttribute("aria-label", `Data stale, last updated ${rel}`);
  } else {
    pill.className   = "freshness-pill freshness--ok";
    pill.textContent = `Updated ${rel}`;
    pill.setAttribute("aria-label", `Updated ${rel}`);
  }
}

function renderHero(readings, forecast) {
  const skelEl    = document.getElementById("hero-skeleton");
  const eyeEl     = document.getElementById("hero-eyebrow");
  const priceEl   = document.getElementById("hero-price");
  const changeEl  = document.getElementById("hero-change");
  const verdictEl = document.getElementById("verdict-banner");

  if (skelEl) skelEl.hidden = true;
  if (eyeEl)  eyeEl.hidden  = false;

  if (readings.length === 0) {
    priceEl.innerHTML = "—"; // XSS-safe: static literal string, no external data
    priceEl.hidden    = false;
    if (verdictEl) {
      document.getElementById("verdict-icon").textContent    = "○";
      document.getElementById("verdict-headline").textContent = "Not enough data yet";
      document.getElementById("verdict-reason").textContent  = "Awaiting first price reading.";
      verdictEl.dataset.type = "unknown";
      verdictEl.hidden       = false;
    }
    return;
  }

  const latest = readings[readings.length - 1];
  // XSS-safe: rupee() wraps a number with fmtINR (toLocaleString); numbers cannot contain HTML
  priceEl.innerHTML = rupee(latest["22k"]);
  priceEl.hidden    = false;

  // Other karat prices — same as above, rupee() on a number is injection-proof
  const r24 = document.getElementById("rate-24");
  const r18 = document.getElementById("rate-18");
  if (r24) r24.innerHTML = rupee(latest["24k"]);
  if (r18) r18.innerHTML = rupee(latest["18k"]);

  // Today's change
  const todayDelta = computeTodayChange(readings);
  if (todayDelta !== null) {
    const dir    = todayDelta > 0 ? "up" : todayDelta < 0 ? "down" : "flat";
    const arrow  = dir === "up" ? "↑" : dir === "down" ? "↓" : "→";
    const sign   = dir === "up" ? "+" : dir === "down" ? "−" : "";
    changeEl.dataset.direction = dir;
    changeEl.querySelector(".hero-change-arrow").textContent  = arrow;
    changeEl.querySelector(".hero-change-amount").textContent =
      todayDelta === 0 ? "no change" : `${sign}₹${fmtINR(Math.abs(todayDelta))}`;
    changeEl.hidden = false;
  }

  // Verdict
  const verdict = computeVerdict(readings, forecast);
  document.getElementById("verdict-icon").textContent    = verdict.icon;
  document.getElementById("verdict-headline").textContent = verdict.headline;
  document.getElementById("verdict-reason").textContent  = verdict.reason;
  verdictEl.dataset.type = verdict.type;
  verdictEl.hidden       = false;

  renderSparkline(readings);
}

function renderSparkline(readings) {
  const wrap    = document.getElementById("sparkline-wrap");
  const svgEl   = document.getElementById("sparkline");
  const rangeEl = document.getElementById("sparkline-range");

  const now  = Date.now();
  const pts  = readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3);
  if (pts.length < 2) { wrap.hidden = true; return; }

  const prices = pts.map(p => p["22k"]);
  const min22k = Math.min(...prices);
  const max22k = Math.max(...prices);
  const span   = max22k - min22k || 1;

  const W = 300, H = 56, PX = 2, PY = 6;
  const toX = (i) => PX + (i / (prices.length - 1)) * (W - 2 * PX);
  const toY = (p) => PY + (1 - (p - min22k) / span) * (H - 2 * PY);

  const coords   = prices.map((p, i) => `${toX(i).toFixed(1)},${toY(p).toFixed(1)}`);
  const firstX   = coords[0].split(",")[0];
  const lastX    = coords[coords.length - 1].split(",")[0];
  const fillPath = `M ${coords[0]} L ${coords.slice(1).join(" L ")} L ${lastX},${H} L ${firstX},${H} Z`;

  const trendDown = prices[prices.length - 1] <= prices[0];
  const lineClr   = trendDown ? "#6a9a72" : "#c66a4b";
  const fillClr   = trendDown ? "rgba(106,154,114,0.18)" : "rgba(198,106,75,0.15)";
  const netChange = prices[prices.length - 1] - prices[0];

  svgEl.setAttribute("aria-label",
    `7-day price trend: ${trendDown ? "down" : "up"} ₹${fmtINR(Math.abs(Math.round(netChange)))}`);
  // XSS-safe: all interpolated values are derived from numeric price data
  // (coords = toFixed(1) floats, fillClr/lineClr = hardcoded rgba/hex literals).
  svgEl.innerHTML = `
    <path d="${fillPath}" fill="${fillClr}" stroke="none"/>
    <polyline points="${coords.join(" ")}" fill="none" stroke="${lineClr}"
              stroke-width="1.5" stroke-linejoin="round" stroke-linecap="round"/>
  `;

  rangeEl.textContent = `Low ₹${fmtINR(min22k)} · High ₹${fmtINR(max22k)}`;
  wrap.hidden = false;
}

function renderComparisons(readings) {
  const section = document.getElementById("comparison-section");
  const cmp     = computeComparisons(readings);
  if (!cmp || readings.length < 2) { section.hidden = true; return; }

  function setCard(valueId, subId, cardId, delta, avgLabel, nullNote) {
    const valEl  = document.getElementById(valueId);
    const subEl  = document.getElementById(subId);
    const card   = document.getElementById(cardId);
    if (delta === null) {
      valEl.textContent        = "—";
      subEl.textContent        = nullNote || "";
      card.dataset.sentiment   = "neutral";
      return;
    }
    const abs = fmtINR(Math.abs(delta));
    if (delta < 0) {
      valEl.textContent      = `−₹${abs}`;
      subEl.textContent      = `cheaper than ${avgLabel}`;
      card.dataset.sentiment = "good";
    } else if (delta > 0) {
      valEl.textContent      = `+₹${abs}`;
      subEl.textContent      = `pricier than ${avgLabel}`;
      card.dataset.sentiment = "caution";
    } else {
      valEl.textContent      = "at avg";
      subEl.textContent      = avgLabel;
      card.dataset.sentiment = "neutral";
    }
  }

  setCard("cmp-7d-value",  "cmp-7d-sub",  "cmp-7d",  cmp.vs7d,  "7d avg",  "not enough data");
  setCard("cmp-30d-value", "cmp-30d-sub", "cmp-30d", cmp.vs30d, "30d avg", "not enough data");

  const lowVal  = document.getElementById("cmp-low-value");
  const lowSub  = document.getElementById("cmp-low-sub");
  const lowCard = document.getElementById("cmp-low");
  if (cmp.vsLow === null) {
    lowVal.textContent        = "—";
    lowSub.textContent        = "";
    lowCard.dataset.sentiment = "neutral";
  } else if (cmp.vsLow === 0) {
    lowVal.textContent        = "at low";
    lowSub.textContent        = `30d period low`;
    lowCard.dataset.sentiment = "good";
  } else {
    lowVal.textContent        = `+₹${fmtINR(cmp.vsLow)}`;
    lowSub.textContent        = `above 30d low`;
    lowCard.dataset.sentiment = cmp.vsLow < 300 ? "neutral" : "caution";
  }

  section.hidden = false;
}

function renderCommentary(entries) {
  const textEl = document.getElementById("commentary-text");
  const metaEl = document.getElementById("commentary-meta");
  if (!textEl) return;

  if (!Array.isArray(entries) || entries.length === 0) {
    textEl.textContent = "Commentary not yet available. Check back after the next price update.";
    metaEl.textContent = "";
    return;
  }
  const latest = entries[entries.length - 1];
  if (!latest || !latest.text) {
    textEl.textContent = "Commentary unavailable.";
    metaEl.textContent = "";
    return;
  }

  // textContent (not innerHTML) prevents XSS from LLM output.
  textEl.textContent = latest.text;

  const ageH = latest.ts
    ? (Date.now() - new Date(latest.ts).getTime()) / 3_600_000
    : 0;

  if (ageH > 12) {
    metaEl.textContent = `From ${fmtRelative(latest.ts)} · may be stale`;
    metaEl.classList.add("commentary-meta--stale");
  } else {
    metaEl.textContent = latest.ts ? fmtRelative(latest.ts) : "";
    metaEl.classList.remove("commentary-meta--stale");
  }
}

function renderModelSignal(fc) {
  const section = document.getElementById("model-signal-section");
  if (!section) return;

  const cc = fc?.chronos_companion;
  if (!cc || cc.status !== "success") {
    section.hidden = true;
    return;
  }

  const dir       = cc.lean_direction ?? "neutral";
  const arrow     = dir === "up" ? "▲" : dir === "down" ? "▼" : "—";
  const dirLabel  = dir === "up" ? "Up" : dir === "down" ? "Down" : "Neutral";
  const cardClass = dir === "up" ? "signal-card--up" : dir === "down" ? "signal-card--down" : "";

  const strength = typeof cc.lean_strength_pct === "number"
    ? `${cc.lean_strength_pct.toFixed(1)}% lean`
    : "";
  const dirAcc = typeof cc.direction_acc_30f === "number"
    ? `${Math.round(cc.direction_acc_30f * 100)}% direction accuracy (30f)`
    : "—";

  let consensusText = "—";
  if (typeof cc.direction_consensus === "number" && typeof cc.majority_direction === "string") {
    const majorityCount = Math.round(cc.direction_consensus * 5);
    consensusText = `${majorityCount} of 5 samples agree`;
  }

  const calTag = cc.calibration_applied
    ? "Calibrated"
    : "Uncalibrated (gate at 30 pairs)";

  const body = document.getElementById("model-signal-body");
  // XSS-safe: all interpolated values are numbers, booleans, or hardcoded label strings
  // derived from forecast.json. No external text or LLM content reaches this template.
  body.innerHTML = `
    <div class="signal-card ${cardClass}">
      <div class="signal-direction-row">
        <span class="signal-arrow">${arrow}</span>
        <span class="signal-label">${dirLabel}</span>
        ${strength ? `<span class="signal-strength">${strength}</span>` : ""}
      </div>
      <div class="signal-stats-row">
        <span>${dirAcc}</span>
        <span class="signal-dot">·</span>
        <span>${consensusText}</span>
        <span class="signal-dot">·</span>
        <span>${calTag}</span>
      </div>
      <p class="signal-note">Directional signal only — does not change the headline price prediction. System status updated at least every 3 days.</p>
    </div>
  `;

  section.hidden = false;
}

function renderChart(readings, range) {
  let filtered = readings;
  if (range !== "all") {
    const cutoff = Date.now() - parseInt(range, 10) * 86400 * 1000;
    filtered     = readings.filter(r => new Date(r.timestamp).getTime() >= cutoff);
  }
  const labels = filtered.map(r => fmtDate(r.timestamp));
  const data22 = filtered.map(r => r["22k"]);

  const goldLine  = "#E09B2E";
  const axisColor = "#9a9282";
  const gridColor = "#3A3028";
  const ctx       = document.getElementById("chart");

  if (chart) chart.destroy();
  const c2d      = ctx.getContext("2d");
  const gradient = c2d.createLinearGradient(0, 0, 0, ctx.height || 320);
  gradient.addColorStop(0, "rgba(224,155,46,0.40)");
  gradient.addColorStop(1, "rgba(224,155,46,0.00)");

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "22K (₹/g)",
        data: data22,
        borderColor: goldLine,
        backgroundColor: gradient,
        fill: true,
        borderWidth: 2.5,
        pointRadius: filtered.length > 30 ? 0 : 3,
        pointBackgroundColor: goldLine,
        pointBorderWidth: 0,
        tension: 0.3,
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#241E16",
          borderColor: gridColor,
          borderWidth: 1,
          titleColor: axisColor,
          bodyColor: "#F5EDE0",
          padding: 12,
          callbacks: {
            label: (c) => `22K: ₹${fmtINR(c.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          ticks:  { color: axisColor, maxTicksLimit: 5, autoSkip: true, font: { family: "DM Sans", size: 11 } },
          grid:   { color: "transparent" },
          border: { color: gridColor },
        },
        y: {
          ticks:  { color: axisColor, font: { family: "DM Sans", size: 11 }, callback: (v) => "₹" + fmtINR(v) },
          grid:   { color: gridColor },
          border: { display: false },
        },
      },
    },
  });
}

function renderHistory(readings) {
  const tbody    = document.getElementById("history-body");
  const cardList = document.getElementById("history-cards");
  const showBtn  = document.getElementById("history-show-all");

  const EMPTY_TABLE = `<tr><td colspan="5" class="empty">No readings yet.</td></tr>`;
  const EMPTY_CARDS = `<li class="hcard-empty">No readings yet.</li>`;

  if (readings.length === 0) {
    tbody.innerHTML    = EMPTY_TABLE;    // XSS-safe: static template, no external data
    cardList.innerHTML = EMPTY_CARDS;   // XSS-safe: static template, no external data
    if (showBtn) showBtn.hidden = true;
    return;
  }

  const rows = [...readings].reverse().slice(0, 50);

  // ── Desktop table (unchanged from pre-Ψ2B) ──────────────────────────────────
  // XSS-safe: all interpolated values are numbers (fmtINR/rupee) or date strings
  // from prices.json; Groq/LLM output never reaches this template.
  tbody.innerHTML = rows.map((r, i) => {
    const next = rows[i + 1];
    let deltaCell = `<span class="delta-flat">—</span>`;
    if (next && typeof next["22k"] === "number") {
      const d = r["22k"] - next["22k"];
      if (d > 0)      deltaCell = `<span class="delta-up">↑ ₹${fmtINR(d)}</span>`;
      else if (d < 0) deltaCell = `<span class="delta-down">↓ ₹${fmtINR(Math.abs(d))}</span>`;
      else            deltaCell = `<span class="delta-flat">·</span>`;
    }
    return `<tr>
      <td>${fmtDate(r.timestamp)}</td>
      <td class="num">${rupee(r["22k"])}</td>
      <td class="num">${rupee(r["24k"])}</td>
      <td class="num">${rupee(r["18k"])}</td>
      <td class="num">${deltaCell}</td>
    </tr>`;
  }).join("");

  // ── Mobile card list — date-grouped timeline (Ψ2B) ──────────────────────────
  const VISIBLE_DAYS = 3;

  // Returns "29/5/2026" style key for grouping by IST calendar date
  function getISTDateKey(iso) {
    return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
  }

  // Returns "Thu, 29 May" for the date divider label
  function getISTDateLabel(iso) {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata", weekday: "short", day: "numeric", month: "short",
    }).format(new Date(iso));
  }

  // Returns "1:00 pm" for the card time
  function formatISTTime(iso) {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", hour12: true,
    }).format(new Date(iso)).toLowerCase();
  }

  // Group rows (newest-first) by IST calendar date
  const grouped = [];
  let currentGroup = null;
  rows.forEach((r, rowIndex) => {
    const key = getISTDateKey(r.timestamp);
    if (!currentGroup || currentGroup.key !== key) {
      currentGroup = { key, label: getISTDateLabel(r.timestamp), items: [] };
      grouped.push(currentGroup);
    }
    currentGroup.items.push({ r, rowIndex });
  });

  // Build HTML for a slice of groups; delta uses rows[] for cross-day continuity
  function buildGroupsHtml(groups) {
    // XSS-safe: formatISTTime, rupee(), fmtINR all produce safe output from numeric/date data
    return groups.map((group) => {
      const plural  = group.items.length !== 1 ? "s" : "";
      const divider = `<li class="hdate-divider">${group.label} · ${group.items.length} reading${plural}</li>`;
      const cards   = group.items.map(({ r, rowIndex: i }) => {
        const next = rows[i + 1];
        let deltaHtml = "";
        if (next && typeof next["22k"] === "number") {
          const d = r["22k"] - next["22k"];
          if (d > 0)      deltaHtml = `<span class="hcard-delta hcard-delta--up">↑ ₹${fmtINR(d)}</span>`;
          else if (d < 0) deltaHtml = `<span class="hcard-delta hcard-delta--down">↓ ₹${fmtINR(Math.abs(d))}</span>`;
          else            deltaHtml = `<span class="hcard-delta hcard-delta--flat">·</span>`;
        }
        return `<li class="history-card">
          <span class="hcard-time">${formatISTTime(r.timestamp)}</span>
          <span class="hcard-price">${rupee(r["22k"])}</span>
          ${deltaHtml}
        </li>`;
      }).join("");
      return divider + cards;
    }).join("");
  }

  // Render initial VISIBLE_DAYS days
  cardList.innerHTML = buildGroupsHtml(grouped.slice(0, VISIBLE_DAYS));

  // Show-more button (visible on mobile only via CSS display:none / display:block)
  const hiddenDayCount = Math.max(0, grouped.length - VISIBLE_DAYS);
  if (showBtn) {
    if (hiddenDayCount > 0) {
      showBtn.hidden = false;
      const moreLabel = `Show ${hiddenDayCount} more day${hiddenDayCount !== 1 ? "s" : ""}`;
      showBtn.textContent = moreLabel;
      let isExpanded = false;
      showBtn.onclick = () => {
        isExpanded = !isExpanded;
        if (isExpanded) {
          cardList.innerHTML = buildGroupsHtml(grouped);
          showBtn.textContent = "Show less";
        } else {
          cardList.innerHTML = buildGroupsHtml(grouped.slice(0, VISIBLE_DAYS));
          showBtn.textContent = moreLabel;
        }
      };
    } else {
      showBtn.hidden = true;
    }
  }
}

function renderMethodology(fc, bt, drift) {
  const body = document.getElementById("methodology-body");
  if (!body) return;

  const parts = [];

  // Verdict rule explanation
  parts.push(`
    <div class="meth-section">
      <h3 class="meth-heading">Verdict rules</h3>
      <p class="meth-text">Three deterministic buckets. Requires a 7-day slope beyond ±₹100 <em>plus</em> one confirming signal (forecast direction or 30-day mean) to avoid single-noise false alarms.</p>
      <ul class="meth-list">
        <li><strong>Trending down:</strong> 7d slope &lt; −₹100 AND (forecast below current OR current below 30d avg)</li>
        <li><strong>Trending up:</strong> 7d slope &gt; +₹100 AND (forecast above current OR current above 30d avg)</li>
        <li><strong>Roughly flat:</strong> everything else — slope within ±₹100 or signals conflict</li>
      </ul>
    </div>
  `);

  // Forecast details
  if (fc && typeof (fc.headline?.predicted_22k ?? fc.predicted_22k) === "number") {
    const pred22k = fc.headline?.predicted_22k ?? fc.predicted_22k;
    const lower   = fc.headline?.lower ?? fc.lower;
    const upper   = fc.headline?.upper ?? fc.upper;
    const hasPI   = typeof lower === "number" && typeof upper === "number";
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">5-day forecast (naive flat-hold)</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">22K estimate</div>
            <div class="meth-stat-value">₹${fmtINR(pred22k)}</div>
            ${hasPI ? `<div class="meth-stat-sub">80% PI: ₹${fmtINR(lower)} – ₹${fmtINR(upper)}</div>` : ""}
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Method</div>
            <div class="meth-stat-value">Naive flat-hold</div>
            <div class="meth-stat-sub">PI from conformal 80% of last 30 naive errors</div>
          </div>
        </div>
        ${fc.target_time ? `<p class="meth-text" style="margin-top:8px">Target: ${fmtIST(fc.target_time)}</p>` : ""}
        <p class="meth-text" style="margin-top:12px">These prediction intervals are intentionally wide: they cover a 5-day window using the 80th percentile of recent naive 5-day errors (conformal PI per ADR 014), not a single-day forecast.</p>
      </div>
    `);
  }

  // Chronos directional companion
  if (fc?.chronos_companion?.status === "success") {
    const cc = fc.chronos_companion;
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">Chronos directional signal (companion)</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">5d lean</div>
            <div class="meth-stat-value">${cc.lean_direction || "—"}</div>
            <div class="meth-stat-sub">±${cc.lean_strength_pct.toFixed(1)}% magnitude</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Direction accuracy (last 30 folds)</div>
            <div class="meth-stat-value">${(cc.direction_acc_30f * 100).toFixed(1)}%</div>
            <div class="meth-stat-sub">naive baseline: 50%</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Calibration</div>
            <div class="meth-stat-value">${cc.calibration_applied ? "applied" : "pending"}</div>
            <div class="meth-stat-sub">${cc.model_version}</div>
          </div>
        </div>
        <p class="meth-note">Directional signal only — Chronos lean does not change the headline prediction. Used to gate notifications when momentum confirms direction.</p>
      </div>
    `);
  } else if (fc?.chronos_companion?.status === "failed") {
    parts.push(`<p class="meth-text">Chronos signal unavailable.</p>`);
  }

  // Backtest stats
  if (bt && typeof bt.mae_5d_avg_chronos === "number") {
    const maeDiff = Math.round(bt.mae_5d_avg_chronos - bt.mae_5d_avg_naive);
    const dirDiff = Math.round((bt.dir_acc_5d_chronos - bt.dir_acc_5d_naive) * 100);
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">Walk-forward backtest (h=5) (${bt.n_folds ?? "—"} folds)</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">MAE</div>
            <div class="meth-stat-value">₹${fmtINR(Math.round(bt.mae_5d_avg_chronos))}</div>
            <div class="meth-stat-sub">${maeDiff >= 0 ? "+" : ""}₹${fmtINR(Math.abs(maeDiff))} vs naive (₹${fmtINR(Math.round(bt.mae_5d_avg_naive))})</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Direction</div>
            <div class="meth-stat-value">${Math.round(bt.dir_acc_5d_chronos * 100)}%</div>
            <div class="meth-stat-sub">${dirDiff >= 0 ? "+" : ""}${dirDiff}pp vs naive (${Math.round(bt.dir_acc_5d_naive * 100)}%)</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Wilcoxon p</div>
            <div class="meth-stat-value">${bt.wilcoxon_signed_rank_p != null ? bt.wilcoxon_signed_rank_p.toFixed(4) : "—"}</div>
            <div class="meth-stat-sub">paired test, model vs naive</div>
          </div>
        </div>
        <p class="meth-note">Naive baseline = predict last value unchanged. Per ADR 012, naive beats Chronos on 5d MAE (Wilcoxon p=0.0089 over 165 folds); naive is the production headline. Chronos kept as a directional companion.</p>
      </div>
    `);
  }

  // Live drift
  if (Array.isArray(drift) && drift.length > 0) {
    const now      = Date.now();
    const recent7d = drift.filter(e => e.residual != null && now - new Date(e.ts).getTime() <= 7 * 86400e3);
    const rolling  = recent7d.length > 0
      ? Math.round(recent7d.reduce((s, e) => s + Math.abs(e.residual), 0) / recent7d.length)
      : null;
    const withBase = [...drift].reverse().find(e => e.baseline_mae != null);
    const baseMae  = withBase ? Math.round(withBase.baseline_mae) : null;
    const ratio    = rolling != null && baseMae ? (rolling / baseMae).toFixed(2) : null;
    const ratioLabel = ratio
      ? (parseFloat(ratio) < 1 ? "on track" : parseFloat(ratio) <= 1.5 ? "watch" : "retraining recommended")
      : "";
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">Live forecast accuracy (7-day rolling)</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">Rolling MAE</div>
            <div class="meth-stat-value">${rolling != null ? "₹" + fmtINR(rolling) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Baseline MAE</div>
            <div class="meth-stat-value">${baseMae != null ? "₹" + fmtINR(baseMae) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Drift ratio</div>
            <div class="meth-stat-value">${ratio ?? "—"}</div>
            <div class="meth-stat-sub">${ratioLabel}</div>
          </div>
        </div>
      </div>
    `);
  }

  // XSS-safe: parts[] contains only hardcoded HTML templates with numeric/boolean
  // values from forecast.json and backtest.json. Groq commentary is NEVER included
  // here — it is rendered via textContent in renderCommentary() (line ~427).
  body.innerHTML = parts.join("");
}

function bindRangeToggle() {
  const buttons = document.querySelectorAll(".range-toggle button");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      buttons.forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      renderChart(allReadings, btn.dataset.range);
    });
  });
}

// ─── INIT ─────────────────────────────────────────────────────────────────────

(async function init() {
  bindRangeToggle();

  // Load prices (critical path)
  try {
    allReadings = await load();
  } catch (err) {
    if (typeof Sentry !== "undefined") Sentry.captureException(err, { extra: { url: DATA_URL } });
    console.error(err);
    const skelEl = document.getElementById("hero-skeleton");
    if (skelEl) skelEl.hidden = true;
    const priceEl = document.getElementById("hero-price");
    if (priceEl) { priceEl.textContent = "Error loading data"; priceEl.hidden = false; }
    document.getElementById("commentary-text").textContent =
      "Could not load price data. If you just deployed, run the workflow once from the Actions tab.";
    return;
  }

  // Forecast loads in parallel — needed for verdict.
  const fcPromise = loadJSON(FORECAST_URL).catch(err => {
    if (typeof Sentry !== "undefined") Sentry.captureException(err, { extra: { url: FORECAST_URL } });
    return null;
  });

  // Render everything that doesn't need forecast immediately.
  renderFreshness(allReadings);
  renderComparisons(allReadings);
  renderHistory(allReadings);
  renderChart(allReadings, "7");

  // Await forecast, then render hero (hides skeleton, shows verdict).
  const fc = await fcPromise;
  renderHero(allReadings, fc);
  renderStaleBanner(fc);
  renderModelSignal(fc);

  // Remaining optional data (all gracefully degrade on failure).
  const [bt, commentary, drift] = await Promise.allSettled([
    loadJSON(BACKTEST_URL),
    loadJSON(COMMENTARY_URL),
    loadJSON(DRIFT_URL),
  ]);

  // Report any optional-fetch failures so silent pipeline breaks surface in Sentry.
  if (typeof Sentry !== "undefined") {
    const optionalUrls = [BACKTEST_URL, COMMENTARY_URL, DRIFT_URL];
    [bt, commentary, drift].forEach((r, i) => {
      if (r.status === "rejected") Sentry.captureException(r.reason, { extra: { url: optionalUrls[i] } });
    });
  }

  renderCommentary(commentary.status === "fulfilled" ? commentary.value : null);
  renderMethodology(
    fc,
    bt.status        === "fulfilled" ? bt.value        : null,
    drift.status     === "fulfilled" ? drift.value     : null,
  );
})();
