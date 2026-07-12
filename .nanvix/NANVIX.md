# SQLite Port for Nanvix

> **TL;DR:** This is a port of the SQLite database engine for the Nanvix operating system. Jump to [Quick Start](#quick-start) to get started immediately.

---

## Overview

This document describes the port of [SQLite](https://sqlite.org/) database engine for the [Nanvix](https://github.com/nanvix/nanvix) operating system. This port enables SQLite to run on Nanvix, a POSIX-compatible educational operating system.

| Property | Value |
|----------|-------|
| **Base Version** | SQLite 3.49.0 |
| **Target Platform** | Nanvix (i686) |
| **Build System** | GNU Make (wrapping autoconf) |

**What's included:**
- ✅ Cross-compilation support for Nanvix
- ✅ Static library build (`libsqlite3.a`)
- ✅ CLI shell executable (`sqlite3.elf`)
- ✅ Build helper scripts
- ✅ CI/CD integration

**Dependencies:**
- zlib (compression support)

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Prerequisites](#prerequisites)
3. [Building](#building)
4. [Testing](#testing)
5. [Changes Summary](#changes-summary)
6. [Known Limitations](#known-limitations)
7. [CI/CD](#cicd)

---

## Quick Start

For experienced users who want to build quickly:

```bash
SDK=ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f
NANVIX_MACHINE=microvm \
NANVIX_DEPLOYMENT_MODE=standalone \
NANVIX_MEMORY_SIZE=256mb \
  ./z setup --with-docker "$SDK"
./z build
./z test
```

Continue reading for detailed instructions.

---

## Prerequisites

You need the following components to build SQLite for Nanvix:

| Component | Description | Default Location |
|-----------|-------------|------------------|
| **Nanvix SDK** | Clang/LLVM C SDK v0.20.0-sdk.1 | Docker image |
| **Nanvix runtime** | Nanvix 0.20.0 binaries used by tests | `.nanvix/sysroot` |
| **zlib** | SDK-built release 1.3.1-nanvix-0.20.0 | `.nanvix/buildroot` |

`./z setup` downloads the runtime and matching zlib release. Build-time system
headers, libraries, startup objects, and linker configuration are supplied by
the SDK; the downloaded runtime sysroot is not used for compilation.

### Available Platform Configurations

| Platform | Process Mode | Linux | Windows | Artifact Pattern |
|----------|--------------|-------|---------|------------------|
| microvm | standalone | ✅ | ✅ | `microvm.*standalone` |

> **Note:** Nanvix 0.20.0 publishes only the microvm 256 MiB runtime used by
> this port; no Hyperlight runtime artifacts are available. Only standalone
> mode is supported here, with `nanvixd` running the guest on Linux and Windows.

---

## Building

### Using Docker

The pinned SDK contains Clang, LLVM binutils, and the Nanvix target sysroot:

```bash
SDK=ghcr.io/nanvix/nanvix-sdk-c-clang@sha256:f61737cb0780e6a2058c6d0bdf8ae5562db18de437173b2bcbbe6973abd3689f
./z setup --with-docker "$SDK"
./z build
```

Native `gcc` remains responsible only for build-machine generators such as
`jimsh0` and `lemon`. Target objects are compiled with SDK Clang, static
archives use LLVM tools, and executables are linked through Clang.

### Build Outputs

After a successful build, you will have:

| File | Description |
|------|-------------|
| `libsqlite3.a` | SQLite static library |
| `sqlite3.elf` | SQLite CLI shell executable |

---

## Testing

> **Important:** Tests must be run through the Nanvix daemon (`nanvixd.elf`).

### Running the Test Suite

```bash
# Run all tests
./z test
```

### Running Individual Tests

To run sqlite3 shell manually:

```bash
cd "$NANVIX_HOME" && echo "SELECT 'Hello, Nanvix!';" | ./bin/nanvixd.elf -- /path/to/sqlite3.elf
```

### Available Test Executables

| Executable | Description |
|------------|-------------|
| `sqlite3.elf` | SQLite command-line interface shell |

---

## Changes Summary

The following changes were made to support Nanvix.

### Build System Changes

| Change | Description |
|--------|-------------|
| New Makefile | Added `Makefile.nanvix` for Nanvix cross-compilation |
| Cross-compilation | Uses the pinned Nanvix Clang/LLVM SDK |
| Dependency isolation | Reads zlib from `.nanvix/buildroot` |
| Configure wrapper | Wraps standard `./configure` with Nanvix cross-compilation settings |
| Linking | Uses the Clang driver and SDK-provided defaults |
| Shared libraries | Disabled (not supported on Nanvix) |
| Test target | Modified to run via `nanvixd.elf` |

### Configuration Options

| Option | Description |
|--------|-------------|
| `SQLITE_OMIT_WAL` | WAL mode disabled (not supported on Nanvix) |
| `--disable-threadsafe` | Threading disabled (single-threaded mode) |
| `--disable-tcl` | TCL bindings disabled |
| `--disable-shared` | Shared libraries disabled |

### New Files

| File | Purpose |
|------|---------|
| `Makefile.nanvix` | Standalone Makefile for Nanvix cross-compilation |
| `NANVIX.md` | This documentation file |
| `.github/workflows/nanvix-ci.yml` | CI workflow for automated builds |

---

## Known Limitations

| Limitation | Impact |
|------------|--------|
| **No WAL mode** | Write-Ahead Logging disabled (`SQLITE_OMIT_WAL`) |
| **No shared libraries** | Only static library (`libsqlite3.a`) is built |
| **No threading** | Single-threaded mode only |
| **No TCL bindings** | TCL interface not available |
| **Static linking only** | All executables are statically linked |

---

## CI/CD

The GitHub Actions workflow at `.github/workflows/nanvix-ci.yml` automates building and testing on every change.

### Trigger Events

| Event | Description |
|-------|-------------|
| Push to `nanvix/**` | Any push to Nanvix branches |
| PR to `nanvix/**` | Pull requests targeting Nanvix branches |
| Daily schedule | Runs at midnight UTC |
| Manual dispatch | Can be triggered manually |
| Repository dispatch | Triggered by `nanvix-release` events |

### Build Matrix

Nanvix 0.20.0 publishes only microvm runtime assets at 256 MiB; it has no
Hyperlight assets. The active CI and package matrix is:

| Platform | Process Mode | Memory | OS |
|----------|--------------|--------|----|
| microvm | standalone | 256mb | Linux + Windows |

All Linux configurations run functional tests in parallel with `fail-fast: false`, ensuring that all platforms are tested even if one fails. Windows standalone tests use `nanvixd.exe` to run `sqlite3.elf` with functional SQL queries.

### Dependency Management

The CI workflow automatically downloads the matching zlib release from `nanvix/zlib` before building SQLite, ensuring the correct platform-specific zlib library is used.

---
