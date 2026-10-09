"""Shared, narrow storage invariants."""

from pathlib import Path

from ai_toolchain.storage.models import StorageError
from ai_toolchain.storage.ports import FileSystem, Hasher


def relative_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute() or not value or any(part in {"..", ""} for part in path.parts):
        raise StorageError(f"Unsafe relative path: {value}")
    return path


def object_path(root: Path, digest: str) -> Path:
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise StorageError(f"Invalid digest: {digest}")
    return root / "objects" / digest[:2] / digest


def verify_file(fs: FileSystem, hasher: Hasher, path: Path, digest: str, size: int | None = None) -> None:
    if fs.is_link(path) or not fs.is_file(path) or (size is not None and fs.size(path) != size) or hasher.digest(path) != digest:
        raise StorageError(f"File changed or missing: {path}")


def snapshot(fs: FileSystem, hasher: Hasher, root: Path, model: Path, digest: str, size: int) -> Path:
    obj = object_path(root, digest)
    verify_file(fs, hasher, model, digest, size)
    if fs.exists(obj):
        verify_file(fs, hasher, obj, digest, size)
    else:
        if not fs.same_volume(model, root):
            raise StorageError(f"Hardlink snapshot needs same volume: {model}")
        fs.hardlink(model, obj)
        verify_file(fs, hasher, obj, digest, size)
    return obj


def remove_empty_tree(fs: FileSystem, root: Path, directories: list[str]) -> None:
    for value in sorted(directories, key=lambda item: len(Path(item).parts), reverse=True):
        path = root / relative_path(value)
        if fs.exists(path):
            fs.rmdir(path)
    fs.rmdir(root)
