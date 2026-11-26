#!/usr/bin/env python3
"""
편의점 픽업 주문 웹 서버 (FastAPI + ROS2 브릿지)

실행:
    pip install fastapi uvicorn
    python3 web_server.py            # ROS2가 있으면 로봇 모드, 없으면 자동으로 데모 모드
    python3 web_server.py --mock     # 강제로 데모 모드 (로봇 없이 시뮬레이션)

접속:
    손님 화면  http://<PC IP>:8000/
    직원 화면  http://<PC IP>:8000/#staff

ROS2 토픽 (모두 std_msgs/String, JSON 문자열):
    웹 -> 로봇
      /order          {"order_id": 3, "items": {"tuna_can": 2, "milk_pack": 1}}
      /order_choice   {"order_id": 3, "choice": "partial" | "substitute" | "cancel", "substitute": "spam_can"}
      /scan_request   {"reason": "manual"}
    로봇 -> 웹
      /order_status   {"order_id": 3, "state": "checking"|"picking"|"need_choice"|"done"|"cancelled"|"error",
                       "message": "...", "current": "tuna_can", "done": 1, "total": 3,
                       "shortage": {"id": "tuna_can", "want": 2, "have": 1}, "picked": {"tuna_can": 1},
                       "staff_items": {"cola": 1}}   # done 일 때: 로봇이 안 담는 가상 상품(직원 준비)
      /vision_count   {"tuna_can": 1, "spam_can": 3, ...}   # 카메라로 센 개수
"""
import argparse
import json
import random
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from recommender import Recommender

BASE = Path(__file__).parent
STATIC = BASE / "static"
DATA = BASE / "data"
INVENTORY_FILE = BASE / "inventory.json"
PRODUCTS_FILE = DATA / "products.json"  # 상품 목록 (robot_order_node.py, index.html 과 공유)

LOW_STOCK = 1  # 진열 재고가 이 개수 이하이면 보충 필요
# 재고 필드: stock = 진열 재고(손님 화면에 보이는 수량), warehouse = 창고 재고
# robot: true = 실물(로봇이 매대에서 집음), false = 가상(직원이 준비)
PRODUCTS = json.loads(PRODUCTS_FILE.read_text(encoding="utf-8"))["products"]
IS_ROBOT = {p["id"]: p.get("robot", False) for p in PRODUCTS}


def split_items(items):
    """주문을 로봇이 담을 상품 / 직원이 준비할 상품으로 나누기"""
    robot_items = {k: v for k, v in items.items() if v > 0 and IS_ROBOT.get(k)}
    staff_items = {k: v for k, v in items.items() if v > 0 and not IS_ROBOT.get(k)}
    return robot_items, staff_items


