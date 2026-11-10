"""scripts/pxupdate.py: only released versions, a check after every update, and the way back when it fails."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pxupdate.py"
sys.path.insert(0, str(SCRIPT.parent))
import pxupdate  # noqa: E402


def git(cwd, *a):
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def commit(repo, files, msg, tag=None):
    for name, text in files.items():
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg)
    if tag:
        git(repo, "tag", tag)


@pytest.fixture
def upstream(tmp_path):
    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    commit(up, {"VERSION": "1.0.0\n", "a.txt": "one"}, "one", "v1.0.0")
    return up


def run(args, env, check=False):
    e = {"PATH": os.environ["PATH"], "HOME": str(env["home"]), "SG_WEB_STATE": str(env["home"] / "state")}
    e.update({k: str(v) for k, v in env.items() if k != "home"})
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=e, capture_output=True, text=True)


def test_the_latest_release_is_the_highest_stable_tag(upstream):
    commit(upstream, {"a.txt": "2"}, "two", "v1.2.0")
    commit(upstream, {"a.txt": "3"}, "three", "v1.10.0")
    commit(upstream, {"a.txt": "4"}, "rc", "v2.0.0-rc.1")
    commit(upstream, {"a.txt": "5"}, "beta", "nightly")
    assert pxupdate.latest_release(str(upstream)) == "v1.10.0"  # not 1.2.0 (a number, not text), not a pre-release
    assert pxupdate.version_key("v1.10.0") > pxupdate.version_key("v1.2.0")


@pytest.fixture
def checkout(tmp_path, upstream):
    home = tmp_path / "home"
    (home / "lora").mkdir(parents=True)
    git(tmp_path, "clone", "-q", str(upstream), str(home / "lora" / "sd-scripts"))
    return {"home": home, "PX_LORA_HOME": home / "lora", "dir": home / "lora" / "sd-scripts"}


def test_a_checkout_moves_to_the_latest_release_not_to_the_branch_tip(checkout, upstream):
    commit(upstream, {"a.txt": "2"}, "two", "v1.1.0")
    commit(upstream, {"a.txt": "tip"}, "unreleased work on main")
    r = run(["sd-scripts"], checkout)
    assert r.returncode == 0, r.stderr
    d = checkout["dir"]
    assert (d / "a.txt").read_text() == "2" and git(d, "describe", "--tags") == "v1.1.0"


def test_nothing_happens_when_the_checkout_is_at_or_past_the_latest_release(checkout, upstream):
    d = checkout["dir"]
    commit(d, {"a.txt": "mine"}, "my own work on top of the release")
    r = run(["sd-scripts"], checkout)
    assert r.returncode == 0 and "nothing to do" in r.stdout
    assert (d / "a.txt").read_text() == "mine"


def test_local_edits_stop_the_update(checkout, upstream):
    commit(upstream, {"a.txt": "2"}, "two", "v1.1.0")
    (checkout["dir"] / "a.txt").write_text("edited here")
    r = run(["sd-scripts"], checkout)
    assert r.returncode == 1 and "local changes" in r.stderr
    assert (checkout["dir"] / "a.txt").read_text() == "edited here"


def test_a_failed_check_puts_the_previous_version_back(checkout, upstream):
    commit(upstream, {"a.txt": "2"}, "two", "v1.1.0")
    d = checkout["dir"]
    before = git(d, "rev-parse", "HEAD")
    r = run(["sd-scripts"], {**checkout, "PXUPDATE_HEALTH": 'test "$(cat a.txt)" = one'})  # 'one' is the old file
    assert r.returncode == 1 and "rolled back" in r.stderr
    assert git(d, "rev-parse", "HEAD") == before and git(d, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert (d / "a.txt").read_text() == "one"


# ---- Sprite Studio itself


@pytest.fixture
def studio(tmp_path, upstream):
    """An installation (a plain copy of v1.0.0, no .git), and a release v1.1.0 upstream."""
    commit(
        upstream,
        {"scripts/tool.sh": "#!/bin/sh\n", "webapp/app.py": "print('v1')\n", "webapp/static/i.js": "1"},
        "files",
    )
    git(upstream, "tag", "-d", "v1.0.0")
    git(upstream, "tag", "v1.0.0")
    app = tmp_path / "app"
    (app / "scripts").mkdir(parents=True)
    (app / "webapp" / "static").mkdir(parents=True)
    (app / "VERSION").write_text("1.0.0\n")
    (app / "scripts" / "tool.sh").write_text("#!/bin/sh\n")
    (app / "webapp" / "app.py").write_text("print('v1')\n")
    (app / "webapp" / "static" / "old.js").write_text("gone in the next version")
    commit(
        upstream,
        {
            "VERSION": "1.1.0\n",
            "webapp/app.py": "print('v1.1')\n",
            "pyproject.toml": '[project]\nname="x"\ndependencies=["fastapi>=0.110","numpy"]\n',
        },
        "release",
        "v1.1.0",
    )
    git(upstream, "rm", "-q", "-f", "webapp/static/i.js")
    return {"home": tmp_path / "home", "PXUPDATE_APP": app, "SG_UPDATE_REPO": upstream, "app": app}


def test_studio_installs_the_release_keeps_a_backup_and_waits_for_confirmation(studio):
    app = studio["app"]
    r = run(["studio"], studio)
    assert r.returncode == 0, r.stderr
    assert (app / "VERSION").read_text().strip() == "1.1.0" and "v1.1" in (app / "webapp" / "app.py").read_text()
    state = studio["home"] / "state"
    assert (state / "update_pending" / "boots").read_text() == "0"
    backup = Path((state / "update_pending" / "backup").read_text())
    assert (backup / "VERSION").read_text().strip() == "1.0.0"
    assert run(["studio"], studio).returncode == 1  # not while the last one is unconfirmed


def test_an_unconfirmed_update_is_rolled_back_at_the_third_start(studio):
    app = studio["app"]
    run(["studio"], studio)
    for _ in range(2):  # the new version crashes at start, systemd starts it again
        assert run(["studio-guard"], studio).returncode == 0
        assert (app / "VERSION").read_text().strip() == "1.1.0"
    r = run(["studio-guard"], studio)
    assert "going back" in r.stdout
    assert (app / "VERSION").read_text().strip() == "1.0.0" and "v1" in (app / "webapp" / "app.py").read_text()
    assert not (studio["home"] / "state" / "update_pending").exists()
    assert "rolled back" in (studio["home"] / "state" / "last_update.json").read_text()


def test_a_confirmed_update_is_not_touched_by_the_guard(studio):
    run(["studio"], studio)
    import shutil

    shutil.rmtree(studio["home"] / "state" / "update_pending")  # (what the app does once it has answered)
    for _ in range(5):
        run(["studio-guard"], studio)
    assert (studio["app"] / "VERSION").read_text().strip() == "1.1.0"


def test_studio_refuses_a_version_that_is_not_the_latest_or_not_on_the_branch(studio, upstream):
    (studio["app"] / "VERSION").write_text("1.1.0\n")
    assert "nothing to do" in run(["studio"], studio).stdout
    git(upstream, "checkout", "-q", "-b", "side")
    commit(upstream, {"VERSION": "9.9.9\n"}, "side release", "v9.9.9")
    git(upstream, "checkout", "-q", "main")
    r = run(["studio"], studio)
    assert r.returncode == 1 and "not on the default branch" in r.stderr


def test_a_git_checkout_is_never_overwritten(studio):
    (studio["app"] / ".git").mkdir()
    r = run(["studio"], studio)
    assert r.returncode == 1 and "git checkout" in r.stderr


def test_an_update_cut_short_while_replacing_files_is_rolled_back_at_the_next_start(studio):
    app = studio["app"]
    # what an interrupted update leaves: the marker (written first) says "applying", the files are half replaced
    state = studio["home"] / "state"
    backup = state / "backups" / "app-1.0.0-1"
    backup.mkdir(parents=True)
    (backup / "VERSION").write_text("1.0.0\n")
    (backup / "webapp").mkdir()
    (backup / "webapp" / "app.py").write_text("print('v1')\n")
    marker = state / "update_pending"
    marker.mkdir()
    for k, v in {"boots": "0", "backup": str(backup), "from": "1.0.0", "to": "1.1.0", "phase": "applying"}.items():
        (marker / k).write_text(v)
    (app / "VERSION").write_text("1.1.0\n")
    (app / "webapp" / "app.py").write_text("half written")
    r = run(["studio-guard"], studio)  # the very first start afterwards: no need to wait for three
    assert "cut short" in r.stdout
    assert (app / "VERSION").read_text().strip() == "1.0.0" and "v1" in (app / "webapp" / "app.py").read_text()
    assert not marker.exists()
    assert "interrupted" in (state / "last_update.json").read_text()


def test_the_marker_exists_before_any_installed_file_changes(studio):
    # a package install that fails after the files were copied puts everything back and leaves no marker
    app = studio["app"]
    venv = app / "webapp" / ".venv" / "bin"
    venv.mkdir(parents=True)
    pip = venv / "pip"
    pip.write_text('#!/bin/sh\nif [ "$1" = freeze ]; then echo "fastapi==0.1"; exit 0; fi\nexit 1\n')
    pip.chmod(0o755)
    r = run(["studio"], studio)
    assert r.returncode == 1 and "previous version was put back" in r.stderr
    assert (app / "VERSION").read_text().strip() == "1.0.0"
    assert not (studio["home"] / "state" / "update_pending").exists()
    assert (studio["home"] / "state" / "update" / "studio-freeze.txt").read_text().strip() == "fastapi==0.1"


def test_a_release_without_readable_dependencies_is_refused_before_anything_changes(studio, upstream):
    git(upstream, "tag", "-d", "v1.1.0")
    commit(upstream, {"VERSION": "1.2.0\n", "pyproject.toml": "not toml ["}, "bad", "v1.2.0")
    r = run(["studio"], studio)
    assert r.returncode == 1 and "refused" in r.stderr
    assert (studio["app"] / "VERSION").read_text().strip() == "1.0.0"
    assert not (studio["home"] / "state" / "update_pending").exists()
