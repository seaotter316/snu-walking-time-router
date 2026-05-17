from __future__ import annotations

import json
from pathlib import Path

import osmnx as ox


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = PROJECT_ROOT / "data" / "raw"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

PLACE_QUERY = "Seoul National University Gwanak Campus"
BOUNDARY_PATH = PROCESSED_DIR / "snu_campus_boundary.geojson"
STATS_PATH = OUTPUTS_DIR / "snu_campus_boundary_stats.json"


def main() -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(RAW_DIR / "osmnx_cache")

    boundary = ox.geocode_to_gdf(PLACE_QUERY)
    boundary = boundary.to_crs("EPSG:4326")

    boundary_out = boundary.copy()
    boundary_out["boundary_source"] = "OSM geocode_to_gdf"
    boundary_out["place_query"] = PLACE_QUERY
    boundary_out.to_file(BOUNDARY_PATH, driver="GeoJSON")

    stats = {
        "place_query": PLACE_QUERY,
        "output": str(BOUNDARY_PATH.relative_to(PROJECT_ROOT)),
        "features": int(len(boundary_out)),
        "geometry_types": sorted(boundary_out.geometry.geom_type.unique().tolist()),
        "bounds_lon_lat": [round(float(value), 7) for value in boundary_out.total_bounds.tolist()],
        "source_note": "OSM geocode_to_gdf로 가져온 서울대 관악캠퍼스 place boundary",
    }
    STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
