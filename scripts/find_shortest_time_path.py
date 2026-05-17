from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import contextily as cx
import matplotlib.pyplot as plt
import networkx as nx
import osmnx as ox
import requests
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString, Point


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

GRAPH_PATH = PROCESSED_DIR / "snu_walk_with_lawn_plaza.graphml"
BASE_ELEVATION_NODES_PATH = PROCESSED_DIR / "snu_walk_nodes_elevation.geojson"
TIME_GRAPH_PATH = PROCESSED_DIR / "snu_walk_time_weighted.graphml"
ROUTE_HTML_PATH = OUTPUTS_DIR / "snu_shortest_time_route.html"
ROUTE_STATS_PATH = OUTPUTS_DIR / "snu_shortest_time_route_stats.json"

OPEN_METEO_ELEVATION_URL = "https://api.open-meteo.com/v1/elevation"
TO_WEB_MERCATOR = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
TO_LON_LAT = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def load_graph() -> nx.MultiDiGraph:
    if not GRAPH_PATH.exists():
        raise FileNotFoundError(
            "잔디광장 포함 그래프가 없습니다. 먼저 scripts/add_lawn_plaza_crossings.py를 실행하세요."
        )
    return ox.load_graphml(GRAPH_PATH)


def fetch_elevation(lat: float, lon: float) -> float:
    response = requests.get(
        OPEN_METEO_ELEVATION_URL,
        params={"latitude": f"{lat:.7f}", "longitude": f"{lon:.7f}"},
        timeout=30,
    )
    response.raise_for_status()
    return float(response.json()["elevation"][0])


