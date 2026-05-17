from __future__ import annotations

import json
import time
from pathlib import Path

import networkx as nx
import osmnx as ox
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

GRAPH_PATH = PROCESSED_DIR / "snu_walk_base.graphml"
ELEVATION_GRAPH_PATH = PROCESSED_DIR / "snu_walk_elevation.graphml"
ELEVATION_NODES_PATH = PROCESSED_DIR / "snu_walk_nodes_elevation.geojson"
ELEVATION_EDGES_PATH = PROCESSED_DIR / "snu_walk_edges_elevation.geojson"

OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
OPEN_TOPO_DATA_URL = "https://api.opentopodata.org/v1/srtm90m"
BATCH_SIZE = 50


def ensure_dirs() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def request_with_retry(url: str, params: dict[str, str]) -> requests.Response:
    for attempt in range(4):
        response = requests.get(url, params=params, timeout=30)
        if response.status_code != 429:
            response.raise_for_status()
            return response
        time.sleep(2 * (attempt + 1))
    response.raise_for_status()
    return response


def fetch_elevations_open_meteo(
    latitudes: list[float],
    longitudes: list[float],
) -> list[float]:
    elevations: list[float] = []

    for start in range(0, len(latitudes), BATCH_SIZE):
        end = start + BATCH_SIZE
        params = {
            "latitude": ",".join(f"{lat:.7f}" for lat in latitudes[start:end]),
            "longitude": ",".join(f"{lon:.7f}" for lon in longitudes[start:end]),
        }
        response = request_with_retry(OPEN_METEO_ELEVATION_URL, params)
        payload = response.json()
        elevations.extend(payload["elevation"])
        time.sleep(0.5)

    return elevations


def fetch_elevations_open_topo_data(
    latitudes: list[float],
    longitudes: list[float],
) -> list[float]:
    elevations: list[float] = []

    for start in range(0, len(latitudes), BATCH_SIZE):
        end = start + BATCH_SIZE
        locations = "|".join(
            f"{lat:.7f},{lon:.7f}"
            for lat, lon in zip(latitudes[start:end], longitudes[start:end], strict=True)
        )
        response = request_with_retry(OPEN_TOPO_DATA_URL, {"locations": locations})
        payload = response.json()
        elevations.extend(result["elevation"] for result in payload["results"])
        time.sleep(0.5)

    return elevations


def fetch_elevations(latitudes: list[float], longitudes: list[float]) -> tuple[list[float], str]:
    try:
        return (
            fetch_elevations_open_meteo(latitudes, longitudes),
            "Open-Meteo Elevation API, Copernicus DEM GLO-90 기반 고도",
        )
    except requests.HTTPError as exc:
        print(f"Open-Meteo 요청 실패. OpenTopoData SRTM90m으로 대체합니다: {exc}")
        return (
            fetch_elevations_open_topo_data(latitudes, longitudes),
            "OpenTopoData API, SRTM90m 기반 고도",
        )


def add_node_elevations(G: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, str]:
    nodes = list(G.nodes(data=True))
    latitudes = [float(data["y"]) for _, data in nodes]
    longitudes = [float(data["x"]) for _, data in nodes]
    elevations, source_note = fetch_elevations(latitudes, longitudes)

    for (node_id, _), elevation in zip(nodes, elevations, strict=True):
        G.nodes[node_id]["elevation_m"] = float(elevation)

    return G, source_note


def add_edge_grades(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    for u, v, key, data in G.edges(keys=True, data=True):
        length = float(data.get("length", 0) or 0)
        if length <= 0:
            data["grade"] = 0.0
            data["grade_abs"] = 0.0
            continue

        elevation_u = float(G.nodes[u].get("elevation_m", 0))
        elevation_v = float(G.nodes[v].get("elevation_m", 0))
        grade = (elevation_v - elevation_u) / length
        data["grade"] = round(grade, 5)
        data["grade_abs"] = round(abs(grade), 5)

    return G


def save_outputs(G: nx.MultiDiGraph, source_note: str) -> dict:
    ox.save_graphml(G, ELEVATION_GRAPH_PATH)
    nodes, edges = ox.graph_to_gdfs(G)
    nodes.reset_index().to_file(ELEVATION_NODES_PATH, driver="GeoJSON")
    edges.reset_index().to_file(ELEVATION_EDGES_PATH, driver="GeoJSON")

    elevations = [float(data["elevation_m"]) for _, data in G.nodes(data=True)]
    grades = [
        float(data["grade_abs"])
        for _, _, _, data in G.edges(keys=True, data=True)
        if data.get("grade_abs") is not None
    ]
    stats = {
        "nodes_with_elevation": len(elevations),
        "min_elevation_m": min(elevations),
        "max_elevation_m": max(elevations),
        "mean_elevation_m": round(sum(elevations) / len(elevations), 2),
        "max_abs_edge_grade": max(grades) if grades else None,
        "source_note": source_note,
    }
    (OUTPUTS_DIR / "snu_walk_elevation_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats


def main() -> None:
    ensure_dirs()
    G = ox.load_graphml(GRAPH_PATH)
    G, source_note = add_node_elevations(G)
    G = add_edge_grades(G)
    print(json.dumps(save_outputs(G, source_note), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
