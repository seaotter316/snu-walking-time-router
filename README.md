# 서울대 도보 그래프 구축

이 프로젝트는 OpenStreetMap 데이터를 이용해 서울대학교 관악캠퍼스의
기본 도보 네트워크 그래프를 구축한다.

## 설치

현재 로컬 `venv`에는 필요한 패키지가 이미 설치되어 있다.
패키지를 다시 설치하려면 아래 명령을 실행하면 된다.

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
```

## OSM 도보 그래프 생성

```powershell
venv\Scripts\python.exe scripts\build_osm_walk_graph.py
```

이 스크립트는 OSM에 등록된 `Seoul National University Gwanak Campus`
경계 polygon을 기준으로, OSMnx/Overpass를 통해 보행 가능한 OSM 길 데이터를
내려받는다.
내려받은 API 응답은 `data/raw/osmnx_cache`에 캐시된다.

OSM 장소 경계 검색이 실패할 경우에만 스크립트 내부의 예비 polygon을 사용한다.
예비 polygon은 캠퍼스 주변을 넉넉히 감싸기 때문에 캠퍼스 밖 길이 일부 포함될 수
있다.

## 생성 파일

- `data/processed/snu_walk_base.graphml`: NetworkX/OSMnx 그래프 파일
- `data/processed/snu_walk_nodes.geojson`: 그래프 노드 GeoJSON
- `data/processed/snu_walk_edges.geojson`: 그래프 엣지 GeoJSON
- `data/processed/snu_osm_entrances.geojson`: OSM `entrance=*` 입구 점 GeoJSON
- `data/processed/snu_osm_buildings.geojson`: OSM 건물 객체 GeoJSON
- `data/processed/snu_walk_elevation.graphml`: 노드 고도와 엣지 경사도가 추가된 그래프
- `data/processed/snu_walk_nodes_elevation.geojson`: 고도 포함 그래프 노드 GeoJSON
- `data/processed/snu_walk_edges_elevation.geojson`: 경사도 포함 그래프 엣지 GeoJSON
- `data/processed/snu_walk_with_lawn_plaza.graphml`: 잔디광장 가상 엣지가 추가된 그래프
- `data/processed/snu_walk_with_lawn_plaza_nodes.geojson`: 잔디광장 처리 후 노드 GeoJSON
- `data/processed/snu_walk_with_lawn_plaza_edges.geojson`: 잔디광장 처리 후 엣지 GeoJSON
- `data/processed/snu_walk_time_weighted.graphml`: 경사 기반 이동시간 가중치가 추가된 그래프
- `outputs/snu_walk_base.html`: 시각 검수용 인터랙티브 지도
- `outputs/snu_walk_with_lawn_plaza.html`: 잔디광장 가상 엣지 확인용 지도
- `outputs/snu_walk_full.html`: 현재까지 만든 모든 레이어를 포함한 통합 지도
- `outputs/snu_shortest_time_route.html`: 최단시간 경로 결과 지도
- `outputs/snu_walk_base_stats.json`: 기본 그래프 통계
- `outputs/snu_osm_buildings_entrances_stats.json`: 건물/입구 객체 통계
- `outputs/snu_walk_elevation_stats.json`: 고도/경사도 통계
- `outputs/snu_walk_lawn_plaza_stats.json`: 잔디광장 처리 통계
- `outputs/snu_shortest_time_route_stats.json`: 최단시간 경로 결과 통계

`outputs/snu_walk_base.html`에서 파란 선은 OSM 보행 그래프 엣지, 빨간 선은
서울대 관악캠퍼스 경계, 주황색 점은 OSM `entrance=*` 건물 입구, 청록색 점은
고도가 붙은 그래프 노드를 의미한다.

## 통합 지도 생성

```powershell
venv\Scripts\python.exe scripts\create_full_map.py
```

생성된 `outputs/snu_walk_full.html`에는 OSM 보행 엣지, 건물 입구, 노드 고도,
잔디광장 polygon, 잔디광장 진입점, 잔디광장 가상 엣지가 함께 들어간다.
오른쪽 위 레이어 컨트롤에서 각 레이어를 켜고 끌 수 있다.

## OSM 건물/입구 데이터 추출

```powershell
venv\Scripts\python.exe scripts\extract_osm_buildings_entrances.py
```

현재 OSM 기준으로 서울대 관악캠퍼스 경계 안에서 `entrance=*` 입구 점 303개와
건물 객체 202개가 추출된다. 다만 OSM 입구 데이터는 모든 건물의 주출입구를
완전하게 보장하지 않으므로, 주요 건물은 현장 확인 또는 수동 보정이 필요하다.

## 노드 고도와 엣지 경사도 추가

```powershell
venv\Scripts\python.exe scripts\add_elevation_to_walk_graph.py
venv\Scripts\python.exe scripts\build_osm_walk_graph.py
```

첫 번째 명령은 그래프 노드에 고도 값을 붙이고, 각 엣지 양끝 노드의 고도 차이를
이용해 경사도를 계산한다. 두 번째 명령은 고도 노드 레이어가 포함된 HTML 지도를
다시 생성한다.

현재 고도 데이터는 Open-Meteo Elevation API의 Copernicus DEM GLO-90 기반 값이다.
해상도가 약 90m급이므로 캠퍼스 전체의 고저 차이 파악에는 유용하지만, 짧은 계단이나
건물 출입구 주변의 미세한 경사까지 정확하게 표현하지는 못한다.

## 잔디광장 가상 엣지 추가

잔디광장 꼭짓점 좌표를 직접 찍으려면 아래 Python 클릭 도구를 사용한다.

```powershell
venv\Scripts\python.exe scripts\pick_lawn_plaza_corners_matplotlib.py
```

뜨는 matplotlib 창에는 OpenStreetMap 배경지도, OSM 보행 엣지, 건물, 입구가 함께
표시된다. 사각형 꼭짓점 4개를 순서대로 클릭하면
`outputs/lawn_plaza_corners.json`에 좌표가 저장된다. `Backspace` 또는 `Delete`로
마지막 점을 삭제할 수 있고, `Esc`로 전체 삭제할 수 있다.

HTML 방식이 필요하면 아래 명령으로 별도 도구를 생성할 수 있다.

```powershell
venv\Scripts\python.exe scripts\create_lawn_plaza_corner_picker.py
```

```powershell
venv\Scripts\python.exe scripts\add_lawn_plaza_crossings.py
```

서울대 잔디광장은 도로처럼 선형 엣지 하나로 표현하기 어렵기 때문에, 행정관 앞
공간을 수동 polygon으로 근사하고 주요 진입점 6개를 추가한다. 이후 진입점끼리
광장 내부를 직선으로 가로지를 수 있는 `plaza_crossing` 가상 엣지를 양방향으로
연결하고, 각 진입점은 가장 가까운 OSM 보행 그래프 노드와 `plaza_connector`
엣지로 연결한다.

현재 처리 결과는 다음과 같다.

```json
{
  "manual_gate_nodes": 8,
  "directed_plaza_crossing_edges": 56,
  "directed_plaza_connector_edges": 16
}
```

잔디광장 polygon은 사용자가 클릭한 4개 꼭짓점을 주변 OSM 엣지에 스냅한 좌표를
사용한다. 진입점은 꼭짓점 4개, 변 중앙점 2개, 동북/서남 방향의 OSM 연결점 2개로
구성된다. 현장 조사 후 추가 보정하는 것을 전제로 한다.

## 경사 기반 최단시간 경로 탐색

브라우저 지도에서 출발/도착 좌표를 편하게 찍으려면 아래 도구를 사용한다.

```powershell
venv\Scripts\python.exe scripts\create_route_picker_html.py
```

생성된 `outputs/pick_route_points.html`을 열면 서울대 지도를 자유롭게 이동/확대할 수
있다. 출발 지점과 도착 지점을 클릭하면 오른쪽 패널에 실행 명령이 생성된다.

```powershell
venv\Scripts\python.exe scripts\find_shortest_time_path.py
```

이 명령만 실행하면 OpenStreetMap 배경지도가 깔린 matplotlib 창이 뜨고, 출발 지점과
도착 지점을 순서대로 클릭할 수 있다. 클릭 지점에서 가장 가까운 그래프 노드를
출발/도착 노드로 잡은 뒤, 각 엣지의
경사도에 따른 보행속도를 계산하여 `time_sec`를 가중치로 둔 Dijkstra 최단경로를 찾는다.

노드 ID를 직접 알고 있으면 아래처럼 실행할 수 있다.

```powershell
venv\Scripts\python.exe scripts\find_shortest_time_path.py --start-node 2578318968 --end-node 1793038600
```

좌표를 직접 넣는 것도 가능하다.

```powershell
venv\Scripts\python.exe scripts\find_shortest_time_path.py --start-lon 126.9500 --start-lat 37.4603 --end-lon 126.9510 --end-lat 37.4609
```

이동시간은 다음 모델로 계산한다.

```text
slope = (도착 노드 고도 - 출발 노드 고도) / 엣지 길이
speed_kmh = 6 * exp(-3.5 * abs(slope + 0.05)) * walk_type_factor
time_sec = length_m / (speed_kmh * 1000 / 3600)
```

같은 엣지라도 오르막 방향과 내리막 방향의 `slope`가 달라지므로 방향별 시간이 다르게
계산된다.

현재 그래프 통계:

```json
{
  "nodes": 620,
  "edges": 1620,
  "connected_components": 22,
  "largest_component_nodes": 576,
  "total_directed_edge_length_m": 72576.13,
  "boundary": "OSM 서울대 관악캠퍼스 경계",
  "strict_boundary_removed_nodes": 27,
  "strict_boundary_removed_edges": 84,
  "osm_base_timestamp_utc": "2026-05-15T16:05:01Z",
  "osm_base_timestamp_kst": "2026-05-16 01:05:01 KST"
}
```
