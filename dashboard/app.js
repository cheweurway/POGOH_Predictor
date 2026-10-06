// POGOH availability dashboard.
//
// Reads the JSON files written by scripts/build_dashboard.py (in data/) and
// draws four views: a station map (Leaflet over an OpenFreeMap background), one
// station's history, a stockout heatmap, and collection health (Plotly).
//
// Two layers of data:
// - The build (data/*.json, rebuilt on a schedule) has the history: charts,
//   heatmap, health, and a snapshot of every station.
// - The live layer reads the newest poll file straight from the repository's
//   public `data` branch on GitHub, so the map is as fresh as the collector.
//   It never calls the bike share API itself, so visitors add no load there.
// The map shows whichever of the two is newer.
//
// Times from the build are either UTC ISO strings (ending in +00:00) or
// Pittsburgh wall-clock strings without an offset (t_local, hours_local).
// Wall-clock strings go to Plotly as they are, so charts show local time.
//
// Test switches in the page address:
//   ?live=off               never use the live layer (shows the build only)
//   ?live_date=2026-10-07   pretend today (UTC) is that date, to test the
//                           fallback to the previous day's folder
//   ?basemap=osm            use OpenStreetMap tiles instead of OpenFreeMap

"use strict";

const TZ = "America/New_York";
const DATA_DIR = "data/";
const HEATMAP_TOP = 20;  // rows shown before "Show all stations"
// Map colors. A station with 0 bikes is red with a dashed outline (the
// outline means it does not rely on color alone). A station with at least
// one bike runs from yellow (few bikes for its size) to green (full). The
// yellow-to-green stops match the legend gradient in style.css.
const EMPTY_COLOR = "#d73027";
const BIKE_SCALE = ["#fee08b", "#d9ef8b", "#91cf60", "#1a9850"];
// Heatmap colors: green = rarely empty, through yellow, to red = often empty.
const HEATMAP_SCALE = ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027"];
const DEFAULT_STATION_MATCH = "TCS Hall";

// Live layer settings.
const REPO = "cheweurway/POGOH_Predictor";
const DATA_BRANCH = "data";
const LIVE_EVERY_MS = 5 * 60 * 1000;      // check for a newer poll this often
const HISTORY_EVERY_MS = 10 * 60 * 1000;  // reload the built charts this often
const STALE_MINUTES = 20;                 // header turns amber past this age
const MAX_FILES_TRIED = 5;                // newest files to try for a successful poll
const PARAMS = new URLSearchParams(location.search);

const state = {
  map: null, basemap: null, basemapKind: null, markers: {}, stations: [], selected: null,
  heatmap: null, heatmapShowAll: false,
  latestPollUtc: null,         // time of the poll the map is showing
  latestSource: null,          // "build" or "live"
  live: { mode: "checking" },  // checking, live, off, paused, nodata, error, unsupported
  liveBusy: false,
  lastLiveCheck: 0,
  lastHistory: 0,
  drawProblems: [],
};

// ---------- small helpers ----------

// Fetch a JSON file. The ?v= query defeats browser and GitHub Pages caching,
// so a refresh always gets the newest build.
async function fetchJSON(path) {
  const response = await fetch(`${DATA_DIR}${path}?v=${Date.now()}`);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

// "Oct 5, 9:31 PM EDT" from a UTC ISO string.
function formatUtc(isoUtc) {
  return new Intl.DateTimeFormat("en-US", {
    timeZone: TZ, month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit", timeZoneName: "short",
  }).format(new Date(isoUtc));
}

function minutesAgo(isoUtc) {
  return Math.round((Date.now() - new Date(isoUtc).getTime()) / 60000);
}

// Escape text before putting it into HTML (station names contain quotes).
function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text == null ? "" : String(text);
  return div.innerHTML;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// Interpolate the bike scale at f in [0, 1] (0 = yellow, 1 = green).
function bikeColor(f) {
  const x = Math.min(1, Math.max(0, f)) * (BIKE_SCALE.length - 1);
  const i = Math.min(BIKE_SCALE.length - 2, Math.floor(x));
  const t = x - i;
  const a = BIKE_SCALE[i], b = BIKE_SCALE[i + 1];
  const channel = (k) => Math.round(parseInt(a.slice(k, k + 2), 16) * (1 - t) + parseInt(b.slice(k, k + 2), 16) * t);
  return `rgb(${channel(1)}, ${channel(3)}, ${channel(5)})`;
}

// Shared Plotly look that follows the page's light or dark colors.
function baseLayout(extra) {
  const text = cssVar("--text");
  const grid = cssVar("--grid");
  return Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { color: text, family: "system-ui, -apple-system, Segoe UI, Roboto, sans-serif", size: 12 },
    margin: { l: 50, r: 16, t: 16, b: 48 },
    xaxis: { gridcolor: grid, zerolinecolor: grid },
    yaxis: { gridcolor: grid, zerolinecolor: grid },
    legend: { orientation: "h", y: -0.2 },
    hoverlabel: { namelength: -1 },
  }, extra);
}

