#!/usr/bin/env python3
"""Sprite Studio: a web front end for the sprite generator.

Wraps the command-line tools of scripts/ (pxmake.sh, pxbatch.sh,
pxmatrix.sh, train_lora.sh, pxmodel.py, pxtool.py) behind one page: models,
training sets, LoRA training, generation, batches, palettes, LoRA
comparisons, and a viewer to pick and compare the results. The scripts stay the single source of truth; the
app only builds their command lines, runs them as jobs and reads what they
write.

  ./run.sh                     (creates the venv on first start)
  http://<container>:8189

The GPU is shared, so GPU jobs (generation, batches, comparisons,
training) run one at a time from a queue; training sets are built at once
on the CPU. Training needs ComfyUI stopped: the app stops it and starts it
again around the run (sudo rule, see run.sh).

Settings: environment variables (SG_DATA, PX_INPUT, PX_GAME, PX_OUTPUT,
PX_COMFY_URL, COMFY, PX_LORA_HOME, SG_SCRIPTS, SG_WEB_STATE, SG_WEB_PORT,
SG_WEB_PASSWORD) give the defaults; the
Settings page overrides them (saved in ~/.spritegen-web/settings.json).
SG_WEB_PASSWORD, when set, asks for it (any user name) in the browser.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError

HERE = Path(__file__).resolve().parent
SCRIPTS = Path(os.environ.get("SG_SCRIPTS", HERE.parent / "scripts")).resolve()
VERSION = next((f.read_text().strip() for f in (HERE.parent / "VERSION",) if f.exists()), "dev")
STATE_DIR = Path(os.environ.get("SG_WEB_STATE", Path.home() / ".spritegen-web"))
LOG_DIR = STATE_DIR / "logs"
for _d in (STATE_DIR, LOG_DIR):  # what the app keeps (accounts, prompts, paths, logs) is for its own user only
    _d.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        _d.chmod(0o700)
sys.path.insert(0, str(SCRIPTS))

import accounts as acc  # noqa: E402  (users, sessions, ownership: webapp/accounts.py)
import auth_routes  # noqa: E402  (sign in, users, sharing)
import pxupdate  # noqa: E402  (updates to released versions: scripts/pxupdate.py)

import pxmodel  # noqa: E402  (the sprite models: scripts/pxmodel.py)
from pxtool import MAX_COLOURS  # noqa: E402  (the most colours a palette holds)

ACCOUNTS = acc.Accounts(STATE_DIR)
acc_cookie = auth_routes.COOKIE
WRITING: ContextVar[bool] = ContextVar("request_writes", default=False)  # the request changes something


def who_now() -> acc.Who | None:
    """The signed-in user of this request (None: the app itself, e.g. a job finishing in the background)."""
    return acc.CURRENT.get()


def is_admin() -> bool:
    w = who_now()
    return w is None or w.admin


def need_admin() -> None:
    if not is_admin():
        raise HTTPException(403, "only an administrator can do this")


def need(kind: str, key: str, write: bool | None = None) -> None:
    """Refuses (404: it does not exist for you, 403: it is shared with you to use, not to change) what the signed-in
    user may not touch. `write` defaults to what the request does (a GET reads, anything else changes)."""
    w = who_now()
    if w is None:
        return
    a = ACCOUNTS.access(w, kind, key)
    if a is None:
        raise HTTPException(404, "not found")
    if (WRITING.get() if write is None else write) and a != "write":
        raise HTTPException(403, f"{key} is shared with you to use, not to change: ask its owner, or make your own")


def can(kind: str, key: str, write: bool = False) -> bool:
    try:
        need(kind, key, write)
    except HTTPException:
        return False
    return True


def tagged(kind: str, key: str, row: dict) -> dict:
    """A list row with who owns it and who it is shared with (what the pages show next to it)."""
    w = who_now()
    row.update(ACCOUNTS.tag(kind, key))
    row["uid"] = ACCOUNTS.uid(kind, key)  # (the stable id: the name can change, this does not)
    row["access"] = "write" if w is None or ACCOUNTS.access(w, kind, key) == "write" else "read"
    return row


def claim_folder(name: str) -> None:
    """A job writes into this result folder: it is the user's, or new. Someone else's is refused (a batch name
    that exists already would otherwise continue their batch)."""
    w = who_now()
    if w is None:
        return
    if ACCOUNTS.access(w, "result", name) == "write":
        return
    if ACCOUNTS.entry("result", name) or (setting_path("px_output") / name).exists():
        raise HTTPException(400, f"{name} exists already and belongs to someone else: give it another name")
    ACCOUNTS.claim("result", name, w.name)


def claim(kind: str, key: str) -> None:
    """The signed-in user owns what they create."""
    w = who_now()
    if w is not None:
        ACCOUNTS.claim(kind, key, w.name)


def pm():
    """pxmodel, pointed at the folders of the current settings."""
    st = settings()
    pxmodel.HOME = st["lora_home"]
    pxmodel.ROOTS.clear()
    pxmodel.ROOTS.update(sources())
    return pxmodel


def sources() -> dict:
    """The named folders sprites are imported from: input, game, and the ones
    added in Settings ("name=path" lines)."""
    st = settings()
    out = {"input": st["px_input"], "game": st["px_game"]}
    for line in (st.get("sources") or "").splitlines():
        name, _, path = line.partition("=")
        if name.strip() and path.strip():
            out[re.sub(r"[^\w-]", "_", name.strip())] = path.strip()
    return out


def body_model(b: dict) -> str:
    """The model a request is for."""
    if not b.get("model"):
        raise HTTPException(400, "choose a model")
    return b["model"]


def model_cfg(mid: str, write: bool | None = None) -> dict:
    """The model; `write=False` for what only uses it (generating with it), else a change needs the owner."""
    if not re.fullmatch(r"[a-z0-9_]{1,40}", mid or ""):
        raise HTTPException(400, "a model id is lowercase letters, digits and _")
    need("model", mid, write)
    try:
        return pm().load(mid)
    except SystemExit as e:
        raise HTTPException(404, str(e)) from None


BASES = {
    "sd15": {"label": "SD 1.5", "suffix": "", "res": 512, "epochs": 12},
    "sdxl": {"label": "SDXL", "suffix": "_xl", "res": 1024, "epochs": 8},
}


def model_ok(m: str | None) -> str:
    return m if m in BASES else "sd15"


# --------------------------------------------------------------------------
# settings and small persistent state


DATA = Path(os.environ.get("SG_DATA", Path.home() / "sprites"))
DEFAULTS = {
    "px_input": os.environ.get("PX_INPUT", str(DATA / "input")),
    "px_game": os.environ.get("PX_GAME", str(DATA / "game")),
    "px_output": os.environ.get("PX_OUTPUT", str(DATA / "output")),
    "comfy_url": os.environ.get("PX_COMFY_URL", "http://127.0.0.1:8188"),
    "comfy_dir": os.environ.get("COMFY", str(Path.home() / "ComfyUI")),
    "lora_home": os.environ.get("PX_LORA_HOME", str(Path.home() / "lora")),
    "comfy_service": "comfyui",
    "train_stops_comfy": "auto",  # auto: only on cards under 15 GB; always; never
    "sources": "",  # more source folders: name=path, one per line
}
_lock = threading.RLock()


def _load(name: str, default: Any) -> Any:
    try:
        return json.loads((STATE_DIR / name).read_text())
    except (OSError, ValueError):
        return default


def _private_open(path: str | Path, mode: str = "a"):
    """Opens a file of the app's own state for text writing, created readable by its user only (0600)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if mode == "a" else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with contextlib.suppress(OSError):
        os.fchmod(fd, 0o600)
    return os.fdopen(fd, mode)


def _save(name: str, data: Any) -> None:
    tmp = STATE_DIR / (name + ".tmp")
    with _private_open(tmp, "w") as fh:
        fh.write(json.dumps(data, indent=1))
    tmp.replace(STATE_DIR / name)


def settings() -> dict:
    s = dict(DEFAULTS)
    s.update({k: v for k, v in _load("settings.json", {}).items() if k in DEFAULTS and v})
    return s


def setting_path(key: str) -> Path:
    return Path(settings()[key])


def roots() -> list[Path]:
    s = settings()
    return (
        [Path(s[k]).resolve() for k in ("px_output", "lora_home") if s.get(k)]
        + [Path(v).resolve() for v in sources().values()]
        + [Path(s["comfy_dir"]).resolve() / "models" / "loras"]
    )


def safe_path(p: str, write: bool | None = None) -> Path:
    """A path the browser may use: under one of the data folders, and, for a signed-in user, in something they may
    reach (their own results and models, what is shared with them, the shared source folders to read)."""
    try:
        rp = Path(p).resolve()
    except (OSError, RuntimeError):
        raise HTTPException(400, "bad path") from None
    for r in roots():
        if rp == r or r in rp.parents:
            gate_path(r, rp, write)
            return rp
    raise HTTPException(403, "outside the data folders")


def is_recoloured(p: Path, out_root: Path) -> bool:
    return p.relative_to(out_root).parts[0] == "recoloured"


def can_path(p: str) -> bool:
    try:
        safe_path(p, write=False)
    except HTTPException:
        return False
    return True


def gate_path(root: Path, rp: Path, write: bool | None) -> None:
    w = who_now()
    if w is None or w.admin:
        return
    s = settings()
    parts = rp.relative_to(root).parts
    if root == Path(s["px_output"]).resolve():
        if not parts:
            if WRITING.get() if write is None else write:
                raise HTTPException(403, "only an administrator can do this")
            return
        top = parts[0]
        if top == "picked":  # exported picks: each user has a folder of their own (named by their id)
            if len(parts) > 1 and w.id and parts[1] == w.id:
                return
            raise HTTPException(404, "not found")
        if top in ("uploads", "recoloured"):  # sketches and masks sent for a generation: a folder per user
            if len(parts) > 1 and w.id and parts[1] == w.id:
                return
            raise HTTPException(404, "not found")
        if top == ".bin":  # a deleted result belongs to whoever owned the result
            origin = root / ".bin" / parts[1] / ".origin" if len(parts) > 1 else None
            try:
                key = origin.read_text().strip().split("/")[0] if origin else ""
            except OSError:
                key = ""
            if not key:
                raise HTTPException(404, "not found")
            need("result", key, write)
            return
        need("result", top, write)
    elif root == Path(s["lora_home"]).resolve():
        if not parts or not re.fullmatch(r"[a-z0-9_]{1,40}", parts[0]):
            raise HTTPException(403, "only an administrator can do this")
        need("model", parts[0], write)
    elif root in [Path(v).resolve() for v in sources().values()]:  # the shared source folders: read only
        if WRITING.get() if write is None else write:
            raise HTTPException(403, "only an administrator can change the shared source folders")
    else:  # ComfyUI's folders
        raise HTTPException(403, "only an administrator can do this")


# ---- limits: what the app accepts from the browser
MAX_BODY = int(os.environ.get("SG_MAX_UPLOAD_MB", "300")) * 1024 * 1024  # one request
MAX_FILE = 25 * 1024 * 1024  # one uploaded file, decoded
MAX_ZIP_ENTRIES = 5000
MAX_ZIP_TOTAL = int(os.environ.get("SG_MAX_ZIP_MB", "250")) * 1024 * 1024  # a zip's files, uncompressed
INFO_PIXELS = 16_000_000  # the most an image analysis (colours, parts) reads
MAX_TEXT_SERVED = 10 * 1024 * 1024  # the biggest text file /api/file serves
MAX_PIXELS = 64_000_000  # one image
MAX_QUEUED = int(os.environ.get("SG_MAX_QUEUED", "100"))  # jobs waiting for the GPU
MAX_CPU_JOBS = 8  # CPU jobs running at once
MAX_QUEUED_PER_USER = int(os.environ.get("SG_MAX_QUEUED_PER_USER", "20"))  # GPU jobs one user may have waiting
MAX_CPU_JOBS_PER_USER = int(os.environ.get("SG_MAX_CPU_JOBS_PER_USER", "4"))  # CPU jobs one user may have running
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
SERVED_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".json", ".txt", ".csv", ".log"}


def decode_data_url(data: Any, kinds: tuple[str, ...] = ("image/png",), limit: int = MAX_FILE) -> bytes:
    """The bytes of a `data:<type>;base64,...` URL; a clear 400 for anything else (strict base64)."""
    m = re.match(r"data:([^;,]*);base64,(.*)$", data if isinstance(data, str) else "", re.S)
    if not m or (kinds and m.group(1) not in kinds and not (m.group(1) == "" and "" in kinds)):
        raise HTTPException(400, "send the file as a base64 data URL" + (f" ({', '.join(kinds)})" if kinds else ""))
    if len(m.group(2)) * 3 // 4 > limit + 3:  # the size it will have, before any buffer is allocated
        raise HTTPException(413, f"file too big (at most {limit // 1_000_000} MB)")
    try:
        raw = base64.b64decode(m.group(2), validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "the file is not valid base64") from None
    if len(raw) > limit:
        raise HTTPException(413, f"file too big (at most {limit // 1_000_000} MB)")
    return raw


def check_image(raw: bytes, fmt: str | None = None) -> tuple[int, int]:
    """Opens the bytes as an image (not just trusting a name or MIME type): its size, or a 400."""
    try:
        with Image.open(io.BytesIO(raw)) as im:
            if fmt and im.format != fmt:
                raise HTTPException(400, f"not a {fmt} image")
            w, h = im.size
            im.verify()
    except HTTPException:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError):
        raise HTTPException(400, "not a readable image (or too large)") from None
    if w * h > MAX_PIXELS:
        raise HTTPException(400, f"image too large ({w}x{h} px)")
    return w, h


def job_env() -> dict:
    s = settings()
    env = dict(os.environ)
    env.update(
        PX_INPUT=s["px_input"],
        PX_GAME=s["px_game"],
        PX_OUTPUT=s["px_output"],
        PX_COMFY_URL=s["comfy_url"],
        COMFY=s["comfy_dir"],
        PX_LORA_HOME=s["lora_home"],
        PYTHONUNBUFFERED="1",
        PYTHONWARNINGS="ignore",
    )
    env["PATH"] = f"{SCRIPTS}:{env.get('PATH', '')}"
    return env


# --------------------------------------------------------------------------
# jobs: GPU jobs one at a time from a queue, CPU jobs at once


class Jobs:
    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self.queue: deque[str] = deque()
        self.procs: dict[str, subprocess.Popen] = {}
        self.cv = threading.Condition(_lock)
        for j in _load("jobs.json", []):
            if j["status"] in ("running", "queued"):
                j["status"] = "interrupted"
                j["error"] = "the web app was restarted while this job was " + (
                    "running" if j.get("started") else "waiting"
                )
            self.jobs[j["id"]] = j
        threading.Thread(target=self._gpu_worker, daemon=True).start()

    def persist(self) -> None:
        items = sorted(self.jobs.values(), key=lambda j: j["created"])[-300:]
        _save("jobs.json", items)
        keep = {Path(j["log"]).name for j in items}
        for old in LOG_DIR.glob("*.log"):  # the logs of jobs that fell off the list are removed
            if old.name not in keep and time.time() - old.stat().st_mtime > 86400:
                with contextlib.suppress(OSError):
                    old.unlink()

    def submit(
        self,
        kind: str,
        title: str,
        cmd: list[str],
        *,
        lane: str = "gpu",
        env: dict | None = None,
        out: str | None = None,
        meta: dict | None = None,
    ) -> dict:
        jid = dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        job = {
            "id": jid,
            "owner": (who_now().id if who_now() else ""),
            "kind": kind,
            "title": title,
            "lane": lane,
            "cmd": cmd,
            "env": env or {},
            "out": out,
            "meta": meta or {},
            "status": "queued",
            "created": time.time(),
            "started": None,
            "ended": None,
            "returncode": None,
            "error": None,
            "log": str(LOG_DIR / f"{jid}.log"),
        }
        with self.cv:
            mine = job["owner"]
            if mine and lane == "gpu" and kind != "update":
                waiting = sum(1 for q in self.queue if self.jobs[q]["owner"] == mine)
                if waiting >= MAX_QUEUED_PER_USER:
                    raise HTTPException(
                        429,
                        f"you already have {waiting} jobs waiting for the GPU (the most one person can queue is "
                        f"{MAX_QUEUED_PER_USER}): wait for some to finish or cancel some",
                    )
            if mine and lane != "gpu":
                running = sum(
                    1
                    for j in self.jobs.values()
                    if j["lane"] != "gpu" and j["status"] == "running" and j["owner"] == mine
                )
                if running >= MAX_CPU_JOBS_PER_USER:
                    raise HTTPException(429, f"you already have {running} jobs running: try again in a moment")
            if lane == "gpu" and len(self.queue) >= MAX_QUEUED:
                raise HTTPException(
                    429, f"{MAX_QUEUED} jobs are already waiting: wait for some to finish or cancel some"
                )
            if (
                lane != "gpu"
                and sum(1 for j in self.jobs.values() if j["lane"] != "gpu" and j["status"] == "running")
                >= MAX_CPU_JOBS
            ):
                raise HTTPException(429, "too many jobs are running at once: try again in a moment")
            self.jobs[jid] = job
            if lane == "gpu":
                self.queue.append(jid)
                self.cv.notify_all()
            else:
                threading.Thread(target=self._run, args=(jid,), daemon=True).start()
            self.persist()
        return job

    def _gpu_worker(self) -> None:
        while True:
            with self.cv:
                while not self.queue:
                    self.cv.wait()
                jid = self.queue.popleft()
            self._run(jid)

    def _log(self, job: dict, text: str) -> None:
        with _private_open(job["log"]) as fh:
            fh.write(text if text.endswith("\n") else text + "\n")

    def _run(self, jid: str) -> None:
        job = self.jobs[jid]
        with self.cv:
            if job["status"] != "queued":
                return
            job["status"] = "running"
            job["started"] = time.time()
            self.persist()
        stopped_comfy = False
        try:
            if job["meta"].get("needs_free_gpu") and must_free_gpu():
                stopped_comfy = self._free_gpu(job)
            elif job["kind"] in ("generate", "batch", "matrix", "previews") and not comfy_status()["up"]:
                # ComfyUI may be starting again after a training run
                self._log(job, "waiting for ComfyUI to answer (up to 3 minutes)")
                for _ in range(90):
                    if job["status"] == "cancelled" or comfy_status()["up"]:
                        break
                    time.sleep(2)
                else:
                    raise RuntimeError("ComfyUI is not running: start it on the Overview page, then run this job again")
                if job["status"] == "cancelled":
                    return
            env = job_env()
            env.update(job["env"])
            self._log(job, "$ " + " ".join(_quote(c) for c in job["cmd"]))
            with _private_open(job["log"]) as fh:
                p = subprocess.Popen(
                    job["cmd"],
                    stdout=fh,
                    stderr=subprocess.STDOUT,
                    env=env,
                    cwd=str(SCRIPTS),
                    start_new_session=True,
                )
                self.procs[jid] = p
                rc = p.wait()
            job["returncode"] = rc
            if job["status"] == "cancelled":
                pass
            elif rc == 0:
                job["status"] = "done"
            elif rc == 4 and job.get("kind") in ("batch", "matrix"):
                # Some sprites failed, the rest is usable: done, with the reason shown.
                job["status"] = "done"
                tail = read_log(job, 60000)
                m = re.search(r"^failed: (\d+) of", tail, re.M)
                n = int(m.group(1)) if m else len(re.findall(r"^\s*failed: ", tail, re.M))
                job["error"] = f"{n} {'render' if job['kind'] == 'matrix' else 'sprite'}(s) failed: " + (
                    log_error(job) or "see the log"
                )
            else:
                job["status"] = "failed"
                job["error"] = log_error(job) or f"exited with code {rc}: see the log"
        except Exception as e:
            job["status"] = "failed"
            job["error"] = str(e)
            self._log(job, f"error: {e}")
        finally:
            self.procs.pop(jid, None)
            if stopped_comfy:
                ok, msg = comfy_service("start")
                self._log(job, "ComfyUI started again" if ok else f"could not start ComfyUI: {msg}")
            job["ended"] = time.time()
            with self.cv:
                self.persist()
        follow_up(job)

    def _free_gpu(self, job: dict) -> bool:
        if not comfy_status()["up"]:
            self._log(job, "ComfyUI is not running: the GPU is free")
            return False
        self._log(job, "stopping ComfyUI to free the GPU memory for training")
        ok, msg = comfy_service("stop")
        if not ok:
            raise RuntimeError(
                "ComfyUI holds the GPU and the app could not stop it (" + msg + "). "
                "Stop it yourself (systemctl stop comfyui) or add the sudo rule from run.sh."
            )
        for _ in range(30):
            if not comfy_status()["up"]:
                break
            time.sleep(1)
        if comfy_status()["up"]:
            raise RuntimeError(
                "ComfyUI is still running 30 s after it was asked to stop, so the GPU may not be free. "
                "Stop it yourself (systemctl stop comfyui) and start the training again."
            )
        time.sleep(3)
        return True

    def cancel(self, jid: str) -> dict:
        with self.cv:
            job = self.jobs.get(jid)
            if not job:
                raise HTTPException(404, "no such job")
            if job["status"] == "queued":
                if jid in self.queue:
                    self.queue.remove(jid)
                job["status"] = "cancelled"
                job["ended"] = time.time()
                self.persist()
                return job
            if job["status"] != "running":
                return job
            job["status"] = "cancelled"
            p = self.procs.get(jid)
        if p:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGTERM)

            def kill_later() -> None:
                time.sleep(10)
                if p.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(p.pid, signal.SIGKILL)

            threading.Thread(target=kill_later, daemon=True).start()
        self._log(job, "cancelled by the user")
        return job

    def view(self, job: dict, with_progress: bool = True) -> dict:
        if not job_mine(job):  # somebody else's job: the queue shows that the GPU is busy, nothing more
            keys = ("id", "kind", "lane", "status", "created", "started", "ended")
            v = {k: job.get(k) for k in keys}
            v["owner"] = ACCOUNTS.name_of(job.get("owner", ""))
            v.update(title="another user's job", foreign=True, position=None)
            v["position"] = list(self.queue).index(job["id"]) + 1 if job["id"] in self.queue else None
            return v
        v = {k: job[k] for k in job if k not in ("env",)}
        v["owner"] = ACCOUNTS.name_of(job.get("owner", ""))
        v["position"] = list(self.queue).index(job["id"]) + 1 if job["id"] in self.queue else None
        if with_progress:
            v["progress"] = progress(job)
        return v


