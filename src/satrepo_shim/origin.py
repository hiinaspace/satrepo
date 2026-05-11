"""Read generated satrepo static-origin files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class OriginError(ValueError):
    """Raised when the static origin cannot serve a requested artifact."""


@dataclass(frozen=True)
class StaticOrigin:
    location: str

    @classmethod
    def from_location(cls, location: str | Path) -> StaticOrigin:
        return cls(str(location))

    @property
    def is_http(self) -> bool:
        return urlparse(self.location).scheme in {"http", "https"}

    def read_json(self, path: str) -> dict:
        return json.loads(self.read_bytes(path).decode("utf-8"))

    def read_text(self, path: str) -> str:
        return self.read_bytes(path).decode("utf-8")

    def read_bytes(self, path: str) -> bytes:
        if self.is_http:
            url = urljoin(self.location.rstrip("/") + "/", path)
            try:
                request = Request(url, headers={"User-Agent": "satrepo-shim"})
                with urlopen(request, timeout=10) as response:
                    return response.read()
            except (HTTPError, URLError, TimeoutError) as exc:
                raise OriginError(f"could not read {url}: {exc}") from exc

        file_path = Path(self.location).expanduser().resolve() / path
        try:
            return file_path.read_bytes()
        except OSError as exc:
            raise OriginError(f"could not read {file_path}: {exc}") from exc
