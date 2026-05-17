from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox
from pyproj import Transformer

from .routing import (
    add_time_weights,
    as_float,
    graph_needs_time_weights,
    json_safe,
    normalize_graph_values,
    route_geojson,
    shortest_time_route,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"

TIME_GRAPH_PATH = PROCESSED_DIR / "snu_walk_time_weighted.graphml"
PLAZA_GRAPH_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza.graphml"
BASE_ELEVATION_NODES_PATH = PROCESSED_DIR / "snu_walk_nodes_elevation.geojson"

WITH_PLAZA_EDGES_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza_edges.geojson"
BASE_EDGES_PATH = PROCESSED_DIR / "snu_walk_edges.geojson"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"
ELEVATION_NODES_PATH = PROCESSED_DIR / "snu_walk_nodes_elevation.geojson"
CAMPUS_BOUNDARY_PATH = PROCESSED_DIR / "snu_campus_boundary.geojson"

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True)

LAWN_PLAZA_POLYGON_LON_LAT = [
    [126.9499099, 37.4604256],
    [126.9506876, 37.4610799],
    [126.9511786, 37.4606035],
    [126.9504057, 37.4600441],
    [126.9499099, 37.4604256],
]

CAMPUS_BOUNDARY_LON_LAT = [
    [126.9364, 37.4727],
    [126.9546, 37.4727],
    [126.9602, 37.4644],
    [126.9549, 37.4521],
    [126.9396, 37.4517],
    [126.9309, 37.4588],
    [126.9307, 37.4676],
    [126.9364, 37.4727],
]


@dataclass(frozen=True)
class ProjectedNode:
    node_id: Any
    lon: float
    lat: float
    x: float
    y: float


@dataclass
class RoutingGraph:
    graph: nx.MultiDiGraph
    graph_path: Path
    projected_nodes: list[ProjectedNode]

    def nearest_node(self, lon: float, lat: float) -> tuple[Any, float]:
        target_x, target_y = TO_UTM.transform(lon, lat)
        best_node: Any | None = None
        best_distance_sq = math.inf

        for node in self.projected_nodes:
            distance_sq = (node.x - target_x) ** 2 + (node.y - target_y) ** 2
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_node = node.node_id

        if best_node is None:
            raise ValueError("The routing graph has no nodes.")
        return best_node, math.sqrt(best_distance_sq)

    def route_between_points(self, start_lon: float, start_lat: float, end_lon: float, end_lat: float) -> dict[str, Any]:
        start_node, start_snap_distance_m = self.nearest_node(start_lon, start_lat)
        end_node, end_snap_distance_m = self.nearest_node(end_lon, end_lat)

        route, summary = shortest_time_route(self.graph, start_node, end_node)
        summary.update(
            {
                "start_node": json_safe(start_node),
                "end_node": json_safe(end_node),
                "start_snap_distance_m": round(start_snap_distance_m, 2),
                "end_snap_distance_m": round(end_snap_distance_m, 2),
            }
        )

        return {
            "route_geojson": route_geojson(self.graph, route, summary),
            "summary": summary,
        }


def load_routing_graph() -> RoutingGraph:
    graph_path = TIME_GRAPH_PATH if TIME_GRAPH_PATH.exists() else PLAZA_GRAPH_PATH
    if not graph_path.exists():
        raise FileNotFoundError(
            f"No routing graph found. Expected {TIME_GRAPH_PATH} or {PLAZA_GRAPH_PATH}."
        )

    G = ox.load_graphml(graph_path)
    normalize_graph_values(G)
    attach_missing_elevations(G)

    if graph_path == PLAZA_GRAPH_PATH or graph_needs_time_weights(G):
        add_time_weights(G, recompute=True)
    else:
        add_time_weights(G, recompute=False)

    return RoutingGraph(
        graph=G,
        graph_path=graph_path,
        projected_nodes=build_projected_node_index(G),
    )


def build_projected_node_index(G: nx.MultiDiGraph) -> list[ProjectedNode]:
    nodes: list[ProjectedNode] = []
    for node_id, data in G.nodes(data=True):
        lon = as_float(data.get("x"))
        lat = as_float(data.get("y"))
        x, y = TO_UTM.transform(lon, lat)
        nodes.append(ProjectedNode(node_id=node_id, lon=lon, lat=lat, x=x, y=y))
    return nodes


