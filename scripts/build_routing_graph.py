from __future__ import annotations

import csv
from itertools import combinations
import json
import math
from pathlib import Path
import statistics
from typing import Any
from zipfile import ZipFile
import xml.etree.ElementTree as ET

import networkx as nx
import osmnx as ox
from pyproj import Transformer
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from app.routing import MIN_EDGE_TIME_SEC, add_time_weights, edge_geometry, normalize_graph_values


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANUAL_FEATURES_PATH = PROJECT_ROOT / "data" / "manual" / "walk_network_additions.json"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
MEASURED_DIR = PROJECT_ROOT / "measured_data"

ELEVATION_GRAPH_PATH = PROCESSED_DIR / "snu_walk_elevation.graphml"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"
SHUTTLE_STOPS_PATH = PROCESSED_DIR / "snu_shuttle_stops.geojson"
SHUTTLE_ROUTE_PATH = PROCESSED_DIR / "snu_shuttle_circular_route.json"
ROUTING_GRAPH_PATH = PROCESSED_DIR / "snu_routing_graph.graphml"
ROUTING_NODES_PATH = PROCESSED_DIR / "snu_routing_nodes.geojson"
ROUTING_EDGES_PATH = PROCESSED_DIR / "snu_routing_edges.geojson"
BUILDING_ENTRANCE_MATCHES_PATH = PROCESSED_DIR / "snu_building_entrance_matches.json"
STATS_PATH = OUTPUTS_DIR / "snu_routing_graph_stats.json"
STAIR_MEASUREMENTS_PATH = MEASURED_DIR / "계단_측정.csv"
SHUTTLE_HEADWAY_PATH = MEASURED_DIR / "배차간격_측정.csv"
SHUTTLE_TIMES_PATH = MEASURED_DIR / "셔틀_데이터_정리.xlsx"

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True)
FROM_UTM = Transformer.from_crs("EPSG:32652", "EPSG:4326", always_xy=True)
DEFAULT_FLOOR_HEIGHT_M = 3.0
INDOOR_WALK_SPEED_MPS = 1.2
DEFAULT_STAIR_UP_SEC_PER_FLOOR = 18.18
DEFAULT_STAIR_DOWN_SEC_PER_FLOOR = 11.54
DEFAULT_SHUTTLE_WAIT_SEC = 198.87
DEFAULT_SHUTTLE_SPEED_KMH = 20.0
SHUTTLE_STOP_NODE_BASE = -500000
SHUTTLE_DEPART_NODE_BASE = -510000
SHUTTLE_ARRIVE_NODE_BASE = -520000
SNAP_ENDPOINT_RATIO_EPSILON = 1e-6
SNAP_DISTANCE_EPSILON_M = 0.05
MIN_EDGE_LENGTH_M = 0.05
EXACT_DUPLICATE_HAUSDORFF_M = 0.25
EXACT_DUPLICATE_LENGTH_DELTA_M = 1.0
NEAR_DUPLICATE_DISTANCE_M = 0.75
NEAR_DUPLICATE_COVERAGE = 0.9
AUTO_STITCH_MAX_COMPONENT_SIZE = 12
AUTO_STITCH_MAX_DISTANCE_M = 8.0
AUTO_STITCH_NODE_BASE = -700000
AUTO_STITCH_FEATURE_ID = "auto_component_stitch"
AUTO_STITCH_WALK_TYPE = "entrance_connector"
NEAR_NODE_STITCH_DISTANCE_M = 2.5
NEAR_SHUTTLE_NODE_STITCH_DISTANCE_M = 12.0
NEAR_NODE_MAX_ELEVATION_DELTA_M = 5.0
NEAR_NODE_STITCH_FEATURE_ID = "auto_near_node_stitch"
WALK_TYPE_PRIORITY = {
    "pedestrian": 0,
    "footway": 1,
    "path": 2,
    "steps": 3,
    "service": 4,
}
SHUTTLE_MEASURED_NAME_ALIASES = {
    "신소재공동연구소": "신소재",
    "유전공학연구소": "유전공학",
    "기숙사삼거리": "기숙사",
}


def load_manual_features() -> dict[str, Any]:
    return json.loads(MANUAL_FEATURES_PATH.read_text(encoding="utf-8"))


def load_entrance_features() -> dict[str, Any]:
    if not ENTRANCES_PATH.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(ENTRANCES_PATH.read_text(encoding="utf-8"))


def load_building_entrance_matches() -> list[dict[str, Any]]:
    if not BUILDING_ENTRANCE_MATCHES_PATH.exists():
        return []
    return json.loads(BUILDING_ENTRANCE_MATCHES_PATH.read_text(encoding="utf-8"))


def load_shuttle_stops() -> dict[str, Any]:
    if not SHUTTLE_STOPS_PATH.exists():
        return {"type": "FeatureCollection", "features": []}
    return json.loads(SHUTTLE_STOPS_PATH.read_text(encoding="utf-8"))


def load_shuttle_route() -> list[dict[str, Any]]:
    if not SHUTTLE_ROUTE_PATH.exists():
        return []
    return json.loads(SHUTTLE_ROUTE_PATH.read_text(encoding="utf-8"))


def parse_float(value: Any) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def read_csv_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


def robust_mean(values: list[float | None]) -> tuple[float | None, list[float]]:
    samples = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not samples:
        return None, []
    if len(samples) < 4:
        return statistics.mean(samples), samples

    median = statistics.median(samples)
    deviations = [abs(value - median) for value in samples]
    mad = statistics.median(deviations)
    threshold = max(45.0, 3.0 * 1.4826 * mad)
    filtered = [value for value in samples if abs(value - median) <= threshold]
    if not filtered:
        filtered = samples
    return statistics.mean(filtered), filtered


def load_stair_time_model() -> dict[str, Any]:
    up_sec_per_floor = DEFAULT_STAIR_UP_SEC_PER_FLOOR
    down_sec_per_floor = DEFAULT_STAIR_DOWN_SEC_PER_FLOOR
    source = "defaults"

    for row in read_csv_rows(STAIR_MEASUREMENTS_PATH):
        if len(row) > 6 and row[0].strip() == "올라가기" and row[1].strip() == "평균":
            up_sec_per_floor = parse_float(row[6]) or up_sec_per_floor
            source = str(STAIR_MEASUREMENTS_PATH.relative_to(PROJECT_ROOT))
        if len(row) > 6 and row[0].strip() == "내려가기" and row[1].strip() == "평균":
            down_sec_per_floor = parse_float(row[6]) or down_sec_per_floor
            source = str(STAIR_MEASUREMENTS_PATH.relative_to(PROJECT_ROOT))

    return {
        "up_sec_per_floor": round(up_sec_per_floor, 2),
        "down_sec_per_floor": round(down_sec_per_floor, 2),
        "floor_height_m": DEFAULT_FLOOR_HEIGHT_M,
        "source": source,
    }


