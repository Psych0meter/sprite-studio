"""The sign-in, account and user-management routes (the rules are in accounts.py)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import accounts as acc
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

COOKIE = "sg_session"


def make_router(
    accounts: acc.Accounts,
    adopt: Callable[[str], None],
    resource_kinds: Callable[[str, str], bool],
    removed: Callable[[str, str, str], None] = lambda uid, target_id, label: None,
) -> APIRouter:
    """`adopt(admin)` gives what exists before the first account to that administrator; `resource_kinds(kind, key)`
    says whether a resource exists (so that only real ones are shared); `removed(id, target_id, name)` hands the rest
    of a deleted user's state (jobs, picks, projects, folders) to `target`, so a new user of that name starts empty."""
    r = APIRouter()

    def ip_of(request: Request) -> str:
        return request.client.host if request.client else ""

    def cookie(resp: JSONResponse, request: Request, token: str) -> None:
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
        resp.set_cookie(
            COOKIE, token, max_age=acc.SESSION_MAX_AGE, httponly=True, samesite="lax", secure=secure, path="/"
        )

    def me(who: acc.Who) -> dict:
        return {"username": who.name, "role": who.role, "must_change": who.must_change}

    def current() -> acc.Who:
        w = acc.CURRENT.get()
        if w is None:
            raise HTTPException(401, "sign in")
        return w

    def admin() -> acc.Who:
        w = current()
        if not w.admin:
            raise HTTPException(403, "only an administrator can do this")
        return w

    async def body(request: Request) -> dict:
        b = await request.json()
        if not isinstance(b, dict):
            raise HTTPException(400, "send a JSON object")
        return b

    # ---- sign in / out
    @r.get("/api/auth/state")
    def state(request: Request):
        w = acc.CURRENT.get()
        return {"setup": not accounts.has_users(), "user": me(w) if w else None}

    @r.post("/api/auth/setup")
    async def setup(request: Request):
        b = await body(request)
        accounts.setup(b.get("username"), b.get("password"), b.get("code", ""), ip_of(request))
        name = acc.clean_username(b.get("username"))
        adopt(name)
        token, who = accounts.login(name, b.get("password"), ip_of(request), request.headers.get("user-agent", ""))
        resp = JSONResponse(me(who))
        cookie(resp, request, token)
        return resp

    @r.post("/api/auth/login")
    async def login(request: Request):
        b = await body(request)
        token, who = accounts.login(
            b.get("username"), b.get("password"), ip_of(request), request.headers.get("user-agent", "")
        )
        resp = JSONResponse(me(who))
        cookie(resp, request, token)
        return resp

    @r.post("/api/auth/logout")
    def logout(request: Request):
        accounts.logout(request.cookies.get(COOKIE, ""))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(COOKIE, path="/")
        return resp

    @r.get("/api/auth/me")
    def whoami():
        return me(current())

    @r.post("/api/auth/password")
    async def password(request: Request):
        w = current()
        b = await body(request)
        accounts.change_own_password(w.name, b.get("current", ""), b.get("new", ""))
        # every session of this user ended with the change: this browser gets a fresh one
        token = accounts.new_session(w.name, ip_of(request), request.headers.get("user-agent", ""))
        resp = JSONResponse({"ok": True})
        cookie(resp, request, token)
        return resp

    # ---- users
    @r.get("/api/users/names")
    def names():
        """Who a resource can be shared with."""
        w = current()
        return [{"username": n} for n, u in sorted(accounts.users().items()) if n != w.name and not u.get("disabled")]

    @r.get("/api/users")
    def users():
        admin()
        return accounts.listing()

    @r.post("/api/users")
    async def create(request: Request):
        w = admin()
        b = await body(request)
        accounts.create(
            b.get("username"),
            b.get("password"),
            role=b.get("role", "user"),
            must_change=b.get("must_change", True),
            by=w.name,
        )
        return accounts.listing()

    @r.patch("/api/users/{username}")
    async def update(username: str, request: Request):
        w = admin()
        b = await body(request)
        if username == w.name and b.get("disabled"):
            raise HTTPException(400, "you cannot disable your own account")
        if b.get("password") is not None:  # a reset: the person must choose their own at the next sign-in
            accounts.set_password(username, b["password"], must_change=b.get("must_change", True), by=w.name)
        accounts.update(
            username,
            role=b.get("role") if "role" in b else None,
            must_change=b.get("must_change") if b.get("password") is None and "must_change" in b else None,
            disabled=b.get("disabled") if "disabled" in b else None,
            by=w.name,
        )
        return accounts.listing()

    @r.delete("/api/users/{username}")
    def delete(username: str, transfer_to: str = ""):
        w = admin()
        if username == w.name:
            raise HTTPException(400, "you cannot remove your own account: ask another administrator")
        users = accounts.users()
        if username not in users:
            raise HTTPException(404, "no such user")
        target = transfer_to or w.name
        if target == username or target not in users:
            raise HTTPException(400, "choose the user who takes over their resources")
        uid, tid = accounts.id_of(username), accounts.id_of(target)  # (before the account is gone)
        accounts.delete(username, by=w.name)  # (refuses to remove the last administrator)
        moved = accounts.transfer(uid, tid)
        removed(uid, tid, username)
        return {"users": accounts.listing(), "transferred": moved, "to": target}

    # ---- sharing
    def checked(kind: str, key: str) -> dict:
        if kind not in acc.KINDS or not resource_kinds(kind, key):
            raise HTTPException(404, "not found")
        w = current()
        if accounts.access(w, kind, key) != "write":
            raise HTTPException(404 if accounts.access(w, kind, key) is None else 403, "only its owner can share it")
        return accounts.tag(kind, key)

    @r.get("/api/acl/{kind}/{key}")
    def acl_get(kind: str, key: str):
        return checked(kind, key)

    @r.put("/api/acl/{kind}/{key}")
    async def acl_put(kind: str, key: str, request: Request):
        w = current()
        checked(kind, key)
        b = await body(request)
        users_ = b.get("users", [])
        if not isinstance(users_, list) or not all(isinstance(u, str) for u in users_):
            raise HTTPException(400, "users is a list of user names")
        if b.get("owner") is not None and not isinstance(b["owner"], str):
            raise HTTPException(400, "owner is a user name")
        known = accounts.users()
        unknown = [u for u in users_ if u not in known]
        if unknown:
            raise HTTPException(400, f"no such user: {', '.join(map(str, unknown))}")
        if b.get("owner"):  # giving a resource to someone else: an administrator's decision
            if not w.admin:
                raise HTTPException(403, "only an administrator can change the owner")
            if b["owner"] not in accounts.users():
                raise HTTPException(400, "no such user")
            accounts.claim(kind, key, b["owner"], replace=True)
        accounts.share(kind, key, users_, bool(b.get("all")), by=w.name)
        return accounts.tag(kind, key)

    return r


def refusal(exc: acc.AccountError) -> JSONResponse:
    return JSONResponse({"error": exc.message}, status_code=exc.status)


__all__: list[Any] = ["COOKIE", "make_router", "refusal"]
