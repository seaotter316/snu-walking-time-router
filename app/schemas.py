from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RouteRequest(BaseModel):
    start_lon: float = Field(..., ge=-180, le=180)
    start_lat: float = Field(..., ge=-90, le=90)
    end_lon: float = Field(..., ge=-180, le=180)
    end_lat: float = Field(..., ge=-90, le=90)
    base_walk_speed_kmh: float | None = Field(None, gt=0)
    calibration_actual_time_sec: float | None = Field(None, gt=0)
    allow_shuttle: bool = True


class RouteSummary(BaseModel):
    total_length_m: float
    walking_length_m: float = 0.0
    shuttle_length_m: float = 0.0
    total_time_sec: float
    total_time_min: float
    outdoor_walk_time_sec: float = 0.0
    building_internal_time_sec: float = 0.0
    shuttle_wait_time_sec: float = 0.0
    shuttle_ride_time_sec: float = 0.0
    shuttle_dwell_time_sec: float = 0.0
    shuttle_time_sec: float = 0.0
    uses_shuttle: bool = False
    base_walk_speed_kmh: float = 6.0
    total_ascent_m: float
    total_descent_m: float
    start_node: int | str
    end_node: int | str
    start_snap_distance_m: float
    end_snap_distance_m: float
    allow_shuttle: bool = True
    calibrated_base_walk_speed_kmh: float | None = None
    raw_calibrated_base_walk_speed_kmh: float | None = None
    calibration_clamped: bool | None = None
    calibration_actual_time_sec: float | None = None
    calibration_default_time_sec: float | None = None
    calibration_fixed_time_sec: float | None = None
    calibration_outdoor_default_time_sec: float | None = None


class RouteResponse(BaseModel):
    route_geojson: dict[str, Any]
    summary: RouteSummary
