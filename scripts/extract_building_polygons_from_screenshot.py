from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import cv2
import folium
import numpy as np
from PIL import Image
from shapely.geometry import Polygon, mapping
from shapely.ops import transform
from pyproj import Transformer


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
INPUT_HTML = OUTPUTS_DIR / "snu_walk_full.html"
SCREENSHOT_PATH = OUTPUTS_DIR / "building_source_screenshot.png"
MASK_PATH = OUTPUTS_DIR / "building_color_mask.png"
GEOJSON_PATH = OUTPUTS_DIR / "snu_building_polygons_from_screenshot.geojson"
PREVIEW_HTML = OUTPUTS_DIR / "snu_building_polygons_preview.html"

VIEWPORT_WIDTH = 1800
VIEWPORT_HEIGHT = 1400
MIN_AREA_M2 = 20.0
MIN_SHORT_SIDE_M = 4.0
MAX_THIN_ASPECT_RATIO = 12.0
MAX_THIN_AREA_M2 = 900.0
SIMPLIFY_TOLERANCE_M = 1.2

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:32652", always_xy=True).transform
TO_WGS84 = Transformer.from_crs("EPSG:32652", "EPSG:4326", always_xy=True).transform


def building_mask(image_path: Path) -> np.ndarray:
    image = cv2.cvtColor(np.array(Image.open(image_path).convert("RGB")), cv2.COLOR_RGB2BGR)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    # OSM Carto renders buildings as a muted warm gray/beige. Keep this range
    # intentionally strict so neighboring buildings separated by thin map gaps do
    # not get merged into a single polygon.
    lower = np.array([195, 184, 174], dtype=np.uint8)
    upper = np.array([226, 218, 210], dtype=np.uint8)
    mask = cv2.inRange(rgb, lower, upper)

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    mask[(saturation > 38) | (value < 165) | (value > 235)] = 0

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    ok, encoded = cv2.imencode(".png", mask)
    if not ok:
        raise RuntimeError("Failed to encode building mask as PNG.")
    MASK_PATH.write_bytes(encoded.tobytes())
    return mask


def rectangle_sides_m(polygon: Polygon) -> tuple[float, float]:
    rectangle = polygon.minimum_rotated_rectangle
    coords = list(rectangle.exterior.coords)
    sides = [
        float(((coords[i][0] - coords[i + 1][0]) ** 2 + (coords[i][1] - coords[i + 1][1]) ** 2) ** 0.5)
        for i in range(4)
    ]
    unique_sides = sorted({round(side, 3) for side in sides})
    if len(unique_sides) == 1:
        return unique_sides[0], unique_sides[0]
    return unique_sides[0], unique_sides[-1]


async def capture_html() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    from playwright.async_api import async_playwright

    if not INPUT_HTML.exists():
        raise FileNotFoundError(f"Missing map HTML: {INPUT_HTML}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
        await page.goto(INPUT_HTML.resolve().as_uri(), wait_until="networkidle")
        await page.wait_for_timeout(1500)

        await page.evaluate(
            """
            () => {
              for (const selector of [
                '.leaflet-overlay-pane',
                '.leaflet-marker-pane',
                '.leaflet-shadow-pane',
                '.leaflet-tooltip-pane',
                '.leaflet-popup-pane',
                '.leaflet-control-container'
              ]) {
                document.querySelectorAll(selector).forEach((el) => { el.style.display = 'none'; });
              }
            }
            """
        )
        await page.screenshot(path=str(SCREENSHOT_PATH), full_page=False)

        map_info = await page.evaluate(
            """
            () => {
              const mapName = Object.keys(window).find((key) => key.startsWith('map_') && window[key]?.containerPointToLatLng);
              const map = window[mapName];
              const bounds = map.getBounds();
              const zoom = map.getZoom();
              return {
                mapName,
                zoom,
                bounds: {
                  west: bounds.getWest(),
                  south: bounds.getSouth(),
                  east: bounds.getEast(),
                  north: bounds.getNorth()
                }
              };
            }
            """
        )

        await browser.close()

    mask = building_mask(SCREENSHOT_PATH)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    raw_polygons: list[list[list[float]]] = []
    for contour in contours:
        if cv2.contourArea(contour) < 80:
            continue
        epsilon = max(1.5, 0.006 * cv2.arcLength(contour, True))
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) < 4:
            continue
        points = [[float(pt[0][0]), float(pt[0][1])] for pt in approx]
        raw_polygons.append(points)

    pixel_polygons = await page_pixels_to_lonlat(raw_polygons)
    return map_info, pixel_polygons


