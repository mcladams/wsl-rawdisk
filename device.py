import win32con
import win32file
import winioctlcon
import pywintypes
import struct
import logging
from typing import Optional, Dict, Any, Union

logger = logging.getLogger(__name__)

class Device:
    def __init__(self, devicename: str, read_only: bool = True):
        self.devicename: str = devicename
        self.handle: Any = None
        self.read_only: bool = read_only
        self.sector_size: int = 0
        self.size: int = 0

    def __enter__(self) -> 'Device':
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def open(self) -> bool:
        access_mode = win32con.GENERIC_READ
        share_mode = win32con.FILE_SHARE_READ
        
        if not self.read_only:
            access_mode |= win32con.GENERIC_WRITE
            share_mode |= win32con.FILE_SHARE_WRITE

        try:
            self.handle = win32file.CreateFile(
                self.devicename,
                access_mode,
                share_mode,
                None,
                win32con.OPEN_EXISTING,
                win32con.FILE_ATTRIBUTE_NORMAL,
                None
            )
        except pywintypes.error as e:
            logger.error(f"Failed to obtain device handle for {self.devicename}: {e}")
            return False

        if self.handle == win32file.INVALID_HANDLE_VALUE:
            logger.error(f"Invalid handle value for {self.devicename}")
            return False

        self.sector_size = int(self.get_geometry()['BytesPerSector'])
        self.size = self.get_size()

        return True

    def close(self) -> None:
        if self.handle is not None:
            win32file.CloseHandle(self.handle)
            self.handle = None

    def get_geometry(self) -> Dict[str, Union[int, str]]:
        res = struct.unpack("QLLLLQb", win32file.DeviceIoControl(
            self.handle,  # handle
            winioctlcon.IOCTL_DISK_GET_DRIVE_GEOMETRY_EX,  # ioctl api
            b"",  # in buffer
            33  # out buffer
        ))
        return dict(zip(["Cylinders", "MediaType", "TracksPerCylinder", "SectorsPerTrack", "BytesPerSector", "DiskSize", "ExtraData"], res))

    def get_size(self) -> int:
        iRes = win32file.DeviceIoControl(
            self.handle,
            winioctlcon.IOCTL_DISK_GET_LENGTH_INFO,
            None,
            8)
        return int.from_bytes(iRes, 'little')

    def read(self, pos: int, size: int) -> Optional[bytes]:
        # 1. Catch EOF reads immediately
        if pos >= self.size:
            return b''

        offset = pos % self.sector_size
        pos -= offset
        extra = (self.sector_size - (size + offset)) % self.sector_size
        total_size = size + offset + extra

        # 2. Clamp the total_size to the physical disk boundary to prevent Error 87
        if pos + total_size > self.size:
            # Drop the extra padding if it pushes us off the disk
            total_size = self.size - pos
            # Ensure it is still sector-aligned by rounding down
            total_size = (total_size // self.sector_size) * self.sector_size
            if total_size == 0:
                return b''

        # 3. Native 64-bit offsets (Fixes 32-bit SetFilePointer truncation)
        overlapped = pywintypes.OVERLAPPED()
        overlapped.Offset = pos & 0xFFFFFFFF
        overlapped.OffsetHigh = pos >> 32
        # OLD behavior: win32file.SetFilePointer(self.handle, pos, win32file.FILE_BEGIN)
        try:
            # Pass the overlapped struct directly to ReadFile
            res, data = win32file.ReadFile(self.handle, total_size, overlapped)
        except pywintypes.error as e:
            logger.error(f"Read error at pos {pos}: {e}")
            return None

        if res != 0:
            logger.error(f"An error occurred reading: res={res}")
            return None

        if len(data) != total_size:
            logger.error(f"Read {total_size - len(data)} less bytes than requested...")
            return None
            
        return data[offset:offset + size]

    def write(self, pos: int, data: bytes) -> bool:
        if self.read_only:
            logger.error("Attempted to write to a read-only device.")
            return False

        offset = pos % self.sector_size
        if offset != 0:
            logger.error(f"Misaligned write: position {pos} is not aligned to sector size {self.sector_size}")
            return False
        pos -= offset
        
        overlapped = pywintypes.OVERLAPPED()
        overlapped.Offset = pos & 0xFFFFFFFF
        overlapped.OffsetHigh = pos >> 32

        # OLD behaviour: win32file.SetFilePointer(self.handle, pos, win32file.FILE_BEGIN)
        try:
            res = win32file.WriteFile(self.handle, data, overlapped)
            # WriteFile with OVERLAPPED returns a tuple: (error_code, bytes_written)
            if res[0] != 0 or res[1] != len(data):
                logger.error(f"Write failed: code {res[0]}, bytes {res[1]}")
                return False
        except pywintypes.error as e:
            logger.error(f"Write API failed at pos {pos}, len {len(data)}: {e}")
            return False

        return True
