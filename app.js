// app.js — Buyer-focused gold rate tracker.

const DATA_URL      = "data/prices.json";
const FORECAST_URL  = "data/forecast.json";
const BACKTEST_URL  = "data/backtest.json";
const COMMENTARY_URL = "data/commentary.json";
const DRIFT_URL     = "data/drift_metrics.json";
const METRICS_URL   = "data/metrics_history.json";

// D4: True when running as an installed PWA launched from the home screen.
// navigator.standalone is iOS WebKit's proprietary flag (true/false/undefined).
// matchMedia display-mode:standalone is the W3C standard (patchy on older iOS).
// OR'ing both gives best cross-platform coverage without false positives.
const IS_STANDALONE =
  window.matchMedia("(display-mode: standalone)").matches ||
  window.navigator.standalone === true;

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

// One reading per IST calendar day (latest timestamp wins).
// Used in display/chart paths AND in the daily-average computations (vs-7d/vs-30d avg,
// verdict avg30d). allReadings stays raw for everything else: conformal PI, backtest
// inputs, trend slope, current price — those must not be time-averaged away.
function dedupeByISTDay(readings) {
  const byDay = new Map();
  for (const r of readings) {
    const key = new Date(r.timestamp).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
    byDay.set(key, r);
  }
  return [...byDay.values()];
}

// ─── DISPLAY-ONLY DEDUP (Φ8C' / absorbed from Ψ3C.3) ────────────────────────
// prices.json is NEVER modified. ML pipeline reads it directly.

function dedupReadings(readings) {
  if (readings.length === 0) return [];
  const groups = [];
  let g = { reading: readings[0], endTimestamp: readings[0].timestamp, count: 1 };
  for (let i = 1; i < readings.length; i++) {
    if (readings[i]["22k"] === g.reading["22k"]) {
      g.endTimestamp = readings[i].timestamp;
      g.count++;
    } else {
      groups.push(g);
      g = { reading: readings[i], endTimestamp: readings[i].timestamp, count: 1 };
    }
  }
  groups.push(g);
  return groups;
}

function dedupForChart(readings) {
  if (readings.length === 0) return [];
  const pts = [];
  let runStart = readings[0];
  let runEnd   = readings[0];
  for (let i = 1; i < readings.length; i++) {
    if (readings[i]["22k"] === runStart["22k"]) {
      runEnd = readings[i];
    } else {
      pts.push(runStart);
      if (runEnd !== runStart) pts.push(runEnd);
      runStart = readings[i];
      runEnd   = readings[i];
    }
  }
  pts.push(runStart);
  if (runEnd !== runStart) pts.push(runEnd);
  return pts;
}

function fmtDateShort(iso) {
  return new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata", day: "numeric", month: "short",
  }).format(new Date(iso));
}

let chart            = null;
let allReadings      = [];
let currentRange     = "7";   // tracks active chart tab for refreshData()
let pwaHelpDismissed = false; // D5: set true when user taps ✕; survives re-renders
let chartPinnedIndex  = null;  // index of tapped chart point; null = no callout
let trackRecordChart  = null;  // Chart.js instance for forecast-vs-actual section

// Ψ3C.2: stagger card-enter animation across a list of elements.
// Forces a reflow between remove/add so the animation restarts each time.
function staggerEnter(elements, step = 30) {
  elements.forEach((el, i) => {
    if (!el) return;
    el.classList.remove("card-enter");
    void el.offsetWidth; // reflow triggers animation restart
    el.style.animationDelay = `${i * step}ms`;
    el.classList.add("card-enter");
  });
}

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
 * TRENDING_DOWN → "Trending down this week"
 *   Condition: 7-day slope < −₹100
 *              AND (forecast below current  OR  current below 30d avg)
 *   Why two signals: a single 7-day slope can be noisy (festival spikes,
 *   weekend data gaps). A confirming signal from forecast or 30d mean
 *   reduces false alarms.
 *
 * TRENDING_UP → "Trending up this week"
 *   Condition: 7-day slope > +₹100
 *              AND (forecast above current  OR  current above 30d avg)
 *
 * FLAT → "Roughly flat this week"  (DEFAULT)
 *   Condition: slope within ±₹100, OR the two signals conflict.
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

  // 30-day average over IST-day-deduped daily series — one reading per day, latest wins.
  // Deduped so "30-day average" means the avg of 30 daily prices, not time-weighted over ~8
  // readings/day (a flat-held price would otherwise dominate the average).
  const within30d = prices.filter(p => now - new Date(p.timestamp).getTime() <= 30 * 24 * 3600 * 1000);
  const daily30d  = dedupeByISTDay(within30d);
  const avg30d    = daily30d.length > 0
    ? Math.round(daily30d.reduce((s, p) => s + p["22k"], 0) / daily30d.length)
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
      headline: "Trending down this week",
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
      headline: "Trending up this week",
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
    headline: "Roughly flat this week",
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

  // raw7d/raw30d stay raw so vsLow reflects the actual extreme price reached during the period.
  // prices7d/prices30d are deduped to one reading per IST day so vs-7d/vs-30d avg = avg of daily
  // prices, not time-weighted over ~8 readings/day (a flat-held price repeats ~8×/day and
  // would otherwise dominate the average — see dedupeByISTDay() comment for scope of this rule).
  const raw7d    = readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3);
  const raw30d   = readings.filter(r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3);
  const prices7d  = dedupeByISTDay(raw7d).map(p22);
  const prices30d = dedupeByISTDay(raw30d).map(p22);
  const spanDays  = Math.round((now - new Date(readings[0].timestamp).getTime()) / 86400e3);

  return {
    vs7d:     prices7d.length  > 1 ? current - avg(prices7d)          : null,
    vs30d:    prices30d.length > 1 ? current - avg(prices30d)         : null,
    vsLow:    raw30d.length    > 0 ? current - Math.min(...raw30d.map(p22)) : null,
    spanDays,
  };
}

