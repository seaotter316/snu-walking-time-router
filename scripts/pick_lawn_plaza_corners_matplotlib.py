from __future__ import annotations

import json
from pathlib import Path

import contextily as cx
import geopandas as gpd
import matplotlib.pyplot as plt
from pyproj import Transformer
from shapely.geometry import Polygon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
OUTPUT_PATH = OUTPUTS_DIR / "lawn_plaza_corners.json"

EDGES_PATH = PROCESSED_DIR / "snu_walk_edges.geojson"
BUILDINGS_PATH = PROCESSED_DIR / "snu_osm_buildings.geojson"
ENTRANCES_PATH = PROCESSED_DIR / "snu_osm_entrances.geojson"

# 캠퍼스 중앙부를 기본 화면으로 띄운다. 필요하면 툴바로 확대 이동할 수 있다.
DEFAULT_BOUNDS = {
    "min_lon": 126.9478,
    "max_lon": 126.9540,
    "min_lat": 37.4578,
    "max_lat": 37.4624,
}

TO_WEB_MERCATOR = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
TO_LON_LAT = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)


def read_layer(path: Path) -> gpd.GeoDataFrame | None:
    if not path.exists():
        return None
    return gpd.read_file(path)


def draw_context(ax) -> None:
    edges = read_layer(EDGES_PATH)
    buildings = read_layer(BUILDINGS_PATH)
    entrances = read_layer(ENTRANCES_PATH)

    if buildings is not None:
        buildings = buildings.to_crs("EPSG:3857")
        buildings.plot(ax=ax, color="#e5e7eb", edgecolor="#6b7280", linewidth=0.5, alpha=0.8)

    if edges is not None:
        edges = edges.to_crs("EPSG:3857")
        edges.plot(ax=ax, color="#2563eb", linewidth=0.8, alpha=0.55)

    if entrances is not None:
        entrances = entrances.to_crs("EPSG:3857")
        entrances.plot(ax=ax, color="#f97316", markersize=8, alpha=0.9)

    min_x, min_y = TO_WEB_MERCATOR.transform(DEFAULT_BOUNDS["min_lon"], DEFAULT_BOUNDS["min_lat"])
    max_x, max_y = TO_WEB_MERCATOR.transform(DEFAULT_BOUNDS["max_lon"], DEFAULT_BOUNDS["max_lat"])
    ax.set_xlim(min_x, max_x)
    ax.set_ylim(min_y, max_y)
    cx.add_basemap(ax, source=cx.providers.OpenStreetMap.Mapnik, zoom=17, alpha=0.95)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="#d1d5db", linewidth=0.5, alpha=0.7)
    ax.set_xlabel("Web Mercator X")
    ax.set_ylabel("Web Mercator Y")


def save_points(points: list[tuple[float, float]]) -> None:
    lon_lat_points = [TO_LON_LAT.transform(x, y) for x, y in points]
    closed = lon_lat_points + [lon_lat_points[0]] if len(lon_lat_points) == 4 else lon_lat_points
    payload = {
        "plaza_name": "서울대 잔디광장",
        "coordinate_order": "[longitude, latitude]",
        "polygon_lon_lat": [[round(lon, 7), round(lat, 7)] for lon, lat in closed],
    }
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    points: list[tuple[float, float]] = []

    fig, ax = plt.subplots(figsize=(10, 8))
    draw_context(ax)
    ax.set_title("서울대 잔디광장 꼭짓점 4개를 순서대로 클릭하세요")

    point_plot = ax.scatter([], [], s=80, color="#16a34a", edgecolor="#14532d", zorder=10)
    polygon_patch = None
    labels = []

    def redraw() -> None:
        nonlocal polygon_patch, labels

        point_plot.set_offsets(points)

        for label in labels:
            label.remove()
        labels = []

        for idx, (lon, lat) in enumerate(points, start=1):
            labels.append(
                ax.text(
                    lon,
                    lat,
                    str(idx),
                    color="#111827",
                    fontsize=10,
                    fontweight="bold",
                    ha="center",
                    va="bottom",
                    zorder=6,
                )
            )

        if polygon_patch is not None:
            polygon_patch.remove()
            polygon_patch = None

        if len(points) >= 3:
            polygon = Polygon(points)
            x, y = polygon.exterior.xy
            polygon_patch = ax.fill(x, y, color="#86efac", alpha=0.35, zorder=9)[0]

        ax.set_title(f"서울대 잔디광장 꼭짓점 선택: {len(points)} / 4")
        fig.canvas.draw_idle()

    def onclick(event) -> None:
        if event.inaxes != ax or event.xdata is None or event.ydata is None:
            return
        if len(points) >= 4:
            return

        points.append((float(event.xdata), float(event.ydata)))
        save_points(points)
        redraw()

        if len(points) == 4:
            print(f"좌표 저장 완료: {OUTPUT_PATH}")
            print(OUTPUT_PATH.read_text(encoding="utf-8"))

    def onkey(event) -> None:
        if event.key in {"backspace", "delete"} and points:
            points.pop()
            save_points(points)
            redraw()
        elif event.key == "escape":
            points.clear()
            save_points(points)
            redraw()

    fig.canvas.mpl_connect("button_press_event", onclick)
    fig.canvas.mpl_connect("key_press_event", onkey)

    print("지도 창에서 잔디광장 꼭짓점 4개를 순서대로 클릭하세요.")
    print("Backspace/Delete: 마지막 점 삭제, Esc: 전체 삭제")
    print(f"좌표 저장 위치: {OUTPUT_PATH}")

    plt.show()


if __name__ == "__main__":
    main()
