import sys
import os
import time
import threading
import importlib
import win32file
from typing import Dict, Any

# Ensure we import device before mocking
from wsl_rawdisk.server import device
from wsl_rawdisk.server.device import Device

# 1. Create a dummy test image file (1MB) with a unique pattern
IMAGE_PATH = os.path.abspath("test_device.img")
with open(IMAGE_PATH, "wb") as f:
    f.write(b"SMOKE_TEST_PATTERN" + b"\x00" * (1024 * 1024 - 18))

print(f"Created test image at {IMAGE_PATH}")

# 2. Monkey-patch Device to redirect PHYSICALDRIVE99 to the local file
def mock_open(self) -> bool:
    if "PHYSICALDRIVE99" in self.devicename:
        access_mode = win32file.GENERIC_READ
        share_mode = win32file.FILE_SHARE_READ
        if not self.read_only:
            access_mode |= win32file.GENERIC_WRITE
            share_mode |= win32file.FILE_SHARE_WRITE

        try:
            self.handle = win32file.CreateFile(
                IMAGE_PATH,
                access_mode,
                share_mode,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
        except Exception as e:
            print(f"Mock open failed to create file for {IMAGE_PATH}: {e}")
            return False
        self.sector_size = 512
        self.size = 1024 * 1024
        return True
    
    # Otherwise call original/fallback (though we don't expect other drives in the test)
    return False

Device.open = mock_open
Device.get_geometry = lambda self: {"BytesPerSector": 512}
Device.get_size = lambda self: 1024 * 1024

# 3. Load the server module and monkey-patch it
from wsl_rawdisk.server import __main__ as wsl_rawdisk_server

# Mock get_boot_and_pagefile_disk_indices to cache 0 as the boot disk
wsl_rawdisk_server.get_boot_and_pagefile_disk_indices = lambda: {0}

# Mock WMI queries for Win32_DiskDrive
class MockWmiDisk:
    def __init__(self, name):
        self.Name = name

class MockWmi:
    def query(self, q):
        if "Win32_DiskDrive" in q:
            return [MockWmiDisk("\\\\.\\PHYSICALDRIVE0"), MockWmiDisk("\\\\.\\PHYSICALDRIVE99")]
        return []

wsl_rawdisk_server.wmi.WMI = MockWmi

# 4. Start the server main loop in a background thread
# We pass --bind-any so it binds to 0.0.0.0 (necessary for WSL guest connection)
# and we pass --allow-writes
sys.argv = ["wsl-rawdisk-server.py", "tcpserver", "0.0.0.0", "50000", "reconnect", "--allow-writes", "--bind-any"]

server_thread = threading.Thread(target=wsl_rawdisk_server.main, daemon=True)
server_thread.start()
print("Windows-side smoke test server running on port 50000...")

# Keep running until keyboard interrupt or test completion
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("Stopping smoke test server...")
