#!/usr/bin/env python3
"""
거인의 어깨 — 세계적 투자 거인들의 13F 포트폴리오 자동 수집
================================================================
데이터 소스: SEC EDGAR (미국 증권거래위원회 공식 무료 API — 키 불필요)
  ① https://data.sec.gov/submissions/CIK##########.json  → 최근 공시 목록
  ② 13F-HR 최신 2건의 information table XML 파싱          → 보유종목·평가액
  ③ 직전 분기와 비교 → 신규 매수 / 전량 매도 / 지분 증감

출력: data/gurus.json (research.html '거인의 어깨' 탭에 렌더링)

정직한 한계 (페이지에도 명시):
  13F는 분기 1회 + 최대 45일 지연 공시, 미국 상장 롱포지션만 포함.

운영 규칙 준수:
  - 거인 1명 실패해도 나머지 계속 (개별 try/except)
  - 전원 실패 시 기존 gurus.json 유지 (덮어쓰지 않음)
  - SEC 요청 간 0.5초 간격 + User-Agent 명시 (SEC 필수 요건)
"""
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERE = Path(__file__).parent.parent
DATA_DIR = HERE / "data"
CONFIG_PATH = DATA_DIR / "gurus_config.json"
OUT_PATH = DATA_DIR / "gurus.json"

KST = timezone(timedelta(hours=9))
# SEC는 User-Agent 없는 요청을 차단함 — 연락 가능한 형태 필수
UA = "UNIVERTRIX univertrix.com contact@univertrix.com"
SLEEP = 0.5  # SEC 권고: 초당 10회 이하 — 넉넉하게


def _get(url, timeout):
    """SEC 요청 공통: 403/429/5xx·일시 오류 시 3회 재시도 (5s→15s→30s)"""
    last = None
    for i, wait in enumerate([0, 5, 15, 30]):
        if wait:
            print(f"    재시도 {i}/3 ({wait}s 대기) ← {last}", file=sys.stderr)
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept-Encoding": "identity",
                "Host": urllib.parse.urlparse(url).netloc,
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code} {e.reason}"
            if e.code not in (403, 429, 500, 502, 503, 504):
                break  # 404 등은 재시도 무의미
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"{last} — {url}")


def http_json(url):
    return json.loads(_get(url, 30).decode("utf-8"))


def http_text(url):
    return _get(url, 90).decode("utf-8", errors="replace")


def localname(tag):
    """XML 네임스페이스 제거: {ns}value → value"""
    return tag.rsplit("}", 1)[-1]


def find_infotable_url(cik_int, accession):
    """공시 폴더 index.json에서 information table XML 파일 찾기"""
    acc_nodash = accession.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}"
    idx = http_json(f"{base}/index.json")
    time.sleep(SLEEP)
    items = idx.get("directory", {}).get("item", [])
    xmls = [i for i in items if i.get("name", "").lower().endswith(".xml")]
    # 1순위: 파일명에 infotable/informationtable 포함
    for i in xmls:
        n = i["name"].lower()
        if "infotable" in n or "informationtable" in n:
            return f"{base}/{i['name']}"
    # 2순위: primary_doc 아닌 가장 큰 XML (infotable이 항상 제일 큼)
    others = [i for i in xmls if "primary_doc" not in i["name"].lower()]
    if others:
        biggest = max(others, key=lambda i: int(i.get("size", 0) or 0))
        return f"{base}/{biggest['name']}"
    return None


def parse_infotable(xml_text):
    """13F information table → [{name, cusip, value($), shares, put_call}]"""
    root = ET.fromstring(xml_text)
    rows = []
    for el in root.iter():
        if localname(el.tag) != "infoTable":
            continue
        rec = {"name": "", "cusip": "", "title": "", "value": 0, "shares": 0, "put_call": ""}
        for c in el.iter():
            ln = localname(c.tag)
            txt = (c.text or "").strip()
            if ln == "nameOfIssuer":
                rec["name"] = txt
            elif ln == "cusip":
                rec["cusip"] = txt
            elif ln == "titleOfClass":
                # 같은 발행사의 복수 클래스(Alphabet A/C)·복수 ETF(iShares Tr)를
                # 가르는 유일한 필드 — 표기 구분에 쓴다
                rec["title"] = txt
            elif ln == "value":
                try:
                    rec["value"] = int(float(txt))  # 2023년~ 단위: 달러
                except ValueError:
                    pass
            elif ln == "sshPrnamt":
                try:
                    rec["shares"] = int(float(txt))
                except ValueError:
                    pass
            elif ln == "putCall":
                rec["put_call"] = txt
        if rec["cusip"]:
            rows.append(rec)
    return rows


