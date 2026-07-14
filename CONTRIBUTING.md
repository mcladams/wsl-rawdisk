# Contributing to wsl-rawdisk

Thank you for wanting to contribute to `wsl-rawdisk`! This project provides a critical bridge allowing WSL2 FUSE-mounted access to raw physical drives.

Because this tool runs with elevated Windows privileges and manipulates physical disk tables, **safety, strict input validation, and structural correctness are our highest priorities.** A single bug or unhandled edge case can cause irreversible data loss, Blue Screens of Death (BSOD), or bricked operating systems.

By contributing to this repository, you agree to license your contributions under the project's [MIT License](https://gemini.google.com/LICENSE "null").

## 🎯 Our Philosophy

1. **Safety-First, Fail-Closed:** If an operation is not explicitly verified as safe, it must be refused. No speculative execution or silent fallbacks.
    
2. **Headless-First Design:** The Windows backend daemon must remain completely headless. No GUI popups, dialog boxes, or blocking desktop-session modals should ever be introduced to the server. Configuration and authorization are driven strictly by CLI flags and the binary protocol.
    
3. **Strict Boundary Separation:** Keep client logic (Linux/WSL) and server logic (Windows host) decoupled. Network protocol packets are the single source of truth.
    
4. **No Bloat:** Keep dependencies lean, open-source, and standard. Avoid proprietary frameworks or platform lock-in.
    

## 🔒 The Safety Prime Directive

Any pull request touching code that affects disk access, target validation, write authorization, or path parsing is **highly safety-critical**.

When modifying files like `wsl-rawdisk-server.py`, `device.py`, or `protocol.py`:

- **Strict Regex Validation:** Targets must be matched exclusively against strict, vetted device patterns (e.g., matching physical drives using `^\\\\.\\PHYSICALDRIVE\d+$`). Never use arbitrary file resolution check patterns (like `os.path.isfile()`) that allow attackers or client bugs to bypass index checks and write to Windows system files.
    
- **Write Intent Isolation:** Write privileges must be determined per-request at the connection/open layer, not as a blanket server-lifetime switch.
    
- **Refusal by Default:** Attempts to open the boot disk or active paging files with write intent must fail closed unless the server is explicitly started with override flags (such as `--allow-unsafe-boot-disk-writes`).
    

## 🧬 Protocol & Data Packing Standards

We support cross-platform communication between a Windows kernel/host environment and a Linux guest VM. Mixed alignment across socket streams is highly volatile.

- **Explicit Standard Alignment:** All formats used with `struct.pack` and `struct.unpack` must reside in `protocol.py` and strictly use the standard-size prefix (`=`). Native alignment (`@`) or hardcoded non-aligned format strings are strictly forbidden across socket boundaries.
    
- **No Hardcoding:** Always import and use the defined constants (`FMT_OPEN`, `FMT_REPLY_SHORT`, etc.) instead of hardcoding raw format strings in connection loops.
    

## 🧪 Testing Requirements

We do not accept pull requests that reduce test coverage or bypass safety validations.

- **No Mocks on Raw Logic:** While network connections can be mocked using mock classes (see `test_safety.py`), the validation routing and state routing should be fully exercised by the test suite.
    
- **Regression Testing:** If you are fixing a vulnerability or validation bypass, you **must** add a corresponding regression test. For example, if target resolution is tightened, write a test proving that bypass variants (like passing `C:\Windows\System32\notepad.exe`) are explicitly rejected with a `-1` error index.
    
- **Running Tests:** To run the safety suite prior to submitting a PR, execute:
    
    ```
    python -m unittest test_safety.py
    ```
    

## 🚀 Pull Request Checklist

Before submitting your Pull Request, ensure you can check off every item:

- \[ \] Code strictly complies with PEP 8 styling.
    
- \[ \] No manual or blocking GUI/popup libraries (`win32ui`, etc.) are introduced to backend daemons.
    
- \[ \] All network structures in `protocol.py` use standard (`=`) alignment.
    
- \[ \] The test suite (`test_safety.py`) runs and passes completely.
    
- \[ \] Any safety-adjacent changes are explicitly detailed in the PR description (never hide security or validation changes under a "clean up" or "refactor" commit).