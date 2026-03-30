from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.models import AccessPointSnapshot, ClassroomSnapshot, StationObservation


@dataclass(frozen=True)
class DummySnapshotProvider:
    path: Path

    def fetch_snapshot(self, classroom_id: str, observed_at: datetime | None = None) -> ClassroomSnapshot:
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        classroom = payload.get(classroom_id)
        if classroom is None:
            raise KeyError(classroom_id)

        stamp = observed_at or datetime.now(UTC)
        aps = []
        for ap in classroom["aps"]:
            aps.append(
                AccessPointSnapshot(
                    apId=ap["apId"],
                    ssid=ap["ssid"],
                    sourceCommand=ap["sourceCommand"],
                    stations=[StationObservation(**station) for station in ap["stations"]],
                )
            )

        return ClassroomSnapshot(
            classroomId=classroom_id,
            observedAt=stamp,
            collectionMode="dummy-openwrt",
            aps=aps,
        )