// ─── GOOD-PRICE SIGNALS ───────────────────────────────────────────────────────
// Computes the two 30-day signals for "Is today a good price?":
//   percentile30d — % of IST-day-deduped daily prices in the last 30 days that
//                   are at/below today's price (distribution-aware position)
//   vsAvg30d      — today's price minus the mean of those 30 daily prices
//
// Returns null when fewer than 5 distinct IST days are available in the 30d window
// (not enough data for a meaningful signal).
// The "90-day band position" signal is deferred until ~90 distinct days accumulate
// (currently 48 days); see Decision Log Φ11-2 band-at-90d revisit trigger.

function computeGoodPriceSignals(readings) {
  if (!readings || readings.length < 2) return null;

  const now     = Date.now();
  const current = readings[readings.length - 1]["22k"];

  const within30d = readings.filter(
    r => now - new Date(r.timestamp).getTime() <= 30 * 86400e3,
  );
  const daily30d  = dedupeByISTDay(within30d);
  const nDays30d  = daily30d.length;
  if (nDays30d < 5) return null;

  const prices30d     = daily30d.map(r => r["22k"]);
  const percentile30d = Math.round(
    prices30d.filter(p => p <= current).length / nDays30d * 100,
  );

  const avg30d   = Math.round(prices30d.reduce((s, p) => s + p, 0) / nDays30d);
  const vsAvg30d = current - avg30d;

  let verdict, supportLine1, verdictType;
  if (percentile30d <= 30) {
    verdictType  = "low";
    verdict      = "Prices have been lower than usual lately";
    supportLine1 = "Cheaper than most days this past month.";
  } else if (percentile30d >= 70) {
    verdictType  = "high";
    verdict      = "Prices have been higher than usual lately";
    supportLine1 = "Pricier than most days this past month.";
  } else {
    verdictType  = "mid";
    verdict      = "Prices are around usual levels lately";
    supportLine1 = "Around the middle of the past month.";
  }

  const absVsAvg = fmtINR(Math.abs(vsAvg30d));
  const supportLine2 = vsAvg30d < 0
    ? `₹${absVsAvg} below the 30-day average.`
    : vsAvg30d > 0
      ? `₹${absVsAvg} above the 30-day average.`
      : "At the 30-day average.";

  // Divergence: percentile says cheap but vs-avg says above average, or vice versa.
  // Fires in skewed distributions (e.g. one recent spike pulls the average up).
  const divergenceNote =
    (verdictType === "low"  && vsAvg30d > 0) ||
    (verdictType === "high" && vsAvg30d < 0)
      ? "(The two measures diverge here — the percentile counts days, the average measures distance. The headline follows the percentile.)"
      : null;

  return { percentile30d, vsAvg30d, avg30d, nDays30d, verdict, verdictType, supportLine1, supportLine2, divergenceNote };
}

// ─── RENDERERS ────────────────────────────────────────────────────────────────

