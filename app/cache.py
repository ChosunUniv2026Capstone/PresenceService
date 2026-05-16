from __future__ import annotations

import json
from typing import Protocol

import redis

from app.models import ClassroomOverlay, ClassroomSnapshot


class SnapshotCache(Protocol):
    def get_snapshot(self, classroom_id: str) -> ClassroomSnapshot | None: ...

    def set_snapshot(self, snapshot: ClassroomSnapshot, ttl_seconds: int) -> None: ...

    def delete_snapshot(self, classroom_id: str) -> None: ...

    def get_overlay(self, classroom_id: str) -> ClassroomOverlay | None: ...

    def set_overlay(self, overlay: ClassroomOverlay) -> None: ...

    def clear_overlay(self, classroom_id: str) -> None: ...

    def get_collector_snapshot(self, collector_ap_id: str) -> dict | None: ...

    def set_collector_snapshot(self, collector_ap_id: str, payload: dict, ttl_seconds: int) -> None: ...

    def remember_collector_nonce(self, collector_ap_id: str, nonce: str, ttl_seconds: int) -> bool: ...

    def acquire_refresh_lock(self, classroom_id: str, ttl_seconds: int) -> bool: ...

    def release_refresh_lock(self, classroom_id: str) -> None: ...

    def ping(self) -> bool: ...


class RedisSnapshotCache:
    def __init__(self, client: redis.Redis) -> None:
        self.client = client

    @staticmethod
    def snapshot_key(classroom_id: str) -> str:
        return f"presence:snapshot:classroom:{classroom_id}"

    @staticmethod
    def lock_key(classroom_id: str) -> str:
        return f"presence:lock:classroom:{classroom_id}"

    @staticmethod
    def overlay_key(classroom_id: str) -> str:
        return f"presence:overlay:classroom:{classroom_id}"

    @staticmethod
    def collector_snapshot_key(collector_ap_id: str) -> str:
        return f"presence:collector:ap:{collector_ap_id}:snapshot"

    @staticmethod
    def collector_nonce_key(collector_ap_id: str, nonce: str) -> str:
        return f"presence:collector:ap:{collector_ap_id}:nonce:{nonce}"

    def get_snapshot(self, classroom_id: str) -> ClassroomSnapshot | None:
        raw = self.client.get(self.snapshot_key(classroom_id))
        if raw is None:
            return None
        payload = json.loads(raw)
        return ClassroomSnapshot.model_validate(payload)

    def set_snapshot(self, snapshot: ClassroomSnapshot, ttl_seconds: int) -> None:
        payload = snapshot.model_dump(mode="json", by_alias=True)
        self.client.set(self.snapshot_key(snapshot.classroom_id), json.dumps(payload), ex=ttl_seconds)

    def delete_snapshot(self, classroom_id: str) -> None:
        self.client.delete(self.snapshot_key(classroom_id))

    def get_overlay(self, classroom_id: str) -> ClassroomOverlay | None:
        raw = self.client.get(self.overlay_key(classroom_id))
        if raw is None:
            return None
        payload = json.loads(raw)
        return ClassroomOverlay.model_validate(payload)

    def set_overlay(self, overlay: ClassroomOverlay) -> None:
        payload = overlay.model_dump(mode="json", by_alias=True)
        self.client.set(self.overlay_key(overlay.classroom_id), json.dumps(payload))

    def clear_overlay(self, classroom_id: str) -> None:
        self.client.delete(self.overlay_key(classroom_id))

    def get_collector_snapshot(self, collector_ap_id: str) -> dict | None:
        raw = self.client.get(self.collector_snapshot_key(collector_ap_id))
        if raw is None:
            return None
        return json.loads(raw)

    def set_collector_snapshot(self, collector_ap_id: str, payload: dict, ttl_seconds: int) -> None:
        self.client.set(self.collector_snapshot_key(collector_ap_id), json.dumps(payload), ex=ttl_seconds)

    def remember_collector_nonce(self, collector_ap_id: str, nonce: str, ttl_seconds: int) -> bool:
        return bool(self.client.set(self.collector_nonce_key(collector_ap_id, nonce), "1", nx=True, ex=ttl_seconds))

    def acquire_refresh_lock(self, classroom_id: str, ttl_seconds: int) -> bool:
        return bool(self.client.set(self.lock_key(classroom_id), "1", nx=True, ex=ttl_seconds))

    def release_refresh_lock(self, classroom_id: str) -> None:
        self.client.delete(self.lock_key(classroom_id))

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False
