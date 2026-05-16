from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx


@dataclass(frozen=True)
class RegistryInterface:
    interface_id: str
    classroom_id: str
    classroom_network_ap_id: str
    ssid: str
    signal_threshold_dbm: int | None = None
    bssid: str | None = None


@dataclass(frozen=True)
class RegistryAccessPoint:
    collector_ap_id: str
    status: str
    token_hash: str | None
    token_revoked_at: str | None
    token_version: int
    interfaces: tuple[RegistryInterface, ...]


@dataclass(frozen=True)
class RegistrySnapshot:
    access_points: tuple[RegistryAccessPoint, ...]

    def get_access_point(self, collector_ap_id: str) -> RegistryAccessPoint | None:
        return next((ap for ap in self.access_points if ap.collector_ap_id == collector_ap_id), None)

    def classroom_mappings(self, classroom_id: str) -> list[tuple[RegistryAccessPoint, RegistryInterface]]:
        mappings: list[tuple[RegistryAccessPoint, RegistryInterface]] = []
        for ap in self.access_points:
            for interface in ap.interfaces:
                if interface.classroom_id == classroom_id:
                    mappings.append((ap, interface))
        return mappings


class BackendRegistryClient:
    def __init__(self, base_url: str, internal_token: str, timeout_seconds: float = 5.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.internal_token = internal_token
        self.timeout_seconds = timeout_seconds

    def fetch_registry(self) -> RegistrySnapshot:
        response = httpx.get(
            f"{self.base_url}/api/internal/presence/ap-registry",
            headers={"X-Internal-Token": self.internal_token},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return parse_registry(response.json())


class StaticRegistryClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def fetch_registry(self) -> RegistrySnapshot:
        return parse_registry(self.payload)


def parse_registry(payload: dict[str, Any]) -> RegistrySnapshot:
    access_points = []
    for raw_ap in payload.get("accessPoints", []):
        interfaces = []
        for raw_iface in raw_ap.get("interfaces", []):
            interfaces.append(
                RegistryInterface(
                    interface_id=str(raw_iface["interfaceId"]),
                    classroom_id=str(raw_iface["classroomId"]),
                    classroom_network_ap_id=str(raw_iface.get("classroomNetworkApId") or raw_iface["interfaceId"]),
                    ssid=str(raw_iface.get("ssid") or raw_iface.get("interfaceId") or ""),
                    signal_threshold_dbm=raw_iface.get("signalThresholdDbm"),
                    bssid=raw_iface.get("bssid"),
                )
            )
        access_points.append(
            RegistryAccessPoint(
                collector_ap_id=str(raw_ap["collectorApId"]),
                status=str(raw_ap.get("status") or "active"),
                token_hash=raw_ap.get("tokenHash"),
                token_revoked_at=raw_ap.get("tokenRevokedAt"),
                token_version=int(raw_ap.get("tokenVersion") or 0),
                interfaces=tuple(interfaces),
            )
        )
    return RegistrySnapshot(tuple(access_points))