function renderStaleBanner(forecast) {
  const banner = document.getElementById("stale-banner");
  if (!banner) return;
  if (!forecast || !forecast.predicted_at) return;
  const ageH = (Date.now() - new Date(forecast.predicted_at).getTime()) / 3_600_000;
  if (ageH > 18) {
    const hours = Math.round(ageH);
    banner.textContent = `Prices last updated ${fmtRelative(forecast.predicted_at)} — data may not reflect the current rate.`;
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
    pill.textContent = `Not updating · ${rel}`;
    pill.setAttribute("aria-label", `Not updating, last updated ${rel}`);
  } else if (ageH >= 8) {
    pill.className   = "freshness-pill freshness--warn";
    pill.textContent = `Stale · ${rel}`;
    pill.setAttribute("aria-label", `Data stale, last updated ${rel}`);
  } else {
    pill.className   = "freshness-pill freshness--ok";
    pill.textContent = rel;
    pill.setAttribute("aria-label", `Updated ${rel}`);
  }

  // D5: Auto-open iOS help panel when standalone + data is ≥ 12h stale,
  // but only if the user hasn't dismissed it this session (FIX 1).
  if (IS_STANDALONE && ageH >= 12 && !pwaHelpDismissed) {
    const panel = document.getElementById("pwa-help-panel");
    if (panel) panel.hidden = false;
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
  const locEl = document.getElementById("hero-location");
  if (locEl) locEl.hidden = false;

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
  const pts  = dedupeByISTDay(readings.filter(r => now - new Date(r.timestamp).getTime() <= 7 * 86400e3));
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

  const skelEl = document.getElementById("commentary-skeleton");
  if (skelEl) skelEl.hidden = true;
  if (textEl) textEl.hidden = false;
  if (metaEl) metaEl.hidden = false;

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

function computeTrendDescription(readings, nDays = 7) {
  if (!readings || readings.length < 2) return null;
  const cutoff = Date.now() - nDays * 86400 * 1000;
  const recent = readings.filter(r => new Date(r.timestamp).getTime() >= cutoff);
  if (recent.length < 2) return null;
  const first = recent[0]["22k"];
  const last  = readings[readings.length - 1]["22k"];
  const delta = last - first;
  const abs   = Math.abs(delta);
  if (abs < 100) return `Roughly flat over the past ${nDays} days`;
  const dir  = delta > 0 ? "up" : "down";
  const sign = delta > 0 ? "+" : "−";
  return `Trending ${dir} — ${sign}₹${fmtINR(abs)} over the past ${nDays} days`;
}

function renderModelSignal(fc, readings, bt) {
  const section = document.getElementById("model-signal-section");
  if (!section) return;

  const skelEl = document.getElementById("model-signal-skeleton");
  if (skelEl) skelEl.hidden = true;

  const signals = computeGoodPriceSignals(readings ?? []);
  if (!signals) {
    section.hidden = true;
    return;
  }

  const hl = fc?.headline;
  const hasPI = hl && typeof hl.lower === "number" && typeof hl.upper === "number";

  // Volatility context (demoted 5-day band): describes how much gold moves, not where it goes.
  // conformal_pi_half is the half-width of the 80% PI; round to nearest ₹50 for readability.
  let volatilityHtml = "";
  if (hasPI) {
    const piHalf    = hl.conformal_pi_half ?? (hl.upper - hl.lower) / 2;
    const Z         = Math.round(piHalf / 50) * 50;
    const coveragePct = bt?.pi_coverage_80_5d_avg != null
      ? Math.round(bt.pi_coverage_80_5d_avg * 100)
      : 87;
    // XSS-safe: rupee()/fmtINR() wrap numbers only; Z and coveragePct are computed numbers.
    volatilityHtml = `
      <div class="outlook-volatility">
        <p class="outlook-volatility-note">Gold's price typically moves about ±₹${fmtINR(Z)} over 5 days.</p>
        <div class="outlook-range-row">
          <span class="outlook-range-label">5-day range</span>
          <span class="outlook-range-value">${rupee(hl.lower)} – ${rupee(hl.upper)}</span>
        </div>
        <p class="outlook-range-note">Covers typical 5-day moves ${coveragePct}% of the time</p>
      </div>
    `;
  }

  // XSS-safe: verdict/supportLine1/divergenceNote are hardcoded string literals from
  // computeGoodPriceSignals; supportLine2 interpolates fmtINR(number) only.
  document.getElementById("model-signal-body").innerHTML = `
    <div class="outlook-card">
      <p class="good-price-verdict good-price-verdict--${signals.verdictType}">${signals.verdict}</p>
      <ul class="good-price-supporting">
        <li>${signals.supportLine1}</li>
        <li>${signals.supportLine2}</li>
        ${signals.divergenceNote ? `<li class="good-price-divergence">${signals.divergenceNote}</li>` : ""}
      </ul>
      ${volatilityHtml}
    </div>
  `;

  section.hidden = false;
}

// Φ8C' / Ψ3C.3: persistent tap-to-reveal price callout drawn via Chart.js afterDraw.
const CALLOUT_PLUGIN = {
  id: "phi8cCallout",
  afterDraw(ch) {
    if (chartPinnedIndex === null) return;
    const meta = ch.getDatasetMeta(0);
    const dp   = meta.data[chartPinnedIndex];
    if (!dp) return;
    const { ctx, chartArea } = ch;
    const x = dp.x;
    const label = ch.data.labels[chartPinnedIndex] || "";
    const value = ch.data.datasets[0].data[chartPinnedIndex];
    ctx.save();
    ctx.strokeStyle = "rgba(224,155,46,0.50)";
    ctx.lineWidth   = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = "bold 13px DM Sans, system-ui, sans-serif";
    const pw = ctx.measureText(`₹${fmtINR(value)}`).width;
    ctx.font = "11px DM Sans, system-ui, sans-serif";
    const dw = ctx.measureText(label).width;
    const bW = Math.max(pw, dw) + 24;
    const bH = 40;
    const bR = 7;
    let bx = x - bW / 2;
    bx = Math.max(chartArea.left, Math.min(bx, chartArea.right - bW));
    const by = chartArea.top + 4;
    ctx.beginPath();
    ctx.moveTo(bx + bR, by);
    ctx.lineTo(bx + bW - bR, by);
    ctx.quadraticCurveTo(bx + bW, by, bx + bW, by + bR);
    ctx.lineTo(bx + bW, by + bH - bR);
    ctx.quadraticCurveTo(bx + bW, by + bH, bx + bW - bR, by + bH);
    ctx.lineTo(bx + bR, by + bH);
    ctx.quadraticCurveTo(bx, by + bH, bx, by + bH - bR);
    ctx.lineTo(bx, by + bR);
    ctx.quadraticCurveTo(bx, by, bx + bR, by);
    ctx.closePath();
    ctx.fillStyle   = "#241E16";
    ctx.fill();
    ctx.strokeStyle = "#E09B2E";
    ctx.lineWidth   = 1;
    ctx.stroke();
    ctx.fillStyle   = "#E09B2E";
    ctx.font        = "bold 13px DM Sans, system-ui, sans-serif";
    ctx.textAlign   = "center";
    ctx.textBaseline = "alphabetic";
    ctx.fillText(`₹${fmtINR(value)}`, bx + bW / 2, by + 16);
    ctx.fillStyle = "#9A9282";
    ctx.font      = "11px DM Sans, system-ui, sans-serif";
    ctx.fillText(label, bx + bW / 2, by + 31);
    ctx.restore();
  },
};

function renderChart(readings, range) {
  chartPinnedIndex = null;

  let filtered = readings;
  if (range !== "all") {
    const cutoff = Date.now() - parseInt(range, 10) * 86400 * 1000;
    filtered     = readings.filter(r => new Date(r.timestamp).getTime() >= cutoff);
  }

  const chartPts = dedupForChart(filtered);
  const labels   = chartPts.map(r => fmtDate(r.timestamp));
  const data22   = chartPts.map(r => r["22k"]);

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
        pointRadius: chartPts.length > 30 ? 0 : 3,
        pointHoverRadius: 5,
        pointBackgroundColor: goldLine,
        pointBorderWidth: 0,
        tension: 0,
        stepped: "before",
        spanGaps: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      onClick: (event, elements) => {
        if (elements.length > 0) {
          const idx = elements[0].index;
          chartPinnedIndex = (chartPinnedIndex === idx) ? null : idx;
        } else {
          chartPinnedIndex = null;
        }
        chart.update("none");
      },
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
        phi8cCallout: {},
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
    plugins: [CALLOUT_PLUGIN],
  });
}

