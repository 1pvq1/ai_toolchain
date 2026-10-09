"""Byte progress on a terminal; concise stage summaries in redirected output."""

from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TaskProgressColumn, TextColumn, TransferSpeedColumn


class RichProgressSink:
    def __init__(self, enabled: bool = True, console: Console | None = None) -> None:
        self.console = console or Console(stderr=True)
        self.enabled = enabled
        self.active: Progress | None = None
        self.task = None
        self.name = ""
        self.total = 0
        self.current = 0

    def stage(self, name: str, total_bytes: int) -> None:
        self.done()
        self.name, self.total, self.current = name, total_bytes, 0
        if self.enabled and self.console.is_terminal and total_bytes > 0:
            self.active = Progress(TextColumn("{task.description}"), BarColumn(), TaskProgressColumn(), DownloadColumn(), TransferSpeedColumn(), console=self.console)
            self.active.start()
            self.task = self.active.add_task(name, total=total_bytes)

    def advance(self, amount: int) -> None:
        self.current += amount
        if self.active is not None:
            self.active.advance(self.task, amount)

    def done(self) -> None:
        if self.active is not None:
            self.active.stop()
            self.active = None
        elif self.name and self.enabled and self.total > 0:
            self.console.print(f"{self.name}: {self.current:,} / {self.total:,} bytes", highlight=False)
        self.name = ""

    def message(self, value: str) -> None:
        self.console.print(value, highlight=False)


class NullProgressSink:
    def stage(self, name: str, total_bytes: int) -> None:
        pass

    def advance(self, amount: int) -> None:
        pass

    def done(self) -> None:
        pass

    def message(self, value: str) -> None:
        pass