def attach_elevations(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    elevation_by_node: dict[int, float] = {}
    if BASE_ELEVATION_NODES_PATH.exists():
        elevation_nodes = gpd.read_file(BASE_ELEVATION_NODES_PATH)
        for _, row in elevation_nodes.iterrows():
            osmid = row.get("osmid")
            elevation = row.get("elevation_m")
            if osmid is not None and elevation is not None:
                elevation_by_node[int(osmid)] = float(elevation)

    for node_id, data in G.nodes(data=True):
        if "elevation_m" in data and data["elevation_m"] not in (None, ""):
            data["elevation_m"] = as_float(data["elevation_m"])
            continue

        if int(node_id) in elevation_by_node:
            data["elevation_m"] = elevation_by_node[int(node_id)]
            continue

        # 수동으로 추가한 잔디광장 노드는 수가 적으므로 API에서 직접 보완한다.
        data["elevation_m"] = fetch_elevation(as_float(data["y"]), as_float(data["x"]))

    return G


def classify_walk_type(data: dict[str, Any]) -> str:
    walk_type = str(data.get("walk_type", "") or "").strip()
    if walk_type:
        return walk_type

    highway = str(data.get("highway", "") or "").lower()
    if "steps" in highway:
        return "steps"
    if "pedestrian" in highway:
        return "pedestrian"
    if "footway" in highway:
        return "footway"
    if "path" in highway:
        return "path"
    if "service" in highway:
        return "service"
    return "footway"


def tobler_speed_kmh(slope: float, walk_type: str) -> float:
    speed = 6.0 * math.exp(-3.5 * abs(slope + 0.05))

    factors = {
        "footway": 1.00,
        "path": 0.98,
        "pedestrian": 1.00,
        "service": 0.95,
        "plaza_crossing": 1.00,
        "plaza_connector": 0.95,
        "steps": 0.70,
        "entrance_connector": 0.90,
        "indoor": 0.95,
        "shortcut": 0.95,
    }
    speed *= factors.get(walk_type, 1.00)

    return max(1.0, min(speed, 6.0))


def add_time_weights(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    for u, v, key, data in G.edges(keys=True, data=True):
        length_m = as_float(data.get("length"))
        if length_m <= 0:
            length_m = edge_geometry(G, u, v, data).length

        elevation_u = as_float(G.nodes[u].get("elevation_m"))
        elevation_v = as_float(G.nodes[v].get("elevation_m"))
        slope = (elevation_v - elevation_u) / length_m if length_m > 0 else 0.0
        walk_type = classify_walk_type(data)
        speed_kmh = tobler_speed_kmh(slope, walk_type)
        speed_mps = speed_kmh * 1000 / 3600
        time_sec = length_m / speed_mps if speed_mps > 0 else math.inf

        data["length"] = length_m
        data["walk_type"] = walk_type
        data["grade"] = round(slope, 6)
        data["grade_abs"] = round(abs(slope), 6)
        data["speed_kmh"] = round(speed_kmh, 4)
        data["time_sec"] = round(time_sec, 2)

    return G


def edge_geometry(G: nx.MultiDiGraph, u, v, data: dict[str, Any]) -> LineString:
    geometry = data.get("geometry")
    if isinstance(geometry, LineString):
        return geometry
    if isinstance(geometry, str) and geometry.startswith("LINESTRING"):
        return wkt.loads(geometry)
    return LineString(
        [
            (as_float(G.nodes[u]["x"]), as_float(G.nodes[u]["y"])),
            (as_float(G.nodes[v]["x"]), as_float(G.nodes[v]["y"])),
        ]
    )


def nearest_node(G: nx.MultiDiGraph, lon: float, lat: float):
    rows = []
    for node_id, data in G.nodes(data=True):
        rows.append({"node_id": node_id, "geometry": Point(as_float(data["x"]), as_float(data["y"]))})

    nodes = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    projected = nodes.to_crs("EPSG:32652")
    target = gpd.GeoSeries([Point(lon, lat)], crs="EPSG:4326").to_crs("EPSG:32652").iloc[0]
    distances = projected.geometry.distance(target)
    idx = distances.idxmin()
    return nodes.loc[idx, "node_id"], float(distances.loc[idx])


def shortest_time_route(G: nx.MultiDiGraph, start_node, end_node) -> tuple[list[Any], dict]:
    route = nx.shortest_path(G, start_node, end_node, weight="time_sec")

    total_time = 0.0
    total_length = 0.0
    total_ascent = 0.0
    total_descent = 0.0
    route_edges = []

    for u, v in zip(route[:-1], route[1:], strict=True):
        candidates = G.get_edge_data(u, v)
        key, data = min(candidates.items(), key=lambda item: as_float(item[1].get("time_sec"), math.inf))
        elevation_u = as_float(G.nodes[u].get("elevation_m"))
        elevation_v = as_float(G.nodes[v].get("elevation_m"))
        delta = elevation_v - elevation_u
        total_ascent += max(delta, 0)
        total_descent += max(-delta, 0)
        total_time += as_float(data.get("time_sec"))
        total_length += as_float(data.get("length"))
        route_edges.append(
            {
                "u": json_safe(u),
                "v": json_safe(v),
                "key": json_safe(key),
                "length_m": as_float(data.get("length")),
                "time_sec": as_float(data.get("time_sec")),
                "grade": as_float(data.get("grade")),
                "speed_kmh": as_float(data.get("speed_kmh")),
                "walk_type": data.get("walk_type"),
            }
        )

    stats = {
        "start_node": json_safe(start_node),
        "end_node": json_safe(end_node),
        "route_node_count": len(route),
        "route_edge_count": len(route_edges),
        "total_length_m": round(total_length, 1),
        "total_time_sec": round(total_time, 1),
        "total_time_min": round(total_time / 60, 2),
        "total_ascent_m": round(total_ascent, 1),
        "total_descent_m": round(total_descent, 1),
        "model": "Tobler hiking function + walk_type factors, weight=time_sec",
        "route_edges": route_edges,
    }
    return route, stats


def route_line(G: nx.MultiDiGraph, route: list[Any]) -> list[tuple[float, float]]:
    coords: list[tuple[float, float]] = []
    for u, v in zip(route[:-1], route[1:], strict=True):
        candidates = G.get_edge_data(u, v)
        _, data = min(candidates.items(), key=lambda item: as_float(item[1].get("time_sec"), math.inf))
        geom = edge_geometry(G, u, v, data)
        segment = [(lat, lon) for lon, lat in geom.coords]
        if coords and segment and coords[-1] == segment[0]:
            coords.extend(segment[1:])
        else:
            coords.extend(segment)
    return coords


def save_route_html(G: nx.MultiDiGraph, route: list[Any], stats: dict) -> None:
    coords = route_line(G, route)
    center = coords[len(coords) // 2] if coords else [37.46, 126.95]

    m = folium.Map(location=center, zoom_start=17, tiles="OpenStreetMap")

    nodes, edges = ox.graph_to_gdfs(G)
    base_group = folium.FeatureGroup(name="전체 그래프", show=True)
    for _, edge in edges.iterrows():
        geom = edge.geometry
        if geom is None or geom.geom_type != "LineString":
            continue
        source = str(edge.get("source", ""))
        if source == "manual_lawn_plaza":
            continue
        folium.PolyLine(
            [(lat, lon) for lon, lat in geom.coords],
            color="#93c5fd",
            weight=1,
            opacity=0.45,
        ).add_to(base_group)
    base_group.add_to(m)

    folium.PolyLine(
        coords,
        color="#111827",
        weight=7,
        opacity=0.95,
        tooltip=f"최단시간 경로: {stats['total_time_min']}분 / {stats['total_length_m']}m",
    ).add_to(m)

    for label, node_id, color in [
        ("출발", stats["start_node"], "#16a34a"),
        ("도착", stats["end_node"], "#dc2626"),
    ]:
        node = G.nodes[node_id]
        folium.CircleMarker(
            location=[as_float(node["y"]), as_float(node["x"])],
            radius=8,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.95,
            popup=f"{label}<br>node={node_id}",
        ).add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    m.save(ROUTE_HTML_PATH)


def save_outputs(G: nx.MultiDiGraph, route: list[Any], stats: dict) -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, TIME_GRAPH_PATH)
    save_route_html(G, route, stats)
    ROUTE_STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare_graph() -> nx.MultiDiGraph:
    G = load_graph()
    G = attach_elevations(G)
    G = add_time_weights(G)
    return G


def draw_picker_context(ax, G: nx.MultiDiGraph) -> None:
    _, edges = ox.graph_to_gdfs(G)
    edges = edges.to_crs("EPSG:3857")
    edges.plot(ax=ax, color="#93c5fd", linewidth=0.8, alpha=0.75)

    manual_edges = edges[edges.get("source", "") == "manual_lawn_plaza"] if "source" in edges.columns else []
    if len(manual_edges) > 0:
        manual_edges.plot(ax=ax, color="#dc2626", linewidth=1.6, alpha=0.9)

    min_x, min_y = TO_WEB_MERCATOR.transform(126.9478, 37.4578)
    max_x, max_y = TO_WEB_MERCATOR.transform(126.9540, 37.4624)
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    cx.add_basemap(ax, source=cx.providers.OpenStreetMap.Mapnik, zoom=17, alpha=0.95)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="#d1d5db", linewidth=0.5, alpha=0.7)
    ax.set_xlabel("Web Mercator X")
    ax.set_ylabel("Web Mercator Y")


def interactive_pick_and_route(G: nx.MultiDiGraph) -> None:
    clicks: list[tuple[float, float]] = []
    fig, ax = plt.subplots(figsize=(10, 8))
    draw_picker_context(ax, G)
    ax.set_title("출발 지점과 도착 지점을 순서대로 클릭하세요")
    points = ax.scatter([], [], s=90, color="#111827", zorder=5)

    def redraw() -> None:
        points.set_offsets(clicks)
        ax.set_title(f"출발/도착 선택: {len(clicks)} / 2")
        fig.canvas.draw_idle()

    def onclick(event) -> None:
        if event.inaxes != ax or event.xdata is None or event.ydata is None:
            return
        if len(clicks) >= 2:
            return
        clicks.append((float(event.xdata), float(event.ydata)))
        redraw()
        if len(clicks) == 2:
            start_lon, start_lat = TO_LON_LAT.transform(clicks[0][0], clicks[0][1])
            end_lon, end_lat = TO_LON_LAT.transform(clicks[1][0], clicks[1][1])
            start_node, start_dist = nearest_node(G, start_lon, start_lat)
            end_node, end_dist = nearest_node(G, end_lon, end_lat)
            route, stats = shortest_time_route(G, start_node, end_node)
            stats["start_click_nearest_distance_m"] = round(start_dist, 2)
            stats["end_click_nearest_distance_m"] = round(end_dist, 2)
            save_outputs(G, route, stats)
            print(json.dumps(stats, ensure_ascii=False, indent=2))
            print(f"경로 HTML: {ROUTE_HTML_PATH}")

    def onkey(event) -> None:
        if event.key in {"backspace", "delete"} and clicks:
            clicks.pop()
            redraw()
        elif event.key == "escape":
            clicks.clear()
            redraw()

    fig.canvas.mpl_connect("button_press_event", onclick)
    fig.canvas.mpl_connect("key_press_event", onkey)
    print("지도 창에서 출발 지점과 도착 지점을 순서대로 클릭하세요.")
    print("Backspace/Delete: 마지막 클릭 삭제, Esc: 전체 삭제")
    plt.show()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-node", type=int)
    parser.add_argument("--end-node", type=int)
    parser.add_argument("--start-lon", type=float)
    parser.add_argument("--start-lat", type=float)
    parser.add_argument("--end-lon", type=float)
    parser.add_argument("--end-lat", type=float)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="matplotlib 창에서 출발/도착 지점을 클릭합니다.",
    )
    args = parser.parse_args()

    G = prepare_graph()

    if args.start_node is not None and args.end_node is not None:
        start_node = args.start_node
        end_node = args.end_node
    elif None not in (args.start_lon, args.start_lat, args.end_lon, args.end_lat):
        start_node, start_dist = nearest_node(G, args.start_lon, args.start_lat)
        end_node, end_dist = nearest_node(G, args.end_lon, args.end_lat)
    else:
        interactive_pick_and_route(G)
        return

    route, stats = shortest_time_route(G, start_node, end_node)
    if "start_dist" in locals():
        stats["start_click_nearest_distance_m"] = round(start_dist, 2)
        stats["end_click_nearest_distance_m"] = round(end_dist, 2)
    save_outputs(G, route, stats)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"경로 HTML: {ROUTE_HTML_PATH}")


if __name__ == "__main__":
    main()