def load_shuttle_headway_model() -> dict[str, Any]:
    average_headway_sec = None
    expected_wait_sec = None
    source = "defaults"

    for row in read_csv_rows(SHUTTLE_HEADWAY_PATH):
        if not row:
            continue
        label = row[0].strip()
        if label == "평균 배차간격 E[H]":
            average_headway_sec = parse_float(row[1])
            source = str(SHUTTLE_HEADWAY_PATH.relative_to(PROJECT_ROOT))
        if label in {"권장 적용값", "보정 기대대기 Osuna-Newell"}:
            expected_wait_sec = parse_float(row[1]) or expected_wait_sec
            source = str(SHUTTLE_HEADWAY_PATH.relative_to(PROJECT_ROOT))

    return {
        "average_headway_sec": round(average_headway_sec or DEFAULT_SHUTTLE_WAIT_SEC * 2, 2),
        "expected_wait_sec": round(expected_wait_sec or DEFAULT_SHUTTLE_WAIT_SEC, 2),
        "source": source,
    }


def spreadsheet_column_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    result = 0
    for char in letters:
        result = result * 26 + (ord(char.upper()) - 64)
    return result - 1


def read_xlsx_sheets(path: Path) -> dict[str, list[list[str]]]:
    if not path.exists():
        return {}

    namespace = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relationship_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    sheets: dict[str, list[list[str]]] = {}

    with ZipFile(path) as archive:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall("main:si", namespace):
                shared_strings.append("".join(text.text or "" for text in item.findall(".//main:t", namespace)))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        for sheet in workbook.findall("main:sheets/main:sheet", namespace):
            sheet_name = sheet.attrib["name"]
            target = targets[sheet.attrib[relationship_id]]
            if not target.startswith("xl/"):
                target = f"xl/{target}"
            root = ET.fromstring(archive.read(target))
            rows: list[list[str]] = []
            for row in root.findall(".//main:sheetData/main:row", namespace):
                values: dict[int, str] = {}
                max_column = -1
                for cell in row.findall("main:c", namespace):
                    index = spreadsheet_column_index(cell.attrib.get("r", ""))
                    max_column = max(max_column, index)
                    value = ""
                    raw_value = cell.find("main:v", namespace)
                    if raw_value is not None and raw_value.text is not None:
                        if cell.attrib.get("t") == "s":
                            value = shared_strings[int(raw_value.text)]
                        else:
                            value = raw_value.text
                    elif cell.attrib.get("t") == "inlineStr":
                        text = cell.find(".//main:t", namespace)
                        value = text.text if text is not None else ""
                    values[index] = value
                if max_column >= 0:
                    rows.append([values.get(index, "") for index in range(max_column + 1)])
            sheets[sheet_name] = rows

    return sheets


def measured_stop_name(name: str) -> str:
    return SHUTTLE_MEASURED_NAME_ALIASES.get(name, name)


def load_shuttle_time_model() -> dict[str, Any]:
    sheets = read_xlsx_sheets(SHUTTLE_TIMES_PATH)
    segment_times: dict[str, float] = {}
    dwell_times: dict[str, float] = {}
    segment_samples_used: dict[str, list[float]] = {}

    for row in sheets.get("구간소요시간", []):
        label = row[0].strip() if row else ""
        if "→" not in label:
            continue
        mean_value, used_samples = robust_mean([parse_float(value) for value in row[1:7]])
        if mean_value is None:
            continue
        segment_times[label] = round(mean_value, 2)
        segment_samples_used[label] = [round(value, 2) for value in used_samples]

    for row in sheets.get("정차시간", []):
        stop_name = row[0].strip() if row else ""
        if not stop_name or "평균" in stop_name or stop_name == "정차 시간 (초)":
            continue
        mean_value, _ = robust_mean([parse_float(value) for value in row[1:7]])
        if mean_value is not None:
            dwell_times[stop_name] = round(mean_value, 2)

    headway = load_shuttle_headway_model()
    return {
        "segment_times": segment_times,
        "segment_samples_used": segment_samples_used,
        "dwell_times": dwell_times,
        "expected_wait_sec": headway["expected_wait_sec"],
        "average_headway_sec": headway["average_headway_sec"],
        "source": str(SHUTTLE_TIMES_PATH.relative_to(PROJECT_ROOT)) if SHUTTLE_TIMES_PATH.exists() else "defaults",
        "headway_source": headway["source"],
    }


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


def interpolate_elevation(G: nx.MultiDiGraph, u: Any, v: Any, ratio: float) -> float:
    elevation_u = float(G.nodes[u].get("elevation_m", 0.0) or 0.0)
    elevation_v = float(G.nodes[v].get("elevation_m", 0.0) or 0.0)
    return elevation_u + (elevation_v - elevation_u) * ratio


def nearest_network_edge(
    G: nx.MultiDiGraph,
    lon: float,
    lat: float,
    include_manual: bool = False,
) -> tuple[Any, Any, Any, dict[str, Any], LineString, float, float, float, float]:
    target = Point(*TO_UTM.transform(lon, lat))
    nearest: tuple[Any, Any, Any, dict[str, Any], LineString, float, float, float, float] | None = None
    nearest_distance = math.inf

    for u, v, key, data in G.edges(keys=True, data=True):
        if data.get("source") == "manual" and (
            not include_manual or data.get("walk_type") == "entrance_connector"
        ):
            continue

        line_lon_lat = edge_geometry(G, u, v, data)
        line_utm = LineString([TO_UTM.transform(x, y) for x, y in line_lon_lat.coords])
        if line_utm.length <= 0:
            continue

        projected_distance = line_utm.project(target)
        projected = line_utm.interpolate(projected_distance)
        distance = target.distance(projected)
        if distance >= nearest_distance:
            continue

        ratio = max(0.0, min(1.0, projected_distance / line_utm.length))
        snap_lon, snap_lat = FROM_UTM.transform(projected.x, projected.y)
        nearest = (u, v, key, data, line_lon_lat, snap_lon, snap_lat, ratio, distance)
        nearest_distance = distance

    if nearest is None:
        raise ValueError("The base walking graph has no edges.")
    return nearest


def line_length_m(line: LineString) -> float:
    coordinates = list(line.coords)
    total = 0.0
    for start, end in zip(coordinates[:-1], coordinates[1:], strict=True):
        start_x, start_y = TO_UTM.transform(*start)
        end_x, end_y = TO_UTM.transform(*end)
        total += math.hypot(end_x - start_x, end_y - start_y)
    return total


def graph_node_id(G: nx.MultiDiGraph, node_id: Any) -> Any | None:
    if node_id in G:
        return node_id
    text_id = str(node_id)
    if text_id in G:
        return text_id
    try:
        int_id = int(text_id)
    except (TypeError, ValueError):
        return None
    if int_id in G:
        return int_id
    return None


