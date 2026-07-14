import sys
import unittest
from unittest.mock import patch, MagicMock
import struct
import importlib
import time

# Load the hyphenated server module
wsl_rawdisk_server = importlib.import_module("wsl-rawdisk-server")

def make_open_request(device_name: str, write_intent: bool) -> bytes:
    name_bytes = device_name.encode('utf-8')
    header = struct.pack("=BHB", 1, len(name_bytes), 1 if write_intent else 0)
    return header + name_bytes

def make_close_request() -> bytes:
    return struct.pack("B", 6)

class MockServerConnection:
    def __init__(self, data_to_recv):
        self.data_to_recv = bytearray(data_to_recv)
        self.sent_data = bytearray()
        self.connect_calls = 0

    def connect(self) -> bool:
        self.connect_calls += 1
        return self.connect_calls == 1

    def recv(self, size):
        if len(self.data_to_recv) < size:
            raise ConnectionError("EOF")
        res = self.data_to_recv[:size]
        del self.data_to_recv[:size]
        return res

    def send(self, data):
        self.sent_data.extend(data)

    def pack(self, fmt, *values):
        data = struct.pack(fmt, *values)
        self.send(data)

    def unpack(self, fmt):
        size = struct.calcsize(fmt)
        data = self.recv(size)
        values = struct.unpack(fmt, data)
        if len(values) == 1:
            return values[0]
        return values

    def close(self):
        pass

