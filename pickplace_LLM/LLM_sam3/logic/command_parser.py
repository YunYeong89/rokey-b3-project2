"""음성 문장 → 명령 목록 [Command(품목, 개수, 행동)] 추출.

- LLMParser  : 실습(keyword_extraction.py)처럼 LangChain + gpt-4o 로 추출
- RuleParser : 키워드/정규식 기반 추출 (API 없이 동작, LLM 실패 시 대체용)
- CommandParser : LLM 을 먼저 쓰고, 실패하면 규칙 기반으로 대체

예) "라면 하나 추가해주세요" → [Command(item="라면", count=1, action="추가")]
"""
import json
import re
from dataclasses import dataclass
from typing import List, Optional

import config


@dataclass
class Command:
    item: Optional[str]     # 표준 품목명 (목록에 없는 품목이면 들은 그대로)
    count: int              # 1, 2, ... / config.ALL(-1) = 전부
    action: Optional[str]   # config.ACTION_ADD / config.ACTION_REMOVE / None(모름)

    def __str__(self):
        count = "전부" if self.count == config.ALL else f"{self.count}개"
        return f"품목: {self.item}, 개수: {count}, 행동: {self.action}"


# ------------------------------------------------------------------ 정규화 helpers
def _squash(text):
    return re.sub(r"\s+", "", str(text)).lower()


_ALIAS_TO_ITEM = {_squash(alias): item
                  for item, aliases in config.ITEM_ALIASES.items() for alias in aliases}


def normalize_item(raw):
    if raw is None:
        return None
    key = _squash(raw)
    if not key or key in ("null", "none"):
        return None
    return _ALIAS_TO_ITEM.get(key, str(raw).strip())


def normalize_action(raw):
    if raw is None:
        return None
    key = _squash(raw)
    if key in (config.ACTION_ADD, "add", "추가해줘", "넣기", "담기"):
        return config.ACTION_ADD
    if key in (config.ACTION_REMOVE, "remove", "빼줘", "빼", "제거", "삭제"):
        return config.ACTION_REMOVE
    return None


def normalize_count(raw):
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 1
    return config.ALL if value < 0 else value


# ------------------------------------------------------------------ 규칙 기반
_HANGUL = "가-힣"
_COUNTER = r"(?:개|캔|봉지|봉|병|팩|통|묶음|박스|줄)"
_NATIVE = {"한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "석": 3, "셋": 3, "네": 4, "넉": 4,
           "넷": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10}
_SINO = {"일": 1, "이": 2, "삼": 3, "사": 4, "오": 5, "육": 6, "칠": 7, "팔": 8, "구": 9, "십": 10}
_NB = rf"(?<![{_HANGUL}])"   # 앞 글자가 한글이 아닐 때만 (예: '인터넷'의 '넷' 제외)

# (정규식, 값 변환) — 위에서부터 차례로 찾고, 찾은 부분은 가려서 중복 매칭을 막는다
_COUNT_RULES = [
    (re.compile(rf"(\d+)\s*{_COUNTER}?\s*(씩)?"), lambda w: int(w)),
    (re.compile(rf"{_NB}(한|두|세|석|네|넉|다섯|여섯|일곱|여덟|아홉|열)\s*{_COUNTER}\s*(씩)?"),
     lambda w: _NATIVE[w]),
    (re.compile(rf"{_NB}(하나|둘|셋|넷|다섯|여섯|일곱|여덟|아홉)\s*(씩)?"), lambda w: _NATIVE[w]),
    (re.compile(rf"{_NB}(일|이|삼|사|오|육|칠|팔|구|십)\s*개\s*(씩)?"), lambda w: _SINO[w]),
    (re.compile(rf"{_NB}(전부|모두|전체|싹|다)(?![{_HANGUL}])()"), lambda w: config.ALL),
    (re.compile(rf"{_NB}(전부|모두|전체)()"), lambda w: config.ALL),
]

