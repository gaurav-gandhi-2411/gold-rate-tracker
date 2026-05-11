// app.js — fetches prices.json and renders the UI.

const DATA_URL = "data/prices.json";
const FORECAST_URL = "data/forecast.json";
const BACKTEST_URL = "data/backtest.json";
const COMMENTARY_URL = "data/commentary.json";
const DRIFT_URL = "data/drift_metrics.json";

const fmtINR = (n) =>
  typeof n === "number"
    ? n.toLocaleString("en-IN", { maximumFractionDigits: 0 })
    : "—";

function rupee(n) {
  if (typeof n !== "number") return "—";
  return `<span class="rupee">₹</span>${fmtINR(n)}`;
}

function fmtRelative(iso) {
  const d = new Date(iso);
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.round(diff / 60)} min ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)} h ago`;
  return `${Math.round(diff / 86400)} d ago`;
}

function fmtDate(iso) {
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function fmtDateShort(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

let chart = null;
let allReadings = [];

async function loadJSON(url) {
  // Cache-bust so we always get the latest committed JSON.
  const res = await fetch(`${url}?t=${Date.now()}`);
  if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
  return res.json();
}

async function load() {
  const data = await loadJSON(DATA_URL);
  data.sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
  return data;
}

function renderHero(readings) {
  const heroPrice = document.getElementById("hero-price");
  const heroChange = document.getElementById("hero-change");
  const updated = document.getElementById("updated");
  const r22 = document.getElementById("rate-22");
  const r24 = document.getElementById("rate-24");
  const r18 = document.getElementById("rate-18");

  if (readings.length === 0) {
    heroPrice.innerHTML = "—";
    updated.textContent = "Awaiting first reading";
    return;
  }

  const latest = readings[readings.length - 1];
  const prev = readings.length > 1 ? readings[readings.length - 2] : null;

  heroPrice.innerHTML = rupee(latest["22k"]);
  r22.innerHTML = rupee(latest["22k"]);
  r24.innerHTML = rupee(latest["24k"]);
  r18.innerHTML = rupee(latest["18k"]);
  const freshText = `Updated ${fmtRelative(latest.timestamp)}`;
  updated.textContent = freshText;
  const pill = document.getElementById("updated-pill");
  if (pill) pill.textContent = freshText;

  if (prev && typeof prev["22k"] === "number") {
    const delta = latest["22k"] - prev["22k"];
    const dir = delta > 0 ? "up" : delta < 0 ? "down" : "flat";
    heroChange.hidden = false;
    heroChange.dataset.direction = dir;
    heroChange.querySelector(".amount").textContent =
      delta === 0 ? "no change" : `₹${fmtINR(Math.abs(delta))}`;
  } else {
    heroChange.hidden = true;
  }
}

function renderHistory(readings) {
  const tbody = document.getElementById("history-body");
  if (readings.length === 0) {
    tbody.innerHTML =
      `<tr><td colspan="5" class="empty">No readings yet. The bot will populate this soon.</td></tr>`;
    return;
  }

  // Show newest first, capped at 50 rows.
  const rows = [...readings].reverse().slice(0, 50);
  tbody.innerHTML = rows
    .map((r, i) => {
      const next = rows[i + 1]; // older entry (since reversed)
      let deltaCell = '<span class="delta-flat">—</span>';
      if (next && typeof next["22k"] === "number" && typeof r["22k"] === "number") {
        const d = r["22k"] - next["22k"];
        if (d > 0)      deltaCell = `<span class="delta-up">+₹${fmtINR(d)}</span>`;
        else if (d < 0) deltaCell = `<span class="delta-down">−₹${fmtINR(Math.abs(d))}</span>`;
        else            deltaCell = `<span class="delta-flat">±0</span>`;
      }
      return `<tr>
        <td>${fmtDate(r.timestamp)}</td>
        <td class="num">${rupee(r["22k"])}</td>
        <td class="num">${rupee(r["24k"])}</td>
        <td class="num">${rupee(r["18k"])}</td>
        <td class="num">${deltaCell}</td>
      </tr>`;
    })
    .join("");
}

function renderChart(readings, range) {
  let filtered = readings;
  if (range !== "all") {
    const days = parseInt(range, 10);
    const cutoff = Date.now() - days * 86400 * 1000;
    filtered = readings.filter((r) => new Date(r.timestamp).getTime() >= cutoff);
  }

  const labels = filtered.map((r) => fmtDate(r.timestamp));
  const data22 = filtered.map((r) => r["22k"]);

  const css = getComputedStyle(document.body);
  const gold = css.getPropertyValue("--gold").trim() || "#c8a456";
  const cream = css.getPropertyValue("--cream-mute").trim() || "#8a8273";
  const line = css.getPropertyValue("--line").trim() || "#2e2a23";

  const ctx = document.getElementById("chart");

  if (chart) chart.destroy();

  // Gradient fill under the line.
  const c2d = ctx.getContext("2d");
  const gradient = c2d.createLinearGradient(0, 0, 0, ctx.height || 320);
  gradient.addColorStop(0, "rgba(200,164,86,0.28)");
  gradient.addColorStop(1, "rgba(200,164,86,0.00)");

  chart = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "22K (₹/g)",
          data: data22,
          borderColor: gold,
          backgroundColor: gradient,
          fill: true,
          borderWidth: 2,
          pointRadius: filtered.length > 30 ? 0 : 3,
          pointBackgroundColor: gold,
          pointBorderWidth: 0,
          tension: 0.3,
          spanGaps: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "#14110d",
          borderColor: line,
          borderWidth: 1,
          titleColor: cream,
          bodyColor: "#f5ede0",
          padding: 12,
          callbacks: {
            label: (ctx) => `22K: ₹${fmtINR(ctx.parsed.y)}`,
          },
        },
      },
      scales: {
        x: {
          ticks: {
            color: cream,
            maxTicksLimit: 6,
            font: { family: "DM Sans" },
          },
          grid: { color: "transparent" },
          border: { color: line },
        },
        y: {
          ticks: {
            color: cream,
            font: { family: "DM Sans" },
            callback: (v) => "₹" + fmtINR(v),
          },
          grid: { color: line },
          border: { display: false },
        },
      },
    },
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
  } catch (_) {
    return iso;
  }
}

function fmtISTTime(iso) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit", minute: "2-digit", hour12: true,
    }).format(new Date(iso));
  } catch (_) {
    return iso;
  }
}

function renderForecast(fc) {
  const section     = document.getElementById("forecast-section");
  const priceEl     = document.getElementById("forecast-price");
  const warmupEl    = document.getElementById("forecast-warmup");
  const intervalEl  = document.getElementById("forecast-interval");
  const targetEl    = document.getElementById("forecast-target");
  const generatedEl = document.getElementById("forecast-generated");
  const whyEl       = document.getElementById("forecast-why");
  const whyBodyEl   = document.getElementById("forecast-why-body");

  if (!fc || typeof fc.predicted_22k !== "number") {
    section.hidden = true;
    return;
  }

  priceEl.innerHTML = rupee(fc.predicted_22k);

  if (fc.warmup) {
    const needed = Math.max(0, 56 - (fc.real_readings_count || 0));
    warmupEl.textContent =
      `\u{1F4CA} Calibrating · forecast may differ from live until ~${needed} more readings`;
    warmupEl.hidden = false;
  } else {
    warmupEl.hidden = true;
  }

  const hasInterval = typeof fc.lower === "number" && typeof fc.upper === "number";
  if (hasInterval) {
    intervalEl.innerHTML =
      `80% interval: <span class="rupee">₹</span>${fmtINR(fc.lower)} – <span class="rupee">₹</span>${fmtINR(fc.upper)}`;
  } else {
    intervalEl.textContent = "";
  }

  targetEl.textContent = fc.target_time
    ? `For ${fmtIST(fc.target_time)}`
    : "";
  generatedEl.textContent = fc.predicted_at
    ? `Generated ${fmtISTTime(fc.predicted_at)} IST`
    : "";

  if (whyEl && whyBodyEl && fc.explanation) {
    whyBodyEl.textContent = fc.explanation;
    whyEl.hidden = false;
  } else if (whyEl) {
    whyEl.hidden = true;
  }

  section.hidden = false;
}

function renderCommentary(entries) {
  const section = document.getElementById("commentary-section");
  const textEl = document.getElementById("commentary-text");
  const metaEl = document.getElementById("commentary-meta");

  if (!Array.isArray(entries) || entries.length === 0) {
    section.hidden = true;
    return;
  }

  const latest = entries[entries.length - 1];
  if (!latest || !latest.text) {
    section.hidden = true;
    return;
  }

  textEl.textContent = latest.text;
  metaEl.textContent = latest.ts ? fmtRelative(latest.ts) : "";
  section.hidden = false;
}

function renderModelStats(bt) {
  const section = document.getElementById("model-section");

  if (!bt || !bt.model) {
    section.hidden = true;
    return;
  }

  const m = bt.model;
  const b = bt.baseline;

  const maeEl = document.getElementById("model-mae");
  const maeVsEl = document.getElementById("model-mae-vs");
  const dirEl = document.getElementById("model-dir");
  const dirVsEl = document.getElementById("model-dir-vs");
  const foldsEl = document.getElementById("model-folds");

  maeEl.textContent = typeof m.mae === "number" ? `₹${fmtINR(m.mae)}` : "—";
  if (b && typeof b.mae === "number" && typeof m.mae === "number") {
    const diff = Math.abs(m.mae - b.mae);
    const sign = m.mae <= b.mae ? "−" : "+";
    maeVsEl.textContent = `${sign}₹${fmtINR(diff)} vs naive`;
  }

  dirEl.textContent =
    typeof m.direction_acc === "number"
      ? `${Math.round(m.direction_acc * 100)}%`
      : "—";
  if (b && typeof b.direction_acc === "number") {
    const diff = Math.round((m.direction_acc - b.direction_acc) * 100);
    const sign = diff >= 0 ? "+" : "";
    dirVsEl.textContent = `${sign}${diff}pp vs naive`;
  }

  foldsEl.textContent = typeof bt.folds === "number" ? bt.folds : "—";
  section.hidden = false;
}

function renderLivePerf(entries) {
  const section = document.getElementById("live-perf-section");
  if (!Array.isArray(entries) || entries.length === 0) {
    section.hidden = true;
    return;
  }

  const now = Date.now();
  const cutoff7d = now - 7 * 24 * 3600 * 1000;
  const recent = entries.filter(
    (e) => e.residual != null && new Date(e.ts).getTime() >= cutoff7d
  );

  const rollingMae =
    recent.length > 0
      ? recent.reduce((s, e) => s + Math.abs(e.residual), 0) / recent.length
      : null;

  // baseline_mae from most recent entry that has it
  const withBaseline = [...entries].reverse().find((e) => e.baseline_mae != null);
  const baselineMae = withBaseline ? withBaseline.baseline_mae : null;

  const rollingEl = document.getElementById("perf-rolling-mae");
  const baselineEl = document.getElementById("perf-baseline-mae");
  const ratioEl = document.getElementById("perf-drift-ratio");
  const subEl = document.getElementById("perf-drift-sub");

  rollingEl.textContent = rollingMae != null ? `₹${fmtINR(Math.round(rollingMae))}` : "—";
  baselineEl.textContent = baselineMae != null ? `₹${fmtINR(Math.round(baselineMae))}` : "—";

  if (rollingMae != null && baselineMae != null && baselineMae > 0) {
    const ratio = rollingMae / baselineMae;
    ratioEl.textContent = ratio.toFixed(2);
    ratioEl.className = "live-perf-value";
    if (ratio < 1.0) {
      ratioEl.classList.add("drift-green");
      subEl.textContent = "on track";
    } else if (ratio <= 1.5) {
      ratioEl.classList.add("drift-yellow");
      subEl.textContent = "watch";
    } else {
      ratioEl.classList.add("drift-red");
      subEl.textContent = "retraining recommended";
    }
  } else {
    ratioEl.textContent = "—";
    subEl.textContent = "";
  }

  section.hidden = false;
}

function bindRangeToggle() {
  const buttons = document.querySelectorAll(".range-toggle button");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      buttons.forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      renderChart(allReadings, btn.dataset.range);
    });
  });
}

(async function init() {
  bindRangeToggle();

  // Load prices (required)
  try {
    allReadings = await load();
  } catch (err) {
    console.error(err);
    document.getElementById("hero-price").textContent = "Error";
    document.getElementById("updated").textContent = "Could not load data";
    document.getElementById("history-body").innerHTML =
      `<tr><td colspan="5" class="empty">Failed to load prices.json. If you just deployed, run the workflow once from the Actions tab.</td></tr>`;
    return;
  }
  renderHero(allReadings);
  renderHistory(allReadings);
  renderChart(allReadings, "7");

  // Load ML/LLM data (all optional — degrade gracefully on any failure)
  const [fc, bt, commentary, drift] = await Promise.allSettled([
    loadJSON(FORECAST_URL),
    loadJSON(BACKTEST_URL),
    loadJSON(COMMENTARY_URL),
    loadJSON(DRIFT_URL),
  ]);

  renderForecast(fc.status === "fulfilled" ? fc.value : null);
  renderLivePerf(drift.status === "fulfilled" ? drift.value : null);
  renderModelStats(bt.status === "fulfilled" ? bt.value : null);
  renderCommentary(commentary.status === "fulfilled" ? commentary.value : null);
})();