async def page_pixels_to_lonlat(pixel_polygons: list[list[list[float]]]) -> list[dict[str, Any]]:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT})
        await page.goto(INPUT_HTML.resolve().as_uri(), wait_until="networkidle")
        await page.wait_for_timeout(500)
        lonlat_polygons = await page.evaluate(
            """
            (pixelPolygons) => {
              const mapName = Object.keys(window).find((key) => key.startsWith('map_') && window[key]?.containerPointToLatLng);
              const map = window[mapName];
              return pixelPolygons.map((poly) => poly.map(([x, y]) => {
                const ll = map.containerPointToLatLng([x, y]);
                return [ll.lng, ll.lat];
              }));
            }
            """,
            pixel_polygons,
        )
        await browser.close()

    features: list[dict[str, Any]] = []
    for idx, coords in enumerate(lonlat_polygons, start=1):
        if coords[0] != coords[-1]:
            coords.append(coords[0])
        polygon = Polygon(coords)
        if not polygon.is_valid or polygon.is_empty:
            polygon = polygon.buffer(0)
        if polygon.is_empty:
            continue
        utm_polygon = transform(TO_UTM, polygon)
        short_side_m, long_side_m = rectangle_sides_m(utm_polygon)
        aspect_ratio = long_side_m / short_side_m if short_side_m else float("inf")
        if (
            utm_polygon.area < MIN_AREA_M2
            or short_side_m < MIN_SHORT_SIDE_M
            or (aspect_ratio > MAX_THIN_ASPECT_RATIO and utm_polygon.area < MAX_THIN_AREA_M2)
        ):
            continue
        polygon = transform(TO_WGS84, utm_polygon.simplify(SIMPLIFY_TOLERANCE_M, preserve_topology=True))
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "building_id": f"screenshot_building_{idx:04d}",
                    "area_m2": round(float(transform(TO_UTM, polygon).area), 1),
                    "short_side_m": round(short_side_m, 1),
                    "long_side_m": round(long_side_m, 1),
                    "aspect_ratio": round(aspect_ratio, 1),
                    "source": "osm_tile_screenshot_color_mask",
                },
                "geometry": mapping(polygon),
            }
        )
    return features


def save_geojson(features: list[dict[str, Any]]) -> None:
    collection = {"type": "FeatureCollection", "features": features}
    GEOJSON_PATH.write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")


def save_preview(features: list[dict[str, Any]], map_info: dict[str, Any]) -> None:
    bounds = map_info["bounds"]
    center = [(bounds["south"] + bounds["north"]) / 2, (bounds["west"] + bounds["east"]) / 2]
    preview = folium.Map(location=center, zoom_start=int(map_info["zoom"]), tiles="OpenStreetMap")
    folium.GeoJson(
        {"type": "FeatureCollection", "features": features},
        name="extracted building polygons",
        style_function=lambda feature: {
            "color": "#dc2626",
            "weight": 2,
            "fillColor": "#facc15",
            "fillOpacity": 0.35,
        },
        tooltip=folium.GeoJsonTooltip(fields=["building_id", "area_m2"]),
    ).add_to(preview)
    folium.LayerControl(collapsed=False).add_to(preview)
    preview.save(PREVIEW_HTML)


async def main() -> None:
    OUTPUTS_DIR.mkdir(exist_ok=True)
    map_info, features = await capture_html()
    save_geojson(features)
    save_preview(features, map_info)
    print(json.dumps(
        {
            "features": len(features),
            "screenshot": str(SCREENSHOT_PATH),
            "mask": str(MASK_PATH),
            "geojson": str(GEOJSON_PATH),
            "preview_html": str(PREVIEW_HTML),
            "map_zoom": map_info["zoom"],
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    asyncio.run(main())