def job_mine(job: dict) -> bool:
    """Whether the signed-in user may see this job's details (their own; an administrator sees all)."""
    w = who_now()
    return w is None or w.admin or bool(w.id and job.get("owner") == w.id)


def job_get(jid: str) -> dict:
    j = JOBS.jobs.get(jid)
    if not j or not job_mine(j):
        raise HTTPException(404, "no such job")
    return j


def _quote(s: str) -> str:
    return s if re.fullmatch(r"[\w@%+=:,./-]+", s) else "'" + s.replace("'", "'\\''") + "'"


def log_error(job: dict) -> str:
    """The most telling error line at the end of a log, for the job's summary."""
    lines = [ln.strip() for ln in re.split(r"[\r\n]", read_log(job, 30000)) if ln.strip()]
    for pat in (
        r"^error: ",
        r"^FAILED: ",
        r"^[A-Za-z_.]*(Error|Exception): ",
        r"^(FAILED|not found|no references|unknown)",
    ):
        hits = [ln for ln in lines if re.search(pat, ln) and "CalledProcessError" not in ln]
        if hits:
            return hits[-1][:400]
    return ""


def read_log(job: dict, tail: int = 0) -> str:
    try:
        with open(job["log"], "rb") as fh:
            if tail:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - tail))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


STEP_RE = re.compile(r"steps:\s*(\d+)%\|[^|]*\|\s*(\d+)/(\d+)\s*\[([\d:]+)<([\d:?]+)[^\]]*?(?:avr_loss=([\d.]+))?\]")
EPOCH_RE = re.compile(r"epoch (\d+)/(\d+)")


def progress(job: dict) -> dict:
    """What the job's log says about how far it is."""
    kind, meta = job["kind"], job["meta"]
    if job["status"] == "queued":
        return {"fraction": 0, "text": "waiting for the GPU"}
    if kind == "train":
        text = read_log(job)
        pieces = re.split(r"[\r\n]", text)
        loss, step, total, elapsed, remaining = [], 0, 0, "", ""
        for piece in pieces:
            m = STEP_RE.search(piece)
            if m:
                step, total = int(m.group(2)), int(m.group(3))
                elapsed, remaining = m.group(4), m.group(5)
                if m.group(6):
                    loss.append((step, float(m.group(6))))
        epochs = EPOCH_RE.findall(text)
        ep = f"epoch {epochs[-1][0]} of {epochs[-1][1]}" if epochs else ""
        # thin the loss curve to ~200 points
        if len(loss) > 200:
            k = len(loss) / 200
            loss = [loss[int(i * k)] for i in range(200)] + [loss[-1]]
        frac = step / total if total else 0
        parts = [
            p for p in (ep, f"step {step} of {total}" if total else "", f"{remaining} left" if remaining else "") if p
        ]
        return {"fraction": frac, "text": ", ".join(parts) or "starting", "loss": loss, "elapsed": elapsed}
    if kind == "batch":
        text = read_log(job)
        n = len(re.findall(r"^=== \d{4}-", text, re.M))
        total = meta.get("total") or 0
        last = re.findall(r"^=== \S+ \S+ (\S+)", text, re.M)
        return {
            "fraction": min(1, max(0, n - 1) / total) if total else 0,
            "text": f"sprite {n} of {total}" + (f": {last[-1]}" if last else "") if total else f"{n} sprites",
        }
    if kind in ("matrix", "previews"):
        text = read_log(job)
        m = re.findall(r"=== \[(\d+)/(\d+)\]", text)
        if m:
            i, t = map(int, m[-1])
            what = "preview" if kind == "previews" else "cell"
            return {"fraction": (i - 1) / t, "text": f"{what} {i} of {t}"}
        return {"fraction": 0, "text": "starting"}
    if kind == "generate":
        text = read_log(job, 20000)
        n = meta.get("count", 1)
        done = len(re.findall(r"\] seed=\d+", text))
        cleaned = len(re.findall(r"sprite standard:", text))
        frac = (done + cleaned) / (2 * n) if n else 0
        return {"fraction": frac, "text": f"{done} of {n} rendered" + (f", {cleaned} cleaned" if cleaned else "")}
    return {"fraction": 1 if job["status"] == "done" else 0, "text": ""}


# --------------------------------------------------------------------------
# ComfyUI


def comfy_status() -> dict:
    url = settings()["comfy_url"]
    try:
        with urllib.request.urlopen(url + "/system_stats", timeout=2) as r:
            data = json.loads(r.read())
        dev = (data.get("devices") or [{}])[0]
        return {
            "up": True,
            "url": url,
            "device": dev.get("name", "").split(" : ")[0].replace("cuda:0 ", ""),
            "vram_total": dev.get("vram_total"),
            "vram_free": dev.get("vram_free"),
            "version": data.get("system", {}).get("comfyui_version"),
        }
    except (OSError, ValueError, urllib.error.URLError):
        return {"up": False, "url": url}


def gpu_memory_mb() -> int:
    g = gpu_info()
    if g:
        return g["mem_total"]
    c = comfy_status()
    return int((c.get("vram_total") or 0) / 1048576)


def must_free_gpu() -> bool:
    """Whether training has to stop ComfyUI: on cards with less than 15 GB
    both do not fit (setting train_stops_comfy: auto / always / never)."""
    mode = settings().get("train_stops_comfy", "auto")
    if mode in ("always", "never"):
        return mode == "always"
    mem = gpu_memory_mb()
    return not mem or mem < 15000


def _clock_seconds(text: str) -> int:
    """'1:46:02' or '05:32' as seconds."""
    n = 0
    for part in text.split(":"):
        n = n * 60 + int(part)
    return n


def train_seconds_per_step(log: str) -> float | None:
    """Seconds one training step took in a finished run, from the last progress line of its log (the run's
    own total time also holds the start-up and the saves, and depends on how many steps it had)."""
    last = None
    for piece in re.split(r"[\r\n]", log[-200000:]):
        m = STEP_RE.search(piece)
        if m:
            last = m
    if not last or not int(last.group(2)):
        return None
    try:
        return _clock_seconds(last.group(4)) / int(last.group(2))
    except ValueError:
        return None


_speed_cache: dict[str, Any] = {"sig": None, "value": None}


def speed() -> dict:
    """How fast this machine is, from the logs of finished jobs: seconds per rendered image, and per
    training step (per base model; the median of the last runs), plus minutes of the last run.
    Reading those logs is slow, so the answer is kept until a job starts, ends or changes."""
    with _lock:
        jobs = sorted(JOBS.jobs.values(), key=lambda j: -j["created"])
        sig = tuple((j["id"], j["status"], j.get("ended"), j.get("returncode")) for j in jobs)
        if _speed_cache["sig"] == sig:
            return _speed_cache["value"]
    value = _measure_speed(jobs)
    _speed_cache.update(sig=sig, value=value)
    return value


