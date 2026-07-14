import subprocess
import time
import sys
import os
import struct
import json
import socket

# Helpers
def get_wsl_host_ip():
    # Since localhost forwarding is enabled and mirrored mode is common, 127.0.0.1 connects to Windows host.
    return '127.0.0.1'

def test_boot_disk_refusal(host):
    print("Testing boot disk write refusal...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((host, 50000))
    
    # Send Command.OPEN (1)
    # payload: size (H), write_intent_byte (B), device_name (utf-8)
    dev_name = "\\\\.\\PHYSICALDRIVE0"
    name_bytes = dev_name.encode('utf-8')
    header = struct.pack("=BHB", 1, len(name_bytes), 1) # cmd=1, size=len(name_bytes), write_intent=1
    s.sendall(header + name_bytes)
    
    # Receive response (short, 2 bytes)
    res_bytes = s.recv(2)
    res_idx = struct.unpack("=h", res_bytes)[0]
    print(f"Server response index for boot drive write-open: {res_idx}")
    
    # Close connection
    s.sendall(struct.pack("=B", 6)) # CLOSE (6)
    s.close()
    
    if res_idx == -1:
        print("✅ Gate 0 boot disk write refusal verified successfully!")
        return True
    else:
        print("❌ Gate 0 boot disk write refusal FAILED!")
        return False

def run_full_stack_smoke_test(host):
    print("Starting full stack mount test...")
    # Mount PHYSICALDRIVE99 which maps to test_device.img
    # Run wsl-rawdisk.py as a background subprocess using sudo
    # Since we run with sudo, make sure python is run with buffering disabled (-u)
    proc = subprocess.Popen(
        ["python3", "-u", "-m", "wsl_rawdisk.client", "99", "--allow-writes"],
        stdout=None,
        stderr=None,
        text=True
    )
    
    mountpoint = None
    loop_dev = None
    
    # Wait for the mount and loop device mapping to be logged
    start_time = time.time()
    while time.time() - start_time < 15:
        # Check if process died
        if proc.poll() is not None:
            print("❌ client process died prematurely!")
            stdout, stderr = proc.communicate()
            print("STDOUT:", stdout)
            print("STDERR:", stderr)
            return False
            
        # Check loop device mapping via losetup -a
        res = subprocess.run(["losetup", "-a"], capture_output=True, text=True)
        if "physicaldrive99" in res.stdout:
            # Found it! e.g., /dev/loop0: [.../physicaldrive99]
            for line in res.stdout.splitlines():
                if "physicaldrive99" in line:
                    loop_dev = line.split(":")[0].strip()
                    # Extract mountpoint path
                    # e.g., /dev/loop0: [0057]:2456488 (/tmp/wsl_rawdisk_abc/physicaldrive99)
                    if "(" in line:
                        path_in_parentheses = line.split("(")[-1].split(")")[0]
                        mountpoint = os.path.dirname(path_in_parentheses)
            break
        time.sleep(0.5)

    if not loop_dev or not mountpoint:
        print("❌ Did not detect loop device mapping in 15 seconds!")
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
            print("Client STDOUT:\n", stdout)
            print("Client STDERR:\n", stderr)
        except Exception as e:
            print("Failed to read client output:", e)
        return False
        
    print(f"✅ Found loop device: {loop_dev} backing file at mountpoint: {mountpoint}")
    
    # 1. Read first 512 bytes of loop device and verify it has SMOKE_TEST_PATTERN
    try:
        print(f"Reading from loop device: {loop_dev}")
        res = subprocess.run(
            ["dd", f"if={loop_dev}", "bs=512", "count=1"],
            capture_output=True
        )
        if res.returncode != 0:
            print(f"❌ dd read from loop device failed: {res.stderr.decode()}")
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=2.0)
                print("Client STDOUT:\n", stdout)
                print("Client STDERR:\n", stderr)
            except Exception as e:
                print("Failed to read client output:", e)
            return False
        data = res.stdout
        print(f"Read data (first 30 bytes): {data[:30]}")
        if not data.startswith(b"SMOKE_TEST_PATTERN"):
            print(f"❌ Pattern mismatch! Expected to start with b'SMOKE_TEST_PATTERN', got {data[:30]}")
            proc.terminate()
            return False
            
        print("✅ Initial pattern verified successfully!")
        
        # 2. Write new data to the loop device using dd
        print(f"Writing to loop device {loop_dev}...")
        new_pattern = b"MODIFIED_BY_SMOKE_TEST"
        write_data = new_pattern.ljust(512, b"\x00")
        # Write to loop device
        res = subprocess.run(
            ["dd", f"of={loop_dev}", "bs=512", "count=1", "conv=notrunc"],
            input=write_data,
            capture_output=True
        )
        if res.returncode != 0:
            print(f"❌ dd write to loop device failed: {res.stderr.decode()}")
            proc.terminate()
            try:
                stdout, stderr = proc.communicate(timeout=2.0)
                print("Client STDOUT:\n", stdout)
                print("Client STDERR:\n", stderr)
            except Exception as e:
                print("Failed to read client output:", e)
            return False
            
        print("✅ Wrote to loop device successfully.")
        
        # 3. Read back from loop device to confirm the write worked
        res = subprocess.run(
            ["dd", f"if={loop_dev}", "bs=512", "count=1"],
            capture_output=True
        )
        if res.returncode != 0:
            print(f"❌ dd read back from loop device failed: {res.stderr.decode()}")
            proc.terminate()
            return False
        read_back = res.stdout
        print(f"Read back from loop device: {read_back[:30]}")
        if not read_back.startswith(new_pattern):
            print(f"❌ Readback mismatch! Expected to start with {new_pattern}, got {read_back[:30]}")
            proc.terminate()
            return False
            
        print("✅ Readback from loop device verified successfully!")
        
    except Exception as e:
        print(f"❌ Error during mount test: {e}")
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=2.0)
            print("Client STDOUT:\n", stdout)
            print("Client STDERR:\n", stderr)
        except Exception as ex:
            print("Failed to read client output:", ex)
        return False
        
    # Clean up the background client process
    print("Terminating client process...")
    # Send SIGINT (Ctrl+C) to wsl-rawdisk.py to trigger its cleanup
    # We must use sudo kill since it runs as root
    subprocess.run(["kill", "-INT", str(proc.pid)])
    
    # Wait for process to clean up
    try:
        proc.wait(timeout=5)
        print("✅ Client cleaned up successfully.")
    except subprocess.TimeoutExpired:
        print("⚠️ Client did not terminate in 5 seconds. Killing it.")
        subprocess.run(["kill", "-KILL", str(proc.pid)])
        
    return True

def main():
    host = get_wsl_host_ip()
    print(f"WSL Gateway (Windows Host) IP: {host}")
    
    boot_disk_ok = test_boot_disk_refusal(host)
    if not boot_disk_ok:
        sys.exit(1)
        
    stack_ok = run_full_stack_smoke_test(host)
    if not stack_ok:
        sys.exit(1)
        
    print("\n🎉 ALL SMOKE TESTS PASSED SUCCESSFULLY! 🎉")

if __name__ == "__main__":
    main()
