from __future__ import annotations

from itertools import combinations
import json
import math
from pathlib import Path
from typing import Any

import networkx as nx
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, Point

from app.routing import add_time_weights, normalize_graph_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANUAL_FEATURES_PATH = PROJECT_ROOT / "data" / "manual" / "walk_network_additions.json"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

ELEVATION_GRAPH_PATH = PROCESSED_DIR / "snu_walk_elevation.graphml"
ROUTING_GRAPH_PATH = PROCESSED_DIR / "snu_routing_graph.graphml"
ROUTING_NODES_PATH = PROCESSED_DIR / "snu_routing_nodes.geojson"
ROUTING_EDGES_PATH = PROCESSED_DIR / "snu_routing_edges.geojson"
STATS_PATH = OUTPUTS_DIR / "snu_routing_graph_stats.json"

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True)


def load_manual_features() -> dict[str, Any]:
    return json.loads(MANUAL_FEATURES_PATH.read_text(encoding="utf-8"))


def nearest_network_node(G: nx.MultiDiGraph, lon: float, lat: float) -> Any:
    target_x, target_y = TO_UTM.transform(lon, lat)
    nearest_id: Any | None = None
    nearest_distance_sq = math.inf

    for node_id, data in G.nodes(data=True):
        if data.get("source") == "manual":
            continue
        x, y = TO_UTM.transform(float(data["x"]), float(data["y"]))
        distance_sq = (x - target_x) ** 2 + (y - target_y) ** 2
        if distance_sq < nearest_distance_sq:
            nearest_id = node_id
            nearest_distance_sq = distance_sq

    if nearest_id is None:
        raise ValueError("The base walking graph has no nodes.")
    return nearest_id


def line_length_m(line: LineString) -> float:
    coordinates = list(line.coords)
    total = 0.0
    for start, end in zip(coordinates[:-1], coordinates[1:], strict=True):
        start_x, start_y = TO_UTM.transform(*start)
        end_x, end_y = TO_UTM.transform(*end)
        total += math.hypot(end_x - start_x, end_y - start_y)
    return total


def add_edge(
    G: nx.MultiDiGraph,
    u: Any,
    v: Any,
    walk_type: str,
    feature_id: str,
    bidirectional: bool = True,
) -> int:
    line = LineString(
        [
            (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
            (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
        ]
    )
    attributes = {
        "geometry": line,
        "length": line_length_m(line),
        "walk_type": walk_type,
        "source": "manual",
        "feature_id": feature_id,
        "bidirectional": bidirectional,
    }
    G.add_edge(u, v, **attributes)
    if bidirectional:
        G.add_edge(v, u, **attributes)
        return 2
    return 1


def add_manual_features(G: nx.MultiDiGraph, config: dict[str, Any]) -> dict[str, int]:
    connector_edges = 0
    explicit_edges = 0
    area_edges = 0

    for node in config.get("nodes", []):
        node_id = node["id"]
        nearest_id = nearest_network_node(G, node["x"], node["y"])
        elevation = node.get("elevation_m", G.nodes[nearest_id].get("elevation_m", 0.0))
        G.add_node(
            node_id,
            x=float(node["x"]),
            y=float(node["y"]),
            geometry=Point(float(node["x"]), float(node["y"])),
            elevation_m=float(elevation),
            name=node.get("name", str(node_id)),
            feature_id=node.get("feature_id", ""),
            source="manual",
        )
        if node.get("connect_to_network"):
            connector_edges += add_edge(
                G,
                node_id,
                nearest_id,
                node.get("connector_walk_type", "connector"),
                node.get("feature_id", ""),
            )

    for edge in config.get("edges", []):
        explicit_edges += add_edge(
            G,
            edge["u"],
            edge["v"],
            edge.get("walk_type", "shortcut"),
            edge.get("feature_id", ""),
            bool(edge.get("bidirectional", True)),
        )

    for area in config.get("areas", []):
        if not area.get("fully_connected"):
            continue
        node_ids = [
            node["id"] for node in config.get("nodes", []) if node.get("feature_id") == area["id"]
        ]
        for u, v in combinations(node_ids, 2):
            area_edges += add_edge(G, u, v, area.get("walk_type", "shortcut"), area["id"])

    return {
        "manual_nodes": len(config.get("nodes", [])),
        "connector_edges": connector_edges,
        "explicit_edges": explicit_edges,
        "area_edges": area_edges,
    }


def main() -> None:
    if not ELEVATION_GRAPH_PATH.exists():
        raise FileNotFoundError(
            "고도 그래프가 없습니다. 먼저 `python -m scripts.add_elevation_to_walk_graph`를 실행하세요."
        )

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    graph = ox.load_graphml(ELEVATION_GRAPH_PATH)
    normalize_graph_values(graph)
    manual_stats = add_manual_features(graph, load_manual_features())
    add_time_weights(graph, recompute=True)

    ox.save_graphml(graph, ROUTING_GRAPH_PATH)
    nodes, edges = ox.graph_to_gdfs(graph)
    nodes.reset_index().to_file(ROUTING_NODES_PATH, driver="GeoJSON")
    edges.reset_index().to_file(ROUTING_EDGES_PATH, driver="GeoJSON")

    stats = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        **manual_stats,
        "weight": "time_sec",
        "elevation_for_manual_nodes": "nearest connected network node unless elevation_m is configured",
    }
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
