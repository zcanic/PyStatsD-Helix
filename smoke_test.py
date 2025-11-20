import subprocess
import socket
import time
import sys
import os
import signal
from pathlib import Path

def run_smoke_test():
    print("Starting PyStatsD-Helix server with file logging...")
    
    # Clean up previous log
    log_file = Path("smoke_metrics.log")
    if log_file.exists():
        log_file.unlink()

    # Start server
    server_process = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", "smoke_config.toml"],
        stdout=sys.stdout, # Let it print to console
        stderr=sys.stderr,
        cwd=os.getcwd(),
        env=os.environ.copy()
    )

    try:
        time.sleep(3)
        if server_process.poll() is not None:
            print("Server died immediately.")
            return

        print("Sending metrics...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        # Send multiple packets to ensure at least one gets through/flushed
        for i in range(5):
            sock.sendto(f"test.counter:{i+1}|c".encode(), ("127.0.0.1", 8127))
            sock.sendto(f"test.gauge:{i*10}|g".encode(), ("127.0.0.1", 8127))
            time.sleep(0.1)
        
        print("Metrics sent. Waiting for flush (interval 2s)...")
        time.sleep(5)
        
        print("Checking log file...")
        if log_file.exists():
            content = log_file.read_text(encoding="utf-8")
            print(f"--- Log Content ({len(content)} bytes) ---")
            print(content)
            print("-----------------------------------------")
            
            if "test.counter" in content:
                print("SUCCESS: Found metrics in log file.")
            else:
                print("FAILURE: Metrics not found in log file.")
        else:
            print("FAILURE: Log file was not created.")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        print("Stopping server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()

if __name__ == "__main__":
    run_smoke_test()
