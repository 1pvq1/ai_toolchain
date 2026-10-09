"""Refuse live writes while an application rooted at a selected install runs."""

from pathlib import Path
import os
from typing import Callable, Iterable

import psutil

from ai_toolchain.storage.models import StorageError


class PsutilProcessGuard:
    def __init__(self, process_iter: Callable[..., Iterable] = psutil.process_iter, current_pid: Callable[[], int] = os.getpid) -> None:
        self.process_iter, self.current_pid = process_iter, current_pid

    def assert_stopped(self, roots: list[Path]) -> None:
        normalized = [os.path.normcase(str((root.parent if root.name.casefold() == "comfyui" else root).resolve(strict=False))).casefold() for root in roots]
        for process in self.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                details = process.info
                if details["pid"] == self.current_pid():
                    continue
                name = (details.get("name") or "").casefold()
                if not (name.startswith("python") or name in {"comfyui.exe", "webui.exe"}):
                    continue
                args = " ".join(details.get("cmdline") or [])
                cwd = details.get("cwd") or ""
                haystack = (args + " " + cwd).casefold()
                if any(root in haystack for root in normalized):
                    raise StorageError(f"Application appears to be running (PID {details['pid']}): {details.get('name')}")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
