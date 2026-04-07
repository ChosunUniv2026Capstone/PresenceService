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

    def acquire_refresh_lock(self, classroom_id: str, ttl_seconds: int) -> bool:
        return bool(self.client.set(self.lock_key(classroom_id), "1", nx=True, ex=ttl_seconds))

    def release_refresh_lock(self, classroom_id: str) -> None:
        self.client.delete(self.lock_key(classroom_id))

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except redis.RedisError:
            return False