_ACTION_LOOKUP = {kw: action for action, kws in config.ACTION_KEYWORDS.items() for kw in kws}
_ACTION_RE = re.compile("|".join(re.escape(k) for k in sorted(_ACTION_LOOKUP, key=len, reverse=True)))
_ALL_ALIASES = [a for aliases in config.ITEM_ALIASES.values() for a in aliases]
_ITEM_RE = re.compile("|".join(re.escape(a) for a in sorted(_ALL_ALIASES, key=len, reverse=True)),
                      re.IGNORECASE)


def _mask(text, start, end):
    return text[:start] + "□" * (end - start) + text[end:]


class RuleParser:
    name = "rule"

    def parse(self, text) -> List[Command]:
        masked = text

        # 1) 품목 — 먼저 찾아서 가린다 ('빼빼로'의 '빼'가 행동으로 잡히지 않게)
        items = []
        for m in _ITEM_RE.finditer(masked):
            items.append((m.start(), m.end(), normalize_item(m.group(0))))
        for s, e, _ in items:
            masked = _mask(masked, s, e)

        # 2) 개수
        counts = []   # (start, end, value, 씩 여부)
        for pattern, convert in _COUNT_RULES:
            for m in pattern.finditer(masked):
                counts.append((m.start(), m.end(), convert(m.group(1)), bool(m.group(2))))
                masked = _mask(masked, m.start(), m.end())
        counts.sort()

        # 3) 행동
        actions = [(m.start(), m.end(), _ACTION_LOOKUP[m.group(0)]) for m in _ACTION_RE.finditer(masked)]

        if not items:
            # 품목은 못 알아들었지만 행동은 들렸다 → 로봇이 다시 물어볼 수 있게 남긴다
            if actions:
                return [Command(None, counts[0][2] if counts else 1, actions[0][2])]
            return []

        commands, used = [], set()
        for i, (s, e, item) in enumerate(items):
            prev_end = items[i - 1][1] if i > 0 else 0
            next_start = items[i + 1][0] if i + 1 < len(items) else len(text)

            # 행동: 품목 뒤에 처음 나오는 행동, 없으면 앞쪽의 마지막 행동
            after = [a for a in actions if a[0] >= e]
            before = [a for a in actions if a[1] <= s]
            action = after[0][2] if after else (before[-1][2] if before else None)
            action_pos = after[0][0] if after else len(text)

            # 개수: 품목 바로 뒤(다음 품목 전) → 품목 바로 앞 → 뒤쪽의 '~씩' → 기본 1개
            count = None
            for c in counts:
                if e <= c[0] < next_start and c[0] not in used:
                    count = c
                    break
            if count is None:
                for c in reversed(counts):
                    if prev_end <= c[0] and c[1] <= s and c[0] not in used:
                        count = c
                        break
            if count is None:
                for c in counts:
                    if e <= c[0] < action_pos and c[3]:
                        count = c
                        break
            if count is not None and not count[3]:
                used.add(count[0])
            commands.append(Command(item, count[2] if count else 1, action))
        return commands


