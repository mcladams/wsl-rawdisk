# wsl-rawdisk

wsl-rawdisk enables access to pysical drives from WSL including the drive containning essential Windows components

---
Windows considers paritions with any of 3 thigns eseenetial and might BSOD or fail to boot if they are modified: the System volume where the bootmanager resides ESP:\EFI\Microsfot\Boot\bootmgfw.efi; the boot volume where Windows itself loads from with C:\Windows\System32\Winload.efi and all partitions containing cative paging files.

## The Refactor branch is under heavy refactoring and not suitable for any use.

## The legacy main branch from the upstream repo is dangerous and without guardrails 

### Use caution and the guudance below:

# ✅ SAFE: offline partition  (e.g., D: drive not in use)
sudo python3 wsl-rawdisk.py 3 --read-only

# ✅ RELATIVELY SAFE: External USB drive
sudo python3 wsl-rawdisk.py 4

# ❌ DANGEROUS: Non-C:\Windows partitions on the System disk while Windows running
sudo python3 wsl-rawdisk.py 0 --allow-writes

# ❌ EXTREMELY DANGEROUS: Active C: partition
# Do not attempt

---

To start the app, extract the contents of the release package on the WSL side, then run:\
sudo python3 wsl-rawdisk.py

You can then access or mount the partitions on your system disk using /dev/loop0p1 or /dev/disk/by-*.

In WSL fusepy must be installed. For ubuntu this can be done using:\
sudo apt install python3-fusepy

Use at own risk, destroying partition tables or system disk can lead to bricked system.\
Windows will automatically write protect partitions already mounted in Windows.

Tested on Windows 11 and WSL running Ubuntu 22.04

The wsl-rawdisk-server.exe is included in the distribution package, but can be build on windows:\
python3 -m venv\
venv\Scripts\activate\
pip install -r venv-win-requirements.txt\
pyinstaller -F wsl-rawdisk-server.py
