const SNU_CENTER = [37.4601, 126.9512];
const SNAP_WARNING_M = 35;
const POINT_RADIUS = window.matchMedia("(pointer: coarse)").matches ? 7 : 6;
const DEFAULT_BASE_WALK_SPEED_KMH = 6.0;
const SPEED_STORAGE_KEY = "snu.baseWalkSpeedKmh";

const state = {
  start: null,
  end: null,
  selectionMode: "start",
  startMarker: null,
  endMarker: null,
  routeLayer: null,
  layers: new Map(),
  layerData: new Map(),
  baseWalkSpeedKmh: loadStoredSpeed(),
};

const map = L.map("map", {
  zoomControl: false,
}).setView(SNU_CENTER, 17);
window.__snuMap = map;

createMapPanes();

L.control.zoom({ position: "bottomleft" }).addTo(map);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 20,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

const elements = {
  statusText: document.getElementById("statusText"),
  selectStartButton: document.getElementById("selectStartButton"),
  selectEndButton: document.getElementById("selectEndButton"),
  startText: document.getElementById("startText"),
  endText: document.getElementById("endText"),
  routeButton: document.getElementById("routeButton"),
  resetButton: document.getElementById("resetButton"),
  speedText: document.getElementById("speedText"),
  allowShuttleInput: document.getElementById("allowShuttleInput"),
  calibrationInput: document.getElementById("calibrationInput"),
  calibrateButton: document.getElementById("calibrateButton"),
  clearSpeedButton: document.getElementById("clearSpeedButton"),
  summaryPanel: document.getElementById("summaryPanel"),
  timeValue: document.getElementById("timeValue"),
  distanceValue: document.getElementById("distanceValue"),
  ascentValue: document.getElementById("ascentValue"),
  descentValue: document.getElementById("descentValue"),
  outdoorTimeValue: document.getElementById("outdoorTimeValue"),
  buildingTimeValue: document.getElementById("buildingTimeValue"),
  shuttleTimeValue: document.getElementById("shuttleTimeValue"),
  snapWarning: document.getElementById("snapWarning"),
  shuttleNotice: document.getElementById("shuttleNotice"),
};

const startIcon = pointIcon("start");
const endIcon = pointIcon("end");

map.on("click", (event) => {
  clearRoute();
  setPoint(state.selectionMode, event.latlng);

  if (state.selectionMode === "start" && !state.end) {
    setSelectionMode("end");
  }

  setSelectionStatus();
});

elements.selectStartButton.addEventListener("click", () => setSelectionMode("start"));
elements.selectEndButton.addEventListener("click", () => setSelectionMode("end"));
elements.routeButton.addEventListener("click", findRoute);
elements.resetButton.addEventListener("click", resetSelection);
elements.calibrateButton.addEventListener("click", calibrateSpeed);
elements.clearSpeedButton.addEventListener("click", clearSpeedProfile);
elements.calibrationInput.addEventListener("input", updatePointText);
elements.allowShuttleInput.addEventListener("change", handleRouteOptionChange);

initializeLayerToggles();
renderSpeedProfile();
setSelectionMode("start");

function createMapPanes() {
  const panes = [
    ["boundaryPane", 350, "none"],
    ["plazaPane", 360, "none"],
    ["edgePane", 400, "none"],
    ["routePane", 500, "none"],
    ["pointPane", 650, "auto"],
  ];

  panes.forEach(([name, zIndex, pointerEvents]) => {
    map.createPane(name);
    const pane = map.getPane(name);
    pane.style.zIndex = String(zIndex);
    pane.style.pointerEvents = pointerEvents;
  });
}

function initializeLayerToggles() {
  document.querySelectorAll("[data-layer]").forEach((input) => {
    input.addEventListener("change", async () => {
      const layerName = input.dataset.layer;
      try {
        if (input.checked) {
          await showLayer(layerName);
        } else {
          hideLayer(layerName);
        }
      } catch (error) {
        input.checked = false;
        console.error(`[layer:${layerName}] load failed`, error);
        setStatus("레이어를 불러오지 못했습니다.");
      }
    });
  });

  loadDefaultLayers();
}

