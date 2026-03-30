from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep

from app.cache import SnapshotCache
from app.dummy_openwrt import DummySnapshotProvider
from app.models import (
    ClassroomSnapshot,
    EligibilityEvidence,
    EligibilityRequest,
    EligibilityResponse,
    StationObservation,
    normalize_mac,
)


@dataclass
class PresenceService:
    cache: SnapshotCache
    provider: DummySnapshotProvider
    snapshot_ttl_seconds: int
    refresh_lock_seconds: int

    def get_or_refresh_snapshot(self, classroom_id: str) -> tuple[ClassroomSnapshot, bool]:
        cached = self.cache.get_snapshot(classroom_id)
        if cached is not None:
            return cached, True

        if self.cache.acquire_refresh_lock(classroom_id, self.refresh_lock_seconds):
            try:
                snapshot = self.provider.fetch_snapshot(classroom_id)
                self.cache.set_snapshot(snapshot, self.snapshot_ttl_seconds)
                return snapshot, False
            finally:
                self.cache.release_refresh_lock(classroom_id)

        for _ in range(5):
            sleep(0.1)
            cached = self.cache.get_snapshot(classroom_id)
            if cached is not None:
                return cached, True

        snapshot = self.provider.fetch_snapshot(classroom_id)
        self.cache.set_snapshot(snapshot, self.snapshot_ttl_seconds)
        return snapshot, False

    def evaluate_eligibility(self, request: EligibilityRequest) -> EligibilityResponse:
        if not request.registered_devices:
            raise ValueError("DEVICE_NOT_REGISTERED")

        try:
            snapshot, cache_hit = self.get_or_refresh_snapshot(request.classroom_id)
        except KeyError as exc:
            raise LookupError("CLASSROOM_NOT_MAPPED") from exc

        matched_station: StationObservation | None = None
        matched_ap_ids: list[str] = []
        device_macs = {normalize_mac(device.mac_address) for device in request.registered_devices}

        for ap in snapshot.aps:
            for station in ap.stations:
                if station.mac_address in device_macs and station.associated:
                    matched_station = station
                    matched_ap_ids.append(ap.ap_id)
                    break
            if matched_station:
                break

        age_seconds = max(0, int((datetime.now(UTC) - snapshot.observed_at).total_seconds()))

        if matched_station is None:
            return EligibilityResponse(
                eligible=False,
                reasonCode="DEVICE_NOT_PRESENT",
                matchedDeviceMac=None,
                observedAt=snapshot.observed_at,
                snapshotAgeSeconds=age_seconds,
                evidence=EligibilityEvidence(
                    classroomId=request.classroom_id,
                    matchedApIds=[],
                    stationCount=sum(len(ap.stations) for ap in snapshot.aps),
                    signalDbm=None,
                    associated=None,
                    authenticated=None,
                    authorized=None,
                    cacheHit=cache_hit,
                ),
            )

        return EligibilityResponse(
            eligible=True,
            reasonCode="OK",
            matchedDeviceMac=matched_station.mac_address,
            observedAt=snapshot.observed_at,
            snapshotAgeSeconds=age_seconds,
            evidence=EligibilityEvidence(
                classroomId=request.classroom_id,
                matchedApIds=matched_ap_ids,
                stationCount=sum(len(ap.stations) for ap in snapshot.aps),
                signalDbm=matched_station.signal_dbm,
                associated=matched_station.associated,
                authenticated=matched_station.authenticated,
                authorized=matched_station.authorized,
                cacheHit=cache_hit,
            ),
        )
