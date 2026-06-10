# SNU Walking Time Router

서울대학교 관악캠퍼스 안에서 출발지와 도착지를 클릭하면 예상 최단시간 경로를 계산하는 FastAPI + Leaflet 기반 라우터입니다.

기본 보행망은 OpenStreetMap 보행 그래프와 수동 보정 노드/엣지로 구성됩니다. 여기에 건물 내부 계단 이동, 경사 기반 야외 보행 시간, 순환 셔틀버스 이동 시간을 모두 `time_sec` 가중치로 통합해 dijkstra 최단시간 경로를 계산합니다.

## 주요 기능

- 지도에서 출발지/도착지 선택 후 최단시간 경로 계산
- 야외 보행, 건물 내부 이동, 셔틀버스 이동을 하나의 시간 비용으로 비교
- Tobler hiking function 기반 경사 보정 보행 시간 계산
- 사용자의 실제 보행 소요시간 입력을 통한 개인 기본 보행속도 보정
- 건물 입구 간 내부 이동 시간 계산
- 셔틀 정류장, 셔틀 주행 구간, 기대 대기시간 반영
- 캠퍼스 경계, OSM 보행 엣지, 건물 입구, 고도 노드, 수동 보정 구간, 셔틀 레이어 표시

## 현재 시간 모델

### 야외 보행

야외 보행 엣지는 Tobler hiking function을 사용합니다.

```text
slope = (도착 노드 고도 - 출발 노드 고도) / 엣지 길이
speed_kmh = base_walk_speed_kmh * exp(-3.5 * abs(slope + 0.05)) * walk_type_factor
time_sec = length_m / (speed_kmh * 1000 / 3600)
```

기본 `base_walk_speed_kmh`는 6.0km/h입니다. UI에서 사용자가 실제 이동 시간을 입력해 속도 보정을 실행하면, 해당 경로의 기본 보행속도를 역산하고 이후 야외 보행 엣지 시간만 비례 조정합니다. 보정된 기본속도에는 별도 최소/최대 제한을 두지 않습니다. 건물 내부 계단과 셔틀 시간은 개인 보행속도 보정 대상에서 제외됩니다.

### 건물 내부 계단

`measured_data/계단_측정.csv`의 평균값을 사용합니다.

- 올라가기: 18.18초/층
- 내려가기: 11.54초/층
- 기본 층고: 3.0m

건물 내부 출입구 간 엣지는 수평 이동 시간과 층수 차이에 따른 계단 이동 시간을 합산합니다. 상승/하강 방향별 시간이 다르므로 양방향 엣지의 `time_sec`가 서로 다를 수 있습니다.

### 셔틀버스

셔틀 정류장 위치는 `data/processed/snu_shuttle_stops.geojson`을 사용합니다. 순환 노선 순서는 `data/processed/snu_shuttle_circular_route.json`을 사용합니다.

셔틀 시간은 다음 데이터를 반영합니다.

- `measured_data/배차간격_측정.csv`
  - 평균 배차간격: 391.09초
  - Osuna-Newell 보정 기대 대기시간: 198.87초
- `measured_data/셔틀_데이터_정리.xlsx`
  - 정류장별 정차시간
  - 구간별 주행시간

대기시간이 매 셔틀 구간마다 중복되지 않도록 정류장별로 다음 상태 노드를 둡니다.

```text
도보 정류장 노드 -> 셔틀 출발 노드 -> 다음 정류장 도착 노드 -> 도보 정류장 노드
```

계속 탑승하는 경우에는 `도착 노드 -> 출발 노드` 정차시간 엣지를 지나 다음 구간으로 이어집니다. 처음 탑승할 때만 기대 대기시간이 붙습니다.

## 최단경로 알고리즘

최종 라우팅 그래프는 NetworkX `MultiDiGraph`입니다. 경로 탐색은 dijkstra 기반 `networkx.shortest_path(..., weight=...)`를 사용합니다.

런타임 가중치 함수는 엣지 유형별로 다음 값을 반환합니다.

- 야외 보행 엣지: 저장된 기본 시간에 개인 보행속도 비율을 반영
- 건물 내부 엣지: 그래프에 저장된 고정 `time_sec`
- 셔틀 대기/주행/정차/하차 엣지: 그래프에 저장된 고정 `time_sec`
- 셔틀 제외 옵션이 꺼진 경우: 셔틀 엣지는 탐색 대상에서 제외

응답 summary에는 총시간 외에도 보행 거리, 셔틀 거리, 야외 보행 시간, 건물 내부 시간, 셔틀 대기/주행/정차 시간이 분리되어 포함됩니다.

## 프로젝트 구조

