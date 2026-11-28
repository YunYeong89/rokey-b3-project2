"""분석 공통: 한국 시간, 기간·상품 필터, '데이터 수집 중' 표시"""
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from ..config import ANALYTICS_TZ_HOURS

KST = timezone(timedelta(hours=ANALYTICS_TZ_HOURS))
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
SEASONS = {12: "겨울", 1: "겨울", 2: "겨울", 3: "봄", 4: "봄", 5: "봄",
           6: "여름", 7: "여름", 8: "여름", 9: "가을", 10: "가을", 11: "가을"}


def kst(ts):
    return datetime.fromtimestamp(ts, KST)


def kst_date(ts):
    return kst(ts).strftime("%Y-%m-%d")


def day_start(d):
    """'YYYY-MM-DD'(한국 날짜) -> 그날 0시의 유닉스 초"""
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=KST).timestamp()


def days_between(start_d, end_d):
    """[start_d, end_d] 한국 날짜 목록 (문자열)"""
    a = datetime.strptime(start_d, "%Y-%m-%d").date()
    b = datetime.strptime(end_d, "%Y-%m-%d").date()
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def week_key(d):
    """'YYYY-MM-DD' -> 그 주 월요일 날짜"""
    x = date.fromisoformat(d)
    return (x - timedelta(days=x.weekday())).isoformat()


def collecting(reason, have=None, need=None):
    """데이터가 부족한 지표: 화면은 '데이터 수집 중'으로 표시"""
    out = {"status": "collecting", "reason": reason}
    if need is not None:
        out.update(have=have, need=need)
    return out


def not_collected(reason):
    """로봇·모듈이 아직 주지 않는 값: 화면은 '미수집'으로 표시"""
    return {"status": "not_collected", "reason": reason}


@dataclass
class Filters:
    """쿼리 파라미터 -> 필터
    range   today / 7d / 30d / all / custom(start, end: YYYY-MM-DD 한국 날짜)
    product 상품 id (쉼표로 여러 개),  category 카테고리 이름
    source  all / robot(실제 로봇) / mock(데모 로봇)"""
    range: str = "30d"
    start: str | None = None
    end: str | None = None
    products: tuple = ()
    category: str | None = None
    source: str = "all"
    gran: str = "day"
    now: float = field(default_factory=time.time)

    @classmethod
    def parse(cls, range="30d", start=None, end=None, product="", category="", source="all", gran="day", now=None):
        f = cls(range=range if range in ("today", "7d", "30d", "all", "custom") else "30d",
                start=start or None, end=end or None,
                products=tuple(p for p in (product or "").split(",") if p),
                category=category or None,
                source=source if source in ("all", "robot", "mock") else "all",
                gran=gran if gran in ("day", "week", "month") else "day",
                now=now or time.time())
        for d in (f.start, f.end):  # 날짜 형식 확인 (틀리면 ValueError -> 400)
            if d:
                datetime.strptime(d, "%Y-%m-%d")
        return f

    def key(self):
        return (self.range, self.start, self.end, self.products, self.category, self.source, self.gran,
                kst_date(self.now))

    def bounds(self, first_ts=None):
        """(시작 유닉스 초 또는 None, 끝 유닉스 초, 시작 날짜, 끝 날짜)"""
        today = kst_date(self.now)
        if self.range == "custom" and (self.start or self.end):
            s = self.start or (kst_date(first_ts) if first_ts else today)
            e = self.end or today
            if s > e:
                s, e = e, s
        elif self.range == "today":
            s = e = today
        elif self.range == "7d":
            s, e = kst_date(self.now - 6 * 86400), today
        elif self.range == "30d":
            s, e = kst_date(self.now - 29 * 86400), today
        else:  # all
            s, e = (kst_date(first_ts) if first_ts else today), today
        return day_start(s), day_start(e) + 86400, s, e

    def label(self, s, e):
        names = {"today": "오늘", "7d": "최근 7일", "30d": "최근 30일", "all": "전체 기간", "custom": "사용자 지정"}
        return f"{names[self.range]} ({s} ~ {e})" if s != e else f"{names[self.range]} ({s})"

    def source_sql(self, col="source"):
        """SQL 조건과 인자"""
        return ("", ()) if self.source == "all" else (f" AND {col}=?", (self.source,))
