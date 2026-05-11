import json
import stat

from satrepo.cli import main
from satrepo.config import read_config
from satrepo.manifest import read_manifest
from satrepo.paths import repo_paths


def test_init_creates_local_and_static_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"

    assert main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)]) == 0

    paths = repo_paths(root)
    config = read_config(paths.config)
    manifest = read_manifest(paths.site_manifest)

    assert config.handle == "alice.example"
    assert config.did.startswith("did:plc:")
    assert config.pds_url == "https://shim.example"
    assert config.key_dir == tmp_path / "config-home" / "satproto" / config.did
    assert not config.plc_registered

    assert (paths.worktree / "app.bsky.actor.profile").is_dir()
    assert (paths.worktree / "app.bsky.feed.post").is_dir()
    assert (paths.state / "refs" / "did").read_text(encoding="utf-8").strip() == config.did
    assert (paths.site / ".well-known" / "atproto-did").read_text(encoding="utf-8").strip() == config.did
    assert json.loads((paths.state / "did.json").read_text(encoding="utf-8"))["id"] == config.did

    assert manifest == read_manifest(paths.local_manifest)
    assert manifest["did"] == config.did
    assert manifest["events"] == []
    assert manifest["lastSeq"] == 0

    signing_key = config.key_dir / "signing.key"
    rotation_key = config.key_dir / "rotation.key"
    assert signing_key.exists()
    assert rotation_key.exists()
    assert stat.S_IMODE(signing_key.stat().st_mode) == 0o600
    assert stat.S_IMODE(rotation_key.stat().st_mode) == 0o600
    assert not (root / "site" / "signing.key").exists()


def test_init_refuses_existing_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"

    assert main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)]) == 0

    try:
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("second init should fail")


def test_status_reports_initialized_repo(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"

    assert main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)]) == 0
    assert main(["status", "--root", str(root)]) == 0

    out = capsys.readouterr().out
    assert "handle: alice.example" in out
    assert "records: 0" in out