function renderHistory(readings) {
  const tbody    = document.getElementById("history-body");
  const cardList = document.getElementById("history-cards");
  const showBtn  = document.getElementById("history-show-all");
  const skelEl   = document.getElementById("history-skeleton");

  if (skelEl) skelEl.hidden = true;

  const EMPTY_TABLE = `<tr><td colspan="5" class="empty">No readings yet.</td></tr>`;
  const EMPTY_CARDS = `<li class="hcard-empty">No readings yet.</li>`;

  if (readings.length === 0) {
    tbody.innerHTML    = EMPTY_TABLE;
    cardList.innerHTML = EMPTY_CARDS;
    if (showBtn) showBtn.hidden = true;
    return;
  }

  const allGroups = dedupReadings([...readings].reverse());
  const groups    = allGroups.slice(0, 50);

  // ── Desktop table ────────────────────────────────────────────────────────────
  // XSS-safe: all interpolated values are numbers or date strings from prices.json.
  tbody.innerHTML = groups.map((g, i) => {
    const nextGroup = groups[i + 1];
    let deltaCell = `<span class="delta-flat">—</span>`;
    if (nextGroup) {
      const d = g.reading["22k"] - nextGroup.reading["22k"];
      if (d > 0)      deltaCell = `<span class="delta-up">↑ ₹${fmtINR(d)}</span>`;
      else if (d < 0) deltaCell = `<span class="delta-down">↓ ₹${fmtINR(Math.abs(d))}</span>`;
      else            deltaCell = `<span class="delta-flat">·</span>`;
    }
    // g.reading.timestamp = NEWEST reading in run; g.endTimestamp = OLDEST (first occurrence).
    let whenCell;
    if (g.count === 1) {
      whenCell = fmtDate(g.reading.timestamp);
    } else if (i === 0) {
      whenCell = `Since ${fmtDateShort(g.endTimestamp)}`;
    } else {
      whenCell = `${fmtDateShort(g.endTimestamp)} – ${fmtDateShort(g.reading.timestamp)}`;
    }
    return `<tr>
      <td>${whenCell}</td>
      <td class="num">${rupee(g.reading["22k"])}</td>
      <td class="num">${rupee(g.reading["24k"])}</td>
      <td class="num">${rupee(g.reading["18k"])}</td>
      <td class="num">${deltaCell}</td>
    </tr>`;
  }).join("");

  // ── Mobile card list (dedup-grouped) ─────────────────────────────────────────
  function formatISTTime(iso) {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", hour12: true,
    }).format(new Date(iso)).toLowerCase();
  }

  const VISIBLE_GROUPS = 8;

  function buildCardsHtml(start, count) {
    // XSS-safe: all interpolated values are numbers or date strings from prices.json.
    return groups.slice(start, start + count).map((g, relIdx) => {
      const absIdx    = start + relIdx;
      const nextGroup = groups[absIdx + 1];
      let deltaHtml   = "";
      if (nextGroup) {
        const d = g.reading["22k"] - nextGroup.reading["22k"];
        if (d > 0)      deltaHtml = `<span class="hcard-delta hcard-delta--up">↑ ₹${fmtINR(d)}</span>`;
        else if (d < 0) deltaHtml = `<span class="hcard-delta hcard-delta--down">↓ ₹${fmtINR(Math.abs(d))}</span>`;
      }
      let timeLabel;
      if (g.count === 1) {
        timeLabel = formatISTTime(g.reading.timestamp);
      } else if (absIdx === 0) {
        timeLabel = `Since ${fmtDateShort(g.endTimestamp)}`;
      } else {
        timeLabel = `${fmtDateShort(g.endTimestamp)}–${fmtDateShort(g.reading.timestamp)}`;
      }
      return `<li class="history-card">
        <span class="hcard-time">${timeLabel}</span>
        <span class="hcard-price">${rupee(g.reading["22k"])}</span>
        ${deltaHtml}
      </li>`;
    }).join("");
  }

  cardList.innerHTML = buildCardsHtml(0, VISIBLE_GROUPS);

  const hiddenCount = Math.max(0, groups.length - VISIBLE_GROUPS);
  if (showBtn) {
    if (hiddenCount > 0) {
      showBtn.hidden = false;
      const moreLabel = `Show ${hiddenCount} more`;
      showBtn.textContent = moreLabel;
      let isExpanded = false;
      showBtn.onclick = () => {
        isExpanded = !isExpanded;
        cardList.innerHTML = buildCardsHtml(0, isExpanded ? groups.length : VISIBLE_GROUPS);
        showBtn.textContent = isExpanded ? "Show less" : moreLabel;
      };
    } else {
      showBtn.hidden = true;
    }
  }
}