def aggregate(rows):
    """같은 종목(cusip+put/call) 여러 매니저 분산 보고 → 합산"""
    agg = {}
    for r in rows:
        key = (r["cusip"], r["put_call"])
        if key not in agg:
            agg[key] = {"name": r["name"], "cusip": r["cusip"],
                        "title": r.get("title", ""),
                        "put_call": r["put_call"], "value": 0, "shares": 0}
        agg[key]["value"] += r["value"]
        agg[key]["shares"] += r["shares"]
    return list(agg.values())


def pretty_name(raw):
    """'APPLE INC' → 'Apple Inc' 정도의 최소 정돈"""
    small = {"INC", "CORP", "CO", "LTD", "PLC", "SA", "NV", "LP", "ADR", "CL", "A", "B", "&"}
    words = []
    for w in raw.split():
        words.append(w.title() if w.upper() not in small or w == "&" else
                     (w if w == "&" else w.capitalize()))
    return " ".join(words)


# titleOfClass 에서 의미를 지우고 남는 상투어 — 이것만 남으면 판별 불가로 본다
_TITLE_NOISE = re.compile(
    r"\b(COM|COMMON|STK|STOCK|CAP|CAPITAL|SHS|SH|SHARES|ORD|ORDINARY|NPV|USD|NEW|"
    r"PAR|VALUE|VTG|VOTING|NON|UNIT|UNITS|BEN|INT|INTEREST|TR|TRUST)\b")
_CLASS_RE = re.compile(r"\bCL(?:ASS)?\s*([A-Z0-9]{1,2})\b")
_SERIES_RE = re.compile(r"\bSER(?:IES)?\s*([A-Z0-9]{1,2})\b")


def class_label(title):
    """13F titleOfClass → 사람이 읽는 클래스 구분. 판별 불가면 None.

    'CAP STK CL C'      → 'Class C'      (알파벳 A/C 같은 복수 클래스)
    'RUSSELL 2000 ETF'  → 'Russell 2000 Etf'  (iShares Tr 처럼 발행사명이 같고
                                               실제 종목이 이 필드에 담기는 경우)
    'COM' / 'PAR $.01'  → None           (상투어뿐 — 폴백으로 넘긴다)
    """
    t = (title or "").upper().strip()
    if not t:
        return None
    m = _CLASS_RE.search(t)
    if m:
        return f"Class {m.group(1)}"
    m = _SERIES_RE.search(t)
    if m:
        return f"Series {m.group(1)}"
    rest = _TITLE_NOISE.sub(" ", t)
    rest = " ".join(re.sub(r"[^A-Z0-9&. ]", " ", rest).split())
    # 글자가 하나도 없으면 액면가 잔해('PAR $.01' → '.01') 같은 소음이다
    if len(rest) < 3 or not re.search(r"[A-Z]", rest):
        return None
    return rest.title()


def label_holdings(entries):
    """한 매니저 카드 안에서 표시명이 2회 이상 겹치면 클래스 구분을 붙인다.

    ⚠️ 합산 병합은 하지 않는다 — 클래스별 증감(+45% vs +658%)이 매수 패턴
    정보이기 때문이다. 같은 이름 두 줄로 보이는 것이 버그였지, 두 줄인 것이
    버그가 아니다.
    entries: [{"base": 정돈된 발행사명, "mark": ' (PUT)' 등, "title": titleOfClass}]
    → 각 항목에 "label" 을 채워 돌려준다.
    """
    counts = {}
    for e in entries:
        key = e["base"] + e["mark"]
        counts[key] = counts.get(key, 0) + 1
    seen = {}
    for e in entries:
        key = e["base"] + e["mark"]
        if counts[key] < 2:
            e["label"] = key
            continue
        cls = class_label(e.get("title")) or "별도 클래스"
        label = f"{e['base']} ({cls}){e['mark']}"
        seen[label] = seen.get(label, 0) + 1
        if seen[label] > 1:   # 클래스 필드까지 같으면 순번으로라도 갈라 준다
            label = f"{e['base']} ({cls} {seen[label]}){e['mark']}"
        e["label"] = label
    return entries


def put_call_mark(put_call):
    pc = (put_call or "").upper()
    return " (PUT)" if pc == "PUT" else " (CALL)" if pc == "CALL" else ""


