# Copyright(c) The Maintainers of Nanvix.
# Licensed under the MIT License.

"""Nanvix build script for sqlite.

Usage (from repository root):
    ./z setup      # Download sysroot
    ./z build      # Build libsqlite3.a and sqlite3.elf
    ./z test       # Run smoke, integration, and functional tests
    ./z release    # Package release tarball
    ./z clean      # Remove build artifacts
"""

from nanvix_zutil import CFG_GH_TOKEN, CFG_SYSROOT, EXIT_MISSING_DEP, Sysroot, ZScript, log


class SqliteBuild(ZScript):
    """Build script for nanvix/sqlite."""

    def sysroot_required_files(self) -> list[str]:
        """Require zlib in addition to the base sysroot files."""
        files = super().sysroot_required_files()
        files.append("lib/libz.a")
        return files

    def _make_args(self, *targets: str) -> list[str]:
        """Build the ``make -f Makefile.nanvix`` argument list."""
        nanvix_sysroot = self.config.get(CFG_SYSROOT, "")
        if not nanvix_sysroot:
            log.fatal(
                f"{CFG_SYSROOT} is not set.",
                code=EXIT_MISSING_DEP,
                hint="Run `./z setup` first to download the sysroot.",
            )

        return [
            "make",
            "-f",
            "Makefile.nanvix",
            "CONFIG_NANVIX=y",
            f"NANVIX_HOME={nanvix_sysroot}",
            *targets,
        ]

    def _make(self, *targets: str) -> None:
        """Run ``make -f Makefile.nanvix`` with standard Nanvix variables."""
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def setup(self) -> None:
        """Download the Nanvix sysroot and persist its path."""
        sysroot = Sysroot.download(
            machine=self.config.machine,
            deployment_mode=self.config.deployment_mode,
            memory_size=self.config.memory_size,
            tag="latest",
            gh_token=self.config.get(CFG_GH_TOKEN),
        )
        sysroot.verify(self.sysroot_required_files())
        self.config.set(CFG_SYSROOT, str(sysroot.path))
        self.config.save()

    def build(self) -> None:
        """Build libsqlite3.a and sqlite3.elf."""
        self._make("all")

    def test(self) -> None:
        """Run the SQLite test suite.

        Without targets, runs the full suite (smoke + integration + functional).
        With targets (e.g. ``./z test -- test-smoke test-integration``), passes
        them directly to the Makefile.
        """
        targets = self.targets if self.targets else ["test"]
        self.run(*self._make_args(*targets), cwd=self.repo_root)

    def release(self) -> None:
        """Package the SQLite release tarball and verify it."""
        self.run(*self._make_args("package"), cwd=self.repo_root)
        self.run(*self._make_args("verify-package"), cwd=self.repo_root)

    def clean(self) -> None:
        """Remove build artifacts."""
        self.run("make", "-f", "Makefile.nanvix", "clean", cwd=self.repo_root)


if __name__ == "__main__":
    SqliteBuild.main()
