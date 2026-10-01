/* Weather for the active site (Open-Meteo) — load-on-demand from the top
   bar chip. Nothing is fetched until the user clicks (unless "always load
   on startup" is on). The hourly response is normalized into observing rows
   so another provider can be added without changing the panel contract. */

import { $, api, todayLocal, effZone } from "./util.js";
import { S } from "./state.js";

const WX_STATE = { data: null, loading: false, error: false, airError: false };

const WMO_INFO = {
  0: ["☀️", "Clear sky"], 1: ["🌤️", "Mainly clear"], 2: ["⛅", "Partly cloudy"], 3: ["☁️", "Overcast"],
  45: ["🌫️", "Fog"], 48: ["🌫️", "Icy fog"],
  51: ["🌦️", "Light drizzle"], 53: ["🌦️", "Drizzle"], 55: ["🌦️", "Dense drizzle"],
  56: ["🌧️", "Freezing drizzle"], 57: ["🌧️", "Freezing drizzle"],
  61: ["🌧️", "Light rain"], 63: ["🌧️", "Rain"], 65: ["🌧️", "Heavy rain"],
  66: ["🌧️", "Freezing rain"], 67: ["🌧️", "Freezing rain"],
  71: ["🌨️", "Light snow"], 73: ["🌨️", "Snow"], 75: ["🌨️", "Heavy snow"], 77: ["🌨️", "Snow grains"],
  80: ["🌦️", "Rain showers"], 81: ["🌦️", "Rain showers"], 82: ["🌧️", "Violent rain showers"],
  85: ["🌨️", "Snow showers"], 86: ["🌨️", "Snow showers"],
  95: ["⛈️", "Thunderstorm"], 96: ["⛈️", "Thunderstorm, hail"], 99: ["⛈️", "Thunderstorm, hail"],
};
function wxInfo(code) { return WMO_INFO[code] || ["🌡️", "Condition " + code]; }

function wxNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
function wxPercent(value) {
  const n = wxNumber(value);
  return n == null ? null : Math.max(0, Math.min(100, n));
}
function wxFixed(value, digits = 0) {
  const n = wxNumber(value);
  return n == null ? "—" : n.toFixed(digits);
}
function wxNextDateKey(key) {
  const [y, m, d] = String(key).split("-").map(Number);
  const date = new Date(Date.UTC(y, m - 1, d + 1));
  return `${date.getUTCFullYear()}-${String(date.getUTCMonth() + 1).padStart(2, "0")}-${String(date.getUTCDate()).padStart(2, "0")}`;
}
function wxWallTimeKey(date) {
  const parts = new Intl.DateTimeFormat("sv-SE", {
    timeZone: effZone(), year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const part = (type) => (parts.find(p => p.type === type) || {}).value || "00";
  return `${part("year")}-${part("month")}-${part("day")}T${part("hour")}:${part("minute")}`;
}
function wxSelectedDateKey() {
  return $("#dateInput")?.value || todayLocal();
}
function activeSite() {
  if (!S.cfg || !S.cfg.sites) return null;
  return S.cfg.sites.find(s => s.id === S.cfg.active_site_id) || S.cfg.sites[0] || null;
}
function wxTimezone() {
  const site = activeSite();
  return (S.cfg && S.cfg.timezone) || (site && site.tz) || "auto";
}
function wxNightBounds() {
  const N = S.night;
  if (N && N.dusk && N.dawn) {
    const start = wxWallTimeKey(new Date(N.dusk + "Z"));
    const end = wxWallTimeKey(new Date(N.dawn + "Z"));
    if (end > start) return { start, end, label: `${start.slice(11)}–${end.slice(11)}` };
  }
  const selected = wxSelectedDateKey();
  const next = wxNextDateKey(selected);
  return { start: `${selected}T20:00`, end: `${next}T04:00`, label: "20:00–04:00" };
}
async function wxFetchJSON(url) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), 15000);
  try {
    const response = await fetch(url, { signal: ac.signal });
    if (!response.ok) throw new Error("HTTP " + response.status);
    return await response.json();
  } finally {
    clearTimeout(timer);
  }
}
function wxRows(data) {
  const h = data && data.hourly || {};
  const aq = data && data.air_quality && data.air_quality.hourly || {};
  const times = Array.isArray(h.time) ? h.time : [];
  const aqIndex = new Map((Array.isArray(aq.time) ? aq.time : []).map((t, i) => [t, i]));
  const value = (key, i) => wxNumber(Array.isArray(h[key]) ? h[key][i] : null);
  return times.map((time, i) => {
    const ai = aqIndex.get(time);
    const aqValue = (key) => ai == null || !Array.isArray(aq[key]) ? null : wxNumber(aq[key][ai]);
    const temperature = value("temperature_2m", i);
    const dewPoint = value("dew_point_2m", i);
    return {
      time,
      weatherCode: value("weather_code", i),
      temperature,
      dewPoint,
      dewSpread: temperature != null && dewPoint != null ? temperature - dewPoint : null,
      apparentTemperature: value("apparent_temperature", i),
      humidity: wxPercent(value("relative_humidity_2m", i)),
      cloudTotal: wxPercent(value("cloud_cover", i)),
      cloudLow: wxPercent(value("cloud_cover_low", i)),
      cloudMid: wxPercent(value("cloud_cover_mid", i)),
      cloudHigh: wxPercent(value("cloud_cover_high", i)),
      rainProbability: wxPercent(value("precipitation_probability", i)),
      precipitation: value("precipitation", i),
      rain: value("rain", i),
      showers: value("showers", i),
      snowfall: value("snowfall", i),
      visibility: value("visibility", i),
      wind: value("wind_speed_10m", i),
      gusts: value("wind_gusts_10m", i),
      dust: aqValue("dust"),
      aerosol: aqValue("aerosol_optical_depth"),
      pm25: aqValue("pm2_5"),
    };
  }).filter(row => row.time);
}
function wxRowsForNight(data) {
  const rows = wxRows(data);
  const { start, end } = wxNightBounds();
  const startHour = `${start.slice(0, 13)}:00`;
  const endHour = `${end.slice(0, 13)}:00`;
  const endInclusive = end.slice(14, 16) !== "00";
  return rows.filter(row => row.time >= startHour &&
    (endInclusive ? row.time <= endHour : row.time < endHour));
}
function wxDewRisk(row) {
  if (row.dewSpread == null) return { label: "—", cls: "unknown" };
  if (row.dewSpread <= 1.5) return { label: "High", cls: "bad" };
  if (row.dewSpread <= 3) return { label: "Moderate", cls: "warn" };
  return { label: "Low", cls: "good" };
}
function wxRainState(row) {
  const code = row.weatherCode == null ? 0 : row.weatherCode;
  if (code >= 95) return { label: "Storm", cls: "bad" };
  if ((row.precipitation != null && row.precipitation > 0.05) ||
      (row.rain != null && row.rain > 0.05) ||
      (row.showers != null && row.showers > 0.05) ||
      (row.snowfall != null && row.snowfall > 0.05)) {
    const snow = (code >= 71 && code <= 77) || (code >= 85 && code <= 86);
    return { label: snow ? "Snow" : "Rain", cls: "bad" };
  }
  if (row.rainProbability != null && row.rainProbability >= 35) {
    return { label: "Possible", cls: "warn" };
  }
  return { label: "Dry", cls: "good" };
}
function wxSkyClarity(row) {
  if (row.visibility == null && row.aerosol == null) return { label: "Unknown", cls: "unknown" };
  const km = row.visibility == null ? null : row.visibility / 1000;
  const aod = row.aerosol;
  return {
    label: (km != null && km < 2) || (aod != null && aod >= 0.5) ? "Poor" :
      (km != null && km < 5) || (aod != null && aod >= 0.25) ? "Fair" : "Good",
    cls: (km != null && km < 2) || (aod != null && aod >= 0.5) ? "bad" :
      (km != null && km < 5) || (aod != null && aod >= 0.25) ? "warn" : "good",
  };
}
function wxRowScore(row) {
  let score = 100;
  if (row.cloudTotal != null) score -= row.cloudTotal * 0.55;
  if (row.rainProbability != null) score -= row.rainProbability * 0.25;
  const rain = wxRainState(row);
  if (rain.cls === "bad") score -= 40;
  else if (rain.cls === "warn") score -= 8;
  const dew = wxDewRisk(row);
  if (dew.cls === "bad") score -= 12;
  else if (dew.cls === "warn") score -= 6;
  const clarity = wxSkyClarity(row);
  if (clarity.cls === "bad") score -= 12;
  else if (clarity.cls === "warn") score -= 5;
  return Math.max(0, Math.min(100, Math.round(score)));
}
function wxForecastSummary(rows) {
  const clouds = rows.map(r => r.cloudTotal).filter(v => v != null);
  const rain = rows.map(r => r.rainProbability).filter(v => v != null);
  const dewHigh = rows.find(r => wxDewRisk(r).cls === "bad");
  const dewWarn = rows.find(r => wxDewRisk(r).cls === "warn");
  const clarity = rows.map(r => wxSkyClarity(r));
  const maxRain = rain.length ? Math.max(...rain) : null;
  const maxCloud = clouds.length ? Math.max(...clouds) : null;
  const wet = rows.some(r => wxRainState(r).cls === "bad");
  const poorClarity = clarity.some(r => r.cls === "bad");
  const fairClarity = clarity.some(r => r.cls === "warn");
  const scores = rows.map(wxRowScore);
  const score = scores.length ? Math.round(scores.reduce((a, b) => a + b, 0) / scores.length) : null;
  const red = wet || (maxRain != null && maxRain >= 55) || (maxCloud != null && maxCloud >= 85);
  const warn = !red && ((maxRain != null && maxRain >= 20) || (maxCloud != null && maxCloud >= 20) ||
    !!dewHigh || !!dewWarn || poorClarity || fairClarity);
  const status = red ? { label: "Poor", cls: "bad" } :
    warn ? { label: "Fair", cls: "warn" } :
      rows.length ? { label: "Good", cls: "good" } : { label: "Unknown", cls: "unknown" };
  const firstTime = row => row ? row.time.slice(11, 16) : "";
  return {
    cloud: clouds.length ? `${Math.min(...clouds)}–${Math.max(...clouds)}%` : "—",
    rain: maxRain == null ? "—" : wet ? `${Math.round(maxRain)}% · rain risk` :
      maxRain >= 35 ? `${Math.round(maxRain)}% possible` : `${Math.round(maxRain)}% max`,
    clarity: poorClarity ? "Poor" : fairClarity ? "Fair" : rows.length ? "Good" : "Unknown",
    clarityCls: poorClarity ? "bad" : fairClarity ? "warn" : rows.length ? "good" : "unknown",
    dew: dewHigh ? `High after ${firstTime(dewHigh)}` : dewWarn ? `Moderate after ${firstTime(dewWarn)}` : rows.length ? "Low" : "—",
    dewCls: dewHigh ? "bad" : dewWarn ? "warn" : rows.length ? "good" : "unknown",
    status, score,
    detail: red ? (wet ? "Rain or storm risk" : "Very cloudy outlook") :
      warn ? "Some cloud, dew, or haze possible" : rows.length ? "Clear and dry outlook" : "No night forecast available",
  };
}
function wxHourlyMarkup(rows) {
  return rows.map(row => {
    const [icon] = wxInfo(row.weatherCode);
    const dew = wxDewRisk(row);
    const rain = wxRainState(row);
    const clarity = wxSkyClarity(row);
    const cloud = row.cloudTotal == null ? "—" : `${Math.round(row.cloudTotal)}%`;
    const rainText = row.rainProbability == null ? rain.label : `${Math.round(row.rainProbability)}%`;
    const rainAmount = row.precipitation == null ? "" : `<small>${wxFixed(row.precipitation, 1)} mm</small>`;
    const cloudWidth = row.cloudTotal == null ? 0 : Math.round(row.cloudTotal);
    return `<div class="wx-hour" title="${row.time.slice(0, 16).replace("T", " ")} site time">
      <div class="wx-hour-time"><span>${row.time.slice(11, 16)}</span><span class="wx-hour-icon">${icon}</span></div>
      <div class="wx-hour-metric"><small>Cloud</small><span class="wx-meter"><i style="width:${cloudWidth}%"></i></span><b>${cloud}</b></div>
      <div class="wx-hour-metric"><small>Rain</small><b class="wx-${rain.cls}">${rainText}</b>${rainAmount}</div>
      <div class="wx-hour-metric"><small>Clarity</small><b class="wx-${clarity.cls}">${clarity.label}</b></div>
      <div class="wx-hour-metric"><small>Dew</small><b class="wx-${dew.cls}">${dew.label}</b></div>
    </div>`;
  }).join("");
}

