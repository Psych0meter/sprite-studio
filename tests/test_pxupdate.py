"""scripts/pxupdate.sh: a fast-forward pull of a git checkout, and a refusal when its tracked files were edited."""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "pxupdate.sh"


def git(cwd, *a):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True, capture_output=True, text=True
    )


def setup_repos(tmp_path):
    up = tmp_path / "up"
    up.mkdir()
    git(up, "init", "-q", "-b", "main")
    (up / "a.txt").write_text("1")
    git(up, "add", ".")
    git(up, "commit", "-q", "-m", "one")
    clone = tmp_path / "lora" / "sd-scripts"
    clone.parent.mkdir()
    git(tmp_path, "clone", "-q", str(up), str(clone))
    (up / "a.txt").write_text("2")
    git(up, "commit", "-q", "-am", "two")
    return up, clone


def run(tmp_path, target="sd-scripts"):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "PX_LORA_HOME": str(tmp_path / "lora")}
    return subprocess.run([str(SCRIPT), target], env=env, capture_output=True, text=True)


def test_it_pulls_and_says_before_and_after(tmp_path):
    _, clone = setup_repos(tmp_path)
    r = run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert (clone / "a.txt").read_text() == "2" and "done" in r.stdout


def test_it_refuses_when_tracked_files_were_edited(tmp_path):
    _, clone = setup_repos(tmp_path)
    (clone / "a.txt").write_text("mine")
    r = run(tmp_path)
    assert r.returncode == 1 and "local changes" in r.stderr
    assert (clone / "a.txt").read_text() == "mine"


def test_it_rejects_an_unknown_target(tmp_path):
    assert run(tmp_path, "rm").returncode == 2
