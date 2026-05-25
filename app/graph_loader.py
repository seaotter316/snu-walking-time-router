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
    edge_geometry,
    graph_needs_time_weights,
    json_safe,
    normalize_graph_values,
    route_geojson,
    shortest_time_route,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
MANUAL_FEATURES_PATH = PROJECT_ROOT / "data" / "manual" / "walk_network_additions.json"

ROUTING_GRAPH_PATH = PROCESSED_DIR / "snu_routing_graph.graphml"
ROUTING_EDGES_PATH = PROCESSED_DIR / "snu_routing_edges.geojson"
ROUTING_NODES_PATH = PROCESSED_DIR / "snu_routing_nodes.geojson"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"
CAMPUS_BOUNDARY_PATH = PROCESSED_DIR / "snu_campus_boundary.geojson"

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True)


@dataclass(frozen=True)
class ProjectedNode:
    node_id: Any
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
    if not ROUTING_GRAPH_PATH.exists():
        raise FileNotFoundError(
            f"No routing graph found at {ROUTING_GRAPH_PATH}. Run `python -m scripts.build_routing_graph`."
        )

    graph = ox.load_graphml(ROUTING_GRAPH_PATH)
    normalize_graph_values(graph)
    if graph_needs_time_weights(graph):
        add_time_weights(graph, recompute=True)

    return RoutingGraph(
        graph=graph,
        graph_path=ROUTING_GRAPH_PATH,
        projected_nodes=build_projected_node_index(graph),
    )


def build_projected_node_index(graph: nx.MultiDiGraph) -> list[ProjectedNode]:
    nodes: list[ProjectedNode] = []
    for node_id, data in graph.nodes(data=True):
        x, y = TO_UTM.transform(as_float(data.get("x")), as_float(data.get("y")))
        nodes.append(ProjectedNode(node_id=node_id, x=x, y=y))
    return nodes


@lru_cache(maxsize=8)
def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def layer_geojson(layer_name: str, graph: nx.MultiDiGraph | None = None) -> dict[str, Any]:
    if layer_name == "campus_boundary":
        return read_json(CAMPUS_BOUNDARY_PATH)
    if layer_name == "osm_edges":
        return without_manual_edges(read_json(ROUTING_EDGES_PATH))
    if layer_name == "entrances":
        return read_json(ENTRANCES_PATH)
    if layer_name == "elevation_nodes":
        return read_json(ROUTING_NODES_PATH)
    if layer_name == "manual_features":
        return manual_features_geojson(graph)
    raise KeyError(layer_name)


def without_manual_edges(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            feature
            for feature in data.get("features", [])
            if feature.get("properties", {}).get("source") != "manual"
        ],
    }


def manual_features_geojson(graph: nx.MultiDiGraph | None) -> dict[str, Any]:
    config = read_json(MANUAL_FEATURES_PATH)
    features: list[dict[str, Any]] = []
    for area in config.get("areas", []):
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "area", "feature_id": area["id"], "name": area.get("name", area["id"])},
                "geometry": {"type": "Polygon", "coordinates": [area["polygon_lon_lat"]]},
            }
        )

    if graph is None:
        return {"type": "FeatureCollection", "features": features}

    for node_id, data in graph.nodes(data=True):
        if data.get("source") != "manual":
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "node",
                    "feature_id": data.get("feature_id", ""),
                    "node_id": json_safe(node_id),
                    "name": data.get("name", "수동 노드"),
                },
                "geometry": {
                    "type": "Point",
                    "coordinates": [as_float(data.get("x")), as_float(data.get("y"))],
                },
            }
        )

    rendered_bidirectional_edges: set[tuple[str, str, str, str]] = set()
    for u, v, _, data in graph.edges(keys=True, data=True):
        if data.get("source") != "manual":
            continue
        if str(data.get("bidirectional", "")).lower() == "true":
            edge_key = (
                *sorted((str(u), str(v))),
                str(data.get("feature_id", "")),
                str(data.get("walk_type", "")),
            )
            if edge_key in rendered_bidirectional_edges:
                continue
            rendered_bidirectional_edges.add(edge_key)
        line = edge_geometry(graph, u, v, data)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "kind": "edge",
                    "feature_id": data.get("feature_id", ""),
                    "walk_type": data.get("walk_type", "manual"),
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[float(lon), float(lat)] for lon, lat in line.coords],
                },
            }
        )

    return {"type": "FeatureCollection", "features": features}