def _measure_speed(jobs: list[dict]) -> dict:
    per_image, train, steps = [], {}, {}
    for j in jobs:
        if j["status"] != "done" or j.get("returncode") != 0 or j.get("error"):
            continue  # only runs that really finished: not failed, cancelled, interrupted or partly failed ones
        if j["kind"] in ("generate", "batch", "matrix", "previews") and len(per_image) < 200:
            per_image += [int(x) for x in re.findall(r"\] seed=\d+\s+(\d+) s", read_log(j, 200000))][-50:]
        elif j["kind"] == "train" and j.get("started") and j.get("ended"):
            base = model_ok(j["meta"].get("model"))
            train.setdefault(base, (j["ended"] - j["started"]) / 60)
            sps = train_seconds_per_step(read_log(j, 200000))
            if sps and len(steps.setdefault(base, [])) < 5:
                steps[base].append(sps)
    per_image.sort()
    return {
        "sec_per_image": per_image[len(per_image) // 2] if len(per_image) >= 3 else None,
        "train_minutes": {k: round(v) for k, v in train.items()},
        "train_sec_per_step": {k: round(sorted(v)[len(v) // 2], 3) for k, v in steps.items()},
    }


def comfy_service(action: str) -> tuple[bool, str]:
    name = settings()["comfy_service"]
    try:
        r = subprocess.run(["sudo", "-n", "systemctl", action, name], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    msg = (r.stderr or r.stdout).strip()
    if r.returncode and "password" in msg:
        msg = "sudo asks for a password: the sudo rule from run.sh is missing"
    return r.returncode == 0, msg


def gpu_info() -> dict | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = (
            subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.used,memory.total,utilization.gpu,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            .stdout.strip()
            .splitlines()[0]
        )
        name, used, total, util, temp = [x.strip() for x in out.split(",")]
        return {"name": name, "mem_used": int(used), "mem_total": int(total), "util": int(util), "temp": int(temp)}
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        return None


# --------------------------------------------------------------------------
# files on disk


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n":
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
    except OSError:
        pass
    return None


def sprite_list(root: Path, sub: str = "", recursive: bool = False) -> list[dict]:
    base = root / sub if sub else root
    if not base.is_dir():
        return []
    files = sorted(base.rglob("*.png") if recursive else base.glob("*.png"))
    out = []
    for f in files:
        if "/rooms/" in str(f):
            continue
        w_h = png_size(f) or (0, 0)
        out.append({"path": str(f), "name": f.stem, "rel": str(f.relative_to(root)), "w": w_h[0], "h": w_h[1]})
    return out


VARIANT_SKIP = {"orig", "init", "sketch"}


def is_variant(f: Path) -> bool:
    """A generated sprite, not one of the job's working files."""
    return not (f.stem in VARIANT_SKIP or f.stem.startswith("init_") or f.stem.endswith("_x4"))


def render_seconds(png: str | Path) -> float | None:
    """How long the image took to render, from the .json pxcomfy.py writes next to it (None: not recorded)."""
    f = Path(png).with_suffix(".json") if png else None
    try:
        return float(json.loads(f.read_text())["seconds"]) if f and f.is_file() else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def result_set(folder: Path) -> dict:
    """One generation folder (pxmake.sh layout: job.txt, raw/, variants)."""
    job_txt = (folder / "job.txt").read_text(errors="replace") if (folder / "job.txt").exists() else ""
    head = dict(re.findall(r"(\w+)=(\S+)", job_txt.splitlines()[0])) if job_txt else {}
    if head.get("lora_id"):  # (the file may have been renamed since)
        head["lora"] = lora_name_of(head["lora_id"]) or head.get("lora", "")
    prompt = what = ""
    for line in job_txt.splitlines():
        if line.startswith("prompt: "):
            prompt = line[8:]
        elif line.startswith(("what: ", "extra: ")):
            what = line.split(": ", 1)[1]
    variants = []
    for f in sorted(folder.glob("*.png")):
        if not is_variant(f):
            continue
        m = re.search(r"_s(\d+)(?:_c\d+)?$", f.stem)
        wh = png_size(f) or (0, 0)
        raw = next((str(r) for r in (folder / "raw").glob(f"*_s{m.group(1)}_*.png")), "") if m else ""
        variants.append(
            {
                "path": str(f),
                "name": f.stem,
                "seed": m.group(1) if m else "",
                "w": wh[0],
                "h": wh[1],
                "raw": raw,
                "seconds": render_seconds(raw),
            }
        )
    orig = folder / "orig.png"
    if "faction" in head and "group" not in head:  # job files of the first generator versions
        head["group"] = head["faction"]
    if "category" in head and "model" not in head:
        head["model"] = head["category"]
    init = head.get("init", "none")
    target = Path(init).stem if init not in ("none", "") and orig.exists() else head.get("name", folder.name)
    return {
        "target": target,
        "path": str(folder),
        "name": head.get("name", folder.name),
        "orig": str(orig) if orig.exists() else "",
        "orig_size": png_size(orig) if orig.exists() else None,
        "variants": variants,
        "prompt": prompt,
        "what": what,
        "params": head,
        "done": (folder / ".done").exists(),
        "mtime": folder.stat().st_mtime,
    }


def _variants(d: Path) -> list[Path]:
    return [v for v in d.glob("*.png") if is_variant(v)]


def _scan_result(d: Path, mtime: float) -> list[dict]:
    """The list row of one output folder (none when it is not a result)."""
    items: list[dict] = []
    if d.name.startswith("batch-"):
        sets = [s.parent for s in d.glob("*/*/job.txt")]
        made = [s for s in sets if _variants(s)]
        classes = sorted({s.parent.name for s in sets})
        cover = next((str(v) for s in made for v in s.glob("*_s*.png") if not v.stem.endswith("_x4")), "")
        items.append(
            {
                "type": "batch",
                "path": str(d),
                "title": d.name[6:],
                "count": len(made),
                "tried": len(sets),
                "classes": classes,
                "mtime": max([mtime] + [s.stat().st_mtime for s in sets]),
                "cover": cover,
                "empty": not made,
            }
        )
    elif d.name.startswith("matrix-"):
        sheets = sorted(str(s) for s in d.glob("sheet_*.png"))
        made = any(_variants(c) for c in d.glob("*/*/") if c.is_dir())
        items.append(
            {
                "type": "matrix",
                "path": str(d),
                "title": d.name[7:],
                "count": len(sheets),
                "mtime": mtime,
                "cover": sheets[0] if sheets and made else "",
                "empty": not made,
            }
        )
    elif (d / "job.txt").exists():
        vs = _variants(d)
        head = ((d / "job.txt").read_text(errors="replace").splitlines() or [""])[0]
        name = (re.search(r"name=(\S+)", head) or [None, d.name])[1]
        kind = (re.search(r"(?:kind|category)=(\S+)", head) or [None, ""])[1]
        items.append(
            {
                "type": "single",
                "path": str(d),
                "title": name,
                "kind": kind,
                "count": len(vs),
                "mtime": mtime,
                "cover": str(sorted(vs)[0]) if vs else "",
                "done": (d / ".done").exists(),
                "empty": not vs,
            }
        )
    return items


def scan_results() -> list[dict]:
    """Every result folder; "empty" ones (nothing rendered, e.g. every sprite failed) are flagged."""
    out_root = setting_path("px_output")
    items = []
    if not out_root.is_dir():
        return items
    for d in out_root.iterdir():
        if not d.is_dir() or d.name in ("uploads", "picked", "recoloured") or d.name.startswith("."):
            continue
        try:
            mtime = d.stat().st_mtime
            items.extend(_scan_result(d, mtime))
        except Exception as e:  # one unreadable folder must not hide the others
            print(f"results: skipped {d.name}: {e!r}", file=sys.stderr, flush=True)
    items = [tagged("result", Path(i["path"]).name, i) for i in items if can("result", Path(i["path"]).name)]
    busy = busy_outputs()
    for i in items:
        i["busy"] = any(i["path"] == b or b.startswith(i["path"] + "/") for b in busy)
        if i["busy"]:
            i["empty"] = False  # still rendering
    items.sort(key=lambda i: -i["mtime"])
    return items


def busy_outputs() -> list[str]:
    """Output folders of the queued and running jobs."""
    return [
        str(Path(j["out"]).resolve())
        for j in JOBS.jobs.values()
        if j.get("out") and j["status"] in ("queued", "running")
    ]


def results_bin() -> Path:
    return setting_path("px_output") / ".bin"


def delete_result(path: str) -> str:
    """Moves a result folder (a run, a batch, a LoRA test, or one sprite inside a batch) to the bin."""
    p = safe_path(path)
    out_root = setting_path("px_output").resolve()
    if p == out_root or out_root not in p.parents or not p.is_dir():
        raise HTTPException(400, f"not a result folder: {path}")
    rel = p.relative_to(out_root)
    if rel.parts[0] in ("uploads", "picked", "recoloured") or rel.parts[0].startswith("."):
        raise HTTPException(400, f"{rel.parts[0]} is not a result")
    if len(rel.parts) > 1 and not rel.parts[0].startswith(("batch-", "matrix-")):
        raise HTTPException(400, "only whole results, or one sprite of a batch, can be deleted")
    for b in busy_outputs():
        if b == str(p) or b.startswith(str(p) + "/") or str(p).startswith(b + "/"):
            raise HTTPException(409, f"a job is still writing to {rel}: cancel it first (Jobs page)")
    dst = results_bin() / f"{time.time_ns()}_{rel.parts[-1]}"  # sorts in deletion order
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(p), str(dst))
    (dst / ".origin").write_text(str(rel))
    if len(rel.parts) == 3 and rel.parts[0].startswith("batch-"):
        refresh_batch_sheet(out_root / rel.parts[0], rel.parts[1])
    with _lock:
        d = picks_load()
        changed = False
        for picks in d["users"].values():  # (everybody's stars on it)
            for k in [k for k in picks if k == str(p) or k.startswith(str(p) + "/")]:
                picks.pop(k)
                changed = True
        if changed:
            _save("picks.json", d)
    return str(rel)


def refresh_batch_sheet(batch: Path, cls: str) -> None:
    """Rebuilds a batch's comparison sheet for one kind after a sprite left or came back."""
    sheet = batch / f"sheet_{cls}.png"
    sets = sorted(str(d) + "/" for d in (batch / cls).glob("*/") if d.is_dir()) if (batch / cls).is_dir() else []
    if not sets:
        sheet.unlink(missing_ok=True)
        return
    subprocess.run(
        [sys.executable, "-W", "ignore", str(SCRIPTS / "pxtool.py"), "sheet", str(sheet), *sets],
        capture_output=True,
        timeout=120,
    )


def _du(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# --------------------------------------------------------------------------
# training runs on disk


SAMPLE_RE = re.compile(r"_(?:e(\d{6})|(\d{6}))_(\d{2})_\d+")


def train_info(cat: str, model: str = "sd15") -> dict:
    c = model_cfg(cat)
    sfx = BASES[model]["suffix"]
    name = c["trigger"] + sfx
    home = setting_path("lora_home") / cat
    data, out = home / ("dataset" + sfx), home / ("output" + sfx)
    info: dict[str, Any] = {
        "category": cat,
        "trigger": c["trigger"],
        "name": name,
        "model": model,
        "home": str(home),
        "data_dir": str(data),
    }
    rows = []
    img_dir = next(iter(sorted((data / "img").glob(f"*_{c['trigger']}"))), None) if (data / "img").is_dir() else None
    if img_dir:
        for f in sorted(img_dir.glob("*.png")):
            txt = f.with_suffix(".txt")
            rows.append({"id": f.stem, "image": str(f), "caption": txt.read_text().strip() if txt.exists() else ""})
    info["dataset"] = {
        "exists": bool(rows),
        "count": len(rows),
        "repeats": int(img_dir.name.split("_")[0]) if img_dir else None,
        "sheet": str(data / "sheet.png") if (data / "sheet.png").exists() else "",
        "rows": rows,
        "mtime": (data / "sheet.png").stat().st_mtime if (data / "sheet.png").exists() else None,
    }
    ckpts = []
    if out.is_dir():
        for f in sorted(out.glob(f"{name}*.safetensors")):
            m = re.search(r"-(\d+)$", f.stem)
            ckpts.append(
                {
                    "path": str(f),
                    "name": f.name,
                    "epoch": int(m.group(1)) if m else None,
                    "size": f.stat().st_size,
                    "mtime": f.stat().st_mtime,
                }
            )
    info["checkpoints"] = ckpts
    grid: dict[int, dict[int, str]] = {}
    grid_files: list[Path] = []
    prompts = []
    if (out / "sample_prompts.txt").exists():
        prompts = [p.split(" --n ")[0] for p in (out / "sample_prompts.txt").read_text().splitlines() if p.strip()]
    for f in sorted((out / "sample").glob("*.png")) if (out / "sample").is_dir() else []:
        m = SAMPLE_RE.search(f.stem)
        if not m:
            continue
        grid_files.append(f)
        ep = int(m.group(1)) if m.group(1) else -int(m.group(2))  # step-based samples: negative keys
        grid.setdefault(int(m.group(3)), {})[ep] = str(f)
    info["samples"] = {
        "times": {str(f): t for f in grid_files if (t := render_seconds(f)) is not None},
        "prompts": prompts,
        "grid": {str(k): {str(e): v for e, v in sorted(row.items())} for k, row in grid.items()},
    }
    final_epoch = last_train_epochs(cat, model)
    info["final_epoch"] = final_epoch
    try:
        todo = pm().preview_plan(pm().load(cat), out, name, BASES[model]["res"], None, PREVIEW_PROMPTS, final_epoch)
    except SystemExit:
        todo = []
    info["previews_todo"] = len(todo)
    info["previews_epochs"] = sorted({r["epoch"] for r in todo})
    info["previews_by_epoch"] = {str(e): sum(1 for r in todo if r["epoch"] == e) for e in info["previews_epochs"]}
    loras = lora_dir()
    info["installed"] = (
        [
            {"name": f.name, "mtime": f.stat().st_mtime, "size": f.stat().st_size}
            for f in sorted(loras.glob(f"{c['trigger']}*.safetensors"))
            if ("_xl" in f.stem) == (model == "sdxl")
        ]
        if loras.is_dir()
        else []
    )
    return info


PREVIEW_PROMPTS = 2  # previews rendered afterwards: the first N preview prompts of the run


def last_train_epochs(cat: str, model: str) -> int:
    """The epoch the final checkpoint file stands for: the epochs of the last training run of this model
    and base (its request), else the script's default."""
    with _lock:
        jobs = sorted(JOBS.jobs.values(), key=lambda j: -j["created"])
    for j in jobs:
        m = j["meta"]
        if j["kind"] == "train" and m.get("category") == cat and model_ok(m.get("model")) == model:
            try:
                return int((m.get("request") or {}).get("epochs") or BASES[model]["epochs"])
            except (TypeError, ValueError):
                break
    return BASES[model]["epochs"]


def sdxl_ready() -> bool:
    """The SDXL base model is there, whole (a download in progress is smaller)."""
    f = setting_path("comfy_dir") / "models" / "checkpoints" / "sd_xl_base_1.0.safetensors"
    return f.is_file() and f.stat().st_size > 6_000_000_000


def lora_dir() -> Path:
    return setting_path("comfy_dir") / "models" / "loras"


def lora_info(name: str, rec: dict | None = None) -> dict:
    """One LoRA file in ComfyUI: there or not, and where it came from when the app installed it."""
    f = lora_dir() / name
    out: dict[str, Any] = {"name": name, "installed": f.is_file(), "xl": "_xl" in Path(name).stem}
    if not out["installed"]:
        return out
    st = f.stat()
    out.update(size=st.st_size, mtime=st.st_mtime)
    r = (rec if rec is not None else _load("installed.json", {})).get(name)
    if r and r.get("size") == st.st_size and abs(r.get("mtime", 0) - st.st_mtime) < 2:
        out.update(
            {k: r.get(k) for k in ("model", "checkpoint", "epoch", "final", "trained")}, installed_at=r.get("installed")
        )
        if r.get("model_id"):  # (the model's id is what is kept; its name can change)
            out["model"] = ACCOUNTS.name_of_uid("model", r["model_id"]) or r.get("model")
    return out


def model_loras(m: dict) -> dict:
    """The LoRA files of a model in ComfyUI: the default one (<trigger>.safetensors, what generation
    uses), its SDXL twin, and the extra ones added to compare (<trigger>_*.safetensors)."""
    rec = _load("installed.json", {})
    default = lora_info(m["trigger"] + ".safetensors", rec)
    xl = lora_info(m["trigger"] + "_xl.safetensors", rec)
    names = sorted(f.name for f in lora_dir().glob(m["trigger"] + "_*.safetensors")) if lora_dir().is_dir() else []
    extra = [lora_info(n, rec) for n in names if n != xl["name"]]
    return {"default": default, "xl": xl, "extra": extra, "dir_ok": lora_dir().is_dir()}


def require_lora(m: dict, chosen: str = "") -> None:
    """Refuses a render job up front when its LoRA isn't in ComfyUI (every image would fail)."""
    if not lora_dir().is_dir():
        return  # ComfyUI's folder isn't visible from here: let the job tell
    name = chosen or m["trigger"] + ".safetensors"
    if chosen and (lora_dir() / name).is_file():
        lora_file(name, write=False)  # (a LoRA of a model the user cannot see is not theirs to use)
    if not (lora_dir() / name).is_file():
        raise HTTPException(
            400,
            f"No LoRA installed for {m['label']}: {name} isn't in ComfyUI's models/loras. "
            "Train the model, then on the Train page pick an epoch and click Use.",
        )


# --------------------------------------------------------------------------
# the web app

app = FastAPI(title="Sprite Studio", version=VERSION, docs_url=None, redoc_url=None, openapi_url=None)
JOBS = Jobs()
LOGIN_PAGE = HERE / "static" / "login.html"


class BodyTooLarge(Exception):
    pass


class BodyLimit:
    """Counts the bytes of every request body as they arrive: a Content-Length can be missing (HTTP/2, streaming)
    or false, so the limit is enforced on what is really read, whatever the headers say."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH", "DELETE"):
            await self.app(scope, receive, send)
            return
        seen, started = 0, False

        async def limited() -> Any:
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > MAX_BODY:
                    raise BodyTooLarge
            return msg

        async def tracked(msg: Any) -> None:
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, limited, tracked)
        except BodyTooLarge:
            if started:
                raise
            r = JSONResponse({"error": f"request too big (at most {MAX_BODY // 1_000_000} MB)"}, status_code=413)
            await r(scope, receive, send)


app.add_middleware(BodyLimit)


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    """The page and its script are always checked again, so a new version of
    the app shows after a restart without a forced reload."""
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    elif "json" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "private, no-store"  # what the API answers is for the signed-in user only
    else:
        response.headers.setdefault("Cache-Control", "private")  # images and files: kept by the browser, never shared
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _host_of(url: str) -> str:
    return re.sub(r"^[a-z][a-z0-9+.-]*://", "", url.strip().lower()).split("/")[0]


@app.middleware("http")
async def guard(request: Request, call_next):
    """Refuses cross-site requests that change something (a page on another site must not drive the app with
    the browser's saved password) and oversized or length-less bodies."""
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("origin")
        if origin and origin != "null":
            own = {request.headers.get("host", "").lower(), request.headers.get("x-forwarded-host", "").lower()}
            own |= {x.strip().lower() for x in os.environ.get("SG_ALLOWED_ORIGINS", "").split(",") if x.strip()}
            if _host_of(origin) not in own:
                return JSONResponse({"error": "cross-site request refused"}, status_code=403)
        elif request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "cross-site request refused"}, status_code=403)
        length = request.headers.get("content-length")
        if length is None and "chunked" in request.headers.get("transfer-encoding", "").lower():
            return JSONResponse({"error": "send a Content-Length"}, status_code=411)
        if length and length.isdigit() and int(length) > MAX_BODY:
            return JSONResponse({"error": f"request too big (at most {MAX_BODY // 1_000_000} MB)"}, status_code=413)
    return await call_next(request)


@app.exception_handler(UnidentifiedImageError)
async def _bad_image(request: Request, exc: Exception):
    return JSONResponse({"error": "not a readable image"}, status_code=400)


@app.exception_handler(Image.DecompressionBombError)
async def _big_image(request: Request, exc: Exception):
    return JSONResponse({"error": f"image too large (over {MAX_PIXELS // 1_000_000} million pixels)"}, status_code=400)


PUBLIC_API = {"/api/auth/state", "/api/auth/login", "/api/auth/setup"}
OWN_ACCOUNT_API = {
    "/api/auth/me",
    "/api/auth/password",
    "/api/auth/logout",
}  # all a user who must change their password may use
ADMIN_ONLY = re.compile(r"^/api/(settings$|comfy/|updates(/|$)|train/setup-sdxl$|users(?!/names$))")


@app.middleware("http")
async def auth(request: Request, call_next):
    """Everything needs a signed-in user (a session cookie): the login page is what a visitor gets, the API answers
    401. Someone who must change their password may only do that. Administrator-only routes are refused to users."""
    path = request.url.path
    who = ACCOUNTS.session(request.cookies.get(acc_cookie, ""))
    token = acc.CURRENT.set(who)
    writing = WRITING.set(request.method not in ("GET", "HEAD", "OPTIONS"))
    try:
        if path.startswith("/api/"):
            if path not in PUBLIC_API:
                if not who:
                    return JSONResponse({"error": "sign in", "login": True}, status_code=401)
                if who.must_change and path not in OWN_ACCOUNT_API:
                    return JSONResponse({"error": "change your password first", "must_change": True}, status_code=403)
                if ADMIN_ONLY.match(path) and not who.admin:
                    return JSONResponse({"error": "only an administrator can do this"}, status_code=403)
        elif (not who or who.must_change) and path in ("/", "/index.html"):
            return HTMLResponse(LOGIN_PAGE.read_text(), headers={"Cache-Control": "no-store"})
        return await call_next(request)
    finally:
        acc.CURRENT.reset(token)
        WRITING.reset(writing)


@app.exception_handler(acc.AccountError)
async def _account_error(request: Request, exc: Exception):
    assert isinstance(exc, acc.AccountError)
    return JSONResponse({"error": exc.message}, status_code=exc.status)


def body_get(b: dict, key: str, default: Any = None, kind: type = str) -> Any:
    v = b.get(key, default)
    if v in (None, ""):
        return default
    try:
        return kind(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{key}: not a valid {kind.__name__}") from None


def name_ok(s: str) -> str:
    s = re.sub(r"[^\w.-]+", "_", s.strip())[:80]
    if not s:
        raise HTTPException(400, "empty name")
    return s


@app.get("/api/status")
def api_status():
    with _lock:
        running = [JOBS.view(j) for j in JOBS.jobs.values() if j["status"] == "running"]
        queued = [JOBS.view(j, False) for jid in JOBS.queue if (j := JOBS.jobs.get(jid))]
    return {
        "comfy": comfy_status(),
        "gpu": gpu_info(),
        "disks": disks_info(),
        "running": running,
        "queued": queued,
        "time": time.time(),
        "version": VERSION,
    }


def _git(repo: Path, *args: str, timeout: int = 60) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout, check=False)
    if r.returncode:
        raise OSError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r.stdout.strip()


_updates_cache: dict[str, Any] = {"at": 0.0, "data": None}
_updates_lock = threading.Lock()
UPDATES_TTL = 15 * 60  # a check is reused for 15 minutes; ?force=1 looks again


_notes_cache: dict[tuple[str, str], dict] = {}


def disk_info(path: Path) -> dict:
    """Space of the volume that holds `path` (its nearest existing parent): total, used, free in bytes."""
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        u = shutil.disk_usage(p)
    except OSError:
        return {}
    return {"disk": {"total": u.total, "used": u.used, "free": u.free}}


def disks_info() -> list[dict]:
    """The disks the app works on (results, models and LoRAs, ComfyUI, its own state): one entry per volume with the
    folders on it, space in bytes."""
    s = settings()
    folders = [
        ("results", s["px_output"]),
        ("models", s["lora_home"]),
        ("ComfyUI", s["comfy_dir"]),
        ("app", str(STATE_DIR)),
    ]
    vols: dict[int, dict] = {}
    for label, f in folders:
        p = Path(f)
        while not p.exists() and p != p.parent:
            p = p.parent
        try:
            dev = p.stat().st_dev
            u = shutil.disk_usage(p)
        except OSError:
            continue
        v = vols.setdefault(dev, {"total": u.total, "used": u.used, "free": u.free, "what": []})
        v["what"].append(label)
    return list(vols.values())


def tree_size(path: Path) -> int:
    """Bytes under a folder (0 when it is missing); files that vanish meanwhile are skipped."""
    total = 0
    for f in Path(path).rglob("*") if Path(path).is_dir() else []:
        with contextlib.suppress(OSError):
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
    return total


def github_slug(url: str) -> str | None:
    """`owner/repo` of a GitHub remote (https or ssh form), or None for any other host."""
    m = re.match(
        r"^(?:https://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?$",
        url.strip(),
    )
    return m.group(1) if m else None


def release_links(remote: str, tag: str | None, want_notes: bool) -> dict:
    """The GitHub page of a repository, and (when a newer release exists) the page and notes of that release.
    Only github.com is asked, with a short timeout; any failure leaves the link and no notes."""
    slug = github_slug(remote)
    if not slug:
        return {}
    out: dict[str, Any] = {"repo_url": f"https://github.com/{slug}"}
    if not tag:
        return out
    out["release_url"] = f"https://github.com/{slug}/releases/tag/{tag}"
    if want_notes:
        key = (slug, tag)
        if key not in _notes_cache:
            notes = ""
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{slug}/releases/tags/{tag}",
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "sprite-studio"},
                )
                with urllib.request.urlopen(req, timeout=8) as r:  # noqa: S310  (a fixed https host)
                    notes = str(json.loads(r.read(200000)).get("body") or "")[:6000]
            except (OSError, ValueError):
                pass
            _notes_cache[key] = {"notes": notes}
        out.update(_notes_cache[key])
    return out


def check_repo_update(name: str, repo: Path) -> dict:
    """Compares a git checkout with the latest released version (the highest stable `vX.Y.Z` tag). `git fetch --tags`
    refreshes git's own records of the remote; the files of the checkout are never touched and nothing is installed."""
    info: dict[str, Any] = {"name": name, "path": str(repo)}
    if not (repo / ".git").exists():
        return {**info, "ok": False, "error": "not a git checkout"}
    try:
        _git(repo, "fetch", "--quiet", "--tags", "origin", timeout=90)
        tag = pxupdate.latest_release(_git(repo, "remote", "get-url", "origin"))
        if not tag:
            raise OSError("no released version (a vX.Y.Z tag) found upstream")
        at_or_past = (
            subprocess.run(
                ["git", "-C", str(repo), "merge-base", "--is-ancestor", tag, "HEAD"], capture_output=True, check=False
            ).returncode
            == 0
        )
        info.update(
            ok=True,
            current=_git(repo, "describe", "--tags", "--always"),
            latest=tag,
            behind=0 if at_or_past else 1,
            dirty=bool(_git(repo, "status", "--porcelain", "--untracked-files=no")),  # tracked files edited here
            **release_links(_git(repo, "remote", "get-url", "origin"), tag, not at_or_past),
        )
    except (OSError, ValueError, subprocess.TimeoutExpired, pxupdate.UpdateError) as e:
        info.update(ok=False, error=str(e)[:300])
    return info


def studio_state() -> dict:
    """What the Updates page shows for Sprite Studio itself (the pending / last result are local files)."""
    d: dict[str, Any] = {}
    pend = STATE_DIR / "update_pending"
    if pend.is_dir():
        d["pending"] = {k: (pend / k).read_text().strip() for k in ("from", "to") if (pend / k).exists()}
    last = STATE_DIR / "last_update.json"
    if last.exists():
        with contextlib.suppress(OSError, ValueError):
            d["last"] = json.loads(last.read_text())
    d["can_roll_back"] = bool(pxupdate.backups())
    d["backups_bytes"] = tree_size(STATE_DIR / "backups")
    return d


def check_studio_update() -> dict:
    info: dict[str, Any] = {
        "name": "Sprite Studio",
        "path": str(pxupdate.app_dir()),
        "studio": True,
        **studio_state(),
        **disk_info(pxupdate.app_dir()),
    }
    if (pxupdate.app_dir() / ".git").exists():
        return {
            **info,
            "ok": False,
            "error": "this is a git checkout: update it with git pull (an installation made by install.sh updates here)",
        }
    try:
        repo = os.environ.get("SG_UPDATE_REPO", pxupdate.STUDIO_REPO)
        tag = pxupdate.latest_release(repo)
        if not tag:
            raise OSError("no released version (a vX.Y.Z tag) found upstream")
        behind = pxupdate.version_key(tag) > pxupdate.version_key(VERSION)
        info.update(
            ok=True,
            current="v" + VERSION,
            latest=tag,
            behind=1 if behind else 0,
            dirty=False,
            **release_links(repo, tag, behind),
        )
    except (OSError, ValueError, subprocess.TimeoutExpired, pxupdate.UpdateError) as e:
        info.update(ok=False, error=str(e)[:300])
    return info


def restart_self(delay: float = 3.0) -> None:
    """Starts this program again in place (same process id, so systemd keeps watching it) after a short delay."""

    def go() -> None:
        time.sleep(delay)
        os.execv(sys.executable, [sys.executable, *sys.argv])  # noqa: S606

    threading.Thread(target=go, daemon=True).start()


def confirm_update_when_healthy(wait: float = 25.0) -> None:
    """After Sprite Studio updated itself: once the new version has answered for a while, the update is confirmed
    (the safety copy stays for a manual roll back); if it does not answer, it is rolled back and restarted."""
    pend = STATE_DIR / "update_pending"
    if not pend.is_dir():
        return
    try:
        target = (pend / "to").read_text().strip()
    except OSError:
        target = ""
    if target != VERSION:  # (a leftover from an update that never started, or a hand roll back)
        shutil.rmtree(pend, ignore_errors=True)
        return

    def check() -> None:
        time.sleep(wait)
        port = os.environ.get("SG_WEB_PORT", "8189")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/auth/state", timeout=5) as r:
                ok = r.status == 200
        except (OSError, ValueError):
            ok = False
        if ok:
            shutil.rmtree(pend, ignore_errors=True)
            _save_private_text(
                STATE_DIR / "last_update.json", json.dumps({"status": "ok", "to": VERSION, "at": time.time()})
            )
            return
        subprocess.run(
            [sys.executable, str(SCRIPTS / "pxupdate.py"), "studio-rollback", "the new version did not answer"],
            check=False,
            env=job_env(),
        )
        restart_self(1.0)

    threading.Thread(target=check, daemon=True).start()


def _save_private_text(path: Path, text: str) -> None:
    with _private_open(str(path), "w") as fh:
        fh.write(text)


@app.get("/api/updates")
def api_updates(force: int = 0):
    """Are there newer released versions of ComfyUI, kohya sd-scripts and Sprite Studio? The answer is kept for 15
    minutes and one check runs at a time (a second request waits for it and gets its answer); the update itself is a
    manual step."""
    with _updates_lock:
        c = _updates_cache
        if force or not c["data"] or time.time() - c["at"] > UPDATES_TTL:
            c["data"] = [
                check_studio_update(),
                check_repo_update("ComfyUI", setting_path("comfy_dir")),
                check_repo_update("kohya sd-scripts", setting_path("lora_home") / "sd-scripts"),
            ]
            c["at"] = time.time()
        items = [{**i, **studio_state()} if i.get("studio") else i for i in c["data"]]  # (pending / last stay fresh)
        return {"checked": c["at"], "items": items}


@app.post("/api/updates/apply")
async def api_updates_apply(request: Request):
    """Updates ComfyUI, kohya sd-scripts or Sprite Studio to their latest released version (scripts/pxupdate.py), as a
    job; a failed check puts the previous version back. Refused while a job uses the GPU, because the code and
    packages would change under it; Sprite Studio itself waits for every job to end and then restarts. Target
    "studio-rollback" puts the safety copy of the previous Sprite Studio version back."""
    b = await request.json()
    target = b.get("target")
    if target not in ("comfyui", "sd-scripts", "studio", "studio-rollback"):
        raise HTTPException(400, "choose comfyui, sd-scripts or studio")
    own = target in ("studio", "studio-rollback")
    with _lock:
        active = [j for j in JOBS.jobs.values() if j["status"] in ("running", "queued")]
        if own and active:
            raise HTTPException(409, "Sprite Studio restarts after its update: wait until no job is running")
        if any(j["lane"] == "gpu" for j in active):
            raise HTTPException(409, "a job is using the GPU: update when the queue is empty")
        if any(j["kind"] == "update" for j in active):
            raise HTTPException(409, "an update is running already")
    _updates_cache["at"] = 0.0  # (the next check looks again)
    title = {
        "comfyui": "Update ComfyUI",
        "sd-scripts": "Update kohya sd-scripts",
        "studio": "Update Sprite Studio",
        "studio-rollback": "Go back to the previous Sprite Studio",
    }[target]
    return JOBS.submit(
        "update",
        title,
        [sys.executable, str(SCRIPTS / "pxupdate.py"), target],
        lane="cpu",
        env={
            "COMFY": str(setting_path("comfy_dir")),
            "PX_LORA_HOME": str(setting_path("lora_home")),
            "COMFY_SERVICE": settings()["comfy_service"],
            "SG_WEB_STATE": str(STATE_DIR),
        },
        meta={"target": target},
    )


@app.post("/api/comfy/{action}")
def api_comfy(action: str):
    if action not in ("start", "stop", "restart"):
        raise HTTPException(400, "unknown action")
    ok, msg = comfy_service(action)
    if not ok:
        raise HTTPException(500, msg or f"systemctl {action} failed")
    return {"ok": True}


@app.get("/api/catalog")
def api_catalog():
    loras = []
    if lora_dir().is_dir():
        loras = [
            {"name": f.name, "size": f.stat().st_size, "mtime": f.stat().st_mtime}
            for f in sorted(lora_dir().glob("*.safetensors"))
        ]
    lora_models = {r["name"]: r["model"] for r in lora_rows(True, counts=False)}  # (once, not once per file)
    return {
        "models": models_summary(),
        "projects": [x["name"] for x in project_list() if x["name"]],
        "sources": sources(),
        "speed": speed(),
        "gpu_mb": gpu_memory_mb(),
        "loras": [x for x in loras if lora_allowed(lora_models.get(x["name"], ""))],
        "settings": settings() if is_admin() else {},
    }


# ---- projects: a name on each model (one per game); empty ones are kept in projects.json


def project_registry() -> dict:
    """The projects created with no model yet: [owner, name] pairs ("" owner: from before there were accounts)."""
    d = _load("projects.json", {})
    if "entries" not in d:
        d = {"entries": [["", n] for n in d.get("names", [])]}
    return d


def project_list() -> list[dict]:
    w = who_now()
    names = {n for o, n in project_registry()["entries"] if w is None or w.admin or (w.id and o == w.id)}
    models: dict[str, list[str]] = {}
    for m in models_summary():
        models.setdefault(m.get("project") or "", []).append(m["id"])
        if m.get("project"):
            names.add(m["project"])
    out = [
        {"name": n, "uid": ACCOUNTS.uid("project", n), "models": sorted(models.get(n, []))}
        for n in sorted(names, key=str.lower)
    ]
    if models.get(""):
        out.append({"name": "", "uid": "", "models": sorted(models[""])})
    return out


def project_name(v: Any) -> str:
    n = re.sub(r"\s+", " ", str(v or "")).strip()[:60]
    if not n:
        raise HTTPException(400, "give the project a name")
    return n


def _set_project_names(add: str = "", drop: str = "") -> None:
    w = who_now()
    me = w.id if w else ""
    with _lock:
        d = project_registry()
        # a user's project names are theirs; an administrator's changes reach everyone's
        entries = [e for e in d["entries"] if not (e[1] == drop and (w is None or w.admin or e[0] == me))]
        if add and [me, add] not in entries:
            entries.append([me, add])
        _save("projects.json", {"entries": entries})


@app.get("/api/projects")
def api_projects():
    return project_list()


@app.post("/api/projects")
async def api_project_create(request: Request):
    name = project_name((await request.json()).get("name"))
    if any(p["name"].lower() == name.lower() for p in project_list()):
        raise HTTPException(400, f"a project {name} exists already")
    _set_project_names(add=name)
    return project_list()


@app.post("/api/projects/rename")
async def api_project_rename(request: Request):
    """Renames a project (its models follow); renaming onto an existing one merges them."""
    b = await request.json()
    old, new = str(b.get("old") or ""), project_name(b.get("new"))
    p = pm()
    for mid in p.list_models():
        try:
            m = p.load(mid)
        except SystemExit:
            continue
        if (m.get("project") or "") == old and can("model", mid, write=True):
            m["project"] = new
            p.save(m)
    _set_project_names(add=new, drop=old)
    if not ACCOUNTS.uid(
        "project", new, mint=False
    ):  # (a plain rename keeps the project's id; a merge keeps the target's)
        ACCOUNTS.rename_id("project", old, new)
    return project_list()


@app.post("/api/projects/delete")
async def api_project_delete(request: Request):
    """Removes a project; its models move to another project ("move_to", "" = no project), never deleted."""
    b = await request.json()
    name, dest = str(b.get("name") or ""), str(b.get("move_to") or "")
    if not name:
        raise HTTPException(400, "which project?")
    if dest == name:
        raise HTTPException(400, "move the models to another project")
    p = pm()
    for mid in p.list_models():
        try:
            m = p.load(mid)
        except SystemExit:
            continue
        if (m.get("project") or "") == name and can("model", mid, write=True):
            m["project"] = dest
            p.save(m)
    _set_project_names(drop=name)
    ACCOUNTS.forget_id("project", name)
    if dest:
        _set_project_names(add=dest)
    return project_list()


@app.get("/api/sprites")
def api_sprites(root: str = "input", sub: str = ""):
    if root == "input":
        return sprite_list(setting_path("px_input"))
    if root == "game":
        if sub and ".." in sub:
            raise HTTPException(400, "bad folder")
        return sprite_list(setting_path("px_game"), sub, recursive=not sub)
    raise HTTPException(400, "root is input or game")


@app.get("/api/file")
def api_file(path: str = ""):
    if not path:
        raise HTTPException(404, "no file")
    p = safe_path(path)
    if not p.is_file():
        raise HTTPException(404, "no such file")
    if p.suffix.lower() not in SERVED_SUFFIXES:  # images and the small text files the pages read, not models or code
        raise HTTPException(403, "this kind of file is not served")
    if p.suffix.lower() in (".json", ".txt", ".csv", ".log") and p.stat().st_size > MAX_TEXT_SERVED:
        raise HTTPException(413, "this text file is too big to show")
    return FileResponse(
        p, headers={"Cache-Control": "private, no-cache"}
    )  # (kept by this browser only, checked each time)


_info_slots = threading.BoundedSemaphore(2)  # image analysis builds large arrays: at most two at a time


@app.get("/api/info")
def api_info(path: str):
    p = safe_path(path)
    if not _info_slots.acquire(timeout=20):
        raise HTTPException(503, "busy analysing other images: try again in a moment")
    try:
        return _analyse_image(p)
    finally:
        _info_slots.release()


def _analyse_image(p: Path) -> dict:
    import numpy as np
    from scipy import ndimage

    try:
        with Image.open(p) as im:
            w, h = im.size
            if w * h > INFO_PIXELS:  # read the size first: nothing is decoded for a huge image
                raise HTTPException(
                    400, f"image too large to analyse ({w}x{h} px, at most {INFO_PIXELS // 1_000_000} million)"
                )
            a = np.array(im.convert("RGBA"))
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(400, "not a readable image") from None
    alpha = a[..., 3] > 0
    rgb = a[alpha][:, :3].astype(np.uint32)
    cols = (
        len(np.unique((rgb[:, 0] << 16) | (rgb[:, 1] << 8) | rgb[:, 2])) if alpha.any() else 0
    )  # one number per colour
    _, parts = ndimage.label(alpha)
    return {
        "w": a.shape[1],
        "h": a.shape[0],
        "colours": int(cols),
        "parts": int(parts),
        "standard": cols <= 64 and a.shape[0] % 8 == 0 and a.shape[1] % 8 == 0,
    }


@app.post("/api/upload")
async def api_upload(request: Request):
    b = await request.json()
    raw = decode_data_url(b.get("data"))
    check_image(raw, "PNG")
    w = who_now()
    folder = setting_path("px_output") / "uploads" / ((w.id or w.name) if w else "_app")
    folder.mkdir(parents=True, exist_ok=True)
    f = folder / f"{dt.datetime.now():%Y%m%d-%H%M%S}_{name_ok(b.get('name', 'sketch.png'))}"
    if f.suffix != ".png":
        f = f.with_suffix(".png")
    f.write_bytes(raw)
    return {"path": str(f)}


# ---- jobs


@app.get("/api/jobs")
def api_jobs():
    with _lock:
        return [
            JOBS.view(j, j["status"] == "running")
            for j in sorted(JOBS.jobs.values(), key=lambda j: -j["created"])
            if job_mine(j) or j["status"] in ("running", "queued")
        ]


@app.get("/api/jobs/{jid}")
def api_job(jid: str):
    j = job_get(jid)
    v = JOBS.view(j)
    if j.get("out") and j["kind"] == "generate" and Path(j["out"]).is_dir():
        v["result"] = result_set(Path(j["out"]))
    return v


@app.get("/api/jobs/{jid}/log", response_class=PlainTextResponse)
def api_job_log(jid: str, tail: int = 60000):
    j = job_get(jid)
    text = read_log(j, tail)
    # tqdm redraws one line with \r: keep only the last state of each line
    return "\n".join(line.split("\r")[-1] for line in text.split("\n"))


@app.post("/api/jobs/{jid}/cancel")
def api_job_cancel(jid: str):
    job_get(jid)
    return JOBS.view(JOBS.cancel(jid), False)


@app.post("/api/jobs/{jid}/retry")
def api_job_retry(jid: str):
    j = job_get(jid)
    cmd = list(j["cmd"])
    out = j["out"]
    if j["kind"] == "generate" and "--out" in cmd:
        out = re.sub(r"_\d{8}-\d{6}$", "", out) + f"_{dt.datetime.now():%Y%m%d-%H%M%S}"
        cmd[cmd.index("--out") + 1] = out
    return JOBS.submit(j["kind"], j["title"], cmd, lane=j["lane"], env=j["env"], out=out, meta=j["meta"])


@app.delete("/api/jobs")
def api_jobs_clear(only: str = ""):
    """Removes finished jobs: only the successful ones with ?only=done, else every one that is not running or waiting."""
    with _lock:
        gone = (
            [k for k, j in JOBS.jobs.items() if j["status"] == "done" and job_mine(j)]
            if only == "done"
            else [k for k, j in JOBS.jobs.items() if j["status"] not in ("running", "queued") and job_mine(j)]
        )
        for jid in gone:
            j = JOBS.jobs.pop(jid)
            with contextlib.suppress(OSError):
                os.unlink(j["log"])
        JOBS.persist()
    return {"ok": True}


def lora_env(b: dict, m: dict | None = None) -> dict:
    env = {}
    if b.get("lora"):
        env["PX_LORA"] = name_ok(b["lora"])
    used = env.get("PX_LORA") or (m["trigger"] + ".safetensors" if m else "")
    if used and (lora_dir() / used).is_file():
        env["PX_LORA_ID"] = lora_id(used)  # (written in the result's job.txt)
    if b.get("strength") not in (None, ""):
        env["PX_LORA_STRENGTH"] = str(body_get(b, "strength", 1.0, float))
    return env


@app.post("/api/generate")
async def api_generate(request: Request):
    b = await request.json()
    count = body_get(b, "count", 4, int)
    if not 1 <= count <= 32:
        raise HTTPException(400, "between 1 and 32 images")
    stamp = f"{dt.datetime.now():%Y%m%d-%H%M%S}"
    m = model_cfg(body_model(b), write=False)
    group = next((g for g in m["groups"] if g["id"] == b.get("group")), None)
    if not group:
        raise HTTPException(400, "choose a group")
    kind = next((k for k in m["kinds"] if k["id"] == b.get("kind")), None)
    if m["kinds"] and not kind:
        raise HTTPException(400, "choose a kind")
    require_lora(m, name_ok(b["lora"]) if b.get("lora") else "")
    name = name_ok(b.get("name") or f"{kind['id'] if kind else m['id']}_{group['id']}")
    out = str(setting_path("px_output") / f"{name}_{stamp}")
    claim_folder(Path(out).name)
    cmd = [
        str(SCRIPTS / "pxmake.sh"),
        "--model",
        m["id"],
        "--group",
        group["id"],
        "-n",
        str(count),
        "--name",
        name,
        "--out",
        out,
    ]
    if kind:
        cmd += ["--kind", kind["id"]]
    if b.get("refs_kind"):
        cmd += ["--refs-kind", name_ok(b["refs_kind"])]
    if (b.get("extra") or "").strip():
        cmd += ["--extra", b["extra"].strip()]
    if b.get("keep_green") is True:
        cmd.append("--keep-green")
    if b.get("no_tidy"):
        cmd.append("--no-tidy")
    if b.get("outline_style") in ("dark", "selective"):
        cmd += ["--outline-style", b["outline_style"]]
    if b.get("palette"):
        cmd += ["--palette", palette_ref(b["palette"])]
    if b.get("fill") not in (None, ""):
        cmd += ["--fill", str(fill_value(b["fill"]))]
    title = (
        f"{m['label']}: {group['label']}"
        + (f" {kind['label'].lower()}" if kind else "")
        + (f", {b['extra'].strip()}" if (b.get("extra") or "").strip() else "")
    )
    start = b.get("start") or "none"
    if start == "sprite":
        if not b.get("init"):
            raise HTTPException(400, "pick the sprite to start from")
        cmd += ["--init", str(safe_path(b["init"], write=False))]
        if b.get("mask"):
            cmd += ["--mask", str(safe_path(b["mask"], write=False))]
    elif start == "sketch":
        if not b.get("sketch"):
            raise HTTPException(400, "upload a sketch")
        cmd += ["--sketch", str(safe_path(b["sketch"], write=False))]
    if start in ("sprite", "sketch") and b.get("denoise") not in (None, ""):
        cmd += ["--denoise", str(body_get(b, "denoise", 0.6, float))]
    if b.get("colours"):
        cmd += ["--colours", b["colours"].strip()]
    if b.get("seed") not in (None, ""):
        cmd += ["--seed", str(body_get(b, "seed", 0, int))]
    if b.get("size") not in (None, ""):
        size = body_get(b, "size", 64, int)
        if size % 8 or not 8 <= size <= 1024:
            raise HTTPException(400, "size: a multiple of 8, up to 1024")
        cmd += ["--size", str(size)]
    if b.get("mirror") in ("auto", "none", "left", "right"):
        cmd += ["--mirror", b["mirror"]]
    if b.get("mask"):
        title = "Touch-up: " + title
    return JOBS.submit("generate", title, cmd, env=lora_env(b, m), out=out, meta={"count": count, "request": b})


@app.post("/api/batch/plan")
async def api_batch_plan(request: Request):
    b = await request.json()
    cmd = batch_cmd(b) + ["--dry-run"]
    r = subprocess.run(cmd, capture_output=True, text=True, env=job_env(), cwd=str(SCRIPTS), timeout=60)
    lines = r.stdout.splitlines()
    todo = [re.search(r"--name (\S+)", ln).group(1) for ln in lines if ln.startswith("would run:") and "--name" in ln]
    m = re.search(r"already done: (\d+)", r.stdout)
    skipped = [ln.strip()[9:] for ln in lines if ln.strip().startswith("skipped:")]
    return {"todo": todo, "done": int(m.group(1)) if m else 0, "skipped": skipped, "error": r.stderr.strip()[-500:]}


def default_batch_tag(m: dict, b: dict) -> str:
    """The folder name of a batch nobody named: model, LoRA and date, so that batches of other models or LoRAs
    on the same day do not end up in one folder (and a rerun of the same one still continues)."""
    lora = re.sub(r"\.safetensors$", "", name_ok(b["lora"])) if b.get("lora") else "default"
    mode = "-invent" if b.get("mode") == "invent" else ""
    return f"{m['id']}-{lora}-{dt.date.today():%Y%m%d}{mode}"


def batch_cmd(b: dict) -> list[str]:
    m = model_cfg(body_model(b), write=False)
    cmd = [
        str(SCRIPTS / "pxbatch.sh"),
        "--model",
        m["id"],
        "--tag",
        name_ok(b.get("tag") or default_batch_tag(m, b)),
    ]
    groups = [g["id"] for g in m["groups"] if g["id"] in (b.get("groups") or [])]
    kinds = [k["id"] for k in m["kinds"] if k["id"] in (b.get("kinds") or [])]
    if groups:
        cmd += ["--groups", ",".join(groups)]
    if kinds:
        cmd += ["--kinds", ",".join(kinds)]
    cmd += ["-n", str(body_get(b, "count", 2, int))]
    if b.get("mode") == "invent":
        n = body_get(b, "invent", 4, int)
        if not 1 <= n <= 50:
            raise HTTPException(400, "between 1 and 50 new sprites per group and kind")
        cmd += ["--invent", str(n)]
        ideas = [x.strip().replace("|", "/") for x in str(b.get("ideas") or "").splitlines() if x.strip()]
        if ideas:
            cmd += ["--ideas", "|".join(ideas)]
    if b.get("denoise") not in (None, ""):
        cmd += ["--denoise", str(body_get(b, "denoise", 0.7, float))]
    if b.get("palette"):
        cmd += ["--palette", palette_ref(b["palette"])]
    if b.get("fill") not in (None, ""):
        cmd += ["--fill", str(fill_value(b["fill"]))]
    return cmd


def fill_value(v: Any) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise HTTPException(400, "fill is a percentage, 50 to 100 (0: the render's own scale)") from None
    if n and not 50 <= n <= 100:
        raise HTTPException(400, "fill is 50 to 100 % (0: the render's own scale)")
    return n


@app.post("/api/batch")
async def api_batch(request: Request):
    b = await request.json()
    bm = model_cfg(body_model(b), write=False)
    require_lora(bm, name_ok(b["lora"]) if b.get("lora") else "")
    plan = await api_batch_plan(request)
    cmd = batch_cmd(b)
    tag = cmd[cmd.index("--tag") + 1]
    out = str(setting_path("px_output") / f"batch-{tag}")
    claim_folder(Path(out).name)
    return JOBS.submit(
        "batch", f"Batch {tag}", cmd, env=lora_env(b, bm), out=out, meta={"total": len(plan["todo"]), "request": b}
    )


@app.post("/api/matrix")
async def api_matrix(request: Request):
    b = await request.json()
    loras = [name_ok(x) for x in b.get("loras") or []]
    if not loras:
        raise HTTPException(400, "choose at least one LoRA")
    for x in loras:
        if existing_lora(x):
            lora_file(x, write=False)  # (only LoRAs of models the user may use)
    strengths = " ".join(str(float(s)) for s in re.split(r"[ ,]+", str(b.get("strengths") or "1.0")) if s)
    tag = name_ok(b.get("tag") or f"{dt.datetime.now():%Y%m%d-%H%M}")
    labels = [re.sub(r"\.safetensors$", "", x) for x in loras]
    m = model_cfg(body_model(b), write=False)
    env = {
        "MODEL": m["id"],
        "LORAS": " ".join(f"{ln}:{f}" for ln, f in zip(labels, loras, strict=False)),
        "STRENGTHS": strengths,
        "TAG": tag,
        "SEED": str(body_get(b, "seed", 1000, int)),
    }
    out = str(setting_path("px_output") / f"matrix-{tag}")
    claim_folder(Path(out).name)
    return JOBS.submit(
        "matrix",
        f"LoRA comparison: {m['label']} {tag}",
        [str(SCRIPTS / "pxmatrix.sh")],
        env=env,
        out=out,
        meta={"request": b},
    )


# ---- training


@app.get("/api/train/{cat}")
def api_train(cat: str, model: str = "sd15"):
    model_cfg(cat)
    model = model_ok(model)
    info = train_info(cat, model)
    info["sdxl_ready"] = sdxl_ready()
    with _lock:
        jobs = [
            JOBS.view(j)
            for j in sorted(JOBS.jobs.values(), key=lambda j: -j["created"])
            if j["kind"] in ("train", "dataset", "setup", "previews")
            and job_mine(j)
            and j["meta"].get("category") in (cat, None)
            and model_ok(j["meta"].get("model")) == model
        ][:5]
    info["jobs"] = jobs
    return info


@app.post("/api/train/{cat}/dataset")
async def api_train_dataset(cat: str, request: Request):
    mcfg = model_cfg(cat)
    b = await request.json()
    model = model_ok(b.get("model"))
    env = {"CATEGORY": cat, "MODEL": model}
    if b.get("repeats"):
        env["REPEATS"] = str(body_get(b, "repeats", 0, int))
    return JOBS.submit(
        "dataset",
        f"Training set: {mcfg['label']}",
        [str(SCRIPTS / "train_lora.sh"), "dataset"],
        lane="cpu",
        env=env,
        meta={"category": cat, "model": model},
    )


@app.post("/api/train/{cat}/start")
async def api_train_start(cat: str, request: Request):
    mcfg = model_cfg(cat)
    b = await request.json()
    model = model_ok(b.get("model"))
    if not train_info(cat, model)["dataset"]["exists"]:
        raise HTTPException(400, "build the training set first")
    if model == "sdxl" and not sdxl_ready():
        raise HTTPException(
            400,
            "the SDXL model is not downloaded yet: click Download it at the top of the page, "
            "wait for that job to finish (Jobs), then train",
        )
    env = {"CATEGORY": cat, "MODEL": model}
    for key, kind in (("EPOCHS", int), ("DIM", int), ("LR", float)):
        if b.get(key.lower()) not in (None, ""):
            env[key] = str(body_get(b, key.lower(), None, kind))
    if b.get("precision") in ("auto", "no", "fp16", "bf16"):
        env["PRECISION"] = b["precision"]
    title = f"Training: {mcfg['label']}" + (" (SDXL)" if model == "sdxl" else "")
    return JOBS.submit(
        "train",
        title,
        [str(SCRIPTS / "train_lora.sh"), "train"],
        env=env,
        meta={
            "category": cat,
            "model": model,
            "needs_free_gpu": True,
            "request": b,
            "then_previews": bool(b.get("previews_after")),
        },
    )


def submit_previews(cat: str, model: str, epochs: list[int] | None = None, prompts: int = PREVIEW_PROMPTS) -> dict:
    """A job rendering the training run's missing previews (pxpreviews.sh) in the GPU lane."""
    mcfg = model_cfg(cat)
    out = setting_path("lora_home") / cat / ("output" + BASES[model]["suffix"])
    if not any(out.glob("*.safetensors")):
        raise HTTPException(400, "no checkpoint to render: train this model first")
    env = {
        "CATEGORY": cat,
        "MODEL": model,
        "PROMPTS": str(max(1, min(8, prompts))),
        "FINAL_EPOCH": str(last_train_epochs(cat, model)),
    }
    if epochs:
        env["EPOCHS"] = ",".join(str(int(e)) for e in sorted(set(epochs)))
    return JOBS.submit(
        "previews",
        f"Previews: {mcfg['label']}" + (" (SDXL)" if model == "sdxl" else ""),
        [str(SCRIPTS / "pxpreviews.sh")],
        env=env,
        meta={"category": cat, "model": model},
    )


def follow_up(job: dict) -> None:
    """What a finished job asked for next: a training run started with "render previews afterwards"; the app itself
    restarting after it updated (or rolled back) its own files."""
    if (
        job["kind"] == "update"
        and job["meta"].get("target") in ("studio", "studio-rollback")
        and job["status"] == "done"
    ):
        if job["meta"].get("restart", True):
            restart_self(3.0)
        return
    if job["kind"] != "train" or job["status"] != "done" or not job["meta"].get("then_previews"):
        return
    cat, model = job["meta"]["category"], model_ok(job["meta"].get("model"))
    try:
        queued = submit_previews(cat, model)
        if isinstance(queued, dict):  # (the previews belong to whoever trained)
            queued["owner"] = job.get("owner", "")
    except HTTPException as e:
        JOBS._log(job, f"previews not queued: {e.detail}")


@app.post("/api/train/{cat}/previews")
async def api_train_previews(cat: str, request: Request):
    """Renders the previews a run is missing. Body: {"model": "sd15"|"sdxl", "epochs": [2, 4] (default: all),
    "prompts": 2}."""
    b = await request.json()
    model = model_ok(b.get("model"))
    epochs = [int(e) for e in b["epochs"]] if b.get("epochs") else None
    return submit_previews(cat, model, epochs, body_get(b, "prompts", PREVIEW_PROMPTS, int))


@app.post("/api/train/setup-sdxl")
def api_setup_sdxl():
    """Downloads the SDXL base model (about 7 GB) with train_lora.sh setup."""
    return JOBS.submit(
        "setup",
        "Download the SDXL model",
        [str(SCRIPTS / "train_lora.sh"), "setup"],
        lane="cpu",
        env={"MODEL": "sdxl"},
        meta={"model": "sdxl"},
    )


@app.put("/api/train/{cat}/caption")
async def api_train_caption(cat: str, request: Request):
    b = await request.json()
    info = train_info(cat, model_ok(b.get("model")))
    row = next((r for r in info["dataset"]["rows"] if r["id"] == b.get("id")), None)
    if not row:
        raise HTTPException(404, "no such image")
    caption = " ".join((b.get("caption") or "").split())
    trig = model_cfg(cat)["trigger"]
    if not caption.startswith(trig):
        raise HTTPException(400, f"a caption starts with the trigger word {trig}")
    Path(row["image"]).with_suffix(".txt").write_text(caption + "\n")
    csv_path = Path(info["data_dir"]) / "captions.csv"
    rows = [(r["id"], caption if r["id"] == row["id"] else r["caption"]) for r in info["dataset"]["rows"]]
    with open(csv_path, "w", newline="") as fh:
        csv.writer(fh).writerows([("id", "caption")] + rows)
    return {"ok": True}


def lora_id(name: str) -> str:
    """The stable id of a LoRA file (a UUID in the ids registry, made on first use). Results and gallery items
    point at this id, never at the file name, so renaming a file changes nothing for them."""
    legacy = _load("lora_ids.json", {})  # (0.3.3 development builds kept them in a file of their own)
    if name in legacy and not ACCOUNTS.uid("lora", name, mint=False):
        with ACCOUNTS.lock:
            ids = ACCOUNTS._ids(True)
            ids.setdefault("lora", {})[name] = legacy[name]
            ACCOUNTS._write("ids.json", ids)
    return ACCOUNTS.uid("lora", name)


def lora_name_of(lid: str) -> str:
    """The current file name of the LoRA with this id ('' when it is gone)."""
    n = ACCOUNTS.name_of_uid("lora", lid)
    return n if n and (lora_dir() / n).is_file() else ""


def move_lora_id(old: str, new: str) -> None:
    """A LoRA file renamed or archived keeps its id."""
    ACCOUNTS.rename_id("lora", old, new)


def _rewrite_head(jt: Path, lid: str) -> None:
    """Adds `lora_id=` to the first line of a job.txt that has only the file name (older results)."""
    text = jt.read_text(errors="replace")
    first, sep, rest = text.partition("\n")
    tmp = jt.with_name(jt.name + ".tmp")
    tmp.write_text(f"{first} lora_id={lid}{sep}{rest}")
    tmp.replace(jt)


def adopt_legacy_refs(f: Path) -> None:
    """Before a LoRA file is renamed: gives the id to the older results (job.txt with only `lora=<name>`) and
    gallery items (name and content hash) that point at it, so they follow the file afterwards."""
    lid = lora_id(f.name)
    root = setting_path("px_output")
    if root.is_dir():
        for pattern in ("*/job.txt", "*/*/job.txt", "*/*/*/job.txt", ".bin/*/job.txt", ".bin/*/*/job.txt"):
            for jt in root.glob(pattern):
                try:
                    with open(jt, errors="replace") as fh:
                        head = fh.readline()
                    if re.search(rf"\blora={re.escape(f.name)}(\s|$)", head) and "lora_id=" not in head:
                        _rewrite_head(jt, lid)
                except OSError:
                    continue
    sha = ""
    for m in gallery_metas():
        if m.get("lora_id"):
            continue
        if m.get("lora_sha"):
            sha = sha or file_sha(f)
            hit = m["lora_sha"] == sha
        else:
            hit = m.get("lora") == f.name
        if hit:
            m["lora_id"] = lid
            _gallery_write(m["id"], m)


def archive_lora(f: Path) -> Path:
    """Renames a LoRA file that is about to be replaced to `<name>_<unix time>.safetensors` (its install record
    follows) so that nothing a new install does can change an old file; returns the new path."""
    stamp = int(f.stat().st_mtime)
    new = f.with_name(f"{f.stem}_{stamp}.safetensors")
    n = 0
    while new.exists():
        n += 1
        new = f.with_name(f"{f.stem}_{stamp}_{n}.safetensors")
    with _lock:
        f.rename(new)
        rec = _load("installed.json", {})
        if f.name in rec:
            rec[new.name] = rec.pop(f.name)
            _save("installed.json", rec)
    move_lora_id(f.name, new.name)
    return new


_sha_cache: dict[tuple[str, int, int], str] = {}


def file_sha(f: Path) -> str:
    """SHA-256 of a (large) file, kept while its size and time stay the same."""
    st = f.stat()
    key = (str(f), st.st_size, int(st.st_mtime))
    if key not in _sha_cache:
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[key] = h.hexdigest()
    return _sha_cache[key]


def lora_references(f: Path, scan: dict[str, int] | None = None) -> dict[str, int]:
    """What still needs this LoRA file: result folders rendered with it (every user's) and gallery items saved
    with it. They point at the file's id; older ones that have only the name (or name and content hash) are
    matched by that until the file is renamed, which gives them the id."""
    counts = scan if scan is not None else _count_lora_results(setting_path("px_output"))
    lid = lora_id(f.name)
    results = counts.get(lid, 0) + counts.get(f.name, 0)
    sha = ""
    n = 0
    for m in gallery_metas():
        if m.get("lora_id"):
            n += m["lora_id"] == lid
        elif m.get("lora_sha"):
            sha = sha or file_sha(f)
            n += m["lora_sha"] == sha
        else:
            n += m.get("lora") == f.name
    return {"results": results, "gallery": n}


def install_lora(src: Path, name: str, model: str, meta: dict | None = None) -> dict:
    """Copies a LoRA file into ComfyUI's loras folder as `name` and records where it came from
    (`meta` overrides what is read from the source's own name)."""
    lora_dir().mkdir(parents=True, exist_ok=True)
    dst = lora_dir() / name
    if dst.exists():
        archive_lora(dst)  # (never overwritten: results and gallery items made with it stay reproducible)
    shutil.copy2(src, dst)
    m = re.search(r"-(\d+)$", src.stem)
    entry = {
        "model": model,
        "model_id": ACCOUNTS.uid("model", model) if model else "",
        "checkpoint": src.name,
        "epoch": int(m.group(1)) if m else None,
        "final": not m,
        "trained": src.stat().st_mtime,
    }
    entry.update(meta or {})
    entry.update(installed=time.time(), size=dst.stat().st_size, mtime=dst.stat().st_mtime)
    with _lock:
        rec = _load("installed.json", {})
        rec[name] = entry
        _save("installed.json", rec)
    return entry


def lora_in_use_message(name: str, refs: dict[str, int], what: str) -> str:
    bits = []
    if refs["results"]:
        bits.append(f"{refs['results']} result{'s' if refs['results'] != 1 else ''}")
    if refs["gallery"]:
        bits.append(f"{refs['gallery']} gallery image{'s' if refs['gallery'] != 1 else ''}")
    return f"{name} cannot be {what}: {' and '.join(bits)} still use it. Delete them first (a gallery image keeps its LoRA). Renaming is always possible."


def lora_filename(s: str) -> str:
    """A safe LoRA file name from what the browser sent (always ends in .safetensors)."""
    name = name_ok(s)
    return name if name.endswith(".safetensors") else name + ".safetensors"


@app.post("/api/train/{cat}/install")
async def api_train_install(cat: str, request: Request):
    b = await request.json()
    src = safe_path(b.get("checkpoint", ""))
    if src.suffix != ".safetensors" or not src.is_file():
        raise HTTPException(400, "not a checkpoint")
    name = lora_filename(b.get("as") or model_cfg(cat)["trigger"])
    if not is_admin() and lora_guess_model(name, model_triggers()) != cat:
        raise HTTPException(
            400, f"a LoRA file's name starts with the model's trigger word ({model_cfg(cat)['trigger']})"
        )
    install_lora(src, name, cat)
    return {"installed": name, "lora": lora_info(name)}


# ---- LoRA files in ComfyUI: list, use, rename, delete


def model_triggers() -> dict[str, dict]:
    """id -> {label, trigger} of every model that loads."""
    out = {}
    p = pm()
    for mid in p.list_models():
        try:
            m = p.load(mid)
        except SystemExit:
            continue
        out[mid] = {"label": m["label"], "trigger": m["trigger"]}
    return out


_lora_count_cache: dict[str, Any] = {"at": 0.0, "root": "", "data": {}}


def lora_results_count() -> dict[str, int]:
    """How many result folders were rendered with each LoRA file (by `lora_id=`, else `lora=`, of every job.txt). Reading them all
    is slow with thousands of results, so the answer is kept for 20 seconds."""
    root = setting_path("px_output")
    c = _lora_count_cache
    if c["root"] == str(root) and time.time() - c["at"] < 20:
        return c["data"]
    counts = _count_lora_results(root)
    c.update(at=time.time(), root=str(root), data=counts)
    return counts


def _count_lora_results(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not root.is_dir():
        return counts
    for pattern in ("*/job.txt", "*/*/job.txt", "*/*/*/job.txt"):
        for jt in root.glob(pattern):
            if any(part.startswith(".") for part in jt.relative_to(root).parts):
                continue  # the bin
            try:
                with open(jt, errors="replace") as fh:
                    head = fh.readline()
            except OSError:
                continue
            m = re.search(r"\blora_id=([0-9a-f]{32})\b", head) or re.search(r"\blora=(\S+)", head)
            if m:  # (by id when the result has one, else by file name)
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    return counts


def lora_guess_model(name: str, models: dict[str, dict]) -> str:
    """The model a LoRA file belongs to by its name (the longest trigger it starts with)."""
    best = ""
    for mid, m in models.items():
        t = m["trigger"]
        if (
            name.startswith(t)
            and re.match(r"[._-]", name[len(t) :] or ".")
            and len(t) > len(models.get(best, {}).get("trigger", ""))
        ):
            best = mid
    return best


def lora_allowed(model: str, write: bool = False) -> bool:
    """A LoRA file follows its model's rules; one with no known model is for administrators."""
    w = who_now()
    if w is None or w.admin:
        return True
    return bool(model) and can("model", model, write)


def lora_rows(everything: bool = False, counts: bool = True) -> list[dict]:
    models = model_triggers()
    rec = _load("installed.json", {})
    used = lora_results_count() if counts else {}  # (reading every job.txt of the results is the slow part)
    defaults = {}
    for mid, m in models.items():
        defaults[m["trigger"] + ".safetensors"] = (mid, "SD 1.5")
        defaults[m["trigger"] + "_xl.safetensors"] = (mid, "SDXL")
    gal: dict[str, int] = {}
    ids = ACCOUNTS._ids().get("lora", {})
    by_id = {i: n for n, i in ids.items()}
    for m in gallery_metas():  # (by id, else by name; the file's content is checked when deleting)
        k = by_id.get(m.get("lora_id", ""), m.get("lora", ""))
        gal[k] = gal.get(k, 0) + 1
    rows = []
    for f in sorted(lora_dir().glob("*.safetensors")) if lora_dir().is_dir() else []:
        info = lora_info(f.name, rec)
        model = info.get("model") or lora_guess_model(f.name, models)
        epoch, final = info.get("epoch"), info.get("final")
        if "checkpoint" not in info:  # copied outside the app: read what the name says
            em = re.search(r"_e(\d+)(?:_xl)?$", f.stem) or re.search(r"-(\d+)$", f.stem)
            epoch = int(em.group(1)) if em else None
        d = defaults.get(f.name)
        if not everything and not lora_allowed(model):
            continue
        rows.append(
            {
                "name": f.name,
                "size": info["size"],
                "mtime": info["mtime"],
                "model": model,
                "model_label": models.get(model, {}).get("label", ""),
                "base": "SDXL" if info["xl"] else "SD 1.5",
                "epoch": epoch,
                "final": bool(final),
                "known": "checkpoint" in info,
                "trained": info.get("trained"),
                "installed_at": info.get("installed_at"),
                "checkpoint": info.get("checkpoint"),
                "in_use": d[1] if d else "",
                "in_use_model": d[0] if d else "",
                "uid": lora_id(f.name),
                "results": used.get(ids.get(f.name, ""), 0) + used.get(f.name, 0),
                "gallery": gal.get(f.name, 0),
                **ACCOUNTS.tag("model", model or "-"),
            }
        )
    return rows


@app.get("/api/loras")
def api_loras():
    free = None
    if lora_dir().is_dir():
        free = shutil.disk_usage(lora_dir()).free
    return {"dir_ok": lora_dir().is_dir(), "files": lora_rows(), "disk_free": free}


def existing_lora(name: str) -> Path | None:
    """The LoRA file of this exact name in ComfyUI's folder (a file made outside the app may have spaces
    that name_ok would change), else the one lora_filename gives; None when there is none."""
    for n in (name if name.endswith(".safetensors") else name + ".safetensors", lora_filename(name)):
        if n == Path(n).name and (lora_dir() / n).is_file():
            return lora_dir() / n
    return None


def lora_file(name: str, write: bool = True) -> Path:
    f = existing_lora(name)
    if f is None:
        raise HTTPException(404, f"{lora_filename(name)} is not in ComfyUI's models/loras")
    row = next((r for r in lora_rows(True, counts=False) if r["name"] == f.name), None)
    if not lora_allowed((row or {}).get("model", ""), write):
        raise HTTPException(404 if not lora_allowed((row or {}).get("model", "")) else 403, "not yours to change")
    return f


@app.post("/api/loras/use")
async def api_loras_use(request: Request):
    """Installs a LoRA file as its model's default (<trigger>.safetensors, or <trigger>_xl.safetensors for SDXL)."""
    b = await request.json()
    src = lora_file(b.get("name", ""))
    row = next((r for r in lora_rows(True, counts=False) if r["name"] == src.name), None)
    mid = b.get("model") or (row or {}).get("model")
    models = model_triggers()
    if mid not in models:
        raise HTTPException(400, "Which model is this LoRA for? Its name does not tell.")
    need("model", mid, True)
    dst = models[mid]["trigger"] + ("_xl" if "_xl" in src.stem else "") + ".safetensors"
    if dst == src.name:
        raise HTTPException(400, "This LoRA is already the one in use.")
    meta = {k: row[k] for k in ("epoch", "final", "checkpoint", "trained")} if row and row["known"] else {}
    install_lora(src, dst, mid, meta)
    return {"installed": dst}


@app.post("/api/loras/rename")
async def api_loras_rename(request: Request):
    b = await request.json()
    src = lora_file(b.get("name", ""))
    dst = lora_dir() / lora_filename(b.get("to", ""))
    reserved = {m["trigger"] + sfx + ".safetensors" for m in model_triggers().values() for sfx in ("", "_xl")}
    if src.name in reserved:
        raise HTTPException(400, "This is the LoRA a model generates with: install another one with Use instead.")
    if dst.name in reserved:
        raise HTTPException(400, "That name is a model's own: use the Use button to install a LoRA as the one in use.")
    if dst.exists():
        raise HTTPException(400, f"{dst.name} already exists")
    adopt_legacy_refs(src)  # (results and gallery items follow the id from now on)
    with _lock:
        src.rename(dst)
        rec = _load("installed.json", {})
        if src.name in rec:
            rec[dst.name] = rec.pop(src.name)
            _save("installed.json", rec)
    move_lora_id(src.name, dst.name)
    return {"renamed": dst.name}


@app.post("/api/loras/delete")
async def api_loras_delete(request: Request):
    b = await request.json()
    names = [str(n) for n in b.get("names") or []]
    if not names:
        raise HTTPException(400, "nothing to delete")
    done, kept, todo = [], [], []
    scan = _count_lora_results(setting_path("px_output"))  # (once, and outside the lock: it reads every result)
    for n in names:
        f = existing_lora(n)
        if f is None:
            continue
        lora_file(f.name)  # (refused when it is not the user's)
        refs = lora_references(f, scan)
        if refs["results"] or refs["gallery"]:
            kept.append(lora_in_use_message(f.name, refs, "deleted"))
        else:
            todo.append(f)
    with _lock:
        rec = _load("installed.json", {})
        for f in todo:
            with contextlib.suppress(FileNotFoundError):
                f.unlink()
            done.append(f.name)
            rec.pop(f.name, None)
        _save("installed.json", rec)
        for f in todo:
            ACCOUNTS.forget_id("lora", f.name)
    if kept and not done:
        raise HTTPException(409, " ".join(kept))
    return {"deleted": done, "kept": kept}


# ---- models (pxmodel.py): groups, kinds, sprites


def models_summary() -> list[dict]:
    out = []
    p = pm()
    for mid in p.list_models():
        try:
            m = p.load(mid)
        except SystemExit:
            continue
        if not can("model", mid):
            continue
        sd = Path(p.sprites_dir(mid))
        out.append(
            {
                k: m[k]
                for k in (
                    "id",
                    "label",
                    "trigger",
                    "size",
                    "repeats",
                    "colour_words",
                    "colours_from",
                    "tail",
                    "project",
                )
            }
            | {
                "groups": [
                    {k: g.get(k, "") for k in ("id", "label", "words", "enabled", "palette")} for g in m["groups"]
                ],
                "kinds": [
                    {k: kk.get(k, 0) for k in ("id", "label", "words", "size", "denoise", "fill", "tile", "edge")}
                    for kk in m["kinds"]
                ],
                "sprites": len(list(sd.glob("*.png"))) if sd.is_dir() else 0,
                "saved": (Path(p.model_dir(mid)) / "model.json").exists(),
                "lora": model_loras(m),
            }
        )
        tagged("model", mid, out[-1])
    return out


def triggers_in_use(but: str = "") -> dict:
    """trigger word -> model id, for every model but one: two models must not
    share a trigger, their LoRA files would overwrite each other."""
    p = pm()
    out = {}
    for mid in p.list_models():
        if mid == but:
            continue
        with contextlib.suppress(SystemExit):
            out[p.load(mid)["trigger"]] = mid
    return out


def free_trigger(want: str, mid: str) -> str:
    used = triggers_in_use(mid)
    base = re.sub(r"[^a-z0-9]", "", want.lower())[:24] or "px" + re.sub(r"[^a-z0-9]", "", mid)[:20]
    t, n = base, 2
    while t in used:
        t, n = f"{base}{n}", n + 1
    return t


def check_model(m: dict) -> None:
    for key in ("groups", "kinds"):
        ids = [x.get("id", "") for x in m.get(key, [])]
        for i in ids:
            if not re.fullmatch(r"[a-z0-9_]{1,40}", i):
                raise HTTPException(400, f"{key}: '{i}' is not a valid id (lowercase letters, digits, _)")
        if len(set(ids)) != len(ids):
            raise HTTPException(400, f"{key}: two have the same id")
        for x in m.get(key, []):
            for f in ("pattern", "name_pattern"):
                try:
                    re.compile(x.get(f) or "")
                except re.error as e:
                    raise HTTPException(400, f"{x.get('id')}: {f} is not a valid pattern ({e})") from None
    if not re.fullmatch(r"[a-z][a-z0-9]{2,30}", m.get("trigger", "")):
        raise HTTPException(400, "the trigger word is lowercase letters and digits, one word (pxunit)")
    other = triggers_in_use(m.get("id", "")).get(m["trigger"])
    if other:
        raise HTTPException(400, f"the trigger word {m['trigger']} is already the model {other}'s: choose another")
    try:
        re.compile(m.get("batch_skip") or "")
    except re.error as e:
        raise HTTPException(400, f"batch_skip is not a valid pattern ({e})") from None


@app.get("/api/models")
def api_models():
    return models_summary()


@app.get("/api/models/{mid}/prompt")
def api_model_prompt(mid: str, group: str, kind: str = ""):
    """The prompt the model builds for a group and a kind (what a preview line for them would start from)."""
    m = model_cfg(mid)
    if not any(g["id"] == group for g in m["groups"]):
        raise HTTPException(400, f"no group {group}")
    if kind and not any(k["id"] == kind for k in m["kinds"]):
        raise HTTPException(400, f"no kind {kind}")
    try:
        return {"prompt": pm().prompt(m, group, kind or None, "" if m["kinds"] else "new design")["prompt"]}
    except SystemExit as e:
        raise HTTPException(400, str(e)) from None


@app.get("/api/models/{mid}")
def api_model(mid: str):
    m = model_cfg(mid)
    p = pm()
    ss = p.sprites(m)
    for s in ss:
        g = next((g for g in m["groups"] if g["id"] == s["group"]), None)
        k = next((k for k in m["kinds"] if k["id"] == s["kind"]), None)
        s["caption"] = p.caption(m, g, k, s["words"], "<colours>") if g else ""
    try:
        previews = p.auto_previews(m)
    except SystemExit:
        previews = []
    template = next((k for k, t in p.template_sets().items() if mid in t["models"]), None)
    return {
        "model": m,
        "access": tagged("model", mid, {})["access"],
        **ACCOUNTS.tag("model", mid),
        "sprites": ss,
        "auto_previews": previews,
        "template": template,
        "saved": (Path(p.model_dir(mid)) / "model.json").exists(),
        "dir": p.model_dir(mid) if is_admin() else "",
    }


@app.put("/api/models/{mid}")
async def api_model_save(mid: str, request: Request):
    old = model_cfg(mid)
    b = await request.json()
    m = dict(old)
    for k in (
        "label",
        "trigger",
        "colour_words",
        "colours_from",
        "tail",
        "size",
        "repeats",
        "groups",
        "kinds",
        "batch_skip",
        "previews",
        "imports",
        "negative",
        "project",
        "relight",
        "fill",
    ):
        if k in b:
            m[k] = b[k]
    m = pm().normalise(m, mid)
    m["sprites"] = old["sprites"]
    check_model(m)
    pm().save(m)
    return api_model(mid)


@app.post("/api/models")
async def api_model_create(request: Request):
    return create_model(await request.json())


def create_model(b: dict) -> dict:
    mid = (b.get("id") or "").strip()
    if not re.fullmatch(r"[a-z0-9_]{1,40}", mid):
        raise HTTPException(400, "the id is lowercase letters, digits and _ (it names the model's folder)")
    p = pm()
    if (Path(p.model_dir(mid)) / "model.json").exists():
        raise HTTPException(400, f"a model {mid} already exists")
    if b.get("template"):
        sid, _, tid = b["template"].partition("/")
        if tid not in p.template_sets().get(sid, {}).get("models", {}):
            raise HTTPException(400, "no such template")
        m = p.from_template(sid, tid, mid, b.get("project") or None)
        m["label"] = b.get("label") or m["label"]
    elif b.get("copy"):
        base = model_cfg(b["copy"], write=False)
        m = p.normalise(dict(base, sprites={}), mid)
        m["label"] = b.get("label") or mid
        keep = [g for g in m["groups"] if g["id"] in (b.get("groups") or [])]
        if keep:  # a model for some groups only
            m["groups"] = keep
            m["repeats"] = 0  # about 300 steps per epoch for its fewer sprites
            m["previews"] = []
            m["imports"] = [dict(r, only_grouped=True) for r in m["imports"]]
    else:
        m = p.normalise(
            {"label": b.get("label") or mid, "groups": [{"id": "main", "label": "Main", "words": "", "pattern": "."}]},
            mid,
        )
    m["trigger"] = free_trigger(
        b.get("trigger") or ("px" + re.sub(r"[^a-z0-9]", "", mid) if b.get("template") else m.get("trigger", "")), mid
    )
    if not b.get("copy") and not b.get("template"):
        m["imports"] = []
    if b.get("project"):
        m["project"] = b["project"].strip()[:60]
    check_model(m)
    p.save(m)
    claim("model", mid)
    return api_model(mid)


@app.get("/api/templates")
def api_templates():
    p = pm()
    have = set(p.list_models())
    return [
        {
            "id": sid,
            "label": t.get("label", sid),
            "description": t.get("description", ""),
            "models": [
                {"id": mid, "label": mm.get("label", mid), "trigger": mm.get("trigger", ""), "exists": mid in have}
                for mid, mm in t["models"].items()
            ],
        }
        for sid, t in p.template_sets().items()
    ]


@app.post("/api/templates/{sid}/add")
async def api_template_add(sid: str, request: Request):
    """Adds a template set's models that do not exist yet."""
    p = pm()
    t = p.template_sets().get(sid)
    if not t:
        raise HTTPException(404, "no such template set")
    b = await request.json()
    made = []
    for mid in t["models"]:
        if (b.get("models") and mid not in b["models"]) or (Path(p.model_dir(mid)) / "model.json").exists():
            continue
        p.save(p.from_template(sid, mid, project=b.get("project") or None))
        claim("model", mid)
        made.append(mid)
    return {"created": made}


@app.get("/api/models/{mid}/export")
def api_model_export(mid: str):
    m = model_cfg(mid)
    data = {
        "label": m["label"],
        "description": f"Exported from Sprite Studio ({m['project'] or 'no project'})",
        "models": {mid: {k: v for k, v in m.items() if k not in ("id", "sprites")}},
    }
    return Response(
        json.dumps(data, indent=1),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{mid}.json"'},
    )


@app.post("/api/models/import")
async def api_model_import_file(request: Request):
    """Creates models from an exported file ({models: {id: model}}); with
    "save_template", also keeps the file as a template set."""
    b = await request.json()
    data = b.get("data") or {}
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, dict) or not models:
        raise HTTPException(400, 'not a Sprite Studio model file (it needs a "models" object)')
    p = pm()
    made = []
    for mid, mm in models.items():
        new_id = name_ok(b.get("id") or mid).lower()
        if (Path(p.model_dir(new_id)) / "model.json").exists():
            raise HTTPException(400, f"a model {new_id} already exists: give another id")
        m = p.normalise(dict(mm, sprites={}), new_id)
        m["trigger"] = free_trigger(m["trigger"], new_id)
        if b.get("project"):
            m["project"] = b["project"].strip()[:60]
        check_model(m)
        p.save(m)
        claim("model", new_id)
        made.append(new_id)
    if b.get("save_template"):
        need_admin()  # (template sets are shared by everyone)
        folder = Path(p.HOME) / ".templates"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name_ok(b.get('template_name') or made[0])}.json").write_text(json.dumps(data, indent=1))
    return {"created": made}


@app.post("/api/models/{mid}/upload-zip")
async def api_model_upload_zip(mid: str, request: Request):
    """The PNG files of a zip archive, into the model's sprites; sub-folder
    names become a prefix when asked (icons/map/x.png -> map_x.png)."""
    import io
    import zipfile

    m = model_cfg(mid)
    b = await request.json()
    raw = decode_data_url(b.get("data"), kinds=(), limit=MAX_BODY)
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "not a zip file") from None
    infos = z.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise HTTPException(400, f"too many files in the zip (at most {MAX_ZIP_ENTRIES})")
    if sum(i.file_size for i in infos) > MAX_ZIP_TOTAL:
        raise HTTPException(400, f"the zip is too big once unpacked (at most {MAX_ZIP_TOTAL // 1_000_000} MB)")
    folder = Path(pm().sprites_dir(mid))
    folder.mkdir(parents=True, exist_ok=True)
    saved, skipped = [], []
    for info in z.infolist():
        parts = [x for x in info.filename.split("/") if x]
        if (
            info.is_dir()
            or not parts
            or not parts[-1].lower().endswith(".png")
            or "__MACOSX" in parts
            or parts[-1].startswith(".")
        ):
            continue
        prefix = "_".join(parts[-2:-1]) + "_" if b.get("folders") and len(parts) > 1 else ""
        name = re.sub(r"[^\w.-]+", "_", prefix + parts[-1])
        dst = folder / name
        if dst.exists() and not b.get("overwrite"):
            skipped.append(name)
            continue
        if info.file_size > MAX_FILE:
            skipped.append(name + " (too big)")
            continue
        data = z.read(info)
        try:
            check_image(data, "PNG")
        except HTTPException:
            skipped.append(name + " (not a valid PNG)")
            continue
        dst.write_bytes(data)
        saved.append(name)
        if b.get("group"):
            m["sprites"].setdefault(name, {})["group"] = b["group"]
    pm().save(m)
    return {"saved": saved, "skipped": skipped}


