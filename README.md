# PresenceService

Dummy OpenWrt-shaped presence service for the first attendance eligibility slice.

## What it does

- exposes `GET /health`
- exposes `GET /snapshots/classrooms/{classroom_id}`
- exposes `POST /eligibility/check`
- loads realistic dummy AP/station data from `app/dummy_data/classroom_snapshots.json`
- caches classroom snapshots in Redis for 60 seconds

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

```bash
pytest
```