# ------------------------------------------------------------------ LLM 기반 (실습 방식)
_PROMPT = """
당신은 편의점 로봇의 음성 명령 해석기입니다. 사용자의 문장에서 품목, 개수, 행동을 추출하세요.

<품목 리스트>
- 콜라, 사이다, 쫄병, 빼빼로, 스팸, 참치캔, 라면

<행동 리스트>
- 추가 : 장바구니에 넣기 (예: 추가해줘, 넣어줘, 담아줘, 주세요)
- 빼기 : 장바구니에서 빼기 (예: 빼줘, 제거해줘, 꺼내줘)

<규칙>
- item 은 반드시 품목 리스트의 표기로 바꿔 쓰세요. (예: 코카콜라 → 콜라, 참치 → 참치캔, 신라면 → 라면, 쫄병스낵/졸병 → 쫄병)
- 입력은 음성 인식(STT) 결과라서 발음이 비슷한 다른 글자로 적혀 있을 수 있습니다.
  발음이 비슷하면 품목 리스트의 품목으로 고쳐 쓰세요. (예: 스캠 → 스팸, 소병/졸병 → 쫄병, 빼빼루 → 빼빼로, 라멘 → 라면)
- 품목 리스트에 없고 발음도 비슷하지 않은 품목이면 들은 그대로 item 에 쓰세요.
- 행동이 문장 끝에 한 번만 나오면, 앞에 나온 모든 품목에 같은 행동을 적용하세요.
  (예: "콜라 두 개만 참치캔 하나 넣어줘" → 콜라, 참치캔 모두 추가)
- '빼빼로'의 '빼'는 행동이 아니라 품목 이름입니다.
- count 는 정수입니다. (한 개/하나 → 1, 두 개/둘 → 2, 세 개/셋 → 3 ...) 개수를 말하지 않았으면 1.
- '다', '전부', '모두'처럼 전부를 뜻하면 count 는 -1.
- 품목은 말했지만 행동을 알 수 없으면 빈 배열이 아니라 action 을 null 로 출력하세요.
- 명령이 여러 개면 말한 순서대로 모두 출력하세요. 품목도 행동도 없으면 [] 를 출력하세요.

<출력 형식>
- 설명 없이 JSON 배열만 출력하세요: [{{"item": "품목", "count": 정수, "action": "추가" 또는 "빼기"}}]

<예시>
- 입력: "라면 하나 추가해주세요"
  출력: [{{"item": "라면", "count": 1, "action": "추가"}}]
- 입력: "빼빼로 두 개 빼줘"
  출력: [{{"item": "빼빼로", "count": 2, "action": "빼기"}}]
- 입력: "콜라 두 개랑 참치 하나 넣어줘"
  출력: [{{"item": "콜라", "count": 2, "action": "추가"}}, {{"item": "참치캔", "count": 1, "action": "추가"}}]
- 입력: "사이다 하나 추가하고 스팸은 다 빼줘"
  출력: [{{"item": "사이다", "count": 1, "action": "추가"}}, {{"item": "스팸", "count": -1, "action": "빼기"}}]
- 입력: "스팸 하나"
  출력: [{{"item": "스팸", "count": 1, "action": null}}]

<사용자 입력>
"{user_input}"
"""


class LLMParser:
    name = "llm"

    def __init__(self, api_key, model=config.OPENAI_MODEL):
        from langchain_core.prompts import PromptTemplate
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(model=model, temperature=0, api_key=api_key, timeout=20, max_retries=1)
        prompt = PromptTemplate(input_variables=["user_input"], template=_PROMPT)
        self.chain = prompt | llm

    def parse(self, text) -> List[Command]:
        content = self.chain.invoke({"user_input": text}).content.strip()
        content = re.sub(r"^```(?:json)?|```$", "", content, flags=re.MULTILINE).strip()
        data = json.loads(content)
        if isinstance(data, dict):
            data = [data]
        return [Command(normalize_item(d.get("item")),
                        normalize_count(d.get("count", 1)),
                        normalize_action(d.get("action")))
                for d in data if isinstance(d, dict)]


class CommandParser:
    """LLM(gpt-4o) 으로 해석하고, 실패하거나 결과가 없으면 규칙 기반으로 대체한다."""

    def __init__(self, api_key):
        self.rule = RuleParser()
        self.llm = LLMParser(api_key)

    def parse(self, text) -> List[Command]:
        try:
            commands = self.llm.parse(text)
            if commands:
                print(f"[parser:llm] {text!r} → {len(commands)}개 명령")
                return commands
        except Exception as e:  # 네트워크/크레딧/형식 오류 → 규칙 기반으로 대체
            print(f"[parser] LLM 해석 실패 → 규칙 기반 사용 ({type(e).__name__}: {e})")
        commands = self.rule.parse(text)
        print(f"[parser:rule] {text!r} → {len(commands)}개 명령")
        return commands
