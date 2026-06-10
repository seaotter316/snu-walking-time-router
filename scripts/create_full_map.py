from __future__ import annotations

import json
from pathlib import Path
import warnings

import folium
import geopandas as gpd
from shapely.geometry import Polygon, mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANUAL_FEATURES_PATH = PROJECT_ROOT / "data" / "manual" / "walk_network_additions.json"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

FULL_HTML_PATH = OUTPUTS_DIR / "snu_walk_full.html"
BOUNDARY_PATH = PROCESSED_DIR / "snu_campus_boundary.geojson"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"
ROUTING_NODES_PATH = PROCESSED_DIR / "snu_routing_nodes.geojson"
ROUTING_EDGES_PATH = PROCESSED_DIR / "snu_routing_edges.geojson"


def read_gdf(path: Path) -> gpd.GeoDataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required map layer is missing: {path}")
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Could not parse column 'reversed' as JSON; leaving as string")
        return gpd.read_file(path)


def line_coords(geometry) -> list[tuple[float, float]]:
    return [(lat, lon) for lon, lat in geometry.coords]


def add_network_edges(map_: folium.Map, edges: gpd.GeoDataFrame) -> None:
    group = folium.FeatureGroup(name="OSM 보행 엣지", show=True)
    base_edges = edges[edges["source"] != "manual"] if "source" in edges.columns else edges
    rendered_edges: set[tuple[str, str, str, str]] = set()
    for _, edge in base_edges.iterrows():
        if edge.geometry is None or edge.geometry.geom_type != "LineString":
            continue
        edge_key = (
            *sorted((str(edge.get("u")), str(edge.get("v")))),
            str(edge.get("osmid", "")),
            str(edge.get("walk_type", "")),
        )
        if edge_key in rendered_edges:
            continue
        rendered_edges.add(edge_key)
        folium.PolyLine(
            line_coords(edge.geometry),
            color="#2563eb",
            weight=2,
            opacity=0.6,
            tooltip=f"OSM / {float(edge.get('length', 0)):.1f}m",
        ).add_to(group)
    group.add_to(map_)


def add_entrances(map_: folium.Map, entrances: gpd.GeoDataFrame) -> None:
    group = folium.FeatureGroup(name="건물 입구", show=True)
    for _, entrance in entrances.iterrows():
        if entrance.geometry is None or entrance.geometry.geom_type != "Point":
            continue
        folium.CircleMarker(
            location=[entrance.geometry.y, entrance.geometry.x],
            radius=4,
            color="#c2410c",
            fill=True,
            fill_color="#f97316",
            fill_opacity=0.9,
            tooltip="건물 입구",
        ).add_to(group)
    group.add_to(map_)


def add_elevation_nodes(map_: folium.Map, nodes: gpd.GeoDataFrame) -> None:
    group = folium.FeatureGroup(name="노드 고도", show=False)
    for _, node in nodes.iterrows():
        elevation = node.get("elevation_m")
        if node.geometry is None or node.geometry.geom_type != "Point" or elevation is None:
            continue
        folium.CircleMarker(
            location=[node.geometry.y, node.geometry.x],
            radius=4,
            color="#0f766e",
            fill=True,
            fill_color="#14b8a6",
            fill_opacity=0.8,
            tooltip=f"고도 {float(elevation):.1f}m",
        ).add_to(group)
    group.add_to(map_)


