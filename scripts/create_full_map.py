from __future__ import annotations

from pathlib import Path

import folium
import geopandas as gpd
import osmnx as ox
from shapely.geometry import Polygon, mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

FULL_HTML_PATH = OUTPUTS_DIR / "snu_walk_full.html"

PLACE_QUERY = "Seoul National University Gwanak Campus"

BASE_NODES_PATH = PROCESSED_DIR / "snu_walk_nodes.geojson"
BASE_EDGES_PATH = PROCESSED_DIR / "snu_walk_edges.geojson"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"
ELEVATION_NODES_PATH = PROCESSED_DIR / "snu_walk_nodes_elevation.geojson"
PLAZA_NODES_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza_nodes.geojson"
PLAZA_EDGES_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza_edges.geojson"

LAWN_PLAZA_POLYGON_LON_LAT = [
    (126.9499099, 37.4604256),
    (126.9506876, 37.4610799),
    (126.9511786, 37.4606035),
    (126.9504057, 37.4600441),
    (126.9499099, 37.4604256),
]


def read_gdf(path: Path) -> gpd.GeoDataFrame | None:
    if not path.exists():
        return None
    return gpd.read_file(path)


def line_coords(geometry):
    return [(lat, lon) for lon, lat in geometry.coords]


def add_base_edges(m: folium.Map, edges: gpd.GeoDataFrame) -> None:
    group = folium.FeatureGroup(name="OSM 보행 엣지", show=True)
    for _, edge in edges.iterrows():
        geometry = edge.geometry
        if geometry is None or geometry.geom_type != "LineString":
            continue
        folium.PolyLine(
            line_coords(geometry),
            color="#2563eb",
            weight=2,
            opacity=0.65,
            tooltip=f"OSM / {float(edge.get('length', 0)):.1f}m",
        ).add_to(group)
    group.add_to(m)


def add_entrances(m: folium.Map, entrances: gpd.GeoDataFrame, boundary) -> None:
    group = folium.FeatureGroup(name="건물 입구", show=True)
    entrances = entrances[entrances.geometry.apply(lambda geometry: boundary.covers(geometry))]
    for _, entrance in entrances.iterrows():
        geometry = entrance.geometry
        if geometry is None or geometry.geom_type != "Point":
            continue
        folium.CircleMarker(
            location=[geometry.y, geometry.x],
            radius=4,
            color="#c2410c",
            weight=1,
            fill=True,
            fill_color="#f97316",
            fill_opacity=0.9,
            tooltip="건물 입구",
            popup=f"entrance={entrance.get('entrance', '')}",
        ).add_to(group)
    group.add_to(m)


def add_elevation_nodes(m: folium.Map, elevation_nodes: gpd.GeoDataFrame, boundary) -> None:
    group = folium.FeatureGroup(name="노드 고도", show=False)
    elevation_nodes = elevation_nodes[
        elevation_nodes.geometry.apply(lambda geometry: boundary.covers(geometry))
    ]
    for _, node in elevation_nodes.iterrows():
        geometry = node.geometry
        elevation = node.get("elevation_m")
        if geometry is None or geometry.geom_type != "Point" or elevation is None:
            continue
        folium.CircleMarker(
            location=[geometry.y, geometry.x],
            radius=5,
            color="#0f766e",
            weight=1,
            fill=True,
            fill_color="#14b8a6",
            fill_opacity=0.85,
            tooltip=f"고도 {float(elevation):.1f}m",
            popup=f"고도={float(elevation):.1f}m",
        ).add_to(group)
    group.add_to(m)


def add_lawn_plaza(m: folium.Map) -> None:
    plaza_polygon = Polygon(LAWN_PLAZA_POLYGON_LON_LAT)
    folium.GeoJson(
        {
            "type": "Feature",
            "properties": {"name": "서울대 잔디광장"},
            "geometry": mapping(plaza_polygon),
        },
        name="서울대 잔디광장 polygon",
        style_function=lambda _: {
            "color": "#16a34a",
            "weight": 2,
            "fillColor": "#86efac",
            "fillOpacity": 0.24,
        },
        tooltip="서울대 잔디광장",
    ).add_to(m)

    plaza_edges = read_gdf(PLAZA_EDGES_PATH)
    if plaza_edges is not None and "source" in plaza_edges.columns:
        group = folium.FeatureGroup(name="잔디광장 가상 엣지", show=True)
        plaza_edges = plaza_edges[plaza_edges["source"] == "manual_lawn_plaza"]
        for _, edge in plaza_edges.iterrows():
            geometry = edge.geometry
            if geometry is None or geometry.geom_type != "LineString":
                continue
            walk_type = edge.get("walk_type", "")
            color = "#dc2626" if walk_type == "plaza_crossing" else "#f97316"
            weight = 4 if walk_type == "plaza_crossing" else 3
            folium.PolyLine(
                line_coords(geometry),
                color=color,
                weight=weight,
                opacity=0.78,
                tooltip=f"{walk_type} / {float(edge.get('length', 0)):.1f}m",
            ).add_to(group)
        group.add_to(m)

    plaza_nodes = read_gdf(PLAZA_NODES_PATH)
    if plaza_nodes is not None and "source" in plaza_nodes.columns:
        group = folium.FeatureGroup(name="잔디광장 진입점", show=True)
        plaza_nodes = plaza_nodes[plaza_nodes["source"] == "manual_lawn_plaza"]
        for _, node in plaza_nodes.iterrows():
            geometry = node.geometry
            if geometry is None or geometry.geom_type != "Point":
                continue
            folium.CircleMarker(
                location=[geometry.y, geometry.x],
                radius=6,
                color="#15803d",
                weight=2,
                fill=True,
                fill_color="#22c55e",
                fill_opacity=0.95,
                tooltip=node.get("name", "잔디광장 진입점"),
                popup=f"{node.get('plaza_name', '')}<br>{node.get('name', '')}",
            ).add_to(group)
        group.add_to(m)


def main() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

    nodes = read_gdf(BASE_NODES_PATH)
    edges = read_gdf(BASE_EDGES_PATH)
    if nodes is None or edges is None:
        raise FileNotFoundError("기본 노드/엣지 GeoJSON을 먼저 생성해야 합니다.")

    boundary = ox.geocode_to_gdf(PLACE_QUERY).geometry.iloc[0]
    center_lat = float(nodes.geometry.y.mean())
    center_lon = float(nodes.geometry.x.mean())

    m = folium.Map(location=[center_lat, center_lon], zoom_start=16, tiles="OpenStreetMap")

    folium.GeoJson(
        {
            "type": "Feature",
            "properties": {"name": "서울대 관악캠퍼스 경계"},
            "geometry": mapping(boundary),
        },
        name="서울대 관악캠퍼스 경계",
        style_function=lambda _: {
            "color": "#ef4444",
            "weight": 2,
            "fillOpacity": 0.02,
        },
        interactive=False,
    ).add_to(m)

    add_base_edges(m, edges)

    entrances = read_gdf(ENTRANCES_PATH)
    if entrances is not None:
        add_entrances(m, entrances, boundary)

    elevation_nodes = read_gdf(ELEVATION_NODES_PATH)
    if elevation_nodes is not None:
        add_elevation_nodes(m, elevation_nodes, boundary)

    add_lawn_plaza(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(FULL_HTML_PATH)
    print(FULL_HTML_PATH)


if __name__ == "__main__":
    main()
