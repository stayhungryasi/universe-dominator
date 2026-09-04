# SpaceX 10년 리서치 프로젝트

## 목적
prompts/spacex_master.md 의 지시에 따라 SpaceX(SPCX) 분석 문서를 만들고 매월 갱신한다.

## 파일 구조
- prompts/spacex_master.md : 마스터 프롬프트 (수정 금지)
- state/spacex_state.json : 시나리오 확률, 이정표 판정, 적정가치. 매 실행 후 갱신
- reports/YYYY-MM.md : 그 달의 전체 보고서
- reports/changelog.md : 지난달 대비 바뀐 것만 누적

## 규칙
- 모든 숫자는 웹 검색으로 확인하고 (출처, 날짜)를 붙인다. 확인 못 한 숫자는 "추정"으로 표기
- 머스크 발언은 판단 근거로 쓰지 않는다
- 실행 시작 시 state/spacex_state.json이 있으면 먼저 읽고 "지난달 대비 변화"부터 쓴다
- 한국어로 작성
- **준법 분리**: 진입 방식·포지션 규모·매수/관망 권고는 `reports/YYYY-MM.md` 에 쓰지 않는다.
  `reports/YYYY-MM_private.md` 에만 쓴다(이 파일은 .gitignore 로 로컬에만 남는다).
  본 보고서의 PHASE 4 는 적정가치 산출·주가 반영 분해·판단 변경 트리거까지만.