@app.post("/api/models/{mid}/reset")
async def api_model_reset(mid: str, request: Request):
    """Back to the built-in settings of this model (groups, kinds, words...),
    keeping its sprites and what was set by hand on them; with "keep" the
    listed fields stay as they are."""
    old = model_cfg(mid)  # first: someone else's model does not exist for you
    p = pm()
    sid = next((k for k, t in p.template_sets().items() if mid in t["models"]), None)
    if not sid:
        raise HTTPException(400, "this model does not come from a template")
    b = await request.json()
    m = p.from_template(sid, mid, project=old["project"])
    m["sprites"] = old["sprites"]
    for k in b.get("keep") or []:
        if k in old:
            m[k] = old[k]
    p.save(m)
    return api_model(mid)


@app.delete("/api/models/{mid}")
def api_model_delete(mid: str):
    model_cfg(mid)
    src = Path(pm().model_dir(mid))
    if not src.exists():
        raise HTTPException(400, "this built-in model has nothing saved to delete")
    dst = Path(pm().HOME) / ".deleted" / f"{mid}-{dt.datetime.now():%Y%m%d-%H%M%S}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    ACCOUNTS.forget("model", mid)
    return {"moved_to": str(dst)}


@app.post("/api/models/{mid}/upload")
async def api_model_upload(mid: str, request: Request):
    m = model_cfg(mid)
    b = await request.json()
    p = pm()
    folder = Path(p.sprites_dir(mid))
    folder.mkdir(parents=True, exist_ok=True)
    saved, skipped = [], []
    for f in b.get("files") or []:
        name = re.sub(r"[^\w.-]+", "_", Path(f.get("name", "")).name)
        try:
            raw = decode_data_url(f.get("data"))
            check_image(raw, "PNG")
        except HTTPException as e:
            skipped.append(f"{f.get('name', '?')} ({e.detail})")
            continue
        if not name.lower().endswith(".png"):
            skipped.append(f.get("name", "?") + " (not a PNG)")
            continue
        dst = folder / name
        if dst.exists() and not b.get("overwrite"):
            skipped.append(name + " (already there)")
            continue
        dst.write_bytes(raw)
        saved.append(name)
        if b.get("group"):
            m["sprites"].setdefault(name, {})["group"] = b["group"]
        if b.get("kind"):
            m["sprites"].setdefault(name, {})["kind"] = b["kind"]
    p.save(m)
    return {"saved": saved, "skipped": skipped}


