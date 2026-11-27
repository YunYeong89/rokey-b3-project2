"""
상품 추천 (서버 pickup/service.py, robot_order_node.py 가 함께 사용)

추천 목적별로 알고리즘을 나눔 (Switching Hybrid)
  첫 화면  home()       기존 고객: R(재구매) Top 3  "평소 자주 구매하시는 상품"
                        신규 고객: Popularity Top 3 "인기 상품 추천"
                        R 후보가 3개가 안 되면 인기 상품으로 보충
  장바구니 for_basket() S(같은 코너 상품) -> C(다른 코너) -> 인기 상품 순으로 Top 3
                        "함께 구매하면 좋은 상품"
  재고 부족 substitute() 대체 상품 (our_recs.json, 기존과 같음)

오프라인 실험(Instacart 76,781건, Hit@3): R 62.94% > Popularity 35.92% > S 32.70% > C 23.04%,
Grid Search 최적 가중치 R=1, S=0, C=0 -> 가중합 대신 화면별로 나눠서 사용.
R 은 재구매에 강하고(75.91%) 새 상품에 약함(4.01%) -> 새 상품 탐색은 장바구니의 S, C 가 맡음.

입력
  data/products.json                 판매 상품 (id, name, emoji, stock, aisle, robot ...)
  data/recommend/hybrid_scores_optimized.json  Colab 분석 결과 (pattern_analysis_v3)
      item_aisle {pid: Instacart aisle_id}, popularity {pid: 주문 비율},
      s_pairs [{"source": pid, "target": pid, "score"}]        같은 코너 상품 -> 상품
      c_pairs [{"source": aisle_id, "target": aisle_id, "score"}] 다른 코너 -> 코너
      score = (1 - lift_weight) * confidence + lift_weight * max(0, 1 - 1/lift)   (lift_weight = 0.5)
      동시 구매 min_count(=200) 건 이상인 관계만 들어 있음
  data/recommend/same_aisle_scores.json, cross_aisle_scores.json  관계별 confidence, lift (추천 이유 표시, lift > 1 거르기)
  data/recommend/our_recs.json       대체 상품, (hybrid 파일이 없을 때) 인기도

R(재구매) 은 Instacart 고객이 아니라 이 웹의 실제 고객 이력(pickup/history.py)으로 계산해서
home(history=...) 로 넘겨 받음. 재고는 서버의 실시간 값을 써야 하므로 stock_fn(pid) -> int 로 받음.

데이터 파일이 없거나 깨져도 예외 없이 인기 상품(그것도 없으면 재고 순)으로 동작.
점수·순서는 _rank() 하나에 모여 있어서, 나중에 밴딧(클릭/구매 반응으로 보정)을 붙일 때 여기만 바꾸면 됨.
"""
import json
import random
from pathlib import Path

from .config import CROSS_FILE, HYBRID_FILE, PRODUCTS_FILE, RECS_FILE, SAME_FILE

TOP_K = 3
NEW_ITEM = "new_item"        # 콜드 스타트 실험용 상품 (분석 데이터에 없음)
NEW_ITEM_RATE = 0.0          # 홈 추천에 new_item 을 끼워 넣을 확률 (예전 0.2. 새 방식은 Top 3 을 그대로 보여 주므로 꺼 둠)
MIN_C_LIFT = 1.0             # C(다른 코너)는 lift 가 이보다 큰 관계만 (lift < 1 은 오히려 같이 안 사는 관계)
ALGO_RANK = {"R": 3, "S": 2, "C": 1, "popular": 0}   # 같은 목록 안에서의 순서
LEVEL = {"R": "personal", "S": "strong", "C": "weak", "popular": "popular"}  # 화면 색 (index.html .chip.*)


def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        print(f"[recommender] {Path(path).name} 없음 -> 이 데이터 없이 동작")
    except (ValueError, OSError) as e:
        print(f"[recommender] {Path(path).name} 읽기 실패 ({e}) -> 이 데이터 없이 동작")
    return default


def _wa(word):
    """'콜라' -> '와', '참치캔' -> '과' (받침 여부)"""
    ch = word[-1] if word else ""
    return "과" if "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 else "와"