class TestSafetyArchitecture(unittest.TestCase):
    def test_per_request_write_authorization(self):
        # 1. Setup mocks using patch.object
        with patch.object(wsl_rawdisk_server, 'get_boot_and_pagefile_disk_indices') as mock_get_boot, \
             patch.object(wsl_rawdisk_server, 'Device') as mock_device_class:
             
            mock_get_boot.return_value = {0} # PHYSICALDRIVE0 is boot disk
            
            mock_device_instance = MagicMock()
            mock_device_instance.open.return_value = True
            mock_device_instance.size = 1024 * 1024
            mock_device_class.return_value = mock_device_instance

            # 2. Setup mock client connection
            # Issue 1: Open drive 1 (non-boot) read-only
            # Issue 2: Open drive 2 (non-boot) write-intent
            # Issue 3: Close
            payload = (
                make_open_request("\\\\.\\PHYSICALDRIVE1", False) +
                make_open_request("\\\\.\\PHYSICALDRIVE2", True) +
                make_close_request()
            )
            mock_conn = MockServerConnection(payload)

            # Patch parse_connection to only return the mock connection for connection args
            def mock_parse(args):
                if len(args) > 0 and args[0] in ("tcpserver", "tcpclient"):
                    return mock_conn, 3
                return None, 0

            # Patch sys.argv to specify tcpserver and enable writes globally
            test_args = ["wsl-rawdisk-server.py", "tcpserver", "127.0.0.1", "50000", "--allow-writes"]
            
            with patch.object(sys, 'argv', test_args), \
                 patch.object(wsl_rawdisk_server, 'parse_connection', side_effect=mock_parse):
                wsl_rawdisk_server.main()

            # 3. Verify Device constructor calls in the same session
            self.assertEqual(mock_device_class.call_count, 2)
            
            # Verify read-only open
            first_call = mock_device_class.call_args_list[0]
            self.assertEqual(first_call[0][0], "\\\\.\\PHYSICALDRIVE1")
            self.assertEqual(first_call[1].get('read_only'), True)

            # Verify write-intent open
            second_call = mock_device_class.call_args_list[1]
            self.assertEqual(second_call[0][0], "\\\\.\\PHYSICALDRIVE2")
            self.assertEqual(second_call[1].get('read_only'), False)

            # Verify response from server: two success indexes (0 and 1)
            # Each index is packed as "h" (2 bytes)
            res_idx1 = struct.unpack("h", mock_conn.sent_data[0:2])[0]
            res_idx2 = struct.unpack("h", mock_conn.sent_data[2:4])[0]
            self.assertEqual(res_idx1, 0)
            self.assertEqual(res_idx2, 1)

    def test_boot_disk_write_refusal_by_default(self):
        with patch.object(wsl_rawdisk_server, 'get_boot_and_pagefile_disk_indices') as mock_get_boot, \
             patch.object(wsl_rawdisk_server, 'Device') as mock_device_class:
             
            mock_get_boot.return_value = {0}
            
            mock_device_instance = MagicMock()
            mock_device_instance.open.return_value = True
            mock_device_class.return_value = mock_device_instance

            # Client requests write-intent on the boot drive (PHYSICALDRIVE0)
            payload = (
                make_open_request("\\\\.\\PHYSICALDRIVE0", True) +
                make_close_request()
            )
            mock_conn = MockServerConnection(payload)

            def mock_parse(args):
                if len(args) > 0 and args[0] in ("tcpserver", "tcpclient"):
                    return mock_conn, 3
                return None, 0

            # Run with general writes allowed, but no boot override
            test_args = ["wsl-rawdisk-server.py", "tcpserver", "127.0.0.1", "50000", "--allow-writes"]
            
            with patch.object(sys, 'argv', test_args), \
                 patch.object(wsl_rawdisk_server, 'parse_connection', side_effect=mock_parse):
                wsl_rawdisk_server.main()

            # The device should NEVER be instantiated for write-open
            mock_device_class.assert_not_called()

            # The server response should be -1 (refused)
            res_idx = struct.unpack("h", mock_conn.sent_data[0:2])[0]
            self.assertEqual(res_idx, -1)

    def test_boot_disk_write_allowed_with_override(self):
        with patch.object(wsl_rawdisk_server, 'get_boot_and_pagefile_disk_indices') as mock_get_boot, \
             patch.object(wsl_rawdisk_server, 'Device') as mock_device_class:
             
            mock_get_boot.return_value = {0}
            
            mock_device_instance = MagicMock()
            mock_device_instance.open.return_value = True
            mock_device_instance.size = 2048 * 2048
            mock_device_class.return_value = mock_device_instance

            # Client requests write-intent on the boot drive
            payload = (
                make_open_request("\\\\.\\PHYSICALDRIVE0", True) +
                make_close_request()
            )
            mock_conn = MockServerConnection(payload)

            def mock_parse(args):
                if len(args) > 0 and args[0] in ("tcpserver", "tcpclient"):
                    return mock_conn, 3
                return None, 0

            # Run with general writes allowed AND the specific boot disk write override
            test_args = [
                "wsl-rawdisk-server.py", "tcpserver", "127.0.0.1", "50000", 
                "--allow-writes", "--allow-unsafe-boot-disk-writes"
            ]
            
            with patch.object(sys, 'argv', test_args), \
                 patch.object(wsl_rawdisk_server, 'parse_connection', side_effect=mock_parse):
                wsl_rawdisk_server.main()

            # Device should be created with read_only=False
            mock_device_class.assert_called_once_with("\\\\.\\PHYSICALDRIVE0", read_only=False)

            # Response should be a success index (0)
            res_idx = struct.unpack("h", mock_conn.sent_data[0:2])[0]
            self.assertEqual(res_idx, 0)

    def test_invalid_target_string_refused(self):
        with patch.object(wsl_rawdisk_server, 'get_boot_and_pagefile_disk_indices') as mock_get_boot, \
             patch.object(wsl_rawdisk_server, 'Device') as mock_device_class:
             
            mock_get_boot.return_value = {0}
            mock_device_instance = MagicMock()
            mock_device_instance.open.return_value = True
            mock_device_class.return_value = mock_device_instance

            # Client requests write-intent on an invalid string: \\.\C:
            payload = (
                make_open_request("\\\\.\\C:", True) +
                make_close_request()
            )
            mock_conn = MockServerConnection(payload)

            def mock_parse(args):
                if len(args) > 0 and args[0] in ("tcpserver", "tcpclient"):
                    return mock_conn, 3
                return None, 0

            test_args = ["wsl-rawdisk-server.py", "tcpserver", "127.0.0.1", "50000", "--allow-writes"]
            
            with patch.object(sys, 'argv', test_args), \
                 patch.object(wsl_rawdisk_server, 'parse_connection', side_effect=mock_parse):
                wsl_rawdisk_server.main()

            # The device should NEVER be instantiated
            mock_device_class.assert_not_called()
            # The server response should be -1
            res_idx = struct.unpack("h", mock_conn.sent_data[0:2])[0]
            self.assertEqual(res_idx, -1)

    def test_boot_disk_override_expiration(self):
        with patch.object(wsl_rawdisk_server, 'get_boot_and_pagefile_disk_indices') as mock_get_boot, \
             patch.object(wsl_rawdisk_server, 'Device') as mock_device_class:
             
            mock_get_boot.return_value = {0}
            
            mock_device_instance = MagicMock()
            mock_device_instance.open.return_value = True
            mock_device_class.return_value = mock_device_instance

            # Client requests write-intent on the boot drive (PHYSICALDRIVE0)
            payload = (
                make_open_request("\\\\.\\PHYSICALDRIVE0", True) +
                make_close_request()
            )
            mock_conn = MockServerConnection(payload)

            def mock_parse(args):
                if len(args) > 0 and args[0] in ("tcpserver", "tcpclient"):
                    return mock_conn, 3
                return None, 0

            # Mock time.time to simulate expiration
            # Let startup time be time 100, and query time be time 500 (elapsed = 400 > 300)
            time_values = [100.0, 500.0]
            def mock_time():
                if time_values:
                    return time_values.pop(0)
                return 500.0

            # Run with general writes allowed AND the specific boot disk write override
            test_args = [
                "wsl-rawdisk-server.py", "tcpserver", "127.0.0.1", "50000", 
                "--allow-writes", "--allow-unsafe-boot-disk-writes"
            ]
            
            with patch.object(sys, 'argv', test_args), \
                 patch.object(wsl_rawdisk_server, 'parse_connection', side_effect=mock_parse), \
                 patch('time.time', side_effect=mock_time):
                wsl_rawdisk_server.main()

            # The device should NEVER be instantiated because override has expired
            mock_device_class.assert_not_called()
            # Response should be -1
            res_idx = struct.unpack("h", mock_conn.sent_data[0:2])[0]
            self.assertEqual(res_idx, -1)

if __name__ == '__main__':
    unittest.main()
