import sys
import os
import win32ui
import win32event
import json
import logging
import wmi  # type: ignore
import win32com.shell.shell as shell
from win32com.shell import shellcon

import connections
from device import Device
from connected_device import ConnectedDevice
from protocol import Command

logger = logging.getLogger(__name__)

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
                        size = server_conn.unpack("H")
                        device_name = server_conn.recv(size).decode('utf-8')
                        logger.debug(f"recv cmd open {device_name}")
                        
                        if forward_conn is None:
                            device = Device(device_name, read_only=not allow_writes)
                        else:
                            device = ConnectedDevice(forward_conn, device_name)

                        if device.open():
                            server_conn.pack("h", len(devices))
                            devices.append(device)
                        else:
                            server_conn.pack("h", -1)

                    elif command == Command.READ:
                        index, pos, size = server_conn.unpack("=H2Q")
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
                        index, pos, size = server_conn.unpack("=H2Q")
                        data = server_conn.recv(size)
                        assert len(data) == size
                        if index < len(devices):
                            res = devices[index].write(pos, data)
                        else:
                            res = False

                        server_conn.pack("B", 0 if res else 1)

                    elif command == Command.GET_SIZE:
                        index = server_conn.unpack("H")
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