const PLOT_CONFIG = { responsive: true, displayModeBar: false };

// ---------- header and footer ----------

// "10:42 PM" from milliseconds since 1970.
function formatClock(ms) {
  return new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "2-digit" }).format(new Date(ms));
}

// Footer line and the gap-rule numbers in the notes, from meta.json.
function renderMeta(meta) {
  document.getElementById("built-at").textContent = meta.first_poll_utc
    ? `Page data built ${formatUtc(meta.built_at_utc)}. ${meta.n_polls_ok} successful polls since ${formatUtc(meta.first_poll_utc)}.`
    : `Page data built ${formatUtc(meta.built_at_utc)}.`;
  document.querySelectorAll(".gap-minutes").forEach((el) => { el.textContent = meta.gap_minutes; });
}

// The status box in the header. Called whenever something changes and once a
// minute, so "N min ago" stays current.
function updateStatus() {
  const status = document.getElementById("status");
  if (!state.latestPollUtc) {
    status.textContent = "No data yet.";
    return;
  }
  const ago = minutesAgo(state.latestPollUtc);
  const when = `${formatUtc(state.latestPollUtc)} (${ago} min ago)`;
  const live = state.live;
  let text;
  if (live.mode === "live") {
    text = `Live: last poll ${when}`;
  } else {
    text = `Data as of ${when}.`;
    if (live.mode === "checking") text += " Checking for newer data...";
    else if (live.mode === "off") text += " Live updates off.";
    else if (live.mode === "paused") text += ` Live updates paused until ${formatClock(live.pausedUntil)} (GitHub rate limit).`;
    else text += " Live updates unavailable right now.";
  }
  if (state.drawProblems.length) {
    text += ` Problem drawing: ${state.drawProblems.join(", ")} (see browser console).`;
  }
  status.textContent = text;
  status.classList.toggle("stale", ago > STALE_MINUTES || state.drawProblems.length > 0);
}

// ---------- map ----------

// Background map: OpenFreeMap's plain light "positron" style, in both light
// and dark page modes (the user prefers it). Free with no key; credit is
// added to the map corner automatically from the style. If the browser
// cannot run MapLibre (it needs WebGL) or ?basemap=osm is in the address,
// standard OpenStreetMap tiles are used instead, so the map is never blank.
const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/positron";

function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(canvas.getContext("webgl2") || canvas.getContext("webgl"));
  } catch (error) {
    return false;
  }
}

function addOsmTiles() {
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(state.map);
  state.basemapKind = "osm";
}

function addBasemap() {
  const forceOsm = new URLSearchParams(location.search).get("basemap") === "osm";
  const canUseMapLibre = typeof L.maplibreGL === "function" && window.maplibregl && webglAvailable();
  if (forceOsm || !canUseMapLibre) {
    addOsmTiles();
    return;
  }
  try {
    state.basemap = L.maplibreGL({ style: OPENFREEMAP_STYLE }).addTo(state.map);
    state.basemapKind = "openfreemap";
  } catch (error) {
    console.warn("Dashboard: OpenFreeMap failed, using OpenStreetMap tiles", error);
    addOsmTiles();
  }
}

