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

import dataclasses
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from nanvix_zutil import (
    CFG_SYSROOT,
    EXIT_MISSING_DEP,
    TOOLCHAIN_CONTAINER_PATH,
    ZScript,
    log,
    make_initrd,
    run,
)

IS_WINDOWS = sys.platform == "win32"

# Makefile variable names (build-system-specific).
_MAKE_VAR_HOME = "NANVIX_HOME"
_MAKE_VAR_TOOLCHAIN = "NANVIX_TOOLCHAIN"
_MAKE_VAR_PLATFORM = "PLATFORM"
_MAKE_VAR_PROCESS_MODE = "PROCESS_MODE"
_MAKE_VAR_MEMORY_SIZE = "MEMORY_SIZE"
_MAKE_VAR_INSTALL_PREFIX = "INSTALL_PREFIX"

# SQLite embeds --prefix into the configure step.
# Use /sysroot so that release tarballs don't contain ephemeral runner paths.
_DEFAULT_INSTALL_PREFIX = "/sysroot"


class SqliteBuild(ZScript):
    """Build script for nanvix/sqlite."""

    def _make_args(
        self,
        *targets: str,
        with_install_prefix: bool = True,
    ) -> list[str]:
        """Build the common make argument list.

        Path translation for ``NANVIX_HOME`` is applied when running
        under Docker (i.e. ``self.docker`` is set); otherwise the raw
        host path is used.
        """
        sysroot = self.config.get(CFG_SYSROOT, "")
        if not sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )
        toolchain_p = TOOLCHAIN_CONTAINER_PATH
        sysroot_p = (
            self.docker.translate_path(Path(sysroot)) if self.docker else Path(sysroot)
        )

        args = [
            "make",
            "-f",
            "Makefile.nanvix",
            f"{_MAKE_VAR_HOME}={sysroot_p}",
            f"{_MAKE_VAR_TOOLCHAIN}={toolchain_p}",
        ]

        args.extend(
            [
                f"{_MAKE_VAR_PLATFORM}={self.config.machine}",
                f"{_MAKE_VAR_PROCESS_MODE}={self.config.deployment_mode}",
                f"{_MAKE_VAR_MEMORY_SIZE}={self.config.memory_size}",
            ]
        )

        if with_install_prefix:
            args.append(
                f"{_MAKE_VAR_INSTALL_PREFIX}={_DEFAULT_INSTALL_PREFIX}",
            )

        args.extend(targets)
        return args

    def setup(self) -> bool:
        """Download the Nanvix sysroot and dependencies.

        After the base setup installs dependencies into the buildroot,
        merge buildroot libraries and headers into the sysroot so the
        existing Makefile.nanvix can find them at its expected paths.
        """
        failed = super().setup()
        # pyright: ignore[reportOptionalMemberAccess]
        assert self.buildroot is not None
        buildroot = self.buildroot.create().path
        sysroot = Path(self.config.get(CFG_SYSROOT, ""))
        for subdir in ("lib", "include"):
            src = buildroot / subdir
            dst = sysroot / subdir
            if not src.is_dir():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            for item in src.iterdir():
                target = dst / item.name
                if item.is_dir():
                    shutil.copytree(item, target, dirs_exist_ok=True)
                    log.info(
                        f"Merged directory {subdir}/{item.name} into sysroot",
                    )
                elif not target.exists():
                    shutil.copy2(item, target)
                    log.info(
                        f"Merged {subdir}/{item.name} into sysroot",
                    )
        return failed

    def build(self) -> None:
        """Cross-compile libsqlite3.a and sqlite3.elf for Nanvix.

        Linux: the host has a native ``cc``, so host-side tools (jimsh0,
        lemon, ...) are pre-built on the host and only the configure and
        cross-compile steps run inside Docker.

        Windows: the host has no ``cc``.  Because zutils' Windows mode
        uses tar-copy isolation between Docker invocations (artifacts
        vanish between ``docker run`` calls), the entire pipeline --
        installing native gcc, building jimsh0, running configure,
        building host tools, and the final cross-compile -- is bundled
        into a single Docker invocation; only the final artifacts are
        copied back to the host.
        """
        if IS_WINDOWS:
            self._build_windows()
            return
        self._prebuild_host_tools()
        run(*self._make_args("all"), cwd=self.repo_root, docker=self.docker)

    # ------------------------------------------------------------------
    # Windows: single-shot Docker build
    # ------------------------------------------------------------------

    # Build artifacts to copy back from the container to the host after
    # the Windows single-shot build completes.  These are everything the
    # downstream targets (test, package) consume.
    _WINDOWS_OUTPUT_FILES = [
        "libsqlite3.a",
        "sqlite3.h",
        "sqlite3.elf",
    ]

    def _build_windows(self) -> None:
        """Run the full build pipeline inside a single Docker invocation.

        The ``toolchain-gcc`` image is cross-only (no native ``cc``), so
        we apt-install ``gcc``/``make`` once per build before running the
        host-tool and cross-compile phases.
        """
        if self.docker is None:
            log.fatal(
                "Docker mode is not active.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup --with-docker IMAGE` first.",
            )

        # Add output_files so build_windows_run_cmd copies artifacts back
        # to the mounted workspace after the container exits.
        docker_cfg = dataclasses.replace(
            self.docker,
            output_files=list(self._WINDOWS_OUTPUT_FILES),
        )

        jimsh0_cflags = " ".join(shlex.quote(f) for f in self._JIMSH0_CFLAGS)
        configure_cmd = shlex.join(self._make_args("configure"))
        all_cmd = shlex.join(self._make_args("all"))

        # Single shell script: install native gcc, build jimsh0, configure,
        # build host tools using the autoconf-generated Makefile, then
        # cross-compile via Makefile.nanvix.
        script = (
            "set -e; "
            "if ! command -v cc >/dev/null 2>&1; then "
            "  apt-get update >/dev/null && "
            "  DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "    --no-install-recommends gcc libc6-dev make >/dev/null; "
            "fi; "
            "if [ ! -x ./jimsh0 ]; then "
            f"  cc -o jimsh0 {jimsh0_cflags} autosetup/jimsh0.c; "
            "fi; "
            f"{configure_cmd}; "
            "make lemon mksourceid mkkeywordhash srcck1 src-verify "
            "  B.cc=cc B.tclsh=./jimsh0 TOP=\"$PWD\"; "
            f"{all_cmd}"
        )

        log.info("Building SQLite inside Docker (Windows single-shot)...")
        run("sh", "-c", script, cwd=self.repo_root, docker=docker_cfg)

    # ------------------------------------------------------------------
    # Host-tool pre-build helpers (Docker-only)
    # ------------------------------------------------------------------

    _JIMSH0_CFLAGS = [
        "-DHAVE_REALPATH",
        "-DHAVE_DIRENT_H",
        "-DHAVE_SYS_TIME_H",
    ]

    def _prebuild_host_tools(self) -> None:
        """Build host-side tools needed by autosetup and the Makefile.

        Linux/CI path: the official ``toolchain-gcc`` Docker image is
        cross-only (ships ``i686-nanvix-gcc`` but no native ``cc``), so
        any phase that needs a host compiler must run on the runner
        outside the container.  Only Phase 2 (``./configure``) and the
        final cross-compile run inside Docker.

        Phase 1 -- jimsh0 (TCL bootstrap for ``./configure``) -- host cc.
        Phase 2 -- ``make configure`` inside Docker (generates Makefile).
        Phase 3 -- lemon, mkkeywordhash, mksourceid, srcck1, src-verify
                   compiled on the host using the generated Makefile.

        TODO(toolchain-gcc): if/when the image grows a native ``gcc``,
        Phases 1 and 3 can move back into Docker and this whole helper
        collapses to the cross-build call.
        """
        root = self.repo_root

        # Phase 1: build jimsh0 on the host.
        jimsh0 = root / "jimsh0"
        if not jimsh0.is_file():
            log.info("Pre-building jimsh0 on the host...")
            subprocess.run(  # noqa: S603
                [
                    "cc",
                    "-o",
                    str(jimsh0),
                    *self._JIMSH0_CFLAGS,
                    str(root / "autosetup" / "jimsh0.c"),
                ],
                check=True,
                cwd=root,
            )

        # Phase 2: run configure inside Docker.
        log.info("Running configure inside Docker...")
        run(*self._make_args("configure"), cwd=root, docker=self.docker)

        # Phase 3: build remaining host tools on the host.
        host_tools = [
            "lemon",
            "mksourceid",
            "mkkeywordhash",
            "srcck1",
            "src-verify",
        ]
        missing = [t for t in host_tools if not (root / t).is_file()]
        if missing:
            log.info(f"Pre-building host tools on the host: {missing}")
            subprocess.run(  # noqa: S603
                [
                    "make",
                    *missing,
                    "B.cc=cc",
                    "B.tclsh=./jimsh0",
                    f"TOP={root}",
                ],
                check=True,
                cwd=root,
            )

    def test(self) -> None:
        """Run the test suite.

        Smoke and integration tests are always delegated to the Makefile.
        The functional test in standalone mode is handled in Python via
        make_initrd so that initrd creation is shared across platforms.
        """
        if IS_WINDOWS:
            self._run_tests_windows()
            return

        if self.config.deployment_mode == "standalone":
            targets = self.targets if self.targets else []
            # Targets that require the Python functional path.
            _functional_targets = {"test", "test-functional"}
            needs_functional = not targets or bool(set(targets) & _functional_targets)
            # Delegate non-functional targets to the Makefile.
            make_targets = [t for t in targets if t not in _functional_targets]
            if not targets:
                make_targets = ["test-smoke", "test-integration"]
            elif needs_functional and not make_targets:
                # Ensure Makefile prerequisites run when only functional
                # targets are requested (build + smoke/integration).
                if "test" in targets:
                    make_targets = ["test-smoke", "test-integration"]
                else:
                    make_targets = ["test-integration"]
            if make_targets:
                run(*self._make_args(*make_targets), cwd=self.repo_root)
            if needs_functional:
                self._run_functional_standalone()
        else:
            targets = self.targets or ["test"]
            run(*self._make_args(*targets), cwd=self.repo_root)

    def _run_functional_standalone(self) -> None:
        """Run standalone functional tests using make_initrd.

        Creates an initrd bundling sqlite3.elf with system daemons via
        make_initrd, and a ramfs providing /tmp. SQL commands are piped
        through stdin via shell redirection.
        """
        sqlite3_elf = self.repo_root / "sqlite3.elf"
        if not sqlite3_elf.is_file():
            log.fatal(
                "sqlite3.elf not found.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z build` first.",
            )

        sysroot = self.config.get(CFG_SYSROOT, "")
        sysroot_path = Path(sysroot)
        mkramfs = sysroot_path / "bin" / "mkramfs.elf"

        print("=== SQLite functional tests ===")
        print("  Running sqlite3.elf via nanvixd standalone...")

        # Bundle sqlite3.elf + daemons into an initrd.
        initrd = make_initrd(self, "sqlite3.elf")

        sql_file = self.repo_root / ".nanvix" / "functional_test.sql"

        try:
            with tempfile.TemporaryDirectory(prefix="nanvix_sqlite_") as tmpdir:
                tmpdir_path = Path(tmpdir)
                ramfs_dir = tmpdir_path / "ramfs"
                ramfs_dir.mkdir()
                (ramfs_dir / "tmp").mkdir(exist_ok=True)
                ramfs_img = tmpdir_path / "rootfs.img"

                run(
                    str(mkramfs),
                    "-o",
                    str(ramfs_img),
                    str(ramfs_dir),
                )

                nanvixd = sysroot_path / "bin" / "nanvixd.elf"
                cmd = (
                    f'"{nanvixd}"'
                    f' -bin-dir "{sysroot_path / "bin"}"'
                    f' -ramfs "{ramfs_img}"'
                    f' -- "{initrd}"'
                    f' < "{sql_file}"'
                )
                run("sh", "-c", cmd, timeout=120)
        finally:
            if initrd.exists():
                initrd.unlink()

        print("  PASS: sqlite3 standalone (exit code 0)")
        print("  PASS: SQLite functional tests")
        print("=== All SQLite tests PASSED ===")

    def _run_tests_windows(self) -> None:
        """Run tests natively on Windows using nanvixd.exe.

        Only standalone mode is tested on Windows; multi-process and
        single-process require linuxd, which is Linux-only. Uses
        make_initrd to bundle the binary with system daemons, and a
        ramfs providing /tmp for any test I/O.
        """
        if self.config.deployment_mode != "standalone":
            log.info(
                f"Skipping tests on Windows for mode"
                f" '{self.config.deployment_mode}' (requires linuxd).",
            )
            return

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
                hint=(
                    "Build the test binaries first"
                    " (for example, run `./z build`)"
                    " and then rerun `./z test`."
                ),
            )

        sql_file = self.repo_root / ".nanvix" / "functional_test.sql"
        sql_input = sql_file.read_bytes()

        failed: list[str] = []
        for binary in test_binaries:
            name = binary.stem
            print(f"RUN  {name}...")
            # make_initrd resolves binaries relative to repo_root;
            # copy the ELF there temporarily unless it already lives there.
            repo_elf = self.repo_root / binary.name
            copied_elf = False
            if binary.resolve() != repo_elf.resolve():
                if repo_elf.exists():
                    raise FileExistsError(f"refusing to clobber existing {repo_elf}")
                shutil.copy2(binary, repo_elf)
                copied_elf = True
            initrd: Path | None = None
            try:
                initrd = make_initrd(self, binary.name)
                with tempfile.TemporaryDirectory(
                    prefix=f"nanvix_{name}_",
                    ignore_cleanup_errors=True,
                ) as tmpdir:
                    tmpdir_path = Path(tmpdir)
                    ramfs_dir = tmpdir_path / "ramfs"
                    ramfs_dir.mkdir()
                    (ramfs_dir / "tmp").mkdir(exist_ok=True)
                    ramfs_img = tmpdir_path / f"rootfs_{name}.img"

                    run(
                        str(mkramfs),
                        "-o",
                        str(ramfs_img),
                        str(ramfs_dir),
                    )

                    result = subprocess.run(  # noqa: S603
                        [
                            str(nanvixd.resolve()),
                            "-bin-dir",
                            str((sysroot_path / "bin").resolve()),
                            "-ramfs",
                            str(ramfs_img),
                            "--",
                            str(initrd),
                        ],
                        input=sql_input,
                        timeout=120,
                        check=False,
                    )
                    if result.returncode != 0:
                        print(f"FAIL {name} (exit code {result.returncode})")
                        failed.append(name)
                    else:
                        print(f"OK   {name}")
            except subprocess.TimeoutExpired:
                print(f"FAIL {name} (timeout)")
                failed.append(name)
            finally:
                if initrd is not None and initrd.exists():
                    initrd.unlink()
                if copied_elf and repo_elf.exists():
                    repo_elf.unlink()

        if failed:
            msg = f"{len(failed)} test(s) failed: {' '.join(failed)}"
            raise RuntimeError(msg)
        log.info(
            f"\t\t*** All {len(test_binaries)} tests PASSED ***",
        )

    def release(self) -> None:
        """Package the SQLite release tarball and verify it."""
        run(*self._make_args("package"), cwd=self.repo_root)
        run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        run(
            "make",
            "-f",
            "Makefile.nanvix",
            "clean",
            cwd=self.repo_root,
        )


if __name__ == "__main__":
    SqliteBuild.main()
