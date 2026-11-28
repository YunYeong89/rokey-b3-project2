"""Whisper 가 받아 적은 글에서 호출어 "자비스 / Jarvis" 를 발음 규칙으로 찾는다.

같은 "자비스/Jarvis" 라도 발음에 따라 Whisper 는 매번 다르게 적는다 (실측):
  JARVIS, Jarvis, JAVAS, 자르비스, 좌베스, 짜르베스, 자르시, 자러스, 자베스, 잘비스, 제비스, 자비쎄 …
그래서 글자 목록 대신 한글을 자모로 쪼개 "발음 모양"을 본다.
  첫 글자   : ㅈ/ㅉ/ㅊ + ㅏ ㅐ ㅑ ㅓ ㅔ ㅕ ㅘ ㅝ … (받침 없음 또는 ㄹ)   → "자/재/저/좌/잘/짜/차"
  가운데    : ㄹ / ㅂ / ㅃ / ㅍ 소리 (r, b, v), 0~2 글자               → "르/러/비/베"
  마지막    : ㅅ / ㅆ / ㅈ / ㅉ + ㅡ ㅣ ㅔ ㅐ ㅜ, 받침 없음              → "스/시/쎄/즈"
  전체 3~4 글자, 끝에 붙은 "야/아/님/씨" 는 떼고 본다.
영어로 적히면 "jarvis" 와 철자가 2글자 이내로 다르면 인정 (Javas, Jervis …).

걸러지는 말 (실측): 서비스, 자르세요, 자비로운, 자세히, 잡수세요, 저기요, 비스킷, 재미있어요, "자 이제" …
놓치는 표현이 있으면 config.WAKEWORD_EXTRA_WORDS 에 그대로 추가하면 된다.
"""
import re

import config

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"

FIRST_CHO, FIRST_JUNG, FIRST_JONG = set("ㅈㅉㅊ"), set("ㅏㅐㅑㅒㅓㅔㅕㅖㅘㅝ"), {" ", "ㄹ"}
MID_CHO = set("ㄹㅂㅃㅍ")
LAST_CHO, LAST_JUNG = set("ㅅㅆㅈㅉ"), set("ㅡㅣㅔㅐㅜ")
# 호출어 뒤에 붙어서 한 단어로 적히는 말. Whisper 가 무음 끝에 "MBC 뉴스 자베스입니다." 처럼
# 붙이는 환각도 있어서 "입니다" 등을 뗀다. ("요" 는 넣지 않는다 — "자르세요" → "자르세" 오감지 방지)
SUFFIXES = ("입니다", "이에요", "이야", "예요", "한테", "에게", "야", "아", "님", "씨", "가", "는", "을", "를")

_TOKEN = re.compile(r"[가-힣]+|[A-Za-z]+")


def _jamo(syllable):
    code = ord(syllable) - 0xAC00
    return _CHO[code // 588], _JUNG[(code % 588) // 28], _JONG[code % 28]


def _hangul_like_jarvis(word):
    if _shape_ok(word):
        return True
    for suffix in SUFFIXES:                       # "자비스야" / "자베스입니다" → "자비스" / "자베스"
        if word.endswith(suffix) and len(word) - len(suffix) >= 3 and _shape_ok(word[: -len(suffix)]):
            return True
    return False


def _shape_ok(word):
    if not 3 <= len(word) <= 4:
        return False
    syllables = [_jamo(ch) for ch in word]
    cho, jung, jong = syllables[0]
    if cho not in FIRST_CHO or jung not in FIRST_JUNG or jong not in FIRST_JONG:
        return False
    for (_, prev_jung, _), (cho, jung, jong) in zip(syllables[:-2], syllables[1:-1]):
        long_vowel = cho == "ㅇ" and jung == prev_jung          # "자아비스", "저어비스" 처럼 길게 끈 발음
        if (cho not in MID_CHO and not long_vowel) or jong != " ":
            return False
    cho, jung, jong = syllables[-1]
    return cho in LAST_CHO and jung in LAST_JUNG and jong == " "


def _edit_distance(a, b):
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _english_like_jarvis(word):
    w = word.lower()
    return len(w) >= 5 and w[0] in "jgy" and _edit_distance(w, "jarvis") <= 2


def find_wake_word(text):
    """호출어가 있으면 (시작, 끝) 위치를, 없으면 None 을 돌려준다."""
    for extra in getattr(config, "WAKEWORD_EXTRA_WORDS", []):
        pos = text.find(extra)
        if pos >= 0:
            return pos, pos + len(extra)

    tokens = list(_TOKEN.finditer(text))
    for i, tok in enumerate(tokens):
        word = tok.group(0)
        if word.isascii():
            if _english_like_jarvis(word):
                return tok.start(), tok.end()
            continue
        if _hangul_like_jarvis(word):
            return tok.start(), tok.end()
        # Whisper 가 "자 비스" 처럼 띄어 쓴 경우: 한 글자 + 다음 단어를 붙여서 확인
        if len(word) == 1 and i + 1 < len(tokens) and not tokens[i + 1].group(0).isascii():
            nxt = tokens[i + 1]
            if text[tok.end():nxt.start()].strip() == "" and _hangul_like_jarvis(word + nxt.group(0)):
                return tok.start(), nxt.end()
    return None