function markerStyle(station) {
  if (station.free_bikes === 0) {
    return { radius: 8, color: "#222", weight: 2, dashArray: "3 3", fillColor: EMPTY_COLOR, fillOpacity: 1 };
  }
  const fill = station.frac_full == null ? "#999" : bikeColor(station.frac_full);
  return { radius: 8, color: "#fff", weight: 1.5, fillColor: fill, fillOpacity: 0.95 };
}

function popupHtml(station) {
  return `<h3>${escapeHtml(station.name)}</h3>
    <div><strong>${station.free_bikes}</strong> bikes
      (${station.normal_bikes ?? "?"} regular, ${station.ebikes ?? "?"} e-bikes)</div>
    <div><strong>${station.empty_slots}</strong> empty docks</div>
    <div class="popup-time">As of ${escapeHtml(formatUtc(station.t_utc))}</div>`;
}

function renderMap(stations) {
  const located = stations.filter((s) => s.lat != null && s.lon != null);
  if (!state.map) {
    state.map = L.map("map", { scrollWheelZoom: false });
    // The map needs a view (center and zoom) before any background or circle
    // is added; Leaflet cannot place things on a map with no view. So zoom to
    // the stations first, once, and leave the view alone after that.
    if (located.length) {
      state.map.fitBounds(L.latLngBounds(located.map((s) => [s.lat, s.lon])), { padding: [20, 20] });
    } else {
      state.map.setView([40.44, -79.96], 12);  // Pittsburgh
    }
    addBasemap();
  }
  for (const station of located) {
    let marker = state.markers[station.station_id];
    if (!marker) {
      marker = L.circleMarker([station.lat, station.lon], markerStyle(station)).addTo(state.map);
      marker.on("click", () => selectStation(station.station_id));
      marker.bindPopup(popupHtml(station));
      marker.bindTooltip(escapeHtml(station.name));
      state.markers[station.station_id] = marker;
    } else {
      // Update in place, so a popup that is open shows the new numbers.
      marker.setStyle(markerStyle(station));
      marker.setPopupContent(popupHtml(station));
    }
  }
}

// ---------- station picker and history ----------

function renderPicker(stations) {
  const select = document.getElementById("station-select");
  select.innerHTML = "";
  for (const station of stations) {  // already sorted by name
    const option = document.createElement("option");
    option.value = station.station_id;
    option.textContent = station.name;
    select.appendChild(option);
  }
  select.onchange = () => selectStation(select.value);
}

async function selectStation(stationId) {
  state.selected = stationId;
  document.getElementById("station-select").value = stationId;
  const chart = document.getElementById("series-chart");
  let series;
  try {
    series = await fetchJSON(`series/${stationId}.json`);
  } catch (error) {
    chart.textContent = `Could not load this station's history (${error.message}).`;
    return;
  }
  if (state.selected !== stationId) return;  // the user picked another station meanwhile

  // Bikes available only (the user chose not to show empty docks here).
  const traces = [
    { x: series.t_local, y: series.free_bikes, name: "Bikes available", mode: "lines",
      line: { color: cssVar("--bikes"), width: 2 }, connectgaps: false,
      hovertemplate: "%{x|%b %d %I:%M %p}<br>%{y} bikes<extra></extra>" },
  ];
  if (series.rebalancing.length) {
    traces.push({
      x: series.rebalancing.map((e) => e.t_local),
      y: series.rebalancing.map((e) => e.after),
      text: series.rebalancing.map((e) => `${e.before} to ${e.after} bikes`),
      name: "Suspected rebalancing", mode: "markers",
      marker: { symbol: "diamond", size: 10, color: cssVar("--text") },
      hovertemplate: "%{x|%b %d %I:%M %p}<br>Suspected rebalancing: %{text}<extra></extra>",
    });
  }
  Plotly.react(chart, traces, baseLayout({
    uirevision: stationId,  // keep the user's zoom when the same station refreshes
    yaxis: { title: { text: "Bikes available" }, rangemode: "tozero", gridcolor: cssVar("--grid") },
    xaxis: { gridcolor: cssVar("--grid") },
  }), PLOT_CONFIG);
}

