// POGOH availability dashboard.
//
// Reads the JSON files written by scripts/build_dashboard.py (in data/) and
// draws four views: a station map (Leaflet over an OpenFreeMap background), one
// station's history, a stockout heatmap, and collection health (Plotly).
//
// Times from the build are either UTC ISO strings (ending in +00:00) or
// Pittsburgh wall-clock strings without an offset (t_local, hours_local).
// Wall-clock strings go to Plotly as they are, so charts show local time.

"use strict";

const TZ = "America/New_York";
const DATA_DIR = "data/";
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
// Map colors. A station with 0 bikes is red with a dashed outline (the
// outline means it does not rely on color alone). A station with at least
// one bike runs from yellow (few bikes for its size) to green (full). The
// yellow-to-green stops match the legend gradient in style.css.
const EMPTY_COLOR = "#d73027";
const BIKE_SCALE = ["#fee08b", "#d9ef8b", "#91cf60", "#1a9850"];
// Heatmap colors: green = rarely empty, through yellow, to red = often empty.
const HEATMAP_SCALE = ["#1a9850", "#91cf60", "#fee08b", "#fc8d59", "#d73027"];
const DEFAULT_STATION_MATCH = "TCS Hall";

const state = { map: null, basemap: null, basemapKind: null, markers: {}, stations: [], selected: null };

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

// ---------- header ----------

function renderStatus(meta) {
  const status = document.getElementById("status");
  if (!meta.last_poll_utc) {
    status.textContent = "No data yet.";
    return;
  }
  const ago = minutesAgo(meta.last_poll_utc);
  status.textContent = `Data as of ${formatUtc(meta.last_poll_utc)} (${ago} min ago)`;
  status.classList.toggle("stale", ago > 20);
  document.getElementById("built-at").textContent =
    `Page data built ${formatUtc(meta.built_at_utc)}. ${meta.n_polls_ok} successful polls since ${formatUtc(meta.first_poll_utc)}.`;
  document.querySelectorAll(".gap-minutes").forEach((el) => { el.textContent = meta.gap_minutes; });
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
      state.markers[station.station_id] = marker;
    } else {
      marker.setStyle(markerStyle(station));
    }
    marker.bindPopup(popupHtml(station));
    marker.bindTooltip(escapeHtml(station.name));
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

  const traces = [
    { x: series.t_local, y: series.free_bikes, name: "Bikes", mode: "lines",
      line: { color: cssVar("--bikes"), width: 2 }, connectgaps: false,
      hovertemplate: "%{x|%b %d %I:%M %p}<br>%{y} bikes<extra></extra>" },
    { x: series.t_local, y: series.empty_slots, name: "Empty docks", mode: "lines",
      line: { color: cssVar("--docks"), width: 1.5, dash: "dot" }, connectgaps: false,
      hovertemplate: "%{x|%b %d %I:%M %p}<br>%{y} empty docks<extra></extra>" },
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
    yaxis: { title: { text: "Count" }, rangemode: "tozero", gridcolor: cssVar("--grid") },
    xaxis: { gridcolor: cssVar("--grid") },
  }), PLOT_CONFIG);
}

// ---------- heatmap ----------

function hourLabel(h) {
  const day = DAYS[Math.floor(h / 24)];
  const hour = h % 24;
  const label = hour === 0 ? "12 AM" : hour < 12 ? `${hour} AM` : hour === 12 ? "12 PM" : `${hour - 12} PM`;
  return `${day} ${label}`;
}

function renderHeatmap(heatmap) {
  const chart = document.getElementById("heatmap-chart");
  if (!heatmap.station_ids.length) {
    chart.textContent = "No data yet.";
    return;
  }
  const x = Array.from({ length: 168 }, (_, h) => hourLabel(h));
  const rows = heatmap.station_ids.length;
  Plotly.react(chart, [{
    type: "heatmap",
    z: heatmap.z,
    x: x,
    y: heatmap.names,
    zmin: 0, zmax: 1,
    colorscale: HEATMAP_SCALE.map((c, i) => [i / (HEATMAP_SCALE.length - 1), c]),
    colorbar: { title: { text: "Share empty" }, tickformat: ".0%" },
    hoverongaps: false,
    hovertemplate: "%{y}<br>%{x}<br>%{z:.0%} of polls had 0 bikes<extra></extra>",
  }], baseLayout({
    height: Math.max(360, rows * 16 + 120),
    margin: { l: 230, r: 16, t: 16, b: 60 },
    xaxis: { tickvals: DAYS.map((_, d) => x[d * 24]), ticktext: DAYS, gridcolor: cssVar("--grid") },
    yaxis: { autorange: "reversed", automargin: true, tickfont: { size: 10 } },
  }), PLOT_CONFIG);
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
  // Draw each part separately, so one failing part cannot blank the rest.
  // Failures are listed in the status box and the browser console.
  const failed = [];
  const attempt = (label, draw) => {
    try {
      draw();
    } catch (error) {
      failed.push(label);
      console.error(`Dashboard: ${label} failed`, error);
    }
  };
  attempt("header", () => renderStatus(meta));
  attempt("map", () => renderMap(stations));
  attempt("station list", () => renderPicker(stations));
  attempt("heatmap", () => renderHeatmap(heatmap));
  attempt("collection health", () => renderHealth(health));
  attempt("station history", () => {
    const preferred = stations.find((s) => s.name.includes(DEFAULT_STATION_MATCH)) || stations[0];
    if (preferred) selectStation(preferred.station_id);
  });
  if (failed.length) {
    status.textContent += ` Problem drawing: ${failed.join(", ")} (see browser console).`;
    status.classList.add("stale");
  }
}

init();
