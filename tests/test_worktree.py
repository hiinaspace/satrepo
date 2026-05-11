import json

import pytest

from satrepo.errors import SatRepoError
from satrepo.worktree import scan_records


def test_scan_records_returns_collection_and_rkey(tmp_path):
    record_dir = tmp_path / "worktree" / "app.bsky.feed.post"
    record_dir.mkdir(parents=True)
    (record_dir / "hello.json").write_text(
        json.dumps({"$type": "app.bsky.feed.post", "text": "hello"}),
        encoding="utf-8",
    )
    (tmp_path / "worktree" / "blobs").mkdir()

    records = scan_records(tmp_path)

    assert len(records) == 1
    assert records[0].collection == "app.bsky.feed.post"
    assert records[0].rkey == "hello"
    assert records[0].repo_path == "app.bsky.feed.post/hello"
    assert records[0].record["text"] == "hello"


def test_scan_records_rejects_bad_collection_dir(tmp_path):
    bad_dir = tmp_path / "worktree" / "not-a-collection"
    bad_dir.mkdir(parents=True)

    with pytest.raises(SatRepoError):
        scan_records(tmp_path)