// ---------- heatmap ----------

// "12 AM", "1 AM", ... "11 PM" for hours 0 to 23.
function hourLabel(hour) {
  if (hour === 0) return "12 AM";
  if (hour < 12) return `${hour} AM`;
  if (hour === 12) return "12 PM";
  return `${hour - 12} PM`;
}

// Stations x hour of day. Rows come sorted with the most often empty first,
// so showing the first HEATMAP_TOP rows shows the stations that matter most.
// The button below the chart switches between the top rows and all of them.
function renderHeatmap(heatmap) {
  const chart = document.getElementById("heatmap-chart");
  const button = document.getElementById("heatmap-toggle");
  if (!heatmap.station_ids.length) {
    chart.textContent = "No data yet.";
    button.hidden = true;
    return;
  }
  state.heatmap = heatmap;
  const total = heatmap.station_ids.length;
  const rows = state.heatmapShowAll ? total : Math.min(HEATMAP_TOP, total);
  const x = heatmap.hours.map(hourLabel);
  const noGrid = { showgrid: false, zeroline: false };
  Plotly.react(chart, [{
    type: "heatmap",
    z: heatmap.z.slice(0, rows),
    x: x,
    y: heatmap.names.slice(0, rows),
    zmin: 0, zmax: 1,
    xgap: 1, ygap: 1,  // thin gaps between cells instead of grid lines
    colorscale: HEATMAP_SCALE.map((c, i) => [i / (HEATMAP_SCALE.length - 1), c]),
    colorbar: { title: { text: "Share empty" }, tickformat: ".0%", thickness: 12 },
    hoverongaps: false,
    hovertemplate: "%{y}<br>%{x}<br>%{z:.0%} of polls had 0 bikes<extra></extra>",
  }], baseLayout({
    height: rows * 22 + 90,
    margin: { l: 230, r: 16, t: 8, b: 40 },
    xaxis: Object.assign({ tickvals: x.filter((_, h) => h % 3 === 0), side: "bottom" }, noGrid),
    yaxis: Object.assign({ autorange: "reversed", automargin: true, tickfont: { size: 11 } }, noGrid),
  }), PLOT_CONFIG);

  button.hidden = total <= HEATMAP_TOP;
  button.textContent = state.heatmapShowAll
    ? `Show top ${HEATMAP_TOP} stations`
    : `Show all ${total} stations`;
  button.onclick = () => {
    state.heatmapShowAll = !state.heatmapShowAll;
    renderHeatmap(state.heatmap);
  };
}

// ---------- collection health ----------

function renderHealth(health) {
  const chart = document.getElementById("health-chart");
  Plotly.react(chart, [
    { type: "bar", x: health.hours_local, y: health.ok, name: "Successful", marker: { color: cssVar("--bikes") },
      hovertemplate: "%{x|%b %d %I %p}<br>%{y} successful<extra></extra>" },
    { type: "bar", x: health.hours_local, y: health.failed, name: "Failed", marker: { color: cssVar("--failed") },
      hovertemplate: "%{x|%b %d %I %p}<br>%{y} failed<extra></extra>" },
  ], baseLayout({
    barmode: "stack",
    yaxis: { title: { text: "Polls per hour" }, rangemode: "tozero", gridcolor: cssVar("--grid") },
    xaxis: { gridcolor: cssVar("--grid") },
  }), PLOT_CONFIG);

  const body = document.querySelector("#gap-table tbody");
  body.innerHTML = "";
  const fmt = (local) => local.replace("T", " ").slice(0, 16);
  for (const gap of [...health.gaps].reverse()) {  // newest first
    const row = document.createElement("tr");
    row.innerHTML = `<td>${fmt(gap.start_local)}</td><td>${fmt(gap.end_local)}</td><td>${gap.minutes}</td>`;
    body.appendChild(row);
  }
  if (!health.gaps.length) {
    body.innerHTML = '<tr><td colspan="3">No gaps.</td></tr>';
  }
}

