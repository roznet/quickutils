"""
Tests for sops-encrypted files in syncfiles.py.

They use throwaway age keys generated in a temp dir, and are skipped when
sops or age-keygen are not installed.
"""
import grp
import json
import os
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bin import syncfiles

pytestmark = pytest.mark.skipif(
    not (shutil.which("sops") and shutil.which("age-keygen")),
    reason="sops and age are required for the encrypted-file tests",
)

PLAIN = b"API_KEY=dummy-one\nOTHER_SECRET=dummy-two\n"


def make_args(**overrides):
    base = dict(command="status", all=False, force=False, execute=False, verbose=False,
                difftool="auto", nogit=True, files=[], show_secrets=False)
    base.update(overrides)
    return SimpleNamespace(**base)


def keygen(path):
    subprocess.run(["age-keygen", "-o", str(path)], check=True, capture_output=True)
    return subprocess.run(["age-keygen", "-y", str(path)], check=True, capture_output=True).stdout.decode().strip()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """
    repo/  (.syncfiles + .sops.yaml, rule: everything to key1)
    dest/  (the dirmap destination)
    key1 is the host key, key2 is a key that is not a recipient.
    """
    key1 = tmp_path / "key1.txt"
    key2 = tmp_path / "key2.txt"
    pub1 = keygen(key1)
    keygen(key2)
    monkeypatch.delenv("SOPS_AGE_KEY_CMD", raising=False)
    monkeypatch.delenv("SOPS_AGE_KEY", raising=False)
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(key1))

    repo = tmp_path / "repo"
    dest = tmp_path / "dest"
    (repo / "app").mkdir(parents=True)
    dest.mkdir()
    (repo / ".sops.yaml").write_text("creation_rules:\n  - path_regex: .*\n    age: {}\n".format(pub1))
    monkeypatch.chdir(repo)
    return SimpleNamespace(tmp=tmp_path, repo=repo, dest=dest, key1=key1, key2=key2, pub1=pub1)


def write_defs(env, encrypted):
    defs = {"dirmap": {".": str(env.dest)}, "encrypted": encrypted}
    (env.repo / ".syncfiles").write_text(json.dumps(defs))


def sops_encrypt(env, plaintext, repo_rel, sops_type="dotenv"):
    src = env.tmp / "plain-input"
    src.write_bytes(plaintext)
    out = subprocess.run(["sops", "--config", str(env.repo / ".sops.yaml"), "encrypt",
                          "--filename-override", str(env.repo / repo_rel),
                          "--input-type", sops_type, "--output-type", sops_type, str(src)],
                         check=True, capture_output=True)
    (env.repo / repo_rel).write_bytes(out.stdout)
    src.unlink()


def sops_decrypt(path, sops_type="dotenv"):
    return subprocess.run(["sops", "decrypt", "--input-type", sops_type, "--output-type", sops_type, str(path)],
                          check=True, capture_output=True).stdout


def is_encrypted(path, sops_type="dotenv"):
    out = subprocess.run(["sops", "filestatus", "--input-type", sops_type, str(path)], capture_output=True)
    return out.returncode == 0 and json.loads(out.stdout)["encrypted"] is True


def status_lines(capsys, **args):
    cfg = syncfiles.Config(make_args(**args))
    cfg.cmd_status()
    return cfg, capsys.readouterr().out


def no_temp_files(directory):
    return not [p for p in pathlib.Path(directory).rglob("*.syncfiles-tmp")]


# --- status -----------------------------------------------------------------

def test_status_match_modified_missing(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")

    _, out = status_lines(capsys)
    assert out.startswith("? app/.env.secrets")

    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(PLAIN)
    os.chmod(env.dest / "app/.env.secrets", 0o600)
    _, out = status_lines(capsys, all=True)
    assert out.startswith(". app/.env.secrets")

    (env.dest / "app/.env.secrets").write_bytes(PLAIN + b"NEW=1\n")
    _, out = status_lines(capsys)
    assert out.startswith("M app/.env.secrets")
    assert "dummy" not in out


def test_status_no_key(env, capsys, monkeypatch):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(env.key2))
    cfg, out = status_lines(capsys)
    assert out.startswith("K app/.env.secrets")
    assert not cfg.errors


