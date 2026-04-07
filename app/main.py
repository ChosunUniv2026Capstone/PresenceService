from __future__ import annotations

from functools import lru_cache

import redis
from fastapi import FastAPI, HTTPException

from app.cache import RedisSnapshotCache
from app.config import Settings, get_settings
from app.dummy_openwrt import DummySnapshotProvider
from app.models import (
    AdminSnapshotEnvelope,
    DummyOverlayMutationRequest,
    EligibilityRequest,
    EligibilityResponse,
    HealthResponse,
    SnapshotEnvelope,
)
from app.service import PresenceService


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        service = get_presence_service()
        return HealthResponse(
            status="ok",
            redisConnected=service.cache.ping(),
            snapshotTtlSeconds=settings.snapshot_ttl_seconds,
        )

    @app.get("/snapshots/classrooms/{classroom_id}", response_model=SnapshotEnvelope)
    def get_snapshot(classroom_id: str) -> SnapshotEnvelope:
        service = get_presence_service()
        try:
            snapshot, cache_hit = service.get_or_refresh_snapshot(classroom_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="CLASSROOM_NOT_MAPPED") from exc
        return SnapshotEnvelope(cacheHit=cache_hit, snapshot=snapshot)

    @app.post("/eligibility/check", response_model=EligibilityResponse)
    def check_eligibility(request: EligibilityRequest) -> EligibilityResponse:
        service = get_presence_service()
        try:
            return service.evaluate_eligibility(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/admin/dummy/classrooms/{classroom_id}/snapshot", response_model=AdminSnapshotEnvelope)
    def get_admin_snapshot(classroom_id: str) -> AdminSnapshotEnvelope:
        service = get_presence_service()
        try:
            return service.get_admin_snapshot(classroom_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="CLASSROOM_NOT_MAPPED") from exc

    @app.post("/admin/dummy/classrooms/{classroom_id}/overlay", response_model=AdminSnapshotEnvelope)
    def apply_admin_overlay(classroom_id: str, request: DummyOverlayMutationRequest) -> AdminSnapshotEnvelope:
        service = get_presence_service()
        try:
            return service.apply_overlay(classroom_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="CLASSROOM_NOT_MAPPED") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/admin/dummy/classrooms/{classroom_id}/overlay/reset", response_model=AdminSnapshotEnvelope)
    def reset_admin_overlay(classroom_id: str) -> AdminSnapshotEnvelope:
        service = get_presence_service()
        try:
            return service.reset_overlay(classroom_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="CLASSROOM_NOT_MAPPED") from exc
        except TimeoutError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


@lru_cache(maxsize=1)
def get_presence_service() -> PresenceService:
    settings: Settings = get_settings()
    client = redis.Redis(host=settings.redis_host, port=settings.redis_port, db=settings.redis_db, decode_responses=True)
    cache = RedisSnapshotCache(client)
    provider = DummySnapshotProvider(settings.dummy_snapshot_path)
    return PresenceService(
        cache=cache,
        provider=provider,
        snapshot_ttl_seconds=settings.snapshot_ttl_seconds,
        refresh_lock_seconds=settings.refresh_lock_seconds,
    )


app = create_app()
