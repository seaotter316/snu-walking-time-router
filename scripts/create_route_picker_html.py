from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
HTML_PATH = OUTPUTS_DIR / "pick_route_points.html"


HTML = """<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>서울대 출발/도착 지점 선택</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
  <style>
    html, body, #map {
      height: 100%;
      margin: 0;
    }
    #panel {
      position: absolute;
      top: 12px;
      right: 12px;
      z-index: 1000;
      width: 360px;
      max-width: calc(100vw - 24px);
      background: #ffffff;
      border: 1px solid #cbd5e1;
      border-radius: 8px;
      box-shadow: 0 8px 24px rgba(15, 23, 42, 0.18);
      padding: 12px;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 16px;
      line-height: 1.3;
    }
    p {
      margin: 0 0 10px;
      font-size: 13px;
      line-height: 1.45;
      color: #475569;
    }
    textarea {
      width: 100%;
      height: 138px;
      box-sizing: border-box;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 8px;
      resize: vertical;
      font: 12px/1.45 Consolas, "Courier New", monospace;
    }
    .buttons {
      display: flex;
      gap: 8px;
      margin-top: 8px;
      flex-wrap: wrap;
    }
    button {
      border: 1px solid #94a3b8;
      border-radius: 6px;
      background: #f8fafc;
      padding: 7px 9px;
      font-size: 13px;
      cursor: pointer;
    }
    button.primary {
      background: #14532d;
      border-color: #14532d;
      color: #ffffff;
    }
    #status {
      font-size: 13px;
      font-weight: 600;
      color: #166534;
      margin-bottom: 8px;
    }
  </style>
</head>
<body>
  <div id="map"></div>
  <section id="panel">
    <h1>출발/도착 지점 선택</h1>
    <p>서울대 지도를 자유롭게 이동/확대하고, 출발 지점과 도착 지점을 순서대로 클릭하세요.</p>
    <div id="status">0 / 2개 선택됨</div>
    <textarea id="output" readonly spellcheck="false"></textarea>
    <div class="buttons">
      <button class="primary" id="copy">명령 복사</button>
      <button id="undo">마지막 점 삭제</button>
      <button id="clear">전체 삭제</button>
    </div>
  </section>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
  <script>
    const map = L.map("map").setView([37.4598, 126.9520], 16);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 20,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    const points = [];
    const markers = [];
    const statusEl = document.getElementById("status");
    const outputEl = document.getElementById("output");

    function makeCommand() {
      if (points.length < 2) {
        return "출발 지점과 도착 지점을 순서대로 클릭하세요.";
      }
      const start = points[0];
      const end = points[1];
      return [
        "venv\\\\Scripts\\\\python.exe scripts\\\\find_shortest_time_path.py",
        `--start-lon ${start.lng.toFixed(7)}`,
        `--start-lat ${start.lat.toFixed(7)}`,
        `--end-lon ${end.lng.toFixed(7)}`,
        `--end-lat ${end.lat.toFixed(7)}`
      ].join(" ");
    }

    function refresh() {
      statusEl.textContent = `${points.length} / 2개 선택됨`;
      outputEl.value = makeCommand();
    }

    function markerStyle(index) {
      return {
        radius: 8,
        color: index === 0 ? "#16a34a" : "#dc2626",
        weight: 2,
        fill: true,
        fillColor: index === 0 ? "#22c55e" : "#ef4444",
        fillOpacity: 0.95
      };
    }

    function addPoint(latlng) {
      if (points.length >= 2) {
        return;
      }
      const index = points.length;
      points.push(latlng);
      const marker = L.circleMarker(latlng, markerStyle(index))
        .bindTooltip(index === 0 ? "출발" : "도착", { permanent: true, direction: "top" })
        .addTo(map);
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
      statusEl.textContent = "명령을 복사했습니다";
      setTimeout(refresh, 900);
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
