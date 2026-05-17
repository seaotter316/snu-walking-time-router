# SNU Walking Time Router

서울대학교 관악캠퍼스의 보행 네트워크를 OpenStreetMap 데이터로 구축하고, 고도와 경사도를 반영해 예상 이동 시간이 가장 짧은 도보 경로를 탐색하는 웹 애플리케이션입니다.

단순 최단거리보다 실제 캠퍼스 보행 환경에 가까운 경로 탐색을 목표로 합니다. 경사, 계단, 보행로 유형, 잔디광장처럼 OSM 보행망만으로 표현하기 어려운 공간을 보정해 경로 계산에 반영합니다.

## 주요 기능

- 서울대 관악캠퍼스 OSM 보행 네트워크 구축
- 건물 입구와 캠퍼스 경계 GeoJSON 추출
- Open-Meteo Elevation API 기반 노드 고도 및 엣지 경사도 계산
- 잔디광장 내부 통행을 위한 수동 가상 노드/엣지 추가
- Tobler hiking function 기반 보행 속도 및 이동 시간 계산
- FastAPI API와 Leaflet 기반 웹 지도 제공
- 출발/도착 지점 클릭 후 최단시간 경로 시각화

## 프로젝트 구조

```text
.
├── app/
│   ├── main.py              # FastAPI 진입점
│   ├── graph_loader.py      # GraphML/GeoJSON 로딩 및 레이어 제공
│   ├── routing.py           # 경사 기반 시간 가중치와 최단경로 계산
│   ├── schemas.py           # API 요청/응답 모델
│   └── static/              # Leaflet 웹 UI
├── data/
│   └── processed/           # 배포 앱이 사용하는 전처리 GraphML/GeoJSON
├── scripts/                 # OSM 데이터 수집, 보정, 검수용 스크립트
├── requirements.txt
└── README.md
```

`outputs/`, `cache/`, `data/raw/`, `venv/`는 로컬 생성물이라 Git에는 포함하지 않습니다.

## 설치 및 로컬 실행

Python 가상환경을 만든 뒤 의존성을 설치합니다.

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

FastAPI 서버를 실행합니다.

```powershell
venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저에서 아래 주소를 엽니다.

```text
http://localhost:8000
```

헬스 체크:

```text
http://localhost:8000/api/health
```

## Render 배포

Render의 Python Web Service로 배포할 수 있습니다.

Build Command:

```bash
pip install -r requirements.txt
```

Start Command:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

배포 후 `/api/health`에서 그래프 노드/엣지 수가 반환되면 데이터 로딩이 정상적으로 완료된 것입니다.

## 데이터 생성 파이프라인

전처리 데이터를 다시 만들고 싶을 때는 `scripts/`의 스크립트를 순서대로 실행합니다.

1. OSM 보행 그래프 생성

```powershell
venv\Scripts\python.exe scripts\build_osm_walk_graph.py
```

2. 건물/입구 데이터 추출

```powershell
venv\Scripts\python.exe scripts\extract_osm_buildings_entrances.py
```

3. 캠퍼스 경계 추출

```powershell
venv\Scripts\python.exe scripts\export_osm_campus_boundary.py
```

4. 고도와 경사도 추가

```powershell
venv\Scripts\python.exe scripts\add_elevation_to_walk_graph.py
```

5. 잔디광장 가상 통행 엣지 추가

```powershell
venv\Scripts\python.exe scripts\add_lawn_plaza_crossings.py
```

6. 통합 검수 지도 생성

```powershell
venv\Scripts\python.exe scripts\create_full_map.py
```

생성된 GraphML/GeoJSON 파일은 `data/processed/`에 저장됩니다. 검수용 HTML과 통계 파일은 `outputs/`에 저장됩니다.

## 경로 계산 방식

각 엣지의 이동 시간은 다음 모델을 기반으로 계산합니다.

```text
slope = (도착 노드 고도 - 출발 노드 고도) / 엣지 길이
speed_kmh = 6 * exp(-3.5 * abs(slope + 0.05)) * walk_type_factor
time_sec = length_m / (speed_kmh * 1000 / 3600)
```

같은 길이라도 오르막과 내리막 방향의 경사도가 다르므로 방향별 이동 시간이 달라질 수 있습니다. `footway`, `path`, `service`, `steps`, `plaza_crossing` 등 보행로 유형별 보정 계수도 적용합니다.

## 현재 데이터 기준

현재 포함된 전처리 데이터는 OSM 서울대 관악캠퍼스 경계 기준으로 생성되었습니다.

```json
{
  "nodes": 620,
  "edges": 1620,
  "largest_component_nodes": 576,
  "total_directed_edge_length_m": 72576.13,
  "osm_base_timestamp_kst": "2026-05-16 01:05:01 KST"
}
```

잔디광장 보정은 수동으로 근사한 polygon과 진입점을 사용합니다.

```json
{
  "manual_gate_nodes": 8,
  "directed_plaza_crossing_edges": 56,
  "directed_plaza_connector_edges": 16
}
```

## 한계와 주의사항

- OSM 데이터 품질에 따라 누락된 보행로, 건물 입구, 계단이 있을 수 있습니다.
- 고도 데이터는 약 90m급 DEM 기반이라 짧은 계단이나 건물 출입구 주변의 미세한 경사는 정확하지 않을 수 있습니다.
- 잔디광장 내부 통행은 실제 보행 동선이 아니라 수동 근사 모델입니다.
- 경로 결과는 참고용이며, 실제 보행 가능 여부는 현장 상황과 다를 수 있습니다.

## License

이 저장소의 코드와 데이터 사용 조건은 추후 명시 예정입니다. OpenStreetMap 기반 데이터는 OSM의 라이선스 및 기여자 표시 정책을 따릅니다.
