import json

from satrepo.cli import main
from satrepo.config import read_config
from satrepo.paths import repo_paths
from satrepo.rkeys import is_valid_tid
from satrepo.verify import verify_repo


def test_standard_site_helpers_create_publication_and_document(tmp_path, monkeypatch, capsys):
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
                "standard",
                "publication",
                "Alice Notes",
                "--url",
                "https://alice.example",
                "--description",
                "Small notes from Alice",
                "--root",
                str(root),
            ]
        )
        == 0
    )
    out = capsys.readouterr().out
    publication_files = sorted((root / "worktree" / "site.standard.publication").glob("*.json"))
    assert len(publication_files) == 1
    publication_rkey = publication_files[0].stem
    assert is_valid_tid(publication_rkey)
    assert f"created site.standard.publication/{publication_rkey}" in out

    publication = json.loads(publication_files[0].read_text(encoding="utf-8"))
    assert publication == {
        "$type": "site.standard.publication",
        "description": "Small notes from Alice",
        "name": "Alice Notes",
        "url": "https://alice.example",
    }

    config = read_config(repo_paths(root).config)
    assert (root / "site" / ".well-known" / "site.standard.publication").read_text(
        encoding="utf-8"
    ).strip() == (f"at://{config.did}/site.standard.publication/{publication_rkey}")

    assert (
        main(
            [
                "standard",
                "document",
                "Hello Standard.site",
                "# Hello Standard.site\n\nThis came from satrepo.",
                "--path",
                "/hello-standard-site",
                "--tag",
                "test",
                "--published-at",
                "2026-05-12T00:00:00Z",
                "--root",
                str(root),
            ]
        )
        == 0
    )
    document_files = sorted((root / "worktree" / "site.standard.document").glob("*.json"))
    assert len(document_files) == 1
    document_rkey = document_files[0].stem
    assert is_valid_tid(document_rkey)
    document = json.loads(document_files[0].read_text(encoding="utf-8"))
    assert document == {
        "$type": "site.standard.document",
        "site": f"at://{config.did}/site.standard.publication/{publication_rkey}",
        "title": "Hello Standard.site",
        "path": "/hello-standard-site",
        "publishedAt": "2026-05-12T00:00:00Z",
        "textContent": "# Hello Standard.site\n\nThis came from satrepo.",
        "content": {
            "$type": "at.markpub.markdown",
            "text": "# Hello Standard.site\n\nThis came from satrepo.",
            "flavor": "GFM",
        },
        "tags": ["test"],
    }

    assert main(["commit", "--root", str(root)]) == 0
    verification = verify_repo(root)
    assert verification.ok
    assert verification.record_count == 2
