#!/usr/bin/env python3
"""Debug script to test server startup in container"""
import subprocess
import time
import sys
import socket
import urllib.request

CONFIG = "/app/bench_config.toml"

print("Starting server...")
proc = subprocess.Popen(
    [sys.executable, "-m", "pystatsd_helix.main", "--config", CONFIG],
    stdout=subprocess.PIPE, 
    stderr=subprocess.STDOUT,
    text=True
)

time.sleep(4)

if proc.poll() is not None:
    print(f"Server exited with code: {proc.returncode}")
    output = proc.stdout.read() if proc.stdout else ""
    print(f"Output:\n{output[:3000]}")
    sys.exit(1)

print("Server running, PID:", proc.pid)

# Test UDP
print("\nTesting UDP...")
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
for i in range(100):
    sock.sendto(f"test.counter:1|c".encode(), ("127.0.0.1", 8125))
sock.close()
print("Sent 100 UDP packets")

time.sleep(3)

# Test metrics endpoint
print("\nTesting metrics endpoint...")
try:
    with urllib.request.urlopen("http://127.0.0.1:9102/metrics", timeout=5) as resp:
        content = resp.read().decode()
        for line in content.splitlines():
            if "aggregator_received" in line:
                print(line)
except Exception as e:
    print(f"Error: {e}")

# Cleanup
proc.terminate()
try:
    proc.wait(timeout=5)
except:
    proc.kill()

print("\nDone!")
