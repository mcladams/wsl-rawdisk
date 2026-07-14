from wsl_rawdisk.protocol import (
    Command,
    FMT_OPEN,
    FMT_READ_WRITE,
    FMT_GET_SIZE,
    FMT_REPLY_BYTE,
    FMT_REPLY_SHORT,
    FMT_REPLY_QWORD
)
from typing import Any

class ConnectedDevice:
    def __init__(self, conn: Any, device_name: str):
        self.conn: Any = conn
        self.device_name: str = device_name
        self.index: int = -1
        self.size: int = 0
        self.filename: str = ""
        self.loop_dev: Any = None

    def open(self, write_intent: bool = False) -> bool:
        device_name_bytes = self.device_name.encode('utf-8')
        self.conn.pack(FMT_OPEN, Command.OPEN, len(device_name_bytes), 1 if write_intent else 0)
        self.conn.send(device_name_bytes)
        self.index = self.conn.unpack(FMT_REPLY_SHORT)
        if self.index != -1:
            self.size = self.get_size()
        return self.index != -1

    def read(self, pos: int, size: int) -> bytes:
       self.conn.pack(FMT_READ_WRITE, Command.READ, self.index, pos, size)
       status = self.conn.unpack(FMT_REPLY_BYTE)
       if status == 0:
           data = self.conn.recv(size)
       else:
           data = b''
       return data

    def write(self, pos: int, data: bytes) -> bool:
       self.conn.pack(FMT_READ_WRITE, Command.WRITE, self.index, pos, len(data))
       self.conn.send(data)
       return self.conn.unpack(FMT_REPLY_BYTE) == 0

    def get_size(self) -> int:
        self.conn.pack(FMT_GET_SIZE, Command.GET_SIZE, self.index)
        return self.conn.unpack(FMT_REPLY_QWORD)

    def close(self) -> None:
        if hasattr(self.conn, 'close'):
            self.conn.close()