"""Streaming SHA-256 with injected byte progress."""

import hashlib
from pathlib import Path

from ai_toolchain.storage.ports import ProgressSink


class Sha256Hasher:
    def __init__(self, progress: ProgressSink) -> None:
        self.progress = progress

    def digest(self, path: Path) -> str:
        value = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
                value.update(block)
                self.progress.advance(len(block))
        return value.hexdigest()
