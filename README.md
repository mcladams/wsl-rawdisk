# wsl-rawdisk

wsl-rawdisk enables access to physical drives from WSL including the drive containing essential Windows components

---
Windows considers partitions with any of 3 things essential and might BSOD or fail to boot if they are modified: the System volume where the bootmanager resides ESP:\EFI\Microsoft\Boot\bootmgfw.efi; the boot volume where Windows itself loads from with C:\Windows\System32\Winload.efi and all partitions containing active paging files.

## 🛡️ Enforced Safety Features

This version of `wsl-rawdisk` contains server-side validation and write authorization checks to prevent accidental modifications to critical Windows components:

1. **Boot & Pagefile Protection:** Write access is disabled by default for the physical drive containing the active Windows boot/system volume or pagefiles.
2. **Path Validation:** Only valid `PHYSICALDRIVE` targets (e.g. `\\.\PHYSICALDRIVE<N>`) are allowed; arbitrary file paths are rejected.
3. **Per-Request Isolation:** Write authorization is requested and evaluated per drive connection, rather than as a global server-wide switch.

### 🚀 Usage Guidance & Guardrails

* **Read-Only Mode (Safe):** By default, connections are read-only.
  ```bash
  sudo uv run wsl-rawdisk 3
  ```
  *(Always use read-only unless write access is explicitly required)*

* **Write Mode on Non-Boot Drives (Caution):** Allowed for external USB drives, offline disks, etc.
  ```bash
  sudo uv run wsl-rawdisk 3 --allow-writes
  ```

* **Write Mode on Boot Disk (Blocked by Default):** Rejects write opens to the boot disk index (usually `0`).
  ```bash
  sudo uv run wsl-rawdisk 0 --allow-writes  # Rejected by server
  ```
  To permit writes to the boot/pagefile disk, the Windows server daemon must be started with the `--allow-unsafe-boot-disk-writes` flag. This opens a temporary **5-minute authorization window** from server startup:
  ```powershell
  uv run wsl-rawdisk-server tcpserver 0.0.0.0 50000 --allow-unsafe-boot-disk-writes
  ```
  If write-open is not requested within 5 minutes of server startup, the override expires and the server rejects subsequent boot disk write opens.

To install and start the app, install `uv` on both platforms:

### 🖥️ Windows Server Setup
1. Sync dependencies for the server:
   ```powershell
   uv sync --extra server
   ```
2. Start the Windows-side server proxy:
   ```powershell
   uv run wsl-rawdisk-server tcpserver 0.0.0.0 50000 --allow-writes
   ```

### 🐧 WSL Client Setup
1. Sync dependencies for the client:
   ```bash
   uv sync --extra client
   ```
2. Run the client mapping:
   ```bash
   sudo uv run wsl-rawdisk <drive_number>
   ```

You can then access or mount the partitions on your system disk in WSL using `/dev/loop0p1` or `/dev/disk/by-*`.

Use at own risk; destroying partition tables or system disks can lead to a bricked system. Windows will automatically write protect partitions that are already mounted/active in Windows.

Tested on Windows 11 and WSL running Ubuntu 26.04.

To build the standalone Windows server executable:
```powershell
uv run pyinstaller -F src/wsl_rawdisk/server/__main__.py --name wsl-rawdisk-server
```