def add_edge(
    G: nx.MultiDiGraph,
    u: Any,
    v: Any,
    walk_type: str,
    feature_id: str,
    bidirectional: bool = True,
    geometry_lon_lat: list[list[float]] | None = None,
    source: str = "manual",
) -> int:
    if geometry_lon_lat:
        line = LineString([(float(lon), float(lat)) for lon, lat in geometry_lon_lat])
    else:
        line = LineString(
            [
                (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
                (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
            ]
        )
    attributes = {
        "geometry": line,
        "length": max(line_length_m(line), MIN_EDGE_LENGTH_M),
        "walk_type": walk_type,
        "source": source,
        "feature_id": feature_id,
        "bidirectional": bidirectional,
    }
    G.add_edge(u, v, **attributes)
    if bidirectional:
        G.add_edge(v, u, **attributes)
        return 2
    return 1


def add_building_internal_edge(
    G: nx.MultiDiGraph,
    u: Any,
    v: Any,
    building_id: str,
    stair_time_model: dict[str, Any],
) -> None:
    line = LineString(
        [
            (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
            (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
        ]
    )
    horizontal_m = max(line_length_m(line), MIN_EDGE_LENGTH_M)

    def attributes_for_direction(start: Any, end: Any, geometry: LineString) -> dict[str, Any]:
        elevation_start = float(G.nodes[start].get("elevation_m", 0.0) or 0.0)
        elevation_end = float(G.nodes[end].get("elevation_m", 0.0) or 0.0)
        elevation_delta = elevation_end - elevation_start
        vertical_m = abs(elevation_delta)
        floor_delta = vertical_m / DEFAULT_FLOOR_HEIGHT_M if DEFAULT_FLOOR_HEIGHT_M > 0 else 0.0
        if elevation_delta > 0:
            stair_direction = "up"
            sec_per_floor = float(stair_time_model["up_sec_per_floor"])
        elif elevation_delta < 0:
            stair_direction = "down"
            sec_per_floor = float(stair_time_model["down_sec_per_floor"])
        else:
            stair_direction = "level"
            sec_per_floor = 0.0

        horizontal_time_sec = horizontal_m / INDOOR_WALK_SPEED_MPS
        vertical_time_sec = floor_delta * sec_per_floor
        time_sec = horizontal_time_sec + vertical_time_sec
        vertical_speed_mps = vertical_m / vertical_time_sec if vertical_time_sec > 0 else 0.0
        return {
            "geometry": geometry,
            "length": round(horizontal_m, 3),
            "vertical_m": round(vertical_m, 3),
            "floor_delta": round(floor_delta, 3),
            "stair_direction": stair_direction,
            "horizontal_time_sec": round(horizontal_time_sec, 2),
            "vertical_time_sec": round(vertical_time_sec, 2),
            "time_sec": round(max(time_sec, MIN_EDGE_TIME_SEC), 2),
            "walk_type": "building_internal",
            "source": "building_internal",
            "feature_id": building_id,
            "indoor": "true",
            "indoor_walk_speed_mps": INDOOR_WALK_SPEED_MPS,
            "stair_sec_per_floor": round(sec_per_floor, 2),
            "stair_vertical_speed_mps": round(vertical_speed_mps, 4),
            "stair_measurement_source": stair_time_model["source"],
        }

    G.add_edge(u, v, **attributes_for_direction(u, v, line))
    G.add_edge(v, u, **attributes_for_direction(v, u, LineString(list(reversed(line.coords)))))


def add_building_internal_edges(
    G: nx.MultiDiGraph,
    building_matches: list[dict[str, Any]],
    stair_time_model: dict[str, Any],
) -> dict[str, int]:
    buildings_used = 0
    directed_edges_added = 0
    missing_nodes = 0
    single_entrance_buildings = 0

    for building in building_matches:
        building_id = str(building.get("building_id", ""))
        entrance_node_ids: list[Any] = []
        for entrance in building.get("entrances", []):
            node_id = graph_node_id(G, entrance.get("node_id"))
            if node_id is None:
                missing_nodes += 1
                continue
            if node_id not in entrance_node_ids:
                entrance_node_ids.append(node_id)

        if len(entrance_node_ids) < 2:
            single_entrance_buildings += 1
            continue

        buildings_used += 1
        for u, v in combinations(entrance_node_ids, 2):
            add_building_internal_edge(G, u, v, building_id, stair_time_model)
            directed_edges_added += 2

    return {
        "building_internal_buildings_available": len(building_matches),
        "building_internal_buildings_used": buildings_used,
        "building_internal_single_entrance_buildings": single_entrance_buildings,
        "building_internal_missing_nodes": missing_nodes,
        "building_internal_directed_edges_added": directed_edges_added,
        "building_internal_walk_speed_mps": INDOOR_WALK_SPEED_MPS,
        "building_internal_stair_up_sec_per_floor": stair_time_model["up_sec_per_floor"],
        "building_internal_stair_down_sec_per_floor": stair_time_model["down_sec_per_floor"],
        "building_internal_stair_measurement_source": stair_time_model["source"],
    }


def projected_edge_geometry(G: nx.MultiDiGraph, u: Any, v: Any, data: dict[str, Any]) -> LineString:
    return LineString([TO_UTM.transform(x, y) for x, y in edge_geometry(G, u, v, data).coords])


def projected_node_point(G: nx.MultiDiGraph, node_id: Any) -> Point:
    return Point(*TO_UTM.transform(float(G.nodes[node_id]["x"]), float(G.nodes[node_id]["y"])))


def edge_priority(data: dict[str, Any]) -> tuple[int, float, str]:
    walk_type = str(data.get("walk_type", "footway"))
    length = float(data.get("length", math.inf) or math.inf)
    osmid = str(data.get("osmid", ""))
    return (WALK_TYPE_PRIORITY.get(walk_type, 10), length, osmid)


def same_directed_edge_duplicates(
    G: nx.MultiDiGraph,
    edges: list[tuple[Any, Any, Any, dict[str, Any]]],
) -> set[tuple[Any, Any, Any]]:
    removals: set[tuple[Any, Any, Any]] = set()
    grouped: dict[tuple[Any, Any], list[tuple[Any, Any, Any, dict[str, Any], LineString]]] = {}

    for u, v, key, data in edges:
        grouped.setdefault((u, v), []).append((u, v, key, data, projected_edge_geometry(G, u, v, data)))

    for candidates in grouped.values():
        if len(candidates) < 2:
            continue

        remaining = candidates[:]
        while remaining:
            current = remaining.pop(0)
            group = [current]
            rest = []
            for candidate in remaining:
                length_delta = abs(current[4].length - candidate[4].length)
                if (
                    current[4].hausdorff_distance(candidate[4]) <= EXACT_DUPLICATE_HAUSDORFF_M
                    and length_delta <= EXACT_DUPLICATE_LENGTH_DELTA_M
                ):
                    group.append(candidate)
                else:
                    rest.append(candidate)
            remaining = rest

            if len(group) < 2:
                continue
            keep = min(group, key=lambda item: edge_priority(item[3]))
            for item in group:
                if item[:3] != keep[:3]:
                    removals.add(item[:3])

    return removals


def coverage_ratio(reference: LineString, other: LineString, tolerance_m: float) -> float:
    if reference.length <= 0 or other.length <= 0:
        return 0.0
    overlap = reference.buffer(tolerance_m).intersection(other).length
    return overlap / max(reference.length, other.length)


def near_parallel_edge_duplicates(
    G: nx.MultiDiGraph,
    edges: list[tuple[Any, Any, Any, dict[str, Any]]],
) -> set[tuple[Any, Any, Any]]:
    removals: set[tuple[Any, Any, Any]] = set()
    projected = [(u, v, key, data, projected_edge_geometry(G, u, v, data)) for u, v, key, data in edges]

    for index, first in enumerate(projected):
        u1, v1, key1, data1, line1 = first
        for u2, v2, key2, data2, line2 in projected[index + 1 :]:
            if (u1, v1, key1) in removals or (u2, v2, key2) in removals:
                continue
            if {u1, v1} == {u2, v2}:
                continue

            if line1.distance(line2) > NEAR_DUPLICATE_DISTANCE_M:
                continue
            if coverage_ratio(line1, line2, NEAR_DUPLICATE_DISTANCE_M) < NEAR_DUPLICATE_COVERAGE:
                continue

            priority1 = WALK_TYPE_PRIORITY.get(str(data1.get("walk_type", "footway")), 10)
            priority2 = WALK_TYPE_PRIORITY.get(str(data2.get("walk_type", "footway")), 10)
            if priority1 == priority2:
                remove = first if edge_priority(data1) > edge_priority(data2) else (u2, v2, key2, data2, line2)
            else:
                remove = first if priority1 > priority2 else (u2, v2, key2, data2, line2)
            removals.add(remove[:3])

    return removals


def remove_edges_preserving_connectivity(
    G: nx.MultiDiGraph,
    removals: set[tuple[Any, Any, Any]],
) -> int:
    removed = 0
    component_count = nx.number_connected_components(G.to_undirected())

    for u, v, key in sorted(removals, key=lambda item: (str(item[0]), str(item[1]), str(item[2]))):
        if not G.has_edge(u, v, key):
            continue
        edge_data = G.edges[u, v, key].copy()
        G.remove_edge(u, v, key)
        if nx.number_connected_components(G.to_undirected()) > component_count or not nx.has_path(G, u, v):
            G.add_edge(u, v, key=key, **edge_data)
            continue
        removed += 1

    return removed


def consolidate_duplicate_edges(G: nx.MultiDiGraph) -> dict[str, int]:
    base_edges = [
        (u, v, key, data)
        for u, v, key, data in G.edges(keys=True, data=True)
        if data.get("source") not in {"manual", "osm_entrance"}
    ]
    exact_removals = same_directed_edge_duplicates(G, base_edges)
    exact_removed = remove_edges_preserving_connectivity(G, exact_removals)

    base_edges = [
        (u, v, key, data)
        for u, v, key, data in G.edges(keys=True, data=True)
        if data.get("source") not in {"manual", "osm_entrance"}
    ]
    near_removals = near_parallel_edge_duplicates(G, base_edges)
    near_removed = remove_edges_preserving_connectivity(G, near_removals)

    return {
        "duplicate_exact_edges_removed": exact_removed,
        "duplicate_near_edges_removed": near_removed,
    }


def manual_edges_overlapping_base(G: nx.MultiDiGraph) -> set[tuple[Any, Any, Any]]:
    removals: set[tuple[Any, Any, Any]] = set()
    manual_edges = [
        (u, v, key, data, projected_edge_geometry(G, u, v, data))
        for u, v, key, data in G.edges(keys=True, data=True)
        if data.get("source") == "manual"
    ]
    base_edges = [
        (u, v, key, data, projected_edge_geometry(G, u, v, data))
        for u, v, key, data in G.edges(keys=True, data=True)
        if data.get("source") != "manual"
    ]

    for manual_u, manual_v, manual_key, _, manual_line in manual_edges:
        for _, _, _, _, base_line in base_edges:
            if manual_line.distance(base_line) > NEAR_DUPLICATE_DISTANCE_M:
                continue
            if coverage_ratio(manual_line, base_line, NEAR_DUPLICATE_DISTANCE_M) < NEAR_DUPLICATE_COVERAGE:
                continue
            removals.add((manual_u, manual_v, manual_key))
            break

    return removals


def consolidate_manual_base_overlaps(G: nx.MultiDiGraph) -> dict[str, int]:
    removals = manual_edges_overlapping_base(G)
    return {
        "manual_base_overlap_edges_removed": remove_edges_preserving_connectivity(G, removals),
    }


def manual_edges_overlapping_manual(G: nx.MultiDiGraph) -> set[tuple[Any, Any, Any]]:
    removals: set[tuple[Any, Any, Any]] = set()
    manual_edges = [
        (u, v, key, data, projected_edge_geometry(G, u, v, data))
        for u, v, key, data in G.edges(keys=True, data=True)
        if data.get("source") == "manual"
    ]

    for index, first in enumerate(manual_edges):
        u1, v1, key1, data1, line1 = first
        for u2, v2, key2, data2, line2 in manual_edges[index + 1 :]:
            if (u1, v1, key1) in removals or (u2, v2, key2) in removals:
                continue
            if {u1, v1} == {u2, v2}:
                continue
            if line1.distance(line2) > NEAR_DUPLICATE_DISTANCE_M:
                continue
            if coverage_ratio(line1, line2, NEAR_DUPLICATE_DISTANCE_M) < NEAR_DUPLICATE_COVERAGE:
                continue

            if edge_priority(data1) == edge_priority(data2):
                remove = first if float(data1.get("length", math.inf) or math.inf) >= float(data2.get("length", math.inf) or math.inf) else (u2, v2, key2, data2, line2)
            else:
                remove = first if edge_priority(data1) > edge_priority(data2) else (u2, v2, key2, data2, line2)
            removals.add(remove[:3])

    return removals


def consolidate_manual_overlaps(G: nx.MultiDiGraph) -> dict[str, int]:
    removals = manual_edges_overlapping_manual(G)
    return {
        "manual_manual_overlap_edges_removed": remove_edges_preserving_connectivity(G, removals),
    }


def register_edge_split(
    G: nx.MultiDiGraph,
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
    u: Any,
    v: Any,
    key: Any,
    data: dict[str, Any],
    line_lon_lat: LineString,
    split_node_id: Any,
    ratio: float,
    feature_id: str,
) -> None:
    line_utm = projected_edge_geometry(G, u, v, data)
    distance_m = max(0.0, min(line_utm.length, line_utm.length * ratio))
    split_registry.setdefault((u, v, key), []).append(
        {"node_id": split_node_id, "distance_m": distance_m, "feature_id": feature_id}
    )

    reverse_edges = G.get_edge_data(v, u, default={})
    for reverse_key, reverse_data in reverse_edges.items():
        reverse_line = edge_geometry(G, v, u, reverse_data)
        reverse_line_utm = projected_edge_geometry(G, v, u, reverse_data)
        if abs(reverse_line_utm.length - line_utm.length) > EXACT_DUPLICATE_LENGTH_DELTA_M:
            continue
        if line_utm.hausdorff_distance(reverse_line_utm) > EXACT_DUPLICATE_HAUSDORFF_M:
            continue
        split_registry.setdefault((v, u, reverse_key), []).append(
            {
                "node_id": split_node_id,
                "distance_m": max(0.0, min(reverse_line_utm.length, reverse_line_utm.length * (1.0 - ratio))),
                "feature_id": feature_id,
            }
        )


def interpolate_utm(coords: list[tuple[float, float]], distances: list[float], target: float) -> tuple[float, float]:
    if target <= 0:
        return coords[0]
    if target >= distances[-1]:
        return coords[-1]

    for index in range(1, len(coords)):
        previous_distance = distances[index - 1]
        current_distance = distances[index]
        if target > current_distance:
            continue
        segment_length = current_distance - previous_distance
        if segment_length <= 0:
            return coords[index]
        ratio = (target - previous_distance) / segment_length
        start_x, start_y = coords[index - 1]
        end_x, end_y = coords[index]
        return (start_x + (end_x - start_x) * ratio, start_y + (end_y - start_y) * ratio)

    return coords[-1]


def subline_by_distance(line_lon_lat: LineString, start_m: float, end_m: float) -> LineString:
    if end_m < start_m:
        start_m, end_m = end_m, start_m

    lon_lat_coords = list(line_lon_lat.coords)
    utm_coords = [TO_UTM.transform(lon, lat) for lon, lat in lon_lat_coords]
    distances = [0.0]
    for start, end in zip(utm_coords[:-1], utm_coords[1:], strict=True):
        distances.append(distances[-1] + math.hypot(end[0] - start[0], end[1] - start[1]))

    start_m = max(0.0, min(distances[-1], start_m))
    end_m = max(0.0, min(distances[-1], end_m))
    segment_utm = [interpolate_utm(utm_coords, distances, start_m)]
    for coord, distance in zip(utm_coords[1:-1], distances[1:-1], strict=True):
        if start_m < distance < end_m:
            segment_utm.append(coord)
    segment_utm.append(interpolate_utm(utm_coords, distances, end_m))

    segment_lon_lat = [FROM_UTM.transform(x, y) for x, y in segment_utm]
    return LineString(segment_lon_lat)


def coalesced_split_points(
    G: nx.MultiDiGraph,
    split_points: list[dict[str, Any]],
    edge_length_m: float,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for point in sorted(split_points, key=lambda item: item["distance_m"]):
        distance_m = float(point["distance_m"])
        if distance_m <= SNAP_DISTANCE_EPSILON_M or distance_m >= edge_length_m - SNAP_DISTANCE_EPSILON_M:
            continue
        if result and abs(distance_m - float(result[-1]["distance_m"])) <= SNAP_DISTANCE_EPSILON_M:
            if point["node_id"] != result[-1]["node_id"]:
                add_edge(
                    G,
                    point["node_id"],
                    result[-1]["node_id"],
                    "entrance_connector",
                    str(point.get("feature_id", "")),
                )
            continue
        result.append(point)
    return result


def add_directed_split_edge(
    G: nx.MultiDiGraph,
    u: Any,
    v: Any,
    source_data: dict[str, Any],
    geometry: LineString,
) -> None:
    attributes = source_data.copy()
    attributes["geometry"] = geometry
    attributes["length"] = max(line_length_m(geometry), MIN_EDGE_LENGTH_M)
    for stale_key in ("grade", "grade_abs", "speed_kmh", "time_sec"):
        attributes.pop(stale_key, None)
    G.add_edge(u, v, **attributes)


def split_registered_edges(
    G: nx.MultiDiGraph,
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
) -> dict[str, int]:
    removed = 0
    added = 0

    for (u, v, key), split_points in list(split_registry.items()):
        if not G.has_edge(u, v, key):
            continue
        source_data = G.edges[u, v, key].copy()
        line_lon_lat = edge_geometry(G, u, v, source_data)
        line_utm = projected_edge_geometry(G, u, v, source_data)
        points = coalesced_split_points(G, split_points, line_utm.length)
        if not points:
            continue

        G.remove_edge(u, v, key)
        removed += 1

        chain_nodes = [u, *[point["node_id"] for point in points], v]
        chain_distances = [0.0, *[float(point["distance_m"]) for point in points], line_utm.length]
        for start_node, end_node, start_m, end_m in zip(
            chain_nodes[:-1],
            chain_nodes[1:],
            chain_distances[:-1],
            chain_distances[1:],
            strict=True,
        ):
            if start_node == end_node:
                continue
            add_directed_split_edge(G, start_node, end_node, source_data, subline_by_distance(line_lon_lat, start_m, end_m))
            added += 1

    return {
        "split_original_edges_removed": removed,
        "split_edges_added": added,
    }


def is_auto_stitch_candidate_edge(data: dict[str, Any]) -> bool:
    if data.get("source") in {"building_internal", "snu_shuttle"}:
        return False
    return str(data.get("walk_type", "footway")) not in {
        "building_internal",
        "shuttle_wait",
        "shuttle_ride",
        "shuttle_dwell",
        "shuttle_alight",
    }


def split_node_for_edge_distance(
    G: nx.MultiDiGraph,
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
    u: Any,
    v: Any,
    key: Any,
    data: dict[str, Any],
    distance_m: float,
    node_id: Any,
) -> Any:
    line_utm = projected_edge_geometry(G, u, v, data)
    edge_length = line_utm.length
    if edge_length <= 0:
        return u

    distance_m = max(0.0, min(edge_length, distance_m))
    if distance_m <= SNAP_DISTANCE_EPSILON_M:
        return u
    if distance_m >= edge_length - SNAP_DISTANCE_EPSILON_M:
        return v

    ratio = distance_m / edge_length
    point_utm = line_utm.interpolate(distance_m)
    lon, lat = FROM_UTM.transform(point_utm.x, point_utm.y)

    if node_id not in G:
        G.add_node(
            node_id,
            x=float(lon),
            y=float(lat),
            geometry=Point(float(lon), float(lat)),
            elevation_m=float(interpolate_elevation(G, u, v, ratio)),
            name="Auto component stitch",
            feature_id=AUTO_STITCH_FEATURE_ID,
            source="manual",
        )

    register_edge_split(
        G,
        split_registry,
        u,
        v,
        key,
        data,
        edge_geometry(G, u, v, data),
        node_id,
        ratio,
        AUTO_STITCH_FEATURE_ID,
    )
    return node_id


def repair_small_disconnected_components(G: nx.MultiDiGraph) -> dict[str, int | float]:
    components = sorted(nx.connected_components(G.to_undirected()), key=len, reverse=True)
    if len(components) <= 1:
        return {
            "auto_stitch_components_before": len(components),
            "auto_stitch_components_connected": 0,
            "auto_stitch_connector_edges": 0,
            "auto_stitch_split_original_edges_removed": 0,
            "auto_stitch_split_edges_added": 0,
            "auto_stitch_max_distance_m": AUTO_STITCH_MAX_DISTANCE_M,
        }

    main_component = set(components[0])
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    connected_components = 0
    connector_edges = 0

    main_edges = [
        (u, v, key, data, projected_edge_geometry(G, u, v, data))
        for u, v, key, data in G.edges(keys=True, data=True)
        if u in main_component and v in main_component and is_auto_stitch_candidate_edge(data)
    ]

    for component_index, component in enumerate(components[1:], start=1):
        if len(component) > AUTO_STITCH_MAX_COMPONENT_SIZE:
            continue

        component_edges = [
            (u, v, key, data, projected_edge_geometry(G, u, v, data))
            for u, v, key, data in G.edges(keys=True, data=True)
            if u in component and v in component and is_auto_stitch_candidate_edge(data)
        ]
        if not component_edges:
            continue

        best: tuple[
            float,
            tuple[Any, Any, Any, dict[str, Any], LineString],
            tuple[Any, Any, Any, dict[str, Any], LineString],
        ] | None = None
        for component_edge in component_edges:
            for main_edge in main_edges:
                distance = component_edge[4].distance(main_edge[4])
                if distance > AUTO_STITCH_MAX_DISTANCE_M:
                    continue
                if best is None or distance < best[0]:
                    best = (distance, component_edge, main_edge)

        if best is None:
            continue

        distance, component_edge, main_edge = best
        component_point, main_point = nearest_points(component_edge[4], main_edge[4])
        component_distance = component_edge[4].project(component_point)
        main_distance = main_edge[4].project(main_point)

        component_node = split_node_for_edge_distance(
            G,
            split_registry,
            component_edge[0],
            component_edge[1],
            component_edge[2],
            component_edge[3],
            component_distance,
            AUTO_STITCH_NODE_BASE - component_index * 10 - 1,
        )
        main_node = split_node_for_edge_distance(
            G,
            split_registry,
            main_edge[0],
            main_edge[1],
            main_edge[2],
            main_edge[3],
            main_distance,
            AUTO_STITCH_NODE_BASE - component_index * 10 - 2,
        )

        if component_node == main_node:
            continue

        connector_edges += add_edge(
            G,
            component_node,
            main_node,
            AUTO_STITCH_WALK_TYPE,
            AUTO_STITCH_FEATURE_ID,
            source="manual",
        )
        connected_components += 1
        main_component.update(component)

    split_stats = split_registered_edges(G, split_registry)
    return {
        "auto_stitch_components_before": len(components),
        "auto_stitch_components_connected": connected_components,
        "auto_stitch_connector_edges": connector_edges,
        "auto_stitch_split_original_edges_removed": split_stats["split_original_edges_removed"],
        "auto_stitch_split_edges_added": split_stats["split_edges_added"],
        "auto_stitch_max_distance_m": AUTO_STITCH_MAX_DISTANCE_M,
    }


def is_near_node_stitch_candidate(data: dict[str, Any]) -> bool:
    if str(data.get("snap_exclude", "")).lower() == "true":
        return False
    if data.get("source") == "snu_shuttle" and data.get("node_role") != "shuttle_stop":
        return False
    return "x" in data and "y" in data


def is_shuttle_stop_node(data: dict[str, Any]) -> bool:
    return data.get("source") == "snu_shuttle" and data.get("node_role") == "shuttle_stop"


def add_nearby_node_stitches(G: nx.MultiDiGraph) -> dict[str, int | float]:
    nodes = [
        (
            node_id,
            data,
            *TO_UTM.transform(float(data["x"]), float(data["y"])),
            float(data.get("elevation_m", 0.0) or 0.0),
        )
        for node_id, data in G.nodes(data=True)
        if is_near_node_stitch_candidate(data)
    ]

    added = 0
    shuttle_added = 0
    general_added = 0
    skipped_elevation = 0
    for index, first in enumerate(nodes):
        u, u_data, ux, uy, u_elevation = first
        for v, v_data, vx, vy, v_elevation in nodes[index + 1 :]:
            if G.has_edge(u, v) or G.has_edge(v, u):
                continue

            has_shuttle_stop = is_shuttle_stop_node(u_data) or is_shuttle_stop_node(v_data)
            max_distance = NEAR_SHUTTLE_NODE_STITCH_DISTANCE_M if has_shuttle_stop else NEAR_NODE_STITCH_DISTANCE_M
            distance = math.hypot(ux - vx, uy - vy)
            if distance > max_distance:
                continue

            if abs(u_elevation - v_elevation) > NEAR_NODE_MAX_ELEVATION_DELTA_M:
                skipped_elevation += 1
                continue

            walk_type = "shuttle_connector" if has_shuttle_stop else "entrance_connector"
            added += add_edge(
                G,
                u,
                v,
                walk_type,
                NEAR_NODE_STITCH_FEATURE_ID,
                source="manual",
            )
            if has_shuttle_stop:
                shuttle_added += 2
            else:
                general_added += 2

    return {
        "near_node_stitch_edges_added": added,
        "near_node_stitch_general_edges_added": general_added,
        "near_node_stitch_shuttle_edges_added": shuttle_added,
        "near_node_stitch_elevation_skipped": skipped_elevation,
        "near_node_stitch_distance_m": NEAR_NODE_STITCH_DISTANCE_M,
        "near_node_stitch_shuttle_distance_m": NEAR_SHUTTLE_NODE_STITCH_DISTANCE_M,
    }


def add_snap_connection(
    G: nx.MultiDiGraph,
    node_id: Any,
    node: dict[str, Any],
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
) -> int:
    u, v, key, edge_data, line_lon_lat, snap_lon, snap_lat, ratio, distance = nearest_network_edge(
        G,
        node["x"],
        node["y"],
        include_manual=bool(node.get("include_manual_edges", False)),
    )
    feature_id = node.get("feature_id", "")
    walk_type = node.get("connector_walk_type", "entrance_connector")
    connector_source = node.get("connector_source", "manual")

    if ratio <= SNAP_ENDPOINT_RATIO_EPSILON:
        if node_id == u:
            return 0
        return add_edge(G, node_id, u, walk_type, feature_id, source=connector_source)
    if ratio >= 1.0 - SNAP_ENDPOINT_RATIO_EPSILON:
        if node_id == v:
            return 0
        return add_edge(G, node_id, v, walk_type, feature_id, source=connector_source)

    if distance <= SNAP_DISTANCE_EPSILON_M:
        register_edge_split(G, split_registry, u, v, key, edge_data, line_lon_lat, node_id, ratio, feature_id)
        return 0

    default_snap_id = node_id * 1000 if isinstance(node_id, int) else f"{node_id}_snap"
    snap_id = node.get("snap_node_id", default_snap_id)
    snap_elevation = interpolate_elevation(G, u, v, ratio)

    if snap_id not in G:
        G.add_node(
            snap_id,
            x=float(snap_lon),
            y=float(snap_lat),
            geometry=Point(float(snap_lon), float(snap_lat)),
            elevation_m=float(snap_elevation),
            name=f"{node.get('name', node_id)} snap",
            feature_id=feature_id,
            source="manual",
            snap_distance_m=round(distance, 2),
        )

    register_edge_split(G, split_registry, u, v, key, edge_data, line_lon_lat, snap_id, ratio, feature_id)
    return add_edge(G, node_id, snap_id, walk_type, feature_id, source=connector_source)


def entrance_node_id(feature: dict[str, Any]) -> Any | None:
    properties = feature.get("properties", {})
    return properties.get("id") or properties.get("osmid")


def add_osm_entrances(
    G: nx.MultiDiGraph,
    entrances: dict[str, Any],
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
) -> dict[str, int]:
    added_nodes = 0
    already_connected = 0
    connector_edges = 0
    skipped = 0

    for feature in entrances.get("features", []):
        node_id = entrance_node_id(feature)
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [])
        if node_id is None or geometry.get("type") != "Point" or len(coordinates) < 2:
            skipped += 1
            continue

        lon = float(coordinates[0])
        lat = float(coordinates[1])
        properties = feature.get("properties", {})
        name = (
            properties.get("name")
            or properties.get("name:ko")
            or properties.get("name:en")
            or f"OSM entrance {node_id}"
        )

        if node_id in G:
            G.nodes[node_id].setdefault("entrance", properties.get("entrance", "yes"))
            G.nodes[node_id].setdefault("wheelchair", properties.get("wheelchair"))
            if G.degree(node_id) > 0:
                already_connected += 1
                continue
        else:
            u, v, _, _, _, _, _, ratio, _ = nearest_network_edge(G, lon, lat, include_manual=True)
            elevation = interpolate_elevation(G, u, v, ratio)
            G.add_node(
                node_id,
                x=lon,
                y=lat,
                geometry=Point(lon, lat),
                elevation_m=float(elevation),
                name=name,
                entrance=properties.get("entrance", "yes"),
                wheelchair=properties.get("wheelchair"),
                feature_id="osm_entrances",
                source="osm_entrance",
            )
            added_nodes += 1

        connector_edges += add_snap_connection(
            G,
            node_id,
            {
                "id": node_id,
                "x": lon,
                "y": lat,
                "name": name,
                "feature_id": "osm_entrances",
                "connector_walk_type": "entrance_connector",
                "include_manual_edges": True,
            },
            split_registry,
        )

    return {
        "osm_entrance_features": len(entrances.get("features", [])),
        "osm_entrance_nodes_added": added_nodes,
        "osm_entrance_nodes_already_connected": already_connected,
        "osm_entrance_connector_edges": connector_edges,
        "osm_entrance_features_skipped": skipped,
    }


def add_shuttle_stop_nodes(
    G: nx.MultiDiGraph,
    stops: dict[str, Any],
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    added_nodes = 0
    duplicate_stop_features = 0
    connector_edges = 0
    skipped = 0
    stop_node_by_code: dict[str, Any] = {}

    for feature in stops.get("features", []):
        geometry = feature.get("geometry", {})
        coordinates = geometry.get("coordinates", [])
        properties = feature.get("properties", {})
        code = str(properties.get("snu_station_code", "")).strip()
        if geometry.get("type") != "Point" or len(coordinates) < 2 or not code:
            skipped += 1
            continue
        if code in stop_node_by_code:
            duplicate_stop_features += 1
            continue

        lon = float(coordinates[0])
        lat = float(coordinates[1])
        sequence = int(properties.get("sequence") or (len(stop_node_by_code) + 1))
        node_id = SHUTTLE_STOP_NODE_BASE - sequence
        route_name = properties.get("route_stop_name") or properties.get("official_name") or code
        nearest_id = nearest_network_node(G, lon, lat)
        elevation = G.nodes[nearest_id].get("elevation_m", 0.0)

        G.add_node(
            node_id,
            x=lon,
            y=lat,
            geometry=Point(lon, lat),
            elevation_m=float(elevation),
            name=route_name,
            official_name=properties.get("official_name"),
            station_short_code=properties.get("station_short_code"),
            snu_station_code=code,
            shuttle_sequence=sequence,
            original_node_id=properties.get("node_id", ""),
            feature_id=properties.get("feature_id", "snu_circular_shuttle"),
            source="snu_shuttle",
            node_role="shuttle_stop",
        )
        stop_node_by_code[code] = node_id
        added_nodes += 1

        connector_edges += add_snap_connection(
            G,
            node_id,
            {
                "id": node_id,
                "x": lon,
                "y": lat,
                "name": route_name,
                "feature_id": properties.get("feature_id", "snu_circular_shuttle"),
                "connector_walk_type": "shuttle_connector",
                "connector_source": "snu_shuttle_connector",
                "include_manual_edges": True,
            },
            split_registry,
        )

    stats = {
        "shuttle_stop_features": len(stops.get("features", [])),
        "shuttle_stop_nodes_added": added_nodes,
        "shuttle_stop_duplicate_features": duplicate_stop_features,
        "shuttle_stop_connector_edges": connector_edges,
        "shuttle_stop_features_skipped": skipped,
    }
    return stop_node_by_code, stats


def stop_node_coordinates(G: nx.MultiDiGraph, node_id: Any) -> tuple[float, float]:
    return (float(G.nodes[node_id]["x"]), float(G.nodes[node_id]["y"]))


def add_transit_state_node(
    G: nx.MultiDiGraph,
    node_id: Any,
    stop_node_id: Any,
    role: str,
    name_suffix: str,
) -> None:
    if node_id in G:
        return
    stop = G.nodes[stop_node_id]
    G.add_node(
        node_id,
        x=float(stop["x"]),
        y=float(stop["y"]),
        geometry=Point(float(stop["x"]), float(stop["y"])),
        elevation_m=float(stop.get("elevation_m", 0.0) or 0.0),
        name=f"{stop.get('name', stop_node_id)} {name_suffix}",
        snu_station_code=stop.get("snu_station_code", ""),
        feature_id=stop.get("feature_id", "snu_circular_shuttle"),
        source="snu_shuttle",
        node_role=role,
        snap_exclude="true",
    )


def add_fixed_time_edge(
    G: nx.MultiDiGraph,
    u: Any,
    v: Any,
    walk_type: str,
    time_sec: float,
    feature_id: str,
    geometry: LineString | None = None,
    **extra: Any,
) -> None:
    if geometry is None:
        geometry = LineString(
            [
                (float(G.nodes[u]["x"]), float(G.nodes[u]["y"])),
                (float(G.nodes[v]["x"]), float(G.nodes[v]["y"])),
            ]
        )
    attributes = {
        "geometry": geometry,
        "length": max(line_length_m(geometry), 0.0),
        "walk_type": walk_type,
        "source": "snu_shuttle",
        "feature_id": feature_id,
        "time_sec": round(max(float(time_sec), MIN_EDGE_TIME_SEC), 2),
        **extra,
    }
    G.add_edge(u, v, **attributes)


def route_segment_label(origin_name: str, destination_name: str) -> str:
    return f"{measured_stop_name(origin_name)}→{measured_stop_name(destination_name)}"


def fallback_shuttle_time_sec(G: nx.MultiDiGraph, u: Any, v: Any) -> float:
    line = LineString([stop_node_coordinates(G, u), stop_node_coordinates(G, v)])
    speed_mps = DEFAULT_SHUTTLE_SPEED_KMH * 1000 / 3600
    return line_length_m(line) / speed_mps if speed_mps > 0 else 0.0


def add_shuttle_transit_edges(
    G: nx.MultiDiGraph,
    route_entries: list[dict[str, Any]],
    stop_node_by_code: dict[str, Any],
    shuttle_time_model: dict[str, Any],
) -> dict[str, Any]:
    if not route_entries or not stop_node_by_code:
        return {
            "shuttle_route_entries": len(route_entries),
            "shuttle_transit_edges_added": 0,
        }

    feature_id = "snu_circular_shuttle"
    expected_wait_sec = float(shuttle_time_model["expected_wait_sec"])
    segment_times: dict[str, float] = shuttle_time_model["segment_times"]
    dwell_times: dict[str, float] = shuttle_time_model["dwell_times"]
    state_nodes_added = 0
    boarding_edges = 0
    alighting_edges = 0
    dwell_edges = 0
    ride_edges = 0
    missing_route_stops = 0
    fallback_segments: list[str] = []
    depart_node_by_code: dict[str, Any] = {}
    arrive_node_by_code: dict[str, Any] = {}

    for code, stop_node_id in stop_node_by_code.items():
        sequence = int(G.nodes[stop_node_id].get("shuttle_sequence") or (len(depart_node_by_code) + 1))
        depart_node = SHUTTLE_DEPART_NODE_BASE - sequence
        arrive_node = SHUTTLE_ARRIVE_NODE_BASE - sequence
        before = G.number_of_nodes()
        add_transit_state_node(G, depart_node, stop_node_id, "shuttle_depart", "출발")
        add_transit_state_node(G, arrive_node, stop_node_id, "shuttle_arrive", "도착")
        state_nodes_added += G.number_of_nodes() - before
        depart_node_by_code[code] = depart_node
        arrive_node_by_code[code] = arrive_node

        stop_name = measured_stop_name(str(G.nodes[stop_node_id].get("name", "")))
        dwell_sec = dwell_times.get(stop_name, 0.0)
        same_point = LineString([stop_node_coordinates(G, stop_node_id), stop_node_coordinates(G, stop_node_id)])
        add_fixed_time_edge(
            G,
            stop_node_id,
            depart_node,
            "shuttle_wait",
            expected_wait_sec,
            feature_id,
            same_point,
            wait_time_sec=expected_wait_sec,
            from_stop=G.nodes[stop_node_id].get("name", ""),
        )
        boarding_edges += 1
        add_fixed_time_edge(
            G,
            arrive_node,
            stop_node_id,
            "shuttle_alight",
            MIN_EDGE_TIME_SEC,
            feature_id,
            same_point,
            from_stop=G.nodes[stop_node_id].get("name", ""),
        )
        alighting_edges += 1
        add_fixed_time_edge(
            G,
            arrive_node,
            depart_node,
            "shuttle_dwell",
            dwell_sec,
            feature_id,
            same_point,
            dwell_time_sec=dwell_sec,
            from_stop=G.nodes[stop_node_id].get("name", ""),
        )
        dwell_edges += 1

    for origin, destination in zip(route_entries[:-1], route_entries[1:], strict=True):
        origin_code = str(origin.get("snu_station_code", ""))
        destination_code = str(destination.get("snu_station_code", ""))
        if origin_code not in depart_node_by_code or destination_code not in arrive_node_by_code:
            missing_route_stops += 1
            continue

        label = route_segment_label(str(origin.get("route_stop_name", "")), str(destination.get("route_stop_name", "")))
        origin_stop = stop_node_by_code[origin_code]
        destination_stop = stop_node_by_code[destination_code]
        ride_time_sec = segment_times.get(label)
        if ride_time_sec is None:
            ride_time_sec = fallback_shuttle_time_sec(G, origin_stop, destination_stop)
            fallback_segments.append(label)

        line = LineString([stop_node_coordinates(G, origin_stop), stop_node_coordinates(G, destination_stop)])
        add_fixed_time_edge(
            G,
            depart_node_by_code[origin_code],
            arrive_node_by_code[destination_code],
            "shuttle_ride",
            ride_time_sec,
            feature_id,
            line,
            ride_time_sec=ride_time_sec,
            route_segment=label,
            from_stop=origin.get("route_stop_name", ""),
            to_stop=destination.get("route_stop_name", ""),
        )
        ride_edges += 1

    return {
        "shuttle_route_entries": len(route_entries),
        "shuttle_unique_stops": len(stop_node_by_code),
        "shuttle_state_nodes_added": state_nodes_added,
        "shuttle_boarding_edges": boarding_edges,
        "shuttle_alighting_edges": alighting_edges,
        "shuttle_dwell_edges": dwell_edges,
        "shuttle_ride_edges": ride_edges,
        "shuttle_transit_edges_added": boarding_edges + alighting_edges + dwell_edges + ride_edges,
        "shuttle_missing_route_segments": missing_route_stops,
        "shuttle_fallback_segments": fallback_segments,
        "shuttle_expected_wait_sec": expected_wait_sec,
        "shuttle_average_headway_sec": shuttle_time_model["average_headway_sec"],
        "shuttle_time_source": shuttle_time_model["source"],
        "shuttle_headway_source": shuttle_time_model["headway_source"],
    }


def add_manual_features(
    G: nx.MultiDiGraph,
    config: dict[str, Any],
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]],
) -> dict[str, int]:
    connector_edges = 0
    explicit_edges = 0
    area_edges = 0

    for node in config.get("nodes", []):
        node_id = node["id"]
        nearest_id = nearest_network_node(G, node["x"], node["y"])
        elevation = node.get("elevation_m")
        if elevation is None and node.get("connect_to_nearest_edge"):
            u, v, _, _, _, _, _, ratio, _ = nearest_network_edge(G, node["x"], node["y"])
            elevation = interpolate_elevation(G, u, v, ratio)
        if elevation is None:
            elevation = G.nodes[nearest_id].get("elevation_m", 0.0)
        elevation = float(elevation)
        elevation += float(node.get("elevation_offset_m", 0.0) or 0.0)
        elevation += float(node.get("floor_offset", 0.0) or 0.0) * DEFAULT_FLOOR_HEIGHT_M
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
        if node.get("connect_to_nearest_edge"):
            connector_edges += add_snap_connection(G, node_id, node, split_registry)

    for edge in config.get("edges", []):
        explicit_edges += add_edge(
            G,
            edge["u"],
            edge["v"],
            edge.get("walk_type", "shortcut"),
            edge.get("feature_id", ""),
            bool(edge.get("bidirectional", True)),
            edge.get("geometry_lon_lat"),
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
    stair_time_model = load_stair_time_model()
    shuttle_time_model = load_shuttle_time_model()
    duplicate_stats = consolidate_duplicate_edges(graph)
    split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    manual_stats = add_manual_features(graph, load_manual_features(), split_registry)
    entrance_stats = add_osm_entrances(graph, load_entrance_features(), split_registry)
    split_stats = split_registered_edges(graph, split_registry)
    manual_base_overlap_stats = consolidate_manual_base_overlaps(graph)
    manual_manual_overlap_stats = consolidate_manual_overlaps(graph)
    post_split_duplicate_stats = consolidate_duplicate_edges(graph)
    shuttle_split_registry: dict[tuple[Any, Any, Any], list[dict[str, Any]]] = {}
    shuttle_stop_node_by_code, shuttle_stop_stats = add_shuttle_stop_nodes(
        graph,
        load_shuttle_stops(),
        shuttle_split_registry,
    )
    shuttle_split_stats = split_registered_edges(graph, shuttle_split_registry)
    auto_stitch_stats = repair_small_disconnected_components(graph)
    near_node_stitch_stats = add_nearby_node_stitches(graph)
    add_time_weights(graph, recompute=True)
    building_internal_stats = add_building_internal_edges(
        graph,
        load_building_entrance_matches(),
        stair_time_model,
    )
    shuttle_transit_stats = add_shuttle_transit_edges(
        graph,
        load_shuttle_route(),
        shuttle_stop_node_by_code,
        shuttle_time_model,
    )

    ox.save_graphml(graph, ROUTING_GRAPH_PATH)
    nodes, edges = ox.graph_to_gdfs(graph)
    nodes.reset_index().to_file(ROUTING_NODES_PATH, driver="GeoJSON")
    edges.reset_index().to_file(ROUTING_EDGES_PATH, driver="GeoJSON")

    stats = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        **duplicate_stats,
        **{f"post_split_{key}": value for key, value in post_split_duplicate_stats.items()},
        **split_stats,
        **manual_base_overlap_stats,
        **manual_manual_overlap_stats,
        **manual_stats,
        **entrance_stats,
        **{f"shuttle_{key}": value for key, value in shuttle_split_stats.items()},
        **auto_stitch_stats,
        **near_node_stitch_stats,
        **shuttle_stop_stats,
        **building_internal_stats,
        **shuttle_transit_stats,
        "weight": "time_sec",
        "elevation_for_manual_nodes": "nearest connected network node unless elevation_m is configured",
        "outdoor_walk_time_model": "Tobler hiking function with request-time base_walk_speed_kmh scaling",
    }
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
