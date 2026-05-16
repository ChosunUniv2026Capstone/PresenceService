from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.dummy_openwrt import DummySnapshotProvider
from app.main import create_app
from app.models import ClassroomNetworkThreshold, CollectorSnapshotRequest, DummyOverlayMutationRequest, EligibilityRequest
from app.registry import StaticRegistryClient
from app.service import PresenceService


class InMemoryCache:
    def __init__(self) -> None:
        self.snapshots = {}
        self.overlays = {}
        self.locks = set()
        self.operations: list[str] = []
        self.collector_snapshots = {}
        self.collector_nonces = set()

    def get_snapshot(self, classroom_id: str):
        self.operations.append(f"get_snapshot:{classroom_id}")
        return self.snapshots.get(classroom_id)

    def set_snapshot(self, snapshot, ttl_seconds: int) -> None:
        self.operations.append(f"set_snapshot:{snapshot.classroom_id}")
        self.snapshots[snapshot.classroom_id] = snapshot

    def delete_snapshot(self, classroom_id: str) -> None:
        self.operations.append(f"delete_snapshot:{classroom_id}")
        self.snapshots.pop(classroom_id, None)

    def get_overlay(self, classroom_id: str):
        self.operations.append(f"get_overlay:{classroom_id}")
        return self.overlays.get(classroom_id)

    def set_overlay(self, overlay) -> None:
        self.operations.append(f"set_overlay:{overlay.classroom_id}")
        self.overlays[overlay.classroom_id] = overlay

    def clear_overlay(self, classroom_id: str) -> None:
        self.operations.append(f"clear_overlay:{classroom_id}")
        self.overlays.pop(classroom_id, None)

    def get_collector_snapshot(self, collector_ap_id: str):
        self.operations.append(f"get_collector_snapshot:{collector_ap_id}")
        return self.collector_snapshots.get(collector_ap_id)

    def set_collector_snapshot(self, collector_ap_id: str, payload: dict, ttl_seconds: int) -> None:
        self.operations.append(f"set_collector_snapshot:{collector_ap_id}")
        self.collector_snapshots[collector_ap_id] = payload

    def remember_collector_nonce(self, collector_ap_id: str, nonce: str, ttl_seconds: int) -> bool:
        self.operations.append(f"remember_collector_nonce:{collector_ap_id}:{nonce}")
        key = (collector_ap_id, nonce)
        if key in self.collector_nonces:
            return False
        self.collector_nonces.add(key)
        return True

    def acquire_refresh_lock(self, classroom_id: str, ttl_seconds: int) -> bool:
        self.operations.append(f"acquire_lock:{classroom_id}")
        if classroom_id in self.locks:
            return False
        self.locks.add(classroom_id)
        return True

    def release_refresh_lock(self, classroom_id: str) -> None:
        self.operations.append(f"release_lock:{classroom_id}")
        self.locks.discard(classroom_id)

    def ping(self) -> bool:
        return True


def make_service() -> tuple[PresenceService, InMemoryCache]:
    fixture = Path(__file__).resolve().parents[1] / "app" / "dummy_data" / "classroom_snapshots.json"
    cache = InMemoryCache()
    return (
        PresenceService(
            cache=cache,
            provider=DummySnapshotProvider(fixture),
            snapshot_ttl_seconds=60,
            refresh_lock_seconds=10,
        ),
        cache,
    )


def make_client(service: PresenceService, monkeypatch) -> TestClient:
    from app import main as main_module

    main_module.get_presence_service.cache_clear()
    monkeypatch.setattr(main_module, "get_presence_service", lambda: service)
    return TestClient(create_app())


def eligibility_request(mac_address: str) -> EligibilityRequest:
    return EligibilityRequest(
        studentId="20201239",
        courseId="CSE116",
        classroomId="B101",
        purpose="attendance",
        classroomNetworks=[
            {"apId": "phy0-ap0", "ssid": "CU-B101-5G-1", "signalThresholdDbm": -65},
            {"apId": "phy1-ap0", "ssid": "CU-B101-2G-1", "signalThresholdDbm": -65},
            {"apId": "phy2-ap0", "ssid": "CU-B101-5G-2", "signalThresholdDbm": -65},
            {"apId": "phy3-ap0", "ssid": "CU-B101-2G-2", "signalThresholdDbm": -65},
        ],
        registeredDevices=[{"mac": mac_address, "label": "Choi Phone"}],
    )


