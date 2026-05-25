from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import osmnx as ox


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

BOUNDARY_PATH = PROCESSED_DIR / "snu_campus_boundary.geojson"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"
STATS_PATH = OUTPUTS_DIR / "snu_osm_entrances_stats.json"


def main() -> None:
    if not BOUNDARY_PATH.exists():
        raise FileNotFoundError(
            "캠퍼스 경계가 없습니다. 먼저 `python -m scripts.export_osm_campus_boundary`를 실행하세요."
        )

    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW_DIR / "osmnx_cache")

    boundary = gpd.read_file(BOUNDARY_PATH).geometry.iloc[0]
    entrances = ox.features_from_polygon(boundary, tags={"entrance": True})
    entrances = entrances[entrances.geometry.apply(lambda geometry: boundary.covers(geometry))]
    entrances.reset_index().to_file(ENTRANCES_PATH, driver="GeoJSON")

    stats = {
        "entrances": len(entrances),
        "entrance_geometry_types": entrances.geometry.geom_type.value_counts().to_dict(),
        "source_note": "OSMnx/Overpass로 가져온 OpenStreetMap entrance=* 객체",
    }
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