@app.post("/api/models/{mid}/import")
async def api_model_import(mid: str, request: Request):
    m = model_cfg(mid)
    b = await request.json()
    rules = None
    if b.get("glob"):
        if ".." in b["glob"] or b.get("root") not in sources():
            raise HTTPException(400, "choose a source folder (Settings) and a pattern inside it")
        rules = [
            {
                "root": b["root"],
                "glob": b["glob"],
                "prefix": name_ok(b["prefix"]) if b.get("prefix") else "",
                "group": b.get("group") or None,
            }
        ]
    elif not m["imports"]:
        raise HTTPException(400, "this model has no import rules: give a folder and a pattern")
    done = pm().import_sprites(m, rules, overwrite=bool(b.get("overwrite")))
    pm().save(m)
    return {"imported": done}


@app.get("/api/models/{mid}/lighting")
def api_model_lighting(mid: str):
    """Light direction of each sprite, read from its shading."""
    m = model_cfg(mid)
    out = []
    for s in pm().sprites(m):
        top, left = pm().light_direction(s["path"])
        out.append({"name": s["name"], "top": round(top, 2), "left": round(left, 2)})
    return out


@app.post("/api/models/{mid}/sprites")
async def api_model_sprites(mid: str, request: Request):
    """Sets group / kind / words / exclude of some sprites by hand (null:
    back to the automatic choice)."""
    m = model_cfg(mid)
    b = await request.json()
    names = b.get("names") or []
    for n in names:
        ov = m["sprites"].setdefault(n, {})
        for k in ("group", "kind", "words", "exclude"):
            if k in b:
                if b[k] is None:
                    ov.pop(k, None)
                else:
                    ov[k] = b[k]
        if not ov:
            m["sprites"].pop(n, None)
    pm().save(m)
    return {"ok": True}