def mutate_demo_device(service: PresenceService, mutate: Callable[[], None]) -> None:
    response = service.evaluate_eligibility(eligibility_request("52:54:00:12:34:56"))
    assert response.eligible is True
    mutate()


def test_eligibility_matches_registered_device() -> None:
    service, _ = make_service()
    response = service.evaluate_eligibility(eligibility_request("36:68:99:4f:01:db"))
    assert response.eligible is True
    assert response.reason_code == "OK"
    assert response.matched_device_mac == "36:68:99:4f:01:db"


def test_cached_snapshot_is_reused() -> None:
    service, _ = make_service()
    snapshot, cache_hit = service.get_or_refresh_snapshot("B102")
    assert cache_hit is False
    cached_snapshot, cache_hit = service.get_or_refresh_snapshot("B102")
    assert cache_hit is True
    assert cached_snapshot.classroom_id == snapshot.classroom_id


def test_overlay_mutation_flips_eligibility_without_changing_matching_formula() -> None:
    service, _ = make_service()

    mutate_demo_device(
        service,
        lambda: service.apply_overlay(
            "B101",
            DummyOverlayMutationRequest(
                stations=[
                    {
                        "macAddress": "52:54:00:12:34:56",
                        "apId": "phy3-ap0",
                        "present": True,
                        "associated": False,
                    }
                ]
            ),
        ),
    )

    response = service.evaluate_eligibility(eligibility_request("52:54:00:12:34:56"))
    assert response.eligible is False
    assert response.reason_code == "NETWORK_NOT_ELIGIBLE"
    assert response.evidence.cache_hit is True


def test_reset_overlay_restores_baseline_snapshot() -> None:
    service, _ = make_service()
    service.apply_overlay(
        "B101",
        DummyOverlayMutationRequest(
            stations=[
                {
                    "macAddress": "52:54:00:12:34:56",
                    "apId": "phy0-ap0",
                    "present": True,
                    "associated": False,
                }
            ]
        ),
    )

    reset_response = service.reset_overlay("B101")
    assert reset_response.overlay_active is False

    response = service.evaluate_eligibility(eligibility_request("52:54:00:12:34:56"))
    assert response.eligible is True
    assert response.reason_code == "OK"


def test_signal_threshold_blocks_weak_connected_device() -> None:
    service, _ = make_service()
    request = eligibility_request("52:54:00:12:34:56")
    request.classroom_networks = [
        ClassroomNetworkThreshold(apId="phy3-ap0", ssid="CU-B101-2G-2", signalThresholdDbm=-40),
    ]
    response = service.evaluate_eligibility(request)
    assert response.eligible is False
    assert response.reason_code == "NETWORK_NOT_ELIGIBLE"
    assert response.evidence.signal_threshold_dbm == -40


def test_missing_threshold_uses_minus_65_fallback() -> None:
    service, _ = make_service()
    request = eligibility_request("52:54:00:12:34:56")
    request.classroom_networks = [
        ClassroomNetworkThreshold(apId="phy3-ap0", ssid="CU-B101-2G-2", signalThresholdDbm=None),
    ]
    response = service.evaluate_eligibility(request)
    assert response.eligible is True
    assert response.evidence.signal_threshold_dbm == -65


def test_overlay_mutation_follows_lock_write_evict_prewarm_sequence() -> None:
    service, cache = make_service()
    cache.operations.clear()

    service.apply_overlay(
        "B101",
        DummyOverlayMutationRequest(
            stations=[
                {
                    "macAddress": "52:54:00:12:34:56",
                    "apId": "phy3-ap0",
                    "present": False,
                }
            ]
        ),
    )

    assert cache.operations == [
        "acquire_lock:B101",
        "get_overlay:B101",
        "set_overlay:B101",
        "delete_snapshot:B101",
        "set_snapshot:B101",
        "release_lock:B101",
    ]


