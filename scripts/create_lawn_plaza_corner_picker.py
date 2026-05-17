from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
HTML_PATH = OUTPUTS_DIR / "pick_lawn_plaza_corners.html"


HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>서울대 잔디광장 꼭짓점 선택</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIINfQigU8QVZ4CwLq8T7MkT3odkOWM3HfA="
    crossorigin="" />
  <style>
    html, body {
      height: 100%;
      margin: 0;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      color: #111827;
    }
    #app {
      display: grid;
      grid-template-columns: minmax(0, 1fr) 360px;
      height: 100%;
    }
    #map {
      height: 100%;
      width: 100%;
    }
    #panel {
      border-left: 1px solid #d1d5db;
      padding: 14px;
      background: #ffffff;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    h1 {
      font-size: 18px;
      line-height: 1.3;
      margin: 0;
    }
    p {
      font-size: 13px;
      line-height: 1.45;
      margin: 0;
      color: #4b5563;
    }
    .status {
      font-size: 14px;
      font-weight: 600;
      color: #166534;
    }
    textarea {
      width: 100%;
      min-height: 260px;
      resize: vertical;
      box-sizing: border-box;
      font: 12px/1.45 Consolas, "Courier New", monospace;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 10px;
    }
    .buttons {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    button {
      border: 1px solid #9ca3af;
      background: #f9fafb;
      color: #111827;
      border-radius: 6px;
      padding: 8px 10px;
      cursor: pointer;
      font-size: 13px;
    }
    button.primary {
      background: #14532d;
      border-color: #14532d;
      color: #ffffff;
    }
    .hint {
      padding: 10px;
      border-radius: 6px;
      background: #f3f4f6;
      font-size: 12px;
      line-height: 1.45;
      color: #374151;
    }
    @media (max-width: 860px) {
      #app {
        grid-template-columns: 1fr;
        grid-template-rows: minmax(420px, 1fr) auto;
      }
      #panel {
        border-left: 0;
        border-top: 1px solid #d1d5db;
      }
    }
  </style>
</head>
<body>
  <div id="app">
    <div id="map"></div>
    <aside id="panel">
      <h1>서울대 잔디광장 꼭짓점 선택</h1>
      <p>지도에서 잔디광장 사각형의 꼭짓점 4개를 순서대로 클릭하세요. 점은 드래그해서 미세 조정할 수 있습니다.</p>
      <div id="status" class="status">0 / 4개 선택됨</div>
      <div class="buttons">
        <button class="primary" id="copy">좌표 복사</button>
        <button id="undo">마지막 점 삭제</button>
        <button id="clear">전체 삭제</button>
      </div>
      <textarea id="output" spellcheck="false" readonly></textarea>
      <div class="hint">
        복사한 좌표를 그대로 채팅에 붙여 주세요. 그러면 이 좌표로
        <code>add_lawn_plaza_crossings.py</code>의 polygon을 업데이트할 수 있습니다.
      </div>
    </aside>
  </div>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""></script>
  <script>
    const map = L.map("map").setView([37.46025, 126.95025], 18);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    const points = [];
    const markers = [];
    let polygon = null;

    const statusEl = document.getElementById("status");
    const outputEl = document.getElementById("output");

    function toLonLat(point) {
      return [Number(point.lng.toFixed(7)), Number(point.lat.toFixed(7))];
    }

    function refresh() {
      statusEl.textContent = `${points.length} / 4개 선택됨`;

      const coords = points.map(toLonLat);
      const closed = coords.length === 4 ? [...coords, coords[0]] : coords;
      const payload = {
        plaza_name: "서울대 잔디광장",
        coordinate_order: "[longitude, latitude]",
        polygon_lon_lat: closed
      };
      outputEl.value = JSON.stringify(payload, null, 2);

      if (polygon) {
        map.removeLayer(polygon);
      }
      if (points.length >= 2) {
        polygon = L.polygon(points.map(p => [p.lat, p.lng]), {
          color: "#16a34a",
          weight: 2,
          fillColor: "#86efac",
          fillOpacity: 0.25
        }).addTo(map);
      }
    }

    function addPoint(latlng) {
      if (points.length >= 4) {
        return;
      }

      points.push(latlng);
      const number = points.length;
      const marker = L.marker(latlng, { draggable: true })
        .bindTooltip(`${number}`, {
          permanent: true,
          direction: "top",
          offset: [0, -8]
        })
        .addTo(map);

      marker.on("drag", () => {
        points[number - 1] = marker.getLatLng();
        refresh();
      });

      markers.push(marker);
      refresh();
    }

    map.on("click", event => addPoint(event.latlng));

    document.getElementById("undo").addEventListener("click", () => {
      const marker = markers.pop();
      if (marker) {
        map.removeLayer(marker);
      }
      points.pop();
      refresh();
    });

    document.getElementById("clear").addEventListener("click", () => {
      while (markers.length) {
        map.removeLayer(markers.pop());
      }
      points.length = 0;
      refresh();
    });

    document.getElementById("copy").addEventListener("click", async () => {
      outputEl.select();
      await navigator.clipboard.writeText(outputEl.value);
      statusEl.textContent = "좌표를 복사했습니다";
      setTimeout(refresh, 1000);
    });

    refresh();
  </script>
</body>
</html>
"""


def main() -> None:
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    HTML_PATH.write_text(HTML, encoding="utf-8")
    print(HTML_PATH)


if __name__ == "__main__":
    main()
