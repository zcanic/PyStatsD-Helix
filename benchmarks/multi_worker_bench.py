#!/usr/bin/env python3
"""
Multi-Worker Benchmark Suite
Tests linear scaling with 1, 2, 4 workers at various packet rates.
"""
import subprocess
import time
import sys
import socket
import urllib.request
import json
import os
import re
import tempfile
import shutil

# Test configurations
WORKER_COUNTS = [1, 2, 4]
RATES = [50000, 100000, 150000, 200000]
DURATION = 10
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8130
OBS_PORT = 8131
BASE_CONFIG = "multi_worker_bench_config.toml"


def kill_port_holder(port):
    """Kill any process holding the specified UDP port"""
    try:
        result = subprocess.run(
            ['netstat', '-anop', 'udp'],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f':{port}' in line:
                match = re.search(r'(\d+)\s*$', line)
                if match:
                    pid = match.group(1)
                    subprocess.run(['taskkill', '/F', '/PID', pid], 
                                 capture_output=True)
                    time.sleep(1)
    except:
        pass


def get_received_count(retries=20, delay=0.5):
    """Fetch total received metrics from all workers via shared memory"""
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"http://{SERVER_HOST}:{OBS_PORT}/metrics", timeout=3) as resp:
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
    
    # Pre-generate payloads with variety
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
        tokens = min(tokens, rate * 2)  # Allow burst
        
        batch = 0
        while tokens >= 1.0 and batch < 1000:
            try:
                sock.sendto(payloads[sent % len(payloads)], target_addr)
                sent += 1
                tokens -= 1.0
                batch += 1
            except BlockingIOError:
                time.sleep(0.00001)
                break
            except Exception:
                break
        
        if batch == 0:
            time.sleep(0.00001)
    
    sock.close()
    actual_duration = time.monotonic() - start
    actual_rate = sent / actual_duration if actual_duration > 0 else 0
    return sent, actual_rate


def create_config_with_workers(num_workers):
    """Create a temporary config file with specified worker count"""
    with open(BASE_CONFIG, 'r') as f:
        config = f.read()
    
    # Replace num_workers line
    config = re.sub(r'num_workers\s*=\s*\d+', f'num_workers = {num_workers}', config)
    
    temp_config = f"temp_bench_config_{num_workers}w.toml"
    with open(temp_config, 'w') as f:
        f.write(config)
    
    return temp_config


def run_single_test(num_workers, rate):
    """Run a single test with specified workers and rate"""
    print(f"\n  {num_workers}W @ {rate:,} pkt/s: ", end="", flush=True)
    
    # Cleanup port
    kill_port_holder(SERVER_PORT)
    kill_port_holder(OBS_PORT)
    time.sleep(2)
    
    # Create config
    config_file = create_config_with_workers(num_workers)
    
    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", config_file],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Wait for startup (more workers need more time)
    time.sleep(3 + num_workers)
    
    if proc.poll() is not None:
        print("FAILED (server crashed)")
        os.remove(config_file)
        return None
    
    try:
        initial = get_received_count()
        if initial < 0:
            print("FAILED (metrics unavailable)")
            return None
        
        # Run benchmark
        sent, actual_rate = send_packets(rate, DURATION)
        
        # Wait for flush (longer for more workers)
        time.sleep(4 + num_workers)
        
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
        
        # Cleanup
        if os.path.exists(config_file):
            os.remove(config_file)
        
        time.sleep(4)


def main():
    print("="*70)
    print("PyStatsD-Helix Multi-Worker Benchmark")
    print("="*70)
    print(f"Platform: {sys.platform}")
    print(f"Workers to test: {WORKER_COUNTS}")
    print(f"Rates to test: {[f'{r//1000}k' for r in RATES]}")
    print(f"Duration per test: {DURATION}s")
    
    results = []
    
    for num_workers in WORKER_COUNTS:
        print(f"\n{'='*70}")
        print(f"Testing with {num_workers} Worker(s)")
        print("="*70)
        
        for rate in RATES:
            result = run_single_test(num_workers, rate)
            if result:
                results.append(result)
    
    # Summary table
    print("\n" + "="*80)
    print("SUMMARY: Multi-Worker Scaling Results")
    print("="*80)
    print(f"{'Workers':>8} | {'Target Rate':>12} | {'Actual Rate':>12} | {'Sent':>12} | {'Received':>12} | {'Loss %':>8}")
    print("-"*80)
    
    for r in results:
        print(f"{r['workers']:>8} | {r['target_rate']:>12,} | {r['actual_rate']:>12,.0f} | {r['sent']:>12,} | {r['received']:>12,} | {r['loss_pct']:>8.2f}%")
    
    # Scaling analysis
    print("\n" + "="*80)
    print("SCALING ANALYSIS")
    print("="*80)
    
    # Find zero-loss threshold for each worker count
    for num_workers in WORKER_COUNTS:
        worker_results = [r for r in results if r['workers'] == num_workers]
        zero_loss_results = [r for r in worker_results if r['loss_pct'] < 0.1]
        if zero_loss_results:
            max_zero_loss_rate = max(r['target_rate'] for r in zero_loss_results)
            print(f"  {num_workers} Worker(s): Zero-loss threshold ≈ {max_zero_loss_rate:,} pkt/s")
        else:
            print(f"  {num_workers} Worker(s): No zero-loss achieved in tested range")
    
    # Save results
    with open("multi_worker_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    with open("multi_worker_results.csv", "w") as f:
        f.write("workers,target_rate,actual_rate,sent,received,loss_pct\n")
        for r in results:
            f.write(f"{r['workers']},{r['target_rate']},{r['actual_rate']:.0f},{r['sent']},{r['received']},{r['loss_pct']:.4f}\n")
    
    print("\nResults saved to multi_worker_results.json and multi_worker_results.csv")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
