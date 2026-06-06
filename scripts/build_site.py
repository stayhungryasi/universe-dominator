"""
build_site.py — data/latest.json + scripts/template.html → index.html
"""
import json
from pathlib import Path

HERE = Path(__file__).parent.parent
DATA_PATH = HERE / "data" / "latest.json"
TEMPLATE_PATH = HERE / "scripts" / "template.html"
OUTPUT_PATH = HERE / "index.html"


def main():
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    
    # 데이터를 인라인 JSON으로 임베드
    data_json = json.dumps(data, ensure_ascii=False, indent=2)
    
    # 메타 정보 추출 (페이지 표시용)
    meta = data.get("meta", {})
    fetched_date = meta.get("fetched_date", "—")
    fetched_label = fetched_date.replace("-", ".")  # YYYY.MM.DD
    
    # 글로벌 1위 시총 / 합계 계산
    earth_stocks = data.get("regions", {}).get("earth", {}).get("stocks", [])
    top1_mc = earth_stocks[0]["mc"] if earth_stocks else 0
    top1_name = earth_stocks[0]["name"] if earth_stocks else "—"
    
    # Trillion club count: 1T 이상 종목 수
    trillion_count = sum(1 for s in earth_stocks if s["mc"] >= 1000)
    # 글로벌 TOP 100 합계는 글로벌 페이지 전체에서 계산 (현재 TOP 20만 있음)
    # → 전체 합계는 별도 메타로 처리하거나 TOP 20 합계로 대체
    top20_sum = sum(s["mc"] for s in earth_stocks)
    
    html = template
    html = html.replace("{{DATA_JSON}}", data_json)
    html = html.replace("{{FETCHED_DATE}}", fetched_label)
    html = html.replace("{{TOP1_NAME}}", top1_name)
    html = html.replace("{{TOP1_MC}}", f"${top1_mc/1000:.2f}T" if top1_mc >= 1000 else f"${top1_mc:.0f}B")
    html = html.replace("{{TRILLION_COUNT}}", str(trillion_count))
    html = html.replace("{{TOP20_SUM}}", f"${top20_sum/1000:.1f}T" if top20_sum >= 1000 else f"${top20_sum:.0f}B")
    
    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"[OK] {OUTPUT_PATH} 빌드 완료 ({len(html):,} chars)")


if __name__ == "__main__":
    main()