def add_manual_features(map_: folium.Map, nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame) -> None:
    config = json.loads(MANUAL_FEATURES_PATH.read_text(encoding="utf-8"))
    for area in config.get("areas", []):
        folium.GeoJson(
            {
                "type": "Feature",
                "properties": {"name": area.get("name", area["id"])},
                "geometry": mapping(Polygon(area["polygon_lon_lat"])),
            },
            name=area.get("name", area["id"]),
            style_function=lambda _: {
                "color": "#16a34a",
                "weight": 2,
                "fillColor": "#86efac",
                "fillOpacity": 0.24,
            },
        ).add_to(map_)

    edge_group = folium.FeatureGroup(name="수동 보정 엣지", show=True)
    if "source" in edges.columns:
        rendered_bidirectional_edges: set[tuple[str, str, str, str]] = set()
        for _, edge in edges[edges["source"] == "manual"].iterrows():
            if edge.geometry is None or edge.geometry.geom_type != "LineString":
                continue
            if str(edge.get("bidirectional", "")).lower() == "true":
                edge_key = (
                    *sorted((str(edge.get("u")), str(edge.get("v")))),
                    str(edge.get("feature_id", "")),
                    str(edge.get("walk_type", "")),
                )
                if edge_key in rendered_bidirectional_edges:
                    continue
                rendered_bidirectional_edges.add(edge_key)
            crossing = edge.get("walk_type") == "plaza_crossing"
            folium.PolyLine(
                line_coords(edge.geometry),
                color="#dc2626" if crossing else "#f97316",
                weight=4 if crossing else 3,
                opacity=0.78,
                tooltip=edge.get("walk_type", "manual"),
            ).add_to(edge_group)
    edge_group.add_to(map_)

    node_group = folium.FeatureGroup(name="수동 보정 노드", show=True)
    if "source" in nodes.columns:
        for _, node in nodes[nodes["source"] == "manual"].iterrows():
            folium.CircleMarker(
                location=[node.geometry.y, node.geometry.x],
                radius=6,
                color="#15803d",
                fill=True,
                fill_color="#22c55e",
                fill_opacity=0.95,
                tooltip=node.get("name", "수동 노드"),
            ).add_to(node_group)
    node_group.add_to(map_)


def add_shuttle_features(map_: folium.Map, nodes: gpd.GeoDataFrame, edges: gpd.GeoDataFrame) -> None:
    group = folium.FeatureGroup(name="셔틀 정류장/노선", show=True)
    if "source" in edges.columns and "walk_type" in edges.columns:
        for _, edge in edges[(edges["source"] == "snu_shuttle") & (edges["walk_type"] == "shuttle_ride")].iterrows():
            if edge.geometry is None or edge.geometry.geom_type != "LineString":
                continue
            folium.PolyLine(
                line_coords(edge.geometry),
                color="#7c3aed",
                weight=4,
                opacity=0.78,
                tooltip=f'{edge.get("from_stop", "")} → {edge.get("to_stop", "")}',
            ).add_to(group)

    if "source" in nodes.columns and "node_role" in nodes.columns:
        for _, node in nodes[(nodes["source"] == "snu_shuttle") & (nodes["node_role"] == "shuttle_stop")].iterrows():
            if node.geometry is None or node.geometry.geom_type != "Point":
                continue
            folium.CircleMarker(
                location=[node.geometry.y, node.geometry.x],
                radius=6,
                color="#6d28d9",
                fill=True,
                fill_color="#a855f7",
                fill_opacity=0.92,
                tooltip=node.get("name", "셔틀 정류장"),
            ).add_to(group)

    group.add_to(map_)


def main() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    boundary = read_gdf(BOUNDARY_PATH)
    entrances = read_gdf(ENTRANCES_PATH)
    nodes = read_gdf(ROUTING_NODES_PATH)
    edges = read_gdf(ROUTING_EDGES_PATH)

    map_ = folium.Map(
        location=[float(nodes.geometry.y.mean()), float(nodes.geometry.x.mean())],
        zoom_start=16,
        tiles="OpenStreetMap",
    )
    folium.GeoJson(
        boundary.__geo_interface__,
        name="서울대 관악캠퍼스 경계",
        style_function=lambda _: {"color": "#ef4444", "weight": 2, "fillOpacity": 0.02},
        interactive=False,
    ).add_to(map_)

    add_network_edges(map_, edges)
    add_entrances(map_, entrances)
    add_elevation_nodes(map_, nodes)
    add_manual_features(map_, nodes, edges)
    add_shuttle_features(map_, nodes, edges)
    folium.LayerControl(collapsed=False).add_to(map_)
    map_.save(FULL_HTML_PATH)
    print(FULL_HTML_PATH)


if __name__ == "__main__":
    main()
