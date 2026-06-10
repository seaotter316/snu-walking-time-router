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

본 프로젝트의 핵심 가정은 캠퍼스 이동을 다음 세 종류의 시간 비용으로 분리한 뒤, 모두 초 단위 `time_sec`로 환산해 하나의 그래프에서 비교하는 것입니다.

```text
총 경로 시간 =
  건물 외부 경사 보행 시간
+ 건물 내부 계단/수평 이동 시간
+ 셔틀버스 대기/정차/주행 시간
```

각 엣지는 `walk_type`과 `source` 속성으로 이동 유형을 구분합니다. dijkstra는 엣지 유형을 직접 구분하지 않고, 런타임 가중치 함수가 반환하는 `time_sec`만 보고 최단시간 경로를 찾습니다.

### 야외 보행

야외 보행은 건물 외부에서 사람이 걷는 모든 구간을 의미합니다. OSM 보행망, 수동 보정 지름길, 광장 통과, 건물 입구 연결, 셔틀 정류장까지의 보행 연결선이 여기에 들어갑니다. 대표 `walk_type`은 다음과 같습니다.

```text
footway
path
pedestrian
service
steps
plaza_crossing
plaza_connector
entrance_connector
shortcut
shuttle_connector
```

야외 보행 엣지는 Tobler hiking function을 사용합니다. 엣지의 양 끝 노드 고도 차이로 경사를 계산하고, 보행로 유형별 계수를 곱해 속도를 조정합니다.

```text
slope = (도착 노드 고도 - 출발 노드 고도) / 엣지 길이
speed_kmh = base_walk_speed_kmh * exp(-3.5 * abs(slope + 0.05)) * walk_type_factor
time_sec = length_m / (speed_kmh * 1000 / 3600)
```

기본 `base_walk_speed_kmh`는 6.0km/h입니다. `walk_type_factor`는 보행로 종류에 따른 상대 속도 계수입니다. 예를 들어 `footway`는 1.00, `path`는 0.98, `service`는 0.95, `steps`는 0.70처럼 적용됩니다.

엣지 방향이 바뀌면 `slope`의 부호도 바뀝니다. 따라서 같은 물리적 구간이라도 오르막 방향과 내리막 방향의 시간이 다를 수 있습니다.

### 개인 보행속도 보정

사용자는 지도에서 경로를 찍고 실제 걸린 시간을 입력할 수 있습니다. 이때 시스템은 기본속도 6.0km/h로 계산한 해당 경로의 야외 보행 시간을 기준으로 개인 기본속도를 역산합니다.

```text
default_total_time = outdoor_default_time + fixed_time
actual_total_time = 사용자 입력 실제 시간
actual_outdoor_time = actual_total_time - fixed_time
calibrated_base_walk_speed_kmh =
  6.0 * outdoor_default_time / actual_outdoor_time
```

여기서 `fixed_time`은 건물 내부 계단 시간과 셔틀 시간처럼 개인 보행속도로 바뀌면 안 되는 시간입니다. 즉 속도 보정은 야외 보행 엣지에만 적용됩니다.

보정된 기본속도에는 별도 최소/최대 제한을 두지 않습니다. 사용자가 입력한 결과가 비현실적으로 빠르거나 느려도 그대로 반영합니다. 이후 야외 보행 엣지의 시간은 저장된 기본 시간에 다음 비율을 곱해 런타임에 조정합니다.

```text
adjusted_outdoor_time =
  stored_outdoor_time * stored_base_walk_speed_kmh / requested_base_walk_speed_kmh
```

현재 저장 그래프의 `stored_base_walk_speed_kmh`는 6.0km/h입니다.

### 건물 내부 계단

건물 내부 이동은 건물의 서로 다른 출입구 사이를 실내에서 이동하는 경우를 모델링합니다. `walk_type == "building_internal"` 엣지로 저장되며, 일반 야외 보행 공식과 분리해 고정 시간으로 계산합니다.

건물 내부 엣지는 `data/processed/snu_building_entrance_matches.json`의 건물별 출입구 매칭을 사용해 만들어집니다. 같은 건물에 속한 출입구들을 서로 연결하고, 출입구의 고도 차이를 층수 차이로 환산합니다.

`measured_data/계단_측정.csv`의 평균값을 사용합니다.

- 올라가기: 18.18초/층
- 내려가기: 11.54초/층
- 기본 층고: 3.0m

계산 방식은 다음과 같습니다.

```text
horizontal_m = 두 출입구 좌표 사이 수평 거리
vertical_m = abs(도착 출입구 고도 - 출발 출입구 고도)
floor_delta = vertical_m / 3.0

horizontal_time_sec = horizontal_m / 1.2
vertical_time_sec =
  floor_delta * 18.18  # 올라가기
  floor_delta * 11.54  # 내려가기

building_internal_time_sec =
  horizontal_time_sec + vertical_time_sec
```

수평 이동 속도는 1.2m/s로 둡니다. 상승/하강 방향별 시간이 다르므로 양방향 엣지의 `time_sec`가 서로 다를 수 있습니다. 이 엣지는 개인 보행속도 보정 영향을 받지 않습니다.

### 셔틀버스

셔틀버스 이동은 정류장 노드와 셔틀 상태 노드로 나누어 모델링합니다. 정류장 위치는 `data/processed/snu_shuttle_stops.geojson`을 사용합니다. 순환 노선 순서는 `data/processed/snu_shuttle_circular_route.json`을 사용합니다.

