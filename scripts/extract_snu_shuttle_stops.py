from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

import folium
import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

STATION_LIST_URL = "https://shuttlebus.snu.ac.kr/mobile/station/stationList.action"
STATION_MAP_URL = "https://shuttlebus.snu.ac.kr/mobile/station/stationMap.action?searchText="
USER_AGENT = "Mozilla/5.0"

SHUTTLE_STOPS_PATH = PROCESSED_DIR / "snu_shuttle_stops.geojson"
SHUTTLE_ROUTE_PATH = PROCESSED_DIR / "snu_shuttle_circular_route.json"
PREVIEW_HTML_PATH = OUTPUTS_DIR / "snu_shuttle_stops_preview.html"

# The circular shuttle described by the user follows the B-side city-bus shared
# stops where the official SNU service exposes both A/B directions.
ROUTE_STOPS = [
    ("정문", "100"),
    ("법과대", "200"),
    ("자연대", "400"),
    ("농생대", "500"),
    ("38동", "2129200"),
    ("신소재공동연구소", "701"),
    ("302동", "900"),
    ("301동", "1000"),
    ("유전공학연구소", "1101"),
    ("교수회관", "1301"),
    ("기숙사삼거리", "1500"),
    ("국제대학원", "1600"),
    ("수의대", "1700"),
    ("경영대", "1800"),
    ("정문", "100"),
]

# The official SNU map currently reports 38동 (21292B) as 0,0. It shares the
# Seoul city bus stop ARS 21292, exposed in OSM as 공대입구 around this point.
COORDINATE_FALLBACKS = {
    "2129200": {
        "lat": 37.4549653,
        "lon": 126.9497691,
        "coordinate_source": "osm_bus_stop_공대입구_21292_fallback",
        "note": "SNU official stationMap returns 0,0 for 38동 (21292B); using nearby OSM 공대입구/ARS 21292 coordinate.",
    }
}


def fetch_text(url: str) -> str:
    response = requests.get(url, timeout=30, headers={"User-Agent": USER_AGENT}, verify=False)
    response.raise_for_status()
    return response.text


def parse_station_types() -> dict[str, dict[str, Any]]:
    text = fetch_text(STATION_LIST_URL)
    result: dict[str, dict[str, Any]] = {}
    item_pattern = re.compile(r'<li>\s*<a href="(?P<href>[^"]+)"(?P<body>.*?)</a>\s*</li>', re.S)
    for match in item_pattern.finditer(text):
        body = match.group("body")
        code_match = re.search(r"bus_station_code=([0-9]+)", match.group("href"))
        title_match = re.search(r'<div class="title">(.*?)</div>', body, re.S)
        if not code_match or not title_match:
            continue
        icons = re.findall(r'<strong class="bicon [^"]+">(.*?)</strong>', body, re.S)
        code = code_match.group(1)
        result[code] = {
            "official_title": clean_html(title_match.group(1)),
            "transport_types": [clean_html(icon) for icon in icons],
        }
    return result


def parse_station_coordinates() -> dict[str, dict[str, Any]]:
    text = fetch_text(STATION_MAP_URL)
    station_pattern = re.compile(
        r"\['(?P<name>[^']*)',\s*(?P<lat>-?[0-9.]+),\s*(?P<lon>-?[0-9.]+),\s*"
        r"(?P<order>[0-9]+),\s*\"(?P<icon>[^\"]*)\",\s*\"(?P<code>[^\"]*)\",\s*\"(?P<short>[^\"]*)\"\]"
    )
    result: dict[str, dict[str, Any]] = {}
    for match in station_pattern.finditer(text):
        code = match.group("code")
        result[code] = {
            "official_name": match.group("name"),
            "lat": float(match.group("lat")),
            "lon": float(match.group("lon")),
            "map_order": int(match.group("order")),
            "station_short_code": match.group("short"),
            "coordinate_source": "snu_shuttle_stationMap",
        }
    return result


