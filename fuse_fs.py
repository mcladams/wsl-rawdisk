import pyfuse3
import errno
import stat
import time
import asyncio
import logging
import os

logger = logging.getLogger(__name__)

class FS(pyfuse3.Operations):
    def __init__(self, devices):
        super().__init__()
        self.devices = devices
        self.files = {}
        self.fd = 0
        
        self.inode_map = {pyfuse3.ROOT_INODE: '/'}
        self.path_map = {'/': pyfuse3.ROOT_INODE}
        self.next_inode = pyfuse3.ROOT_INODE + 1

        for name in devices:
            self.inode_map[self.next_inode] = '/' + name
            self.path_map['/' + name] = self.next_inode
            self.next_inode += 1

    async def getattr(self, inode, ctx=None):
        logger.info(f"getattr called for inode {inode}")
        if inode not in self.inode_map:
            raise pyfuse3.FUSEError(errno.ENOENT)
        
        path = self.inode_map[inode]
        entry = pyfuse3.EntryAttributes()
        now = int(time.time() * 1e9)
        
        entry.st_ino = inode
        entry.generation = 0
        entry.entry_timeout = 300
        entry.attr_timeout = 300
        
        if path == '/':
            entry.st_mode = stat.S_IFDIR | 0o755
            entry.st_nlink = 2
            entry.st_size = 0
        else:
            name = path[1:]
            device = self.devices[name]
            entry.st_mode = stat.S_IFREG | 0o755
            entry.st_nlink = 1
            entry.st_size = device.size
            
        entry.st_atime_ns = now
        entry.st_ctime_ns = now
        entry.st_mtime_ns = now
        entry.st_gid = os.getgid()
        entry.st_uid = os.getuid()
        
        return entry

    async def lookup(self, parent_inode, name, ctx=None):
        name_str = name.decode('utf-8')
        logger.info(f"lookup called for parent {parent_inode}, name {name_str}")
        if parent_inode != pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOENT)
        
        path = '/' + name_str
        if path in self.path_map:
            return await self.getattr(self.path_map[path], ctx)
        
        raise pyfuse3.FUSEError(errno.ENOENT)

    async def opendir(self, inode, ctx):
        if inode != pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOENT)
        return inode

    async def readdir(self, fh, off, token):
        if fh != pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.ENOENT)
        
        entries = []
        for name in self.devices:
            entries.append((name.encode('utf-8'), self.path_map['/' + name]))
            
        for i, (name, child_inode) in enumerate(entries[off:]):
            attr = await self.getattr(child_inode)
            if not pyfuse3.readdir_reply(token, name, attr, off + i + 1):
                break

    async def open(self, inode, flags, ctx):
        if inode not in self.inode_map or self.inode_map[inode] == '/':
            raise pyfuse3.FUSEError(errno.ENOENT)
            
        self.fd += 1
        self.files[self.fd] = inode
        return pyfuse3.FileInfo(fh=self.fd)

    async def read(self, fh, off, size):
        logger.info(f"read called: fh={fh}, off={off}, size={size}")
        if fh not in self.files:
            raise pyfuse3.FUSEError(errno.EBADF)
        inode = self.files[fh]
        if inode == pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.EISDIR) # Safeguard against directory reads

        path = self.inode_map[inode]
        name = path[1:]
        device = self.devices[name]
        
        try:
            data = await asyncio.wait_for(device.read(off, size), timeout=15.0)
            if data is None:
                raise pyfuse3.FUSEError(errno.EIO)
            return bytes(data)
        except asyncio.TimeoutError:
            logger.error(f"Read timeout on device {name} at offset {off}")
            raise pyfuse3.FUSEError(errno.ETIMEDOUT)
        except ConnectionError as e:
            logger.error(f"Connection error on read: {e}")
            raise pyfuse3.FUSEError(errno.EIO)
        except Exception as e:
            logger.error(f"Unexpected error on read: {e}")
            raise pyfuse3.FUSEError(errno.EIO)

    async def write(self, fh, off, buf):
        logger.info(f"write called: fh={fh}, off={off}, len={len(buf)}")
        if fh not in self.files:
            raise pyfuse3.FUSEError(errno.EBADF)
        inode = self.files[fh]
        if inode == pyfuse3.ROOT_INODE:
            raise pyfuse3.FUSEError(errno.EISDIR) # Safeguard against directory reads

        path = self.inode_map[inode]
        name = path[1:]
        device = self.devices[name]
        
        try:
            success = await asyncio.wait_for(device.write(off, buf), timeout=15.0)
            if success:
                return len(buf)
            else:
                raise pyfuse3.FUSEError(errno.EACCES)
        except asyncio.TimeoutError:
            logger.error(f"Write timeout on device {name} at offset {off}")
            raise pyfuse3.FUSEError(errno.ETIMEDOUT)
        except ConnectionError as e:
            logger.error(f"Connection error on write: {e}")
            raise pyfuse3.FUSEError(errno.EIO)
        except Exception as e:
            logger.error(f"Unexpected error on write: {e}")
            raise pyfuse3.FUSEError(errno.EIO)
