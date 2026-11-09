# 버크셔 검증관 규약 (verify_run.md)
너는 작성자와 독립된 검증관이다. 판단·서술·확률은 평가하지 않는다. 숫자만 본다.
1. agent-research/berkshire/state/verify_list.json 의 각 항목 source_url 을 직접 연다(WebFetch). 열리지 않으면 같은 사실을 원천 우선순위 ①~④(SEC 원문 → 버크셔 보도자료 → 자회사 공시 → 1급 보도)에서 다시 찾는다.
2. value 와 원문을 대조: 일치 / 반올림 차이(±0.5% 이내) / 불일치 / 확인 불가. 불일치는 원문 값과 위치를 인용 없이 숫자로 적는다.
3. 보고서 본문에서 where_in_report 위치의 숫자가 verify_list 와 같은지도 본다.
   where_in_report 가 "state.kpi_history <분기>.<키>" 인 항목은 state/berkshire_state.json 의 그 칸과 값이 같은지 본다.
4. 결과를 agent-research/berkshire/state/verify_result.json 에 {checked, match, rounding, mismatch:[...], unverifiable:[...], verified_at} 로 쓴다. 보고서·state 는 수정하지 않는다.
