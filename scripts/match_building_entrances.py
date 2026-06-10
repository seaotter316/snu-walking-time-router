from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import folium
from pyproj import Transformer
from shapely.geometry import Point, mapping, shape
from shapely.ops import transform


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PROCESSED_DIR = DATA_DIR / "processed"

BUILDING_POLYGONS_PATH = OUTPUTS_DIR / "snu_building_polygons_from_screenshot.geojson"
ROUTING_NODES_PATH = DATA_DIR / "processed" / "snu_routing_nodes.geojson"
MANUAL_CONFIG_PATH = DATA_DIR / "manual" / "walk_network_additions.json"

FILTERED_BUILDINGS_PATH = OUTPUTS_DIR / "snu_buildings_with_entrances.geojson"
MATCHES_JSON_PATH = OUTPUTS_DIR / "snu_building_entrance_matches.json"
MATCHES_CSV_PATH = OUTPUTS_DIR / "snu_building_entrance_matches.csv"
PREVIEW_HTML_PATH = OUTPUTS_DIR / "snu_building_entrance_matches_preview.html"
ENTRANCES_WITH_FLOORS_PATH = OUTPUTS_DIR / "snu_building_entrances_with_floors.geojson"
PROCESSED_MATCHES_JSON_PATH = PROCESSED_DIR / "snu_building_entrance_matches.json"
PROCESSED_ENTRANCES_WITH_FLOORS_PATH = PROCESSED_DIR / "snu_building_entrances_with_floors.geojson"

MATCH_BUFFER_M = 4.0
DEFAULT_FLOOR_HEIGHT_M = 3.0

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True).transform
TO_WGS84 = Transformer.from_crs("EPSG:32652", "EPSG:4326", always_xy=True).transform


