import sys
import time
import os
import json
import tempfile
import asyncio
import struct
import logging
import pyfuse3
import pyfuse3_asyncio
import subprocess
import argparse
from typing import Dict, Any

from wsl_rawdisk.protocol import (
    Command,
    FMT_OPEN,
    FMT_READ_WRITE,
    FMT_GET_SIZE,
    FMT_REPLY_BYTE,
    FMT_REPLY_SHORT,
    FMT_REPLY_QWORD
)
from wsl_rawdisk.client.fuse_fs import FS

logger = logging.getLogger(__name__)

class AsyncTcpClientConnection:
    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self.reader = None
        self.writer = None
        self.lock = asyncio.Lock()

    async def connect(self):
        self.reader, self.writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), 
            timeout=6.0
        )

    async def send(self, data: bytes):
        if not self.writer:
            raise ConnectionError("Not connected")
        self.writer.write(data)
        await self.writer.drain()

    async def recv(self, n: int) -> bytes:
        if not self.reader:
            raise ConnectionError("Not connected")
        data = await self.reader.readexactly(n)
        return data

    async def pack(self, fmt, *values):
        data = struct.pack(fmt, *values)
        await self.send(data)

    async def unpack(self, fmt):
        size = struct.calcsize(fmt)
        data = await self.recv(size)
        values = struct.unpack(fmt, data)
        if len(values) == 1:
            return values[0]
        return values
    
    async def close(self):
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except Exception:
                pass


class AsyncConnectedDevice:
    def __init__(self, conn: AsyncTcpClientConnection, device_name: str):
        self.conn = conn
        self.device_name = device_name
        self.index = -1
        self.size = 0
        self.filename = ""
        self.loop_dev = None

    async def open(self, write_intent: bool = False) -> bool:
        device_name_bytes = self.device_name.encode('utf-8')
        async with self.conn.lock: # <--- ACQUIRE LOCK
            await self.conn.pack(FMT_OPEN, Command.OPEN, len(device_name_bytes), 1 if write_intent else 0)
            await self.conn.send(device_name_bytes)
            self.index = await self.conn.unpack(FMT_REPLY_SHORT)
        # Call get_size OUTSIDE the lock, as get_size acquires it too
        if self.index != -1:
            self.size = await self.get_size()
        return self.index != -1

    async def read(self, pos: int, size: int) -> bytes:
        async with self.conn.lock: # <--- ACQUIRE LOCK
            await self.conn.pack(FMT_READ_WRITE, Command.READ, self.index, pos, size)
            status = await self.conn.unpack(FMT_REPLY_BYTE)
            if status == 0:
                data = await self.conn.recv(size)
            else:
                data = b''
        return data

    async def write(self, pos: int, data: bytes) -> bool:
        async with self.conn.lock: # <--- ACQUIRE LOCK
            await self.conn.pack(FMT_READ_WRITE, Command.WRITE, self.index, pos, len(data))
            await self.conn.send(data)
            status = await self.conn.unpack(FMT_REPLY_BYTE)
        return status == 0

    async def get_size(self) -> int:
        async with self.conn.lock: # <--- ACQUIRE LOCK
            await self.conn.pack(FMT_GET_SIZE, Command.GET_SIZE, self.index)
            return await self.conn.unpack(FMT_REPLY_QWORD)

    async def close(self) -> None:
        pass


async def loop_device_manager(devices: Dict[str, AsyncConnectedDevice], mountpoint: str):
    if not os.geteuid() == 0:
        logger.warning("not running as root, cannot create loop devices")
        return

    if devices:
        test_file = os.path.join(mountpoint, next(iter(devices.values())).filename)
        
        def check_ready() -> bool:
            start_time = time.time()
            while time.time() - start_time < 10.0:
                try:
                    os.stat(test_file)
                    return True
                except Exception:
                    time.sleep(0.1)
            return False

        logger.info(f"Waiting for FUSE mount readiness at {mountpoint}...")
        loop = asyncio.get_running_loop()
        is_ready = await loop.run_in_executor(None, check_ready)
        if not is_ready:
            logger.error(f"FUSE mount at {mountpoint} did not become ready within timeout.")
            return
        logger.info("FUSE mount is ready.")
    else:
        logger.warning("No devices registered; skipping FUSE readiness check.")

    for d in devices.values():
        c = ["losetup", "-f", "--show", "-P", "--direct-io=on", os.path.join(mountpoint, d.filename)]
        def run_losetup():
            return subprocess.run(c, capture_output=True, text=True)
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, run_losetup)
        if res.returncode == 0:
            d.loop_dev = res.stdout.strip()
            logger.info(f"Mapped {d.device_name} to {d.loop_dev}")
        else:
            logger.error(f"Failed to map {d.device_name}: {res.stderr}")

async def cleanup_loop_devices(devices: Dict[str, AsyncConnectedDevice]):
    for d in devices.values():
        if d.loop_dev is not None:
            c = ["losetup", "-d", d.loop_dev]
            def run_cleanup():
                return subprocess.run(c, capture_output=True)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, run_cleanup)