def test_admin_overlay_endpoints_return_effective_snapshot_immediately(monkeypatch) -> None:
    service, _ = make_service()
    client = make_client(service, monkeypatch)

    baseline_response = client.get("/admin/dummy/classrooms/B101/snapshot")
    assert baseline_response.status_code == 200
    assert baseline_response.json()["overlayActive"] is False

    overlay_response = client.post(
        "/admin/dummy/classrooms/B101/overlay",
        json={
            "stations": [
                {
                    "macAddress": "52:54:00:12:34:56",
                    "apId": "phy3-ap0",
                    "present": False,
                }
            ]
        },
    )
    assert overlay_response.status_code == 200
    assert overlay_response.json()["overlayActive"] is True

    eligibility_response = client.post(
        "/eligibility/check",
        json=eligibility_request("52:54:00:12:34:56").model_dump(mode="json", by_alias=True),
    )
    assert eligibility_response.status_code == 200
    assert eligibility_response.json()["eligible"] is False
    assert eligibility_response.json()["reasonCode"] == "DEVICE_NOT_PRESENT"

    reset_response = client.post("/admin/dummy/classrooms/B101/overlay/reset")
    assert reset_response.status_code == 200
    assert reset_response.json()["overlayActive"] is False

    restored_response = client.post(
        "/eligibility/check",
        json=eligibility_request("52:54:00:12:34:56").model_dump(mode="json", by_alias=True),
    )
    assert restored_response.status_code == 200
    assert restored_response.json()["eligible"] is True


def test_collector_push_accepts_valid_token_and_updates_ap_snapshot() -> None:
    service, cache = make_service()
    token = "demo-token"
    service.ap_token_hash_secret = "test-pepper"
    service.registry_client = StaticRegistryClient({
        "accessPoints": [
            {
                "collectorApId": "openwrt-a",
                "status": "active",
                "tokenHash": service._hash_ap_token(token),
                "tokenVersion": 1,
                "interfaces": [
                    {
                        "interfaceId": "phy0-ap0",
                        "classroomId": "B101",
                        "classroomNetworkApId": "phy0-ap0",
                        "ssid": "CU-B101-5G-1",
                        "signalThresholdDbm": -65,
                    }
                ],
            }
        ]
    })
    request = CollectorSnapshotRequest(
        collectorApId="openwrt-a",
        observedAt=datetime.now(UTC).isoformat(),
        diagnosticClassroomId="B101",
        interfaces=[
            {
                "interfaceId": "phy0-ap0",
                "ssid": "CU-B101-5G-1",
                "stations": [{"mac": "52:54:00:12:34:56", "signalDbm": -44}],
            }
        ],
    )
    response = service.ingest_collector_snapshot(
        collector_ap_id="openwrt-a",
        authorization=f"Bearer {token}",
        request=request,
        nonce="n1",
        timestamp_header=datetime.now(UTC).isoformat(),
    )
    assert response.accepted is True
    assert cache.collector_snapshots["openwrt-a"]["interfaces"][0]["classroomId"] == "B101"

    eligibility = service.evaluate_eligibility(eligibility_request("52:54:00:12:34:56"))
    assert eligibility.eligible is True
    assert eligibility.reason_code == "OK"
    assert eligibility.evidence.matched_ap_ids == ["phy0-ap0"]


def test_admin_snapshot_prefers_collector_snapshot_from_redis() -> None:
    service, cache = make_service()
    service.registry_client = StaticRegistryClient({
        "accessPoints": [
            {
                "collectorApId": "openwrt-a",
                "status": "active",
                "tokenHash": "hash",
                "tokenVersion": 1,
                "interfaces": [
                    {
                        "interfaceId": "phy0-ap0",
                        "classroomId": "B101",
                        "classroomNetworkApId": "phy0-ap0",
                        "ssid": "CU-B101-5G-1",
                        "signalThresholdDbm": -65,
                    }
                ],
            }
        ]
    })
    cache.collector_snapshots["openwrt-a"] = {
        "collectorApId": "openwrt-a",
        "observedAt": datetime.now(UTC).isoformat(),
        "interfaces": [
            {
                "interfaceId": "phy0-ap0",
                "classroomId": "B101",
                "apId": "phy0-ap0",
                "ssid": "CU-B101-5G-1",
                "stations": [
                    {
                        "macAddress": "52:54:00:12:34:56",
                        "signalDbm": -44,
                        "connectedSeconds": 12,
                        "rxBytes": 100,
                        "txBytes": 200,
                    }
                ],
            }
        ],
    }

    snapshot = service.get_admin_snapshot("B101")

    assert snapshot.overlay_active is False
    assert snapshot.snapshot.collection_mode == "openwrt-push"
    assert snapshot.snapshot.aps[0].ap_id == "phy0-ap0"
    assert snapshot.snapshot.aps[0].stations[0].mac_address == "52:54:00:12:34:56"


