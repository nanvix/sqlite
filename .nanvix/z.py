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
import tempfile
from pathlib import Path

import subprocess
import sys

from nanvix_zutil import CFG_SYSROOT, CFG_TOOLCHAIN, EXIT_MISSING_DEP, ZScript, log

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

    def _make_args(self, *targets: str, with_install_prefix: bool = True) -> list[str]:
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
            args.append(f"{_MAKE_VAR_INSTALL_PREFIX}={_DEFAULT_INSTALL_PREFIX}")

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
                    log.info(f"Merged directory {subdir}/{item.name} into sysroot")
                elif not target.exists():
                    shutil.copy2(item, target)
                    log.info(f"Merged {subdir}/{item.name} into sysroot")

    def build(self) -> None:
        """Cross-compile libsqlite3.a and sqlite3.elf for Nanvix."""
        self.run(*self._make_args("all"), cwd=self.repo_root)

    def test(self) -> None:
        """Run the test suite.

        On non-Windows, delegates to the Makefile (smoke + integration + functional).
        On Windows, runs sqlite3.elf via nanvixd.exe in standalone mode,
        piping SQL commands through stdin.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe.

        Only standalone mode is tested on Windows; multi-process and
        single-process require linuxd, which is Linux-only. The sqlite3.elf
        binary is discovered in the repository root, where the Makefile emits
        ELF outputs, rather than under ``build/``.
        """
        if self.config.deployment_mode != "standalone":
            print(f"Skipping tests on Windows for mode '{self.config.deployment_mode}' (requires linuxd).")
            return

        # --- standalone: full functional test via nanvixd.exe ---
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(f"{CFG_SYSROOT} is not set.", code=EXIT_MISSING_DEP, hint="Run `./z setup` first.")
        sysroot_path = Path(sysroot)
        nanvixd = sysroot_path / "bin" / "nanvixd.exe"
        mkramfs = sysroot_path / "bin" / "mkramfs.exe"
        if not nanvixd.is_file():
            log.fatal("nanvixd.exe not found.", code=EXIT_MISSING_DEP, hint="Run `./z setup` first.")
        if not mkramfs.is_file():
            log.fatal("mkramfs.exe not found.", code=EXIT_MISSING_DEP, hint="Run `./z setup` first.")

        # The Makefile outputs ELFs directly to the repository root, not to a
        # build/ subdirectory.  Search the repo root first; fall back to build/
        # for forward-compatibility in case a future Makefile change moves them.
        test_allowlist = {"sqlite3.elf"}
        test_binaries: list[Path] = []
        for candidate in [self.repo_root, self.repo_root / "build"]:
            if candidate.is_dir():
                elfs = sorted(candidate.glob("*.elf"))
                found = [b for b in elfs if b.name in test_allowlist]
                for b in found:
                    if b.name not in {x.name for x in test_binaries}:
                        test_binaries.append(b)

        if not test_binaries:
            expected = ", ".join(sorted(test_allowlist))
            log.fatal(
                f"No allowlisted test binaries found. Expected: {expected}.",
                code=EXIT_MISSING_DEP,
                hint="Build the test binaries first (for example, run `./z build`) and then rerun `./z test`.",
            )

        failed = []
        for binary in test_binaries:
            name = binary.stem
            print(f"RUN  {name}...")
            with tempfile.TemporaryDirectory(prefix=f"nanvix_{name}_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                shutil.copy2(binary, ramfs_dir / binary.name)

                # Copy the shared SQL test script (single source of truth
                # used by both Makefile.nanvix and this Windows test path).
                shared_sql = self.repo_root / ".nanvix" / "functional_test.sql"
                sql_file = ramfs_dir / "_sqlite_test.sql"
                shutil.copy2(shared_sql, sql_file)

                # Write ramfs image alongside the ramfs source dir to avoid
                # self-inclusion while keeping artifacts scoped to this temp dir.
                ramfs_img = tmpdir_path / f"rootfs_{name}.img"
                try:
                    subprocess.run(
                        [str(mkramfs.resolve()), "-o", str(ramfs_img), str(ramfs_dir)],
                        check=True, timeout=60,
                    )
                except subprocess.CalledProcessError as e:
                    print(f"FAIL {name} (mkramfs exit code {e.returncode})")
                    failed.append(name)
                    continue
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name} (mkramfs timeout)")
                    failed.append(name)
                    continue
                try:
                    with sql_file.open("rb") as sql_stdin:
                        result = subprocess.run(
                            [str(nanvixd.resolve()), "-bin-dir", str((sysroot_path / "bin").resolve()),
                             "-ramfs", str(ramfs_img), "--", str(binary.resolve())],
                            stdin=sql_stdin, timeout=120,
                        )
                    if result.returncode != 0:
                        print(f"FAIL {name} (exit code {result.returncode})")
                        failed.append(name)
                    else:
                        print(f"OK   {name}")
                except subprocess.TimeoutExpired:
                    print(f"FAIL {name} (timeout)")
                    failed.append(name)

        if failed:
            msg = " ".join(failed)
            raise RuntimeError(f"{len(failed)} test(s) failed: {msg}")
        print(f"\t\t*** All {len(test_binaries)} tests PASSED ***")

    def release(self) -> None:
        """Package the SQLite release tarball and verify it."""
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run(
            "make", "-f", "Makefile.nanvix", "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    SqliteBuild.main()
