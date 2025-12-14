#!/usr/bin/env python3
"""
Windows Benchmark Suite - Run locally on Windows
"""
import subprocess
import time
import sys
import socket
import random
import urllib.request
import json
import os

# Configuration
RATES = [10000, 30000, 50000, 70000, 100000, 120000, 150000]
DURATION = 10
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8130
OBS_PORT = 8131
CONFIG_FILE = "loss_test_windows_config.toml"


def get_received_count(retries=15, delay=0.5):
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4 * 1024 * 1024)
    target_addr = (SERVER_HOST, SERVER_PORT)
    
    payloads = [f"bench.c.{i%100}:1|c".encode() for i in range(500)]
    
    start = time.monotonic()
    end = start + duration
    sent = 0
    tokens = 0.0
    last = time.monotonic()
    
    while time.monotonic() < end:
        now = time.monotonic()
        tokens += (now - last) * rate
        last = now
        tokens = min(tokens, rate)
        
        batch = 0
        while tokens >= 1.0 and batch < 500:
            try:
                sock.sendto(payloads[sent % len(payloads)], target_addr)
                sent += 1
                tokens -= 1.0
                batch += 1
            except:
                break
        
        if batch == 0:
            time.sleep(0.0001)
    
    sock.close()
    return sent


def kill_port_holder(port):
    """Kill any process holding the specified UDP port"""
    import re
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


def run_test(rate):
    print(f"\n=== Testing {rate:,} pkt/s ===")
    
    # Force cleanup any process holding the port
    kill_port_holder(SERVER_PORT)
    time.sleep(2)
    
    # Start server
    proc = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", CONFIG_FILE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    time.sleep(4)
    
    if proc.poll() is not None:
        print("  Server failed to start!")
        return None
    
    try:
        initial = get_received_count()
        if initial < 0:
            print("  Metrics endpoint not available!")
            proc.terminate()
            return None
        
        print(f"  Server ready, initial count: {initial}")
        
        sent = send_packets(rate, DURATION)
        print(f"  Sent: {sent:,}")
        
        time.sleep(5)  # Wait for flush
        
        final = get_received_count()
        received = final - initial
        loss = sent - received
        loss_pct = (loss / sent * 100) if sent > 0 else 0
        
        print(f"  Received: {received:,}, Loss: {loss_pct:.2f}%")
        
        return {"rate": rate, "sent": sent, "received": received, "loss_pct": loss_pct}
    
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except:
            proc.kill()
        # Windows needs significant time to release UDP sockets
        print("  Waiting for port release...")
        time.sleep(6)


def main():
    print("="*50)
    print("PyStatsD-Helix Windows Benchmark")
    print("="*50)
    print(f"Platform: {sys.platform}")
    print("Event Loop: asyncio (Windows)")
    
    results = []
    for rate in RATES:
        r = run_test(rate)
        if r:
            results.append(r)
    
    # Summary
    print("\n" + "="*60)
    print("SUMMARY")
    print("="*60)
    print(f"{'Rate':>12} | {'Sent':>12} | {'Received':>12} | {'Loss %':>10}")
    print("-"*60)
    for r in results:
        print(f"{r['rate']:>12,} | {r['sent']:>12,} | {r['received']:>12,} | {r['loss_pct']:>10.2f}%")
    
    # Save CSV
    with open("windows_loss_curve.csv", "w") as f:
        f.write("rate,sent,received,loss_pct\n")
        for r in results:
            f.write(f"{r['rate']},{r['sent']},{r['received']},{r['loss_pct']:.4f}\n")
    
    # Save JSON
    with open("windows_bench_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("\nResults saved to windows_loss_curve.csv")
    
    # Print JSON for extraction
    print("\n===JSON_START===")
    print(json.dumps(results))
    print("===JSON_END===")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
