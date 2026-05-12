import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from satrepo.cli import main
from satrepo.config import read_config
from satrepo.manifest import read_manifest
from satrepo.paths import repo_paths
from satrepo.verify import verify_repo

POST_TID = "3jzfcijpj2z2a"


def test_plc_update_rewrites_unregistered_repo_and_republishes(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://old.example", "--root", str(root)])
        == 0
    )

    post_dir = root / "worktree" / "app.bsky.feed.post"
    post_dir.mkdir(parents=True, exist_ok=True)
    (post_dir / f"{POST_TID}.json").write_text(
        json.dumps(
            {
                "$type": "app.bsky.feed.post",
                "text": "hello after plc repair",
                "createdAt": "2026-05-11T20:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    assert main(["commit", "--root", str(root)]) == 0

    paths = repo_paths(root)
    old_config = read_config(paths.config)
    assert main(["plc", "update", "--root", str(root), "--pds-url", "https://shim.example/"]) == 0

    config = read_config(paths.config)
    manifest = read_manifest(paths.site_manifest)
    did_doc = json.loads((paths.state / "did.json").read_text(encoding="utf-8"))
    site_did_doc = json.loads((paths.site / "did.json").read_text(encoding="utf-8"))
    verification = verify_repo(root)

    assert config.did != old_config.did
    assert config.pds_url == "https://shim.example"
    assert config.key_dir == tmp_path / "config-home" / "satrepo" / config.did
    assert did_doc["id"] == config.did
    assert did_doc["service"][0]["serviceEndpoint"] == "https://shim.example"
    assert site_did_doc == did_doc
    assert manifest["did"] == config.did
    assert manifest["lastSeq"] == 5
    assert verification.ok
    assert verification.warnings == []
    assert verification.record_count == 1

    assert main(["plc", "update", "--root", str(root), "--pds-url", "https://shim.example"]) == 0
    assert read_config(paths.config).did == config.did


def test_plc_submit_posts_genesis_operation(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))
    root = tmp_path / "repo"
    assert (
        main(["init", "alice.example", "--pds-url", "https://shim.example", "--root", str(root)])
        == 0
    )
    capsys.readouterr()
    paths = repo_paths(root)
    did_doc = read_manifest(paths.state / "did.json")
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if not received:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b"DID not registered")
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(did_doc).encode("utf-8"))

        def do_POST(self):  # noqa: N802
            length = int(self.headers["Content-Length"])
            received["path"] = self.path
            received["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
            self.send_response(200)
            self.end_headers()

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert main(["plc", "submit", "--root", str(root), "--directory", url]) == 0
    finally:
        server.shutdown()
        thread.join(timeout=2)

    out = capsys.readouterr().out
    config = read_config(paths.config)
    assert "submitted local did:plc genesis operation" in out
    assert config.plc_registered is True
    assert received["path"] == f"/{config.did}"
    assert received["body"]["type"] == "plc_operation"
    assert "did" not in received["body"]