function renderForecastVsActual(bt) {
  const section = document.getElementById("section-track-record");
  if (!section) return;
  if (!bt?.folds?.length) { section.hidden = true; return; }

  const folds = bt.folds
    .filter(f => !f.sub_30_context)
    .slice(-30);

  if (folds.length < 3) { section.hidden = true; return; }

  const labels  = folds.map(f => fmtDateShort(f.context_end_date + "T00:00:00Z"));
  const actuals = folds.map(f => typeof f.actuals[0] === "number" ? f.actuals[0] : null);
  const naives  = folds.map(f => typeof f.naive[0]   === "number" ? f.naive[0]   : null);

  const goldLine  = "#E09B2E";
  const axisColor = "#9a9282";
  const gridColor = "#3A3028";
  const ctx       = document.getElementById("track-record-chart");
  if (!ctx) { section.hidden = true; return; }

  if (trackRecordChart) trackRecordChart.destroy();

  trackRecordChart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "What happened",
          data: actuals,
          borderColor: goldLine,
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 2,
          pointRadius: 3,
          pointBackgroundColor: goldLine,
          pointBorderWidth: 0,
          tension: 0.1,
          spanGaps: true,
        },
        {
          label: "Flat-hold forecast",
          data: naives,
          borderColor: "#6B5E4E",
          backgroundColor: "transparent",
          fill: false,
          borderWidth: 1.5,
          pointRadius: 0,
          borderDash: [5, 4],
          tension: 0,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: true,
          labels: { color: axisColor, font: { family: "DM Sans", size: 11 }, boxWidth: 24 },
        },
        tooltip: {
          backgroundColor: "#241E16",
          borderColor: gridColor,
          borderWidth: 1,
          titleColor: axisColor,
          bodyColor: "#F5EDE0",
          padding: 12,
          callbacks: {
            label: (c) => `${c.dataset.label}: ₹${fmtINR(c.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          ticks:  { color: axisColor, maxTicksLimit: 6, autoSkip: true, font: { family: "DM Sans", size: 11 } },
          grid:   { color: "transparent" },
          border: { color: gridColor },
        },
        y: {
          ticks:  { color: axisColor, font: { family: "DM Sans", size: 11 }, callback: v => "₹" + fmtINR(v) },
          grid:   { color: gridColor },
          border: { display: false },
        },
      },
    },
  });

  section.hidden = false;
}

function renderMethodology(fc, bt, drift) {
  const body = document.getElementById("methodology-body");
  if (!body) return;

  const parts = [];

  // Verdict rule explanation
  parts.push(`
    <div class="meth-section">
      <h3 class="meth-heading">Verdict rules</h3>
      <p class="meth-text">Three simple cases — each needs two things to agree to avoid reacting to a single unusual reading.</p>
      <ul class="meth-list">
        <li><strong>Trending down:</strong> price has fallen more than ₹100 over 7 days, and the forecast or 30-day average agrees</li>
        <li><strong>Trending up:</strong> price has risen more than ₹100 over 7 days, and the forecast or 30-day average agrees</li>
        <li><strong>Roughly flat:</strong> everything else — movement within ±₹100 or the two checks disagree</li>
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
        <h3 class="meth-heading">5-day expected range</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">22K estimate</div>
            <div class="meth-stat-value">₹${fmtINR(pred22k)}</div>
            ${hasPI ? `<div class="meth-stat-sub">80% range: ₹${fmtINR(lower)} – ₹${fmtINR(upper)}</div>` : ""}
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Method</div>
            <div class="meth-stat-value">Predict no change</div>
            <div class="meth-stat-sub">Range covers 80% of typical 5-day swings</div>
          </div>
        </div>
        ${fc.target_time ? `<p class="meth-text" style="margin-top:8px">Target: ${fmtIST(fc.target_time)}</p>` : ""}
        <p class="meth-text" style="margin-top:12px">This price range is intentionally wide — it covers the whole 5-day window, not just tomorrow. The range is based on how much prices have typically swung over 5 days in recent history.</p>
      </div>
    `);
  }

  // Direction signal — honest base-rate framing (ADR 019/020)
  if (fc?.chronos_companion?.status === "success") {
    const cc = fc.chronos_companion;
    const acc30f = typeof cc.direction_acc_30f === "number"
      ? `${(cc.direction_acc_30f * 100).toFixed(1)}%`
      : "—";
    parts.push(`
      <div class="meth-section">
        <h3 class="meth-heading">Direction signal</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">Accuracy (recent 30 windows)</div>
            <div class="meth-stat-value">${acc30f}</div>
            <div class="meth-stat-sub">gold rising ~70% of days in our data</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Adjusted to Tanishq prices</div>
            <div class="meth-stat-value">${cc.calibration_applied ? "Yes" : "Not yet"}</div>
            ${!cc.calibration_applied ? `<div class="meth-stat-sub">activates after 30 days of data</div>` : `<div class="meth-stat-sub">${cc.model_version}</div>`}
          </div>
        </div>
        <p class="meth-note">Current price-move alerts use 7-day momentum — not the AI direction model — because the AI's 56% accuracy over all windows doesn't exceed the ~70% base rate. No directional edge is claimed.</p>
      </div>
    `);
  } else if (fc?.chronos_companion?.status === "failed") {
    parts.push(`<p class="meth-text">Direction signal unavailable this cycle.</p>`);
  }

  // "How good is this?" — honest track record panel (Φ8C', ADR 019/020/012)
  if (bt && typeof bt.mae_5d_avg_naive === "number") {
    const n           = bt.n_folds ?? "—";
    const naiveMae    = fmtINR(Math.round(bt.mae_5d_avg_naive));
    const chronosMae  = typeof bt.mae_5d_avg_chronos === "number"
      ? fmtINR(Math.round(bt.mae_5d_avg_chronos))
      : "—";
    const maePctWorse = typeof bt.mae_5d_avg_chronos === "number"
      ? Math.round(((bt.mae_5d_avg_chronos - bt.mae_5d_avg_naive) / bt.mae_5d_avg_naive) * 100)
      : null;
    const dirAll   = typeof bt.dir_acc_5d_chronos === "number"
      ? Math.round(bt.dir_acc_5d_chronos * 100) + "%"
      : "—";
    const pVal     = bt.wilcoxon_signed_rank_p != null
      ? bt.wilcoxon_signed_rank_p.toFixed(4)
      : "—";
    const coverPct = bt.pi_coverage_80_5d_avg != null
      ? Math.round(bt.pi_coverage_80_5d_avg * 100)
      : 87;
    const hl      = fc?.headline;
    const rangeStr = hl && typeof hl.lower === "number" && typeof hl.upper === "number"
      ? `₹${fmtINR(hl.lower)}–₹${fmtINR(hl.upper)}`
      : "the current range";

    parts.push(`
      <div class="meth-section meth-how-good">
        <h3 class="meth-heading">How accurate is this forecast?</h3>

        <p class="meth-text"><strong>The price estimate uses flat-hold (today's price, unchanged)</strong><br>
        Gold prices over 5 days are close to unpredictable; no model we tested could beat simply using today's price as the forecast. Over ${n} windows from 2022–2026:<br>
        &bull; Flat-hold average error: ₹${naiveMae}/g<br>
        ${maePctWorse != null ? `&bull; Time-series AI average error: ₹${chronosMae}/g — ${maePctWorse}% worse (p&thinsp;=&thinsp;${pVal})<br>` : ""}
        Today's price is the forecast.</p>

        <p class="meth-text"><strong>The ${rangeStr} range has held ${coverPct}% of the time</strong><br>
        It reflects the real distribution of 5-day price moves — wide because gold can move sharply.</p>

        <p class="meth-text"><strong>About the direction signal</strong><br>
        Over ${n} windows the AI signal was correct ${dirAll}. Gold has risen roughly 70% of trading days in our data. A naive "always-up" guess clears ~70% without a model — our signal doesn't beat that baseline.<br>
        No directional edge is claimed. Current price-move alerts use 7-day momentum, not the AI.</p>

        <p class="meth-text"><strong>What would change this</strong><br>
        A mixed price regime (roughly equal up/down days) or a momentum signal that consistently clears the base rate in held-out tests. We'll update this section when that happens.</p>
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
        <h3 class="meth-heading">Forecast accuracy — last 7 days</h3>
        <div class="meth-stats">
          <div class="meth-stat">
            <div class="meth-stat-label">Recent avg. error</div>
            <div class="meth-stat-value">${rolling != null ? "₹" + fmtINR(rolling) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Historical avg. error</div>
            <div class="meth-stat-value">${baseMae != null ? "₹" + fmtINR(baseMae) : "—"}</div>
          </div>
          <div class="meth-stat">
            <div class="meth-stat-label">Accuracy drift</div>
            <div class="meth-stat-value">${ratio ?? "—"}</div>
            <div class="meth-stat-sub">${ratioLabel === "retraining recommended" ? "may need recalibration" : ratioLabel}</div>
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

// D3: Lightweight data re-fetch — prices + forecast only.
// Assigns to a local `fresh` first (FIX 2): allReadings is only committed
// after both fetches resolve, keeping state consistent on partial failure.
async function refreshData() {
  const btn = document.getElementById("refresh-btn");
  if (btn) { btn.classList.add("refresh-btn--spinning"); btn.disabled = true; }
  try {
    const fresh = await load();
    const fc    = await loadJSON(FORECAST_URL).catch(() => null);
    allReadings  = fresh;
    renderFreshness(allReadings);
    renderComparisons(allReadings);
    renderHistory(allReadings);
    renderChart(allReadings, currentRange);
    renderHero(allReadings, fc);
    renderStaleBanner(fc);
    renderModelSignal(fc, allReadings);
    // Ψ3C.2: stagger visible data cards to confirm refresh visually
    staggerEnter([
      document.getElementById("comparison-section"),
      document.querySelector(".karat-strip"),
      document.getElementById("model-signal-section"),
    ]);
  } catch (err) {
    console.error("Refresh failed:", err);
  } finally {
    if (btn) { btn.classList.remove("refresh-btn--spinning"); btn.disabled = false; }
  }
}

function bindRangeToggle() {
  const buttons = document.querySelectorAll(".range-toggle button");
  buttons.forEach(btn => {
    btn.addEventListener("click", () => {
      if (btn.classList.contains("active")) return; // already selected — no-op
      buttons.forEach(b => { b.classList.remove("active"); b.setAttribute("aria-selected", "false"); });
      btn.classList.add("active");
      btn.setAttribute("aria-selected", "true");
      currentRange = btn.dataset.range;
      // Ψ3C.2: chart fade — fade out 150ms, render new data, fade in 200ms
      const wrap = document.querySelector(".chart-wrap");
      if (wrap) {
        wrap.classList.add("chart-fade-out");
        setTimeout(() => {
          renderChart(allReadings, currentRange);
          wrap.classList.remove("chart-fade-out");
          wrap.classList.add("chart-fade-in");
          wrap.addEventListener("animationend", () => wrap.classList.remove("chart-fade-in"), { once: true });
        }, 150);
      } else {
        renderChart(allReadings, currentRange);
      }
    });
  });
}

// ─── BOTTOM NAV SCROLLSPY ─────────────────────────────────────────────────────
// Option B: scroll-anchor nav. IntersectionObserver tracks which section is in view.
// Click handler uses scrollIntoView({behavior:"smooth"}); CSS scroll-margin-top handles
// the fixed header offset so sections don't slide under it.

const NAV_SECTIONS = [
  "section-home",
  "section-trend",
  "section-history",
  "section-info",
];

function initBottomNav() {
  const navItems = document.querySelectorAll(".bottom-nav-item");

  // Click: prevent default anchor jump, use smooth scrollIntoView instead.
  // CSS scroll-margin-top on each section accounts for the 52px header.
  //
  // iOS race fix (WI-Φ9B-3): the old order was scrollIntoView() then el.open=true.
  // On iOS, el.open=true triggers a layout reflow that cancels the in-flight smooth
  // scroll, so tap 1 opened but didn't scroll and tap 2 scrolled. Fix: open FIRST,
  // then scrollIntoView() inside one requestAnimationFrame so the expanded DETAILS
  // layout is stable before the browser computes the scroll target position.
  // Single rAF is sufficient — if a future iOS regression appears, escalate to
  // double-rAF: requestAnimationFrame(() => requestAnimationFrame(() => scroll...)).
  navItems.forEach(item => {
    item.addEventListener("click", (e) => {
      e.preventDefault();
      const sectionId = item.dataset.section;
      const el = document.getElementById(sectionId);
      if (!el) return;
      if (el.tagName === "DETAILS" && !el.open) el.open = true;
      requestAnimationFrame(() => {
        el.scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  });

  // Scrollspy: set active nav item as the user scrolls.
  // Strategy: last section whose top edge is ≤ (scrollY + HEADER_H + threshold).
  // requestAnimationFrame throttles scroll handler to one update per frame.
  // Read actual header height so standalone mode (taller header) is accounted for.
  const HEADER_H = document.getElementById("utility-row")?.getBoundingClientRect().height ?? 52;
  const THRESHOLD = 24; // px below header before marking a section active

  const sectionEls = NAV_SECTIONS
    .map(id => document.getElementById(id))
    .filter(Boolean);

  function updateActiveNav() {
    const scrollY = window.scrollY;
    const trigger = scrollY + HEADER_H + THRESHOLD;
    let activeId = sectionEls[0]?.id;

    for (const el of sectionEls) {
      // getBoundingClientRect().top + scrollY = element's offset from document top
      const elTop = el.getBoundingClientRect().top + scrollY;
      if (elTop <= trigger) activeId = el.id;
    }

    navItems.forEach(item => {
      const isActive = item.dataset.section === activeId;
      item.classList.toggle("active", isActive);
      item.setAttribute("aria-current", isActive ? "page" : "false");
    });
  }

  let ticking = false;
  window.addEventListener("scroll", () => {
    if (!ticking) {
      requestAnimationFrame(() => { updateActiveNav(); ticking = false; });
      ticking = true;
    }
  }, { passive: true });

  updateActiveNav(); // set initial state
}

// ─── PULL-TO-REFRESH — Ψ3C.2 ────────────────────────────────────────────────
//
// Gesture: touch at scrollY=0 and drag down ≥60px → call refreshData().
// iOS risk: overscroll-behavior-y:none only works Safari 16+; older iOS still
// rubber-bands. Real-device test required. Fallback: ↻ button still works.
//
// Safety rules (per spec):
//   - preventDefault() ONLY when actively pulling (deltaY > 0 AND scrollY === 0)
//   - overscroll-behavior-y:none (html.ptr-pulling class) ONLY during active pull
//   - No haptics (navigator.vibrate unreliable on iOS) — visual feedback only

function initPullToRefresh() {
  const indicator = document.getElementById("pull-indicator");
  const mainEl    = document.querySelector("main");
  if (!indicator || !mainEl) return;

  const THRESHOLD = 60; // px — minimum pull distance to trigger refresh

  let startY    = 0;
  let isPulling = false;

  function endPull(doRefresh) {
    isPulling = false;
    startY    = 0;
    document.documentElement.classList.remove("ptr-pulling");

    // Snap main back with a spring-like transition
    mainEl.style.transition = "transform 300ms ease-out";
    mainEl.style.transform  = "";
    mainEl.addEventListener("transitionend", () => { mainEl.style.transition = ""; }, { once: true });

    if (doRefresh) {
      indicator.classList.add("pull-indicator--loading");
      indicator.querySelector(".pull-icon").textContent = "↻";
      refreshData().finally(() => {
        indicator.style.opacity = "0";
        indicator.classList.remove("pull-indicator--loading", "pull-indicator--ready");
      });
    } else {
      indicator.style.opacity = "0";
      indicator.classList.remove("pull-indicator--ready");
    }
  }

  document.addEventListener("touchstart", (e) => {
    if (window.scrollY > 2) return; // only arm when at scroll top
    startY    = e.touches[0].clientY;
    isPulling = false;
  }, { passive: true });

  document.addEventListener("touchmove", (e) => {
    if (!startY) return;
    if (window.scrollY > 2) { startY = 0; return; } // scrolled away — disarm

    const deltaY = e.touches[0].clientY - startY;
    if (deltaY <= 0) {
      if (isPulling) endPull(false); // reversed direction — cancel
      return;
    }

    // Active pull: prevent native scroll/rubber-band for this gesture
    e.preventDefault();

    if (!isPulling) {
      isPulling = true;
      document.documentElement.classList.add("ptr-pulling");
    }

    // Dampen the translation: user moves 2px → content moves 1px (max 60px)
    const translatePx = Math.min(deltaY * 0.5, THRESHOLD);
    mainEl.style.transform = `translateY(${translatePx}px)`;

    const ratio = Math.min(deltaY / THRESHOLD, 1);
    indicator.style.opacity = String(ratio);

    if (ratio >= 1) {
      if (!indicator.classList.contains("pull-indicator--ready")) {
        indicator.classList.add("pull-indicator--ready");
        indicator.querySelector(".pull-icon").textContent = "↻"; // release cue
      }
    } else {
      indicator.classList.remove("pull-indicator--ready");
      indicator.querySelector(".pull-icon").textContent = "↓";
    }
  }, { passive: false }); // passive: false required to call preventDefault()

  document.addEventListener("touchend", (e) => {
    if (!isPulling) return;
    const releaseY = e.changedTouches[0]?.clientY ?? (startY + 0);
    endPull(releaseY - startY >= THRESHOLD);
  }, { passive: true });
}

// ─── INIT ─────────────────────────────────────────────────────────────────────

(async function init() {
  bindRangeToggle();
  initBottomNav();
  initPullToRefresh();

  // Ambient header: add elevation (.scrolled → border + shadow) only when content
  // is scrolling under the header. Passive listener — no layout work on scroll.
  const appHeader = document.getElementById("utility-row");
  if (appHeader) {
    const onScroll = () => appHeader.classList.toggle("scrolled", window.scrollY > 0);
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll(); // set initial state for pages loaded at non-zero scroll position
  }

  // D3: Refresh button — works in both browser and standalone mode.
  const refreshBtn = document.getElementById("refresh-btn");
  if (refreshBtn) refreshBtn.addEventListener("click", refreshData);

  // D4/D5: iOS help panel — revealed only in standalone mode.
  const helpBtn   = document.getElementById("pwa-help-btn");
  const helpPanel = document.getElementById("pwa-help-panel");
  const helpClose = document.getElementById("pwa-help-close");

  if (IS_STANDALONE) {
    if (helpBtn) {
      helpBtn.hidden = false;
      helpBtn.addEventListener("click", () => {
        if (helpPanel) helpPanel.hidden = !helpPanel.hidden;
      });
    }
    if (helpClose) {
      // ✕ sets the dismissed flag (FIX 1) — auto-open won't re-fire this session.
      // Manual ? toggle does not set the flag; only explicit dismissal does.
      helpClose.addEventListener("click", () => {
        pwaHelpDismissed = true;
        if (helpPanel) helpPanel.hidden = true;
      });
    }
  }

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
  renderModelSignal(fc, allReadings);  // first render — coverage% uses fallback until backtest loads

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

  const btData = bt.status === "fulfilled" ? bt.value : null;
  renderModelSignal(fc, allReadings, btData);  // re-render with coverage% from backtest
  renderCommentary(commentary.status === "fulfilled" ? commentary.value : null);
  renderForecastVsActual(btData);
  renderMethodology(
    fc,
    btData,
    drift.status === "fulfilled" ? drift.value : null,
  );

  // Dismiss chart callout when tapping outside the chart canvas (Φ8C'/Ψ3C.3)
  const chartCanvas = document.getElementById("chart");
  document.addEventListener("click", (e) => {
    if (chartCanvas && e.target !== chartCanvas && chartPinnedIndex !== null) {
      chartPinnedIndex = null;
      if (chart) chart.update("none");
    }
  }, { passive: true, capture: false });
})();