# ---------------------------------------------------------------- 상태 저장
class Store:
    def __init__(self):
        self.lock = threading.Lock()
        self.products = [dict(p) for p in PRODUCTS]
        if INVENTORY_FILE.exists():
            # 저장된 재고(stock, warehouse)만 가져오고 상품 정보는 products.json 기준
            saved = {p["id"]: p for p in json.loads(INVENTORY_FILE.read_text(encoding="utf-8"))}
            for p in self.products:
                for key in ("stock", "warehouse"):
                    if key in saved.get(p["id"], {}):
                        p[key] = saved[p["id"]][key]
        self.vision = {}          # 카메라로 센 개수
        self.vision_time = None
        self.alerts = []          # 직원 알림
        self.status = {"state": "idle"}
        self.next_id = 1

    def save(self):
        INVENTORY_FILE.write_text(json.dumps(self.products, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, pid):
        for p in self.products:
            if p["id"] == pid:
                return p
        return None

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


store = Store()
# 추천: 재고는 store 의 실시간 값 사용 (store.lock 을 잡은 상태에서 호출)
rec = Recommender(PRODUCTS_FILE, DATA / "our_recs.json",
                  stock_fn=lambda pid: (store.get(pid) or {}).get("stock", 0))


# ---------------------------------------------------------------- 로봇 연결 (ROS2 / 데모)
class RobotLink:
    """ROS2 토픽으로 로봇과 통신"""
    def __init__(self):
        import rclpy
        from rclpy.node import Node
        from std_msgs.msg import String
        self.String = String
        rclpy.init()
        self.node = Node("pickup_web_bridge")
        self.pub_order = self.node.create_publisher(String, "/order", 10)
        self.pub_choice = self.node.create_publisher(String, "/order_choice", 10)
        self.pub_scan = self.node.create_publisher(String, "/scan_request", 10)
        self.node.create_subscription(String, "/order_status", self._on_status, 10)
        self.node.create_subscription(String, "/vision_count", self._on_vision, 10)
        threading.Thread(target=rclpy.spin, args=(self.node,), daemon=True).start()
        print("[web] ROS2 로봇 모드")

    def _pub(self, pub, data):
        pub.publish(self.String(data=json.dumps(data, ensure_ascii=False)))

    def send_order(self, oid, items): self._pub(self.pub_order, {"order_id": oid, "items": items})
    def send_choice(self, data):      self._pub(self.pub_choice, data)
    def request_scan(self):           self._pub(self.pub_scan, {"reason": "manual"})

    def _on_status(self, msg):
        handle_status(json.loads(msg.data))

    def _on_vision(self, msg):
        with store.lock:
            store.on_vision(json.loads(msg.data))


class MockRobot:
    """로봇 없이 동작을 흉내 내는 시뮬레이터 (발표 리허설, 웹 개발용)"""
    def __init__(self):
        self.choice_evt = threading.Event()
        self.choice = None
        print("[web] 데모 모드 (로봇 없음)")

    def send_order(self, oid, items):
        threading.Thread(target=self._run, args=(oid, dict(items)), daemon=True).start()

    def send_choice(self, data):
        self.choice = data
        self.choice_evt.set()

    def request_scan(self):
        def scan():
            time.sleep(1.5)
            with store.lock:
                counts = {p["id"]: p["stock"] for p in store.products if p.get("robot")}  # 카메라는 실물 매대만
            if random.random() < 0.3:  # 가끔 불일치 상황을 만들어 시연
                pid = random.choice(list(counts))
                counts[pid] = max(0, counts[pid] - 1)
            with store.lock:
                store.on_vision(counts)
        threading.Thread(target=scan, daemon=True).start()

    def _st(self, **kw):
        handle_status(kw)

    def _run(self, oid, items):
        # robot: true 상품만 로봇이 담고, 가상 상품은 직원 준비 (staff_items)
        items, staff = split_items(items)
        total = sum(items.values())
        self._st(order_id=oid, state="checking", message="매대에서 재고를 확인하고 있어요", done=0, total=total)
        time.sleep(1.5)
        # 재고 확인 (데모: 마지막 점검에서 카메라로 센 값, 없으면 장부 재고를 '보이는 개수'로 사용)
        with store.lock:
            seen = {pid: store.vision.get(pid, store.get(pid)["stock"]) for pid in items}
            store.on_vision(seen)
        for pid, want in list(items.items()):
            have = seen[pid]
            if have < want:
                self.choice_evt.clear()
                self._st(order_id=oid, state="need_choice", done=0, total=total,
                         message=f"재고가 부족해요", shortage={"id": pid, "want": want, "have": have},
                         substitute=None)  # handle_status 에서 추천기로 채움
                self.choice_evt.wait(timeout=120)
                c = (self.choice or {}).get("choice", "cancel")
                if c == "cancel":
                    self._st(order_id=oid, state="cancelled", message="주문이 취소되었어요")
                    return
                items[pid] = have
                if c == "substitute" and self.choice.get("substitute"):
                    sub = self.choice["substitute"]
                    dst = items if IS_ROBOT.get(sub) else staff  # 대체 상품이 가상이면 직원 준비
                    dst[sub] = dst.get(sub, 0) + (want - have)
                items = {k: v for k, v in items.items() if v > 0}
                total = sum(items.values())
        picked, done = {}, 0
        for pid, n in items.items():
            for _ in range(n):
                self._st(order_id=oid, state="picking", current=pid, done=done, total=total,
                         message="상품을 집어서 바구니에 담고 있어요", picked=picked)
                time.sleep(2.0)
                picked[pid] = picked.get(pid, 0) + 1
                done += 1
        self._st(order_id=oid, state="done", done=done, total=total, picked=picked, staff_items=staff,
                 message="담기 완료! 픽업대에서 가져가세요")


def handle_status(st):
    with store.lock:
        prev = store.status
        store.status = st
        if st.get("state") == "need_choice" and st.get("shortage"):
            # 대체 상품은 추천기(our_recs.json substitute + 현재 재고)로 결정
            subs = rec.substitute(st["shortage"]["id"])
            st["substitute"] = subs[0] if subs else None
        if st.get("state") == "done" and prev.get("state") != "done":
            store.apply_picked({**st.get("picked", {}), **st.get("staff_items", {})})
        if st.get("state") == "error":
            store.alert("error", f"로봇 오류: {st.get('message', '')}")


robot = None


# ---------------------------------------------------------------- API
app = FastAPI(title="편의점 픽업 주문")


class OrderReq(BaseModel):
    items: dict[str, int]


class ChoiceReq(BaseModel):
    choice: str
    substitute: str | None = None


class RestockReq(BaseModel):
    id: str
    qty: int
    # move: 창고 -> 진열로 옮기기 / receive: 창고에 입고 / adjust: 진열 재고를 직접 고치기(카메라값 반영)
    mode: str = "move"


@app.get("/api/products")
def products():
    with store.lock:
        return {"products": store.products, "mode": "mock" if isinstance(robot, MockRobot) else "robot"}


@app.post("/api/order")
def order(req: OrderReq):
    items = {k: v for k, v in req.items.items() if v > 0}
    if not items:
        raise HTTPException(400, "담은 상품이 없어요")
    with store.lock:
        if store.status.get("state") in ("checking", "picking", "need_choice"):
            raise HTTPException(409, "로봇이 다른 주문을 처리 중이에요. 잠시 후 다시 시도해 주세요")
        for pid, n in items.items():
            p = store.get(pid)
            if not p:
                raise HTTPException(400, f"없는 상품: {pid}")
            if not p.get("robot") and n > p["stock"]:  # 가상 상품은 카메라 확인이 없으므로 장부로 확인
                raise HTTPException(400, f"{p['name']} 재고가 부족해요 (남은 {p['stock']}개)")
        oid = store.next_id
        store.next_id += 1
        store.status = {"order_id": oid, "state": "checking", "message": "주문을 로봇에게 보냈어요",
                        "done": 0, "total": sum(split_items(items)[0].values())}
    robot.send_order(oid, items)
    return {"order_id": oid}


@app.get("/api/status")
def status():
    with store.lock:
        return store.status


@app.post("/api/choice")
def choice(req: ChoiceReq):
    with store.lock:
        oid = store.status.get("order_id")
    robot.send_choice({"order_id": oid, "choice": req.choice, "substitute": req.substitute})
    return {"ok": True}


@app.get("/api/recommend")
def recommend(basket: str = ""):
    ids = [b for b in basket.split(",") if b]
    with store.lock:
        return {"items": rec.for_basket(ids)}


@app.get("/api/recommend/home")
def recommend_home():
    with store.lock:
        return {"items": rec.home()}


@app.get("/api/inventory")
def inventory():
    with store.lock:
        rows = []
        for p in store.products:
            seen = store.vision.get(p["id"])
            if seen is not None and seen != p["stock"]:
                state = "mismatch"
            elif p["stock"] <= LOW_STOCK:
                state = "low" if p["warehouse"] > 0 else "order"  # order: 창고에도 없어 발주 필요
            else:
                state = "ok"
            rows.append({**p, "vision": seen, "state": state})
        return {"rows": rows, "vision_time": store.vision_time, "alerts": store.alerts,
                "robot": store.status}


@app.post("/api/scan")
def scan():
    robot.request_scan()
    return {"ok": True}


@app.post("/api/restock")
def restock(req: RestockReq):
    with store.lock:
        p = store.get(req.id)
        if not p:
            raise HTTPException(404, "없는 상품")
        if req.mode == "move":
            n = min(req.qty, p["warehouse"]) if req.qty > 0 else max(req.qty, -p["stock"])
            if n == 0:
                raise HTTPException(400, "창고 재고가 없어요" if req.qty > 0 else "진열 재고가 없어요")
            p["warehouse"] -= n
            p["stock"] += n
            text = f"{p['name']} 창고 -> 진열 {n:+d}개 (진열 {p['stock']}개 / 창고 {p['warehouse']}개)"
        elif req.mode == "receive":
            p["warehouse"] = max(0, p["warehouse"] + req.qty)
            text = f"{p['name']} 창고 입고 {req.qty:+d}개 (창고 {p['warehouse']}개)"
        elif req.mode == "adjust":
            p["stock"] = max(0, p["stock"] + req.qty)
            text = f"{p['name']} 진열 재고 {req.qty:+d}개 수정 (진열 {p['stock']}개)"
        else:
            raise HTTPException(400, f"알 수 없는 mode: {req.mode}")
        store.vision.pop(req.id, None)
        store.save()
        store.alert("info", text)
    return {"ok": True}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/data", StaticFiles(directory=DATA), name="data")  # products.json, our_recs.json (브라우저 데모용)


@app.get("/manifest.json")
def manifest():
    return FileResponse(STATIC / "manifest.json")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true", help="로봇 없이 데모 모드로 실행")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    if args.mock:
        robot = MockRobot()
    else:
        try:
            robot = RobotLink()
        except Exception as e:  # rclpy가 없거나 초기화 실패
            print(f"[web] ROS2 연결 실패 ({e}) -> 데모 모드로 전환")
            robot = MockRobot()
    uvicorn.run(app, host="0.0.0.0", port=args.port)
