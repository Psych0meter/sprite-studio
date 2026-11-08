#!/usr/bin/env python3
"""pxupdate.py: update ComfyUI, kohya sd-scripts or Sprite Studio itself to their latest *released* version.

A release is the highest stable tag `vX.Y.Z` of the project's repository (a branch tip, a pre-release such as
`v1.2.0-rc.1` or a tag older than what is installed is never used). Every update checks itself afterwards and goes
back to what was installed when the check fails.

  pxupdate.py latest <repo>             the latest release tag of a repository (a URL or a folder)
  pxupdate.py comfyui                   ComfyUI: check out the release tag, its requirements, restart, wait for it
  pxupdate.py sd-scripts                kohya sd-scripts: check out the release tag, its requirements, import test
  pxupdate.py studio                    Sprite Studio: install the release over this folder (the app restarts itself
                                        afterwards and confirms the update, see below)
  pxupdate.py studio-rollback           put back the version that was installed before the last studio update
  pxupdate.py studio-guard              run by run.sh at each start: after a studio update that never got confirmed
                                        (the new version crashes at start) it rolls back by itself

Environment: COMFY (ComfyUI folder, default ~/ComfyUI), PX_LORA_HOME (default ~/lora), COMFY_SERVICE (systemd unit,
default comfyui; restarting it needs the sudo rule install.sh adds), PX_COMFY_URL (default http://127.0.0.1:8188),
SG_WEB_STATE (the app's state folder: backups, freeze files, the pending marker; default ~/.spritegen-web),
SG_UPDATE_REPO (where Sprite Studio's releases come from). PXUPDATE_APP and PXUPDATE_HEALTH are for tests.

Studio update, step by step: the release is cloned (from a tag that is on the default branch), its VERSION must
match the tag and its Python must compile; what is installed is copied to <state>/backups/; the files are
replaced; a marker `<state>/update_pending/` records the backup and a start counter. The app then restarts itself;
once it answers on its own port for 25 seconds it removes the marker (confirmed). If it never does (it crashes: the
counter reaches 3 in run.sh; it runs but does not answer: the app checks itself) the backup is restored.
The data, models, accounts and the Python environment are never touched.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

STABLE = re.compile(r"^v?(\d+(?:\.\d+)*)$")
STUDIO_REPO = "https://github.com/Psych0meter/sprite-studio.git"
APP_FILES = ("scripts", "webapp", "docs", "README.md", "CHANGELOG.md", "VERSION")
KEEP_BACKUPS = 3
GUARD_BOOTS = 3  # starts of an unconfirmed update before it is rolled back


class UpdateError(Exception):
    """What to tell the person: the update did not happen (or was undone)."""


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 900, check: bool = True) -> str:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode:
        raise UpdateError(f"{' '.join(cmd[:3])}: {(r.stderr or r.stdout).strip()[-400:]}")
    return (r.stdout or "").strip()


def version_key(tag: str) -> tuple[int, ...]:
    m = STABLE.match(tag)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def latest_release(repo: str) -> str | None:
    """The highest stable tag of a repository (url or folder), or None."""
    out = run(["git", "ls-remote", "--tags", "--refs", str(repo)], timeout=90)
    tags = [ln.split("refs/tags/", 1)[1] for ln in out.splitlines() if "refs/tags/" in ln]
    stable = [t for t in tags if STABLE.match(t)]
    return max(stable, key=version_key) if stable else None


def state_dir() -> Path:
    return Path(os.environ.get("SG_WEB_STATE", Path.home() / ".spritegen-web"))


def app_dir() -> Path:
    return Path(os.environ.get("PXUPDATE_APP", Path(__file__).resolve().parent.parent))


def say(msg: str) -> None:
    print(msg, flush=True)


# ---- ComfyUI and kohya sd-scripts: git checkouts moved to a release tag


def comfy_up() -> bool:
    try:
        urllib.request.urlopen(os.environ.get("PX_COMFY_URL", "http://127.0.0.1:8188") + "/system_stats", timeout=3)
        return True
    except OSError:
        return False


def service_active(name: str) -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", name]).returncode == 0


def pip_install(venv: Path, *args: str) -> None:
    pip = venv / "bin" / "pip"
    if not pip.exists():
        say(f"no environment at {venv}: skipped the Python packages")
        return
    run([str(pip), "install", "-q", *args], timeout=1800)


def git_target(target: str) -> None:
    comfy = Path(os.environ.get("COMFY", Path.home() / "ComfyUI"))
    lora = Path(os.environ.get("PX_LORA_HOME", Path.home() / "lora"))
    d, venv = (comfy, comfy / ".venv") if target == "comfyui" else (lora / "sd-scripts", lora / "venv")
    if not (d / ".git").exists():
        raise UpdateError(f"{d} is not a git checkout")
    if run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=d):
        raise UpdateError(f"{d} has local changes to tracked files: not updating (commit or stash them first)")
    run(["git", "fetch", "--quiet", "--tags", "origin"], cwd=d, timeout=300)
    tag = latest_release(run(["git", "remote", "get-url", "origin"], cwd=d))
    if not tag:
        raise UpdateError("no release (stable vX.Y.Z tag) was found upstream")
    if subprocess.run(["git", "merge-base", "--is-ancestor", tag, "HEAD"], cwd=d).returncode == 0:
        say(f"already at or beyond the latest release ({tag}): nothing to do")
        return
    before = run(["git", "rev-parse", "HEAD"], cwd=d)
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=d)
    back = branch if branch != "HEAD" else before
    say(f"before: {run(['git', 'describe', '--tags', '--always'], cwd=d)}")
    freeze = state_dir() / "update" / f"{target}-freeze.txt"
    freeze.parent.mkdir(parents=True, exist_ok=True)
    pip = venv / "bin" / "pip"
    if pip.exists():
        freeze.write_text(run([str(pip), "freeze"]))
    was_up = target == "comfyui" and (comfy_up() or service_active(os.environ.get("COMFY_SERVICE", "comfyui")))
    try:
        run(["git", "checkout", "--quiet", "--detach", tag], cwd=d)
        say(f"after:  {run(['git', 'describe', '--tags', '--always'], cwd=d)}")
        if (d / "requirements.txt").exists():
            pip_install(venv, "-r", str(d / "requirements.txt"))
        healthy(target, d, venv, restart=was_up)
    except (UpdateError, subprocess.TimeoutExpired, OSError) as e:
        say(f"the update failed ({e}): going back to {back}")
        run(["git", "checkout", "--quiet", back], cwd=d, check=False)
        if freeze.exists() and freeze.read_text().strip():
            run([str(pip), "install", "-q", "-r", str(freeze)], check=False, timeout=1800)
        try:
            healthy(target, d, venv, restart=was_up)
            say("rolled back: the previous version works again")
        except UpdateError as again:
            say(f"rolled back, but it does not check out either: {again}")
        raise UpdateError(f"update to {tag} failed and was rolled back: {e}") from None
    say(f"updated to {tag}")


def healthy(target: str, d: Path, venv: Path, restart: bool) -> None:
    """The project works after a change: ComfyUI answers again, kohya's training code imports."""
    custom = os.environ.get("PXUPDATE_HEALTH")
    if custom:
        if subprocess.run(custom, shell=True, cwd=d).returncode:
            raise UpdateError("the health check failed")
        return
    if target == "comfyui":
        if not restart:
            say("ComfyUI was not running: not restarted")
            return
        service = os.environ.get("COMFY_SERVICE", "comfyui")
        if subprocess.run(["sudo", "-n", "systemctl", "restart", service], capture_output=True).returncode:
            raise UpdateError("could not restart ComfyUI (the sudo rule for systemctl is missing)")
        for _ in range(60):
            if comfy_up():
                say("ComfyUI restarted and answers")
                return
            time.sleep(2)
        raise UpdateError("ComfyUI does not answer 2 minutes after the restart")
    py = venv / "bin" / "python"
    if py.exists():
        code = f"import sys; sys.path.insert(0, {str(d)!r}); import library.train_util"
        if subprocess.run([str(py), "-c", code], cwd=d, capture_output=True, timeout=300).returncode:
            raise UpdateError("kohya's training code does not import")
        say("kohya's training code imports")


