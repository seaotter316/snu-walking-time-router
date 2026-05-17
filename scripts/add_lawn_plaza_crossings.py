from __future__ import annotations

import json
from itertools import combinations
from pathlib import Path

import folium
import geopandas as gpd
import networkx as nx
import osmnx as ox
from shapely.geometry import LineString, Point, Polygon, mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

BASE_GRAPH_PATH = PROCESSED_DIR / "snu_walk_base.graphml"
PLAZA_GRAPH_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza.graphml"
PLAZA_NODES_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza_nodes.geojson"
PLAZA_EDGES_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza_edges.geojson"
HTML_PATH = OUTPUTS_DIR / "snu_walk_with_lawn_plaza.html"

PLACE_QUERY = "Seoul National University Gwanak Campus"

# 사용자가 클릭한 잔디광장 4개 꼭짓점을 주변 OSM 엣지에 스냅해서 만든 polygon이다.
# 좌표는 (경도, 위도) 순서다.
LAWN_PLAZA_POLYGON_LON_LAT = [
    (126.9499099, 37.4604256),
    (126.9506876, 37.4610799),
    (126.9511786, 37.4606035),
    (126.9504057, 37.4600441),
    (126.9499099, 37.4604256),
]

LAWN_PLAZA_GATES = [
    (-100001, "lawn_plaza_gate_west", "잔디광장 서측 꼭짓점", 126.9499099, 37.4604256),
    (-100002, "lawn_plaza_gate_north", "잔디광장 북측 꼭짓점", 126.9506876, 37.4610799),
    (-100003, "lawn_plaza_gate_east", "잔디광장 동측 꼭짓점", 126.9511786, 37.4606035),
    (-100004, "lawn_plaza_gate_south", "잔디광장 남측 꼭짓점", 126.9504057, 37.4600441),
    (-100005, "lawn_plaza_gate_northwest_edge", "잔디광장 북서 변 중앙", 126.9502988, 37.4607528),
    (-100006, "lawn_plaza_gate_southeast_edge", "잔디광장 남동 변 중앙", 126.9507922, 37.4603238),
    (-100007, "lawn_plaza_gate_northeast_osm_junction", "잔디광장 동북측 OSM 연결점", 126.9509070, 37.4608927),
    (-100008, "lawn_plaza_gate_southwest_osm_junction", "잔디광장 서남측 OSM 연결점", 126.9500497, 37.4602836),
]


def ensure_dirs() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def edge_length_m(line: LineString) -> float:
    gdf = gpd.GeoDataFrame(geometry=[line], crs="EPSG:4326").to_crs("EPSG:32652")
    return float(gdf.geometry.length.iloc[0])


def nearest_existing_node(G: nx.MultiDiGraph, lon: float, lat: float):
    points = []
    node_ids = []
    for node_id, data in G.nodes(data=True):
        if data.get("source") == "manual_lawn_plaza":
            continue
        points.append(Point(float(data["x"]), float(data["y"])))
        node_ids.append(node_id)

    nodes_gdf = gpd.GeoDataFrame({"node_id": node_ids}, geometry=points, crs="EPSG:4326")
    target = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs("EPSG:32652").iloc[0]
    projected = nodes_gdf.to_crs("EPSG:32652")
    nearest_index = projected.distance(target).idxmin()
    return nodes_gdf.loc[nearest_index, "node_id"]


def add_edge(
    G: nx.MultiDiGraph,
    u,
    v,
    geometry: LineString,
    walk_type: str,
    source: str = "manual_lawn_plaza",
) -> None:
    length = edge_length_m(geometry)
    attrs = {
        "geometry": geometry,
        "length": length,
        "walk_type": walk_type,
        "source": source,
        "bidirectional": True,
    }
    G.add_edge(u, v, **attrs)
    G.add_edge(v, u, **attrs)


