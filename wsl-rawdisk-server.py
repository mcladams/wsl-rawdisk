import sys
import os
import win32ui
import win32event
import json
import logging
import wmi  # type: ignore
import win32com.shell.shell as shell
from win32com.shell import shellcon
import re
import time
from typing import Optional, Set
import win32file
import winioctlcon
import struct
import pywintypes

import connections
from device import Device
from connected_device import ConnectedDevice
from protocol import (
    Command,
    FMT_OPEN,
    FMT_OPEN_POST_CMD,
    FMT_READ_WRITE,
    FMT_GET_SIZE,
    FMT_REPLY_BYTE,
    FMT_REPLY_SHORT,
    FMT_REPLY_QWORD
)

logger = logging.getLogger(__name__)

def is_valid_device_name(device_name: str) -> bool:
    if re.match(r'^\\\\\.\\PHYSICALDRIVE\d+$', device_name, re.IGNORECASE):
        return True
    try:
        if os.path.isfile(device_name):
            return True
    except Exception:
        pass
    return False

def get_physical_drive_index(device_name: str) -> Optional[int]:
    match = re.search(r'PHYSICALDRIVE(\d+)', device_name, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def get_boot_and_pagefile_disk_indices() -> Set[int]:
    indices: Set[int] = set()
    drive_letters = set()
    sys_drive = os.environ.get('SystemDrive', 'C:')
    if sys_drive:
        drive_letters.add(sys_drive.strip(':').upper() + ':')

    try:
        c = wmi.WMI()
        for pf in c.Win32_PageFileUsage():
            if pf.Name and len(pf.Name) >= 2 and pf.Name[1] == ':':
                drive_letters.add(pf.Name[:2].upper())
    except Exception as e:
        logger.warning(f"Could not query pagefile usage via WMI: {e}")

    try:
        c = wmi.WMI()
        for drive in c.Win32_DiskDrive():
            drive_index = drive.Index
            if drive_index is None:
                continue
            for partition in drive.associators('Win32_DiskDriveToDiskPartition'):
                for logical in partition.associators('Win32_LogicalDiskToPartition'):
                    if logical.DeviceID and logical.DeviceID.upper() in drive_letters:
                        indices.add(int(drive_index))
    except Exception as e:
        logger.warning(f"Could not map logical drives via WMI: {e}")

    # Fallback to IOCTL
    for dl in drive_letters:
        try:
            h = win32file.CreateFile(
                f"\\\\.\\{dl}",
                win32file.GENERIC_READ,
                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
            try:
                res = win32file.DeviceIoControl(
                    h,
                    winioctlcon.IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS,
                    b'',
                    1024
                )
                num_extents = struct.unpack('I', res[:4])[0]
                for i in range(num_extents):
                    ext_offset = 8 + i * 24
                    disk_num = struct.unpack('I', res[ext_offset:ext_offset+4])[0]
                    indices.add(disk_num)
            finally:
                win32file.CloseHandle(h)
        except pywintypes.error:
            pass
        except Exception:
            pass

    return indices

def parse_connection(args):
    if len(args) == 0:
        return None, 0
        
    if args[0] == "tcpserver":
        host = args[1]
        port = int(args[2])
        return connections.TcpServer(host, port), 3

    elif args[0] == "tcpclient":
        host = args[1]
        port = int(args[2])
        return connections.TcpClientConnection(host, port), 3

    else:
        return None, 0

def main():
    script = os.path.abspath(sys.argv[0])
    if script.endswith('.py'):
        exe = sys.executable
        params = [script]
    else:
        exe = script
        params = []

    server_conn = None
    forward_conn = None
    reconnect = False
    allow_writes = False
    allow_unsafe_boot_disk_writes = False
    verbose = False
    log_file = None
    processes = []

    i = 1
    while i < len(sys.argv):
        new_conn, consumed_args = parse_connection(sys.argv[i:])
        if new_conn:
            server_conn = new_conn
            i += consumed_args
            
        elif sys.argv[i] == "reconnect":
            reconnect = True
            i += 1
            
        elif sys.argv[i] in ["debug", "-v", "--verbose"]:
            verbose = True
            i += 1
            
        elif sys.argv[i] == "--allow-writes":
            allow_writes = True
            i += 1

        elif sys.argv[i] == "--allow-unsafe-boot-disk-writes":
            allow_unsafe_boot_disk_writes = True
            i += 1
            
        elif sys.argv[i] == "--log-file":
            i += 1
            if i < len(sys.argv) and not sys.argv[i].startswith("-") and sys.argv[i] not in ["forward", "elevate", "echo", "message", "tcpserver", "tcpclient", "stdiopipe", "namedpipeclient", "namedpipeserver", "reconnect"]:
                log_file = sys.argv[i]
                i += 1
            else:
                log_file = os.path.join(os.environ.get("LOCALAPPDATA", ""), "wsl-rawdisk", "server.log")
                
        elif sys.argv[i] == "forward":
            new_conn, consumed_args = parse_connection(sys.argv[i+1:])
            forward_conn = new_conn
            i += consumed_args + 1
            
        elif sys.argv[i] == "elevate":
            params += sys.argv[i+1:]
            p = shell.ShellExecuteEx(lpVerb='runas', lpFile=exe, lpParameters=' '.join(params), fMask=shellcon.SEE_MASK_NOCLOSEPROCESS)
            i = len(sys.argv)
            processes.append(p)
            
        elif sys.argv[i] == "echo":
            print(sys.argv[i+1], flush=True)
            i += 2
            
        elif sys.argv[i] == "message":
            win32ui.MessageBox(sys.argv[i+1], "wsl-rawdisk-server")
            i += 2
            
        else:
            raise Exception("Unknown command " + sys.argv[i])

    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        os.makedirs(os.path.dirname(log_file), exist_ok=True)
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(level=log_level, format=log_format, handlers=handlers)

    # Cache boot and pagefile drive indices on startup
    boot_indices = get_boot_and_pagefile_disk_indices()
    logger.info(f"Cached boot/pagefile drive indices: {boot_indices}")
    server_startup_time = time.time()

    if forward_conn is not None:
        assert forward_conn.connect()

    if server_conn is not None:
        while True:
            devices = []
            if not server_conn.connect():
                break
                
            logger.debug("connected")

            while True:
                try:
                    command = server_conn.unpack("B")
                    if command == Command.OPEN:
                        size, write_intent_byte = server_conn.unpack(FMT_OPEN_POST_CMD)
                        write_intent = (write_intent_byte != 0)
                        device_name = server_conn.recv(size).decode('utf-8')
                        logger.debug(f"recv cmd open {device_name} write_intent={write_intent}")
                        
                        if not is_valid_device_name(device_name):
                            logger.error(f"Refusing open request for invalid target string: {device_name}")
                            server_conn.pack("h", -1)
                            continue

                        is_allowed = True
                        read_only = True
                        
                        if write_intent:
                            drive_idx = get_physical_drive_index(device_name)
                            if drive_idx is not None and drive_idx in boot_indices:
                                elapsed = time.time() - server_startup_time
                                if allow_writes and allow_unsafe_boot_disk_writes and (elapsed < 300):
                                    logger.info(f"Allowing write open to boot/pagefile disk {device_name} (override enabled, elapsed: {elapsed:.1f}s)")
                                    read_only = False
                                else:
                                    if elapsed >= 300:
                                        logger.error(f"Refusing write open to boot/pagefile disk {device_name}. Override flag expired (elapsed: {elapsed:.1f}s). Restart server to re-enable.")
                                    else:
                                        logger.error(f"Refusing write open to boot/pagefile disk {device_name}. Override flag --allow-unsafe-boot-disk-writes is required.")
                                    is_allowed = False
                            else:
                                if allow_writes:
                                    read_only = False
                                else:
                                    logger.error(f"Refusing write open to {device_name}. Server --allow-writes is required.")
                                    is_allowed = False
                        else:
                            read_only = True

                        if not is_allowed:
                            server_conn.pack("h", -1)
                        else:
                            if forward_conn is None:
                                device = Device(device_name, read_only=read_only)
                                open_success = device.open()
                            else:
                                device = ConnectedDevice(forward_conn, device_name)
                                open_success = device.open(write_intent=write_intent)

                            if open_success:
                                server_conn.pack("h", len(devices))
                                devices.append(device)
                            else:
                                server_conn.pack("h", -1)

                    elif command == Command.READ:
                        index, pos, size = server_conn.unpack(FMT_READ_WRITE_POST_CMD)
                        if index < len(devices):
                            data = devices[index].read(pos, size)
                        else:
                            logger.error("index out of range")
                            data = None

                        if data is None:
                            server_conn.pack("B", 1)
                        else:
                            server_conn.pack("B", 0)
                            server_conn.send(data)

                    elif command == Command.WRITE:
                        index, pos, size = server_conn.unpack(FMT_READ_WRITE_POST_CMD)
                        data = server_conn.recv(size)
                        assert len(data) == size
                        if index < len(devices):
                            res = devices[index].write(pos, data)
                        else:
                            res = False

                        server_conn.pack("B", 0 if res else 1)

                    elif command == Command.GET_SIZE:
                        index = server_conn.unpack(FMT_GET_SIZE_POST_CMD)
                        if index < len(devices):
                            server_conn.pack("Q", devices[index].size)
                        else:
                            logger.error("index out of range")
                            server_conn.pack("Q", 0)

                    elif command == Command.GET_DISKDRIVES:
                        drives = [disk.Name for disk in wmi.WMI().query("SELECT * from Win32_DiskDrive")]
                        drives_bytes = json.dumps(drives).encode("utf-8")
                        server_conn.pack("I", len(drives_bytes))
                        server_conn.send(drives_bytes)

                    elif command == Command.CLOSE:
                        if forward_conn is not None:
                            forward_conn.pack("b", Command.CLOSE)
                        break

                    else:
                        logger.error(f"unknown command {command}")

                except ConnectionError as e:
                    logger.debug(f"Connection error/closed: {e}")
                    break

            for d in devices:
                d.close()

            if not reconnect:
                break

    for p in processes:
        try:
            win32event.WaitForSingleObject(p['hProcess'], -1)
        except:
            pass

if __name__ == '__main__':
    main()
