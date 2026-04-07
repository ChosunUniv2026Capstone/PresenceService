from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator


def normalize_mac(value: str) -> str:
    return value.strip().lower()


class Purpose(StrEnum):
    ATTENDANCE = "attendance"
    EXAM = "exam"


class RegisteredDevice(BaseModel):
    device_id: str | None = Field(default=None, alias="deviceId")
    label: str | None = None
    mac_address: str = Field(alias="mac")

    @field_validator("mac_address")
    @classmethod
    def validate_mac(cls, value: str) -> str:
        normalized = normalize_mac(value)
        parts = normalized.split(":")
        if len(parts) != 6 or any(len(part) != 2 for part in parts):
            raise ValueError("MAC address must be in aa:bb:cc:dd:ee:ff format")
        return normalized


class StationObservation(BaseModel):
    mac_address: str = Field(alias="macAddress")
    authorized: bool = True
    authenticated: bool = True
    associated: bool = True
    signal_dbm: int = Field(alias="signalDbm")
    connected_seconds: int = Field(alias="connectedSeconds")
    rx_bytes: int = Field(alias="rxBytes")
    tx_bytes: int = Field(alias="txBytes")

    @field_validator("mac_address")
    @classmethod
    def normalize_station_mac(cls, value: str) -> str:
        return normalize_mac(value)


class AccessPointSnapshot(BaseModel):
    ap_id: str = Field(alias="apId")
    ssid: str
    source_command: str = Field(alias="sourceCommand")
    stations: list[StationObservation]


class ClassroomSnapshot(BaseModel):
    classroom_id: str = Field(alias="classroomId")
    observed_at: datetime = Field(alias="observedAt")
    collection_mode: str = Field(default="dummy-openwrt", alias="collectionMode")
    aps: list[AccessPointSnapshot]


class DummyOverlayStation(BaseModel):
    mac_address: str = Field(alias="macAddress")
    ap_id: str | None = Field(default=None, alias="apId")
    present: bool = True
    authorized: bool | None = None
    authenticated: bool | None = None
    associated: bool | None = None
    signal_dbm: int | None = Field(default=None, alias="signalDbm")
    connected_seconds: int | None = Field(default=None, alias="connectedSeconds")
    rx_bytes: int | None = Field(default=None, alias="rxBytes")
    tx_bytes: int | None = Field(default=None, alias="txBytes")

    @field_validator("mac_address")
    @classmethod
    def normalize_overlay_mac(cls, value: str) -> str:
        return normalize_mac(value)


class ClassroomOverlay(BaseModel):
    classroom_id: str = Field(alias="classroomId")
    stations: list[DummyOverlayStation] = Field(default_factory=list)


class DummyOverlayMutationRequest(BaseModel):
    stations: list[DummyOverlayStation] = Field(default_factory=list)


class ClassroomNetworkThreshold(BaseModel):
    ap_id: str = Field(alias="apId")
    ssid: str
    signal_threshold_dbm: int | None = Field(default=None, alias="signalThresholdDbm")


class EligibilityRequest(BaseModel):
    student_id: str = Field(alias="studentId")
    course_id: str | None = Field(default=None, alias="courseId")
    classroom_id: str = Field(alias="classroomId")
    purpose: Purpose
    classroom_networks: list[ClassroomNetworkThreshold] = Field(default_factory=list, alias="classroomNetworks")
    registered_devices: list[RegisteredDevice] = Field(default_factory=list, alias="registeredDevices")


class EligibilityEvidence(BaseModel):
    classroom_id: str = Field(alias="classroomId")
    matched_ap_ids: list[str] = Field(default_factory=list, alias="matchedApIds")
    station_count: int = Field(alias="stationCount")
    signal_dbm: int | None = Field(default=None, alias="signalDbm")
    signal_threshold_dbm: int | None = Field(default=None, alias="signalThresholdDbm")
    associated: bool | None = None
    authenticated: bool | None = None
    authorized: bool | None = None
    cache_hit: bool = Field(alias="cacheHit")


class EligibilityResponse(BaseModel):
    eligible: bool
    reason_code: str = Field(alias="reasonCode")
    matched_device_mac: str | None = Field(default=None, alias="matchedDeviceMac")
    observed_at: datetime | None = Field(default=None, alias="observedAt")
    snapshot_age_seconds: int | None = Field(default=None, alias="snapshotAgeSeconds")
    evidence: EligibilityEvidence


class HealthResponse(BaseModel):
    status: str
    redis_connected: bool = Field(alias="redisConnected")
    snapshot_ttl_seconds: int = Field(alias="snapshotTtlSeconds")


class SnapshotEnvelope(BaseModel):
    cache_hit: bool = Field(alias="cacheHit")
    snapshot: ClassroomSnapshot


class AdminSnapshotEnvelope(BaseModel):
    cache_hit: bool = Field(alias="cacheHit")
    overlay_active: bool = Field(alias="overlayActive")
    snapshot: ClassroomSnapshot


JsonDict = dict[str, Any]
