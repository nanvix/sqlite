"""Type stub for nanvix_zutil (external, not shipped with this repo)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

CFG_SYSROOT: str
CFG_TOOLCHAIN: str
EXIT_MISSING_DEP: int


class _Config:
    machine: str
    deployment_mode: str
    memory_size: str

    def get(self, key: str, default: str = "") -> str: ...


class _Log:
    def fatal(
        self, msg: str, *, code: int = ..., hint: str = ...,
    ) -> NoReturn: ...
    def info(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


log: _Log


class ZScript:
    config: _Config
    repo_root: Path
    nanvix_dir: Path
    targets: list[str]

    def translate_path(self, path: Path) -> str: ...
    def run(self, *args: Any, cwd: Path | None = ...) -> None: ...
    def setup(self) -> None: ...

    @classmethod
    def main(cls) -> None: ...
