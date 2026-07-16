# wsl-rawdisk

`wsl-rawdisk` gives WSL2 FUSE-mounted access to Windows physical drives — including the drive holding Windows' own boot and system components — via a small Windows-side proxy server and a WSL-side client.

## Read this before you use it

Windows treats three kinds of partition as essential to a working boot, and modifying any of them from outside Windows can cause a BSOD or an unbootable system:

- the **System/ESP volume** holding the boot manager (`\EFI\Microsoft\Boot\bootmgfw.efi`)
- the **boot volume** Windows itself loads from (`C:\Windows\System32\Winload.efi`)
- any partition containing an **active paging file**

This tool can grant write access to any of those. The safety features below reduce the chance of doing that by accident — they do not make it safe to experiment on your only Windows install. Test against an external USB drive or an offline secondary disk first.

## Enforced safety features

- **Boot & pagefile protection.** Write access to the physical drive backing the active Windows boot/system volume or an active pagefile is refused by default, independent of any other flag.
- **Path validation.** Only `\\.\PHYSICALDRIVE<N>` targets are accepted; arbitrary file paths are rejected server-side.
- **Per-request authorization.** Read/write intent is negotiated per drive connection, not as a single switch that applies for the server's whole lifetime — one session can hold a read-only mount and a write-enabled mount to different drives at once.

None of this is a substitute for knowing which physical drive number you're pointing at. Run `uv run wsl-rawdisk` with no arguments first — it lists the drives it can see before you commit to a number.

## Requirements

- **Windows side:** Windows 11, [uv](https://docs.astral.sh/uv/), and an **elevated (Administrator) PowerShell** — the server opens raw physical drive handles, which Windows will not grant to an unelevated process.
- **WSL side:** WSL2 running Ubuntu (tested on 26.04), uv, and root (`sudo`) for the FUSE mount and loop-device setup.

## Installation

### Windows server

powershell

```powershell
uv sync --extra server
```

### WSL client

bash

```bash
uv sync --extra client
```

## Usage

Start the server first, from an **elevated** PowerShell:

powershell

```powershell
uv run wsl-rawdisk-server tcpserver 0.0.0.0 50000
```

This starts read-only-capable only — no drive can be opened for writing yet. From WSL:

bash

```bash
# List available drives, no mount performed
sudo uv run wsl-rawdisk

# Mount drive 3 read-only (default)
sudo uv run wsl-rawdisk 3
```

### Enabling writes on a non-boot drive

Restart the server with `--allow-writes`, then request write intent from the client:

powershell

```powershell
uv run wsl-rawdisk-server tcpserver 0.0.0.0 50000 --allow-writes
```

bash

```bash
sudo uv run wsl-rawdisk 3 --allow-writes
```

The server still refuses this for the boot/pagefile disk regardless of `--allow-writes` — see below.

### Writing to the boot disk (not recommended)

Blocked by default:

bash

```bash
sudo uv run wsl-rawdisk 0 --allow-writes  # rejected by the server
```

To permit it, the server must be started with **both** flags, which opens a 5-minute authorization window starting from server launch:

powershell

```powershell
uv run wsl-rawdisk-server tcpserver 0.0.0.0 50000 --allow-writes --allow-unsafe-boot-disk-writes
```

If no write-open of the boot disk is requested within that window, the override expires and further boot-disk write requests are refused until the server is restarted. This is a narrow safety margin, not a green light — restarting the server to get a fresh window is easy, and the window existing at all doesn't mean the write is a good idea.

## Verifying your setup

Before relying on a fresh install, run the test suite:

bash

```bash
uv run --extra dev pytest tests/test_safety.py
```

This exercises the write-authorization and boot-disk-refusal logic against mocked connections — it does not touch a real disk. `tests/smoke_test_server.py` and `tests/smoke_test_client.py` are intended to be run manually, one on each side, against a real (ideally loopback/test) device before you trust a build against anything that matters.

## Mounting

Once mounted, partitions on the opened drive are reachable from WSL via `/dev/loop0p1` or `/dev/disk/by-*`.

Destroying a partition table or a system disk with this tool can brick the machine. Windows will automatically write-protect partitions that are already mounted/active within Windows itself, but that protection does not extend to partitions Windows isn't using at the time.

## Known limitations

- The server binds `0.0.0.0` (all interfaces) by default rather than the WSL-facing adapter specifically — anything that can reach the port can attempt to connect, not just WSL. Tighten this yourself via firewall rule if the machine is on an untrusted network.
- The wire protocol has no authentication or encryption. Treat the connection as trusted-network-only.
- Boot/pagefile disk indices are cached once at server startup; a pagefile added or moved to a different disk mid-session won't be reflected until the server restarts.

See `TASKS.md` for the staged plan addressing these.

## Building a standalone server executable

powershell

```powershell
uv run pyinstaller -F src/wsl_rawdisk/server/__main__.py --name wsl-rawdisk-server
```

## Contributing

See `CONTRIBUTING.md` and `AGENTS.md`.
