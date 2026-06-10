from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import folium
import geopandas as gpd
import osmnx as ox


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

BOUNDARY_PATH = PROCESSED_DIR / "snu_campus_boundary.geojson"
BUILDINGS_PATH = PROCESSED_DIR / "snu_osm_buildings.geojson"
PREVIEW_HTML_PATH = OUTPUTS_DIR / "snu_osm_buildings_preview.html"
STATS_PATH = OUTPUTS_DIR / "snu_osm_buildings_stats.json"

BOUNDARY_BUFFER_M = 80.0


def load_query_polygon() -> Any:
    boundary = gpd.read_file(BOUNDARY_PATH).geometry.iloc[0]
    return (
        gpd.GeoSeries([boundary], crs="EPSG:4326")
        .to_crs("EPSG:32652")
        .buffer(BOUNDARY_BUFFER_M)
        .to_crs("EPSG:4326")
        .iloc[0]
    )


def stringify(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def extract_buildings() -> gpd.GeoDataFrame:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(PROJECT_ROOT / "cache" / "osmnx_cache")

    buildings = ox.features_from_polygon(load_query_polygon(), tags={"building": True})
    buildings = buildings[buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].copy()
    buildings = buildings.to_crs("EPSG:32652")
    buildings["area_m2"] = buildings.geometry.area.round(1)
    buildings = buildings[buildings["area_m2"] >= 15.0].copy()
    buildings = buildings.to_crs("EPSG:4326")

    buildings = buildings.reset_index()
    keep_columns = [
        column
        for column in [
            "element",
            "id",
            "building",
            "name",
            "name:ko",
            "name:en",
            "addr:housenumber",
            "operator",
            "amenity",
            "office",
            "shop",
            "area_m2",
            "geometry",
        ]
        if column in buildings.columns
    ]
    buildings = buildings[keep_columns]
    buildings["building_id"] = [f"osm_building_{index:04d}" for index in range(1, len(buildings) + 1)]
    for column in buildings.columns:
        if column != "geometry":
            buildings[column] = buildings[column].map(stringify)
    return buildings


def save_preview(buildings: gpd.GeoDataFrame) -> None:
    center = [float(buildings.geometry.centroid.y.mean()), float(buildings.geometry.centroid.x.mean())]
    preview = folium.Map(location=center, zoom_start=16, tiles="OpenStreetMap")
    folium.GeoJson(
        buildings,
        name="OSM building footprints",
        style_function=lambda feature: {
            "color": "#2563eb",
            "weight": 2,
            "fillColor": "#93c5fd",
            "fillOpacity": 0.35,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=["building_id", "name", "name:ko", "area_m2"],
            aliases=["id", "name", "ko", "area"],
        ),
    ).add_to(preview)
    folium.LayerControl(collapsed=False).add_to(preview)
    preview.save(PREVIEW_HTML_PATH)


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    buildings = extract_buildings()
    buildings.to_file(BUILDINGS_PATH, driver="GeoJSON")
    save_preview(buildings)
    stats = {
        "buildings": int(len(buildings)),
        "source": "OpenStreetMap building=* via OSMnx",
        "boundary_buffer_m": BOUNDARY_BUFFER_M,
        "geojson": str(BUILDINGS_PATH),
        "preview_html": str(PREVIEW_HTML_PATH),
    }
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