def clean_html(value: str) -> str:
    return html.unescape(re.sub(r"<.*?>", "", value).strip())


def build_route_features() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    station_types = parse_station_types()
    station_coordinates = parse_station_coordinates()
    features: list[dict[str, Any]] = []
    route_entries: list[dict[str, Any]] = []

    for sequence, (route_name, code) in enumerate(ROUTE_STOPS, start=1):
        station = {**station_types.get(code, {}), **station_coordinates.get(code, {})}
        fallback = COORDINATE_FALLBACKS.get(code)
        if fallback and (not station.get("lat") or not station.get("lon")):
            station.update(fallback)

        lat = station.get("lat")
        lon = station.get("lon")
        has_coordinate = lat is not None and lon is not None and float(lat) != 0.0 and float(lon) != 0.0
        entry = {
            "sequence": sequence,
            "route_stop_name": route_name,
            "snu_station_code": code,
            "official_name": station.get("official_name"),
            "official_title": station.get("official_title"),
            "station_short_code": station.get("station_short_code"),
            "transport_types": station.get("transport_types", []),
            "coordinate_source": station.get("coordinate_source"),
            "has_coordinate": has_coordinate,
            "lat": lat,
            "lon": lon,
            "note": station.get("note"),
        }
        route_entries.append(entry)

        if not has_coordinate:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    key: value
                    for key, value in entry.items()
                    if key not in {"lat", "lon"}
                }
                | {
                    "node_id": f"shuttle_stop_{sequence:02d}_{code}",
                    "source": "snu_shuttle",
                    "feature_id": "snu_circular_shuttle",
                },
                "geometry": {"type": "Point", "coordinates": [float(lon), float(lat)]},
            }
        )

    return features, route_entries


def save_preview(features: list[dict[str, Any]]) -> None:
    if features:
        center = [
            sum(feature["geometry"]["coordinates"][1] for feature in features) / len(features),
            sum(feature["geometry"]["coordinates"][0] for feature in features) / len(features),
        ]
    else:
        center = [37.459, 126.952]

    preview = folium.Map(location=center, zoom_start=15, tiles="OpenStreetMap")
    group = folium.FeatureGroup(name="SNU circular shuttle stops", show=True)
    coordinates = []
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"]
        props = feature["properties"]
        coordinates.append([lat, lon])
        folium.CircleMarker(
            location=[lat, lon],
            radius=5,
            color="#7c3aed",
            weight=2,
            fill=True,
            fill_color="#a855f7",
            fill_opacity=0.9,
            tooltip=f'{props["sequence"]}. {props["route_stop_name"]} / {props.get("station_short_code")}',
            popup=(
                f'{props["sequence"]}. {props["route_stop_name"]}<br>'
                f'official={props.get("official_title") or props.get("official_name")}<br>'
                f'code={props["snu_station_code"]}<br>'
                f'source={props.get("coordinate_source")}'
            ),
        ).add_to(group)
    if len(coordinates) > 1:
        folium.PolyLine(coordinates, color="#7c3aed", weight=3, opacity=0.65).add_to(group)
    group.add_to(preview)
    folium.LayerControl(collapsed=False).add_to(preview)
    preview.save(PREVIEW_HTML_PATH)


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    features, route_entries = build_route_features()
    SHUTTLE_STOPS_PATH.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    SHUTTLE_ROUTE_PATH.write_text(json.dumps(route_entries, ensure_ascii=False, indent=2), encoding="utf-8")
    save_preview(features)
    print(
        json.dumps(
            {
                "route_entries": len(route_entries),
                "features_with_coordinates": len(features),
                "missing_coordinates": [entry for entry in route_entries if not entry["has_coordinate"]],
                "geojson": str(SHUTTLE_STOPS_PATH),
                "route_json": str(SHUTTLE_ROUTE_PATH),
                "preview_html": str(PREVIEW_HTML_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
