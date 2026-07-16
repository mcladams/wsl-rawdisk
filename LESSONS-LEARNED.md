  ### 🧠 Harvested Lessons Learned - Agy.exe (antigravity-cli agent)

  #### 1. FUSE API Callback Conventions ( pyfuse3 )

  • Finding: Once a file handle is opened, subsequent read, write, and release operations in FUSE
  receive the file handle ( fh ) as their first argument, not the  inode .
  • Impact: Attempting to treat  fh  as an  inode  leads to dynamic file resolution issues. In our
  case,  fh = 1  was erroneously resolved as the root directory  inode = 1 , raising a false  EISDIR
  error and failing the  dd  test.
  • Best Practice: In FUSE filesystems, always map  fh -> inode  in  open()  and resolve it in
  read()  and  write()  to ensure correct path routing.

  #### 2. EOF Protocol Desynchronization

  • Finding: Kernel and block-level tools (like partition scanners or  dd ) often issue page-aligned
  read requests (e.g. 4096 bytes) that extend beyond the physical boundaries of the device. The
  Windows server handles this gracefully by truncating the read and returning only the available
  bytes (e.g. 512 bytes).
  • Impact: The client's  AsyncConnectedDevice  was calling  readexactly(size)  expecting all 4096
  bytes. Because the server only sent 512, the client's socket hung indefinitely waiting for the
  remaining bytes, triggering an  EIO  error in FUSE.
  • Best Practice: For cross-boundary raw block device proxying, the server must pad truncated EOF
  reads with null bytes ( \x00 ) to match the client's expected size, ensuring the stream remains
  synchronized.

  #### 3. FUSE Loop Device Deadlocks

  • Finding: Mounting a loop device with  --direct-io=on  on top of a FUSE mountpoint can trigger
  uninterruptible kernel sleeps ( D+  state) if loop attachment is executed synchronously within the
  FUSE daemon's event loop thread. The partition scanner ( -P ) locks the device while FUSE is
  waiting on the scanner to finish, causing a circular wait.
  • Best Practice: Run all loop attachment commands ( losetup ) asynchronously inside a thread pool
  executor ( loop.run_in_executor ) to keep the main event loop thread responsive. Remove the  -P
  partition scanning flag from the daemon's setup phase to prevent the kernel block subsystem from
  locking the FUSE driver.

  #### 4. WSL/Windows Host Git Ownership Boundary

  • Finding: Running Git commands inside WSL (e.g.  wsl -u root ) on a mounted Windows directory (
  /mnt/c/... ) causes ownership mismatches. Linux Git detects different Windows owner SIDs and
  raises fatal  dubious ownership  errors, breaking automated test scripts.
  • Best Practice: Run all Git and workspace-modifying commands natively on the host OS shell
  (Windows PowerShell). Reserve the  wsl  tool strictly for triggering boundary tests and target
  guest runtimes.
  ──────
  ### 🚀 Best Practice Checklist for Future Sessions

  • Use the  pyproject.toml  and  uv  Workflow:
      • Windows:  uv sync --extra server --extra dev  to install host-side packages.
      • Linux/WSL:  uv sync --extra client --extra dev  to install guest-side packages.
      • Run entry points directly:  uv run wsl-rawdisk-server  and  uv run wsl-rawdisk .
  • Testing Command Protocol:
      • Run unit tests natively:  pytest tests/test_safety.py -v .
      • Trigger smoke tests across boundaries:
          1. Start mock server:  uv run wsl-rawdisk-server tcpserver 0.0.0.0 50000 reconnect --
          allow-writes --bind-any
          2. Run client test suite in WSL:  wsl -d Ubuntu-26.04 -u root bash -c "cd
          /mnt/c/Users/Mike/src/wsl-rawdisk && PYTHONPATH=src python3 -u tests/smoke_test_client.py"
          3. Terminate host server processes when done.