"""Staleness check: syncfiles refuses to run from a checkout behind its upstream."""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bin import syncfiles


def make_args(**overrides):
    base = dict(command="status", all=False, force=False, execute=False, verbose=False,
                difftool="auto", nogit=False, allow_stale=False, files=[])
    base.update(overrides)
    return SimpleNamespace(**base)


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "init.defaultBranch=main",
                           *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repos(tmp_path):
    """A bare remote, a clone `a` (where syncfiles runs) and a clone `b` (another machine)."""
    remote, a, b, target = tmp_path / "remote.git", tmp_path / "a", tmp_path / "b", tmp_path / "target"
    target.mkdir()
    git(tmp_path, "init", "--bare", str(remote))
    git(tmp_path, "clone", str(remote), str(a))
    (a / ".syncfiles").write_text(json.dumps({"dirmap": {".": str(target)}}))
    (a / "conf.txt").write_text("v1\n")
    git(a, "add", ".")
    git(a, "commit", "-m", "init")
    git(a, "push", "-u", "origin", "HEAD")
    git(tmp_path, "clone", str(remote), str(b))
    return SimpleNamespace(remote=remote, a=a, b=b)


def push_from_b(repos):
    (repos.b / "conf.txt").write_text("v2\n")
    git(repos.b, "commit", "-am", "v2")
    git(repos.b, "push")


def test_up_to_date_runs(repos, monkeypatch, capsys):
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args(verbose=True))
    assert "up to date with origin/" in capsys.readouterr().out


def test_behind_exits(repos, monkeypatch, capsys):
    push_from_b(repos)
    monkeypatch.chdir(repos.a)
    with pytest.raises(SystemExit) as exc:
        syncfiles.Config(make_args())
    assert exc.value.code == 2
    assert "1 commit(s) behind" in capsys.readouterr().out


def test_allow_stale_overrides(repos, monkeypatch):
    push_from_b(repos)
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args(allow_stale=True))


def test_ahead_only_is_fine(repos, monkeypatch):
    (repos.a / "conf.txt").write_text("local\n")
    git(repos.a, "commit", "-am", "local only")
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args())


def test_recent_fetch_is_reused(repos, monkeypatch):
    """Within FETCH_MAX_AGE no new fetch: a push made meanwhile isn't seen yet."""
    git(repos.a, "fetch")
    push_from_b(repos)
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args())
    fetch_head = repos.a / ".git" / "FETCH_HEAD"
    old = time.time() - syncfiles.FETCH_MAX_AGE - 5
    os.utime(fetch_head, (old, old))
    with pytest.raises(SystemExit):
        syncfiles.Config(make_args())


def test_offline_only_warns(repos, monkeypatch, capsys):
    shutil.rmtree(repos.remote)
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args())
    assert "git fetch failed" in capsys.readouterr().out


def test_no_upstream_only_warns(repos, monkeypatch, capsys):
    git(repos.a, "checkout", "-b", "local-branch")
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args())
    assert "no upstream" in capsys.readouterr().out


def test_nogit_skips_check(repos, monkeypatch):
    push_from_b(repos)
    monkeypatch.chdir(repos.a)
    syncfiles.Config(make_args(nogit=True))


def test_version_flag():
    out = subprocess.run([sys.executable, str(ROOT / "bin" / "syncfiles.py"), "--version"],
                         capture_output=True, text=True, check=True).stdout
    assert out.startswith("syncfiles " + syncfiles.__version__)
    assert "sha1 " in out