def add_lawn_plaza(G: nx.MultiDiGraph) -> tuple[nx.MultiDiGraph, dict]:
    plaza_polygon = Polygon(LAWN_PLAZA_POLYGON_LON_LAT)
    gate_nodes = []

    for node_id, manual_node_id, name, lon, lat in LAWN_PLAZA_GATES:
        G.add_node(
            node_id,
            x=lon,
            y=lat,
            geometry=Point(lon, lat),
            node_type="plaza_gate",
            manual_node_id=manual_node_id,
            name=name,
            plaza_name="서울대 잔디광장",
            source="manual_lawn_plaza",
        )
        gate_nodes.append(node_id)

        nearest_osm_node = nearest_existing_node(G, lon, lat)
        nearest_data = G.nodes[nearest_osm_node]
        connector = LineString([(lon, lat), (float(nearest_data["x"]), float(nearest_data["y"]))])
        add_edge(G, node_id, nearest_osm_node, connector, "plaza_connector")

    crossing_count = 0
    for u, v in combinations(gate_nodes, 2):
        u_data = G.nodes[u]
        v_data = G.nodes[v]
        crossing = LineString(
            [
                (float(u_data["x"]), float(u_data["y"])),
                (float(v_data["x"]), float(v_data["y"])),
            ]
        )
        add_edge(G, u, v, crossing, "plaza_crossing")
        crossing_count += 2

    stats = {
        "plaza_name": "서울대 잔디광장",
        "manual_gate_nodes": len(gate_nodes),
        "directed_plaza_crossing_edges": crossing_count,
        "directed_plaza_connector_edges": len(gate_nodes) * 2,
        "source_note": "잔디광장을 통과 가능한 면 공간으로 보고 진입점 간 가상 엣지를 추가함",
    }
    return G, stats


def save_graph_outputs(G: nx.MultiDiGraph) -> None:
    ox.save_graphml(G, PLAZA_GRAPH_PATH)
    nodes, edges = ox.graph_to_gdfs(G)
    nodes.reset_index().to_file(PLAZA_NODES_PATH, driver="GeoJSON")
    edges.reset_index().to_file(PLAZA_EDGES_PATH, driver="GeoJSON")


def save_html(G: nx.MultiDiGraph) -> None:
    nodes, edges = ox.graph_to_gdfs(G)
    boundary = ox.geocode_to_gdf(PLACE_QUERY).geometry.iloc[0]
    plaza_polygon = Polygon(LAWN_PLAZA_POLYGON_LON_LAT)

    m = folium.Map(
        location=[float(nodes.geometry.y.mean()), float(nodes.geometry.x.mean())],
        zoom_start=17,
        tiles="OpenStreetMap",
    )

    folium.GeoJson(
        {"type": "Feature", "properties": {"name": "서울대 관악캠퍼스 경계"}, "geometry": mapping(boundary)},
        name="서울대 관악캠퍼스 경계",
        style_function=lambda _: {"color": "#ef4444", "weight": 2, "fillOpacity": 0.02},
        interactive=False,
    ).add_to(m)

    folium.GeoJson(
        {"type": "Feature", "properties": {"name": "서울대 잔디광장"}, "geometry": mapping(plaza_polygon)},
        name="서울대 잔디광장 polygon",
        style_function=lambda _: {"color": "#16a34a", "weight": 2, "fillColor": "#86efac", "fillOpacity": 0.22},
        tooltip="서울대 잔디광장",
    ).add_to(m)

    base_group = folium.FeatureGroup(name="OSM 보행 엣지", show=True)
    plaza_group = folium.FeatureGroup(name="잔디광장 가상 엣지", show=True)
    gate_group = folium.FeatureGroup(name="잔디광장 진입점", show=True)

    for _, edge in edges.iterrows():
        geometry = edge.geometry
        if geometry is None or geometry.geom_type != "LineString":
            continue

        coords = [(lat, lon) for lon, lat in geometry.coords]
        walk_type = edge.get("walk_type", "")
        source = edge.get("source", "")
        if source == "manual_lawn_plaza":
            color = "#dc2626" if walk_type == "plaza_crossing" else "#f97316"
            weight = 4 if walk_type == "plaza_crossing" else 3
            target_group = plaza_group
        else:
            color = "#2563eb"
            weight = 2
            target_group = base_group

        folium.PolyLine(
            coords,
            color=color,
            weight=weight,
            opacity=0.8,
            tooltip=f"{walk_type or 'osm'} / {float(edge.get('length', 0)):.1f}m",
        ).add_to(target_group)

    manual_nodes = nodes[nodes.get("source", "") == "manual_lawn_plaza"]
    for _, node in manual_nodes.iterrows():
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
        ).add_to(gate_group)

    base_group.add_to(m)
    plaza_group.add_to(m)
    gate_group.add_to(m)
    folium.LayerControl().add_to(m)
    m.save(HTML_PATH)


def main() -> None:
    ensure_dirs()
    G = ox.load_graphml(BASE_GRAPH_PATH)
    G, stats = add_lawn_plaza(G)
    save_graph_outputs(G)
    save_html(G)
    (OUTPUTS_DIR / "snu_walk_lawn_plaza_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