async def main_async():
    parser = argparse.ArgumentParser(description="WSL RawDisk Proxy Client")
    parser.add_argument("drive", nargs="?", help="Drive number (e.g., '2' for PHYSICALDRIVE2) or full path")
    parser.add_argument("--allow-writes", action="store_true", help="Request write access to the drive")
    parser.add_argument("--read-only", action="store_true", help="Request read-only access (default)")
    args = parser.parse_args()

    write_intent = False
    if args.allow_writes and not args.read_only:
        write_intent = True

    tmp_mountpoint = tempfile.TemporaryDirectory(prefix="wsl_rawdisk_")
    mountpoint = os.path.abspath(tmp_mountpoint.name)
    
    def get_wsl_host_ip():
        # 1. Try explicit config check for Mirrored Mode
        try:
            # Get Windows %USERPROFILE% via interop
            res_cmd = subprocess.run(["/mnt/c/Windows/System32/cmd.exe", "/C", "echo %USERPROFILE%"], capture_output=True, text=True)
            if res_cmd.returncode == 0:
                win_path = res_cmd.stdout.strip()
                # Convert to WSL path
                res_wslpath = subprocess.run(["wslpath", "-u", win_path], capture_output=True, text=True)
                if res_wslpath.returncode == 0:
                    wsl_profile_path = res_wslpath.stdout.strip()
                    wslconfig_path = os.path.join(wsl_profile_path, ".wslconfig")
                    
                    # Check for mirrored networking
                    if os.path.isfile(wslconfig_path):
                        with open(wslconfig_path, "r", encoding="utf-8") as f:
                            config_text = f.read().lower().replace(" ", "")
                            if "networkingmode=mirrored" in config_text:
                                logger.info("Detected mirrored networking mode in .wslconfig. Using 127.0.0.1")
                                return '127.0.0.1'
        except Exception as e:
            logger.debug(f"Could not read .wslconfig explicitly, falling back to routing table: {e}")

        # 2. Fall back to NAT mode discovery via routing table
        try:
            result = subprocess.run(['ip', 'route'], capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if line.startswith('default via'):
                    nat_ip = line.split()[2]
                    logger.info(f"Detected NAT networking mode. Using gateway IP: {nat_ip}")
                    return nat_ip
        except Exception as e:
            logger.warning(f"Could not determine WSL host IP from routing table, falling back to localhost: {e}")
        
        return '127.0.0.1'

    host = get_wsl_host_ip()
    port = 50000

    conn = AsyncTcpClientConnection(host, port)
    try:
        await conn.connect()
    except asyncio.TimeoutError:
        logger.error(f"Timeout connecting to Windows host at {host}:{port}.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to connect to Windows host at {host}:{port}: {e}")
        sys.exit(1)

    # 1. Fetch available drives
    async with conn.lock:
        await conn.pack("B", Command.GET_DISKDRIVES)
        size = await conn.unpack("I")
        drives_bytes = await conn.recv(size)
        
    all_drives = json.loads(drives_bytes.decode("utf-8"))
    all_drives.sort()

    # 2. If no drive specified, print the menu and exit
    if not args.drive:
        print("\nAvailable Windows Physical Drives:")
        print("----------------------------------")
        for d in all_drives:
            # Extract just the number for easy reading
            num = d.replace("\\\\.\\PHYSICALDRIVE", "")
            print(f"  [{num}] {d}")
        print("\nTo mount a drive, run: sudo python3 wsl-rawdisk.py <number>")
        await conn.pack("b", Command.CLOSE)
        await conn.close()
        sys.exit(0)

    # 3. Format the requested drive
    target_drive = args.drive
    if target_drive.isdigit():
        target_drive = f"\\\\.\\PHYSICALDRIVE{target_drive}"
    elif not target_drive.startswith("\\\\.\\"):
        target_drive = "\\\\.\\" + target_drive

    if target_drive not in all_drives:
        logger.error(f"Drive {target_drive} not found on Windows host.")
        await conn.pack("b", Command.CLOSE)
        await conn.close()
        sys.exit(1)

    # 4. Mount ONLY the requested drive
    devices = {}
    logger.info(f"Requesting mount for {target_drive}...")
    device = AsyncConnectedDevice(conn, target_drive)
    
    filename = target_drive.replace("\\", "").replace(".", "").replace(":", "").lower()
    
    try:
        if await asyncio.wait_for(device.open(write_intent=write_intent), timeout=5.0):
            device.filename = filename
            device.loop_dev = None
            devices[filename] = device
        else:
            logger.error(f"Opening {target_drive} failed on the Windows side.")
            sys.exit(1)
    except asyncio.TimeoutError:
        logger.error(f"Timeout opening device {target_drive}")
        sys.exit(1)

    loop_task = asyncio.create_task(loop_device_manager(devices, mountpoint))

    pyfuse3_asyncio.enable()
    fs = FS(devices)
    options = set(pyfuse3.default_options)
    options.add('fsname=wsl_rawdisk')
    options.add('allow_other')
    
    pyfuse3.init(fs, mountpoint, options)
    
    try:
        await pyfuse3.main()
    except asyncio.exceptions.CancelledError:
        pass
    except KeyboardInterrupt:
        logger.info("Ctrl+C detected, unmounting...")
    finally:
        logger.info("Tearing down loop devices...")
        loop_task.cancel()
        await cleanup_loop_devices(devices)
        logger.info("Closing FUSE mount...")
        pyfuse3.close()
        await conn.pack("b", Command.CLOSE)
        await conn.close()

def main():
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(main_async())

if __name__ == '__main__':
    main()