async function loadDefaultLayers() {
  for (const input of document.querySelectorAll("[data-layer]:checked")) {
    await showLayer(input.dataset.layer);
  }
}

function pointIcon(kind) {
  return L.divIcon({
    className: `point-marker ${kind}-marker`,
    iconSize: [20, 20],
    iconAnchor: [10, 10],
  });
}

function setPoint(kind, latlng) {
  state[kind] = latlng;
  const markerKey = `${kind}Marker`;
  const icon = kind === "start" ? startIcon : endIcon;
  const label = kind === "start" ? "출발" : "도착";

  if (state[markerKey]) {
    state[markerKey].setLatLng(latlng);
  } else {
    state[markerKey] = L.marker(latlng, {
      icon,
      draggable: true,
      pane: "pointPane",
      zIndexOffset: 1000,
      riseOnHover: true,
    }).addTo(map);

    state[markerKey].on("dragstart", clearRoute);
    state[markerKey].on("dragend", (event) => {
      state[kind] = event.target.getLatLng();
      setSelectionMode(kind);
      updatePointText();
      setSelectionStatus();
    });
  }

  state[markerKey]
    .bindTooltip(label, { direction: "top", offset: [0, -10], sticky: true })
    .bindPopup(`${label}<br>${formatLatLng(latlng)}`);
  updatePointText();
}

function setSelectionMode(mode) {
  state.selectionMode = mode;
  const isStart = mode === "start";
  elements.selectStartButton.classList.toggle("active", isStart);
  elements.selectEndButton.classList.toggle("active", !isStart);
  elements.selectStartButton.setAttribute("aria-pressed", String(isStart));
  elements.selectEndButton.setAttribute("aria-pressed", String(!isStart));
  setSelectionStatus();
}

function setSelectionStatus() {
  if (!state.start) {
    setStatus("출발지를 선택하세요.");
    return;
  }
  if (!state.end) {
    setStatus("도착지를 선택하세요.");
    return;
  }
  const target = state.selectionMode === "start" ? "출발지" : "도착지";
  setStatus(`${target}를 다시 찍거나 경로 찾기를 실행하세요.`);
}

function clearPoint(kind) {
  const markerKey = `${kind}Marker`;
  if (state[markerKey]) {
    map.removeLayer(state[markerKey]);
  }
  state[kind] = null;
  state[markerKey] = null;
  updatePointText();
}

function updatePointText() {
  elements.startText.textContent = state.start ? formatLatLng(state.start) : "미선택";
  elements.endText.textContent = state.end ? formatLatLng(state.end) : "미선택";
  elements.routeButton.disabled = !(state.start && state.end);
  elements.calibrateButton.disabled = !(state.start && state.end && calibrationTimeSec());
}

function formatLatLng(latlng) {
  return `${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}`;
}

async function findRoute() {
  if (!state.start || !state.end) return;

  setRouteButtonBusy(true);
  setStatus("경로를 계산하는 중입니다.");

  try {
    const response = await routeRequest();

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "경로를 찾지 못했습니다.");
    }

    drawRoute(data.route_geojson, data.summary);
    renderSummary(data.summary);
    setRouteStatus(data.summary);
  } catch (error) {
    clearRoute();
    elements.summaryPanel.hidden = true;
    setStatus(error.message);
  } finally {
    setRouteButtonBusy(false);
  }
}

async function calibrateSpeed() {
  if (!state.start || !state.end) return;
  const actualTimeSec = calibrationTimeSec();
  if (!actualTimeSec) {
    setStatus("실측 시간을 입력하세요.");
    return;
  }

  elements.calibrateButton.disabled = true;
  setStatus("속도 보정 중...");

  try {
    const response = await routeRequest({ calibration_actual_time_sec: actualTimeSec });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "속도 보정에 실패했습니다.");
    }

    const calibrated = Number(data.summary?.calibrated_base_walk_speed_kmh);
    if (Number.isFinite(calibrated)) {
      state.baseWalkSpeedKmh = calibrated;
      localStorage.setItem(SPEED_STORAGE_KEY, String(calibrated));
      renderSpeedProfile();
    }

    drawRoute(data.route_geojson, data.summary);
    renderSummary(data.summary);
    setRouteStatus(data.summary, "보정된 속도 적용");
  } catch (error) {
    setStatus(error.message);
  } finally {
    updatePointText();
  }
}

