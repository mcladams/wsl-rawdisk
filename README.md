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
  sudo python3 wsl-rawdisk.py 3
  ```
  *(Always use read-only unless write access is explicitly required)*

* **Write Mode on Non-Boot Drives (Caution):** Allowed for external USB drives, offline disks, etc.
  ```bash
  sudo python3 wsl-rawdisk.py 3 --allow-writes
  ```

* **Write Mode on Boot Disk (Blocked by Default):** Rejects write opens to the boot disk index (usually `0`).
  ```bash
  sudo python3 wsl-rawdisk.py 0 --allow-writes  # Rejected by server
  ```
  To permit writes to the boot/pagefile disk, the Windows server daemon must be started with the `--allow-unsafe-boot-disk-writes` flag. This opens a temporary **5-minute authorization window** from server startup:
  ```powershell
  python wsl-rawdisk-server.py --allow-unsafe-boot-disk-writes
  ```
  If write-open is not requested within 5 minutes of server startup, the override expires and the server rejects subsequent boot disk write opens.

To start the app, extract the contents of the release package on the WSL side, then run:\
sudo python3 wsl-rawdisk.py

You can then access or mount the partitions on your system disk using /dev/loop0p1 or /dev/disk/by-*.

In WSL, `pyfuse3` and `pyfuse3_asyncio` must be installed. For Ubuntu, this can be done using:
sudo apt install python3-pyfuse3

Use at own risk, destroying partition tables or system disk can lead to bricked system.\
Windows will automatically write protect partitions already mounted in Windows.

Tested on Windows 11 and WSL running Ubuntu 22.04

The wsl-rawdisk-server.exe is included in the distribution package, but can be build on windows:\
python3 -m venv\
venv\Scripts\activate\
pip install -r venv-win-requirements.txt\
pyinstaller -F wsl-rawdisk-server.py
