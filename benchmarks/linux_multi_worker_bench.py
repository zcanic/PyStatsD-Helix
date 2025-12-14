#!/usr/bin/env python3
"""
Linux Multi-Worker Benchmark Suite
Tests linear scaling with 1, 2, 4 workers at various packet rates inside Docker.
"""
import subprocess
import time
import sys
import socket
import urllib.request
import json
import os
import re

# Test configurations
WORKER_COUNTS = [1, 2, 4]
RATES = [40000, 60000, 80000, 100000, 120000]
DURATION = 10
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8125
OBS_PORT = 9102

# Base config template
BASE_CONFIG_TEMPLATE = """
[server]
host = "0.0.0.0"
port = 8125
num_workers = {num_workers}
flush_interval = 2.0
log_level = "WARNING"
active_backends = ["logger"]
max_series = 100000
timer_histogram_config = [1, 3600000, 3]
# socket_buffer_size = 4194304

obs_host = "0.0.0.0"
obs_port = 9102

[backend_configs.logger]
level = "WARNING"
mode = "ndjson"
sample_percent = 0.0
destination = "stdout"
"""

def kill_server_processes():
    """Kill any existing pystatsd server processes"""
    try:
        # Use pkill to find and kill python processes running main
        subprocess.run(['pkill', '-f', 'pystatsd_helix.main'], 
                     stdout=subprocess.DEVNULL, 
                     stderr=subprocess.DEVNULL)
        time.sleep(1)
    except:
        pass


def get_received_count(retries=20, delay=0.5):
    """Fetch total received metrics from all workers via shared memory"""
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"http://{SERVER_HOST}:{OBS_PORT}/metrics", timeout=2) as resp:
                content = resp.read().decode('utf-8')
                for line in content.splitlines():
                    if "pystatsd_aggregator_received_total" in line and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(float(parts[-1]))
                return 0
        except Exception:
            time.sleep(delay)
    return 0


def send_packets(rate, duration):
    """Send UDP packets at target rate"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8 * 1024 * 1024)
    target_addr = (SERVER_HOST, SERVER_PORT)
    
    # Pre-generate payloads
    payloads = [f"bench.counter.{i%1000}:1|c".encode() for i in range(1000)]
    
    start = time.monotonic()
    end = start + duration
    sent = 0
    tokens = 0.0
    last = time.monotonic()
    
    while time.monotonic() < end:
        now = time.monotonic()
        tokens += (now - last) * rate
        last = now
        tokens = min(tokens, rate * 2)
        
        batch = 0
        while tokens >= 1.0 and batch < 1000:
            try:
                sock.sendto(payloads[sent % len(payloads)], target_addr)
                sent += 1
                tokens -= 1.0
                batch += 1
            except BlockingIOError:
                break
            except Exception:
                break
        
        if batch == 0:
            time.sleep(0.00001)
    
    sock.close()
    actual_duration = time.monotonic() - start
    actual_rate = sent / actual_duration if actual_duration > 0 else 0
    return sent, actual_rate


def create_config(num_workers):
    """Create config file"""
    config_content = BASE_CONFIG_TEMPLATE.format(num_workers=num_workers)
    filename = f"/app/bench_config_{num_workers}w.toml"
    with open(filename, 'w') as f:
        f.write(config_content)
    return filename


def run_single_test(num_workers, rate):
    """Run a single test"""
    print(f"\n  {num_workers}W @ {rate:,} pkt/s: ", end="", flush=True)
    
    # Clean state
    kill_server_processes()
    
    config_file = create_config(num_workers)
    
    # Start server with pipes
    env = os.environ.copy()
    env["PYSTATSD_DISABLE_UVLOOP"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", config_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env
    )
    
    # Wait for startup
    time.sleep(3 + num_workers)
    
    if proc.poll() is not None:
        print("FAILED (server crashed)")
        _, stderr_out = proc.communicate()
        print(f"STDERR: {stderr_out}")
        return None
    
    try:
        initial = get_received_count()
        if initial < 0: # get_received_count returns 0 on failure currently, maybe improve
            # Check liveness again if 0
            pass 
        
        # Run benchmark
        sent, actual_rate = send_packets(rate, DURATION)
        
        # Wait for flush
        time.sleep(3 + num_workers)
        
        final = get_received_count()
        received = final - initial
        loss = sent - received
        loss_pct = (loss / sent * 100) if sent > 0 else 0
        
        status = "[OK]" if loss_pct < 0.1 else ("[WARN]" if loss_pct < 5 else "[LOSS]")
        print(f"sent={sent:,}, recv={received:,}, loss={loss_pct:.2f}% {status}")
        
        return {
            "workers": num_workers,
            "target_rate": rate,
            "actual_rate": actual_rate,
            "sent": sent,
            "received": received,
            "loss_pct": loss_pct
        }
    
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except:
            proc.kill()
        kill_server_processes()
        time.sleep(2)


def main():
    print("="*60)
    print("PyStatsD-Helix Linux Multi-Worker Benchmark")
    print("="*60)
    print(f"CPU Count: {os.cpu_count()}")
    
    try:
        import uvloop
        print("uvloop: Available but DISABLED for stability")
    except:
        print("uvloop: Not Available")
        
    results = []
    
    for num_workers in WORKER_COUNTS:
        print(f"\n{'='*60}")
        print(f"Testing with {num_workers} Worker(s)")
        print("="*60)
        
        for rate in RATES:
            result = run_single_test(num_workers, rate)
            if result:
                results.append(result)
            
            # Simple heuristic to stop early if loss is excessively high to save time
            if result and result['loss_pct'] > 20:
                print("  Stopping further rate tests for this worker count due to high loss.")
                break
    
    # Print JSON to stdout for extraction
    print("\n===JSON_START===")
    print(json.dumps(results))
    print("===JSON_END===")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
