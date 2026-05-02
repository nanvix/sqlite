# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for SQLite.

Usage:
    ./z setup     # Download Nanvix sysroot and dependencies
    ./z build     # Cross-compile libsqlite3.a and sqlite3.elf
    ./z test      # Run test suite (smoke + integration + functional)
    ./z release   # Package release tarball
    ./z clean     # Remove build artifacts
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nanvix_zutil import (  # type: ignore[import-not-found]
    CFG_SYSROOT,
    CFG_TOOLCHAIN,
    EXIT_MISSING_DEP,
    ZScript,
    log,
)

# Makefile variable names (build-system-specific).
_MAKE_VAR_CONFIG = "CONFIG_NANVIX"
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"
_MAKE_VAR_INSTALL_PREFIX = "INSTALL_PREFIX"

# SQLite embeds --prefix into the configure step.
# Use /sysroot so that release tarballs don't contain ephemeral runner paths.
_DEFAULT_INSTALL_PREFIX = "/sysroot"

IS_WINDOWS = sys.platform == "win32"


class SqliteBuild(ZScript):
    """Build script for nanvix/sqlite."""

    def _make_args(
        self, *targets: str, with_install_prefix: bool = True,
    ) -> list[str]:
        """Build the common make argument list."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        toolchain = self.config.get(CFG_TOOLCHAIN, "/opt/nanvix")
        sysroot_p = self.translate_path(Path(sysroot))
        toolchain_p = self.translate_path(Path(toolchain))

        args = [
            "make", "-f", "Makefile.nanvix",
            f"{_MAKE_VAR_CONFIG}=y",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend([
            f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
            f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
            f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
        ])

        if with_install_prefix:
            args.append(
                f"{_MAKE_VAR_INSTALL_PREFIX}={_DEFAULT_INSTALL_PREFIX}",
            )

        args.extend(targets)
        return args

    def setup(self) -> None:
        """Download the Nanvix sysroot and dependencies.

        After the base setup installs dependencies into the buildroot,
        merge buildroot libraries and headers into the sysroot so the
        existing Makefile.nanvix can find them at its expected paths.
        """
        super().setup()

        buildroot = self.nanvix_dir / "buildroot"
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot or not buildroot.is_dir():
            return

        sysroot_path = Path(sysroot)
        for subdir in ("lib", "include"):
            src = buildroot / subdir
            dst = sysroot_path / subdir
            if not src.is_dir():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                target = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                    log.info(
                        f"Merged directory {subdir}/{item.name}"
                        " into sysroot",
                    )
                elif not target.exists():
                    shutil.copy2(item, target)
                    log.info(
                        f"Merged {subdir}/{item.name} into sysroot",
                    )

    def build(self) -> None:
        """Cross-compile libsqlite3.a and sqlite3.elf for Nanvix."""
        self.run(*self._make_args("all"), cwd=self.repo_root)

    def test(self) -> None:
        """Run the test suite.

        On non-Windows, delegates to the Makefile
        (smoke + integration + functional).
        On Windows, runs sqlite3.elf via nanvixd.exe in standalone mode,
        piping SQL commands through stdin.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return
        targets = self.targets or ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def _resolve_windows_tools(self) -> tuple[Path, Path, Path]:
        """Resolve sysroot path, nanvixd, and mkramfs for Windows tests."""
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal(
                "nanvixd.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        if not mkramfs.is_file():
            log.fatal(
                "mkramfs.exe not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first.",
            )
        return sysroot_path, nanvixd, mkramfs

    def _find_test_binaries(self) -> list[Path]:
        """Find allowlisted ELF test binaries in the repo."""
        test_allowlist = {"sqlite3.elf"}
        test_binaries: list[Path] = []
        for candidate in [self.repo_root, self.repo_root / "build"]:
            if candidate.is_dir():
                elfs = sorted(candidate.glob("*.elf"))
                found = [
                    b for b in elfs if b.name in test_allowlist
                ]
                for b in found:
                    if b.name not in {x.name for x in test_binaries}:
                        test_binaries.append(b)

        if not test_binaries:
            expected = ", ".join(sorted(test_allowlist))
            log.fatal(
                f"No allowlisted test binaries found."
                f" Expected: {expected}.",
                code=EXIT_MISSING_DEP,
                hint=(
                    "Build the test binaries first"
                    " (for example, run `./z build`)"
                    " and then rerun `./z test`."
                ),
            )
        return test_binaries

    def _run_single_test(
        self,
        binary: Path,
        nanvixd: Path,
        mkramfs: Path,
        sysroot_path: Path,
    ) -> bool:
        """Run a single Windows test binary. Return True on success."""
        name = binary.stem
        log.info(f"RUN  {name}...")
        with tempfile.TemporaryDirectory(
            prefix=f"nanvix_{name}_", ignore_cleanup_errors=True,
        ) as tmpdir:
            tmpdir_path = Path(tmpdir)
            ramfs_dir = tmpdir_path / "ramfs"
            ramfs_dir.mkdir()
            (ramfs_dir / "tmp").mkdir(exist_ok=True)
            shutil.copy2(binary, ramfs_dir / binary.name)

            shared_sql = (
                self.repo_root / ".nanvix" / "functional_test.sql"
            )
            sql_file = ramfs_dir / "_sqlite_test.sql"
            shutil.copy2(shared_sql, sql_file)

            ramfs_img = tmpdir_path / f"rootfs_{name}.img"
            if not self._build_ramfs(mkramfs, ramfs_img, ramfs_dir, name):
                return False
            return self._execute_test(
                nanvixd, sysroot_path, ramfs_img, binary, sql_file, name,
            )

    def _build_ramfs(
        self, mkramfs: Path, ramfs_img: Path, ramfs_dir: Path, name: str,
    ) -> bool:
        """Build the ramfs image. Return True on success."""
        try:
            subprocess.run(  # noqa: S603
                [
                    str(mkramfs.resolve()),
                    "-o", str(ramfs_img),
                    str(ramfs_dir),
                ],
                check=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            log.error(
                f"FAIL {name} (mkramfs exit code {e.returncode})",
            )
            return False
        except subprocess.TimeoutExpired:
            log.error(f"FAIL {name} (mkramfs timeout)")
            return False
        return True

    def _execute_test(  # noqa: PLR0913
        self,
        nanvixd: Path,
        sysroot_path: Path,
        ramfs_img: Path,
        binary: Path,
        sql_file: Path,
        name: str,
    ) -> bool:
        """Execute a test binary via nanvixd. Return True on success."""
        try:
            sql_input = sql_file.read_bytes()
            bin_dir = str((sysroot_path / "bin").resolve())
            result = subprocess.run(  # noqa: S603
                [
                    str(nanvixd.resolve()),
                    "-bin-dir", bin_dir,
                    "-ramfs", str(ramfs_img),
                    "--", str(binary.resolve()),
                ],
                input=sql_input,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                log.error(
                    f"FAIL {name} (exit code {result.returncode})",
                )
                return False
            log.info(f"OK   {name}")
        except subprocess.TimeoutExpired:
            log.error(f"FAIL {name} (timeout)")
            return False
        return True

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe.

        Only standalone mode is tested on Windows; multi-process and
        single-process require linuxd, which is Linux-only.
        """
        if self.config.deployment_mode != "standalone":
            log.info(
                f"Skipping tests on Windows for mode"
                f" '{self.config.deployment_mode}' (requires linuxd).",
            )
            return

        sysroot_path, nanvixd, mkramfs = self._resolve_windows_tools()
        test_binaries = self._find_test_binaries()

        failed = [
            binary.stem
            for binary in test_binaries
            if not self._run_single_test(
                binary, nanvixd, mkramfs, sysroot_path,
            )
        ]

        if failed:
            msg = f"{len(failed)} test(s) failed: {' '.join(failed)}"
            raise RuntimeError(msg)
        log.info(
            f"\t\t*** All {len(test_binaries)} tests PASSED ***",
        )

    def release(self) -> None:
        """Package the SQLite release tarball and verify it."""
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(
            *self._make_args("verify-package"), cwd=self.repo_root,
        )

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run(
            "make", "-f", "Makefile.nanvix", "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    SqliteBuild.main()