// ---------- live layer ----------

class RateLimited extends Error {
  constructor(resetMs) {
    super("GitHub rate limit");
    this.resetMs = resetMs;
  }
}

// Poll files live in raw/YYYY/MM/DD/ by UTC date.
function dayFolder(date) {
  const y = date.getUTCFullYear();
  const m = String(date.getUTCMonth() + 1).padStart(2, "0");
  const d = String(date.getUTCDate()).padStart(2, "0");
  return `raw/${y}/${m}/${d}`;
}

// Today's folder and yesterday's. Midnight UTC is 8 PM in Pittsburgh (7 PM
// in winter), and for a few minutes after it today's folder does not exist
// yet, so yesterday's is the fallback.
function liveFolders() {
  const forced = PARAMS.get("live_date");
  const today = forced ? new Date(`${forced}T12:00:00Z`) : new Date();
  if (Number.isNaN(today.getTime())) throw new Error(`live_date "${forced}" is not a date`);
  return [dayFolder(today), dayFolder(new Date(today.getTime() - 24 * 60 * 60 * 1000))];
}

// List one day's poll files, newest first. A missing folder means no polls
// that day yet. GitHub allows 60 listing requests per hour per network
// address; when that runs out it answers 403 or 429, which becomes
// RateLimited with GitHub's reset time.
async function listPollFiles(folder) {
  const response = await fetch(`https://api.github.com/repos/${REPO}/contents/${folder}?ref=${DATA_BRANCH}`);
  if (response.status === 404) return [];
  if (response.status === 403 || response.status === 429) {
    const reset = Number(response.headers.get("x-ratelimit-reset"));
    const retryAfter = Number(response.headers.get("retry-after"));
    if (response.headers.get("x-ratelimit-remaining") === "0" && reset) throw new RateLimited(reset * 1000);
    if (retryAfter) throw new RateLimited(Date.now() + retryAfter * 1000);
  }
  if (!response.ok) throw new Error(`GitHub listing: HTTP ${response.status}`);
  const entries = await response.json();
  return entries
    .filter((entry) => entry.type === "file" && entry.name.endsWith(".json.gz"))
    .sort((a, b) => (a.name < b.name ? 1 : -1));  // names sort by time
}

// Download one poll file and unzip it in the browser. GitHub serves it as
// plain bytes; the gzip check also copes if a server ever unzips it first.
async function readPollRecord(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`poll file: HTTP ${response.status}`);
  const bytes = new Uint8Array(await response.arrayBuffer());
  let text;
  if (bytes[0] === 0x1f && bytes[1] === 0x8b) {
    const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
    text = await new Response(stream).text();
  } else {
    text = new TextDecoder().decode(bytes);
  }
  return JSON.parse(text);
}

// The raw API response in a poll file, in the same shape as stations.json.
function stationsFromRecord(record) {
  return record.payload.network.stations.map((s) => {
    const extra = s.extra || {};
    const capacity = s.free_bikes + s.empty_slots;
    return {
      station_id: s.id,
      name: s.name,
      lat: s.latitude,
      lon: s.longitude,
      free_bikes: s.free_bikes,
      normal_bikes: extra.normal_bikes ?? null,
      ebikes: extra.ebikes ?? null,
      empty_slots: s.empty_slots,
      capacity: capacity,
      frac_full: capacity ? s.free_bikes / capacity : null,
      t_utc: record.polled_at_utc,
    };
  }).sort((a, b) => a.name.localeCompare(b.name));
}

// Show a set of stations on the map if it is newer than what is showing.
function showIfNewer(stations, polledAtUtc, source) {
  if (state.latestPollUtc && new Date(polledAtUtc) <= new Date(state.latestPollUtc)) return;
  state.stations = stations;
  state.latestPollUtc = polledAtUtc;
  state.latestSource = source;
  renderMap(stations);
}

