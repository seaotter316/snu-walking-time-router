const SNU_CENTER = [37.4601, 126.9512];
const SNAP_WARNING_M = 35;
const POINT_RADIUS = window.matchMedia("(pointer: coarse)").matches ? 7 : 6;

const state = {
  start: null,
  end: null,
  startMarker: null,
  endMarker: null,
  routeLayer: null,
  layers: new Map(),
  layerData: new Map(),
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
  startText: document.getElementById("startText"),
  endText: document.getElementById("endText"),
  routeButton: document.getElementById("routeButton"),
  resetButton: document.getElementById("resetButton"),
  summaryPanel: document.getElementById("summaryPanel"),
  timeValue: document.getElementById("timeValue"),
  distanceValue: document.getElementById("distanceValue"),
  ascentValue: document.getElementById("ascentValue"),
  descentValue: document.getElementById("descentValue"),
  snapWarning: document.getElementById("snapWarning"),
};

const startIcon = pointIcon("start");
const endIcon = pointIcon("end");

map.on("click", (event) => {
  if (!state.start || (state.start && state.end)) {
    clearRoute();
    setPoint("start", event.latlng);
    clearPoint("end");
    setStatus("도착지를 선택하세요.");
    return;
  }

  setPoint("end", event.latlng);
  setStatus("경로 찾기를 실행하세요.");
});

elements.routeButton.addEventListener("click", findRoute);
elements.resetButton.addEventListener("click", resetSelection);

initializeLayerToggles();

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
      pane: "pointPane",
      zIndexOffset: 1000,
      riseOnHover: true,
    }).addTo(map);
  }

  state[markerKey]
    .bindTooltip(label, { direction: "top", offset: [0, -10], sticky: true })
    .bindPopup(`${label}<br>${formatLatLng(latlng)}`);
  updatePointText();
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
}

function formatLatLng(latlng) {
  return `${latlng.lat.toFixed(6)}, ${latlng.lng.toFixed(6)}`;
}

async function findRoute() {
  if (!state.start || !state.end) return;

  elements.routeButton.disabled = true;
  setStatus("계산 중...");

  try {
    const response = await fetch("/api/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_lon: state.start.lng,
        start_lat: state.start.lat,
        end_lon: state.end.lng,
        end_lat: state.end.lat,
      }),
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "경로를 찾지 못했습니다.");
    }

    drawRoute(data.route_geojson, data.summary);
    renderSummary(data.summary);
    setStatus("최단시간 경로");
  } catch (error) {
    clearRoute();
    elements.summaryPanel.hidden = true;
    setStatus(error.message);
  } finally {
    elements.routeButton.disabled = !(state.start && state.end);
  }
}

function drawRoute(geojson, summary) {
  clearRoute();
  const routeCoords = routeCoordinatesFromGeoJson(geojson);
  console.info(`[route] coordinate count=${routeCoords.length}; Leaflet order=[lat, lon]`, routeCoords[0]);

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
    state.routeLayer.bindTooltip(`${summary.total_time_min.toFixed(1)}분 / ${summary.total_length_m.toFixed(0)}m`);
  }
}

function routeCoordinatesFromGeoJson(geojson) {
  const feature = geojson?.features?.[0];
  const coords = feature?.geometry?.coordinates || [];
  console.info("[route] GeoJSON sample order=[lon, lat]", coords[0]);
  return coords.map(([lon, lat]) => [lat, lon]);
}

function clearRoute() {
  if (state.routeLayer) {
    map.removeLayer(state.routeLayer);
    state.routeLayer = null;
  }
  elements.summaryPanel.hidden = true;
}

function renderSummary(summary) {
  elements.summaryPanel.hidden = false;
  elements.timeValue.textContent = `${summary.total_time_min.toFixed(1)}분`;
  elements.distanceValue.textContent = `${summary.total_length_m.toFixed(0)}m`;
  elements.ascentValue.textContent = `${summary.total_ascent_m.toFixed(0)}m`;
  elements.descentValue.textContent = `${summary.total_descent_m.toFixed(0)}m`;

  const startSnap = summary.start_snap_distance_m;
  const endSnap = summary.end_snap_distance_m;
  if (startSnap > SNAP_WARNING_M || endSnap > SNAP_WARNING_M) {
    elements.snapWarning.hidden = false;
    elements.snapWarning.textContent = `스냅 거리: 출발 ${startSnap.toFixed(1)}m, 도착 ${endSnap.toFixed(1)}m`;
  } else {
    elements.snapWarning.hidden = true;
    elements.snapWarning.textContent = "";
  }
}

function resetSelection() {
  clearRoute();
  clearPoint("start");
  clearPoint("end");
  setStatus("출발지를 선택하세요.");
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
  logGeoJsonLoad(layerName, data);
  return data;
}

function logGeoJsonLoad(layerName, data) {
  const featureCount = data?.features?.length || 0;
  const sample = firstCoordinate(data?.features?.[0]?.geometry);
  console.info(`[layer:${layerName}] loaded features=${featureCount}; GeoJSON sample order=[lon, lat]`, sample);
}

function firstCoordinate(geometry) {
  if (!geometry) return null;
  if (geometry.type === "Point") return geometry.coordinates;
  if (geometry.type === "LineString") return geometry.coordinates?.[0] || null;
  if (geometry.type === "Polygon") return geometry.coordinates?.[0]?.[0] || null;
  if (geometry.type === "MultiPolygon") return geometry.coordinates?.[0]?.[0]?.[0] || null;
  return null;
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

  if (layerName === "lawn_plaza") {
    const polygonLayer = L.geoJSON(data, {
      pane: "plazaPane",
      interactive: false,
      filter: (feature) => feature.geometry?.type !== "Point",
      style: {
        color: "#16a34a",
        weight: 2,
        fillColor: "#86efac",
        fillOpacity: 0.22,
      },
    });

    const gateLayer = L.geoJSON(data, {
      pane: "pointPane",
      filter: (feature) => feature.geometry?.type === "Point",
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
          .bindTooltip("잔디광장 진입점", { sticky: true })
          .bindPopup(feature.properties?.name || "잔디광장 진입점"),
    });

    return L.layerGroup([polygonLayer, gateLayer]);
  }

  return L.geoJSON(data);
}

function elevationColor(elevation) {
  if (elevation >= 160) return "#7c3aed";
  if (elevation >= 135) return "#dc2626";
  if (elevation >= 115) return "#f59e0b";
  return "#0ea5e9";
}
