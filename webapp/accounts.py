"""Accounts: users, passwords, sessions, and who owns and who may use what.

Plain standard library, no web framework in here (app.py turns AccountError into an HTTP answer).

- Passwords: scrypt with a random salt per user, compared in constant time; an unknown user costs the same as a
  wrong password; at least 10 characters, no composition rules, nothing from a short list of common passwords.
- Sessions: a random token in an HttpOnly cookie; only its SHA-256 is stored, with an idle and an absolute
  lifetime. They end when the password changes, the account is disabled or deleted.
- Brute force: failures are counted per user and per address, and lock them out for a growing time.
- Admin rules: the last administrator cannot be deleted, demoted or disabled.
- Ownership: every model, result, palette and project has an owner and can be shared with some users or with
  everyone. The owner and the administrators may change a resource, the people it is shared with may use it.
- Every sign-in, failure, password change and account change is logged to `auth.log` (never a password).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ID_PREFIX = "U-"  # an id starts with a capital: no user name can (names are lowercase)
USERNAME = re.compile(r"[a-z0-9][a-z0-9_.-]{1,31}")
ROLES = ("admin", "user")
KINDS = ("model", "result", "palette")
MIN_PASSWORD = 10
MAX_PASSWORD = 128
SESSION_MAX_AGE = 14 * 86400  # however active
SESSION_IDLE = 7 * 86400  # without a request
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
LOCK_AFTER = 5  # failures before a lock-out
LOCK_BASE, LOCK_MAX = 30, 900  # seconds: 30, 60, 120... up to 15 minutes
COMMON_PASSWORDS = {
    "password",
    "password1",
    "password12",
    "password123",
    "123456789",
    "1234567890",
    "12345678910",
    "qwertyuiop",
    "qwerty12345",
    "qwerty123456",
    "azertyuiop",
    "iloveyou123",
    "letmein1234",
    "welcome1234",
    "admin12345",
    "administrator",
    "changeme123",
    "spritestudio",
    "sprite-studio",
    "0123456789",
    "1q2w3e4r5t",
    "abcdefghij",
    "motdepasse",
}


def new_id() -> str:
    return ID_PREFIX + secrets.token_hex(16)


class AccountError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(frozen=True)
class Who:
    name: str
    role: str
    must_change: bool = False
    id: str = ""  # the account's permanent identity (names can be reused after a deletion, ids never are)

    @property
    def admin(self) -> bool:
        return self.role == "admin"


CURRENT: ContextVar[Who | None] = ContextVar("current_user", default=None)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str, n: int = SCRYPT_N, r: int = SCRYPT_R, p: int = SCRYPT_P) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=128 * n * r * 2 + (1 << 20))
    return f"scrypt${n}${r}${p}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt, key = stored.split("$")
        n, r, p = int(n), int(r), int(p)
        calc = hashlib.scrypt(
            password.encode(),
            salt=base64.b64decode(salt),
            n=n,
            r=r,
            p=p,
            dklen=32,
            maxmem=128 * n * r * 2 + (1 << 20),
        )
        return hmac.compare_digest(calc, base64.b64decode(key))
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password("not a real password")  # what an unknown user is checked against: same cost, always false


def check_password_policy(password: Any, username: str = "") -> str:
    if not isinstance(password, str):
        raise AccountError("send a password")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"the password needs at least {MIN_PASSWORD} characters (a few words make a good one)")
    if len(password) > MAX_PASSWORD:
        raise AccountError(f"the password is too long (at most {MAX_PASSWORD} characters)")
    low = password.lower()
    if low in COMMON_PASSWORDS or len(set(low)) < 4:
        raise AccountError("this password is too easy to guess: choose another one")
    if username and username.lower() in low:
        raise AccountError("the password must not contain the user name")
    return password


def clean_username(name: Any) -> str:
    n = str(name or "").strip().lower()
    if not USERNAME.fullmatch(n):
        raise AccountError(
            "a user name is 2 to 32 characters: lowercase letters, digits, . _ -, starting with a letter or digit"
        )
    return n


class Accounts:
    def __init__(self, state: Path) -> None:
        self.state = Path(state)
        self.lock = threading.RLock()
        self._fails: dict[str, list[float]] = {}  # key -> [count, locked_until]
        self._cache: dict[str, tuple[tuple[int, int], Any]] = {}
        self._seen: dict[str, float] = {}  # session hash -> last write of its last_seen
        self.state.mkdir(mode=0o700, parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            self.state.chmod(0o700)
        self.migrate_ids()

    # ---- identity: resources, jobs and folders point at an id, never at a name that could be given again
    def migrate_ids(self) -> None:
        """Gives every account an id and rewrites the names in the ownership and sharing lists to ids (once)."""
        with self.lock:
            users = self.users()
            if not users:
                return
            fresh = False
            for u in users.values():
                if not str(u.get("id", "")).startswith(ID_PREFIX):
                    u["id"] = new_id()
                    fresh = True
            if fresh:
                self._write("users.json", users)
            ids = {n: u["id"] for n, u in users.items()}
            acl = self._acl(True)
            changed = False
            for items in acl.values():
                for e in items.values():
                    owner = e.get("owner", "")
                    if owner and not owner.startswith(ID_PREFIX):
                        e["owner"], changed = ids.get(owner, ""), True
                    shared = e.get("users", [])
                    if any(not str(u).startswith(ID_PREFIX) for u in shared):
                        e["users"], changed = [ids[u] for u in shared if u in ids], True
            if changed:
                self._write("acl.json", acl)

    def id_of(self, who: str) -> str:
        """The id of a user given by name (or an id, returned as it is); "" when there is no such user."""
        if str(who).startswith(ID_PREFIX):
            return str(who)
        return str(self.users().get(str(who), {}).get("id", ""))

    def name_of(self, uid: str) -> str:
        """The name of the user with this id ("" for a user that was removed)."""
        for n, u in self.users().items():
            if u.get("id") == uid:
                return n
        return ""

    # ---- files (0600: hashes and tokens)
    def _read(self, name: str, default: Any, fresh: bool = False) -> Any:
        """A state file. Readers share one parsed copy, parsed again only when the file changed (a list asks for
        the same file for every row), so they must not change it; code that changes it asks for `fresh`."""
        path = self.state / name
        try:
            st = path.stat()
            key = (st.st_mtime_ns, st.st_size)
            hit = None if fresh else self._cache.get(name)
            if hit and hit[0] == key:
                return hit[1]
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return default
        if not fresh:
            self._cache[name] = (key, data)
        return data

    def _write(self, name: str, data: Any) -> None:
        tmp = self.state / (name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=1)
        tmp.replace(self.state / name)

    def audit(self, event: str, **kv: Any) -> None:
        line = (
            time.strftime("%Y-%m-%d %H:%M:%S ")
            + event
            + "".join(f" {k}={str(v)[:80]!r}" for k, v in kv.items() if v not in (None, ""))
        )
        p = self.state / "auth.log"
        with self.lock:
            try:
                if p.exists() and p.stat().st_size > 1_000_000:  # keeps the last part
                    keep = "".join(p.read_text().splitlines(True)[-2000:])
                    with os.fdopen(os.open(p, os.O_WRONLY | os.O_TRUNC, 0o600), "w") as fh:
                        fh.write(keep)
                fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with contextlib.suppress(OSError):
                    os.fchmod(fd, 0o600)
                with os.fdopen(fd, "a") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    # ---- users
    def users(self) -> dict[str, dict]:
        return self._read("users.json", {}, True)

    def has_users(self) -> bool:
        return bool(self.users())

    def who(self, name: str) -> Who | None:
        u = self.users().get(name)
        if not u or u.get("disabled"):
            return None
        return Who(name, u.get("role", "user"), bool(u.get("must_change")), u.get("id", ""))

    def listing(self) -> list[dict]:
        users = self.users()
        counts = self.resource_counts()
        return [
            {
                "username": n,
                "role": u.get("role", "user"),
                "must_change": bool(u.get("must_change")),
                "disabled": bool(u.get("disabled")),
                "created": u.get("created"),
                "last_login": u.get("last_login"),
                "resources": counts.get(n, 0),
            }
            for n, u in sorted(users.items())
        ]

    def _admins(self, users: dict[str, dict]) -> list[str]:
        return [n for n, u in users.items() if u.get("role") == "admin" and not u.get("disabled")]

    def create(self, username: str, password: str, role: str = "user", must_change: bool = True, by: str = "") -> None:
        name = clean_username(username)
        if role not in ROLES:
            raise AccountError("the role is admin or user")
        check_password_policy(password, name)
        with self.lock:
            users = self.users()
            if name in users:
                raise AccountError(f"{name} exists already", 409)
            users[name] = {
                "id": new_id(),
                "hash": hash_password(password),
                "role": role,
                "must_change": bool(must_change),
                "disabled": False,
                "created": time.time(),
                "last_login": None,
            }
            self._write("users.json", users)
        self.audit("user-created", user=name, role=role, by=by)

    def set_password(self, username: str, password: str, must_change: bool = False, by: str = "") -> None:
        check_password_policy(password, username)
        with self.lock:
            users = self.users()
            if username not in users:
                raise AccountError("no such user", 404)
            users[username]["hash"] = hash_password(password)
            users[username]["must_change"] = bool(must_change)
            users[username]["pw_changed"] = time.time()
            self._write("users.json", users)
            self.revoke(username)
        self.audit("password-set", user=username, by=by, must_change=must_change)

    def change_own_password(self, username: str, current: str, new: str) -> None:
        u = self.users().get(username)
        if not u or not verify_password(str(current), u["hash"]):
            self.audit("password-change-refused", user=username)
            raise AccountError("the current password is not right", 403)
        if verify_password(str(new), u["hash"]):
            raise AccountError("the new password must differ from the current one")
        self.set_password(username, new, must_change=False, by=username)

    def update(
        self,
        username: str,
        role: str | None = None,
        must_change: bool | None = None,
        disabled: bool | None = None,
        by: str = "",
    ) -> None:
        with self.lock:
            users = self.users()
            u = users.get(username)
            if not u:
                raise AccountError("no such user", 404)
            was_admin = u.get("role") == "admin" and not u.get("disabled")
            if role is not None:
                if role not in ROLES:
                    raise AccountError("the role is admin or user")
                u["role"] = role
            if must_change is not None:
                u["must_change"] = bool(must_change)
            if disabled is not None:
                u["disabled"] = bool(disabled)
            if was_admin and not (u.get("role") == "admin" and not u.get("disabled")) and not self._admins(users):
                raise AccountError("there must be at least one active administrator", 409)
            self._write("users.json", users)
            if disabled or role is not None:
                self.revoke(username)
        self.audit("user-updated", user=username, role=role, must_change=must_change, disabled=disabled, by=by)

    def delete(self, username: str, by: str = "") -> None:
        with self.lock:
            users = self.users()
            if username not in users:
                raise AccountError("no such user", 404)
            rest = {n: u for n, u in users.items() if n != username}
            if users[username].get("role") == "admin" and not self._admins(rest):
                raise AccountError("there must be at least one active administrator", 409)
            self._write("users.json", rest)
            self.revoke(username)
        self.audit("user-deleted", user=username, by=by)

    # ---- first administrator
    def setup_code(self) -> str | None:
        """The one-time code that allows creating the first administrator (None once there is one)."""
        if self.has_users():
            return None
        p = self.state / "setup_code"
        try:
            return p.read_text().strip() or None
        except OSError:
            pass
        code = secrets.token_urlsafe(9)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(code)
        return code

    def setup(self, username: str, password: str, code: str, ip: str = "") -> None:
        key = f"setup:{ip}"
        self._check_lock([key])
        with self.lock:
            want = self.setup_code()
            if want is None:
                raise AccountError("the application is already set up", 409)
            if not hmac.compare_digest(str(code).strip().encode(), want.encode()):
                self._fail([key])
                self.audit("setup-refused", ip=ip)
                raise AccountError("the setup code is not right", 403)
            self.create(username, password, role="admin", must_change=False, by="setup")
            with contextlib.suppress(OSError):
                (self.state / "setup_code").unlink()
        self._ok([key])

    # ---- sign in and sessions
    def _check_lock(self, keys: list[str]) -> None:
        now = time.time()
        for k in keys:
            f = self._fails.get(k)
            if f and f[1] > now:
                raise AccountError(f"too many attempts: try again in {int(f[1] - now) + 1} seconds", 429)

    def _fail(self, keys: list[str]) -> None:
        now = time.time()
        for k in keys:
            f = self._fails.setdefault(k, [0, 0])
            f[0] += 1
            if f[0] >= LOCK_AFTER:
                f[1] = now + min(LOCK_MAX, LOCK_BASE * 2 ** (f[0] - LOCK_AFTER))
        if len(self._fails) > 5000:  # a flood of names must not grow this for ever
            self._fails = {k: v for k, v in self._fails.items() if v[1] > now}

    def _ok(self, keys: list[str]) -> None:
        for k in keys:
            self._fails.pop(k, None)

    def login(self, username: str, password: str, ip: str = "", agent: str = "") -> tuple[str, Who]:
        name = str(username or "").strip().lower()[:64]
        keys = [f"u:{name}", f"ip:{ip}"]
        self._check_lock(keys)
        u = self.users().get(name)
        ok = verify_password(str(password or "")[: MAX_PASSWORD * 2], u["hash"] if u else _DUMMY)
        if not (u and ok) or u.get("disabled"):
            self._fail(keys)
            self.audit("login-failed", user=name, ip=ip)
            raise AccountError("wrong user name or password", 401)
        self._ok(keys)
        token = self.new_session(name, ip, agent)
        with self.lock:
            users = self.users()
            users[name]["last_login"] = time.time()
            self._write("users.json", users)
        self.audit("login", user=name, ip=ip)
        return token, Who(name, u.get("role", "user"), bool(u.get("must_change")), u.get("id", ""))

    def new_session(self, name: str, ip: str = "", agent: str = "") -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock:
            sessions = self._read("sessions.json", {}, True)
            sessions = {h: s for h, s in sessions.items() if self._alive(s, now)}
            sessions[self._hash(token)] = {
                "user": name,
                "uid": self.id_of(name),
                "created": now,
                "seen": now,
                "ip": ip,
                "agent": agent[:120],
            }
            self._write("sessions.json", sessions)
        return token

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _alive(s: dict, now: float) -> bool:
        return now - s["created"] < SESSION_MAX_AGE and now - s["seen"] < SESSION_IDLE

    def session(self, token: str) -> Who | None:
        if not token:
            return None
        h = self._hash(token)
        now = time.time()
        sessions = self._read("sessions.json", {}, True)
        s = sessions.get(h)
        if not s or not self._alive(s, now):
            return None
        who = self.who(s["user"])
        if not who or (s.get("uid") and s["uid"] != who.id):  # an account of the same name, but not the same one
            return None
        if now - self._seen.get(h, 0) > 300:  # the last-seen time is written at most every 5 minutes
            with self.lock:
                sessions = self._read("sessions.json", {}, True)
                if h in sessions:
                    sessions[h]["seen"] = now
                    self._write("sessions.json", sessions)
                    self._seen[h] = now
        return who

    def logout(self, token: str) -> None:
        with self.lock:
            sessions = self._read("sessions.json", {}, True)
            s = sessions.pop(self._hash(token), None)
            self._write("sessions.json", sessions)
        if s:
            self.audit("logout", user=s["user"])

    def revoke(self, username: str, keep_token: str = "") -> None:
        """Ends the sessions of a user (all but the one of `keep_token`)."""
        keep = self._hash(keep_token) if keep_token else ""
        with self.lock:
            sessions = self._read("sessions.json", {}, True)
            sessions = {h: s for h, s in sessions.items() if s["user"] != username or h == keep}
            self._write("sessions.json", sessions)

    # ---- ownership and sharing
    def _acl(self, fresh: bool = False) -> dict:
        return self._read("acl.json", {}, fresh)

    def entry(self, kind: str, key: str) -> dict | None:
        return self._acl().get(kind, {}).get(key)

    def owner(self, kind: str, key: str) -> str:
        e = self.entry(kind, key)
        return self.name_of(e.get("owner", "")) if e else ""

    def claim(self, kind: str, key: str, owner: str, replace: bool = False) -> None:
        """Records the owner of a new resource (an existing owner is kept unless `replace`)."""
        with self.lock:
            acl = self._acl(True)
            e = acl.setdefault(kind, {}).get(key)
            if e and e.get("owner") and not replace:
                return
            owner = self.id_of(owner)  # (given by name or id)
            if not owner:
                raise AccountError("no such user", 404)
            acl[kind][key] = {"owner": owner, "users": (e or {}).get("users", []), "all": (e or {}).get("all", False)}
            self._write("acl.json", acl)

    def forget(self, kind: str, key: str) -> None:
        with self.lock:
            acl = self._acl(True)
            if acl.get(kind, {}).pop(key, None) is not None:
                self._write("acl.json", acl)

    def rename(self, kind: str, old: str, new: str) -> None:
        with self.lock:
            acl = self._acl(True)
            e = acl.get(kind, {}).pop(old, None)
            if e is not None:
                acl[kind][new] = e
                self._write("acl.json", acl)

    def share(self, kind: str, key: str, users: list[str], everyone: bool, by: str = "") -> dict:
        if kind not in KINDS:
            raise AccountError("unknown kind of resource", 404)
        known = self.users()
        with self.lock:
            acl = self._acl(True)
            e = acl.get(kind, {}).get(key)
            if not e:
                raise AccountError("this resource has no owner yet: an administrator can give it one", 409)
            names, ids = [], []
            for n in users:
                n = str(n).strip().lower()
                uid = known.get(n, {}).get("id", "")
                if n and uid and uid != e["owner"] and uid not in ids:
                    names.append(n)
                    ids.append(uid)
            e["users"], e["all"] = ids, bool(everyone)
            self._write("acl.json", acl)
        self.audit("shared", kind=kind, key=key, users=",".join(names), everyone=everyone, by=by)
        return e

    def access(self, who: Who | None, kind: str, key: str) -> str | None:
        """ "write" for the owner and the administrators, "read" for those it is shared with, else None."""
        if not who:
            return None
        if who.admin:
            return "write"
        e = self.entry(kind, key)
        if not e:
            return None
        if who.id and e.get("owner") == who.id:
            return "write"
        if e.get("all") or (who.id and who.id in e.get("users", [])):
            return "read"
        return None

    def resource_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for items in self._acl().values():
            for e in items.values():
                name = self.name_of(e.get("owner", ""))
                if name:
                    out[name] = out.get(name, 0) + 1
        return out

    def transfer(self, source: str, target: str) -> int:
        """Gives everything `source` owns to `target` (names or ids); the shares of those resources are kept."""
        n = 0
        source, target = self.id_of(source) or str(source), self.id_of(target) or str(target)
        with self.lock:
            acl = self._acl(True)
            for items in acl.values():
                for e in items.values():
                    if e.get("owner") == source:
                        e["owner"] = target
                        e["users"] = [u for u in e.get("users", []) if u != target]
                        n += 1
                    elif source in e.get("users", []):
                        e["users"] = [u for u in e["users"] if u != source]
            self._write("acl.json", acl)
        return n

    def tag(self, kind: str, key: str) -> dict:
        """What a list shows next to a resource: its owner and who it is shared with."""
        e = self.entry(kind, key) or {}
        names = {u["id"]: n for n, u in self.users().items() if u.get("id")}
        return {
            "owner": names.get(e.get("owner", ""), ""),
            "shared_with": [names[u] for u in e.get("users", []) if u in names],
            "shared_all": bool(e.get("all")),
        }