class Recommender:
    def __init__(self, products_file=PRODUCTS_FILE, recs_file=RECS_FILE, stock_fn=None, rng=None,
                 hybrid_file=HYBRID_FILE, same_file=SAME_FILE, cross_file=CROSS_FILE):
        catalog = _load_json(products_file, {"products": []})
        self.products = {p["id"]: p for p in catalog.get("products", [])}
        self.recs = _load_json(recs_file, {})        # substitute, 예전 인기도
        hy = _load_json(hybrid_file, {})
        self.stock_fn = stock_fn or (lambda pid: self.products.get(pid, {}).get("stock", 0))
        self.rng = rng or random.Random()
        self.min_count = hy.get("min_count", 200)
        self.lift_weight = hy.get("lift_weight", 0.5)

        # 인기도: 분석 결과(27종) 우선, 없으면 our_recs.json
        self.pop = {pid: v for pid, v in (hy.get("popularity") or {}).items() if v}
        for pid, r in self.recs.items():
            if pid not in self.pop and (r or {}).get("popularity"):
                self.pop[pid] = r["popularity"]

        # 상품 -> Instacart 코너 id, 코너 -> 판매 상품들
        self.item_aisle = {pid: a for pid, a in (hy.get("item_aisle") or {}).items() if pid in self.products}
        self.aisle_items = {}
        for pid, a in self.item_aisle.items():
            self.aisle_items.setdefault(a, []).append(pid)

        # 관계별 confidence / lift (추천 이유, C 의 lift > 1 거르기)
        def index(path, scope):
            return {(r["source"], r["target"]): r for r in _load_json(path, [])
                    if isinstance(r, dict) and r.get("scope") == scope}
        same = index(same_file, hy.get("s_scope", "aisles_1_5"))
        cross = index(cross_file, hy.get("c_scope", "aisles_2_5"))

        # S: 상품 -> [(상품, score, conf, lift)]   C: 코너 -> [(코너, score, conf, lift)]
        self.s_pairs, self.c_pairs = {}, {}
        for p in hy.get("s_pairs") or []:
            r = same.get((p["source"], p["target"]), {})
            if p["source"] in self.products and p["target"] in self.products:
                self.s_pairs.setdefault(p["source"], []).append(
                    (p["target"], p["score"], r.get("confidence"), r.get("lift")))
        for p in hy.get("c_pairs") or []:
            r = cross.get((p["source"], p["target"]), {})
            if r.get("lift") is not None and r["lift"] <= MIN_C_LIFT:
                continue
            self.c_pairs.setdefault(p["source"], []).append((p["target"], p["score"], r.get("confidence"), r.get("lift")))

    # ------------------------------------------------------------ 도우미
    def stock(self, pid):
        try:
            return self.stock_fn(pid) if pid in self.products else 0
        except Exception:
            return 0

    def popularity(self, pid):
        return self.pop.get(pid, 0.0)

    def sellable(self, pid, seen=()):
        """웹에서 팔고 있고, 재고가 있고, 이미 담거나 추천한 상품이 아님"""
        return pid in self.products and pid not in seen and self.stock(pid) > 0

    @staticmethod
    def _rank(algo, score, context=None):
        """추천 후보 하나의 순서 키 (클수록 앞). 밴딧을 붙일 때 여기만 바꾸면 됨.
        algo : R / S / C / popular,  score : R 점수, S·C 연관 점수, 인기도
        context : {"page": "home"} 또는 {"basket": [...]} - 지금은 안 쓰지만 밴딧에서 사용"""
        return (ALGO_RANK.get(algo, 0), score)

    def _item(self, pid, algo, reason, score=None, basis=None):
        p = self.products[pid]
        return {"id": pid, "name": p["name"], "emoji": p["emoji"], "reason": reason,
                "level": LEVEL[algo], "algo": algo, "score": score, "basis": basis or {}}

    def _popular(self, k, seen, used_aisles=None, ctx=None):
        """인기 상품으로 k 개 채우기. 인기도 데이터가 아예 없으면 재고 많은 순"""
        if k <= 0:
            return []
        cands = [pid for pid in self.products if pid != NEW_ITEM and self.sellable(pid, seen)
                 and (self.popularity(pid) > 0 or not self.pop)]
        cands.sort(key=lambda pid: (self._rank("popular", self.popularity(pid), ctx), self.stock(pid)), reverse=True)
        out = []
        for pid in cands:
            a = self.item_aisle.get(pid)
            if used_aisles is not None and a is not None and a in used_aisles:
                continue
            out.append(self._item(pid, "popular", "인기 상품", round(self.popularity(pid), 4)))
            seen.add(pid)
            if used_aisles is not None and a is not None:
                used_aisles.add(a)
            if len(out) >= k:
                break
        return out

    # ------------------------------------------------------------ 첫 화면
    def home(self, k=TOP_K, history=None, exclude=()):
        """첫 화면 추천. history = customer_history.stats() 결과 {"n_orders", "counts", "last"} 또는 None
        이력이 있으면 R(재구매) 순, 모자라면 인기 상품으로 보충. 결과의 algo 로 어떤 방식인지 알 수 있음"""
        ctx = {"page": "home"}
        seen = set(exclude)
        out = []
        n = (history or {}).get("n_orders") or 0
        if n:
            last = history.get("last") or {}
            cands = sorted(((c / n, last.get(pid, 0), self.popularity(pid), pid, c)
                            for pid, c in (history.get("counts") or {}).items()),
                           key=lambda t: (self._rank("R", t[0], ctx), t[1], t[2]), reverse=True)
            for r, _, _, pid, c in cands:
                if not self.sellable(pid, seen):
                    continue
                out.append(self._item(pid, "R", f"주문 {n}번 중 {c}번 구매", round(r, 4),
                                      {"orders_with_item": c, "orders": n}))
                seen.add(pid)
                if len(out) >= k:
                    break
        out += self._popular(k - len(out), seen, ctx=ctx)
        if NEW_ITEM_RATE and self.sellable(NEW_ITEM, seen) and out and self.rng.random() < NEW_ITEM_RATE:
            out = out[:k - 1]
            out.insert(self.rng.randrange(len(out) + 1), self._item(NEW_ITEM, "popular", "새로 들어왔어요"))
        return out

    # ------------------------------------------------------------ 장바구니
    def for_basket(self, basket_ids, k=TOP_K):
        """장바구니 전체를 보고 '함께 구매하면 좋은 상품' Top k.
        1) S: 장바구니 상품과 같은 코너에서 함께 사는 상품 (예: 콜라 -> 오렌지 탄산음료)
        2) C: 장바구니 상품의 코너와 함께 사는 다른 코너의 상품 (코너마다 인기 1개, lift > 1 만)
        3) 근거가 모자라면 인기 상품
        같은 상품이 여러 장바구니 상품과 관련 있으면 점수를 더함. 결과에는 코너당 1개만."""
        basket = [b for b in dict.fromkeys(basket_ids) if b in self.products]
        if not basket:
            return []
        ctx = {"basket": basket}
        seen = set(basket)
        cand = {}  # (pid, algo) -> {"score": 합, "best": (기여 점수, 근거)}

        def add(pid, algo, score, src, conf, lift):
            e = cand.setdefault((pid, algo), {"score": 0.0, "best": (-1, None)})
            e["score"] += score
            if score > e["best"][0]:
                e["best"] = (score, {"from": src, "confidence": conf, "lift": lift})

        for b in basket:
            for t, score, conf, lift in self.s_pairs.get(b, []):
                if self.sellable(t, seen):
                    add(t, "S", score, b, conf, lift)
            for ta, score, conf, lift in self.c_pairs.get(self.item_aisle.get(b), []):
                # 다른 코너에서는 그 코너의 (재고 있는) 가장 인기 상품 하나
                items = [t for t in self.aisle_items.get(ta, []) if self.sellable(t, seen)]
                if items:
                    add(max(items, key=lambda t: (self.popularity(t), self.stock(t))), "C", score, b, conf, lift)

        ranked = sorted(cand.items(), key=lambda kv: self._rank(kv[0][1], kv[1]["score"], ctx), reverse=True)
        out, used_aisles = [], set()
        for (pid, algo), e in ranked:
            a = self.item_aisle.get(pid)
            if pid in seen or (a is not None and a in used_aisles):
                continue
            basis = e["best"][1]
            out.append(self._item(pid, algo, self._reason(algo, basis), round(e["score"], 4), basis))
            seen.add(pid)
            if a is not None:
                used_aisles.add(a)
            if len(out) >= k:
                return out
        return out + self._popular(k - len(out), seen, used_aisles, ctx)

    def _reason(self, algo, basis):
        src = self.products.get(basis.get("from"), {}).get("name", "담은 상품")
        lift = basis.get("lift")
        times = f" {lift:.1f}배" if lift else ""
        if algo == "S":
            return f"{src}{_wa(src)} 같은 코너 · 함께 구매{times}"
        return f"{src}{_wa(src)} 함께 구매{times}"

    # ------------------------------------------------------------ 재고 부족 대체
    def substitute(self, pid):
        """재고 부족 시 대체 상품 후보 (재고 있는 것만, our_recs.json 순서)"""
        return [s for s in (self.recs.get(pid) or {}).get("substitute", [])
                if s in self.products and s != pid and self.stock(s) > 0]
