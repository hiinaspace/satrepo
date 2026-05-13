import asyncio

from aiohttp.test_utils import TestClient, TestServer

from satrepo.cli import main
from satrepo.config import read_config
from satrepo.keys import read_private_key
from satrepo.paths import repo_paths
from satrepo.remote_signer import create_signer_app
from satrepo.verify import verify_repo


def test_commit_can_delegate_signing_to_remote_provider(tmp_path, monkeypatch):
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
                "hello through a remote signer",
                "--created-at",
                "2026-05-13T18:00:00Z",
                "--root",
                str(root),
            ]
        )
        == 0
    )

    paths = repo_paths(root)
    config = read_config(paths.config)
    signing_key_path = config.key_dir / "signing.key"
    signing_key = read_private_key(signing_key_path)

    async def scenario() -> None:
        client = TestClient(
            TestServer(
                create_signer_app(
                    signing_key=signing_key,
                    token="test-token",
                    allowed_did=config.did,
                )
            )
        )
        await client.start_server()
        try:
            signer_url = str(client.make_url("/")).rstrip("/")
            signing_key_path.rename(config.key_dir / "signing.key.offline")

            assert (
                await asyncio.to_thread(
                    main,
                    [
                        "commit",
                        "--root",
                        str(root),
                        "--signer-url",
                        signer_url,
                        "--signer-token",
                        "test-token",
                    ],
                )
                == 0
            )
        finally:
            await client.close()

    asyncio.run(scenario())

    result = verify_repo(root)
    assert result.ok, result.errors
