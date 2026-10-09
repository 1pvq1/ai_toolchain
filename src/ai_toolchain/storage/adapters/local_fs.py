"""Local filesystem operations with no-clobber writes and link-aware scans."""

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Callable

@dataclass
class LocalTree:
    files: list[Path]
    directories: list[Path]
    links: list[Path]


class LocalFileSystem:
    def exists(self, path: Path) -> bool:
        return os.path.lexists(path)

    def is_dir(self, path: Path) -> bool:
        return path.is_dir()

    def is_file(self, path: Path) -> bool:
        return path.is_file()

    def is_link(self, path: Path) -> bool:
        if path.is_symlink():
            return True
        try:
            return bool(path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
        except (AttributeError, OSError):
            return False

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def same_target(self, path: Path, target: Path) -> bool:
        return path.is_symlink() and os.path.normcase(str(path.resolve(strict=False))) == os.path.normcase(str(target.resolve(strict=False)))

    def readlink(self, path: Path) -> str:
        return os.readlink(path)

    def scan(self, path: Path) -> LocalTree:
        files: list[Path] = []
        directories: list[Path] = []
        links: list[Path] = []
        for base, names, filenames in os.walk(path, followlinks=False):
            root = Path(base)
            for name in list(names):
                item = root / name
                if self.is_link(item):
                    names.remove(name)
                    links.append(item)
                else:
                    directories.append(item)
            for name in filenames:
                item = root / name
                if self.is_link(item):
                    links.append(item)
                elif item.is_file():
                    files.append(item)
                else:
                    raise OSError(f"Unsupported filesystem item: {item}")
        return LocalTree(files, directories, links)

    def size(self, path: Path) -> int:
        return path.stat().st_size

    def same_volume(self, first: Path, second: Path) -> bool:
        return first.stat().st_dev == second.stat().st_dev

    def free_bytes(self, path: Path) -> int:
        return shutil.disk_usage(path).free

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def rename(self, source: Path, target: Path) -> None:
        if self.exists(target):
            raise FileExistsError(target)
        source.rename(target)

    def hardlink(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.link(source, target)

    def symlink(self, source: Path, target: Path) -> None:
        os.symlink(str(target), str(source), target_is_directory=True)

    def unlink(self, path: Path) -> None:
        path.unlink()

    def rmdir(self, path: Path) -> None:
        path.rmdir()

    def copy_new(self, source: Path, target: Path, on_bytes: Callable[[int], None]) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.exists(target):
            raise FileExistsError(target)
        handle, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".copy", dir=target.parent)
        try:
            with source.open("rb") as read, os.fdopen(handle, "wb") as write:
                for block in iter(lambda: read.read(8 * 1024 * 1024), b""):
                    write.write(block)
                    on_bytes(len(block))
                write.flush()
                os.fsync(write.fileno())
            os.link(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def read_bytes(self, path: Path) -> bytes:
        return path.read_bytes()

    def write_atomic(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def hide(self, path: Path) -> None:
        if os.name == "nt":
            import ctypes

            hidden = stat.FILE_ATTRIBUTE_HIDDEN
            kernel = ctypes.windll.kernel32
            attrs = kernel.GetFileAttributesW(str(path))
            if attrs != 0xFFFFFFFF and not attrs & hidden:
                if not kernel.SetFileAttributesW(str(path), attrs | hidden):
                    raise OSError(f"Cannot hide warehouse state: {path}")