def test_status_plaintext_listed_as_encrypted(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    (env.repo / "app/.env.secrets").write_bytes(PLAIN)
    cfg, out = status_lines(capsys)
    assert out.startswith("X app/.env.secrets")
    assert cfg.errors


def test_status_plaintext_binary_listed_as_encrypted(env, capsys):
    write_defs(env, {"app/AuthKey.p8": "binary"})
    (env.repo / "app/AuthKey.p8").write_bytes(b"\x00not encrypted")
    cfg, out = status_lines(capsys)
    assert out.startswith("X app/AuthKey.p8")


def test_status_declared_file_not_in_repo_yet(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(PLAIN)
    _, out = status_lines(capsys)
    assert out.startswith("A app/.env.secrets")


# --- install / push ---------------------------------------------------------

def test_install_writes_plaintext_mode_600(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")

    cfg = syncfiles.Config(make_args())
    cfg.cmd_install()
    out = capsys.readouterr().out
    assert "decrypt " in out and "Preview mode" in out
    assert not (env.dest / "app/.env.secrets").exists()

    cfg = syncfiles.Config(make_args(execute=True))
    cfg.cmd_install()
    target = env.dest / "app/.env.secrets"
    assert target.read_bytes() == PLAIN
    assert target.stat().st_mode & 0o777 == 0o600
    assert no_temp_files(env.dest)
    assert "dummy" not in capsys.readouterr().out


def test_install_replaces_existing_and_fixes_mode(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    (env.dest / "app").mkdir()
    target = env.dest / "app/.env.secrets"
    target.write_bytes(PLAIN)
    os.chmod(target, 0o644)

    _, out = status_lines(capsys)
    assert out.startswith("M app/.env.secrets") and "expected 0o600" in out

    syncfiles.Config(make_args(execute=True)).cmd_install()
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.read_bytes() == PLAIN


def test_install_custom_mode_and_group(env):
    group = grp.getgrgid(os.getgid()).gr_name
    write_defs(env, {"app/secrets.json": {"type": "json", "mode": "0640", "group": group}})
    sops_encrypt(env, b'{"db_password": "dummy"}', "app/secrets.json", "json")
    syncfiles.Config(make_args(execute=True)).cmd_install()
    st = (env.dest / "app/secrets.json").stat()
    assert st.st_mode & 0o777 == 0o640
    assert st.st_gid == os.getgid()


def test_install_skips_no_key_and_refuses_plaintext(env, capsys, monkeypatch):
    write_defs(env, {"app/.env.secrets": "dotenv", "app/plain.json": "json"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    (env.repo / "app/plain.json").write_bytes(b'{"a": "dummy"}')
    monkeypatch.setenv("SOPS_AGE_KEY_FILE", str(env.key2))

    cfg = syncfiles.Config(make_args(execute=True))
    cfg.cmd_install()
    out = capsys.readouterr().out
    assert "skip (no key on this host)" in out
    assert "REFUSING" in out
    assert cfg.errors
    assert not (env.dest / "app").exists()


def test_binary_round_trip(env):
    data = bytes(range(256))
    write_defs(env, {"app/AuthKey.p8": "binary"})
    sops_encrypt(env, data, "app/AuthKey.p8", "binary")
    syncfiles.Config(make_args(execute=True)).cmd_install()
    assert (env.dest / "app/AuthKey.p8").read_bytes() == data


# --- pull -------------------------------------------------------------------

def test_pull_new_file_is_encrypted(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(PLAIN)

    syncfiles.Config(make_args()).cmd_pull()
    assert "encrypt " in capsys.readouterr().out
    assert not (env.repo / "app/.env.secrets").exists()

    cfg = syncfiles.Config(make_args(execute=True))
    cfg.cmd_pull()
    repo_file = env.repo / "app/.env.secrets"
    assert not cfg.errors
    assert is_encrypted(repo_file)
    assert b"dummy" not in repo_file.read_bytes()
    assert sops_decrypt(repo_file) == PLAIN
    assert no_temp_files(env.repo)


def test_pull_existing_keeps_unchanged_ciphertext(env):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    repo_file = env.repo / "app/.env.secrets"
    before = dict(l.split("=", 1) for l in repo_file.read_text().splitlines() if "=" in l)

    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(PLAIN.replace(b"dummy-one", b"dummy-changed"))
    cfg = syncfiles.Config(make_args(execute=True))
    cfg.cmd_pull()
    assert not cfg.errors

    after = dict(l.split("=", 1) for l in repo_file.read_text().splitlines() if "=" in l)
    assert after["OTHER_SECRET"] == before["OTHER_SECRET"]
    assert after["API_KEY"] != before["API_KEY"]
    assert sops_decrypt(repo_file) == PLAIN.replace(b"dummy-one", b"dummy-changed")


def test_pull_sops_failure_leaves_repo_untouched(env, capsys, monkeypatch):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    repo_file = env.repo / "app/.env.secrets"
    original = repo_file.read_bytes()
    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(PLAIN + b"NEW=1\n")

    def broken_edit(self, encrypted_path, plaintext_path, sops_type):
        # simulate sops leaving plaintext behind: must be caught by filestatus
        shutil.copyfile(plaintext_path, encrypted_path)
        return True

    monkeypatch.setattr(syncfiles.Sops, "edit_from", broken_edit)
    cfg = syncfiles.Config(make_args(execute=True))
    cfg.cmd_pull()
    assert cfg.errors
    assert "left untouched" in capsys.readouterr().out
    assert repo_file.read_bytes() == original
    assert no_temp_files(env.repo)


def test_pull_new_file_without_sops_config_fails_closed(env, capsys):
    (env.repo / ".sops.yaml").unlink()
    write_defs(env, {"app/.env.secrets": "dotenv"})
    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(PLAIN)
    cfg = syncfiles.Config(make_args(execute=True))
    cfg.cmd_pull()
    assert cfg.errors
    assert not (env.repo / "app/.env.secrets").exists()
    assert no_temp_files(env.repo)


def test_pull_normalisation_hint(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(b"A=dummy\n\nB=dummy\n")  # sops drops blank lines
    syncfiles.Config(make_args(execute=True)).cmd_pull()
    assert "run install once" in capsys.readouterr().out


# --- diff / check / config --------------------------------------------------

def test_diff_refuses_without_show_secrets(env, capfd):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    (env.dest / "app").mkdir()
    (env.dest / "app/.env.secrets").write_bytes(b"API_KEY=dummy-other\n")

    syncfiles.Config(make_args()).cmd_diff()
    out = capfd.readouterr().out
    assert "--show-secrets" in out
    assert "dummy" not in out

    syncfiles.Config(make_args(show_secrets=True)).cmd_diff()
    out = capfd.readouterr().out
    assert "dummy-one" in out
    assert not [p for p in pathlib.Path(syncfiles.tempfile.gettempdir()).glob("syncfiles-*-.env.secrets")]


def test_check_command(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv", "app/plain.json": "json", "app/missing.json": "json"})
    sops_encrypt(env, PLAIN, "app/.env.secrets")
    cfg = syncfiles.Config(make_args())
    cfg.cmd_check()
    assert not cfg.errors

    (env.repo / "app/plain.json").write_bytes(b'{"a": "dummy"}')
    cfg = syncfiles.Config(make_args())
    cfg.cmd_check()
    out = capsys.readouterr().out
    assert cfg.errors
    assert "X app/plain.json" in out
    assert "dummy" not in out


def test_invalid_type_exits(env):
    write_defs(env, {"app/x": "toml"})
    with pytest.raises(SystemExit) as excinfo:
        syncfiles.Config(make_args())
    assert excinfo.value.code == 1


def test_missing_sops_exits(env, monkeypatch):
    write_defs(env, {"app/x": "dotenv"})
    real_which = shutil.which
    monkeypatch.setattr(syncfiles.shutil, "which", lambda name: None if name == "sops" else real_which(name))
    with pytest.raises(SystemExit) as excinfo:
        syncfiles.Config(make_args())
    assert excinfo.value.code == 1


def test_sops_yaml_is_not_synced(env, capsys):
    write_defs(env, {})
    _, out = status_lines(capsys, all=True)
    assert ".sops.yaml" not in out


def test_unencrypted_pairs_unchanged(env, capsys):
    write_defs(env, {"app/.env.secrets": "dotenv"})
    (env.repo / "app/config.txt").write_text("plain")
    syncfiles.Config(make_args(execute=True)).cmd_install()
    out = capsys.readouterr().out
    assert "cp {} {}".format(env.repo / "app/config.txt", env.dest / "app/config.txt") in out
    assert (env.dest / "app/config.txt").read_text() == "plain"