def read_geojson(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def manual_node_ids() -> set[str]:
    config = read_geojson(MANUAL_CONFIG_PATH)
    return {str(node["id"]) for node in config.get("nodes", [])}


def entrance_candidates() -> list[dict[str, Any]]:
    manual_ids = manual_node_ids()
    nodes = read_geojson(ROUTING_NODES_PATH)
    candidates: list[dict[str, Any]] = []

    for feature in nodes.get("features", []):
        props = feature.get("properties", {})
        node_id = str(props.get("osmid"))
        source = props.get("source")
        is_osm_entrance = source == "osm_entrance"
        is_manual_node = source == "manual" and node_id in manual_ids
        if not (is_osm_entrance or is_manual_node):
            continue

        point = shape(feature["geometry"])
        if not isinstance(point, Point):
            continue

        candidates.append(
            {
                "node_id": node_id,
                "candidate_kind": "osm_entrance" if is_osm_entrance else "manual_node",
                "source": source,
                "name": props.get("name"),
                "feature_id": props.get("feature_id"),
                "entrance": props.get("entrance"),
                "wheelchair": props.get("wheelchair"),
                "elevation_m": props.get("elevation_m"),
                "lon": point.x,
                "lat": point.y,
                "geometry": point,
                "geometry_utm": transform(TO_UTM, point),
            }
        )

    return candidates


def sorted_buildings(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(feature: dict[str, Any]) -> tuple[float, float]:
        centroid = shape(feature["geometry"]).centroid
        return (-centroid.y, centroid.x)

    return sorted(features, key=sort_key)


def add_predicted_floors(entrances: list[dict[str, Any]]) -> tuple[float | None, int | None, int | None]:
    elevations = [float(entrance["elevation_m"]) for entrance in entrances if entrance["elevation_m"] is not None]
    if not elevations:
        for entrance in entrances:
            entrance.update(
                {
                    "building_base_elevation_m": None,
                    "elevation_above_base_m": None,
                    "predicted_floor_offset": None,
                    "predicted_floor": None,
                    "floor_prediction_residual_m": None,
                }
            )
        return None, None, None

    base_elevation = min(elevations)
    floors: list[int] = []
    for entrance in entrances:
        elevation = entrance["elevation_m"]
        if elevation is None:
            entrance.update(
                {
                    "building_base_elevation_m": round(base_elevation, 2),
                    "elevation_above_base_m": None,
                    "predicted_floor_offset": None,
                    "predicted_floor": None,
                    "floor_prediction_residual_m": None,
                }
            )
            continue
        elevation_above_base = float(elevation) - base_elevation
        floor_offset = int(round(elevation_above_base / DEFAULT_FLOOR_HEIGHT_M))
        predicted_floor = floor_offset + 1
        floors.append(predicted_floor)
        entrance.update(
            {
                "building_base_elevation_m": round(base_elevation, 2),
                "elevation_above_base_m": round(elevation_above_base, 2),
                "predicted_floor_offset": floor_offset,
                "predicted_floor": predicted_floor,
                "floor_prediction_residual_m": round(elevation_above_base - floor_offset * DEFAULT_FLOOR_HEIGHT_M, 2),
            }
        )

    return round(base_elevation, 2), min(floors) if floors else None, max(floors) if floors else None


def match_buildings() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    building_features = sorted_buildings(read_geojson(BUILDING_POLYGONS_PATH).get("features", []))
    candidates = entrance_candidates()

    candidate_matches: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw_index, feature in enumerate(building_features, start=1):
        polygon = shape(feature["geometry"])
        polygon_utm = transform(TO_UTM, polygon)
        search_area = polygon_utm.buffer(MATCH_BUFFER_M)

        for candidate in candidates:
            point_utm = candidate["geometry_utm"]
            if not search_area.covers(point_utm):
                continue
            candidate_matches[candidate["node_id"]].append(
                {
                    "raw_index": raw_index,
                    "feature": feature,
                    "distance_m": round(float(polygon_utm.distance(point_utm)), 2),
                    "inside_or_on_polygon": bool(polygon_utm.covers(point_utm)),
                    "candidate": candidate,
                }
            )

    chosen_by_building: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for matches in candidate_matches.values():
        # If a node falls into touching/overlapping polygons, keep the closest one.
        best = sorted(
            matches,
            key=lambda match: (
                match["distance_m"],
                0 if match["inside_or_on_polygon"] else 1,
                shape(match["feature"]["geometry"]).area,
            ),
        )[0]
        chosen_by_building[best["raw_index"]].append(best)

    filtered_features: list[dict[str, Any]] = []
    building_matches: list[dict[str, Any]] = []
    kept_raw_indexes = sorted(chosen_by_building)
    building_index_by_raw = {raw_index: f"snu_building_{idx:03d}" for idx, raw_index in enumerate(kept_raw_indexes, start=1)}

    for raw_index in kept_raw_indexes:
        feature = building_features[raw_index - 1]
        matches = sorted(
            chosen_by_building[raw_index],
            key=lambda match: (match["candidate"]["candidate_kind"], match["candidate"]["node_id"]),
        )
        building_id = building_index_by_raw[raw_index]
        manual_count = sum(1 for match in matches if match["candidate"]["candidate_kind"] == "manual_node")
        osm_count = sum(1 for match in matches if match["candidate"]["candidate_kind"] == "osm_entrance")
        entrances = [
            {
                key: match["candidate"][key]
                for key in [
                    "node_id",
                    "candidate_kind",
                    "source",
                    "name",
                    "feature_id",
                    "entrance",
                    "wheelchair",
                    "elevation_m",
                    "lon",
                    "lat",
                ]
            }
            | {
                "distance_to_polygon_m": match["distance_m"],
                "inside_or_on_polygon": match["inside_or_on_polygon"],
            }
            for match in matches
        ]
        base_elevation_m, floor_min, floor_max = add_predicted_floors(entrances)
        elevations = [float(entrance["elevation_m"]) for entrance in entrances if entrance["elevation_m"] is not None]

        props = dict(feature.get("properties", {}))
        props.update(
            {
                "building_id": building_id,
                "raw_building_id": props.get("building_id"),
                "entrance_count": len(matches),
                "manual_node_count": manual_count,
                "osm_entrance_count": osm_count,
                "elevation_min_m": round(min(elevations), 2) if elevations else None,
                "elevation_max_m": round(max(elevations), 2) if elevations else None,
                "floor_height_assumption_m": DEFAULT_FLOOR_HEIGHT_M,
                "base_elevation_m": base_elevation_m,
                "predicted_floor_min": floor_min,
                "predicted_floor_max": floor_max,
                "entrance_node_ids": [match["candidate"]["node_id"] for match in matches],
            }
        )
        filtered_features.append({"type": "Feature", "properties": props, "geometry": feature["geometry"]})

        building_matches.append(
            {
                "building_id": building_id,
                "raw_building_id": props.get("raw_building_id"),
                "entrance_count": len(matches),
                "manual_node_count": manual_count,
                "osm_entrance_count": osm_count,
                "elevation_min_m": props["elevation_min_m"],
                "elevation_max_m": props["elevation_max_m"],
                "floor_height_assumption_m": DEFAULT_FLOOR_HEIGHT_M,
                "base_elevation_m": base_elevation_m,
                "predicted_floor_min": floor_min,
                "predicted_floor_max": floor_max,
                "entrances": entrances,
            }
        )

    return filtered_features, building_matches


def write_outputs(features: list[dict[str, Any]], matches: list[dict[str, Any]]) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    FILTERED_BUILDINGS_PATH.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    matches_json = json.dumps(matches, ensure_ascii=False, indent=2)
    MATCHES_JSON_PATH.write_text(matches_json, encoding="utf-8")
    PROCESSED_MATCHES_JSON_PATH.write_text(matches_json, encoding="utf-8")

    with MATCHES_CSV_PATH.open("w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=[
                "building_id",
                "node_id",
                "candidate_kind",
                "source",
                "name",
                "feature_id",
                "entrance",
                "wheelchair",
                "elevation_m",
                "building_base_elevation_m",
                "elevation_above_base_m",
                "predicted_floor_offset",
                "predicted_floor",
                "floor_prediction_residual_m",
                "lon",
                "lat",
                "distance_to_polygon_m",
                "inside_or_on_polygon",
            ],
        )
        writer.writeheader()
        for building in matches:
            for entrance in building["entrances"]:
                writer.writerow({"building_id": building["building_id"], **entrance})

    entrance_features = []
    for building in matches:
        for entrance in building["entrances"]:
            props = {
                "building_id": building["building_id"],
                **{key: value for key, value in entrance.items() if key not in {"lon", "lat"}},
            }
            entrance_features.append(
                {
                    "type": "Feature",
                    "properties": props,
                    "geometry": {"type": "Point", "coordinates": [entrance["lon"], entrance["lat"]]},
                }
            )
    entrances_geojson = json.dumps({"type": "FeatureCollection", "features": entrance_features}, ensure_ascii=False, indent=2)
    ENTRANCES_WITH_FLOORS_PATH.write_text(entrances_geojson, encoding="utf-8")
    PROCESSED_ENTRANCES_WITH_FLOORS_PATH.write_text(entrances_geojson, encoding="utf-8")


def save_preview(features: list[dict[str, Any]], matches: list[dict[str, Any]]) -> None:
    if features:
        centroids = [shape(feature["geometry"]).centroid for feature in features]
        center = [sum(point.y for point in centroids) / len(centroids), sum(point.x for point in centroids) / len(centroids)]
    else:
        center = [37.459, 126.952]

    preview = folium.Map(location=center, zoom_start=16, tiles="OpenStreetMap")
    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        name="buildings with matched entrances",
        style_function=lambda feature: {
            "color": "#b91c1c",
            "weight": 2,
            "fillColor": "#fde047",
            "fillOpacity": 0.35,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "building_id",
                "entrance_count",
                "manual_node_count",
                "osm_entrance_count",
                "elevation_min_m",
                "elevation_max_m",
                "predicted_floor_min",
                "predicted_floor_max",
            ],
            aliases=["building", "entrances", "manual", "osm", "min elev", "max elev", "min floor", "max floor"],
        ),
    ).add_to(preview)

    entrance_group = folium.FeatureGroup(name="matched entrance nodes", show=True)
    for building in matches:
        for entrance in building["entrances"]:
            is_manual = entrance["candidate_kind"] == "manual_node"
            color = "#7c3aed" if is_manual else "#0284c7"
            folium.CircleMarker(
                location=[entrance["lat"], entrance["lon"]],
                radius=4 if is_manual else 3,
                color=color,
                weight=2,
                fill=True,
                fill_color=color,
                fill_opacity=0.9,
                tooltip=f'{building["building_id"]} / {entrance["candidate_kind"]} / {entrance["node_id"]}',
                popup=(
                    f'{building["building_id"]}<br>'
                    f'node={entrance["node_id"]}<br>'
                    f'kind={entrance["candidate_kind"]}<br>'
                    f'elevation_m={entrance["elevation_m"]}<br>'
                    f'predicted_floor={entrance["predicted_floor"]}<br>'
                    f'elevation_above_base_m={entrance["elevation_above_base_m"]}<br>'
                    f'distance_m={entrance["distance_to_polygon_m"]}'
                ),
            ).add_to(entrance_group)
    entrance_group.add_to(preview)
    folium.LayerControl(collapsed=False).add_to(preview)
    preview.save(PREVIEW_HTML_PATH)


def main() -> None:
    features, matches = match_buildings()
    write_outputs(features, matches)
    save_preview(features, matches)
    print(
        json.dumps(
            {
                "buildings_with_entrances": len(features),
                "matched_entrances": sum(building["entrance_count"] for building in matches),
                "manual_nodes": sum(building["manual_node_count"] for building in matches),
                "osm_entrances": sum(building["osm_entrance_count"] for building in matches),
                "filtered_buildings_geojson": str(FILTERED_BUILDINGS_PATH),
                "matches_json": str(MATCHES_JSON_PATH),
                "processed_matches_json": str(PROCESSED_MATCHES_JSON_PATH),
                "matches_csv": str(MATCHES_CSV_PATH),
                "entrances_with_floors_geojson": str(ENTRANCES_WITH_FLOORS_PATH),
                "processed_entrances_with_floors_geojson": str(PROCESSED_ENTRANCES_WITH_FLOORS_PATH),
                "preview_html": str(PREVIEW_HTML_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
