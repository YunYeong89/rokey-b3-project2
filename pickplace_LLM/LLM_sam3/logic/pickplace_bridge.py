"""LLM_sam3 ↔ pickplace_sam3 연결 — 로봇 동작은 pickplace_sam3 가 한다 (카메라 + SAM3/DINOv3 로 매대를 보고 집어 봉투에).

왜 따로 프로세스인가: 두 폴더 모두 config.py / logic/ / robot/ 이라는 같은 이름을 써서, 한 프로세스에서 같이 import 하면
서로 덮어쓴다. 그래서 pickplace_sam3 는 '작업 프로세스' 하나에서 계속 돌리고 (모델은 처음 한 번만 로드),
LLM 쪽은 Pipe 로 명령만 보낸다.

  bridge = PickplaceBridge(gripper="ros", detector=None)
  bridge.start()                          # 로봇·카메라·인식기 준비 — 끝날 때까지 기다림
  bridge.add({"cola": 2})                 → {"ok": True, "done": {"cola": 2}, "skipped": [["cola", "이유"], ...], "in_bag": {...}}
  bridge.remove("cola", 1)                → {"ok": True, "removed": 1, "note": "다 못 꺼낸 이유", "in_bag": {...}}
  bridge.status()                         → {"ok": True, "in_bag": {"cola": 2}, "returned": 1}
  bridge.close()
실패하면 {"ok": False, "error": "..."} (로봇은 대기 자세로 돌아가려고 한 번 시도)
"""
import multiprocessing as mp
import os
import sys

import config


def _use_pickplace(root):
    """(작업 프로세스) spawn 은 LLM_sam3 의 main·config·logic 을 먼저 불러 둔다 → 지우고 pickplace_sam3 를 앞에"""
    llm_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None) or ""
        if name != "__mp_main__" and os.path.abspath(f).startswith(llm_dir + os.sep):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != llm_dir]
    sys.path.insert(0, root)


def _worker(conn, root, opts):
    """(작업 프로세스) pickplace_sam3 를 불러 명령을 처리한다"""
    for stream in (sys.stdout, sys.stderr):           # 로봇 로그가 LLM 로그와 섞여도 순서대로 바로 보이게
        try:
            stream.reconfigure(line_buffering=True)
        except (AttributeError, ValueError):
            pass
    _use_pickplace(root)
    try:
        import config as pp_config                    # 이제 pickplace_sam3 의 config
        if not os.path.abspath(pp_config.__file__).startswith(root):
            raise RuntimeError(f"pickplace_sam3 의 config 가 아님: {pp_config.__file__}")
        if not opts["yes"] and pp_config.UNTAUGHT_POSITIONS:
            raise RuntimeError(f"아직 티칭하지 않은 위치: {', '.join(pp_config.UNTAUGHT_POSITIONS)} "
                               f"— {root}/teach.py 로 티칭하거나, 예시 좌표로 해 보려면 --yes")
        from logic.pick_place import PickPlace, load_products
        products = load_products()
        from robot.robot_arm import DoosanArm
        from vision.camera import Camera
        from vision.factory import make_detector
        arm = DoosanArm(gripper=opts["gripper"])
        pp = PickPlace(arm, Camera(), make_detector(opts["detector"]), products)
        arm.home()
    except BaseException as e:                       # SystemExit (모델 없음 등) 도 LLM 쪽에 알려 줌
        conn.send({"ok": False, "error": f"pickplace_sam3 준비 실패: {type(e).__name__}: {e}"})
        return
    known = {p["id"] for p in products}

    def in_bag():
        counts = {}
        for p in pp.placed:
            counts[p.pid] = counts.get(p.pid, 0) + 1
        return counts

    conn.send({"ok": True, "ready": True, "products": sorted(known)})
    try:
        while True:
            msg = conn.recv()
            cmd = msg.get("cmd")
            if cmd == "quit":
                break
            try:
                if cmd == "add":
                    items = {pid: int(n) for pid, n in msg["items"].items() if pid in known and int(n) > 0}
                    done = pp.run_order(items)
                    reply = {"ok": True, "done": done, "skipped": [list(s) for s in pp.skipped]}
                elif cmd == "remove":
                    n = pp.remove_from_bag(msg["pid"], int(msg["count"]))
                    reply = {"ok": True, "removed": n, "note": pp.remove_note}
                elif cmd == "status":
                    reply = {"ok": True, "returned": len(pp.returned)}
                elif cmd == "new_bag":
                    pp.new_bag()
                    reply = {"ok": True}
                elif cmd == "clear_returned":
                    pp.clear_returned()
                    reply = {"ok": True}
                else:
                    reply = {"ok": False, "error": f"모르는 명령: {cmd}"}
            except Exception as e:
                reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
                try:
                    arm.home()
                except Exception:
                    pass
            reply["in_bag"] = in_bag()
            conn.send(reply)
    finally:
        arm.shutdown()


class PickplaceBridge:
    def __init__(self, gripper="ros", detector=None, yes=False, root=None):
        self.root = os.path.abspath(root or config.PICKPLACE_DIR)
        if not os.path.exists(os.path.join(self.root, "logic", "pick_place.py")):
            raise SystemExit(f"pickplace_sam3 폴더를 찾지 못했습니다: {self.root} "
                             "(pickplace_LLM 폴더 안에 LLM_sam3 와 pickplace_sam3 가 함께 있어야 함)")
        self.opts = {"gripper": gripper, "detector": detector, "yes": yes}
        self.proc = self.conn = None
        self.products = []

    def start(self):
        ctx = mp.get_context("spawn")                  # 새 파이썬에서 시작 → LLM 쪽 모듈이 섞이지 않음
        self.conn, child = ctx.Pipe()
        self.proc = ctx.Process(target=_worker, args=(child, self.root, self.opts), daemon=True)
        self.proc.start()
        reply = self._wait()
        if not reply.get("ok"):
            raise SystemExit(reply.get("error"))
        self.products = reply.get("products", [])
        print(f"[bridge] pickplace_sam3 준비 완료 ({self.root})")
        return self

    def _wait(self):
        while not self.conn.poll(0.5):
            if not self.proc.is_alive():
                return {"ok": False, "error": f"pickplace_sam3 작업 프로세스가 멈췄습니다 (exit {self.proc.exitcode})"}
        return self.conn.recv()

    def _call(self, **msg):
        if self.proc is None or not self.proc.is_alive():
            return {"ok": False, "error": "pickplace_sam3 작업 프로세스가 없습니다"}
        self.conn.send(msg)
        return self._wait()

    def add(self, items):
        return self._call(cmd="add", items=items)

    def remove(self, pid, count):
        return self._call(cmd="remove", pid=pid, count=count)

    def status(self):
        return self._call(cmd="status")

    def new_bag(self):
        return self._call(cmd="new_bag")

    def clear_returned(self):
        return self._call(cmd="clear_returned")

    def close(self):
        if self.proc is not None and self.proc.is_alive():
            try:
                self.conn.send({"cmd": "quit"})
                self.proc.join(timeout=10)
            except (BrokenPipeError, OSError):
                pass
            if self.proc.is_alive():
                self.proc.terminate()
        self.proc = None