# ---- Sprite Studio itself: a copy of the release over the installed files, with a backup and a pending marker


def marker() -> Path:
    return state_dir() / "update_pending"


def backups() -> list[Path]:
    root = state_dir() / "backups"
    return (
        sorted((p for p in root.glob("app-*") if p.is_dir()), key=lambda p: p.stat().st_mtime) if root.is_dir() else []
    )


def read_version(d: Path) -> str:
    try:
        return (d / "VERSION").read_text().strip()
    except OSError:
        return ""


def copy_files(src: Path, dst: Path) -> None:
    for name in APP_FILES:
        s, t = src / name, dst / name
        if not s.exists():
            continue
        if s.is_dir():
            if name in ("scripts", "docs") and t.is_dir():
                shutil.rmtree(t, ignore_errors=True)  # (a file the new version dropped goes too)
            if name == "webapp" and (t / "static").is_dir():
                shutil.rmtree(t / "static")
            shutil.copytree(s, t, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".venv", "__pycache__", "*.pyc"))
        else:
            shutil.copy2(s, t)
    for f in list((dst / "scripts").glob("*.sh")) + list((dst / "scripts").glob("*.py")) + [dst / "webapp" / "run.sh"]:
        if f.exists():
            f.chmod(f.stat().st_mode | 0o111)