async function checkLive() {
  if (PARAMS.get("live") === "off") {
    state.live = { mode: "off" };
    updateStatus();
    return;
  }
  if (state.live.mode === "paused" && Date.now() < state.live.pausedUntil) return;
  if (typeof DecompressionStream !== "function") {
    state.live = { mode: "unsupported" };  // very old browser
    updateStatus();
    return;
  }
  if (state.liveBusy) return;
  state.liveBusy = true;
  state.lastLiveCheck = Date.now();
  try {
    for (const folder of liveFolders()) {
      const files = await listPollFiles(folder);
      for (const file of files.slice(0, MAX_FILES_TRIED)) {
        const record = await readPollRecord(file.download_url);
        if (record.ok) {
          showIfNewer(stationsFromRecord(record), record.polled_at_utc, "live");
          state.live = { mode: "live", folder: folder, file: file.name };
          return;
        }
      }
    }
    state.live = { mode: "nodata" };
  } catch (error) {
    if (error instanceof RateLimited) {
      state.live = { mode: "paused", pausedUntil: error.resetMs };
    } else {
      state.live = { mode: "error", detail: error.message };
      console.warn("Dashboard: live update failed", error);
    }
  } finally {
    state.liveBusy = false;
    updateStatus();
  }
}

// Reload the built charts (they are rebuilt on a schedule) without
// reloading the page. The heatmap's show-all choice and the history chart's
// zoom are kept.
async function refreshHistory() {
  state.lastHistory = Date.now();
  try {
    const [meta, heatmap, health] = await Promise.all([
      fetchJSON("meta.json"), fetchJSON("heatmap.json"), fetchJSON("health.json"),
    ]);
    renderMeta(meta);
    renderHeatmap(heatmap);
    renderHealth(health);
    if (meta.last_poll_utc && (!state.latestPollUtc || new Date(meta.last_poll_utc) > new Date(state.latestPollUtc))) {
      showIfNewer(await fetchJSON("stations.json"), meta.last_poll_utc, "build");
    }
    if (state.selected) selectStation(state.selected);
  } catch (error) {
    console.warn("Dashboard: refreshing charts failed", error);
  }
  updateStatus();
}

// Runs once a minute and when the tab becomes visible again. Nothing is
// fetched while the tab is hidden.
function tick() {
  updateStatus();
  if (document.visibilityState !== "visible") return;
  const now = Date.now();
  if (now - state.lastLiveCheck >= LIVE_EVERY_MS) checkLive();
  if (now - state.lastHistory >= HISTORY_EVERY_MS) refreshHistory();
}

// ---------- start ----------

async function init() {
  const status = document.getElementById("status");
  let meta, stations, heatmap, health;
  try {
    [meta, stations, heatmap, health] = await Promise.all([
      fetchJSON("meta.json"), fetchJSON("stations.json"), fetchJSON("heatmap.json"), fetchJSON("health.json"),
    ]);
  } catch (error) {
    status.textContent = `Could not load dashboard data (${error.message}).`;
    status.classList.add("stale");
    return;
  }
  state.stations = stations;
  state.latestPollUtc = meta.last_poll_utc;
  state.latestSource = "build";
  state.lastHistory = Date.now();

  // Draw each part separately, so one failing part cannot blank the rest.
  // Failures are listed in the status box and the browser console.
  const attempt = (label, draw) => {
    try {
      draw();
    } catch (error) {
      state.drawProblems.push(label);
      console.error(`Dashboard: ${label} failed`, error);
    }
  };
  attempt("footer", () => renderMeta(meta));
  attempt("map", () => renderMap(stations));
  attempt("station list", () => renderPicker(stations));
  attempt("heatmap", () => renderHeatmap(heatmap));
  attempt("collection health", () => renderHealth(health));
  attempt("station history", () => {
    const preferred = stations.find((s) => s.name.includes(DEFAULT_STATION_MATCH)) || stations[0];
    if (preferred) selectStation(preferred.station_id);
  });
  updateStatus();

  checkLive();
  setInterval(tick, 60 * 1000);
  document.addEventListener("visibilitychange", tick);
}

init();
