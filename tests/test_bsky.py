import json

from satrepo.cli import main
from satrepo.rkeys import is_valid_tid


def test_bsky_post_creates_tid_worktree_record(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )
    capsys.readouterr()

    assert (
        main(
            [
                "bsky",
                "post",
                "hello from the cli",
                "--created-at",
                "2026-05-11T22:00:00Z",
                "--root",
                str(root),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out

    post_files = sorted((root / "worktree" / "app.bsky.feed.post").glob("*.json"))
    assert len(post_files) == 1
    rkey = post_files[0].stem
    assert is_valid_tid(rkey)
    assert f"created app.bsky.feed.post/{rkey}" in out

    record = json.loads(post_files[0].read_text(encoding="utf-8"))
    assert record == {
        "$type": "app.bsky.feed.post",
        "text": "hello from the cli",
        "createdAt": "2026-05-11T22:00:00Z",
    }

    assert main(["status", "--root", str(root)]) == 0
    dirty = capsys.readouterr().out
    assert f"create app.bsky.feed.post/{rkey}" in dirty

    assert main(["commit", "--root", str(root)]) == 0
    capsys.readouterr()

    assert main(["log", "--root", str(root), "--limit", "1"]) == 0
    log_out = capsys.readouterr().out
    assert f"create app.bsky.feed.post/{rkey}" in log_out


def test_bsky_post_rejects_empty_text(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )

    try:
        main(["bsky", "post", "", "--root", str(root)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("empty post text should fail")
