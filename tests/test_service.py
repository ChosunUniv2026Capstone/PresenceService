from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.dummy_openwrt import DummySnapshotProvider
from app.models import EligibilityRequest
from app.service import PresenceService


class InMemoryCache:
    def __init__(self) -> None:
        self.snapshots = {}
        self.locks = set()

    def get_snapshot(self, classroom_id: str):
        return self.snapshots.get(classroom_id)

    def set_snapshot(self, snapshot, ttl_seconds: int) -> None:
        self.snapshots[snapshot.classroom_id] = snapshot

    def acquire_refresh_lock(self, classroom_id: str, ttl_seconds: int) -> bool:
        if classroom_id in self.locks:
            return False
        self.locks.add(classroom_id)
        return True

    def release_refresh_lock(self, classroom_id: str) -> None:
        self.locks.discard(classroom_id)

    def ping(self) -> bool:
        return True


def make_service() -> PresenceService:
    fixture = Path(__file__).resolve().parents[1] / "app" / "dummy_data" / "classroom_snapshots.json"
    return PresenceService(
        cache=InMemoryCache(),
        provider=DummySnapshotProvider(fixture),
        snapshot_ttl_seconds=60,
        refresh_lock_seconds=10,
    )


def test_eligibility_matches_registered_device() -> None:
    service = make_service()
    response = service.evaluate_eligibility(
        EligibilityRequest(
            studentId="20201234",
            classroomId="B101",
            purpose="attendance",
            registeredDevices=[{"mac": "36:68:99:4f:01:db", "label": "iPhone"}],
        )
    )
    assert response.eligible is True
    assert response.reason_code == "OK"
    assert response.matched_device_mac == "36:68:99:4f:01:db"


def test_eligibility_rejects_when_device_missing() -> None:
    service = make_service()
    response = service.evaluate_eligibility(
        EligibilityRequest(
            studentId="20201234",
            classroomId="B101",
            purpose="attendance",
            registeredDevices=[{"mac": "ff:ff:ff:ff:ff:ff", "label": "Unknown"}],
        )
    )
    assert response.eligible is False
    assert response.reason_code == "DEVICE_NOT_PRESENT"


def test_cached_snapshot_is_reused() -> None:
    service = make_service()
    snapshot, cache_hit = service.get_or_refresh_snapshot("B102")
    assert cache_hit is False
    cached_snapshot, cache_hit = service.get_or_refresh_snapshot("B102")
    assert cache_hit is True
    assert cached_snapshot.classroom_id == snapshot.classroom_id