// DE-06: every request carries a generation. A site/date change can happen
// while both provider calls are in flight; a late response must not repaint a
// panel labelled with a different site.
let _wxGen = 0;

async function loadWeather() {
  const chip = $("#wxChip");
  if (!chip) return;
  const gen = ++_wxGen;
  WX_STATE.loading = true; WX_STATE.error = false; WX_STATE.airError = false;
  wxRenderChip();
  const site = activeSite();
  const lat = site ? site.lat : (S.cfg && S.cfg.lat);
  const lon = site ? site.lon : (S.cfg && S.cfg.lon);
  if (lat == null || lon == null) {
    WX_STATE.error = true; WX_STATE.loading = false;
    wxRenderChip();
    return;
  }
  const tz = encodeURIComponent(wxTimezone());
  const url = "https://api.open-meteo.com/v1/forecast?latitude=" + lat + "&longitude=" + lon +
    "&hourly=temperature_2m,apparent_temperature,relative_humidity_2m,dew_point_2m,precipitation_probability,precipitation,rain,showers,snowfall,weather_code,cloud_cover,cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m" +
    "&timezone=" + tz + "&forecast_days=4";
  const airUrl = "https://air-quality-api.open-meteo.com/v1/air-quality?latitude=" + lat + "&longitude=" + lon +
    "&hourly=pm10,pm2_5,dust,aerosol_optical_depth&timezone=" + tz + "&forecast_days=4";
  try {
    const [weather, air] = await Promise.allSettled([wxFetchJSON(url), wxFetchJSON(airUrl)]);
    if (gen !== _wxGen) return;
    if (weather.status !== "fulfilled") throw weather.reason || new Error("weather unavailable");
    WX_STATE.data = weather.value;
    WX_STATE.data.air_quality = air.status === "fulfilled" ? air.value : null;
    WX_STATE.airError = air.status !== "fulfilled";
  } catch {
    if (gen !== _wxGen) return;
    WX_STATE.error = true; WX_STATE.data = null;
  } finally {
    if (gen === _wxGen) WX_STATE.loading = false;
  }
  if (gen !== _wxGen) return;
  wxRenderChip();
  const panel = $("#weatherPanel");
  if (panel && !panel.classList.contains("hidden")) wxRenderPanel();
}

