from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
from time import sleep

from app.cache import SnapshotCache
from app.dummy_openwrt import DummySnapshotProvider
from app.models import (
    AccessPointSnapshot,
    AdminSnapshotEnvelope,
    ClassroomOverlay,
    ClassroomNetworkThreshold,
    ClassroomSnapshot,
    CollectorIngestResponse,
    CollectorSnapshotRequest,
    DummyOverlayMutationRequest,
    DummyOverlayStation,
    EligibilityEvidence,
    EligibilityRequest,
    EligibilityResponse,
    StationObservation,
    normalize_mac,
)
from app.registry import BackendRegistryClient, RegistrySnapshot


@dataclass
class PresenceService:
    cache: SnapshotCache
    provider: DummySnapshotProvider
    snapshot_ttl_seconds: int
    refresh_lock_seconds: int
    registry_client: BackendRegistryClient | None = None
    collector_offline_after_seconds: int = 10
    collector_timestamp_window_seconds: int = 60
    ap_token_hash_secret: str = "smart-class-dev-ap-token-pepper"
    registry_cache_ttl_seconds: int = 5


    def _hash_ap_token(self, token: str) -> str:
        return hashlib.sha256(f"{self.ap_token_hash_secret}:{token}".encode("utf-8")).hexdigest()

    def _registry(self) -> RegistrySnapshot | None:
        if self.registry_client is None:
            return None
        now = datetime.now(UTC)
        cached = getattr(self, "_registry_cache", None)
        expires_at = getattr(self, "_registry_cache_expires_at", None)
        if cached is not None and expires_at is not None and now < expires_at:
            return cached
        fresh = self.registry_client.fetch_registry()
        self._registry_cache = fresh
        self._registry_cache_expires_at = now + timedelta(seconds=self.registry_cache_ttl_seconds)
        return fresh

    @staticmethod
    def _bearer_token(authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise PermissionError("COLLECTOR_TOKEN_MISSING")
        token = authorization.removeprefix("Bearer ").strip()
        if not token:
            raise PermissionError("COLLECTOR_TOKEN_MISSING")
        return token

    def ingest_collector_snapshot(
        self,
        *,
        collector_ap_id: str,
        authorization: str | None,
        request: CollectorSnapshotRequest,
        nonce: str | None,
        timestamp_header: str | None,
    ) -> CollectorIngestResponse:
        if request.collector_ap_id != collector_ap_id:
            raise ValueError("COLLECTOR_AP_ID_MISMATCH")
        registry = self._registry()
        if registry is None:
            raise RuntimeError("COLLECTOR_REGISTRY_UNAVAILABLE")
        ap = registry.get_access_point(collector_ap_id)
        if ap is None or ap.status != "active":
            raise PermissionError("COLLECTOR_AP_UNKNOWN")
        if not ap.token_hash or ap.token_revoked_at:
            raise PermissionError("COLLECTOR_TOKEN_REVOKED")
        token = self._bearer_token(authorization)
        if self._hash_ap_token(token) != ap.token_hash:
            raise PermissionError("COLLECTOR_TOKEN_INVALID")

        timestamp = request.observed_at
        if timestamp_header:
            try:
                timestamp = datetime.fromisoformat(timestamp_header.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("COLLECTOR_TIMESTAMP_INVALID") from exc
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=UTC)
        age = abs((datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds())
        if age > self.collector_timestamp_window_seconds:
            raise ValueError("COLLECTOR_TIMESTAMP_STALE")

        if nonce and not self.cache.remember_collector_nonce(collector_ap_id, nonce, self.collector_timestamp_window_seconds):
            raise ValueError("COLLECTOR_NONCE_REPLAY")

        iface_registry = {interface.interface_id: interface for interface in ap.interfaces}
        accepted_interfaces = []
        for interface in request.interfaces:
            registry_interface = iface_registry.get(interface.interface_id)
            if registry_interface is None:
                continue
            if request.diagnostic_classroom_id and request.diagnostic_classroom_id != registry_interface.classroom_id:
                raise ValueError("COLLECTOR_CLASSROOM_MISMATCH")
            accepted_interfaces.append(
                {
                    "interfaceId": registry_interface.interface_id,
                    "classroomId": registry_interface.classroom_id,
                    "apId": registry_interface.classroom_network_ap_id,
                    "ssid": interface.ssid or registry_interface.ssid,
                    "bssid": interface.bssid or registry_interface.bssid,
                    "stations": [station.model_dump(mode="json", by_alias=True) for station in interface.stations],
                }
            )
        if not accepted_interfaces:
            raise ValueError("COLLECTOR_INTERFACE_NOT_MAPPED")

        payload = {
            "collectorApId": collector_ap_id,
            "observedAt": request.observed_at.astimezone(UTC).isoformat(),
            "interfaces": accepted_interfaces,
        }
        self.cache.set_collector_snapshot(collector_ap_id, payload, self.collector_offline_after_seconds)
        return CollectorIngestResponse(
            accepted=True,
            collectorApId=collector_ap_id,
            acceptedInterfaceCount=len(accepted_interfaces),
            stationCount=sum(len(interface["stations"]) for interface in accepted_interfaces),
            observedAt=request.observed_at,
        )

    def _collector_snapshot_for_classroom(self, classroom_id: str) -> tuple[ClassroomSnapshot | None, bool] | None:
        registry = self._registry()
        if registry is None:
            return None
        mappings = registry.classroom_mappings(classroom_id)
        if not mappings:
            return None
        aps: list[AccessPointSnapshot] = []
        observed_times: list[datetime] = []
        for ap, expected_interface in mappings:
            raw = self.cache.get_collector_snapshot(ap.collector_ap_id)
            if raw is None:
                continue
            observed_raw = raw.get("observedAt")
            try:
                observed_at = datetime.fromisoformat(str(observed_raw).replace("Z", "+00:00"))
            except ValueError:
                observed_at = datetime.now(UTC)
            observed_times.append(observed_at)
            for interface in raw.get("interfaces", []):
                if interface.get("interfaceId") != expected_interface.interface_id:
                    continue
                if interface.get("classroomId") != classroom_id:
                    continue
                aps.append(
                    AccessPointSnapshot(
                        apId=expected_interface.classroom_network_ap_id,
                        ssid=interface.get("ssid") or expected_interface.ssid,
                        sourceCommand="openwrt-collector:ubus hostapd.get_clients",
                        stations=[
                            StationObservation.model_validate(station)
                            for station in interface.get("stations", [])
                        ],
                    )
                )
        if not aps:
            return (None, True)
        return (
            ClassroomSnapshot(
                classroomId=classroom_id,
                observedAt=max(observed_times) if observed_times else datetime.now(UTC),
                collectionMode="openwrt-push",
                aps=aps,
            ),
            True,
        )

    def _ap_offline_response(self, request: EligibilityRequest) -> EligibilityResponse:
        return EligibilityResponse(
            eligible=False,
            reasonCode="AP_OFFLINE",
            matchedDeviceMac=None,
            observedAt=None,
            snapshotAgeSeconds=None,
            evidence=EligibilityEvidence(
                classroomId=request.classroom_id,
                matchedApIds=[],
                stationCount=0,
                signalDbm=None,
                signalThresholdDbm=None,
                associated=None,
                authenticated=None,
                authorized=None,
                cacheHit=True,
            ),
        )


    def collector_health(self) -> dict:
        registry = self._registry()
        if registry is None:
            return {"accessPoints": []}
        access_points = []
        for ap in registry.access_points:
            snapshot = self.cache.get_collector_snapshot(ap.collector_ap_id)
            classrooms = sorted({interface.classroom_id for interface in ap.interfaces})
            access_points.append(
                {
                    "collectorApId": ap.collector_ap_id,
                    "online": snapshot is not None,
                    "classroomIds": classrooms,
                    "observedAt": snapshot.get("observedAt") if snapshot else None,
                    "interfaceCount": len(snapshot.get("interfaces", [])) if snapshot else 0,
                    "stationCount": sum(len(interface.get("stations", [])) for interface in snapshot.get("interfaces", [])) if snapshot else 0,
                }
            )
        return {"accessPoints": access_points}

    def get_or_refresh_snapshot(self, classroom_id: str) -> tuple[ClassroomSnapshot, bool]:
        cached = self.cache.get_snapshot(classroom_id)
        if cached is not None:
            return cached, True

        if self.cache.acquire_refresh_lock(classroom_id, self.refresh_lock_seconds):
            try:
                snapshot = self.compose_effective_snapshot(classroom_id)
                self.cache.set_snapshot(snapshot, self.snapshot_ttl_seconds)
                return snapshot, False
            finally:
                self.cache.release_refresh_lock(classroom_id)

        for _ in range(5):
            sleep(0.1)
            cached = self.cache.get_snapshot(classroom_id)
            if cached is not None:
                return cached, True

        snapshot = self.compose_effective_snapshot(classroom_id)
        self.cache.set_snapshot(snapshot, self.snapshot_ttl_seconds)
        return snapshot, False

    def get_admin_snapshot(self, classroom_id: str, *, force_refresh: bool = False) -> AdminSnapshotEnvelope:
        collector_snapshot = self._collector_snapshot_for_classroom(classroom_id)
        if collector_snapshot is not None:
            snapshot, cache_hit = collector_snapshot
            if snapshot is None:
                snapshot = ClassroomSnapshot(
                    classroomId=classroom_id,
                    observedAt=datetime.now(UTC),
                    collectionMode="openwrt-push",
                    aps=[],
                )
            return AdminSnapshotEnvelope(
                cacheHit=cache_hit,
                overlayActive=False,
                snapshot=snapshot,
            )

        if force_refresh:
            self.cache.delete_snapshot(classroom_id)
        snapshot, cache_hit = self.get_or_refresh_snapshot(classroom_id)
        return AdminSnapshotEnvelope(
            cacheHit=cache_hit,
            overlayActive=self.has_overlay(classroom_id),
            snapshot=snapshot,
        )

    def apply_overlay(self, classroom_id: str, request: DummyOverlayMutationRequest) -> AdminSnapshotEnvelope:
        if not self.cache.acquire_refresh_lock(classroom_id, self.refresh_lock_seconds):
            raise TimeoutError("SNAPSHOT_REFRESH_BUSY")

        try:
            baseline = self.provider.fetch_snapshot(classroom_id)
            existing_overlay = self.cache.get_overlay(classroom_id)
            next_overlay = self.build_updated_overlay(
                classroom_id=classroom_id,
                baseline=baseline,
                existing_overlay=existing_overlay,
                request=request,
            )
            self.persist_overlay(classroom_id, next_overlay)
            self.cache.delete_snapshot(classroom_id)
            snapshot = self.compose_effective_snapshot(
                classroom_id,
                baseline=baseline,
                overlay=next_overlay,
            )
            self.cache.set_snapshot(snapshot, self.snapshot_ttl_seconds)
            return AdminSnapshotEnvelope(
                cacheHit=False,
                overlayActive=bool(next_overlay.stations),
                snapshot=snapshot,
            )
        finally:
            self.cache.release_refresh_lock(classroom_id)

    def reset_overlay(self, classroom_id: str) -> AdminSnapshotEnvelope:
        if not self.cache.acquire_refresh_lock(classroom_id, self.refresh_lock_seconds):
            raise TimeoutError("SNAPSHOT_REFRESH_BUSY")

        try:
            baseline = self.provider.fetch_snapshot(classroom_id)
            self.cache.clear_overlay(classroom_id)
            self.cache.delete_snapshot(classroom_id)
            self.cache.set_snapshot(baseline, self.snapshot_ttl_seconds)
            return AdminSnapshotEnvelope(
                cacheHit=False,
                overlayActive=False,
                snapshot=baseline,
            )
        finally:
            self.cache.release_refresh_lock(classroom_id)

    def compose_effective_snapshot(
        self,
        classroom_id: str,
        baseline: ClassroomSnapshot | None = None,
        overlay: ClassroomOverlay | None = None,
    ) -> ClassroomSnapshot:
        baseline_snapshot = baseline or self.provider.fetch_snapshot(classroom_id)
        overlay_state = overlay if overlay is not None else self.cache.get_overlay(classroom_id)
        if overlay_state is None or not overlay_state.stations:
            return baseline_snapshot
        return self.merge_snapshot(baseline_snapshot, overlay_state)

    def build_updated_overlay(
        self,
        classroom_id: str,
        baseline: ClassroomSnapshot,
        existing_overlay: ClassroomOverlay | None,
        request: DummyOverlayMutationRequest,
    ) -> ClassroomOverlay:
        existing_station_map = {
            station.mac_address: station
            for station in (existing_overlay.stations if existing_overlay is not None else [])
        }
        baseline_station_map, _ = self.index_snapshot(baseline)

        for station_update in request.stations:
            existing_station = existing_station_map.get(station_update.mac_address)
            baseline_station, baseline_ap_id = baseline_station_map.get(station_update.mac_address, (None, None))
            target_ap_id = station_update.ap_id or (
                existing_station.ap_id if existing_station is not None else baseline_ap_id
            )

            if station_update.present and target_ap_id is None:
                raise ValueError("CLASSROOM_AP_NOT_MAPPED")
            if station_update.present and target_ap_id not in {ap.ap_id for ap in baseline.aps}:
                raise ValueError("CLASSROOM_AP_NOT_MAPPED")

            existing_station_map[station_update.mac_address] = DummyOverlayStation(
                macAddress=station_update.mac_address,
                apId=target_ap_id,
                present=station_update.present,
                authorized=self.resolve_overlay_field(
                    station_update.authorized,
                    existing_station.authorized if existing_station is not None else None,
                    baseline_station.authorized if baseline_station is not None else True,
                ),
                authenticated=self.resolve_overlay_field(
                    station_update.authenticated,
                    existing_station.authenticated if existing_station is not None else None,
                    baseline_station.authenticated if baseline_station is not None else True,
                ),
                associated=self.resolve_overlay_field(
                    station_update.associated,
                    existing_station.associated if existing_station is not None else None,
                    baseline_station.associated if baseline_station is not None else True,
                ),
                signalDbm=self.resolve_overlay_field(
                    station_update.signal_dbm,
                    existing_station.signal_dbm if existing_station is not None else None,
                    baseline_station.signal_dbm if baseline_station is not None else -50,
                ),
                connectedSeconds=self.resolve_overlay_field(
                    station_update.connected_seconds,
                    existing_station.connected_seconds if existing_station is not None else None,
                    baseline_station.connected_seconds if baseline_station is not None else 0,
                ),
                rxBytes=self.resolve_overlay_field(
                    station_update.rx_bytes,
                    existing_station.rx_bytes if existing_station is not None else None,
                    baseline_station.rx_bytes if baseline_station is not None else 0,
                ),
                txBytes=self.resolve_overlay_field(
                    station_update.tx_bytes,
                    existing_station.tx_bytes if existing_station is not None else None,
                    baseline_station.tx_bytes if baseline_station is not None else 0,
                ),
            )

        return ClassroomOverlay(
            classroomId=classroom_id,
            stations=sorted(existing_station_map.values(), key=lambda station: station.mac_address),
        )

    @staticmethod
    def resolve_overlay_field(
        requested_value: bool | int | None,
        existing_value: bool | int | None,
        default_value: bool | int,
    ) -> bool | int:
        if requested_value is not None:
            return requested_value
        if existing_value is not None:
            return existing_value
        return default_value

    def persist_overlay(self, classroom_id: str, overlay: ClassroomOverlay) -> None:
        if overlay.stations:
            self.cache.set_overlay(overlay)
        else:
            self.cache.clear_overlay(classroom_id)

    def has_overlay(self, classroom_id: str) -> bool:
        overlay = self.cache.get_overlay(classroom_id)
        return overlay is not None and bool(overlay.stations)

    def merge_snapshot(self, baseline: ClassroomSnapshot, overlay: ClassroomOverlay) -> ClassroomSnapshot:
        baseline_station_map, ap_map = self.index_snapshot(baseline)
        ap_metadata = {
            ap.ap_id: (ap.ssid, ap.source_command)
            for ap in baseline.aps
        }

        for overlay_station in overlay.stations:
            for stations in ap_map.values():
                stations[:] = [station for station in stations if station.mac_address != overlay_station.mac_address]

            if not overlay_station.present:
                continue

            if overlay_station.ap_id is None or overlay_station.ap_id not in ap_map:
                raise ValueError("CLASSROOM_AP_NOT_MAPPED")

            baseline_station, _ = baseline_station_map.get(overlay_station.mac_address, (None, None))
            ap_map[overlay_station.ap_id].append(
                self.materialize_station(
                    overlay_station=overlay_station,
                    baseline_station=baseline_station,
                )
            )

        aps = []
        for ap in baseline.aps:
            ssid, source_command = ap_metadata[ap.ap_id]
            aps.append(
                AccessPointSnapshot(
                    apId=ap.ap_id,
                    ssid=ssid,
                    sourceCommand=source_command,
                    stations=ap_map[ap.ap_id],
                )
            )

        return ClassroomSnapshot(
            classroomId=baseline.classroom_id,
            observedAt=baseline.observed_at,
            collectionMode=baseline.collection_mode,
            aps=aps,
        )

    @staticmethod
    def materialize_station(
        overlay_station: DummyOverlayStation,
        baseline_station: StationObservation | None,
    ) -> StationObservation:
        return StationObservation(
            macAddress=overlay_station.mac_address,
            authorized=overlay_station.authorized if overlay_station.authorized is not None else (
                baseline_station.authorized if baseline_station is not None else True
            ),
            authenticated=overlay_station.authenticated if overlay_station.authenticated is not None else (
                baseline_station.authenticated if baseline_station is not None else True
            ),
            associated=overlay_station.associated if overlay_station.associated is not None else (
                baseline_station.associated if baseline_station is not None else True
            ),
            signalDbm=overlay_station.signal_dbm if overlay_station.signal_dbm is not None else (
                baseline_station.signal_dbm if baseline_station is not None else -50
            ),
            connectedSeconds=overlay_station.connected_seconds if overlay_station.connected_seconds is not None else (
                baseline_station.connected_seconds if baseline_station is not None else 0
            ),
            rxBytes=overlay_station.rx_bytes if overlay_station.rx_bytes is not None else (
                baseline_station.rx_bytes if baseline_station is not None else 0
            ),
            txBytes=overlay_station.tx_bytes if overlay_station.tx_bytes is not None else (
                baseline_station.tx_bytes if baseline_station is not None else 0
            ),
        )

    @staticmethod
    def index_snapshot(
        snapshot: ClassroomSnapshot,
    ) -> tuple[dict[str, tuple[StationObservation, str]], dict[str, list[StationObservation]]]:
        station_map: dict[str, tuple[StationObservation, str]] = {}
        ap_map: dict[str, list[StationObservation]] = {}
        for ap in snapshot.aps:
            ap_map[ap.ap_id] = list(ap.stations)
            for station in ap.stations:
                station_map[station.mac_address] = (station, ap.ap_id)
        return station_map, ap_map

    def evaluate_eligibility(self, request: EligibilityRequest) -> EligibilityResponse:
        if not request.registered_devices:
            raise ValueError("DEVICE_NOT_REGISTERED")

        collector_snapshot = self._collector_snapshot_for_classroom(request.classroom_id)
        if collector_snapshot is not None:
            snapshot, cache_hit = collector_snapshot
            if snapshot is None:
                return self._ap_offline_response(request)
        else:
            try:
                snapshot, cache_hit = self.get_or_refresh_snapshot(request.classroom_id)
            except KeyError as exc:
                raise LookupError("CLASSROOM_NOT_MAPPED") from exc

        matched_station: StationObservation | None = None
        matched_ap_ids: list[str] = []
        matched_threshold: int | None = None
        saw_matching_device = False
        strongest_seen_station: StationObservation | None = None
        strongest_seen_ap_id: str | None = None
        device_macs = {normalize_mac(device.mac_address) for device in request.registered_devices}
        threshold_by_ap = {
            network.ap_id: self.resolve_signal_threshold(network)
            for network in request.classroom_networks
        }

        for ap in snapshot.aps:
            for station in ap.stations:
                if station.mac_address not in device_macs:
                    continue
                saw_matching_device = True
                if strongest_seen_station is None or station.signal_dbm > strongest_seen_station.signal_dbm:
                    strongest_seen_station = station
                    strongest_seen_ap_id = ap.ap_id
                threshold = threshold_by_ap.get(ap.ap_id, -65)
                if station.associated and station.signal_dbm >= threshold:
                    matched_station = station
                    matched_ap_ids.append(ap.ap_id)
                    matched_threshold = threshold
                    break
            if matched_station:
                break

        age_seconds = max(0, int((datetime.now(UTC) - snapshot.observed_at).total_seconds()))

        if matched_station is None and not saw_matching_device:
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
                    signalThresholdDbm=None,
                    associated=None,
                    authenticated=None,
                    authorized=None,
                    cacheHit=cache_hit,
                ),
            )

        if matched_station is None:
            fallback_threshold = threshold_by_ap.get(strongest_seen_ap_id, -65 if strongest_seen_ap_id else None)
            return EligibilityResponse(
                eligible=False,
                reasonCode="NETWORK_NOT_ELIGIBLE",
                matchedDeviceMac=strongest_seen_station.mac_address if strongest_seen_station else None,
                observedAt=snapshot.observed_at,
                snapshotAgeSeconds=age_seconds,
                evidence=EligibilityEvidence(
                    classroomId=request.classroom_id,
                    matchedApIds=[strongest_seen_ap_id] if strongest_seen_ap_id else [],
                    stationCount=sum(len(ap.stations) for ap in snapshot.aps),
                    signalDbm=strongest_seen_station.signal_dbm if strongest_seen_station else None,
                    signalThresholdDbm=fallback_threshold,
                    associated=strongest_seen_station.associated if strongest_seen_station else None,
                    authenticated=strongest_seen_station.authenticated if strongest_seen_station else None,
                    authorized=strongest_seen_station.authorized if strongest_seen_station else None,
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
                signalThresholdDbm=matched_threshold,
                associated=matched_station.associated,
                authenticated=matched_station.authenticated,
                authorized=matched_station.authorized,
                cacheHit=cache_hit,
            ),
        )

    @staticmethod
    def resolve_signal_threshold(network: ClassroomNetworkThreshold) -> int:
        return network.signal_threshold_dbm if network.signal_threshold_dbm is not None else -65
