# PresenceService AGENTS

이 repo 는 Wi-Fi / OpenWrt / 게이트웨이 / 단말 수집 / 재실성 판정 보조 로직을 담당한다.

## 시작 전 필수
1. `git checkout main`
2. `git pull --ff-only origin main`
3. `git -C ../docs checkout main`
4. `git -C ../docs pull --ff-only origin main`

## 구현 전 반드시 확인할 문서
- `../docs/01-requirements/req-attendance-presence.md`
- `../docs/01-requirements/req-device-auth.md`
- `../docs/02-decisions/adr-0003-openwrt-device-collection.md`
- `../docs/02-decisions/adr-0004-attendance-authorization-flow.md`
- `../docs/03-conventions/conv-service-boundary.md`
- `../docs/04-architecture/network-topology.md`
- `../docs/04-architecture/service-map.md`

## docs gap 규칙
다음이면 구현 중지:
- 단말 수집 필드 / 매칭 우선순위 규칙이 없음
- Wi-Fi / AP / gateway 판정 조건 문서가 없음
- presence-service 와 backend 의 책임 경계가 애매함

이 경우 `$spec-first-dev-guard` 절차를 따른다.

## Git 규칙
- 브랜치: `feat/presence/<slug>` 등
- 커밋: `<type>(presence): <subject>`
- 판정 규칙이 바뀌면 docs 와 테스트를 함께 수정한다.
- 수집 데이터 형식이 바뀌면 architecture / convention 을 먼저 갱신한다.

## 권장 skill
- 개발 전 문서 검증: `$spec-first-dev-guard`
- Git 규약: `$git-governance`