셔틀 시간은 다음 데이터를 반영합니다.

- `measured_data/배차간격_측정.csv`
  - 평균 배차간격: 391.09초
  - 불규칙 배차를 고려한 기대 대기시간: 198.87초
- `measured_data/셔틀_데이터_정리.xlsx`
  - 정류장별 정차시간
  - 구간별 주행시간

대기시간은 Osuna and Newell(1972)의 논문 *Control Strategies for an Idealized Public Transportation System*에서 사용하는 무작위 도착 승객의 평균 대기시간 관계를 참고합니다. 승객이 셔틀 도착 시각과 독립적으로 정류장에 도착한다고 보면, 배차간격 `H`에 대한 기대 대기시간은 다음과 같습니다.

```text
E[W] = E[H^2] / (2E[H])
     = E[H] / 2 + Var(H) / (2E[H])
```

배차간격이 완전히 일정하면 `E[H] / 2`와 같지만, 실제 배차간격의 분산이 있으면 평균 대기시간이 더 커질 수 있습니다. 따라서 본 프로젝트에서는 `배차간격_측정.csv`의 실측 배차간격으로 위 값을 계산해 `shuttle_wait`의 기대 대기시간으로 사용합니다.

셔틀 엣지는 다음 네 가지 `walk_type`으로 나뉩니다.

```text
shuttle_wait    # 정류장에서 셔틀을 기다리고 탑승하기까지
shuttle_ride    # 한 정류장에서 다음 정류장까지 주행
shuttle_dwell   # 같은 버스를 계속 탔을 때 정류장에서 정차하는 시간
shuttle_alight  # 도착 상태에서 보행 정류장 노드로 나오는 연결
```

대기시간이 매 셔틀 구간마다 중복되지 않도록 정류장별로 다음 상태 노드를 둡니다.

```text
도보 정류장 노드 -> 셔틀 출발 노드 -> 다음 정류장 도착 노드 -> 도보 정류장 노드
```

처음 셔틀을 타는 경우:

```text
도보 정류장 노드
-> shuttle_wait, 198.87초
-> 셔틀 출발 노드
-> shuttle_ride, 측정 구간 주행시간
-> 다음 정류장 도착 노드
-> shuttle_alight
-> 도보 정류장 노드
```

계속 탑승하는 경우:

```text
정류장 도착 노드
-> shuttle_dwell, 해당 정류장 평균 정차시간
-> 같은 정류장 출발 노드
-> shuttle_ride, 다음 구간 주행시간
```

이 구조 덕분에 한 번 탄 셔틀을 여러 정류장 동안 계속 이용할 때 기대 대기시간이 반복해서 붙지 않습니다. 셔틀 시간은 개인 보행속도 보정 영향을 받지 않습니다.

## 최단경로 알고리즘

최종 라우팅 그래프는 NetworkX `MultiDiGraph`입니다. 경로 탐색은 dijkstra 기반 `networkx.shortest_path(..., weight=...)`를 사용합니다.

런타임 가중치 함수는 엣지 유형별로 다음 값을 반환합니다.

- 야외 보행 엣지: 저장된 기본 시간에 개인 보행속도 비율을 반영
- 건물 내부 엣지: 그래프에 저장된 고정 `time_sec`
- 셔틀 대기/주행/정차/하차 엣지: 그래프에 저장된 고정 `time_sec`
- 셔틀 제외 옵션이 꺼진 경우: 셔틀 엣지는 탐색 대상에서 제외

응답 summary에는 총시간 외에도 보행 거리, 셔틀 거리, 야외 보행 시간, 건물 내부 시간, 셔틀 대기/주행/정차 시간이 분리되어 포함됩니다.

### 경로 요약값

`POST /api/route` 응답의 `summary`에는 계산 검토를 위해 다음 항목이 포함됩니다.

```text
total_time_sec
total_time_min
total_length_m
walking_length_m
shuttle_length_m
outdoor_walk_time_sec
building_internal_time_sec
shuttle_wait_time_sec
shuttle_ride_time_sec
shuttle_dwell_time_sec
shuttle_time_sec
uses_shuttle
base_walk_speed_kmh
total_ascent_m
total_descent_m
start_snap_distance_m
end_snap_distance_m
```

`total_ascent_m`과 `total_descent_m`은 보행 및 건물 내부 이동 구간을 기준으로 계산합니다. 셔틀 이동 중의 고도 변화는 사용자가 직접 걸어서 오르내린 것이 아니므로 보행 상승/하강량에 넣지 않습니다.

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
py -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

`py` 명령을 사용할 수 없는 환경에서는 설치된 Python 실행 파일로 `python -m venv venv`를 실행합니다.

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

- 최종 그래프: 노드 1502개, 방향 엣지 5188개
- 셔틀 정류장 노드: 14개
- 셔틀 상태 노드: 28개
- 셔틀 탑승/하차/정차/주행 엣지: 56개
- 건물 내부 엣지: 1232개
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

코드의 라이선스는 별도 배포 전 확정이 필요합니다. OpenStreetMap 기반 데이터와 지도 타일은 OpenStreetMap 기여자 표기 및 ODbL 조건을 따라야 하며, 앱 화면의 Leaflet 기본 타일 레이어에도 OpenStreetMap attribution을 표시합니다.
