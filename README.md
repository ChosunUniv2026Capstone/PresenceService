# PresenceService

Dummy OpenWrt-shaped presence service for the smart-class attendance and eligibility local MVP.

## What it does

- exposes `GET /health`
- exposes `GET /snapshots/classrooms/{classroom_id}`
- exposes `POST /eligibility/check`
- exposes admin dummy snapshot/overlay/reset endpoints for demo control
- loads realistic dummy AP/station data from `app/dummy_data/classroom_snapshots.json`
- caches classroom snapshots in Redis for 60 seconds by default
- evaluates registered device presence with classroom network/AP threshold evidence

## Environment

- `REDIS_HOST` default: `redis`
- `REDIS_PORT` default: `6379`
- `REDIS_DB` default: `0`
- `SNAPSHOT_TTL_SECONDS` default: `60`
- `REFRESH_LOCK_SECONDS` default: `15`
- `DUMMY_SNAPSHOT_PATH` optional path override

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

## Run tests

Run from the `PresenceService` directory with the app package on `PYTHONPATH`:

```bash
PYTHONPATH=. pytest -q
```

Current tests pass with Pydantic alias warnings; warning cleanup is a follow-up quality task, not a test failure.