def test_admin_demo_snapshot_bypasses_collector_snapshot() -> None:
    service, cache = make_service()
    service.registry_client = StaticRegistryClient({
        "accessPoints": [
            {
                "collectorApId": "openwrt-a",
                "status": "active",
                "tokenHash": "hash",
                "tokenVersion": 1,
                "interfaces": [
                    {
                        "interfaceId": "phy0-ap0",
                        "classroomId": "B101",
                        "classroomNetworkApId": "phy0-ap0",
                        "ssid": "CU-B101-5G-1",
                    }
                ],
            }
        ]
    })
    cache.collector_snapshots["openwrt-a"] = {
        "collectorApId": "openwrt-a",
        "observedAt": datetime.now(UTC).isoformat(),
        "interfaces": [
            {
                "interfaceId": "phy0-ap0",
                "classroomId": "B101",
                "apId": "phy0-ap0",
                "ssid": "CU-B101-5G-1",
                "stations": [
                    {
                        "macAddress": "52:54:00:12:34:56",
                        "signalDbm": -44,
                        "connectedSeconds": 12,
                        "rxBytes": 100,
                        "txBytes": 200,
                    }
                ],
            }
        ],
    }

    snapshot = service.get_admin_snapshot("B101", source="demo")

    assert snapshot.snapshot.collection_mode == "dummy-openwrt"
    assert snapshot.snapshot.aps[0].source_command == "iw dev phy0-ap0 station dump"


def test_admin_snapshot_force_refresh_evicts_dummy_classroom_cache() -> None:
    service, cache = make_service()

    service.get_admin_snapshot("B101")
    cache.operations.clear()

    service.get_admin_snapshot("B101", force_refresh=True)

    assert "delete_snapshot:B101" in cache.operations


def test_collector_push_rejects_invalid_token_without_state_update() -> None:
    service, cache = make_service()
    service.ap_token_hash_secret = "test-pepper"
    service.registry_client = StaticRegistryClient({
        "accessPoints": [{"collectorApId": "openwrt-a", "status": "active", "tokenHash": service._hash_ap_token("good"), "tokenVersion": 1, "interfaces": [{"interfaceId": "phy0-ap0", "classroomId": "B101", "classroomNetworkApId": "phy0-ap0", "ssid": "CU-B101-5G-1"}]}]
    })
    request = CollectorSnapshotRequest(collectorApId="openwrt-a", observedAt=datetime.now(UTC).isoformat(), interfaces=[{"interfaceId": "phy0-ap0", "stations": []}])
    try:
        service.ingest_collector_snapshot(collector_ap_id="openwrt-a", authorization="Bearer bad", request=request, nonce="n1", timestamp_header=datetime.now(UTC).isoformat())
    except PermissionError as exc:
        assert str(exc) == "COLLECTOR_TOKEN_INVALID"
    else:
        raise AssertionError("expected invalid token")
    assert cache.collector_snapshots == {}


def test_classroom_eligibility_returns_ap_offline_when_zero_online_aps() -> None:
    service, _ = make_service()
    service.registry_client = StaticRegistryClient({
        "accessPoints": [{"collectorApId": "openwrt-a", "status": "active", "tokenHash": "hash", "tokenVersion": 1, "interfaces": [{"interfaceId": "phy0-ap0", "classroomId": "B101", "classroomNetworkApId": "phy0-ap0", "ssid": "CU-B101-5G-1"}]}]
    })
    response = service.evaluate_eligibility(eligibility_request("52:54:00:12:34:56"))
    assert response.eligible is False
    assert response.reason_code == "AP_OFFLINE"
