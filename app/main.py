from __future__ import annotations

from functools import lru_cache
from typing import Any

import redis
from fastapi import Body, FastAPI, Header, HTTPException, status

from app.cache import RedisSnapshotCache
from app.config import Settings, get_settings
from app.dummy_openwrt import DummySnapshotProvider
from app.models import (
    AdminSnapshotEnvelope,
    DummyOverlayMutationRequest,
    CollectorIngestResponse,
    CollectorSnapshotRequest,
    EligibilityRequest,
    EligibilityResponse,
    HealthResponse,
    SnapshotEnvelope,
)
from app.registry import BackendRegistryClient
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



    @app.get("/collector/aps/health")
    def collector_health() -> dict:
        service = get_presence_service()
        try:
            return service.collector_health()
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": str(exc), "message": "collector registry unavailable"}) from exc

    @app.post("/collector/aps/{collector_ap_id}/snapshot", response_model=CollectorIngestResponse)
    def ingest_collector_snapshot(
        collector_ap_id: str,
        payload: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None, alias="Authorization"),
        x_collector_nonce: str | None = Header(default=None, alias="X-Collector-Nonce"),
        x_collector_timestamp: str | None = Header(default=None, alias="X-Collector-Timestamp"),
    ) -> CollectorIngestResponse:
        service = get_presence_service()
        try:
            return service.ingest_collector_snapshot(
                collector_ap_id=collector_ap_id,
                authorization=authorization,
                request=CollectorSnapshotRequest.model_validate(payload),
                nonce=x_collector_nonce,
                timestamp_header=x_collector_timestamp,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": str(exc), "message": "collector authentication failed"}) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"code": str(exc), "message": "collector registry unavailable"}) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": str(exc), "message": "collector snapshot rejected"}) from exc

    @app.post("/eligibility/check", response_model=EligibilityResponse)
    def check_eligibility(payload: dict[str, Any] = Body(...)) -> EligibilityResponse:
        service = get_presence_service()
        try:
            return service.evaluate_eligibility(EligibilityRequest.model_validate(payload))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/admin/dummy/classrooms/{classroom_id}/snapshot", response_model=AdminSnapshotEnvelope)
    def get_admin_snapshot(classroom_id: str, refresh: bool = False, source: str = "auto") -> AdminSnapshotEnvelope:
        service = get_presence_service()
        try:
            return service.get_admin_snapshot(classroom_id, force_refresh=refresh, source=source)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="CLASSROOM_NOT_MAPPED") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/admin/dummy/classrooms/{classroom_id}/overlay", response_model=AdminSnapshotEnvelope)
    def apply_admin_overlay(classroom_id: str, payload: dict[str, Any] = Body(...)) -> AdminSnapshotEnvelope:
        service = get_presence_service()
        try:
            return service.apply_overlay(classroom_id, DummyOverlayMutationRequest.model_validate(payload))
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
    registry_client = BackendRegistryClient(settings.backend_service_url, settings.presence_internal_token) if settings.collector_push_enabled else None
    return PresenceService(
        cache=cache,
        provider=provider,
        snapshot_ttl_seconds=settings.snapshot_ttl_seconds,
        refresh_lock_seconds=settings.refresh_lock_seconds,
        registry_client=registry_client,
        collector_offline_after_seconds=settings.collector_offline_after_seconds,
        collector_timestamp_window_seconds=settings.collector_timestamp_window_seconds,
        ap_token_hash_secret=settings.ap_token_hash_secret,
        registry_cache_ttl_seconds=settings.registry_cache_ttl_seconds,
    )


app = create_app()
