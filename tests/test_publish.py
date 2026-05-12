import json
import stat

from carbox.car import read_car

from satrepo.cli import main
from satrepo.manifest import read_manifest
from satrepo.paths import repo_paths
from satrepo.verify import verify_repo

POST_TID = "3jzfcijpj2z2a"


def test_publish_creates_static_repo_artifacts(tmp_path, monkeypatch):
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
                "text": "hello from static files",
                "createdAt": "2026-05-11T18:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    assert main(["commit", "--root", str(root)]) == 0

    paths = repo_paths(root)
    manifest = read_manifest(paths.site_manifest)
    assert manifest["head"]["cid"]
    assert manifest["head"]["rev"]
    assert manifest["lastSeq"] == 5
    assert [event["type"] for event in manifest["events"]] == [
        "#commit",
        "#identity",
        "#account",
        "#sync",
        "#commit",
    ]

    commit_events = [
        read_manifest(paths.site / event["path"])
        for event in manifest["events"]
        if event["type"] == "#commit"
    ]
    assert commit_events[-1]["ops"] == [
        {
            "action": "create",
            "path": f"app.bsky.feed.post/{POST_TID}",
            "cid": commit_events[-1]["ops"][0]["cid"],
        }
    ]
    assert (paths.site / commit_events[-1]["blocks"]).exists()
    assert (paths.site / "repo" / "snapshot.car").exists()
    for path in paths.site.rglob("*"):
        if path.is_file():
            assert path.stat().st_mode & stat.S_IROTH, path
        elif path.is_dir():
            assert path.stat().st_mode & stat.S_IXOTH, path

    roots, blocks = read_car((paths.site / "repo" / "snapshot.car").read_bytes())
    assert str(roots[0]) == manifest["head"]["cid"]
    assert blocks

    verification = verify_repo(root)
    assert verification.ok
    assert verification.record_count == 1
    assert verification.event_count == 5
    assert verification.snapshot_block_count == len(blocks)


def test_publish_commits_update_and_delete_ops(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )

    post_path = root / "worktree" / "app.bsky.feed.post" / f"{POST_TID}.json"
    post_path.write_text(
        json.dumps(
            {
                "$type": "app.bsky.feed.post",
                "text": "first version",
                "createdAt": "2026-05-11T18:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert main(["commit", "--root", str(root)]) == 0

    post_path.write_text(
        json.dumps(
            {
                "$type": "app.bsky.feed.post",
                "text": "edited version",
                "createdAt": "2026-05-11T18:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert main(["commit", "--root", str(root)]) == 0
    update_event = _latest_commit_event(repo_paths(root))
    assert update_event["ops"][0]["action"] == "update"
    assert update_event["ops"][0]["path"] == f"app.bsky.feed.post/{POST_TID}"
    assert update_event["ops"][0]["cid"]
    assert update_event["ops"][0]["prev"]

    post_path.unlink()
    assert main(["commit", "--root", str(root)]) == 0
    delete_event = _latest_commit_event(repo_paths(root))
    assert delete_event["ops"][0]["action"] == "delete"
    assert delete_event["ops"][0]["path"] == f"app.bsky.feed.post/{POST_TID}"
    assert delete_event["ops"][0]["prev"]

    verification = verify_repo(root)
    assert verification.ok
    assert verification.record_count == 0


def test_publish_is_noop_without_record_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )
    assert main(["commit", "--root", str(root)]) == 0

    paths = repo_paths(root)
    first = read_manifest(paths.site_manifest)
    assert main(["commit", "--root", str(root)]) == 0
    second = read_manifest(paths.site_manifest)

    assert second == first


def test_verify_warns_on_non_https_pds_url(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "http://shim.example", "--root", str(root)])
        == 0
    )
    assert main(["commit", "--root", str(root)]) == 0

    verification = verify_repo(root)

    assert verification.ok
    assert verification.warnings == ["pdsUrl is not an absolute https URL"]


def test_verify_reports_invalid_committed_bsky_rkey(tmp_path, monkeypatch):
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
                "text": "hello from static files",
                "createdAt": "2026-05-11T18:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert main(["commit", "--root", str(root)]) == 0

    paths = repo_paths(root)
    event_path = paths.site / "repo" / "events" / "0000000000000005.json"
    event = read_manifest(event_path)
    event["ops"][0]["path"] = "app.bsky.feed.post/not-a-tid"
    event_path.write_text(json.dumps(event), encoding="utf-8")

    verification = verify_repo(root)

    assert not verification.ok
    assert any("requires a TID rkey" in error for error in verification.errors)
    assert main(["verify", "--root", str(root)]) == 1


def _latest_commit_event(paths):
    manifest = read_manifest(paths.site_manifest)
    latest = next(event for event in reversed(manifest["events"]) if event["type"] == "#commit")
    return read_manifest(paths.site / latest["path"])