@app.delete("/api/models/{mid}/sprites")
async def api_model_sprites_delete(mid: str, request: Request):
    m = model_cfg(mid)
    b = await request.json()
    folder = Path(pm().sprites_dir(mid))
    trash = folder / ".trash"
    trash.mkdir(exist_ok=True)
    for n in b.get("names") or []:
        f = folder / Path(n).name
        if f.is_file():
            shutil.move(str(f), str(trash / f.name))
        m["sprites"].pop(Path(n).name, None)
    pm().save(m)
    return {"ok": True}


# ---- results


@app.get("/api/results")
def api_results():
    return scan_results()


@app.post("/api/results/delete")
async def api_results_delete(request: Request):
    """Body: {"paths": [...]} or {"empty": true} (every result with nothing in it)."""
    b = await request.json()
    paths = list(b.get("paths") or [])
    if b.get("empty"):
        paths += [i["path"] for i in scan_results() if i["empty"] and i.get("access") == "write"]
    if not paths:
        raise HTTPException(400, "nothing to delete")
    done = [delete_result(x) for x in dict.fromkeys(paths)]
    return {"deleted": done, "bin": bin_info()}


def bin_owner_key(d: Path) -> str:
    """The result a binned folder came from (its top folder), "" when unknown."""
    try:
        return (d / ".origin").read_text().strip().split("/")[0]
    except OSError:
        return ""


def bin_visible(d: Path, write: bool = False) -> bool:
    w = who_now()
    if w is None or w.admin:
        return True
    key = bin_owner_key(d)
    return bool(key) and can("result", key, write)


def bin_info(listing: bool = False) -> dict:
    b = results_bin()
    items = [d for d in b.iterdir() if d.is_dir() and bin_visible(d)] if b.is_dir() else []
    info: dict[str, Any] = {"count": len(items), "bytes": sum(_du(d) for d in items), "path": str(b)}
    if listing:  # what is in it, newest deletion first
        rows = []
        for d in sorted(items, key=lambda x: x.name, reverse=True)[:200]:  # the newest 200: sizes cost a disk walk each
            origin = d / ".origin"
            rel = origin.read_text().strip() if origin.exists() else d.name.split("_", 1)[-1]
            cover = next((p for p in sorted(d.rglob("*.png")) if "raw" not in p.parts and "_x4" not in p.name), None)
            stamp = d.name.split("_", 1)[0]
            rows.append(
                {
                    "id": d.name,
                    "name": rel,
                    "deleted": int(stamp) / 1e9 if stamp.isdigit() else d.stat().st_mtime,
                    "bytes": _du(d),
                    "cover": str(cover) if cover else None,
                    "restorable": origin.exists() and not (setting_path("px_output") / rel).exists(),
                }
            )
        info["items"] = rows
    return info


@app.get("/api/results/bin")
def api_results_bin(items: int = 0):
    return bin_info(bool(items))


@app.post("/api/results/bin/restore")
async def api_results_restore(request: Request):
    """Puts back the last deleted results (body: {"count": n}, default 1), or the ones named in {"ids": [...]}."""
    b = await request.json()
    items = (
        sorted((d for d in results_bin().iterdir() if d.is_dir() and bin_visible(d, True)), key=lambda d: d.name)
        if results_bin().is_dir()
        else []
    )
    if isinstance(b.get("ids"), list):
        ids = {str(x) for x in b["ids"]}
        chosen = [d for d in items if d.name in ids]
    else:
        chosen = items[-max(1, int(b.get("count", 1))) :]
    restored = []
    for d in chosen:
        origin = d / ".origin"
        if not origin.exists():
            continue
        rel = Path(origin.read_text().strip())
        dst = setting_path("px_output") / rel
        if dst.exists():
            continue
        origin.unlink()
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(d), str(dst))
        restored.append(str(rel))
        if len(rel.parts) == 3 and rel.parts[0].startswith("batch-"):
            refresh_batch_sheet(setting_path("px_output") / rel.parts[0], rel.parts[1])
    return {"restored": restored, "bin": bin_info()}


