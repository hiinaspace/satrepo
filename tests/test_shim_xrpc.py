import asyncio
import json

from aiohttp.test_utils import TestClient, TestServer
from carbox.car import read_car

from satrepo.cli import main
from satrepo.manifest import read_manifest
from satrepo.paths import repo_paths
from satrepo_shim.server import CAR_MIME_TYPE, create_app


def test_shim_serves_readonly_sync_xrpcs(tmp_path, monkeypatch):
    root, rkey = _create_committed_repo(tmp_path, monkeypatch)
    paths = repo_paths(root)
    manifest = read_manifest(paths.site_manifest)

    async def scenario() -> None:
        client = TestClient(TestServer(create_app(origin=str(paths.site))))
        await client.start_server()
        try:
            latest = await _get_json(
                client,
                "/xrpc/com.atproto.sync.getLatestCommit",
                did=manifest["did"],
            )
            assert latest == manifest["head"]

            status = await _get_json(
                client,
                "/xrpc/com.atproto.sync.getRepoStatus",
                did=manifest["did"],
            )
            assert status == {
                "did": manifest["did"],
                "active": True,
                "rev": manifest["head"]["rev"],
            }

            listed = await _get_json(client, "/xrpc/com.atproto.sync.listRepos")
            assert listed == {
                "repos": [
                    {
                        "did": manifest["did"],
                        "head": manifest["head"]["cid"],
                        "rev": manifest["head"]["rev"],
                        "active": True,
                    }
                ]
            }

            resolved = await _get_json(
                client,
                "/xrpc/com.atproto.identity.resolveHandle",
                handle=manifest["handle"],
            )
            assert resolved == {"did": manifest["did"]}

            described = await _get_json(
                client,
                "/xrpc/com.atproto.repo.describeRepo",
                repo=manifest["did"],
            )
            assert described["did"] == manifest["did"]
            assert described["handle"] == manifest["handle"]
            assert described["didDoc"]["id"] == manifest["did"]
            assert described["collections"] == ["app.bsky.feed.post"]
            assert described["handleIsCorrect"] is True

            health = await _get_json(client, "/xrpc/_health")
            assert health["ok"] is True
            assert health["did"] == manifest["did"]
            assert health["head"] == manifest["head"]["cid"]

            repo_response = await client.get(
                "/xrpc/com.atproto.sync.getRepo",
                params={"did": manifest["did"]},
            )
            assert repo_response.status == 200
            assert repo_response.content_type == CAR_MIME_TYPE
            repo_roots, repo_blocks = read_car(await repo_response.read())
            assert str(repo_roots[0]) == manifest["head"]["cid"]
            assert repo_blocks

            record_response = await client.get(
                "/xrpc/com.atproto.sync.getRecord",
                params={
                    "did": manifest["did"],
                    "collection": "app.bsky.feed.post",
                    "rkey": rkey,
                },
            )
            assert record_response.status == 200
            assert record_response.content_type == CAR_MIME_TYPE
            _, record_blocks = read_car(await record_response.read())
            records = [
                block.decoded
                for block in record_blocks
                if isinstance(block.decoded, dict)
                and block.decoded.get("$type") == "app.bsky.feed.post"
            ]
            assert records == [
                {
                    "$type": "app.bsky.feed.post",
                    "createdAt": "2026-05-11T23:00:00Z",
                    "text": "hello from the shim test",
                }
            ]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_shim_returns_xrpc_error_for_unknown_repo(tmp_path, monkeypatch):
    root, _ = _create_committed_repo(tmp_path, monkeypatch)
    paths = repo_paths(root)

    async def scenario() -> None:
        client = TestClient(TestServer(create_app(origin=str(paths.site))))
        await client.start_server()
        try:
            response = await client.get(
                "/xrpc/com.atproto.sync.getLatestCommit",
                params={"did": "did:plc:unknown"},
            )
            assert response.status == 400
            assert await response.json() == {
                "error": "RepoNotFound",
                "message": "Could not find repo for DID: did:plc:unknown",
            }
        finally:
            await client.close()

    asyncio.run(scenario())


async def _get_json(client: TestClient, path: str, **params):
    response = await client.get(path, params=params)
    assert response.status == 200
    return await response.json()


def _create_committed_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )
    assert (
        main(
            [
                "bsky",
                "post",
                "hello from the shim test",
                "--created-at",
                "2026-05-11T23:00:00Z",
                "--root",
                str(root),
            ]
        )
        == 0
    )
    post_files = sorted((root / "worktree" / "app.bsky.feed.post").glob("*.json"))
    assert len(post_files) == 1
    rkey = post_files[0].stem
    assert (
        json.loads(post_files[0].read_text(encoding="utf-8"))["text"] == "hello from the shim test"
    )
    assert main(["commit", "--root", str(root)]) == 0
    return root, rkey
