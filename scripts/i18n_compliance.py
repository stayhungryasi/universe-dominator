#!/usr/bin/env python3
"""
i18n_compliance.py — 4개 언어 준법 판정 (G4)
================================================================================
막으려는 것 한 문장: **화면 문장이 특정 종목의 매매·비중·목표가를 '권하는' 것.**
그래서 낱말이 아니라 **문장의 목적**으로 판정한다(방지선 지뢰: 표면 특징으로 자르면 정상이 막힌다).

  purpose(sentence, lang) → "advice"(권고) · "disclaimer"(부정형 고지) · "neutral"
    권고 표현이 있고 부정 표지가 없으면 advice — 위반
    권고 표현이 있어도 같은 문장에 부정 표지가 있으면 disclaimer — '매수·매도 신호가 아닙니다'
  표현 표는 언어별, 판정 함수는 하나다.

  ko '진입'·'비중'은 분석에서도 흔하다('궤도 진입'·'매출 비중 42%') → 포지션 형태만 잡는다
     순위 '신규 진입'('TOP 20 신규 진입 (3위', '신규 진입·이탈')은 권고가 아니다 → 포지션 동사·조사가 붙은 형태만
     (probe_gate.FORBIDDEN_TEXT 와 같은 원칙. 관측 존 이름 '관망'은 사이트의 시차 관측 눈금이라 뺀다)
  en 'buy-back'·'sell-off' 같은 합성어는 권고가 아니다 → 하이픈 앞뒤는 제외
  ja '売買'·'売上'·'買い物'는 권고가 아니다
텔레그램은 범위 밖(한국어 그대로).
"""
import re

ADVICE = {
    "ko": re.compile(
        r"매수\s*(의견|추천|권|판단|하세요|하라|타이밍|기회)|매도\s*(의견|추천|권|판단|하세요|하라|타이밍)|"
        r"비중\s*(확대|축소|유지|을\s*늘|을\s*줄)|(포트폴리오|최대|목표|투자)\s*비중|"
        r"진입\s*(가격|가|시점|구간|트리거|하세요)|(분할|신규|추가)\s*매수|(분할|추가)\s*진입|"
        r"신규\s*진입\s*(하|을|를|은|시|타이밍)|목표\s*주?가|상승\s*여력|"
        r"사야\s*(한다|합니다)|팔아야\s*(한다|합니다)"),
    "en": re.compile(
        r"(?<![-\w])(buy|sell)(?![-\w])|\b(over|under)weight\b|\btarget\s+price\b|\bprice\s+target\b|"
        r"\bupside\b|\bstrong\s+buy\b|\baccumulate\b|\bwe\s+recommend\b|\byou\s+should\s+(buy|sell|own)\b", re.I),
    "es": re.compile(
        r"\b(comprar|vender|compre|venda|compren|vendan)\b|\bsobreponderar\b|\binfraponderar\b|"
        r"\bprecio\s+objetivo\b|\bpotencial\s+alcista\b|\brecomendamos\b", re.I),
    "ja": re.compile(r"(?<!売)買い(?!物)|売り(?!上げ|手)|目標株価|推奨|オーバーウェイト|アンダーウェイト|上値余地"),
}
NEGATION = {
    "ko": re.compile(r"아님|아닙니다|아니다|아니라|않습니다|않는다|않음|없습니다|금지"),
    "en": re.compile(r"\bnot\b|n't\b|\bno\b|\bnothing\b|\bnever\b|\bnor\b|\bneither\b|\bwithout\b", re.I),
    "es": re.compile(r"\bno\b|\bnada\b|\bnunca\b|\bni\b|\bsin\b", re.I),
    "ja": re.compile(r"ではありません|ではない|ではなく|しません|しない|ありません|該当しない|禁止"),
}
_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+|(?<=[。！？])")
_TAG = re.compile(r"<[^>]+>")


def sentences(text):
    t = _TAG.sub(" ", text or "")
    return [s.strip() for s in _SPLIT.split(t) if s and s.strip()]


def purpose(sentence, lang):
    """문장의 목적 — 'advice' · 'disclaimer' · 'neutral'."""
    adv = ADVICE.get(lang)
    if not adv or not adv.search(sentence or ""):
        return "neutral"
    return "disclaimer" if NEGATION[lang].search(sentence) else "advice"


def violations(text, lang):
    """권고 목적의 문장 목록(빈 목록 = 통과)."""
    return [s[:120] for s in sentences(text) if purpose(s, lang) == "advice"]
