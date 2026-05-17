from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RouteRequest(BaseModel):
    start_lon: float = Field(..., ge=-180, le=180)
    start_lat: float = Field(..., ge=-90, le=90)
    end_lon: float = Field(..., ge=-180, le=180)
    end_lat: float = Field(..., ge=-90, le=90)


class RouteSummary(BaseModel):
    total_length_m: float
    total_time_sec: float
    total_time_min: float
    total_ascent_m: float
    total_descent_m: float
    start_node: int | str
    end_node: int | str
    start_snap_distance_m: float
    end_snap_distance_m: float


class RouteResponse(BaseModel):
    route_geojson: dict[str, Any]
    summary: RouteSummary
