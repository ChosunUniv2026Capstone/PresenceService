from app.registry import parse_registry


def registry_payload() -> dict:
    return {
        "accessPoints": [
            {
                "collectorApId": "openwrt-a",
                "status": "active",
                "tokenHash": "hash-a",
                "tokenVersion": 3,
                "tokenRevokedAt": None,
                "interfaces": [
                    {
                        "interfaceId": "phy1-ap0",
                        "classroomId": "B101",
                        "classroomNetworkApId": "phy1-ap0",
                        "ssid": "SmartClass-Demo",
                    }
                ],
            }
        ]
    }


def test_parse_registry_accepts_raw_registry_payload() -> None:
    snapshot = parse_registry(registry_payload())

    assert [ap.collector_ap_id for ap in snapshot.access_points] == ["openwrt-a"]
    assert snapshot.access_points[0].interfaces[0].classroom_id == "B101"


def test_parse_registry_unwraps_success_data_envelope() -> None:
    snapshot = parse_registry({"success": True, "data": registry_payload(), "message": "ok", "meta": {}})

    assert [ap.collector_ap_id for ap in snapshot.access_points] == ["openwrt-a"]
    assert snapshot.get_access_point("openwrt-a") is not None
