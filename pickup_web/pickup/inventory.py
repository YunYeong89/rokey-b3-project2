"""재고 (진열 / 창고 / 카메라) + 직원 알림 + 로봇 현재 상태. 항상 store.lock 을 잡고 사용"""
import json
import threading
import time

from .catalog import PRODUCTS
from .config import INVENTORY_FILE, LOW_STOCK


class Store:
    def __init__(self, path=INVENTORY_FILE):
        self.path = path
        self.lock = threading.Lock()
        self.products = [dict(p) for p in PRODUCTS]
        if path.exists():
            # 저장된 재고(stock, warehouse)만 가져오고 상품 정보는 products.json 기준
            saved = {p["id"]: p for p in json.loads(path.read_text(encoding="utf-8"))}
            for p in self.products:
                for key in ("stock", "warehouse"):
                    if key in saved.get(p["id"], {}):
                        p[key] = saved[p["id"]][key]
        self.vision = {}          # 카메라로 센 개수
        self.vision_time = None
        self.alerts = []          # 직원 알림
        self.status = {"state": "idle"}   # 로봇이 마지막으로 보낸 /order_status

    def save(self):
        self.path.write_text(json.dumps(self.products, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, pid):
        for p in self.products:
            if p["id"] == pid:
                return p
        return None

    def stock(self, pid):
        return (self.get(pid) or {}).get("stock", 0)

    def alert(self, level, text):
        self.alerts.insert(0, {"time": time.strftime("%H:%M:%S"), "level": level, "text": text})
        del self.alerts[30:]

    def apply_picked(self, picked):
        for pid, n in picked.items():
            p = self.get(pid)
            if p:
                p["stock"] = max(0, p["stock"] - n)
                if pid in self.vision:
                    self.vision[pid] = max(0, self.vision[pid] - n)
                if p["stock"] <= LOW_STOCK:
                    if p["warehouse"] > 0:
                        self.alert("warn", f"{p['name']} 진열 보충 필요 (진열 {p['stock']}개 / 창고 {p['warehouse']}개)")
                    else:
                        self.alert("error", f"{p['name']} 창고 재고 없음 - 발주 필요 (진열 {p['stock']}개)")
        self.save()

    def on_vision(self, counts):
        self.vision = counts
        self.vision_time = time.strftime("%H:%M:%S")
        for pid, seen in counts.items():
            p = self.get(pid)
            if p and seen != p["stock"]:
                self.alert("error", f"{p['name']} 불일치: 진열 {p['stock']}개 / 카메라 {seen}개")

    def rows(self):
        """직원 화면 재고 표 (state: ok / low 보충 필요 / order 발주 필요 / mismatch 카메라 불일치)"""
        rows = []
        for p in self.products:
            seen = self.vision.get(p["id"])
            if seen is not None and seen != p["stock"]:
                state = "mismatch"
            elif p["stock"] <= LOW_STOCK:
                state = "low" if p["warehouse"] > 0 else "order"  # order: 창고에도 없어 발주 필요
            else:
                state = "ok"
            rows.append({**p, "vision": seen, "state": state})
        return rows

    def restock(self, pid, qty, mode):
        """move: 창고 -> 진열 / receive: 창고 입고 / adjust: 진열 재고 직접 수정(카메라값 반영)
        성공하면 알림 문구, 잘못된 요청이면 ValueError(메시지), 없는 상품이면 KeyError"""
        p = self.get(pid)
        if not p:
            raise KeyError(pid)
        if mode == "move":
            n = min(qty, p["warehouse"]) if qty > 0 else max(qty, -p["stock"])
            if n == 0:
                raise ValueError("창고 재고가 없어요" if qty > 0 else "진열 재고가 없어요")
            p["warehouse"] -= n
            p["stock"] += n
            text = f"{p['name']} 창고 -> 진열 {n:+d}개 (진열 {p['stock']}개 / 창고 {p['warehouse']}개)"
        elif mode == "receive":
            p["warehouse"] = max(0, p["warehouse"] + qty)
            text = f"{p['name']} 창고 입고 {qty:+d}개 (창고 {p['warehouse']}개)"
        elif mode == "adjust":
            p["stock"] = max(0, p["stock"] + qty)
            text = f"{p['name']} 진열 재고 {qty:+d}개 수정 (진열 {p['stock']}개)"
        else:
            raise ValueError(f"알 수 없는 mode: {mode}")
        self.vision.pop(pid, None)
        self.save()
        self.alert("info", text)
        return text
