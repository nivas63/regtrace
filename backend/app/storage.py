"""Content-addressed file storage.

Prototype implementation: local filesystem (a Docker volume). The interface is the
minimal S3-shaped surface so a MinIO/S3 adapter can replace it without call-site
changes.
"""

from __future__ import annotations

import asyncio
from pathlib import Path


class FileStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        # Keys are sha256 hex + extension; reject anything else (no path traversal).
        if not key.replace(".", "").isalnum() or ".." in key:
            raise ValueError(f"Invalid storage key: {key!r}")
        return self._root / key[:2] / key

    async def put(self, key: str, data: bytes) -> str:
        path = self._path(key)

        def _write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():  # immutable: identical content already stored
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(data)
                tmp.replace(path)

        await asyncio.to_thread(_write)
        return key

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)
