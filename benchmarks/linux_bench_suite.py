#!/usr/bin/env python3
"""
Simple Linux Benchmark - Runs inside Docker container
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
RATES = [10000, 30000, 50000, 80000, 100000, 150000, 200000]
DURATION = 10
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8125
OBS_PORT = 9102
CONFIG_FILE = "/app/bench_config.toml"


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
        except Exception as e:
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


def run_test(rate):
    print(f"\n=== Testing {rate:,} pkt/s ===")
    
    # Start server as background process
    proc = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", CONFIG_FILE],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    time.sleep(4)  # Give more time for startup
    
    if proc.poll() is not None:
        output = proc.stdout.read() if proc.stdout else ""
        print(f"  Server failed! Output: {output[:500]}")
        return None
    
    # Verify server is up by checking metrics endpoint
    initial = get_received_count()
    if initial < 0:
        print("  Metrics endpoint not available!")
        proc.terminate()
        return None
    
    print(f"  Server ready, initial count: {initial}")
    
    try:
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
        time.sleep(2)


def main():
    print("="*50)
    print("PyStatsD-Helix Linux Benchmark")
    print("="*50)
    
    try:
        import uvloop
        print("uvloop: Available")
    except:
        print("uvloop: Not available")
    
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
    with open("/app/linux_loss_curve.csv", "w") as f:
        f.write("rate,sent,received,loss_pct\n")
        for r in results:
            f.write(f"{r['rate']},{r['sent']},{r['received']},{r['loss_pct']:.4f}\n")
    
    # Save JSON
    with open("/app/linux_bench_results.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print("\nResults saved!")
    
    # Print JSON to stdout for extraction
    print("\n===JSON_START===")
    print(json.dumps(results))
    print("===JSON_END===")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