def fetch_guru(cfg, top_n):
    cik = str(cfg["cik"]).lstrip("0")
    cik10 = str(cfg["cik"]).zfill(10)
    sub = http_json(f"https://data.sec.gov/submissions/CIK{cik10}.json")
    time.sleep(SLEEP)
    entity_name = sub.get("name", "?")

    # 안전장치: CIK가 가리키는 SEC 실명이 예상과 다르면 수집 거부 (엉뚱한 펀드 오표시 방지)
    expect = cfg.get("match", "").upper()
    if expect and expect not in entity_name.upper():
        raise RuntimeError(f"CIK 불일치 — SEC 실명 '{entity_name}' ≠ 예상 '{expect}'")

    recent = sub.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accs = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    periods = recent.get("reportDate", [])

    # 13F-HR만 (원본 보고서 — 수정본 /A는 v2에서), 보고기간(period) 기준 최신 2개
    filings = []
    seen_periods = set()
    for f, a, d, p in zip(forms, accs, dates, periods):
        if f != "13F-HR" or p in seen_periods:
            continue
        seen_periods.add(p)
        filings.append({"accession": a, "filed": d, "period": p})
        if len(filings) == 2:
            break
    if not filings:
        raise RuntimeError("13F-HR 공시를 찾지 못함")

    parsed = []
    for fl in filings:
        url = find_infotable_url(cik, fl["accession"])
        if not url:
            raise RuntimeError(f"infotable XML 없음 ({fl['accession']})")
        holdings = aggregate(parse_infotable(http_text(url)))
        time.sleep(SLEEP)
        parsed.append({**fl, "holdings": holdings})

    cur = parsed[0]
    prev = parsed[1] if len(parsed) > 1 else None
    prev_map = {}
    if prev:
        prev_map = {(h["cusip"], h["put_call"]): h for h in prev["holdings"]}

    total = sum(h["value"] for h in cur["holdings"]) or 1
    top = sorted(cur["holdings"], key=lambda h: -h["value"])[:top_n]

    out_holdings = []
    for h in top:
        ph = prev_map.get((h["cusip"], h["put_call"]))
        if ph is None:
            chg = "NEW"
        elif ph["shares"] > 0 and h["shares"] > 0:
            pct = (h["shares"] - ph["shares"]) / ph["shares"] * 100
            chg = f"{pct:+.0f}%" if abs(pct) >= 1 else "―"
        else:
            chg = "―"
        out_holdings.append({
            "base": pretty_name(h["name"]),
            "mark": put_call_mark(h["put_call"]),
            "title": h.get("title", ""),
            "pct": round(h["value"] / total * 100, 1),
            "value_b": round(h["value"] / 1e9, 2),
            "chg": chg,
        })

    # 표시명 확정 — 같은 카드 안 동명이인(복수 클래스)만 구분자를 얻는다
    label_holdings(out_holdings)
    out_holdings = [{"name": e["label"], "pct": e["pct"],
                     "value_b": e["value_b"], "chg": e["chg"]} for e in out_holdings]

    # 전량 매도: 직전 분기 상위 top_n 안에 있었는데 이번에 완전히 사라진 종목
    exits = []
    if prev:
        cur_keys = {(h["cusip"], h["put_call"]) for h in cur["holdings"]}
        prev_top = sorted(prev["holdings"], key=lambda h: -h["value"])[:top_n]
        gone = [h for h in prev_top if (h["cusip"], h["put_call"]) not in cur_keys][:5]
        exits = [e["label"] for e in label_holdings(
            [{"base": pretty_name(h["name"]), "mark": put_call_mark(h["put_call"]),
              "title": h.get("title", "")} for h in gone])]

    q = f"{cur['period'][:4]}년 {(int(cur['period'][5:7]) - 1) // 3 + 1}분기"
    return {
        "cik": cfg["cik"],
        "entity": entity_name,
        "name_ko": cfg.get("name_ko", entity_name),
        "person": cfg.get("person", ""),
        "tagline": cfg.get("tagline", ""),
        "period": cur["period"], "period_label": q,
        "filed": cur["filed"],
        "total_b": round(total / 1e9, 1),
        "positions": len(cur["holdings"]),
        "holdings": out_holdings,
        "exits": exits,
    }


def main():
    cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    top_n = cfg.get("top_n", 15)
    gurus, fails = [], 0
    for g in cfg.get("gurus", []):
        try:
            r = fetch_guru(g, top_n)
            gurus.append(r)
            print(f"  [거인] {r['name_ko']} ({r['entity']}) — {r['period_label']} "
                  f"${r['total_b']}B · {r['positions']}종목 ✓")
        except Exception as e:
            fails += 1
            print(f"  [거인] {g.get('name_ko', g['cik'])} 실패: {e}", file=sys.stderr)

    if not gurus:
        print("  [거인] 전원 실패 — 기존 gurus.json 유지", file=sys.stderr)
        sys.exit(0)  # continue-on-error와 별개로 기존 데이터 보호

    now = datetime.now(KST)
    OUT_PATH.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "generated_label": now.strftime("%Y.%m.%d %H:%M"),
        "gurus": gurus,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[OK] gurus.json — 성공 {len(gurus)} / 실패 {fails}")


if __name__ == "__main__":
    main()