function wxChipText() {
  if (WX_STATE.loading) return "⏳ loading weather…";
  if (WX_STATE.error) return "⚠️ weather unavailable — click to retry";
  if (!WX_STATE.data) return "☁️ Weather — click to load";
  const summary = wxForecastSummary(wxRowsForNight(WX_STATE.data));
  return summary.score == null ? "Weather · no night forecast" : `${summary.status.label} tonight · ${summary.score}/100`;
}

function wxRenderChip() {
  const chip = $("#wxChip");
  if (chip) chip.textContent = wxChipText();
  const dot = $("#wxStatusDot"), btn = $("#btnWeather");
  const summary = WX_STATE.data ? wxForecastSummary(wxRowsForNight(WX_STATE.data)) : null;
  const status = WX_STATE.error ? { label: "Unavailable", cls: "bad" } :
    summary ? summary.status : { label: "Weather", cls: "unknown" };
  if (dot) {
    dot.className = `wx-status-dot wx-${status.cls}`;
    dot.textContent = status.cls === "unknown" ? "○" : "●";
  }
  if (btn) btn.title = summary && summary.score != null
    ? `${status.label} observing conditions tonight, score ${summary.score} of 100 — click for details`
    : "Observing weather for the selected night — click to load";
}

function wxRenderPanel() {
  const body = $("#wxBody"), siteEl = $("#wxSite");
  if (!body) return;
  const site = activeSite();
  if (siteEl) siteEl.textContent = site ? site.name : "active site";
  if (WX_STATE.loading) { body.innerHTML = '<p class="hint">loading…</p>'; return; }
  if (WX_STATE.error || !WX_STATE.data) {
    body.innerHTML = '<p class="wx-err">⚠️ Could not load weather' +
      (WX_STATE.error ? " — are you offline?" : "") + "</p>" +
      '<p class="hint">Weather comes from Open-Meteo and is only fetched when you click the chip. Try again in a moment.</p>';
    return;
  }
  const d = WX_STATE.data;
  const rows = wxRowsForNight(d);
  const summary = wxForecastSummary(rows);
  const bounds = wxNightBounds();
  body.innerHTML = `
    <div class="wx-rating wx-rating-${summary.status.cls}">
      <div class="wx-rating-head"><span class="wx-status-dot wx-${summary.status.cls}">${summary.status.cls === "unknown" ? "○" : "●"}</span><strong>${summary.status.label} night</strong><b>${summary.score == null ? "—" : summary.score}/100</b></div>
      <div class="wx-rating-detail">${summary.detail}</div>
    </div>
    <div class="wx-grid">
      <div><span>Cloud cover</span><b>${summary.cloud}</b></div>
      <div><span>Rain warning</span><b class="wx-${summary.status.cls === "bad" ? "bad" : summary.rain.includes("possible") ? "warn" : "good"}">${summary.rain}</b></div>
      <div><span title="Haze proxy, not an astronomical transparency measurement">Sky clarity</span><b class="wx-${summary.clarityCls}">${summary.clarity}</b></div>
      <div><span>Dew risk</span><b class="wx-${summary.dewCls}">${summary.dew}</b></div>
    </div>
    <div class="wx-section-title"><b>Dark-hour forecast</b><span>${wxSelectedDateKey()} · ${bounds.label} site time</span></div>
    ${rows.length ? `<div class="wx-hourly-head"><span>Time</span><span>Cloud</span><span>Rain</span><span>Clarity</span><span>Dew</span></div><div class="wx-hourly">${wxHourlyMarkup(rows)}</div>` : '<p class="hint">No dark-hour forecast is available for this selected night.</p>'}
    <p class="hint" style="margin-top:8px">Sky clarity is a haze proxy, not an astronomical transparency measurement. Dew risk is estimated from temperature and dew point. Forecast by <a href="https://open-meteo.com/" target="_blank" rel="noopener" style="color:var(--accent)">Open-Meteo</a>.</p>`;
}

