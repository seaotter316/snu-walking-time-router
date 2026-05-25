from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import networkx as nx
import osmnx as ox
from shapely.geometry import Polygon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

PLACE_QUERY = "Seoul National University Gwanak Campus"

# OSM 장소 경계 검색이 실패할 때만 사용하는 예비 polygon이다.
# 서울대 관악캠퍼스 주변을 보수적으로 감싸므로 캠퍼스 밖 길이 일부 포함될 수 있다.
# 좌표는 (경도, 위도) 순서로 작성한다.
SNU_POLYGON_LON_LAT = [
    (126.9364, 37.4727),
    (126.9546, 37.4727),
    (126.9602, 37.4644),
    (126.9549, 37.4521),
    (126.9396, 37.4517),
    (126.9309, 37.4588),
    (126.9307, 37.4676),
    (126.9364, 37.4727),
]


def ensure_dirs() -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def load_graph(source: str) -> tuple[nx.MultiDiGraph, Polygon, str]:
    custom_filter = (
        '["highway"~"footway|path|pedestrian|steps|living_street|residential|service|track"]'
        '["access"!~"private|no"]'
        '["foot"!~"no"]'
    )

    if source == "place":
        try:
            place_gdf = ox.geocode_to_gdf(PLACE_QUERY)
            boundary = place_gdf.geometry.iloc[0]
            graph = ox.graph_from_polygon(
                boundary,
                network_type="walk",
                simplify=True,
                retain_all=True,
                truncate_by_edge=True,
            )
            return graph, boundary, "OSM 서울대 관악캠퍼스 경계"
        except Exception as exc:
            print(f"OSM 장소 경계 검색 실패. 작업용 polygon으로 대체합니다: {exc}")

    polygon = Polygon(SNU_POLYGON_LON_LAT)
    graph = ox.graph_from_polygon(
        polygon,
        custom_filter=custom_filter,
        simplify=True,
        retain_all=True,
        truncate_by_edge=True,
    )
    return graph, polygon, "예비 작업용 경계"


def keep_only_inside_boundary(
    G: nx.MultiDiGraph,
    boundary,
) -> tuple[nx.MultiDiGraph, int, int]:
    nodes, edges = ox.graph_to_gdfs(G)
    original_nodes = len(nodes)
    original_edges = len(edges)

    edges = edges[edges.geometry.apply(lambda geometry: boundary.covers(geometry))]
    kept_node_ids = set(edges.index.get_level_values("u")) | set(edges.index.get_level_values("v"))
    nodes = nodes[nodes.index.isin(kept_node_ids)]
    nodes = nodes[nodes.geometry.apply(lambda geometry: boundary.covers(geometry))]

    kept_node_ids = set(nodes.index)
    edges = edges[
        edges.index.get_level_values("u").isin(kept_node_ids)
        & edges.index.get_level_values("v").isin(kept_node_ids)
    ]

    filtered = ox.convert.graph_from_gdfs(
        nodes,
        edges,
        graph_attrs=G.graph,
    )
    return filtered, original_nodes - len(nodes), original_edges - len(edges)


def latest_overpass_timestamp() -> dict[str, str | None]:
    timestamps = []
    for path in (RAW_DIR / "osmnx_cache").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        if not isinstance(data, dict):
            continue

        timestamp = data.get("osm3s", {}).get("timestamp_osm_base")
        if timestamp:
            timestamps.append(timestamp)

    if not timestamps:
        return {
            "osm_base_timestamp_utc": None,
            "osm_base_timestamp_kst": None,
        }

    timestamp_utc = max(timestamps)
    timestamp_kst = (
        datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
        .astimezone(ZoneInfo("Asia/Seoul"))
        .strftime("%Y-%m-%d %H:%M:%S KST")
    )
    return {
        "osm_base_timestamp_utc": timestamp_utc,
        "osm_base_timestamp_kst": timestamp_kst,
    }


def graph_stats(
    G: nx.MultiDiGraph,
    boundary_name: str,
    removed_nodes: int,
    removed_edges: int,
) -> dict:
    undirected = ox.convert.to_undirected(G)
    components = list(nx.connected_components(undirected))
    largest_component = max((len(c) for c in components), default=0)
    edge_lengths = [
        data.get("length", 0)
        for _, _, _, data in G.edges(keys=True, data=True)
        if data.get("length") is not None
    ]

    return {
        "nodes": len(G.nodes),
        "edges": len(G.edges),
        "connected_components": len(components),
        "largest_component_nodes": largest_component,
        "total_directed_edge_length_m": round(sum(edge_lengths), 2),
        "source_note": "OSMnx/Overpass로 가져온 OpenStreetMap 보행 가능 길",
        "boundary": boundary_name,
        "strict_boundary_removed_nodes": removed_nodes,
        "strict_boundary_removed_edges": removed_edges,
        **latest_overpass_timestamp(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        choices=["polygon", "place"],
        default="place",
        help="작업용 캠퍼스 polygon 또는 OSM 장소 경계를 사용합니다.",
    )
    args = parser.parse_args()

    ensure_dirs()
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW_DIR / "osmnx_cache")
    ox.settings.log_console = True

    G, boundary, boundary_name = load_graph(args.source)
    G = ox.distance.add_edge_lengths(G)
    G, removed_nodes, removed_edges = keep_only_inside_boundary(G, boundary)

    ox.save_graphml(G, PROCESSED_DIR / "snu_walk_base.graphml")

    stats = graph_stats(G, boundary_name, removed_nodes, removed_edges)
    (OUTPUTS_DIR / "snu_walk_base_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
