#!/usr/bin/env python3
"""
상품 추천 (web_server.py, robot_order_node.py 가 함께 사용)

입력
  data/products.json : 상품 목록 (id, name, emoji, stock, aisle, robot ...)
  data/our_recs.json : 상품별 추천 목록 (Instacart 주문 데이터로 계산)
      {"tuna_can": {"popularity": 0.0123, "substitute": ["spam_can"],
                    "recommend": [{"id": "sausage", "lift": 1.15, "count": 2345, "level": "weak"}, ...]}}

level: strong(lift >= 1.2) / weak(1.1 <= lift < 1.2) / popular(인기 상품으로 채움)

재고는 서버의 실시간 값을 써야 하므로 stock_fn(pid) -> int 로 받습니다.
(없으면 products.json 의 stock 값 사용)

점수 계산은 _score() 하나에 모여 있어서, 나중에 밴딧(클릭/구매 반응으로 점수 보정)을
붙일 때 이 함수만 바꾸면 됩니다.
"""
import json
import random
from pathlib import Path

DATA = Path(__file__).parent / "data"
PRODUCTS_FILE = DATA / "products.json"
RECS_FILE = DATA / "our_recs.json"

NEW_ITEM = "new_item"        # 콜드 스타트 실험용 상품
NEW_ITEM_RATE = 0.2          # 홈 추천에 new_item 을 끼워 넣을 확률
LEVEL_RANK = {"strong": 2, "weak": 1, "popular": 0}


class Recommender:
    def __init__(self, products_file=PRODUCTS_FILE, recs_file=RECS_FILE, stock_fn=None, rng=None):
        catalog = json.loads(Path(products_file).read_text(encoding="utf-8"))
        self.products = {p["id"]: p for p in catalog["products"]}
        recs_file = Path(recs_file)
        if recs_file.exists():
            self.recs = json.loads(recs_file.read_text(encoding="utf-8"))
        else:
            print(f"[recommender] {recs_file} 없음 -> 추천 없이 동작")
            self.recs = {}
        self.stock_fn = stock_fn or (lambda pid: self.products.get(pid, {}).get("stock", 0))
        self.rng = rng or random.Random()

    # ------------------------------------------------------------ 도우미
    def stock(self, pid):
        return self.stock_fn(pid) if pid in self.products else 0

    def popularity(self, pid):
        return (self.recs.get(pid) or {}).get("popularity") or 0.0

    def _score(self, pid, entry, context=None):
        """추천 후보 하나의 점수 (클수록 앞). 밴딧을 붙일 때 여기만 바꾸면 됨.

        entry   : our_recs.json 의 recommend 항목 {"id","lift","count","level"} (홈 추천이면 None)
        context : {"basket": [...]} 또는 {"page": "home"} - 지금은 안 쓰지만 밴딧에서 사용
        규칙   : level 단계(strong > weak > popular)가 먼저, 같은 단계 안에서는 lift 높은 순,
                 lift 가 없으면(popular) 인기도 순
        """
        level = self._level(entry)
        lift = (entry or {}).get("lift")
        tie = lift if lift is not None else self.popularity(pid)
        return LEVEL_RANK.get(level, 0) * 100 + tie

    def _item(self, pid, level, reason):
        p = self.products[pid]
        return {"id": pid, "name": p["name"], "emoji": p["emoji"], "reason": reason, "level": level}

    @staticmethod
    def _level(entry):
        """level 이 없으면 lift 로 판정 (strong >= 1.2, weak >= 1.1, 그 외/없음 popular)"""
        if not entry:
            return "popular"
        if entry.get("level"):
            return entry["level"]
        lift = entry.get("lift")
        if lift is None:
            return "popular"
        return "strong" if lift >= 1.2 else "weak" if lift >= 1.1 else "popular"

    @staticmethod
    def _reason(level, lift):
        if level == "strong":
            return f"같이 사는 비율 {lift:.1f}배"
        if level == "weak":
            return "이런 상품은 어때요?"
        return "인기 상품"

    # ------------------------------------------------------------ 추천
    def home(self, k=4):
        """첫 화면 '오늘의 추천': 인기 순, 재고 0 제외, 가끔 new_item 한 자리"""
        ctx = {"page": "home"}
        cands = [pid for pid in self.products if pid != NEW_ITEM and self.stock(pid) > 0]
        cands.sort(key=lambda pid: self._score(pid, None, ctx), reverse=True)
        out = [self._item(pid, "popular", "인기 상품") for pid in cands[:k]]
        if NEW_ITEM in self.products and self.stock(NEW_ITEM) > 0 and k > 0 \
                and self.rng.random() < NEW_ITEM_RATE:
            new = self._item(NEW_ITEM, "new", "새로 들어왔어요")
            out = out[:k - 1]
            out.insert(self.rng.randrange(len(out) + 1), new)
        return out

    def for_basket(self, basket_ids, k=3):
        """장바구니 기반 '함께 사면 좋아요'"""
        basket = set(basket_ids)
        ctx = {"basket": list(basket_ids)}
        # 1) 장바구니 상품들의 recommend 합치기 (같은 상품은 점수(=lift)가 가장 높은 것만)
        #    + 인기 상품(popular) 후보: recommend 가 비었거나 k개가 안 될 때 빈자리를 채움
        entries = [e for b in basket_ids for e in (self.recs.get(b) or {}).get("recommend", [])]
        entries += [{"id": pid, "lift": None, "level": "popular"}
                    for pid in self.products if pid != NEW_ITEM and self.popularity(pid) > 0]
        best = {}
        if basket:
            for e in entries:
                pid = e.get("id")
                if pid not in self.products or pid in basket or self.stock(pid) <= 0:
                    continue
                s = self._score(pid, e, ctx)
                if pid not in best or s > best[pid][0]:
                    best[pid] = (s, e)
        ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)
        # 2) 같은 aisle(예: 참치캔·스팸)은 하나만 - 재고 많은 쪽 (같으면 순위 높은 쪽)
        winner = {}
        for pid, _ in ranked:
            aisle = self.products[pid].get("aisle")
            if aisle and (aisle not in winner or self.stock(pid) > self.stock(winner[aisle])):
                winner[aisle] = pid
        out = []
        for pid, (_, e) in ranked:
            aisle = self.products[pid].get("aisle")
            if aisle and winner[aisle] != pid:
                continue
            level = self._level(e)
            out.append(self._item(pid, level, self._reason(level, e.get("lift"))))
            if len(out) >= k:
                break
        return out

    def substitute(self, pid):
        """재고 부족 시 대체 상품 후보 (재고 있는 것만, our_recs.json 순서)"""
        return [s for s in (self.recs.get(pid) or {}).get("substitute", [])
                if s in self.products and s != pid and self.stock(s) > 0]
