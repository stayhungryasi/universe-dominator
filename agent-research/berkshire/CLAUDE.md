# 🏛 버크셔 탐사선 — 버크셔 해서웨이 10년 리서치 (2026-11-09)

## 목적
prompts/berkshire_master.md 의 지시에 따라 버크셔 해서웨이(BRK.A/BRK.B) 분석 문서를 만들고 매월·사건마다 갱신한다.
무인 탐사선(SpaceX, 상위 폴더)과 **같은 관문·같은 규율**, 대상만 다르다. 차별점은 '파괴적 혁신 렌즈'(state.disruption_watch).

## 파일 구조
- prompts/berkshire_master.md : 마스터 프롬프트 (수정 금지 — 준법 분리 규칙 포함)
- prompts/monthly_run.md : 2회차부터의 실행 규약(마스터의 기준 시점 블록보다 우선)
- prompts/verify_run.md : 독립 검증관 규약(숫자만 본다)
- state/berkshire_state.json : 시나리오 확률·이정표 판정·진척·가정 가치·KPI·파괴적 변수 (공개)
- state/*_private.json · reports/*_private.md : 포지션 판단 별지 — .gitignore, 로컬 전용
- state/verify_list.json · state/verify_result.json : 검증 대상 숫자 / 검증관 판정
- reports/YYYY-MM.md(월간) · reports/YYYY-MM-DD-event.md(사건) · reports/changelog.md(델타 누적)

## 실행 장치
- `.github/workflows/brk-probe.yml` — 매월 2일 09:43 KST · 사건 연동(`brk_watch.py` 가 긴급·중요 SEC 공시·트리거 due 에 발사)
  · 관측 공백 33일이면 측정층이 월간 회차를 스스로 발사(예약 누락 대비) · 수동
- 로컬 Claude Code 수동 실행은 언제든 가능하고 우선한다 — 같은 monthly_run.md 를 따른다
- 게시 관문 `python scripts/probe_gate.py --subject berkshire check` — 스키마·확률 합·판정 상태·회차 사슬·출처·검증관 불일치·준법

## 규칙
- 모든 숫자는 1차 원천으로 확인하고 (출처, 날짜)를 붙인다. 확인 못 한 숫자는 "추정"으로 표기
- 버핏·에이블 등 경영진 발언·주주서한 전망은 판단 근거로 쓰지 않는다(이정표 후보로만)
- 실행 시작 시 state 를 먼저 읽고 "지난 회차 대비 변화"부터 쓴다 · 한국어로 작성
- **준법 분리**: 진입 방식·포지션 규모·매수/관망 권고는 공개 보고서에 쓰지 않는다 — `_private.md` 에만.
  문장의 목적이 기준이다. '비중'은 매수·매도와 같은 줄에 쓰지 않는다(현금 배치율·구성비로).
- daily-update.yml 의 어떤 스텝도 이 폴더에 쓰지 않는다 — 측정층(`brk_watch.py`)도 state 를 읽기만 한다
