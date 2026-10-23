# 월간·사건 탐사 실행 규약 (monthly_run.md)
우선순위: 이 문서 > prompts/spacex_master.md 의 [기준 시점과 확정 사실 — 2026.8.31] 블록. 그 블록은 1회차 기준선이다.
2회차부터 기준선은 state/spacex_state.json 의 최신값 + 이번 회차 취재다. 마스터 프롬프트의 나머지 지시(PHASE 1~5·출력 규칙·준법 분리)는 그대로 따른다.

0. 준비: agent-research/CLAUDE.md · prompts/spacex_master.md · state/spacex_state.json · reports/changelog.md · data/spacex_watch.json · data/spacex_kpi.json · data/latest.json 을 읽는다.
   회차 id = 실행일 YYYY-MM(같은 id 가 있으면 -r2). 모드 monthly = PHASE 1~5 전체 / event = 해당 사건이 건드리는 축·이정표·규칙만 증분, 보고서는 reports/YYYY-MM-DD-event.md.
1. 원천 우선순위: ① SEC EDGAR 원문(8-K Ex.99.1·10-Q·10-K·S-1/424B) ② 회사 IR·어닝콜 ③ 규제기관(FCC·FAA·NASA·USAspending) ④ 발사 기록 ⑤ 1급 보도(Reuters·Bloomberg·WSJ·FT·CNBC) ⑥ 그 외는 교차 확인용.
   모든 숫자에 (출처 URL, 발표일). 원천이 다르면 둘 다 적고 ①에 가까운 쪽 채택. 경영진 발언·전망(머스크·CFO 포함)은 '이정표 후보'에만 — 판정 근거 금지.
2. 델타부터: 지난 회차 이후 사건 목록(날짜·원천) → 건드린 이정표·확률 규칙·트리거 표. 트리거마다 예상일 vs 실제일 — 틀렸으면 틀렸다고 쓰고 예상 방법을 고친다.
   확률 조정은 state.probability_adjustment_rules 에 적힌 규칙으로만. 규칙의 정의가 모호하면(예: 'GW당 연매출' 분자에 SI 전체냐 인프라 임대만이냐) 정의를 먼저 확정해 state 에 기록하고 판정한다.
   규칙 밖 조정은 '규칙 신설 제안'으로 따로 적고 이번 숫자에는 반영하지 않는다.
3. 이정표 진척: 모든 이정표에 progress = {current, target, unit, asof, source}(측정 불가면 null + 사유). status = 미판정 / 진행 / 달성 / 달성(조기) / 미달.
   분기 조기 신호(예: 연간 흑자 이정표에 분기 흑자)는 '진행'으로 두고 근거를 적는다. 3개 연속 미달 규칙 유지.
4. 재산정: 시나리오는 최신 분기 연환산 기준선에서 다시 출발. 지난 회차 대비 20% 넘게 바뀐 칸은 이유 한 줄.
   무위험금리 = data/latest.json meta.macro 의 미국 10년물(값·날짜 명기), 주식수 = 최신 공시 발행주식수. 할인율 산식 명시.
5. 산출: reports/YYYY-MM.md(마스터 출력 규칙) · state/spacex_state.json(run 블록·시나리오·밸류·이정표 progress·트리거 12개월 재작성·이정표 후보·open_questions — axes 에 축 한글 이름 표 포함)
   · reports/changelog.md(이번 회차 델타만) · agent-research/state/verify_list.json(핵심 숫자 상위 20개 {claim, value, source_url, source_date, where_in_report}).
6. 금지: 매매 행위를 지시·권유·암시하는 문장(우회 표현 포함), 목표가 표현, 매수·매도·보유·비중·진입·관망 판단 — 공개 파일 어디에도. 공개 state 에 recommendation·포지션·진입가 계열 키 생성 금지.
   확인 못 한 숫자를 확정처럼 쓰기 금지. 지난 숫자 무근거 복사 금지(같으면 '변화 없음(원천·날짜)' 재확인 표기).
7. 관제실 데이터(2026-10-23 추가 — 화면이 숫자로 그려지도록):
   · 이정표 progress 에 parts = [{label, current_num, target_num, unit, op: ">="|"<="|">"|"=="}] 를 함께 적는다. 복합(AND) 조건은 조건별로 한 줄씩.
     숫자로 옮길 수 없는 조건은 current_num=null + reason(추정 금지). progress 가 null 인 이정표는 progress_parts 에 조건별 사유를 남긴다. 문자열 progress 는 표시용으로 유지.
   · 이정표마다 status_history 에 {run_id, status} 1줄 append.
   · state.run_history 에 {run_id, run_date, price, price_date, probs{bull,base,bear}, band[lo,hi], mid, pw_value} 1줄 append(지난 줄 수정 금지).
   · state.kpi_history 에 분기별 운영 KPI(공시 재무 태그가 아닌 것: 가입자·ARPU·명판 GW·발사 수·부문 매출)를 {quarter, period_end, source, published, …} 로 적는다(같은 분기는 갱신).

