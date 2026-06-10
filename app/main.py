from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .graph_loader import PROJECT_ROOT, RoutingGraph, layer_geojson, load_routing_graph
from .routing import RouteNotFound
from .schemas import RouteRequest, RouteResponse


STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.routing_graph = load_routing_graph()
    yield


app = FastAPI(title="SNU Walking Time Router", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def get_routing_graph(request: Request) -> RoutingGraph:
    graph = getattr(request.app.state, "routing_graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="Routing graph is not loaded yet.")
    return graph


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health(request: Request) -> dict:
    graph = get_routing_graph(request)
    return {
        "status": "ok",
        "graph_path": str(graph.graph_path.relative_to(PROJECT_ROOT)),
        "nodes": graph.graph.number_of_nodes(),
        "edges": graph.graph.number_of_edges(),
    }


@app.post("/api/route", response_model=RouteResponse)
def api_route(payload: RouteRequest, request: Request) -> dict:
    graph = get_routing_graph(request)
    try:
        return graph.route_between_points(
            payload.start_lon,
            payload.start_lat,
            payload.end_lon,
            payload.end_lat,
            payload.base_walk_speed_kmh,
            payload.calibration_actual_time_sec,
            payload.allow_shuttle,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RouteNotFound as exc:
        raise HTTPException(status_code=404, detail="No walkable route was found between the snapped nodes.") from exc


@app.get("/api/layers/{layer_name}")
def api_layer(layer_name: str, request: Request) -> JSONResponse:
    graph = get_routing_graph(request)
    try:
        return JSONResponse(layer_geojson(layer_name, graph.graph))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"Layer source file is missing: {layer_name}") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown layer: {layer_name}") from exc
