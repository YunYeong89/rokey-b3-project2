"""편의점 로봇의 판단 + 실행 (SAM3 + DINOv3 판).

명령(품목/개수/행동)을 받아 먼저 판단하고, 실제 동작은 pickplace_sam3 에 맡긴다 (logic/pickplace_bridge.py):
  - 판매하는 품목인가? 행동(추가/빼기)을 알아들었나? 개수는 적당한가?
  - 추가: 매대를 카메라로 보고(SAM3 + DINOv3) 가까운 것부터 집어 봉투에 넣는다
          → 매대에 없거나 봉투 자리가 없으면 넣은 만큼만 하고 이유를 알려 줌 (재고를 미리 세지 않음 — 카메라가 봄)
  - 빼기: 봉투에 넣은 그 품목을 꺼내 탁자 위 '내려놓는 곳' 에 둔다 (매대 재진열은 직원)
연달아 나온 '추가' 명령은 한 번에 보낸다 → "콜라 두 개랑 라면 하나" 를 한 번 찍고 가까운 순서로 섞어서 집음.
"""
from collections import Counter

import config
from logic.command_parser import Command


def josa(word, pair):
    """받침에 맞는 조사: josa('라면', '을를') → '라면을', josa('콜라', '을를') → '콜라를'"""
    last = word[-1] if word else ""
    has_batchim = "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28 != 0
    return word + (pair[0] if has_batchim else pair[1])


def item_name(pid):
    return config.PID_TO_ITEM.get(pid, pid)


class StoreRobot:
    def __init__(self, bridge):
        self.bridge = bridge
        self.in_bag = Counter()          # 봉투에 든 품목 (pickplace_sam3 가 넣은 기록) {품목: 개수}

    # ---------------------------------------------------------------- 상태
    def basket_summary(self):
        return ", ".join(f"{k} {v}개" for k, v in self.in_bag.items() if v) or "비어 있음"

    def say(self, message):
        print(f"  🤖 {message}")

    def _update(self, reply):
        if "in_bag" in reply:
            self.in_bag = Counter({item_name(pid): n for pid, n in reply["in_bag"].items()})

    # ---------------------------------------------------------------- 판단
    def judge(self, cmd: Command):
        """(실행할 개수, 안내 메시지) 를 돌려준다. 개수가 0 이면 실행하지 않는다."""
        if cmd.item is None:
            return 0, "품목을 알아듣지 못했어요. 다시 말씀해 주세요."
        if cmd.item not in config.ITEMS:
            return 0, f"{josa(cmd.item, '은는')} 판매하지 않는 품목이에요. (판매 품목: {', '.join(config.ITEMS)})"
        if cmd.item not in config.ITEM_TO_PID:
            return 0, f"{josa(cmd.item, '은는')} 아직 로봇이 알아보지 못하는 품목이에요. (config.ITEM_TO_PID)"
        if cmd.action not in (config.ACTION_ADD, config.ACTION_REMOVE):
            return 0, f"{cmd.item} {self._count_text(cmd.count)}, 추가할까요 뺄까요? 다시 말씀해 주세요."
        if cmd.count == 0 or (cmd.count != config.ALL and cmd.count < 0):
            return 0, "개수를 다시 말씀해 주세요."
        if cmd.count != config.ALL and cmd.count > config.MAX_COUNT_PER_COMMAND:
            return 0, f"한 번에 최대 {config.MAX_COUNT_PER_COMMAND}개까지만 옮길 수 있어요."

        if cmd.action == config.ACTION_ADD:
            if cmd.count == config.ALL:
                return 0, f"{cmd.item} 몇 개를 추가할까요? 개수를 말씀해 주세요."
            return cmd.count, f"{cmd.item} {cmd.count}개 추가할게요. 매대를 보고 가까운 것부터 집을게요."

        in_bag = self.in_bag[cmd.item]
        if in_bag == 0:
            return 0, f"봉투에 {josa(cmd.item, '이가')} 없어요."
        if cmd.count == config.ALL:
            return in_bag, f"봉투의 {cmd.item} {in_bag}개를 모두 뺄게요."
        n = min(cmd.count, in_bag)
        if n < cmd.count:
            return n, f"봉투에 {josa(cmd.item, '이가')} {in_bag}개뿐이라 {n}개만 뺄게요."
        return n, f"{cmd.item} {n}개 뺄게요."

    @staticmethod
    def _count_text(count):
        return "전부" if count == config.ALL else f"{count}개"

    # ---------------------------------------------------------------- 실행
    def _add(self, items):
        """items: {품목: 개수} → pickplace_sam3 에 한 번에"""
        reply = self.bridge.add({config.ITEM_TO_PID[i]: n for i, n in items.items()})
        self._update(reply)
        if not reply.get("ok"):
            self.say(f"로봇 동작 중 오류가 나서 멈췄어요: {reply.get('error')}")
            return
        done = reply.get("done", {})
        why = {}
        for pid, reason in reply.get("skipped", []):
            why.setdefault(item_name(pid), []).append(reason)
        for item, n in items.items():
            got = done.get(config.ITEM_TO_PID[item], 0)
            if got >= n:
                self.say(f"{item} {n}개를 봉투에 넣었어요.")
            else:
                reasons = ", ".join(sorted(set(why.get(item, [])))) or "알 수 없는 이유"
                self.say(f"{item} {n}개 중 {got}개만 넣었어요. ({reasons})")

    def _remove(self, item, n):
        reply = self.bridge.remove(config.ITEM_TO_PID[item], n)
        self._update(reply)
        if not reply.get("ok"):
            self.say(f"로봇 동작 중 오류가 나서 멈췄어요: {reply.get('error')}")
            return
        got = reply.get("removed", 0)
        if got >= n:
            self.say(f"{item} {n}개를 꺼내 내려놓는 곳에 두었어요.")
        else:
            self.say(f"{item} {n}개 중 {got}개만 꺼냈어요. ({reply.get('note') or '알 수 없는 이유'})")

    def handle(self, commands):
        if not commands:
            self.say("명령을 알아듣지 못했어요. 예) '라면 하나 추가해주세요'")
            return
        adds = Counter()
        for cmd in commands:
            print(f"  ▶ {cmd}")
            n, message = self.judge(cmd)
            self.say(message)
            if not n:
                continue
            if cmd.action == config.ACTION_ADD:
                adds[cmd.item] += n                   # 연달아 나온 추가는 모아서 한 번에
                continue
            if adds:
                self._add(dict(adds))
                adds.clear()
            self._remove(cmd.item, n)
        if adds:
            self._add(dict(adds))
        print(f"  🧺 봉투: {self.basket_summary()}")