@app.post("/api/results/bin/empty")
async def api_results_bin_empty(request: Request):
    """Erases the bin for good, or only the folders named in the body's {"ids": [...]}."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    b = results_bin()
    if b.is_dir():
        if isinstance(body, dict) and isinstance(body.get("ids"), list):
            for name in {str(x) for x in body["ids"]}:
                d = b / Path(name).name
                if d.is_dir() and d.parent == b and bin_visible(d, True):
                    shutil.rmtree(d)
        else:
            for d in list(b.iterdir()):  # (a user empties only what is theirs)
                if d.is_dir() and bin_visible(d, True):
                    shutil.rmtree(d)
    return bin_info()


# ---- palettes: colours (and a tuning) put on what is generated; files in
# <training folder>/.palettes/<id>.json, read by pxmake.sh too.

PAL_ID = re.compile(r"[a-z0-9_]{1,60}")


def pal_dir() -> Path:
    return Path(pm().palettes_dir())


def palette_ref(ref: str) -> str:
    """A palette chosen in a form: "none", or the id of a saved one."""
    if ref == "none":
        return ref
    if not PAL_ID.fullmatch(ref or "") or not (pal_dir() / f"{ref}.json").is_file():
        raise HTTPException(400, f"no palette {ref}")
    need("palette", ref, False)
    return ref


def clean_palette(b: dict, pid: str) -> dict:
    cols = [str(c).lower() for c in (b.get("colours") or [])]
    for c in cols:
        if not re.fullmatch(r"#[0-9a-f]{6}", c):
            raise HTTPException(400, f"not a colour: {c} (#rrggbb)")
    if len(cols) > 256:
        raise HTTPException(400, "256 colours at most")
    w = [max(0, int(x)) for x in (b.get("weights") or [])]
    tune = {
        k: max(
            -180 if k == "hue" else -100, min(180 if k == "hue" else 100, float((b.get("tune") or {}).get(k, 0) or 0))
        )
        for k in pm().pxtool.TUNE_KEYS + ("light",)
    }
    mode = b.get("mode") or ("snap" if cols else "tune")
    if mode not in ("tune", "snap", "match"):
        raise HTTPException(400, "mode is tune, snap or match")
    if mode != "tune" and not cols:
        raise HTTPException(400, "Snap and Match need colours: add some (Take colours from…) or choose Tuning only")
    return {
        "id": pid,
        "label": (b.get("label") or pid).strip()[:80],
        "colours": cols,
        "weights": w if len(w) == len(cols) else [],
        "tune": tune,
        "mode": mode,
        "words": bool(b.get("words")),
        "source": str(b.get("source") or "")[:300],
    }


def save_palette(pal: dict) -> dict:
    pal_dir().mkdir(parents=True, exist_ok=True)
    f = pal_dir() / f"{pal['id']}.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(pal, indent=1))
    tmp.replace(f)
    return pal | {"mtime": f.stat().st_mtime}


def read_palette(pid: str) -> dict:
    if not PAL_ID.fullmatch(pid):
        raise HTTPException(400, "a palette id is lowercase letters, digits and _")
    f = pal_dir() / f"{pid}.json"
    if not f.is_file():
        raise HTTPException(404, f"no palette {pid}")
    need("palette", pid)
    return pm().pxtool.load_palette(str(f)) | {"id": pid, "mtime": f.stat().st_mtime}


def palette_users(pid: str) -> list[str]:
    out = []
    for mid in pm().list_models():
        try:
            m = pm().load(mid)
        except SystemExit:
            continue
        if can("model", mid):
            out += [f"{m['label']}: {g['label']}" for g in m["groups"] if g.get("palette") == pid]
    return out


def free_palette_id(base: str) -> str:
    base = re.sub(r"[^a-z0-9_]+", "_", base.lower()).strip("_")[:50] or "palette"
    pid, n = base, 2
    while (pal_dir() / f"{pid}.json").exists():
        pid, n = f"{base}_{n}", n + 1
    return pid


@app.get("/api/palettes")
def api_palettes():
    out = []
    for f in sorted(pal_dir().glob("*.json")) if pal_dir().is_dir() else []:
        try:
            out.append(tagged("palette", f.stem, read_palette(f.stem) | {"used_by": palette_users(f.stem)}))
        except HTTPException:
            continue
    return out


@app.get("/api/palettes/{pid}")
def api_palette(pid: str):
    return tagged("palette", pid, read_palette(pid) | {"used_by": palette_users(pid)})


@app.put("/api/palettes/{pid}")
async def api_palette_save(pid: str, request: Request):
    if not PAL_ID.fullmatch(pid):
        raise HTTPException(400, "a palette id is lowercase letters, digits and _")
    if (pal_dir() / f"{pid}.json").exists():
        if not can("palette", pid):
            raise HTTPException(400, f"a palette {pid} exists already: choose another name")
        need("palette", pid)  # (changing one needs it to be yours)
    out = save_palette(clean_palette(await request.json(), pid))
    claim("palette", pid)
    return out


@app.delete("/api/palettes/{pid}")
def api_palette_delete(pid: str):
    read_palette(pid)
    need("palette", pid, True)
    for mid in pm().list_models():  # groups using it go back to no palette
        try:
            m = pm().load(mid)
        except SystemExit:
            continue
        if any(g.get("palette") == pid for g in m["groups"]):
            for g in m["groups"]:
                if g.get("palette") == pid:
                    g["palette"] = ""
            pm().save(m)
    (pal_dir() / f"{pid}.json").unlink()
    ACCOUNTS.forget("palette", pid)
    return {"ok": True}


@app.post("/api/palettes/extract")
async def api_palette_extract(request: Request):
    """A palette from sprites: a model's group (and kind), or given files."""
    b = await request.json()
    paths = [str(safe_path(x, write=False)) for x in b.get("paths") or []]
    label = b.get("label") or ""
    if b.get("batch"):
        files, what = sprite_set({"batch": b["batch"], "group": b.get("group") or ""})
        paths += files
        label = label or what.replace("the results of batch ", "Batch ")
    elif b.get("model"):
        m = model_cfg(b["model"], write=False)
        ss = [
            x
            for x in pm().sprites(m)
            if (not b.get("group") or x["group"] == b["group"]) and (not b.get("kind") or x["kind"] == b["kind"])
        ]
        paths += [x["path"] for x in ss]
        g = next((g for g in m["groups"] if g["id"] == b.get("group")), None)
        label = label or " ".join(x for x in [m["label"], g["label"] if g else ""] if x)
    if not paths:
        raise HTTPException(400, "no sprite to take colours from")
    n = max(2, min(MAX_COLOURS, body_get(b, "n", 32, int)))
    pal = pm().pxtool.extract_palette(paths, n)
    label = label or Path(paths[0]).stem
    pid = free_palette_id(b.get("id") or label)
    src = f"{len(paths)} sprite(s)" + (
        f" of {label}" if b.get("model") or b.get("batch") else f": {', '.join(Path(x).name for x in paths[:3])}"
    )
    out = save_palette(clean_palette(pal | {"label": label, "source": src}, pid))
    claim("palette", pid)
    return out


def sprite_set(spec: dict) -> tuple[list[str], str]:
    """Sprites named by a form: {"model", "group", "kind", "others": true (every group but that one)},
    {"batch": <a batch folder>} (its results), or {"paths": [...]}. Returns them and a label."""
    if spec.get("batch"):
        d = safe_path(spec["batch"], write=False)
        files = [str(f) for f in sorted(d.glob("*/*/*.png")) if is_variant(f)]
        if spec.get("group"):  # only the sprites made for that group (from each job.txt)
            keep = []
            for f in files:
                jt = Path(f).parent / "job.txt"
                head = jt.read_text(errors="replace").splitlines()[0] if jt.exists() else ""
                if re.search(r"\bgroup=" + re.escape(spec["group"]) + r"\b", head):
                    keep.append(f)
            files = keep
        return files, f"the results of batch {d.name[6:]}" + (f" ({spec['group']})" if spec.get("group") else "")
    if spec.get("model"):
        m = model_cfg(spec["model"], write=False)
        g = spec.get("group") or ""
        ss = [
            x
            for x in pm().sprites(m)
            if not x["exclude"]
            and (not g or (x["group"] != g if spec.get("others") else x["group"] == g))
            and (not spec.get("kind") or x["kind"] == spec["kind"])
        ]
        gl = next((x["label"] for x in m["groups"] if x["id"] == g), g)
        what = (
            f"{m['label']}, every group but {gl}"
            if g and spec.get("others")
            else f"{m['label']}: {gl}"
            if g
            else f"every sprite of {m['label']}"
        )
        return [x["path"] for x in ss], what
    return [str(safe_path(x, write=False)) for x in spec.get("paths") or []], "the chosen sprites"


@app.post("/api/palettes/autotune")
async def api_palette_autotune(request: Request):
    """The tuning that gives some sprites ("sprites") the mean light, contrast and saturation of others
    ("reference"); nothing is saved. Body: {"sprites": spec, "reference": spec, "hue": n} (sprite_set)."""
    b = await request.json()
    src, src_label = sprite_set(b.get("sprites") or {})
    ref, ref_label = sprite_set(b.get("reference") or {})
    if not src:
        raise HTTPException(400, f"no sprite in {src_label}")
    if not ref:
        raise HTTPException(400, f"no sprite in {ref_label}")
    tx = pm().pxtool
    px, rpx = tx._opaque_pixels(src), tx._opaque_pixels(ref)
    target = tx.colour_stats(rpx)
    tune = tx.auto_tune(px, target, float(b.get("hue") or 0))
    return {
        "tune": tune,
        "before": tx.colour_stats(px),
        "after": tx.colour_stats(tx.tune_rgb(px, tune)),
        "target": target,
        "sprites": len(src),
        "reference": len(ref),
        "what": f"{src_label} made to look like {ref_label}",
    }


@app.post("/api/palettes/colours")
async def api_palette_colours(request: Request):
    """The colours of some sprites (sprite_set), merged down to n, for a palette being edited; nothing saved."""
    b = await request.json()
    paths, what = sprite_set(b.get("sprites") or {})
    if not paths:
        raise HTTPException(400, f"no sprite in {what}")
    pal = pm().pxtool.extract_palette(paths, max(2, min(MAX_COLOURS, body_get(b, "n", 32, int))))
    return {"colours": pal["colours"], "weights": pal["weights"], "what": f"{len(paths)} sprite(s): {what}"}


@app.post("/api/palettes/import")
async def api_palette_import(request: Request):
    """A palette file sent as a data URL: .json, .hex (Lospec), .gpl (GIMP), or an image (its colours)."""
    import tempfile

    b = await request.json()
    name = Path(b.get("name") or "palette.hex").name
    ext = Path(name).suffix.lower()
    if ext not in (".json", ".hex", ".txt", ".gpl", ".png", ".gif", ".bmp"):
        raise HTTPException(400, "send a .hex, .gpl, .json or image file")
    raw = decode_data_url(b.get("data"), kinds=())
    if ext in (".png", ".gif", ".bmp"):
        check_image(raw)
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / ("palette" + ext)
        f.write_bytes(raw)
        try:
            pal = pm().pxtool.load_palette(str(f))
        except Exception as e:
            raise HTTPException(400, f"could not read {name}: {e}") from None
    if not pal.get("colours"):
        raise HTTPException(400, f"no colour found in {name}")
    pid = free_palette_id(b.get("id") or Path(name).stem)
    out = save_palette(
        clean_palette(
            pal | {"label": pal.get("label") if ext == ".json" else Path(name).stem, "source": f"imported from {name}"},
            pid,
        )
    )
    claim("palette", pid)
    return out


@app.get("/api/palettes/{pid}/export")
def api_palette_export(pid: str, format: str = "hex"):
    pal = read_palette(pid)
    if format == "json":
        data, mt = (
            json.dumps({k: pal[k] for k in ("id", "label", "colours", "weights", "tune", "mode", "words")}, indent=1),
            "application/json",
        )
    elif format in ("hex", "gpl", "png"):
        data = pm().pxtool.export_palette(pal, format)
        mt = "image/png" if format == "png" else "text/plain"
    else:
        raise HTTPException(400, "format is hex, gpl, png or json")
    return Response(data, media_type=mt, headers={"Content-Disposition": f'attachment; filename="{pid}.{format}"'})


@app.post("/api/palettes/apply")
async def api_palette_apply(request: Request):
    """Recolours sprites with a palette (saved, or as edited: body "palette"), next to the originals:
    <name>_pal-<id>.png. Body: {"paths": [...], "id": ..., "palette": {...}}."""
    b = await request.json()
    pal = clean_palette(b["palette"], b.get("id") or "edit") if b.get("palette") else read_palette(b.get("id", ""))
    out_root = setting_path("px_output").resolve()
    made = []
    for x in b.get("paths") or []:
        src = safe_path(x, write=False)
        if src.suffix != ".png" or not src.is_file():
            raise HTTPException(400, f"not a sprite: {x}")
        # Results are recoloured next to themselves; any other sprite (a game sprite, a model's) into
        # the output folder's recoloured/, never over the original.
        mine = True
        if out_root in src.parents:
            try:
                safe_path(x, write=True)
            except HTTPException:
                mine = False  # shared with you to use: the copy goes to your own folder
        w = who_now()
        own = out_root / "recoloured" / ((w.id or w.name) if w else "_app")
        folder = src.parent if out_root in src.parents and mine and not is_recoloured(src, out_root) else own
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"_pal-[a-z0-9_]+$", "", src.stem)
        dst = folder / f"{stem}_pal-{pal['id']}.png"
        pm().pxtool.recolour(str(src), str(dst), pal)
        made.append(str(dst))
    return {"files": made}


# ---- clean up again: the clean-up (pxtool.post) re-run on a variant's saved raw render, with its settings
# changed. No GPU, no ComfyUI: the render is already on disk.

_clean_lock = threading.Lock()
CLEAN_SEED = re.compile(r"_s(\d+)(?:_c\d+)?$")


def _has_green(img_path: Path) -> bool:
    """The sprite has greens of its own (as pxmake.sh checks): more than 2% of it is clearly green."""
    import numpy as np
    from PIL import Image

    a = Image.open(img_path).convert("RGBA")
    hsv = np.array(a.convert("RGB").convert("HSV")).astype(float)
    solid = np.array(a)[..., 3] > 127
    if not solid.any():
        return False
    h, sat, val = hsv[..., 0] * 360 / 255, hsv[..., 1], hsv[..., 2]
    green = solid & (sat > 64) & (val > 64) & (abs(h - 120) < 40)
    return green.sum() / solid.sum() > 0.02


def cleanup_inputs(b: dict) -> dict:
    """What a clean-up of one variant needs, from its folder and the settings asked for."""
    src = safe_path(b.get("path", ""), write=False)
    m = CLEAN_SEED.search(src.stem)
    if src.suffix != ".png" or not src.is_file() or not m:
        raise HTTPException(400, "this sprite has no saved render to clean up again")
    folder = src.parent
    raw = next(iter(sorted((folder / "raw").glob(f"*_s{m.group(1)}_*.png"))), None)
    if not raw:
        raise HTTPException(404, "the raw render of this sprite is no longer there")
    result = result_set(folder)
    head = result["params"]
    if head.get("mask", "none") != "none":
        raise HTTPException(400, "a touch-up cannot be cleaned up again: redo it from the Generate page")
    if head.get("tile") == "1":
        raise HTTPException(
            400, "a tile keeps its full colours: there is nothing to tune, render it again to change it"
        )
    size = png_size(src)
    if not size or not size[0]:
        raise HTTPException(400, "unreadable sprite")
    orig = folder / "orig.png"
    grid = folder / "init.png.json"

    def num(key: str, default: float, lo: float, hi: float, kind: type = float) -> Any:
        v = body_get(b, key, default, kind)
        return kind(max(lo, min(hi, v)))

    pal = b.get("palette", "job")
    if pal == "job":
        pal = head.get("palette", "none")
        if pal in ("none", ""):
            pal = None
        elif not Path(pal).is_file():  # a job file may name the palette by its id
            pal = str(pal_dir() / f"{palette_ref(Path(pal).stem)}.json")
    elif pal == "none":
        pal = None
    else:
        pal = str(pal_dir() / f"{palette_ref(pal)}.json")
    green = b.get("keep_green", "auto")
    if green == "auto":
        green = "glowing green" in result["prompt"] or (orig.is_file() and _has_green(orig))
    fill = body_get(b, "fill", None, int)
    if fill is None and head.get("fill", "0") not in ("0", ""):
        fill = int(head["fill"]) if head["fill"].isdigit() else 0
    mirror = b.get("mirror", "auto")
    if mirror not in ("auto", "none", "left", "right"):
        raise HTTPException(400, "symmetry is auto, none, left or right")
    return {
        "src": str(raw),
        "folder": folder,
        "stem": src.stem,
        "kw": {
            "size": size[0],
            "margin": 0,
            "mirror": mirror,
            "orig": str(orig) if orig.is_file() else None,
            "grid": str(grid) if grid.is_file() else None,
            "keep_green": bool(green),
            "palette": pal,
            "fill": max(0, min(100, fill or 0)),
            "colours": num("colours", 64, 2, MAX_COLOURS, int),
            "edge": num("edge", 0.5, 0.2, 0.8),
            "min_part": num("min_part", 0.004, 0, 0.05),
            "key_tol": num("key_tol", 70, 20, 140, int),
            "outline": b.get("outline", True) is not False,
            "outline_style": "selective" if b.get("outline_style") == "selective" else "dark",
            "tidy": b.get("tidy", True) is not False,
        },
    }


def run_cleanup(inp: dict, dst: Path) -> str:
    """Runs the clean-up into dst; returns its warnings."""
    buf = io.StringIO()
    with _clean_lock, contextlib.redirect_stdout(buf):
        pm().pxtool.post(inp["src"], str(dst), **inp["kw"])
    return "\n".join(x for x in buf.getvalue().splitlines() if x.startswith("warning"))


@app.post("/api/results/cleanup")
def api_results_cleanup(b: dict):
    """Body: the variant's "path", the settings (colours, edge, min_part, key_tol, outline, tidy, mirror, palette,
    keep_green, fill) and "save". Without "save": a preview (PNG as a data URL, nothing written); with it, the new
    variant is saved next to the old one as <name>_c<n>.png."""
    inp = cleanup_inputs(b)
    if not b.get("save"):
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "preview.png"
            notes = run_cleanup(inp, dst)
            if not dst.is_file():
                raise HTTPException(422, notes.strip() or "nothing came out of this clean-up")
            data = base64.b64encode(dst.read_bytes()).decode()
        return {"image": "data:image/png;base64," + data, "notes": notes.strip()}
    safe_path(b.get("path", ""), write=True)  # (saving adds to the result: it must be yours)
    base_stem = re.sub(r"_c\d+$", "", inp["stem"])
    n = 1
    while (inp["folder"] / f"{base_stem}_c{n}.png").exists():
        n += 1
    dst = inp["folder"] / f"{base_stem}_c{n}.png"
    notes = run_cleanup(inp, dst)
    if not dst.is_file():
        raise HTTPException(422, notes.strip() or "nothing came out of this clean-up")
    return {"file": str(dst), "notes": notes.strip()}


@app.get("/api/results/detail")
def api_result_detail(path: str):
    p = safe_path(path)
    if not p.is_dir():
        raise HTTPException(404, "no such folder")
    if p.name.startswith("batch-"):
        sets = []
        for cls in sorted(d for d in p.iterdir() if d.is_dir()):
            for s in sorted(d for d in cls.iterdir() if (d / "job.txt").exists()):
                r = result_set(s)
                r["class"] = cls.name
                sets.append(r)
        sheets = sorted(str(s) for s in p.glob("sheet_*.png"))
        return {"type": "batch", "path": str(p), "title": p.name[6:], "sets": sets, "sheets": sheets}
    if p.name.startswith("matrix-"):
        subjects = []
        for subj in sorted(d for d in p.iterdir() if d.is_dir()):
            cells = [result_set(c) for c in sorted(subj.iterdir()) if (c / "job.txt").exists()]
            sheet = p / f"sheet_{subj.name}.png"
            subjects.append({"name": subj.name, "cells": cells, "sheet": str(sheet) if sheet.exists() else ""})
        return {"type": "matrix", "path": str(p), "title": p.name[7:], "subjects": subjects}
    r = result_set(p)
    r["type"] = "single"
    return r


def picks_load() -> dict:
    """picks.json: {"users": {name: {path: {"as", "time"}}}} (before accounts it was one flat {path: {...}})."""
    d = _load("picks.json", {})
    if "users" not in d:
        d = {"users": {"": d} if d else {}}
    return d


def picks_name() -> str:
    w = who_now()
    return (w.id or w.name) if w else ""


def picks_mine(d: dict) -> dict:
    """The signed-in user's picks inside `d` (an administrator also gets the ones from before accounts)."""
    me = picks_name()
    mine = d["users"].setdefault(me, {})
    if is_admin() and me and "" in d["users"]:
        mine.update(d["users"].pop(""))
    return mine


@app.get("/api/picks")
def api_picks():
    with _lock:
        d = picks_load()
        return {k: v for k, v in picks_mine(d).items() if can_path(k)}


@app.post("/api/picks")
async def api_pick(request: Request):
    b = await request.json()
    p = str(safe_path(b.get("path", ""), write=False))
    with _lock:
        d = picks_load()
        picks = picks_mine(d)
        if b.get("picked"):
            picks[p] = {"as": name_ok(b.get("as") or Path(p).stem), "time": time.time()}
        else:
            picks.pop(p, None)
        _save("picks.json", d)
        return dict(picks)


