from __future__ import annotations

import json
from pathlib import Path

import osmnx as ox


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

PLACE_QUERY = "Seoul National University Gwanak Campus"


def ensure_dirs() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


def save_features() -> dict:
    ensure_dirs()
    boundary = ox.geocode_to_gdf(PLACE_QUERY).geometry.iloc[0]

    entrances = ox.features_from_polygon(boundary, tags={"entrance": True})
    buildings = ox.features_from_polygon(boundary, tags={"building": True})

    entrances = entrances[entrances.geometry.apply(lambda geometry: boundary.covers(geometry))]
    buildings = buildings[buildings.geometry.apply(lambda geometry: boundary.covers(geometry))]

    entrances.reset_index().to_file(
        PROCESSED_DIR / "snu_osm_entrances.geojson",
        driver="GeoJSON",
    )
    buildings.reset_index().to_file(
        PROCESSED_DIR / "snu_osm_buildings.geojson",
        driver="GeoJSON",
    )

    stats = {
        "entrances": len(entrances),
        "buildings": len(buildings),
        "entrance_geometry_types": entrances.geometry.geom_type.value_counts().to_dict(),
        "building_geometry_types": buildings.geometry.geom_type.value_counts().to_dict(),
        "source_note": "OSMnx/Overpass로 가져온 OpenStreetMap 건물 및 entrance=* 객체",
    }
    (OUTPUTS_DIR / "snu_osm_buildings_entrances_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return stats


def main() -> None:
    print(json.dumps(save_features(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
