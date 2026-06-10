from __future__ import annotations

import math
from typing import Any

import networkx as nx
from pyproj import Transformer
from shapely import wkt
from shapely.geometry import LineString


TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True)

DEFAULT_BASE_WALK_SPEED_KMH = 6.0
MIN_EDGE_TIME_SEC = 0.1

WALK_TYPE_FACTORS = {
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
    "shuttle_connector": 0.95,
}

FIXED_TIME_WALK_TYPES = {
    "building_internal",
    "shuttle_wait",
    "shuttle_ride",
    "shuttle_dwell",
    "shuttle_alight",
}
SHUTTLE_WALK_TYPES = {"shuttle_wait", "shuttle_ride", "shuttle_dwell", "shuttle_alight"}


class RouteNotFound(Exception):
    pass


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str) and value.strip().lower() in {"none", "nan", "null"}:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def json_safe(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    return value


def normalize_graph_values(G: nx.MultiDiGraph) -> nx.MultiDiGraph:
    for _, data in G.nodes(data=True):
        data["x"] = as_float(data.get("x"))
        data["y"] = as_float(data.get("y"))
        if "elevation_m" in data:
            data["elevation_m"] = as_float(data.get("elevation_m"))

    for _, _, _, data in G.edges(keys=True, data=True):
        for key in (
            "length",
            "grade",
            "grade_abs",
            "speed_kmh",
            "time_sec",
            "default_time_sec",
            "base_walk_speed_kmh",
            "wait_time_sec",
            "ride_time_sec",
            "dwell_time_sec",
            "vertical_m",
            "horizontal_time_sec",
            "vertical_time_sec",
        ):
            if key in data:
                data[key] = as_float(data.get(key), math.nan)
        data["walk_type"] = classify_walk_type(data)

    return G


def highway_text(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return " ".join(str(item).lower() for item in value)
    return str(value or "").lower()


def classify_walk_type(data: dict[str, Any]) -> str:
    walk_type = str(data.get("walk_type", "") or "").strip()
    if walk_type and walk_type.lower() not in {"none", "nan", "null"}:
        return walk_type

    highway = highway_text(data.get("highway"))
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


def sanitize_base_walk_speed_kmh(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        return DEFAULT_BASE_WALK_SPEED_KMH
    if value <= 0:
        return DEFAULT_BASE_WALK_SPEED_KMH
    return float(value)


def tobler_speed_kmh(
    slope: float,
    walk_type: str,
    base_speed_kmh: float = DEFAULT_BASE_WALK_SPEED_KMH,
) -> float:
    base_speed_kmh = sanitize_base_walk_speed_kmh(base_speed_kmh)
    speed = base_speed_kmh * math.exp(-3.5 * abs(slope + 0.05))
    speed *= WALK_TYPE_FACTORS.get(walk_type, 1.0)
    return speed


def is_shuttle_edge(data: dict[str, Any]) -> bool:
    return classify_walk_type(data) in SHUTTLE_WALK_TYPES or str(data.get("source", "")) == "snu_shuttle"


def is_fixed_time_edge(data: dict[str, Any]) -> bool:
    walk_type = classify_walk_type(data)
    return walk_type in FIXED_TIME_WALK_TYPES or str(data.get("source", "")) == "building_internal"


def is_outdoor_walk_edge(data: dict[str, Any]) -> bool:
    return not is_fixed_time_edge(data)


def edge_travel_time_sec(data: dict[str, Any], base_walk_speed_kmh: float | None = None) -> float:
    time_sec = as_float(data.get("time_sec"), math.inf)
    if not math.isfinite(time_sec):
        return math.inf
    if time_sec < 0:
        return math.inf
    if not is_outdoor_walk_edge(data):
        return max(time_sec, MIN_EDGE_TIME_SEC)

    reference_speed = as_float(data.get("base_walk_speed_kmh"), DEFAULT_BASE_WALK_SPEED_KMH)
    if not math.isfinite(reference_speed) or reference_speed <= 0:
        reference_speed = DEFAULT_BASE_WALK_SPEED_KMH
    requested_speed = sanitize_base_walk_speed_kmh(base_walk_speed_kmh)
    return max(time_sec * reference_speed / requested_speed, MIN_EDGE_TIME_SEC)


def multiedge_bundle(data: dict[str, Any]) -> bool:
    return bool(data) and all(isinstance(value, dict) for value in data.values())


def dijkstra_weight(
    base_walk_speed_kmh: float | None = None,
    allow_shuttle: bool = True,
):
    def weight(_: Any, __: Any, data: dict[str, Any]) -> float | None:
        if multiedge_bundle(data):
            weights = [
                edge_travel_time_sec(edge_data, base_walk_speed_kmh)
                for edge_data in data.values()
                if allow_shuttle or not is_shuttle_edge(edge_data)
            ]
            return min(weights) if weights else None

        if not allow_shuttle and is_shuttle_edge(data):
            return None
        return edge_travel_time_sec(data, base_walk_speed_kmh)

    return weight


def edge_geometry(G: nx.MultiDiGraph, u: Any, v: Any, data: dict[str, Any]) -> LineString:
    geometry = data.get("geometry")
    if isinstance(geometry, LineString):
        line = geometry
    elif isinstance(geometry, str) and geometry.startswith("LINESTRING"):
        line = wkt.loads(geometry)
    else:
        line = LineString(
            [
                (as_float(G.nodes[u]["x"]), as_float(G.nodes[u]["y"])),
                (as_float(G.nodes[v]["x"]), as_float(G.nodes[v]["y"])),
            ]
        )
    return orient_geometry(G, u, v, line)


def orient_geometry(G: nx.MultiDiGraph, u: Any, v: Any, line: LineString) -> LineString:
    coords = list(line.coords)
    if len(coords) < 2:
        return line

    u_coord = (as_float(G.nodes[u]["x"]), as_float(G.nodes[u]["y"]))
    first = coords[0]
    last = coords[-1]
    first_dist = (first[0] - u_coord[0]) ** 2 + (first[1] - u_coord[1]) ** 2
    last_dist = (last[0] - u_coord[0]) ** 2 + (last[1] - u_coord[1]) ** 2
    if last_dist < first_dist:
        return LineString(list(reversed(coords)))
    return line


def line_length_m(line: LineString) -> float:
    coords = list(line.coords)
    if len(coords) < 2:
        return 0.0

    total = 0.0
    prev_x, prev_y = TO_UTM.transform(coords[0][0], coords[0][1])
    for lon, lat in coords[1:]:
        x, y = TO_UTM.transform(lon, lat)
        total += math.hypot(x - prev_x, y - prev_y)
        prev_x, prev_y = x, y
    return total


def add_time_weights(G: nx.MultiDiGraph, recompute: bool = False) -> nx.MultiDiGraph:
    for u, v, _, data in G.edges(keys=True, data=True):
        geometry = edge_geometry(G, u, v, data)
        length_m = as_float(data.get("length"), math.nan)
        if not math.isfinite(length_m) or length_m <= 0:
            length_m = line_length_m(geometry)

        current_time = as_float(data.get("time_sec"), math.nan)
        walk_type = classify_walk_type(data)
        if is_fixed_time_edge(data) and math.isfinite(current_time) and current_time >= 0:
            data["length"] = length_m
            data["walk_type"] = walk_type
            data["time_sec"] = round(max(current_time, MIN_EDGE_TIME_SEC), 2)
            continue

        if not recompute and math.isfinite(current_time) and current_time > 0:
            data["length"] = length_m
            data["walk_type"] = walk_type
            data["time_sec"] = current_time
            continue

        elevation_u = as_float(G.nodes[u].get("elevation_m"))
        elevation_v = as_float(G.nodes[v].get("elevation_m"))
        slope = (elevation_v - elevation_u) / length_m if length_m > 0 else 0.0
        speed_kmh = tobler_speed_kmh(slope, walk_type)
        speed_mps = speed_kmh * 1000 / 3600
        time_sec = length_m / speed_mps if speed_mps > 0 else math.inf

        data["length"] = length_m
        data["walk_type"] = walk_type
        data["grade"] = round(slope, 6)
        data["grade_abs"] = round(abs(slope), 6)
        data["speed_kmh"] = round(speed_kmh, 4)
        data["time_sec"] = round(time_sec, 2)
        data["default_time_sec"] = round(time_sec, 2)
        data["base_walk_speed_kmh"] = DEFAULT_BASE_WALK_SPEED_KMH

    return G


def graph_needs_time_weights(G: nx.MultiDiGraph) -> bool:
    for _, _, _, data in G.edges(keys=True, data=True):
        time_sec = as_float(data.get("time_sec"), math.nan)
        if not math.isfinite(time_sec) or time_sec <= 0:
            return True
    return False


def shortest_time_route(
    G: nx.MultiDiGraph,
    start_node: Any,
    end_node: Any,
    base_walk_speed_kmh: float | None = None,
    allow_shuttle: bool = True,
) -> tuple[list[Any], dict[str, Any]]:
    base_walk_speed_kmh = sanitize_base_walk_speed_kmh(base_walk_speed_kmh)
    try:
        route = nx.shortest_path(
            G,
            start_node,
            end_node,
            weight=dijkstra_weight(base_walk_speed_kmh, allow_shuttle),
        )
    except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
        raise RouteNotFound(str(exc)) from exc

    total_time = 0.0
    total_length = 0.0
    walking_length = 0.0
    shuttle_length = 0.0
    outdoor_walk_time = 0.0
    building_internal_time = 0.0
    shuttle_wait_time = 0.0
    shuttle_ride_time = 0.0
    shuttle_dwell_time = 0.0
    total_ascent = 0.0
    total_descent = 0.0

    for u, v in zip(route[:-1], route[1:], strict=True):
        data = fastest_edge_data(G, u, v, base_walk_speed_kmh, allow_shuttle)
        elevation_u = as_float(G.nodes[u].get("elevation_m"))
        elevation_v = as_float(G.nodes[v].get("elevation_m"))
        delta = elevation_v - elevation_u
        walk_type = classify_walk_type(data)
        edge_time = edge_travel_time_sec(data, base_walk_speed_kmh)
        edge_length = as_float(data.get("length"))

        total_time += edge_time
        total_length += edge_length
        if walk_type == "shuttle_wait":
            shuttle_wait_time += edge_time
        elif walk_type == "shuttle_ride":
            shuttle_ride_time += edge_time
            shuttle_length += edge_length
        elif walk_type == "shuttle_dwell":
            shuttle_dwell_time += edge_time
        elif walk_type == "shuttle_alight":
            pass
        elif walk_type == "building_internal":
            building_internal_time += edge_time
            walking_length += edge_length
            total_ascent += max(delta, 0.0)
            total_descent += max(-delta, 0.0)
        else:
            outdoor_walk_time += edge_time
            walking_length += edge_length
            total_ascent += max(delta, 0.0)
            total_descent += max(-delta, 0.0)

    summary = {
        "start_node": json_safe(start_node),
        "end_node": json_safe(end_node),
        "total_length_m": round(total_length, 1),
        "walking_length_m": round(walking_length, 1),
        "shuttle_length_m": round(shuttle_length, 1),
        "total_time_sec": round(total_time, 1),
        "total_time_min": round(total_time / 60, 2),
        "outdoor_walk_time_sec": round(outdoor_walk_time, 1),
        "building_internal_time_sec": round(building_internal_time, 1),
        "shuttle_wait_time_sec": round(shuttle_wait_time, 1),
        "shuttle_ride_time_sec": round(shuttle_ride_time, 1),
        "shuttle_dwell_time_sec": round(shuttle_dwell_time, 1),
        "shuttle_time_sec": round(shuttle_wait_time + shuttle_ride_time + shuttle_dwell_time, 1),
        "uses_shuttle": (shuttle_wait_time + shuttle_ride_time + shuttle_dwell_time) > 0,
        "base_walk_speed_kmh": round(base_walk_speed_kmh, 2),
        "total_ascent_m": round(total_ascent, 1),
        "total_descent_m": round(total_descent, 1),
    }
    return route, summary


def fastest_edge_data(
    G: nx.MultiDiGraph,
    u: Any,
    v: Any,
    base_walk_speed_kmh: float | None = None,
    allow_shuttle: bool = True,
) -> dict[str, Any]:
    candidates = G.get_edge_data(u, v)
    if not candidates:
        raise RouteNotFound(f"No edge data for {u!r} -> {v!r}")
    eligible = [
        (key, data)
        for key, data in candidates.items()
        if allow_shuttle or not is_shuttle_edge(data)
    ]
    if not eligible:
        raise RouteNotFound(f"No eligible edge data for {u!r} -> {v!r}")
    _, data = min(eligible, key=lambda item: edge_travel_time_sec(item[1], base_walk_speed_kmh))
    return data


def calibrate_base_walk_speed(
    G: nx.MultiDiGraph,
    start_node: Any,
    end_node: Any,
    actual_time_sec: float,
) -> dict[str, Any]:
    if actual_time_sec <= 0:
        raise ValueError("Calibration time must be positive.")

    _, summary = shortest_time_route(
        G,
        start_node,
        end_node,
        base_walk_speed_kmh=DEFAULT_BASE_WALK_SPEED_KMH,
        allow_shuttle=False,
    )
    scalable_time = as_float(summary.get("outdoor_walk_time_sec"))
    fixed_time = as_float(summary.get("total_time_sec")) - scalable_time
    actual_scalable_time = actual_time_sec - fixed_time
    if scalable_time <= 0 or actual_scalable_time <= 0:
        raise ValueError("Calibration route does not contain enough outdoor walking time.")

    raw_speed = DEFAULT_BASE_WALK_SPEED_KMH * scalable_time / actual_scalable_time
    calibrated_speed = sanitize_base_walk_speed_kmh(raw_speed)
    return {
        "calibrated_base_walk_speed_kmh": round(calibrated_speed, 2),
        "raw_calibrated_base_walk_speed_kmh": round(raw_speed, 2),
        "calibration_clamped": False,
        "calibration_actual_time_sec": round(actual_time_sec, 1),
        "calibration_default_time_sec": summary["total_time_sec"],
        "calibration_fixed_time_sec": round(fixed_time, 1),
        "calibration_outdoor_default_time_sec": round(scalable_time, 1),
    }


def route_geojson(
    G: nx.MultiDiGraph,
    route: list[Any],
    summary: dict[str, Any],
    base_walk_speed_kmh: float | None = None,
    allow_shuttle: bool = True,
) -> dict[str, Any]:
    coordinates: list[list[float]] = []
    for u, v in zip(route[:-1], route[1:], strict=True):
        data = fastest_edge_data(G, u, v, base_walk_speed_kmh, allow_shuttle)
        segment = [[float(lon), float(lat)] for lon, lat in edge_geometry(G, u, v, data).coords]
        if coordinates and segment and coordinates[-1] == segment[0]:
            coordinates.extend(segment[1:])
        else:
            coordinates.extend(segment)

    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "total_length_m": summary["total_length_m"],
                    "total_time_sec": summary["total_time_sec"],
                    "total_time_min": summary["total_time_min"],
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": coordinates,
                },
            }
        ],
    }
