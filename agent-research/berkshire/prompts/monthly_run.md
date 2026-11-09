# 버크셔 월간·사건 탐사 실행 규약 (monthly_run.md)
우선순위: 이 문서 > prompts/berkshire_master.md 의 [기준 시점] 블록. 2회차부터 기준선은 state/berkshire_state.json 의 최신값 + 이번 회차 취재다.
마스터 프롬프트의 나머지 지시(PHASE 1~5·파괴적 혁신 렌즈·출력 규칙·준법 분리)는 그대로 따른다. 경로는 모두 agent-research/berkshire/ 아래다.

0. 준비: agent-research/berkshire/CLAUDE.md · prompts/berkshire_master.md · state/berkshire_state.json · reports/changelog.md · data/brk_watch.json · data/latest.json 을 읽는다.
   회차 id = 실행일 YYYY-MM(같은 id 가 있으면 -r2). 모드 monthly = PHASE 1~5 전체 / event = 그 사건이 건드리는 축·이정표·규칙만 증분, 보고서는 reports/YYYY-MM-DD-event.md.
1. 원천 우선순위: ① SEC EDGAR 원문(10-Q·10-K·8-K·13F-HR·DEF 14A, 버크셔 CIK 0001067983) ② berkshirehathaway.com 보도자료·주주서한·연차보고서
   ③ 자회사 공시(BNSF·BHE 의 자체 10-Q/10-K, PacifiCorp 소송 기록) ④ 1급 보도(Reuters·Bloomberg·WSJ·FT·CNBC) ⑤ 그 외는 교차 확인용.
   모든 숫자에 (출처 URL, 발표일). 원천이 다르면 둘 다 적고 ①에 가까운 쪽 채택. 버핏·에이블 등 경영진 발언·전망은 '이정표 후보'에만 — 판정 근거 금지.
2. 델타부터: 지난 회차 이후 사건 목록(날짜·원천) → 건드린 이정표·확률 규칙·트리거 표. 트리거마다 예상일 vs 실제일 — 틀렸으면 틀렸다고 쓰고 예상 방법을 고친다.
   확률 조정은 state.probability_adjustment_rules 에 적힌 규칙으로만. 정의가 모호하면 정의를 먼저 확정해 state.definitions 에 기록하고 판정한다.
   규칙 밖 조정은 '규칙 신설 제안'(state.rule_proposals_not_applied)으로 따로 적고 이번 숫자에는 반영하지 않는다.
3. 이정표 진척: 모든 이정표에 progress = {current, target, unit, asof, source, parts}(측정 불가면 null + progress_reason + progress_parts).
   parts = [{label, current_num, target_num, unit, op: ">="|"<="|">"|"=="}] — 숫자로 옮길 수 없는 조건은 current_num=null + reason(추정 금지).
   status = 미판정 / 진행 / 달성 / 달성(조기) / 미달. 이정표마다 status_history 에 {run_id, status} 1줄 append. 같은 축 3개 연속 미달 규칙 유지.
4. 재산정: 시나리오는 최신 분기 기준선(BPS·세후 영업이익·현금)에서 다시 출발. 지난 회차 대비 20% 넘게 바뀐 칸은 이유 한 줄.
   무위험금리 = data/latest.json meta.macro 의 미국 10년물(값·날짜 명기), 주식 수 = 최신 10-Q 의 A주 환산 주식 수. 할인율 산식 명시.
5. 산출: reports/YYYY-MM.md(마스터 출력 규칙) · state/berkshire_state.json(run 블록·시나리오·valuation·이정표 progress·트리거 12개월 재작성·
   disruption_watch·open_questions_for_next_run·axes) · reports/changelog.md(이번 회차 델타만) · state/verify_list.json(핵심 숫자 상위 20개 이상
   {claim, value, source_url, source_date, where_in_report}).
   · state.run_history 에 {run_id, run_date, price, price_date, probs{bull,base,bear}, band[lo,hi], mid, pw_value, summary} 1줄 append(지난 줄 수정 금지).
   · state.kpi_history 에 분기 KPI 를 {quarter, period_end, source, published, origin("disc"|"calc"), op_earnings_usd_m, buyback_usd_m, cash_tbill_usd_m,
     bvps_a_usd, float_usd_m, equity_net_usd_m, uw_usd_m, bnsf_usd_m, bhe_usd_m} 로 적는다(백만 달러 · 같은 분기는 갱신 · 계산값은 origin=calc + 산식).
     적은 값은 verify_list 에 where_in_report "state.kpi_history <분기>.<키>" 로 올려 검증관 대조를 받는다.
   · data/brk_watch.json 의 facts.quarters(기계가 XBRL 에서 잰 값)와 같은 개념의 값이 다르면 둘 다 적고 차이의 이유(정의 차이)를 한 줄.
6. 금지: 매매 행위를 지시·권유·암시하는 문장(우회 표현 포함), 목표가 표현, 매수·매도·보유·비중·진입·관망 판단 — 공개 파일 어디에도.
   공개 state 에 recommendation·포지션·진입가 계열 키 생성 금지. '현금 비중'·'포트폴리오 비중' 같은 표현은 '현금 배치율'·'구성비'로 쓴다
   (자사주 매입·순매수 같은 회사의 행위 서술은 분석이다 — 독자의 행위를 말하지 마라).
   확인 못 한 숫자를 확정처럼 쓰기 금지. 지난 숫자 무근거 복사 금지(같으면 '변화 없음(원천·날짜)' 재확인 표기).
7. 끝내기 전에 관문 명령으로 스스로 확인한다(이번 실행 정보에 명령이 적혀 있다).