```text
.
├── app/
│   ├── main.py                  # FastAPI 엔드포인트
│   ├── graph_loader.py          # GraphML/GeoJSON 로딩, 레이어 변환, 포인트 스냅
│   ├── routing.py               # 시간 가중치, 속도 보정, 최단경로 계산
│   ├── schemas.py               # API 요청/응답 모델
│   └── static/
│       ├── index.html           # Leaflet UI
│       ├── app.js               # 지도 상호작용, API 호출, 레이어 렌더링
│       └── style.css
├── data/
│   ├── manual/
│   │   └── walk_network_additions.json
│   └── processed/
│       ├── snu_campus_boundary.geojson
│       ├── snu_osm_entrances.geojson
│       ├── snu_walk_base.graphml
│       ├── snu_walk_elevation.graphml
│       ├── snu_shuttle_stops.geojson
│       ├── snu_shuttle_circular_route.json
│       ├── snu_building_entrance_matches.json
│       ├── snu_building_entrances_with_floors.geojson
│       ├── snu_routing_graph.graphml
│       ├── snu_routing_nodes.geojson
│       └── snu_routing_edges.geojson
├── measured_data/
│   ├── 계단_측정.csv
│   ├── 배차간격_측정.csv
│   └── 셔틀_데이터_정리.xlsx
├── scripts/
│   ├── export_osm_campus_boundary.py
│   ├── build_osm_walk_graph.py
│   ├── extract_osm_entrances.py
│   ├── add_elevation_to_walk_graph.py
│   ├── extract_snu_shuttle_stops.py
│   ├── match_building_entrances.py
│   ├── build_routing_graph.py
│   └── create_full_map.py
├── requirements.txt
└── README.md
```

`outputs/`, `cache/`, `tmp/`, `graph_update/`, `opendataloader_output/`, `venv/`는 로컬 생성물 또는 작업 캐시이므로 Git에서 제외합니다.

## 실행 방법

Windows PowerShell 기준입니다.

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 다음 주소를 엽니다.

```text
http://localhost:8000
```

서버 상태는 다음 API에서 확인할 수 있습니다.

```text
GET /api/health
```

## API

```text
GET  /
GET  /api/health
POST /api/route
GET  /api/layers/{layer_name}
```

`POST /api/route` 예시:

```json
{
  "start_lon": 126.948658484027,
  "start_lat": 37.4652425830942,
  "end_lon": 126.951849,
  "end_lat": 37.449935,
  "base_walk_speed_kmh": 6.0,
  "calibration_actual_time_sec": null,
  "allow_shuttle": true
}
```

레이어 이름:

```text
campus_boundary
osm_edges
entrances
elevation_nodes
manual_features
shuttle_features
```

## 데이터 재생성

최종 라우팅 그래프를 다시 만들 때는 프로젝트 루트에서 다음 순서로 실행합니다.

```powershell
venv\Scripts\python.exe -m scripts.export_osm_campus_boundary
venv\Scripts\python.exe -m scripts.build_osm_walk_graph
venv\Scripts\python.exe -m scripts.extract_osm_entrances
venv\Scripts\python.exe -m scripts.add_elevation_to_walk_graph
venv\Scripts\python.exe -m scripts.extract_snu_shuttle_stops
venv\Scripts\python.exe -m scripts.build_routing_graph
```

건물 내부 이동용 `data/processed/snu_building_entrance_matches.json`은 현재 저장된 처리 결과를 사용합니다. 건물 폴리곤과 출입구 매칭을 다시 만들 때는 `scripts.extract_building_polygons_from_screenshot`와 `scripts.match_building_entrances`를 별도로 실행합니다. 이 과정은 로컬 검수용 이미지/HTML을 사용하므로 생성물은 `outputs/`에 남기고 Git에는 포함하지 않습니다.

검토용 Folium HTML 지도는 다음 명령으로 생성합니다.

```powershell
venv\Scripts\python.exe -m scripts.create_full_map
```

생성 결과는 `outputs/snu_walk_full.html`에 저장되며 Git에는 포함하지 않습니다.

## 현재 그래프 통계

최근 `scripts.build_routing_graph` 실행 결과입니다.

- 최종 그래프: 노드 1480개, 방향 엣지 4778개
- 셔틀 정류장 노드: 14개
- 셔틀 상태 노드: 28개
- 셔틀 탑승/하차/정차/주행 엣지: 56개
- 건물 내부 엣지: 986개
- 수동 보정 노드: 165개
- 수동 직접 엣지: 82개
- 광장 내부 연결 엣지: 462개
- OSM 입구 연결 엣지: 520개

자세한 로컬 통계는 `outputs/snu_routing_graph_stats.json`에서 확인할 수 있습니다. 이 파일은 로컬 산출물이므로 Git에는 포함하지 않습니다.

## Render 배포 참고

현재 GitHub `main`에 push되면 Render 서비스가 바로 새 버전을 배포합니다. 따라서 push 전에 최소한 다음을 확인합니다.

```powershell
venv\Scripts\python.exe -m py_compile app\routing.py app\graph_loader.py app\schemas.py app\main.py scripts\build_routing_graph.py scripts\create_full_map.py
node --check app\static\app.js
venv\Scripts\python.exe -m scripts.build_routing_graph
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

운영 런타임은 `data/processed/snu_routing_graph.graphml`을 로드하므로, 라우팅 로직 변경 후에는 최종 그래프 파일도 함께 갱신되어야 합니다.

## 주의사항

- OSM 보행 데이터는 실제 보행 가능 여부, 공사 구간, 임시 폐쇄를 완전히 반영하지 못할 수 있습니다.
- 고도 데이터는 외부 API 기반이므로 계단이나 건물 출입구 주변의 미세한 고도 차이가 부정확할 수 있습니다.
- 셔틀 모델은 현재 평균 대기시간 기반입니다. 실시간 위치나 시간표 기반 모델은 아직 반영하지 않았습니다.
- 사용자 속도 보정은 선택한 경로의 야외 보행 시간만 대상으로 역산합니다. 셔틀과 건물 내부 시간은 고정 비용으로 둡니다.

## License

코드와 데이터 사용 조건은 별도 정리가 필요합니다. OpenStreetMap 기반 데이터는 OSM 라이선스와 기여자 표기 조건을 따라야 합니다.
