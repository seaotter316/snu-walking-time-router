# SNU Walking Time Router

서울대학교 관악캠퍼스의 도보 경로를 예상 이동 시간 기준으로 탐색하는 프로토타입 웹 애플리케이션입니다. OpenStreetMap 보행망에 고도, 경사, 보행로 유형을 반영하고, OSM만으로 표현하기 어려운 통행 구간은 별도 설정 데이터로 추가합니다.

현재 수동 보정 예시는 잔디광장입니다. 수동 노드와 엣지는 코드에 고정하지 않고 `data/manual/walk_network_additions.json`에 보관하므로, 추후 지름길, 실내 연결, 추가 광장 등의 노드/엣지를 같은 방식으로 확장할 수 있습니다.

## 동작 방식

1. 전처리 스크립트가 OSM 보행 그래프와 캠퍼스 표시 레이어를 수집합니다.
2. 고도 스크립트가 그래프 노드에 고도를 붙이고 엣지 경사를 계산합니다.
3. 라우팅 빌드 스크립트가 수동 보정 데이터를 합치고 모든 엣지에 `time_sec` 가중치를 계산합니다.
4. FastAPI 앱은 최종 `snu_routing_graph.graphml`만 로드합니다.
5. 브라우저에서 사용자가 출발/도착 지점을 클릭하면, 서버가 가장 가까운 노드로 스냅한 뒤 최단시간 경로를 반환합니다.

경로 가중치는 Tobler hiking function과 보행로 유형별 계수를 사용합니다.

```text
slope = (도착 노드 고도 - 출발 노드 고도) / 엣지 길이
speed_kmh = 6 * exp(-3.5 * abs(slope + 0.05)) * walk_type_factor
time_sec = length_m / (speed_kmh * 1000 / 3600)
```

## 프로젝트 구조

```text
.
├── app/
│   ├── main.py                  # FastAPI 엔드포인트와 정적 웹 제공
│   ├── graph_loader.py          # 최종 그래프/GeoJSON 레이어 로딩
│   ├── routing.py               # 시간 가중치 및 최단경로 계산
│   ├── schemas.py               # API 요청/응답 모델
│   └── static/                  # Leaflet 지도 UI
├── data/
│   ├── manual/
│   │   └── walk_network_additions.json  # 수동 노드/엣지/면 보정 설정
│   └── processed/
│       ├── snu_campus_boundary.geojson  # 지도 표시용 경계
│       ├── snu_osm_entrances.geojson    # 지도 표시용 입구
│       ├── snu_walk_base.graphml        # OSM 원본 보행 그래프
│       ├── snu_walk_elevation.graphml   # 고도를 추가한 중간 그래프
│       ├── snu_routing_graph.graphml    # 앱이 로드하는 최종 그래프
│       ├── snu_routing_nodes.geojson    # 최종 노드 표시 레이어
│       └── snu_routing_edges.geojson    # 최종 엣지 표시 레이어
├── scripts/
│   ├── export_osm_campus_boundary.py   # 캠퍼스 경계 수집
│   ├── build_osm_walk_graph.py         # OSM 보행 그래프 구축
│   ├── extract_osm_entrances.py        # 입구 레이어 수집
│   ├── add_elevation_to_walk_graph.py  # 고도/경사 추가
│   ├── build_routing_graph.py          # 수동 보정 병합 및 최종 가중치 빌드
│   └── create_full_map.py              # 검수용 통합 HTML 생성
├── requirements.txt
└── README.md
```

`cache/`, `data/raw/`, `outputs/`, `venv/`는 재생성 가능한 로컬 작업물이며 Git에 포함하지 않습니다.

## 로컬 실행

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 `http://localhost:8000`을 엽니다. 상태 점검은 `http://localhost:8000/api/health`에서 할 수 있습니다.

주요 API:

```text
GET  /api/health
POST /api/route
GET  /api/layers/{campus_boundary|osm_edges|entrances|elevation_nodes|manual_features}
```

## 데이터 재생성

프로젝트 루트에서 아래 순서로 실행합니다. 처음 네 단계는 OSM 또는 고도 API 요청을 수행할 수 있고, 마지막 라우팅 그래프 빌드는 이미 내려받은 데이터만 사용합니다.

```powershell
venv\Scripts\python.exe -m scripts.export_osm_campus_boundary
venv\Scripts\python.exe -m scripts.build_osm_walk_graph
venv\Scripts\python.exe -m scripts.extract_osm_entrances
venv\Scripts\python.exe -m scripts.add_elevation_to_walk_graph
venv\Scripts\python.exe -m scripts.build_routing_graph
```

검수용 단일 HTML 지도는 필요할 때 생성합니다.

```powershell
venv\Scripts\python.exe -m scripts.create_full_map
```

## 수동 그래프 확장

`data/manual/walk_network_additions.json`은 세 가지 목록을 가집니다.

- `nodes`: 추가할 노드 좌표와 이름입니다. `connect_to_network: true`이면 가장 가까운 기존 네트워크 노드와 양방향 커넥터를 만듭니다.
- `edges`: 수동 노드 사이에 직접 추가할 엣지입니다. `u`, `v`, `walk_type`, `bidirectional`을 지정합니다.
- `areas`: 지도에 표시할 polygon입니다. `fully_connected: true`이면 같은 `feature_id`를 가진 노드들을 서로 연결하여 통과 가능한 열린 공간으로 모델링합니다.

수동 노드의 `elevation_m`을 지정하지 않으면 연결되는 기존 노드의 고도를 사용합니다. 보정 데이터 변경 후에는 아래 명령 하나로 앱용 최종 그래프를 다시 만듭니다.

```powershell
venv\Scripts\python.exe -m scripts.build_routing_graph
```

## 현재 포함 데이터

현재 최종 라우팅 그래프는 노드 `628`개, 방향성 엣지 `1692`개로 구성됩니다. 이 중 잔디광장 보정은 수동 노드 `8`개와 방향성 엣지 `72`개입니다.

## 한계

- OSM 데이터 품질에 따라 실제 보행로, 계단, 입구가 누락될 수 있습니다.
- 고도 데이터는 짧은 계단이나 건물 출입구 주변의 미세한 경사를 정확히 표현하지 못할 수 있습니다.
- 수동 보정 구간은 실제 현장 통행 가능 여부를 별도로 확인해야 합니다.

## License

이 저장소의 코드와 데이터 사용 조건은 추후 명시 예정입니다. OpenStreetMap 기반 데이터는 OSM 라이선스 및 기여자 표시 정책을 따릅니다.
