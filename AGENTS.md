# AGENTS.md — wsl-rawdisk

## What this project is

A two-process tool giving WSL2 FUSE-mounted access to Windows physical drives: a Windows-side server (`wsl-rawdisk-server.py`, pywin32/wmi, runs elevated) speaking a small binary protocol over TCP to a WSL-side async client (`wsl-rawdisk.py`, pyfuse3) that exposes each opened drive as a file under a temp mountpoint and loop-mounts it.

## Environment

- Windows side: Python 3.x + pywin32, wmi, pyinstaller (for the .exe build)
- WSL side: Ubuntu 22.04+, pyfuse3 + pyfuse3\_asyncio (NOT python3-fusepy — README is wrong, fix it)
- Networking: server binds TCP; WSL client discovers the Windows host IP via `ip route` default gateway
- See the `wsl-windows-boundary` skill for general WSL/Windows boundary reasoning (networking mode, interop, port ownership). This file only states facts specific to _this_ repo.

## The one rule that matters more than any other

This tool grants raw read/write access to physical disks, including the disk Windows boots from. Any change touching `Device.write`, the server's `--allow-writes` handling, or `GET_DISKDRIVES` must be treated as safety-critical, not a normal feature change. Do not "clean up" the safety-adjacent code as a side effect of an unrelated change without calling it out explicitly in the PR/commit description.

## Current known gaps (see TASKS.md for the worked plan)

- Boot/pagefile disk indices are cached on server startup. A pagefile added or moved to a different drive mid-session will not be detected until the server is restarted.
- `Device.write` and the server's WRITE handler use bare `assert` on data that can legitimately be malformed (misaligned offset, truncated recv) — these currently crash the server instead of returning a protocol error.
- Server binds `0.0.0.0` by default — should bind the WSL adapter address specifically.

## Build / test

- Windows server build: `pyinstaller -F wsl-rawdisk-server.py` (from a venv with `venv-win-requirements.txt` installed)
- No test suite currently exists — Gate 3 in TASKS.md adds a loopback-file smoke test before any further protocol changes are trusted.

## Conventions

- Protocol commands are the `Command` IntEnum in `protocol.py` — add new commands there, don't hardcode integers.
- Struct format strings (`FMT_*`) are duplicated across `connected_device.py` and `wsl-rawdisk.py` — if you touch the wire format, update both, or better, factor them into `protocol.py` while you're in there.