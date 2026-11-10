import json
import os
from types import SimpleNamespace
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bin import syncfiles


def make_args(**overrides):
    """
    Build an argparse-like namespace for Config that matches the CLI options.
    """
    base = dict(
        command="status",
        all=False,
        force=False,
        execute=False,
        verbose=False,
        difftool="auto",
        nogit=True,
        files=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def write_syncfiles(base_dir, defs):
    """
    Helper to create a .syncfiles file in base_dir.
    """
    cfg_path = os.path.join(base_dir, ".syncfiles")
    with open(cfg_path, "w", encoding="utf-8") as fp:
        json.dump(defs, fp)
    return cfg_path


def test_file_hash_matches_for_identical_files(tmp_path):
    file1 = tmp_path / "a.txt"
    file2 = tmp_path / "b.txt"
    file1.write_text("hello", encoding="utf-8")
    file2.write_text("hello", encoding="utf-8")

    h1 = syncfiles.file_hash(str(file1))
    h2 = syncfiles.file_hash(str(file2))

    assert h1 == h2


def test_file_hash_differs_for_different_files(tmp_path):
    file1 = tmp_path / "a.txt"
    file2 = tmp_path / "b.txt"
    file1.write_text("hello", encoding="utf-8")
    file2.write_text("world", encoding="utf-8")

    h1 = syncfiles.file_hash(str(file1))
    h2 = syncfiles.file_hash(str(file2))

    assert h1 != h2


def test_expand_dirs_and_should_ignore(tmp_path, monkeypatch):
    """
    Basic check that dirmap and ignore rules are expanded as expected.
    """
    base_dir = tmp_path / "repo"
    base_dir.mkdir()
    src_root = tmp_path / "src"
    src_root.mkdir()

    defs = {
        "dirmap": {
            ".": str(src_root),
        },
        "ignore": ["README.md", "*~"],
    }
    write_syncfiles(str(base_dir), defs)

    # Create files that may be ignored or included
    readme = base_dir / "README.md"
    readme.write_text("readme", encoding="utf-8")
    kept = base_dir / "keep.txt"
    kept.write_text("keep", encoding="utf-8")

    monkeypatch.chdir(base_dir)
    cfg = syncfiles.Config(make_args())

    # README is ignored via explicit path
    assert cfg.should_ignore(str(readme), "README.md")
    # A file matching *~ should be ignored via pattern
    tmp_ignored = base_dir / "foo~"
    tmp_ignored.write_text("tmp", encoding="utf-8")
    assert cfg.should_ignore(str(tmp_ignored), "foo~")
    # A regular file should not be ignored
    assert not cfg.should_ignore(str(kept), "keep.txt")


def test_list_file_pairs_without_git(tmp_path, monkeypatch):
    """
    Discover file pairs using the filesystem (nogit=True).
    """
    base_dir = tmp_path / "repo"
    base_dir.mkdir()
    src_root = tmp_path / "src"
    src_root.mkdir()

    defs = {
        "dirmap": {
            ".": str(src_root),
        },
    }
    write_syncfiles(str(base_dir), defs)

    # Create matching files in repo and src
    repo_file = base_dir / "config.txt"
    src_file = src_root / "config.txt"
    repo_file.write_text("value", encoding="utf-8")
    src_file.write_text("value", encoding="utf-8")

    monkeypatch.chdir(base_dir)
    cfg = syncfiles.Config(make_args())
    pairs = cfg.list_file_pairs()

    assert len(pairs) == 1
    pair = pairs[0]
    assert os.path.basename(pair.file_rel_path) == "config.txt"
    assert os.path.exists(pair.dir_file())
    assert os.path.exists(pair.src_file())
    assert pair.is_match()


def test_validate_config_false_exits(tmp_path, monkeypatch):
    """
    A validate expression that evaluates to False should cause a clean exit,
    unless --force is specified.
    """
    base_dir = tmp_path / "repo"
    base_dir.mkdir()
    src_root = tmp_path / "src"
    src_root.mkdir()

    defs = {
        "dirmap": {
            ".": str(src_root),
        },
        "validate": "False",
    }
    write_syncfiles(str(base_dir), defs)

    monkeypatch.chdir(base_dir)

    with pytest.raises(SystemExit) as excinfo:
        syncfiles.Config(make_args())
    # False validation should cause an exit with code 0
    assert excinfo.value.code == 0


def test_validate_config_raises_without_force(tmp_path, monkeypatch):
    """
    An invalid validate expression should cause a non-zero exit code unless
    --force is specified.
    """
    base_dir = tmp_path / "repo"
    base_dir.mkdir()
    src_root = tmp_path / "src"
    src_root.mkdir()

    defs = {
        "dirmap": {
            ".": str(src_root),
        },
        "validate": "this_name_is_not_defined",
    }
    write_syncfiles(str(base_dir), defs)

    monkeypatch.chdir(base_dir)

    with pytest.raises(SystemExit) as excinfo:
        syncfiles.Config(make_args())
    assert excinfo.value.code == 1





def test_copyfile_keeps_exec_bit(tmp_path):
    src = tmp_path / "repo" / "tool.sh"
    plain = tmp_path / "repo" / "notes.txt"
    src.parent.mkdir()
    src.write_text("#!/bin/sh\n", encoding="utf-8")
    plain.write_text("x", encoding="utf-8")
    os.chmod(src, 0o755)
    os.chmod(plain, 0o644)
    cfg = SimpleNamespace(args=make_args(execute=True))

    dst = tmp_path / "live" / "tool.sh"
    syncfiles.Config.copyfile(cfg, str(src), str(dst))
    assert os.stat(dst).st_mode & 0o777 == 0o755

    # an existing non-executable copy gets the bit too, its other bits kept
    os.chmod(dst, 0o600)
    syncfiles.Config.copyfile(cfg, str(src), str(dst))
    assert os.stat(dst).st_mode & 0o777 == 0o711

    dst_plain = tmp_path / "live" / "notes.txt"
    syncfiles.Config.copyfile(cfg, str(plain), str(dst_plain))
    assert not os.stat(dst_plain).st_mode & 0o111
