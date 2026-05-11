import json

from carbox.car import read_car

from satrepo.cli import main
from satrepo.manifest import read_manifest
from satrepo.paths import repo_paths


def test_publish_creates_static_repo_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)]) == 0

    post_dir = root / "worktree" / "app.bsky.feed.post"
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / "hello.json").write_text(
        json.dumps(
            {
                "$type": "app.bsky.feed.post",
                "text": "hello from static files",
                "createdAt": "2026-05-11T18:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    assert main(["publish", "--root", str(root)]) == 0

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
            "path": "app.bsky.feed.post/hello",
            "cid": commit_events[-1]["ops"][0]["cid"],
        }
    ]
    assert (paths.site / commit_events[-1]["blocks"]).exists()
    assert (paths.site / "repo" / "snapshot.car").exists()

    roots, blocks = read_car((paths.site / "repo" / "snapshot.car").read_bytes())
    assert str(roots[0]) == manifest["head"]["cid"]
    assert blocks


def test_publish_is_noop_without_record_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)]) == 0
    assert main(["publish", "--root", str(root)]) == 0

    paths = repo_paths(root)
    first = read_manifest(paths.site_manifest)
    assert main(["publish", "--root", str(root)]) == 0
    second = read_manifest(paths.site_manifest)

    assert second == first