def attach_missing_elevations(G: nx.MultiDiGraph) -> None:
    references = load_elevation_references()
    elevation_by_node = {node_key(item["node_id"]): item["elevation_m"] for item in references}

    for node_id, data in G.nodes(data=True):
        current = as_float(data.get("elevation_m"), math.nan)
        if math.isfinite(current):
            data["elevation_m"] = current
            continue

        matched = elevation_by_node.get(node_key(node_id))
        if matched is not None:
            data["elevation_m"] = matched
            continue

        nearest = nearest_elevation_reference(data.get("x"), data.get("y"), references)
        data["elevation_m"] = nearest if nearest is not None else 0.0


def node_key(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value)


@lru_cache(maxsize=1)
def load_elevation_references() -> tuple[dict[str, Any], ...]:
    if not BASE_ELEVATION_NODES_PATH.exists():
        return tuple()

    data = read_json(BASE_ELEVATION_NODES_PATH)
    references: list[dict[str, Any]] = []
    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        if len(coords) < 2:
            continue
        elevation_m = as_float(properties.get("elevation_m"), math.nan)
        if not math.isfinite(elevation_m):
            continue
        x, y = TO_UTM.transform(float(coords[0]), float(coords[1]))
        references.append(
            {
                "node_id": properties.get("osmid"),
                "lon": float(coords[0]),
                "lat": float(coords[1]),
                "x": x,
                "y": y,
                "elevation_m": elevation_m,
            }
        )
    return tuple(references)


def nearest_elevation_reference(lon: Any, lat: Any, references: tuple[dict[str, Any], ...]) -> float | None:
    if not references:
        return None

    target_x, target_y = TO_UTM.transform(as_float(lon), as_float(lat))
    best: dict[str, Any] | None = None
    best_distance_sq = math.inf
    for item in references:
        distance_sq = (item["x"] - target_x) ** 2 + (item["y"] - target_y) ** 2
        if distance_sq < best_distance_sq:
            best_distance_sq = distance_sq
            best = item
    return None if best is None else float(best["elevation_m"])


@lru_cache(maxsize=8)
def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def layer_geojson(layer_name: str, graph: nx.MultiDiGraph | None = None) -> dict[str, Any]:
    if layer_name == "campus_boundary":
        return campus_boundary_geojson()
    if layer_name == "osm_edges":
        path = WITH_PLAZA_EDGES_PATH if WITH_PLAZA_EDGES_PATH.exists() else BASE_EDGES_PATH
        return without_manual_lawn_edges(read_json(path))
    if layer_name == "entrances":
        return read_json(ENTRANCES_PATH)
    if layer_name == "elevation_nodes":
        return read_json(ELEVATION_NODES_PATH)
    if layer_name == "lawn_plaza":
        return lawn_plaza_geojson(graph)
    raise KeyError(layer_name)


def without_manual_lawn_edges(data: dict[str, Any]) -> dict[str, Any]:
    features = []
    for feature in data.get("features", []):
        properties = feature.get("properties", {})
        if properties.get("source") == "manual_lawn_plaza":
            continue
        features.append(feature)
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def campus_boundary_geojson() -> dict[str, Any]:
    if CAMPUS_BOUNDARY_PATH.exists():
        return read_json(CAMPUS_BOUNDARY_PATH)

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"kind": "polygon", "name": "campus_boundary"},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [CAMPUS_BOUNDARY_LON_LAT],
                },
            }
        ],
    }


def lawn_plaza_geojson(graph: nx.MultiDiGraph | None) -> dict[str, Any]:
    features: list[dict[str, Any]] = [
        {
            "type": "Feature",
            "properties": {"kind": "polygon", "name": "lawn_plaza"},
            "geometry": {
                "type": "Polygon",
                "coordinates": [LAWN_PLAZA_POLYGON_LON_LAT],
            },
        }
    ]

    if graph is not None:
        for node_id, data in graph.nodes(data=True):
            if data.get("source") != "manual_lawn_plaza":
                continue
            features.append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "gate",
                        "node_id": json_safe(node_id),
                        "name": data.get("manual_node_id") or data.get("name") or "lawn_plaza_gate",
                    },
                    "geometry": {
                        "type": "Point",
                        "coordinates": [as_float(data.get("x")), as_float(data.get("y"))],
                    },
                }
            )

    return {
        "type": "FeatureCollection",
        "features": features,
    }
