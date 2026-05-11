"""Small JSON I/O helpers."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
from typing import Any


def read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def write_json_atomic(path: Path, data: Any) -> None:
    """Write JSON with a same-directory replace so readers never see partial data."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        json.dump(data, file, indent=2, sort_keys=True)
        file.write("\n")
        temp_name = file.name

    Path(temp_name).replace(path)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Write bytes with a same-directory replace."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as file:
        file.write(data)
        temp_name = file.name

    Path(temp_name).replace(path)
