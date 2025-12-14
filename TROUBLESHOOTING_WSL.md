# WSL2 Troubleshooting Guide

## Problem Diagnosis

Based on the analysis of the logs (`bench_stderr.txt`, `bench_stderr_2.txt`), the application is currently running in the **Windows** environment, not the **WSL2 (Linux)** environment.

### Evidence
1. **OS Detection**: The logs explicitly state:
   ```
   [WARNING] [worker-1] Windows detected: SO_REUSEPORT disabled. Load balancing will not work.
   ```
   This message is triggered when `os.name == 'nt'`, which only happens on Windows.

2. **Missing uvloop**: The logs show:
   ```
   [WARNING] [worker-1] uvloop not found! Falling back to standard asyncio loop.
   ```
   `uvloop` is a Linux-only library and is not installed on Windows.

3. **Windows Error Codes**: The logs contain Windows-specific error codes:
   ```
   [CRITICAL] [worker-1] Failed to bind UDP port: [WinError 10048] ...
   ```
   `WinError 10048` is "Address already in use". This happens because `SO_REUSEPORT` is not supported on Windows, so multiple workers cannot bind to the same port.

## Solution

To properly test with WSL2, you must ensure that the Python process is executed within the WSL2 Linux environment.

### Steps to Fix

1. **Enter WSL2 Shell**:
   Open your terminal (PowerShell or VS Code terminal) and type:
   ```bash
   wsl
   ```
   This will drop you into the Linux shell.

2. **Verify Environment**:
   Inside the WSL shell, run the following command to verify you are using the Linux Python interpreter:
   ```bash
   python3 -c "import os, sys; print(f'OS: {os.name}, Platform: {sys.platform}')"
   ```
   **Expected Output**:
   ```
   OS: posix, Platform: linux
   ```
   If it says `OS: nt`, you are still using Windows Python (possibly via an alias or path issue).

3. **Install Dependencies in WSL**:
   Ensure you have installed the project dependencies *inside* the WSL environment. The Windows installation does not carry over.
   ```bash
   # Inside WSL
   pip install -e .[dev]
   ```
   This will install `uvloop` and other Linux-specific dependencies.

4. **Run the Tests**:
   Now run your benchmark or smoke test commands from the WSL shell.
   ```bash
   python3 smoke_test.py
   ```

### Cleaning Up Windows Processes
Since you encountered `WinError 10048` (Address already in use), there might be "zombie" python processes running on Windows holding the port.
Run this in PowerShell (Admin) to kill them:
```powershell
Stop-Process -Name python -Force
```
