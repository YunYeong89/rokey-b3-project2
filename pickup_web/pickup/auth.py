"""로그인 계정 + 세션 (data/runtime/users.json)

계정 파일이 없으면 서버가 처음 켜질 때 DEFAULT_ACCOUNTS 로 만듦 (비밀번호는 PBKDF2 해시로만 저장).
  admin / admin1234  관리자: 손님 화면 + 직원·재고 탭
  user1 / user1234   손님:   구매 이력 기반 추천 (구매 이력의 customer_id = 로그인 ID)
로그인 안 한 비회원도 손님 화면은 그대로 사용 (인기 상품 추천, 구매 이력 저장 안 함).

세션은 서버 메모리에만 있음 -> 서버를 다시 켜면 다시 로그인해야 함.

실행 (계정 관리):
    python3 -m pickup.auth list                 # 계정 목록
    python3 -m pickup.auth set <ID> <admin|user> # 계정 추가 / 비밀번호 바꾸기 (비밀번호는 입력 받음)
    python3 -m pickup.auth delete <ID>
"""
import getpass
import hashlib
import json
import secrets
import sys
import threading
import time

from .config import SESSION_HOURS, USERS_FILE
from .history import CUSTOMER_ID_RE

ROLES = ("admin", "user")
DEFAULT_ACCOUNTS = [("admin", "admin1234", "admin"), ("user1", "user1234", "user")]  # 시연용 기본 계정
PBKDF2_ROUNDS = 200_000


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest}"


def check_password(password, stored):
    try:
        _, rounds, salt, digest = stored.split("$")
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    except (ValueError, AttributeError):
        return False
    return secrets.compare_digest(got, digest)


class Accounts:
    def __init__(self, path=USERS_FILE, session_hours=SESSION_HOURS):
        self.path = path
        self.session_sec = session_hours * 3600
        self.lock = threading.Lock()
        self.sessions = {}   # token -> {"username", "role", "expires"}
        if path.exists():
            self.users = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.users = {}
            for name, pw, role in DEFAULT_ACCOUNTS:
                self.users[name] = {"role": role, "password": hash_password(pw)}
            self.save()
            print(f"[web] 기본 계정 만듦: {', '.join(n for n, _, _ in DEFAULT_ACCOUNTS)} ({path.name})")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.users, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def set_user(self, username, password, role):
        if not CUSTOMER_ID_RE.match(username or ""):
            raise ValueError("ID 는 한글·영문·숫자 20자 이내")
        if role not in ROLES:
            raise ValueError(f"role 은 {' / '.join(ROLES)}")
        with self.lock:
            self.users[username] = {"role": role, "password": hash_password(password)}
            self.save()

    def list_users(self):
        """[{"username", "role"}] (비밀번호 해시는 빼고)"""
        with self.lock:
            return [{"username": name, "role": u["role"]} for name, u in self.users.items()]

    def delete_user(self, username):
        with self.lock:
            if self.users.pop(username, None) is None:
                return False
            self.save()
            for t in [t for t, s in self.sessions.items() if s["username"] == username]:
                del self.sessions[t]
            return True

    # ------------------------------------------------------------ 세션
    def login(self, username, password):
        """맞으면 (token, user), 틀리면 None"""
        u = self.users.get(username or "")
        # 없는 ID 여도 해시 계산을 해서 응답 시간으로 ID 존재 여부를 알 수 없게
        ok = check_password(password or "", u["password"] if u else hash_password("x"))
        if not (u and ok):
            return None
        token = secrets.token_urlsafe(24)
        user = {"username": username, "role": u["role"]}
        with self.lock:
            now = time.time()
            for t in [t for t, s in self.sessions.items() if s["expires"] <= now]:  # 만료된 세션 정리
                del self.sessions[t]
            self.sessions[token] = {**user, "expires": now + self.session_sec}
        return token, user

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

    def user(self, token):
        """token 의 사용자 {"username", "role"} (없거나 만료면 None)"""
        if not token:
            return None
        with self.lock:
            s = self.sessions.get(token)
            if not s or s["expires"] <= time.time():
                self.sessions.pop(token, None)
                return None
            return {"username": s["username"], "role": s["role"]}


if __name__ == "__main__":
    acc = Accounts()
    cmd, args = (sys.argv[1] if len(sys.argv) > 1 else ""), sys.argv[2:]
    if cmd == "list":
        for name, u in acc.users.items():
            print(f"{name:20} {u['role']}")
    elif cmd == "set" and len(args) == 2:
        pw = getpass.getpass(f"{args[0]} 비밀번호: ")
        if not pw or pw != getpass.getpass("한 번 더: "):
            sys.exit("비밀번호가 비었거나 서로 달라요")
        acc.set_user(args[0], pw, args[1])
        print(f"{args[0]} ({args[1]}) 저장. 서버를 다시 켜면 반영돼요")
    elif cmd == "delete" and len(args) == 1:
        print("삭제" if acc.delete_user(args[0]) else "없는 계정")
    else:
        print(__doc__)
