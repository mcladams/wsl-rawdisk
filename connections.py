import socket
import struct
import logging
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)

class Connection:
    def __enter__(self) -> 'Connection':
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def connect(self) -> bool:
        return True

    def send(self, data: bytes) -> None:
        raise ConnectionError("send not implemented")

    def recv(self, size: int) -> Union[bytearray, bytes]:
        raise ConnectionError("recv not implemented")

    def close(self) -> None:
        pass

    def unpack(self, fmt: str) -> Any:
        size = struct.calcsize(fmt)
        data = self.recv(size)
        values = struct.unpack(fmt, data)
        if len(values) == 1:
            return values[0]
        return values

    def pack(self, fmt: str, *values: Any) -> None:
        data = struct.pack(fmt, *values)
        self.send(data)


class TcpServer(Connection):
    def __init__(self, host: str = '0.0.0.0', port: int = 50000):
        try:
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except socket.error as e:
            logger.error(f"Failed to create socket: {e}")
            raise Exception(f"Failed to create socket: {e}") from e

        self.s.bind((host, port))
        self.s.listen()
        self.conn: Optional[socket.socket] = None
        self.port: int = self.s.getsockname()[1]

    def connect(self) -> bool:
        try:
            self.conn, _ = self.s.accept()
            return True
        except socket.error as e:
            logger.error(f"Failed to accept connection: {e}")
            return False

    def recv(self, n: int) -> bytearray:
        if not self.conn:
            raise ConnectionError("Not connected")
        buff = bytearray(n)
        pos = 0
        while pos < n:
            try:
                cr = self.conn.recv_into(memoryview(buff)[pos:])
            except socket.error as e:
                raise ConnectionError(f"TcpServer recv failed: {e}") from e
                
            if cr == 0:
                raise ConnectionError("TcpServer stream closed unexpectedly.")
            pos += cr
        return buff

    def send(self, data: bytes) -> None:
        if not self.conn:
            raise ConnectionError("Not connected")
        try:
            self.conn.sendall(data)
        except socket.error as e:
            raise ConnectionError(f"TcpServer send failed: {e}") from e

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None
        if self.s:
            self.s.close()


class TcpClientConnection(Connection):
    def __init__(self, host: str, port: int = 50000):
        self.host: str = host
        self.port: int = port
        self.s: Optional[socket.socket] = None

    def connect(self) -> bool:
        try:
            self.s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.s.connect((self.host, self.port))
            return True
        except socket.error as e:
            logger.error(f"Failed to connect to {self.host}:{self.port}: {e}")
            if self.s:
                self.s.close()
            return False

    def send(self, data: bytes) -> None:
        if not self.s:
            raise ConnectionError("Not connected")
        try:
            self.s.sendall(data)
        except socket.error as e:
            raise ConnectionError(f"TcpClient send failed: {e}") from e

    def recv(self, n: int) -> bytearray:
        if not self.s:
            raise ConnectionError("Not connected")
        buff = bytearray(n)
        pos = 0
        while pos < n:
            try:
                cr = self.s.recv_into(memoryview(buff)[pos:])
            except socket.error as e:
                raise ConnectionError(f"TcpClient recv failed: {e}") from e
                
            if cr == 0:
                logger.error("tcp client recv 0 error")
                raise ConnectionError("TcpClient stream closed unexpectedly.")
            pos += cr
        return buff

    def close(self) -> None:
        if self.s:
            self.s.close()
            self.s = None
