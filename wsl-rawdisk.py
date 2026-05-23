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

from protocol import Command
from fuse_fs import FS

logger = logging.getLogger(__name__)

FMT_OPEN = "=BH"
FMT_READ_WRITE = "=BH2Q"
FMT_GET_SIZE = "=BH"
FMT_REPLY_BYTE = "B"
FMT_REPLY_SHORT = "h"
FMT_REPLY_QWORD = "Q"

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

    async def open(self) -> bool:
        device_name_bytes = self.device_name.encode('utf-8')
        async with self.conn.lock: # <--- ACQUIRE LOCK
            await self.conn.pack(FMT_OPEN, Command.OPEN, len(device_name_bytes))
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

    # [FIX] Removed synchronous os.path.exists() to prevent FUSE self-deadlock.
    # Yield to the event loop so pyfuse3.main() can initialize.
    logger.info("Waiting 2.0s for FUSE mount to stabilize...")
    await asyncio.sleep(2.0)

    for d in devices.values():
        c = ["losetup", "-f", "--show", "-P", "--direct-io=on", os.path.join(mountpoint, d.filename)]
        proc = await asyncio.create_subprocess_exec(
            *c, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            d.loop_dev = stdout.decode().rstrip("\n")
            logger.info(f"Mapped {d.device_name} to {d.loop_dev}")
        else:
            logger.error(f"Failed to map {d.device_name}: {stderr.decode()}")

async def cleanup_loop_devices(devices: Dict[str, AsyncConnectedDevice]):
    for d in devices.values():
        if d.loop_dev is not None:
            c = ["losetup", "-d", d.loop_dev]
            proc = await asyncio.create_subprocess_exec(*c)
            await proc.wait()

async def main_async():
    parser = argparse.ArgumentParser(description="WSL RawDisk Proxy Client")
    parser.add_argument("drive", nargs="?", help="Drive number (e.g., '2' for PHYSICALDRIVE2) or full path")
    args = parser.parse_args()

    tmp_mountpoint = tempfile.TemporaryDirectory(prefix="wsl_rawdisk_")
    mountpoint = os.path.abspath(tmp_mountpoint.name)
    
    def get_wsl_host_ip():
        try:
            result = subprocess.run(['ip', 'route'], capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if line.startswith('default via'):
                    return line.split()[2]
        except Exception as e:
            logger.warning(f"Could not determine WSL host IP, falling back to localhost: {e}")
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
        if await asyncio.wait_for(device.open(), timeout=5.0):
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
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main_async())

if __name__ == '__main__':
    main()