@app.post("/api/picks/to-model")
async def api_picks_to_model(request: Request):
    """Copies the picked sprites into a model's sprites: an existing one ({"model": id}) or a new one ({"new":
    {"id", "label", "trigger", "project"}}), all in one group / kind if given. {"paths": [...]} limits it to some picks.
    Everything is checked before anything is created or copied, so a refused request leaves no trace."""
    b = await request.json()
    if not isinstance(b, dict):
        raise HTTPException(400, "send a JSON object")
    with _lock:
        picks = dict(picks_mine(picks_load()))
    if isinstance(b.get("paths"), list):
        wanted = {str(safe_path(x, write=False)) for x in b["paths"]}
        picks = {k: v for k, v in picks.items() if k in wanted}
    ready, skipped = [], []  # (source, base name) of the sprites that are real PNG images
    for k, v in picks.items():
        src = Path(k)
        if not src.is_file():
            continue
        base = re.sub(r"\.png$", "", name_ok(v.get("as") or src.stem), flags=re.I)
        try:
            if src.stat().st_size > MAX_FILE:
                raise HTTPException(413, "file too big")
            check_image(src.read_bytes(), "PNG")
        except HTTPException as e:
            skipped.append(f"{base} ({e.detail})")
            continue
        ready.append((src, base))
    if not ready:
        raise HTTPException(
            400,
            "Nothing picked yet: star the variants you want first."
            if not skipped
            else "None of the picks is a usable PNG: " + "; ".join(skipped),
        )
    group, kind = b.get("group") or None, b.get("kind") or None
    new = b.get("new")
    if new is not None and not isinstance(new, dict):
        raise HTTPException(400, "new: send the model's id, label and trigger")
    if new:
        mid = (new.get("id") or "").strip()
        if not re.fullmatch(r"[a-z0-9_]{1,40}", mid):
            raise HTTPException(400, "the id is lowercase letters, digits and _ (it names the model's folder)")
        if (Path(pm().model_dir(mid)) / "model.json").exists():
            raise HTTPException(400, f"a model {mid} already exists")
        groups, kinds = ["main"], []  # what a new model starts with
    else:
        mid = b.get("model") or ""
        m = model_cfg(mid)
        groups, kinds = [g["id"] for g in m["groups"]], [k["id"] for k in m["kinds"]]
    if group and group not in groups:
        raise HTTPException(400, f"the model has no group {group}")
    if kind and kind not in kinds:
        raise HTTPException(400, f"the model has no kind {kind}")
    if new:
        create_model(dict(new, id=mid))
    m = model_cfg(mid)
    folder = Path(pm().sprites_dir(mid))
    folder.mkdir(parents=True, exist_ok=True)
    saved, copied = [], []
    for src, base in ready:
        name, n = base + ".png", 2
        while (folder / name).exists():  # never overwrite a sprite the model already has
            name, n = f"{base}_{n}.png", n + 1
        shutil.copy2(src, folder / name)
        saved.append(name)
        copied.append(str(src))
        ov = m["sprites"].setdefault(name, {})
        if group:
            ov["group"] = group
        if kind:
            ov["kind"] = kind
        if not ov:
            m["sprites"].pop(name, None)
    pm().save(m)
    if b.get("unpick"):  # only the picks that really became sprites lose their star
        with _lock:
            d = picks_load()
            mine = picks_mine(d)
            for k in copied:
                mine.pop(k, None)
            _save("picks.json", d)
    return {"model": mid, "saved": saved, "skipped": skipped}


@app.post("/api/picks/export")
def api_picks_export():
    with _lock:
        picks = dict(picks_mine(picks_load()))
    dest = setting_path("px_output") / "picked" / (picks_name() or "_app")
    dest.mkdir(parents=True, exist_ok=True)
    done = []
    for path, info in picks.items():
        src = Path(path)
        if src.exists():
            shutil.copy2(src, dest / f"{info['as']}.png")
            done.append(info["as"])
    return {"folder": str(dest), "files": sorted(done)}


# ---- gallery: sprites a user keeps, with the recipe that made them

GALLERY_ID = re.compile(r"[0-9a-f]{16}")
MAX_GALLERY_EXPORT = 2000
_gallery_cache: dict[str, Any] = {"stamp": None, "root": "", "data": []}


def gallery_dir() -> Path:
    """Hidden under the results folder (no result list, bin or share ever sees it); every image is served by
    /api/gallery, so nobody reaches it by path."""
    return setting_path("px_output") / ".gallery"


def gallery_metas() -> list[dict]:
    """Every item's meta.json (cached until an item is added or removed; edits of name, folder or tags are read
    from disk by the gallery pages themselves)."""
    root = gallery_dir()
    try:
        stamp = root.stat().st_mtime_ns
    except OSError:
        return []
    c = _gallery_cache
    if c["stamp"] == stamp and c["root"] == str(root):
        return c["data"]
    out = []
    for d in root.iterdir():
        if GALLERY_ID.fullmatch(d.name) and (d / "meta.json").is_file():
            with contextlib.suppress(OSError, ValueError):
                out.append(json.loads((d / "meta.json").read_text()))
    c.update(stamp=stamp, root=str(root), data=out)
    return out


def gallery_meta(gid: str, write: bool = False) -> dict:
    if not GALLERY_ID.fullmatch(gid or ""):
        raise HTTPException(404, "not found")
    need("gallery", gid, write)
    f = gallery_dir() / gid / "meta.json"
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "not found") from None


def _gallery_write(gid: str, meta: dict) -> None:
    f = gallery_dir() / gid / "meta.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, indent=1))
    tmp.replace(f)


def gallery_label(s: Any, limit: int = 40) -> str:
    """A folder or a tag: short, plain text (letters, digits, space, - _ .)."""
    return re.sub(r"[^\w .-]+", "_", str(s or "")).strip(" .")[:limit]


def gallery_tags(v: Any) -> list[str]:
    if not isinstance(v, list):
        return []
    out: list[str] = []
    for t in v:
        t = gallery_label(t, 24).lower()
        if t and t not in out:
            out.append(t)
    return out[:12]


def gallery_row(m: dict) -> dict:
    row = {
        k: m.get(k)
        for k in ("id", "name", "created", "folder", "tags", "note", "seed", "model", "group", "kind", "lora", "w", "h")
    }
    row["epoch"] = (m.get("lora_info") or {}).get("epoch")
    if m.get("model_id"):  # (the model's id is what is kept; its name can change)
        row["model"] = ACCOUNTS.name_of_uid("model", m["model_id"]) or m.get("model")
    if m.get("lora_id"):  # (the file may have been renamed since it was saved)
        row["lora"] = lora_name_of(m["lora_id"]) or m.get("lora")
    return tagged("gallery", m["id"], row)


@app.get("/api/gallery")
def api_gallery():
    """The gallery items this user may see: their own and the ones shared with them."""
    rows = []
    for m in gallery_metas():
        if can("gallery", m["id"]):
            try:  # (the item itself: a rename, a folder or a tag may be newer than the cached list)
                m = json.loads((gallery_dir() / m["id"] / "meta.json").read_text())
            except (OSError, ValueError):
                continue
            rows.append(gallery_row(m))
    rows.sort(key=lambda r: -(r["created"] or 0))
    return {"items": rows}


@app.post("/api/gallery")
async def api_gallery_add(request: Request):
    """Keeps a generated sprite: a copy of the image and the recipe it was made with (LoRA, seed, epoch...). It stays
    when the result is deleted, and the LoRA it was made with can no longer be deleted while it is here."""
    b = await request.json()
    src = safe_path(str(b.get("path", "")), write=False)
    if (
        src.suffix.lower() != ".png"
        or not src.is_file()
        or not is_variant(src)
        or not (src.parent / "job.txt").exists()
    ):
        raise HTTPException(400, "choose a sprite generated in Results")
    me = who_now()
    owner = me.id if me else ""
    for m in gallery_metas():  # saving the same sprite twice gives the first one back
        if m.get("source") == str(src) and m.get("owner") == owner and can("gallery", m["id"], True):
            return {**gallery_row(m), "already": True}
    r = result_set(src.parent)
    params = r["params"]
    seed = next((v["seed"] for v in r["variants"] if v["path"] == str(src)), "")
    wh = png_size(src) or (0, 0)
    lora = params.get("lora", "")
    info: dict[str, Any] = {}
    sha = ""
    lid = params.get("lora_id") or ""
    lf = lora_dir() / lora if lora and lora == Path(lora).name else None
    if lf is not None and lf.is_file():
        li = lora_info(lora)
        lid = lid or lora_id(lora)
        info = {k: li.get(k) for k in ("model", "checkpoint", "epoch", "final", "trained", "installed_at", "size")}
        with contextlib.suppress(OSError):
            sha = file_sha(lf)
    gid = secrets.token_hex(8)
    meta = {
        "id": gid,
        "owner": owner,
        "name": name_ok(b.get("name") or src.stem),
        "created": time.time(),
        "folder": gallery_label(b.get("folder")),
        "tags": gallery_tags(b.get("tags")),
        "note": str(b.get("note") or "")[:500],
        "source": str(src),
        "seed": seed,
        "model": params.get("model", ""),
        "model_id": ACCOUNTS.uid("model", params["model"]) if params.get("model") else "",
        "group": params.get("group", ""),
        "kind": params.get("kind", ""),
        "lora": lora,
        "lora_id": lid,
        "lora_sha": sha,
        "lora_info": info,
        "params": params,
        "prompt": r["prompt"],
        "what": r["what"],
        "w": wh[0],
        "h": wh[1],
    }
    d = gallery_dir() / gid
    d.mkdir(parents=True)
    shutil.copy2(src, d / "image.png")
    _gallery_write(gid, meta)
    claim("gallery", gid)
    return {**gallery_row(meta), "already": False}


@app.get("/api/gallery/{gid}")
def api_gallery_get(gid: str):
    m = gallery_meta(gid)
    return {**gallery_row(m), **{k: m.get(k) for k in ("params", "prompt", "what", "lora_sha", "lora_info", "source")}}


@app.get("/api/gallery/{gid}/image")
def api_gallery_image(gid: str):
    gallery_meta(gid)
    return FileResponse(gallery_dir() / gid / "image.png", headers={"Cache-Control": "private, no-cache"})


@app.patch("/api/gallery/{gid}")
async def api_gallery_patch(gid: str, request: Request):
    """Only the name, the folder, the tags and the note can change: the recipe stays as it was."""
    b = await request.json()
    with _lock:
        m = gallery_meta(gid, write=True)
        if "name" in b:
            m["name"] = name_ok(str(b["name"]))
        if "folder" in b:
            m["folder"] = gallery_label(b["folder"])
        if "tags" in b:
            m["tags"] = gallery_tags(b["tags"])
        if "note" in b:
            m["note"] = str(b["note"] or "")[:500]
        _gallery_write(gid, m)
    return gallery_row(m)


@app.post("/api/gallery/bulk")
async def api_gallery_bulk(request: Request):
    """The same change for several items: a folder ("" takes them out of any), tags to add or remove."""
    b = await request.json()
    ids = [str(i) for i in b.get("ids") or []][:MAX_GALLERY_EXPORT]
    add, drop = gallery_tags(b.get("add_tags")), set(gallery_tags(b.get("remove_tags")))
    done = 0
    with _lock:
        for gid in ids:
            if not GALLERY_ID.fullmatch(gid) or not can("gallery", gid, True):
                continue
            try:
                m = json.loads((gallery_dir() / gid / "meta.json").read_text())
            except (OSError, ValueError):
                continue
            if "folder" in b:
                m["folder"] = gallery_label(b["folder"])
            m["tags"] = gallery_tags([t for t in m.get("tags", []) if t not in drop] + add)
            _gallery_write(gid, m)
            done += 1
    return {"changed": done}


@app.post("/api/gallery/delete")
async def api_gallery_delete(request: Request):
    """Removes images from the gallery (their owner, or an administrator). The LoRAs they needed are free again
    once nothing else uses them."""
    b = await request.json()
    done = 0
    for gid in [str(i) for i in b.get("ids") or []][:MAX_GALLERY_EXPORT]:
        if not GALLERY_ID.fullmatch(gid) or not can("gallery", gid, True):
            continue
        shutil.rmtree(gallery_dir() / gid, ignore_errors=True)
        ACCOUNTS.forget("gallery", gid)
        done += 1
    return {"deleted": done}


@app.post("/api/gallery/export")
async def api_gallery_export(request: Request):
    """A zip of the chosen images (the ones the user may see), named after the sprites, with manifest.json holding
    each recipe. `folders` keeps the gallery folders as folders in the zip."""
    import zipfile

    b = await request.json()
    ids = [str(i) for i in b.get("ids") or []][:MAX_GALLERY_EXPORT]
    keep = bool(b.get("folders"))
    buf = io.BytesIO()
    used: set[str] = set()
    manifest = []
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for gid in ids:
            if not GALLERY_ID.fullmatch(gid) or not can("gallery", gid):
                continue
            try:
                m = json.loads((gallery_dir() / gid / "meta.json").read_text())
                data = (gallery_dir() / gid / "image.png").read_bytes()
            except (OSError, ValueError):
                continue
            total += len(data)
            if total > MAX_ZIP_TOTAL:
                raise HTTPException(400, f"too much to export at once (at most {MAX_ZIP_TOTAL // 1_000_000} MB)")
            base = (m.get("folder") + "/" if keep and m.get("folder") else "") + m["name"]
            arc, n = base + ".png", 1
            while arc.lower() in used:
                n += 1
                arc = f"{base}_{n}.png"
            used.add(arc.lower())
            z.writestr(arc, data)
            manifest.append(
                {
                    "file": arc,
                    **{
                        k: m.get(k)
                        for k in (
                            "name",
                            "folder",
                            "tags",
                            "seed",
                            "model",
                            "group",
                            "kind",
                            "lora",
                            "lora_info",
                            "params",
                            "prompt",
                            "what",
                            "created",
                        )
                    },
                }
            )
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    if not manifest:
        raise HTTPException(400, "nothing to export")
    return Response(
        buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="gallery.zip"', "Cache-Control": "private, no-store"},
    )


# ---- settings


@app.get("/api/settings")
def api_settings():
    s = settings()
    checks = {}
    for k in ("px_input", "px_game", "px_output", "comfy_dir", "lora_home"):
        path = Path(s[k])
        checks[k] = "ok" if path.is_dir() else "missing"
        if k == "px_output" and path.is_dir() and not os.access(path, os.W_OK):
            checks[k] = "read-only"
    sudo = (
        subprocess.run(["sudo", "-n", "-l", "systemctl", "stop", s["comfy_service"]], capture_output=True, text=True)
        if shutil.which("sudo")
        else None
    )
    return {
        "settings": s,
        "defaults": DEFAULTS,
        "checks": checks,
        "scripts": str(SCRIPTS),
        "can_control_comfy": bool(sudo and sudo.returncode == 0),
        "scripts_found": {
            n: (SCRIPTS / n).exists() for n in ("pxmake.sh", "pxbatch.sh", "pxmatrix.sh", "train_lora.sh")
        },
    }


@app.put("/api/settings")
async def api_settings_put(request: Request):
    b = await request.json()
    saved = _load("settings.json", {})
    for k in DEFAULTS:
        if k in b:
            v = str(b[k]).strip()
            if v and v != DEFAULTS[k]:
                saved[k] = v
            else:
                saved.pop(k, None)
    _save("settings.json", saved)
    return api_settings()


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


# ---- accounts: what exists before the first one belongs to the first administrator


def resource_exists(kind: str, key: str) -> bool:
    if kind == "model":
        return (Path(pm().model_dir(key)) / "model.json").exists() if re.fullmatch(r"[a-z0-9_]{1,40}", key) else False
    if kind == "result":
        return not key.startswith(".") and (setting_path("px_output") / key).is_dir()
    if kind == "palette":
        return bool(PAL_ID.fullmatch(key)) and (pal_dir() / f"{key}.json").is_file()
    if kind == "gallery":
        return bool(GALLERY_ID.fullmatch(key)) and (gallery_dir() / key / "meta.json").is_file()
    return False


def adopt_existing(admin: str) -> None:
    aid = ACCOUNTS.id_of(admin)
    for mid in pm().list_models():
        ACCOUNTS.claim("model", mid, aid)
    out = setting_path("px_output")
    for d in out.iterdir() if out.is_dir() else []:
        if d.is_dir() and d.name not in ("uploads", "picked", "recoloured") and not d.name.startswith("."):
            ACCOUNTS.claim("result", d.name, aid)
    for f in pal_dir().glob("*.json") if pal_dir().is_dir() else []:
        ACCOUNTS.claim("palette", f.stem, aid)
    with _lock:
        reg = project_registry()
        _save("projects.json", {"entries": [[o or aid, n] for o, n in reg["entries"]]})
        for j in JOBS.jobs.values():
            j.setdefault("owner", aid)
        JOBS.persist()


def _id_or_blank(owner: str, ids: dict[str, str]) -> str:
    return owner if owner.startswith(acc.ID_PREFIX) or not owner else ids.get(owner, "")


def migrate_identities() -> None:
    """Before 0.3.2 jobs, picks, projects and the per-user folders were keyed by user name. A name can be given
    again after a deletion, so they are keyed by the account's id now (accounts and shares: Accounts.migrate_ids).
    A name that no longer matches an account is dropped (its jobs and projects then belong to nobody but the
    administrators)."""
    users = ACCOUNTS.users()
    if not users:
        return
    ids = {n: u["id"] for n, u in users.items() if u.get("id")}
    with _lock:
        changed = False
        for j in JOBS.jobs.values():
            o = j.get("owner", "")
            if o and not o.startswith(acc.ID_PREFIX):
                j["owner"], changed = _id_or_blank(o, ids), True
        if changed:
            JOBS.persist()
        d = picks_load()
        if any(k and not k.startswith(acc.ID_PREFIX) for k in d["users"]):
            d["users"] = {
                (_id_or_blank(k, ids) or k): v
                for k, v in d["users"].items()
                if k == "" or k in ids or k.startswith(acc.ID_PREFIX)
            }
            _save("picks.json", d)
        reg = project_registry()
        if any(o and not o.startswith(acc.ID_PREFIX) for o, _ in reg["entries"]):
            _save("projects.json", {"entries": [[_id_or_blank(o, ids), n] for o, n in reg["entries"]]})
    out = setting_path("px_output")
    for top in ("uploads", "picked", "recoloured"):
        for name, uid in ids.items():
            src, dst = out / top / name, out / top / uid
            if src.is_dir() and not dst.exists():
                with contextlib.suppress(OSError):
                    src.rename(dst)


def user_removed(uid: str, target_id: str, label: str) -> None:
    """Everything a deleted user left that is keyed by their id goes to `target_id`, so that nothing of it can
    reach a new account (shares and ownership of resources are handled by Accounts)."""
    with _lock:
        for j in JOBS.jobs.values():
            if j.get("owner") == uid:
                j["owner"] = target_id
        JOBS.persist()
        d = picks_load()
        gone = d["users"].pop(uid, {})
        if gone:
            mine = d["users"].setdefault(target_id, {})
            mine.update({k: v for k, v in gone.items() if k not in mine})
        _save("picks.json", d)
        reg = project_registry()
        _save("projects.json", {"entries": [[target_id if o == uid else o, n] for o, n in reg["entries"]]})
    out = setting_path("px_output")
    for top in ("uploads", "picked", "recoloured"):
        src = out / top / uid
        if not src.is_dir():
            continue
        dest = out / top / target_id / f"from_{label}"
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            n = 1
            while dest.exists():
                n += 1
                dest = dest.with_name(f"from_{label}_{n}")
            shutil.move(str(src), str(dest))
        except OSError as e:
            print(f"user removed: could not move {src}: {e!r}", file=sys.stderr, flush=True)


migrate_identities()


app.include_router(auth_routes.make_router(ACCOUNTS, adopt_existing, resource_exists, user_removed))
app.mount("/", StaticFiles(directory=HERE / "static", html=True), name="static")


def announce_setup() -> None:
    """With no account yet, the page asks for a one-time code to create the administrator: it is printed here."""
    if os.environ.get("SG_WEB_PASSWORD"):
        print(
            "NOTE: SG_WEB_PASSWORD is no longer used: sign in with an account (Users page). Remove it from the service.",
            file=sys.stderr,
        )
    code = ACCOUNTS.setup_code()
    if code:
        print(
            f"Sprite Studio has no account yet. Open the page and create the administrator with this setup code: {code}\n"
            f"(it is also in {STATE_DIR / 'setup_code'}). Lost an administrator password later: python3 webapp/users_cli.py reset <name>",
            file=sys.stderr,
            flush=True,
        )


if __name__ == "__main__":
    import uvicorn

    announce_setup()
    confirm_update_when_healthy()
    uvicorn.run(
        app,
        host=os.environ.get("SG_WEB_HOST", "127.0.0.1"),
        port=int(os.environ.get("SG_WEB_PORT", "8189")),
        log_level="warning",
    )