async function routeRequest(extra = {}) {
  const body = {
    start_lon: state.start.lng,
    start_lat: state.start.lat,
    end_lon: state.end.lng,
    end_lat: state.end.lat,
    allow_shuttle: elements.allowShuttleInput.checked,
    ...extra,
  };
  if (state.baseWalkSpeedKmh) {
    body.base_walk_speed_kmh = state.baseWalkSpeedKmh;
  }

  return fetch("/api/route", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function calibrationTimeSec() {
  const minutes = Number(elements.calibrationInput.value);
  if (!Number.isFinite(minutes) || minutes <= 0) return null;
  return minutes * 60;
}

function loadStoredSpeed() {
  const value = Number(localStorage.getItem(SPEED_STORAGE_KEY));
  return Number.isFinite(value) && value > 0 ? value : null;
}

function renderSpeedProfile() {
  const speed = state.baseWalkSpeedKmh || DEFAULT_BASE_WALK_SPEED_KMH;
  elements.speedText.textContent = `${speed.toFixed(1)} km/h`;
}

function setRouteButtonBusy(isBusy) {
  elements.routeButton.disabled = isBusy || !(state.start && state.end);
  elements.routeButton.textContent = isBusy ? "계산 중" : "경로 찾기";
  elements.routeButton.setAttribute("aria-busy", String(isBusy));
}

function clearSpeedProfile() {
  state.baseWalkSpeedKmh = null;
  localStorage.removeItem(SPEED_STORAGE_KEY);
  renderSpeedProfile();
  if (state.routeLayer && state.start && state.end) {
    findRoute();
    return;
  }
  setStatus("기본 속도를 적용했습니다.");
}

function handleRouteOptionChange() {
  if (state.routeLayer && state.start && state.end) {
    findRoute();
    return;
  }

  const shuttleStatus = elements.allowShuttleInput.checked ? "포함" : "제외";
  setStatus(`셔틀을 ${shuttleStatus}합니다. 경로 찾기를 실행하세요.`);
}

function drawRoute(geojson, summary) {
  clearRoute();
  const routeCoords = routeCoordinatesFromGeoJson(geojson);

  state.routeLayer = L.polyline(routeCoords, {
    pane: "routePane",
    color: "#111827",
    weight: 7,
    opacity: 0.95,
    lineCap: "round",
    lineJoin: "round",
    interactive: false,
  }).addTo(map);

  state.routeLayer.bringToFront();
  const bounds = state.routeLayer.getBounds();
  if (bounds.isValid()) {
    map.fitBounds(bounds.pad(0.18));
  }

  if (summary) {
    state.routeLayer.bindTooltip(`${formatDuration(summary.total_time_sec)} / ${summary.total_length_m.toFixed(0)}m`);
  }
}

function routeCoordinatesFromGeoJson(geojson) {
  const feature = geojson?.features?.[0];
  const coords = feature?.geometry?.coordinates || [];
  return coords.map(([lon, lat]) => [lat, lon]);
}

function clearRoute() {
  if (state.routeLayer) {
    map.removeLayer(state.routeLayer);
    state.routeLayer = null;
  }
  elements.summaryPanel.hidden = true;
  elements.shuttleNotice.hidden = true;
  elements.shuttleNotice.textContent = "";
}

function renderSummary(summary) {
  elements.summaryPanel.hidden = false;
  elements.timeValue.textContent = formatDuration(summary.total_time_sec);
  elements.distanceValue.textContent = `${summary.total_length_m.toFixed(0)}m`;
  elements.ascentValue.textContent = `${summary.total_ascent_m.toFixed(0)}m`;
  elements.descentValue.textContent = `${summary.total_descent_m.toFixed(0)}m`;
  elements.outdoorTimeValue.textContent = formatDuration(summary.outdoor_walk_time_sec);
  elements.buildingTimeValue.textContent = formatDuration(summary.building_internal_time_sec);
  elements.shuttleTimeValue.textContent = formatDuration(summary.shuttle_time_sec);

  const startSnap = summary.start_snap_distance_m;
  const endSnap = summary.end_snap_distance_m;
  if (startSnap > SNAP_WARNING_M || endSnap > SNAP_WARNING_M) {
    elements.snapWarning.hidden = false;
    elements.snapWarning.textContent = `스냅 거리: 출발 ${startSnap.toFixed(1)}m, 도착 ${endSnap.toFixed(1)}m`;
  } else {
    elements.snapWarning.hidden = true;
    elements.snapWarning.textContent = "";
  }

  if (summary.uses_shuttle) {
    const waitMin = (summary.shuttle_wait_time_sec / 60).toFixed(1);
    const rideMin = ((summary.shuttle_ride_time_sec + summary.shuttle_dwell_time_sec) / 60).toFixed(1);
    elements.shuttleNotice.hidden = false;
    elements.shuttleNotice.textContent = `셔틀 탑승 필요 · 대기 ${waitMin}분 / 탑승 ${rideMin}분`;
  } else {
    elements.shuttleNotice.hidden = true;
    elements.shuttleNotice.textContent = "";
  }

}

function formatDuration(seconds) {
  const safeSeconds = Number(seconds);
  if (!Number.isFinite(safeSeconds) || safeSeconds < 0) return "-";

  const minutes = safeSeconds / 60;
  if (minutes < 1) {
    return `${Math.round(safeSeconds)}초`;
  }
  if (minutes < 10) {
    return `${minutes.toFixed(1)}분`;
  }
  return `${Math.round(minutes)}분`;
}

function setRouteStatus(summary, prefix = "최단시간 경로") {
  if (summary.uses_shuttle) {
    const waitMin = (summary.shuttle_wait_time_sec / 60).toFixed(1);
    const rideMin = ((summary.shuttle_ride_time_sec + summary.shuttle_dwell_time_sec) / 60).toFixed(1);
    setStatus(`${prefix} · 셔틀 대기 ${waitMin}분 / 탑승 ${rideMin}분`);
    return;
  }
  setStatus(prefix);
}

function resetSelection() {
  clearRoute();
  clearPoint("start");
  clearPoint("end");
  setSelectionMode("start");
}

function setStatus(message) {
  elements.statusText.textContent = message;
}

async function showLayer(layerName) {
  if (state.layers.has(layerName)) {
    state.layers.get(layerName).addTo(map);
    return;
  }

  const data = await loadLayerData(layerName);
  const layer = createLayer(layerName, data);
  state.layers.set(layerName, layer);
  layer.addTo(map);
}

function hideLayer(layerName) {
  const layer = state.layers.get(layerName);
  if (layer) {
    map.removeLayer(layer);
  }
}

async function loadLayerData(layerName) {
  if (state.layerData.has(layerName)) {
    return state.layerData.get(layerName);
  }

  const response = await fetch(`/api/layers/${layerName}`);
  if (!response.ok) {
    throw new Error("레이어를 불러오지 못했습니다.");
  }

  const data = await response.json();
  state.layerData.set(layerName, data);
  return data;
}

function createLayer(layerName, data) {
  if (layerName === "campus_boundary") {
    return L.geoJSON(data, {
      pane: "boundaryPane",
      interactive: false,
      style: {
        color: "#ef4444",
        weight: 2,
        fillOpacity: 0.02,
      },
    });
  }

  if (layerName === "osm_edges") {
    return L.geoJSON(data, {
      pane: "edgePane",
      interactive: false,
      style: {
        color: "#2563eb",
        weight: 1.4,
        opacity: 0.38,
      },
    });
  }

  if (layerName === "entrances") {
    return L.geoJSON(data, {
      pane: "pointPane",
      pointToLayer: (feature, latlng) => {
        const entrance = feature.properties?.entrance || "entrance";
        return L.circleMarker(latlng, {
          pane: "pointPane",
          radius: POINT_RADIUS,
          color: "#c2410c",
          weight: 1.5,
          fillColor: "#fb923c",
          fillOpacity: 0.9,
          bubblingMouseEvents: false,
        })
          .bindTooltip("건물 입구", { sticky: true })
          .bindPopup(`건물 입구<br>entrance=${entrance}`);
      },
    });
  }

  if (layerName === "elevation_nodes") {
    return L.geoJSON(data, {
      pane: "pointPane",
      pointToLayer: (feature, latlng) => {
        const elevation = Number(feature.properties?.elevation_m || 0);
        const label = `고도 ${elevation.toFixed(0)}m`;
        return L.circleMarker(latlng, {
          pane: "pointPane",
          radius: POINT_RADIUS,
          color: "#0f766e",
          weight: 1.5,
          fillColor: elevationColor(elevation),
          fillOpacity: 0.86,
          bubblingMouseEvents: false,
        })
          .bindTooltip(label, { sticky: true })
          .bindPopup(`고도=${elevation.toFixed(1)}m`);
      },
    });
  }

  if (layerName === "manual_features") {
    const areaLayer = L.geoJSON(data, {
      pane: "plazaPane",
      interactive: false,
      filter: (feature) => feature.properties?.kind === "area",
      style: {
        color: "#16a34a",
        weight: 2,
        fillColor: "#86efac",
        fillOpacity: 0.22,
      },
    });

    const edgeLayer = L.geoJSON(data, {
      pane: "edgePane",
      interactive: false,
      filter: (feature) => feature.properties?.kind === "edge",
      style: (feature) => ({
        color: feature.properties?.walk_type === "plaza_crossing" ? "#dc2626" : "#f97316",
        weight: feature.properties?.walk_type === "plaza_crossing" ? 3 : 2,
        opacity: 0.72,
      }),
    });

    const nodeLayer = L.geoJSON(data, {
      pane: "pointPane",
      filter: (feature) => feature.properties?.kind === "node",
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, {
          pane: "pointPane",
          radius: POINT_RADIUS,
          color: "#15803d",
          weight: 2,
          fillColor: "#22c55e",
          fillOpacity: 0.9,
          bubblingMouseEvents: false,
        })
          .bindTooltip(feature.properties?.name || "수동 노드", { sticky: true })
          .bindPopup(feature.properties?.name || "수동 노드"),
    });

    return L.layerGroup([areaLayer, edgeLayer, nodeLayer]);
  }

  if (layerName === "shuttle_features") {
    const rideLayer = L.geoJSON(data, {
      pane: "edgePane",
      interactive: false,
      filter: (feature) => feature.properties?.kind === "ride",
      style: {
        color: "#7c3aed",
        weight: 3,
        opacity: 0.78,
      },
    });

    const stopLayer = L.geoJSON(data, {
      pane: "pointPane",
      filter: (feature) => feature.properties?.kind === "stop",
      pointToLayer: (feature, latlng) =>
        L.circleMarker(latlng, {
          pane: "pointPane",
          radius: POINT_RADIUS + 1,
          color: "#6d28d9",
          weight: 2,
          fillColor: "#a855f7",
          fillOpacity: 0.92,
          bubblingMouseEvents: false,
        })
          .bindTooltip(feature.properties?.name || "셔틀 정류장", { sticky: true })
          .bindPopup(feature.properties?.name || "셔틀 정류장"),
    });

    return L.layerGroup([rideLayer, stopLayer]);
  }

  return L.geoJSON(data);
}

function elevationColor(elevation) {
  if (elevation >= 160) return "#7c3aed";
  if (elevation >= 135) return "#dc2626";
  if (elevation >= 115) return "#f59e0b";
  return "#0ea5e9";
}