/** Re-fetch when the site changed, but only if weather was already loaded
 *  (or auto-load is on) — never fetch unasked. */
export function refreshWeatherForSite() {
  if (WX_STATE.data || WX_STATE.error || (S.cfg && S.cfg.weather_auto)) loadWeather();
}

export function bindWeather() {
  const wxBtn = $("#btnWeather"), wxPanel = $("#weatherPanel");
  if (!wxBtn || !wxPanel) return;
  wxBtn.addEventListener("click", e => {
    e.stopPropagation();
    if (wxPanel.classList.contains("hidden")) {
      wxPanel.classList.remove("hidden");
      if (!WX_STATE.loading && (!WX_STATE.data || WX_STATE.error)) {
        WX_STATE.error = false;
        loadWeather();
      }
      else wxRenderPanel();
    } else {
      wxPanel.classList.add("hidden");
    }
  });
  document.addEventListener("mousedown", e => {
    if (!(e.target instanceof Element)) return;
    if (!wxPanel.classList.contains("hidden") &&
        !e.target.closest("#weatherPanel") && !e.target.closest("#btnWeather")) {
      wxPanel.classList.add("hidden");
    }
  });
  wxPanel.addEventListener("mousedown", e => e.stopPropagation());
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") wxPanel.classList.add("hidden");
  });

  const wxAuto = $("#wxAuto");
  if (wxAuto) {
    wxAuto.checked = !!(S.cfg && S.cfg.weather_auto);
    wxAuto.addEventListener("change", async e => {
      S.cfg.weather_auto = e.target.checked;
      await api("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" }, silent: true,
        body: JSON.stringify({ weather_auto: e.target.checked })
      }).catch(() => {});
      if (e.target.checked && !WX_STATE.data) loadWeather();
    });
  }
  if (S.cfg && S.cfg.weather_auto) loadWeather();
}
