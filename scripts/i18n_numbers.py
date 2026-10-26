#!/usr/bin/env python3
"""
i18n_numbers.py — 번역문의 숫자 관문 (G2 · 지뢰 ⑱ 숫자 규율 재사용)
================================================================================
막으려는 것 한 문장: **원문(한국어)과 숫자 집합이 다른 번역문이 게시되는 것.**
그래서 검사도 그 정의로 한다 — 원문 숫자 ⊆ 번역 숫자, 번역 숫자 ⊆ 원문 숫자(양방향).
대조기는 새로 만들지 않는다: companion_essays.check_numbers(단위 환산·반올림 폭·면제 표기)를
그대로 부르고, 이 모듈은 **언어별 표기를 그 대조기가 읽는 표기로 옮기는 일**만 한다.

  ja  兆·億·万·千 → 조·억·만·천 / 年·月·日 → 년·월·일 / 四半期 → 분기 / 전각 숫자 → 반각
  es  소수점 쉼표(4,8) → 4.8 · 천 단위 마침표(1.234) → 1234 /
      billón(es)=10¹² · mil millones=10⁹ · millón(es)=10⁶ → trillion·billion·million
  en  월 이름 날짜(Oct 26, 2026 · October 26)는 날짜 면제
  공통  HTML 태그는 걷어 내고 대조(속성 숫자·SVG 좌표는 화면 숫자가 아니다)

허용 오차·면제(연도·날짜·분기·10 이하 정수)는 숫자 규율과 **같다** — 기준이 두 벌이 되지 않게.
"""
import re
import unicodedata

_MON_EN = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?"
_MON_ES = r"(?:ene(?:ro)?|feb(?:rero)?|mar(?:zo)?|abr(?:il)?|may(?:o)?|jun(?:io)?|jul(?:io)?|ago(?:sto)?|sep(?:t(?:iembre)?)?|oct(?:ubre)?|nov(?:iembre)?|dic(?:iembre)?)\.?"
_DATE_EN = re.compile(r"\b" + _MON_EN + r"\s+\d{1,2}(?:,\s*\d{4})?\b|\b\d{1,2}\s+" + _MON_EN + r"(?:\s+\d{4})?\b", re.I)
_DATE_ES = re.compile(r"\b\d{1,2}\s+(?:de\s+)?" + _MON_ES + r"(?:\s+(?:de\s+)?\d{4})?\b", re.I)
_TAG = re.compile(r"<[^>]+>")


def _plain(text):
    t = unicodedata.normalize("NFKC", _TAG.sub(" ", text or ""))
    return t.replace("&gt;", ">").replace("&lt;", "<").replace("&amp;", "&").replace("&nbsp;", " ")


def normalize(text, lang):
    """그 언어의 숫자 표기 → 숫자 규율이 읽는 표기(한국어·영어 단위)."""
    t = _plain(text)
    if lang == "ja":
        t = t.replace("四半期", "분기")
        for a, b in (("兆", "조"), ("億", "억"), ("万", "만"), ("千", "천")):
            t = t.replace(a, b)
        t = re.sub(r"(\d)\s*年", r"\1년 ", t)
        t = re.sub(r"(\d)\s*月", r"\1월 ", t)
        t = re.sub(r"(\d)\s*日", r"\1일 ", t)
        t = re.sub(r"(\d)\s*カ月", r"\1개월", t)
    elif lang == "es":
        t = _DATE_ES.sub(" ", t)
        t = re.sub(r"(\d)\s+mil\s+millones\b", r"\1 billion", t, flags=re.I)
        t = re.sub(r"(\d)\s+billones?\b", r"\1 trillion", t, flags=re.I)
        t = re.sub(r"(\d)\s+mill(?:ó|o)n(?:es)?\b", r"\1 million", t, flags=re.I)
        # 천 단위 마침표(1.234.567) → 붙임, 그다음 소수점 쉼표(4,8) → 4.8
        t = re.sub(r"\b\d{1,3}(?:\.\d{3})+\b", lambda m: m.group(0).replace(".", ""), t)
        t = re.sub(r"(\d),(\d)", r"\1.\2", t)
    elif lang == "en":
        t = _DATE_EN.sub(" ", t)
    return t


def check(src_ko, translated, lang):
    """→ (번역에만 있는 숫자, 원문에만 있는 숫자). 둘 다 빈 목록이면 통과."""
    import companion_essays as ce
    src = normalize(src_ko, "ko")
    dst = normalize(translated, lang)
    extra = ce.check_numbers(dst, [], src)
    missing = ce.check_numbers(src, [], dst)
    # 면제 비대칭 보정 — 한쪽만 날짜·연도로 면제된 **맨 숫자**('최근 90일' ↔ 'last 90 days',
    # '2000년' ↔ 'de 2000,')는 반대쪽 본문에 같은 숫자가 그대로 있으면 같은 숫자다.
    # 단위가 붙은 값(1조·$32 million)은 보정하지 않는다 — 그건 환산 대조가 맡는다.
    extra = [x for x in extra if not _bare_in(x, src)]
    missing = [x for x in missing if not _bare_in(x, dst)]
    return extra, missing


def _bare_in(token, text):
    if not re.fullmatch(r"\d[\d,]*(?:\.\d+)?", token or ""):
        return False
    pat = r"(?<![\d.,])" + re.escape(token) + r"(?![\d]|[.,]\d)"
    return re.search(pat, text) is not None


def ok(src_ko, translated, lang):
    e, m = check(src_ko, translated, lang)
    return not e and not m
