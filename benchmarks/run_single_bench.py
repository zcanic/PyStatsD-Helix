"""
Simplified benchmark that runs a single rate test.
Usage: python benchmarks/run_single_bench.py --rate 50000 --duration 10
"""
import argparse
import subprocess
import time
import sys
import os
import urllib.request
import signal

def get_received_count(obs_host: str, obs_port: int, retries: int = 5, delay: float = 0.5) -> int:
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"http://{obs_host}:{obs_port}/metrics", timeout=2) as response:
                content = response.read().decode('utf-8')
                for line in content.splitlines():
                    if line.startswith("#"):
                        continue
                    if "pystatsd_aggregator_received_total" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            return int(float(parts[-1]))
                return 0
        except Exception as e:
            last_error = e
            time.sleep(delay)
    if last_error:
        print(f"Failed to fetch metrics after {retries} attempts: {last_error}")
    return 0

def main():
    parser = argparse.ArgumentParser(description="Run a single rate benchmark")
    parser.add_argument("--rate", type=int, default=50000, help="Target packet rate")
    parser.add_argument("--batch-size", type=int, default=1, help="Metrics per packet")
    parser.add_argument("--duration", type=int, default=10, help="Duration in seconds")
    parser.add_argument("--config", default="loss_test_windows_config.toml")
    parser.add_argument("--server-host", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=8130)
    parser.add_argument("--obs-host", default="127.0.0.1")
    parser.add_argument("--obs-port", type=int, default=8131)
    args = parser.parse_args()

    print(f"=== Benchmark: {args.rate} pkt/s for {args.duration}s ===")
    
    # 1. Start Server
    print("Starting server...")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", args.config],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        cwd=os.getcwd(),
        text=True
    )
    
    # Wait for startup
    time.sleep(3)
    
    if server_process.poll() is not None:
        print("Server failed to start!")
        stderr = server_process.stderr.read()
        print("STDERR:", stderr[-1000:] if len(stderr) > 1000 else stderr)
        return 1
    
    print(f"Server started (PID: {server_process.pid})")
    
    try:
        # 2. Get initial count
        initial = get_received_count(args.obs_host, args.obs_port)
        print(f"Initial received count: {initial}")
        
        # 3. Run benchmark
        print(f"Running ingest_bench at {args.rate} pkt/s...")
        cmd = [
            sys.executable, "benchmarks/ingest_bench.py",
            "--host", args.server_host,
            "--port", str(args.server_port),
            "--duration", str(args.duration),
            "--rate", str(args.rate),
            "--batch-size", str(args.batch_size),
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Parse sent metrics
        sent_metrics = 0
        for line in result.stderr.splitlines():
            if "METRICS_SENT=" in line:
                try:
                    sent_metrics = int(line.split("METRICS_SENT=", 1)[1])
                except ValueError:
                    pass
        
        if sent_metrics == 0:
            print("Could not parse sent metrics. Output:")
            print(result.stderr)
            return 1
        
        print(f"Benchmark finished. Sent: {sent_metrics} metrics")
        
        # 4. Wait for flush cycle
        print("Waiting for flush cycle...")
        time.sleep(4)
        
        # 5. Get final count
        final = get_received_count(args.obs_host, args.obs_port)
        received = final - initial
        
        loss = sent_metrics - received
        loss_rate = (loss / sent_metrics) * 100.0 if sent_metrics > 0 else 0
        
        print("\n" + "="*50)
        print(f"RESULTS:")
        print(f"  Target Rate: {args.rate} pkt/s")
        print(f"  Sent:        {sent_metrics}")
        print(f"  Received:    {received}")
        print(f"  Loss:        {loss}")
        print(f"  Loss Rate:   {loss_rate:.2f}%")
        print("="*50)
        
    finally:
        # 6. Stop server
        print("Stopping server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait()
        print("Done.")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