def studio_update() -> None:
    app = app_dir()
    if (app / ".git").exists():
        raise UpdateError("this is a git checkout, not an installation: update it with git")
    if marker().exists():
        raise UpdateError("the previous update is not confirmed yet: wait a minute, or roll it back")
    repo = os.environ.get("SG_UPDATE_REPO", STUDIO_REPO)
    tag = latest_release(repo)
    if not tag:
        raise UpdateError("no release (stable vX.Y.Z tag) was found upstream")
    current = read_version(app)
    if version_key(tag) <= version_key(current):
        say(f"Sprite Studio {current} is the latest release ({tag}): nothing to do")
        return
    with tempfile.TemporaryDirectory() as t:
        src = Path(t) / "src"
        run(["git", "clone", "--quiet", repo, str(src)], timeout=600)
        head = run(["git", "rev-parse", "origin/HEAD"], cwd=src, check=False) or run(
            ["git", "rev-parse", "HEAD"], cwd=src
        )
        if subprocess.run(["git", "merge-base", "--is-ancestor", tag, head], cwd=src).returncode:
            raise UpdateError(f"{tag} is not on the default branch: refused")
        run(["git", "checkout", "--quiet", "--detach", tag], cwd=src)
        if read_version(src) != tag.lstrip("v"):
            raise UpdateError(f"{tag} carries version {read_version(src)}: refused")
        run([sys.executable, "-m", "compileall", "-q", str(src / "webapp"), str(src / "scripts")])
        say(f"downloaded {tag}; it compiles")
        back = state_dir() / "backups" / f"app-{current}-{int(time.time())}"
        back.mkdir(parents=True)
        copy_files(app, back)
        say(f"backup of {current}: {back}")
        for old in backups()[:-KEEP_BACKUPS]:
            shutil.rmtree(old, ignore_errors=True)
        try:
            copy_files(src, app)
            venv = app / "webapp" / ".venv"
            if (venv / "bin" / "pip").exists():
                import tomllib

                deps = tomllib.loads((src / "pyproject.toml").read_text())["project"]["dependencies"]
                web = [d for d in deps if re.match(r"(fastapi|uvicorn)", d, re.I)]
                if web:
                    run([str(venv / "bin" / "pip"), "install", "-q", *web], timeout=900)
        except (UpdateError, OSError, subprocess.TimeoutExpired) as e:
            copy_files(back, app)
            raise UpdateError(f"the files could not be installed ({e}): the previous version was put back") from None
    m = marker()
    m.mkdir(parents=True)
    for k, v in {
        "boots": "0",
        "backup": str(back),
        "from": current,
        "to": tag.lstrip("v"),
        "at": str(time.time()),
    }.items():
        (m / k).write_text(v)
    say(f"installed {tag}. Sprite Studio restarts; it rolls back by itself if the new version does not start.")


def studio_rollback(reason: str = "") -> None:
    app = app_dir()
    m = marker()
    back = Path((m / "backup").read_text().strip()) if (m / "backup").exists() else (backups() or [None])[-1]
    if not back or not back.is_dir():
        raise UpdateError("no backup to go back to")
    came_from = read_version(app)
    copy_files(back, app)
    shutil.rmtree(m, ignore_errors=True)
    result = state_dir() / "last_update.json"
    result.write_text(
        json.dumps(
            {"status": "rolled back", "from": came_from, "to": read_version(app), "reason": reason, "at": time.time()}
        )
    )
    say(f"rolled back to {read_version(app)} {('(' + reason + ')') if reason else ''}")


def studio_guard() -> None:
    """At each start of the service: an update that has not been confirmed by its third start is undone."""
    m = marker()
    if not (m / "boots").exists():
        return
    n = int((m / "boots").read_text().strip() or 0) + 1
    (m / "boots").write_text(str(n))
    if n >= GUARD_BOOTS:
        say(f"the new version was started {n} times without being confirmed: going back")
        studio_rollback("it did not start")


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    try:
        if cmd == "latest" and len(argv) > 2:
            say(latest_release(argv[2]) or "")
        elif cmd in ("comfyui", "sd-scripts"):
            git_target(cmd)
        elif cmd == "studio":
            studio_update()
        elif cmd == "studio-rollback":
            studio_rollback(argv[2] if len(argv) > 2 else "rolled back by hand")
        elif cmd == "studio-guard":
            studio_guard()
        else:
            print((__doc__ or "").strip(), file=sys.stderr if cmd else sys.stdout)
            return 2 if cmd else 0
    except UpdateError as e:
        print(str(e), file=sys.stderr, flush=True)
        return 1
    except subprocess.TimeoutExpired as e:
        print(f"timed out: {e.cmd[:3]}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
