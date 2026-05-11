import json

from satrepo.cli import main

POST_TID = "3jzfcijpj2z2a"


def test_status_reports_dirty_worktree(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )
    capsys.readouterr()

    assert main(["status", "--root", str(root)]) == 0
    clean = capsys.readouterr().out
    assert "working tree clean" in clean

    post_dir = root / "worktree" / "app.bsky.feed.post"
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / f"{POST_TID}.json").write_text(
        json.dumps(
            {
                "$type": "app.bsky.feed.post",
                "text": "hello",
                "createdAt": "2026-05-11T21:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    assert main(["status", "--root", str(root)]) == 0
    dirty = capsys.readouterr().out
    assert "changes not committed:" in dirty
    assert f"create app.bsky.feed.post/{POST_TID}" in dirty


def test_commit_alias_and_log_show_commit_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )

    post_dir = root / "worktree" / "app.bsky.feed.post"
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / f"{POST_TID}.json").write_text(
        json.dumps(
            {
                "$type": "app.bsky.feed.post",
                "text": "hello",
                "createdAt": "2026-05-11T21:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    assert main(["commit", "--root", str(root)]) == 0
    commit_out = capsys.readouterr().out
    assert "committed did:plc:" in commit_out
    assert "writes: 1" in commit_out

    assert main(["log", "--root", str(root), "--limit", "1"]) == 0
    log_out = capsys.readouterr().out
    assert "commit " in log_out
    assert "seq: 5" in log_out
    assert "ops: 1" in log_out
    assert f"create app.bsky.feed.post/{POST_TID}" in log_out


def test_publish_command_is_not_public(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    try:
        main(["publish", "--root", str(tmp_path / "repo")])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("publish command should not be public")
