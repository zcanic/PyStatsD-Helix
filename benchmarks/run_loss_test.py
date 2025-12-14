import argparse
import subprocess
import time
import sys
import os
import urllib.request

def get_received_count(obs_host: str, obs_port: int, retries: int = 5, delay: float = 0.5) -> int:
    last_error: Exception | None = None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(f"http://{obs_host}:{obs_port}/metrics", timeout=2) as response:
                content = response.read().decode('utf-8')
                total = 0.0
                for line in content.splitlines():
                    if line.startswith("#"):
                        continue
                    if "pystatsd_aggregator_received_total" in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            total += float(parts[-1])
                return int(total)
        except Exception as e:
            last_error = e
            time.sleep(delay)
    if last_error:
        print(f"Failed to fetch metrics after {retries} attempts: {last_error}")
    return 0

def run_test(rate: int, batch_size: int, *, duration: int, config_path: str,
             server_host: str, server_port: int, obs_host: str, obs_port: int) -> dict | None:
    print(f"--- Testing Rate: {rate} pkt/s (batch={batch_size}) ---")
    
    # 1. Start Server
    server_process = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", config_path],
        stdout=subprocess.DEVNULL,
        stderr=open("server_stderr.log", "w"), # Redirect to file to avoid pipe deadlock
        cwd=os.getcwd(),
        text=True
    )
    
    # Wait for startup
    time.sleep(2)
    
    if server_process.poll() is not None:
        print("Server failed to start")
        try:
            with open("server_stderr.log", "r") as f:
                print("STDERR:", f.read())
        except:
            pass
        return None

    try:
        # Get initial count (should be 0, but good to be safe)
        initial_received = get_received_count(obs_host, obs_port)

        # 3. Run Benchmark
        cmd = [
            sys.executable, "benchmarks/ingest_bench.py",
            "--host", server_host,
            "--port", str(server_port),
            "--duration", str(duration),
            "--rate", str(rate),
            "--batch-size", str(batch_size),
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        
        # Parse sent metric count from output
        sent_metrics = 0
        sent_packets = 0
        for line in result.stderr.splitlines(): # logging goes to stderr
            line = line.strip()
            if "METRICS_SENT=" in line:
                try:
                    sent_metrics = int(line.split("METRICS_SENT=", 1)[1])
                except ValueError:
                    pass
            if "PACKETS_SENT=" in line:
                try:
                    sent_packets = int(line.split("PACKETS_SENT=", 1)[1])
                except ValueError:
                    pass
        
        if sent_metrics == 0:
            print("Could not parse sent metrics from benchmark output")
            print(result.stderr)
            return None
            
        # 4. Wait for processing - need to wait for at least one flush cycle + buffer
        # flush_interval is typically 2s, so wait 4s to be safe
        time.sleep(4)
        
        # 5. Get final count
        final_received = get_received_count(obs_host, obs_port)
        received = final_received - initial_received
        
        # 6. Stop Server - be aggressive to ensure port is released
        server_process.terminate()
        try:
            server_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            server_process.kill()
            server_process.wait(timeout=2)
        
        # Extra wait for Windows to release UDP sockets
        time.sleep(2)
        
        # Read stderr from file
        stderr = ""
        try:
            with open("server_stderr.log", "r") as f:
                stderr = f.read()
        except Exception:
            pass
            
        if "CRITICAL" in stderr or "ERROR" in stderr:
             # Only print if there are errors to avoid spamming
             # print("Server STDERR (last 500 chars):")
             # print(stderr[-500:])
             pass
            
        loss = sent_metrics - received
        loss_rate = (loss / sent_metrics) * 100.0 if sent_metrics > 0 else 0
        
        print(f"Sent metrics: {sent_metrics}, Received: {int(received)}, Loss: {loss}, Rate: {loss_rate:.2f}%")
        return {
            "rate": rate,
            "batch_size": batch_size,
            "packets": sent_packets,
            "sent": sent_metrics,
            "received": int(received),
            "loss_rate": loss_rate
        }

    except Exception as e:
        print(f"Error: {e}")
        server_process.kill()
        return None

def main():
    windows_config = "loss_test_windows_config.toml"
    default_config = windows_config if os.name == "nt" and os.path.exists(windows_config) else "loss_test_config.toml"

    parser = argparse.ArgumentParser(description="Run packet loss benchmarks against pystatsd-helix")
    parser.add_argument("--rates", type=int, nargs="+", default=[10000, 20000, 40000, 60000, 80000, 100000, 120000, 150000, 200000])
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1], help="Metrics per UDP packet")
    parser.add_argument("--duration", type=int, default=5)
    parser.add_argument("--config", default=default_config)
    parser.add_argument("--server-host", default="127.0.0.1")
    parser.add_argument("--server-port", type=int, default=8130)
    parser.add_argument("--obs-host", default="127.0.0.1")
    parser.add_argument("--obs-port", type=int, default=8131)
    args = parser.parse_args()

    results = []
    
    print("Starting Loss Test Sequence...")
    
    for batch in args.batch_sizes:
        for r in args.rates:
            res = run_test(
                r,
                batch,
                duration=args.duration,
                config_path=args.config,
                server_host=args.server_host,
                server_port=args.server_port,
                obs_host=args.obs_host,
                obs_port=args.obs_port,
            )
            if res:
                results.append(res)
            # Windows needs extra time to release UDP sockets
            time.sleep(3)
        
    print("\n=== Final Results ===")
    print(f"{'Batch':<7} | {'Target Rate':<15} | {'Sent Metrics':<13} | {'Received':<10} | {'Loss Rate %':<10}")
    print("-" * 70)
    for res in results:
        print(
            f"{res['batch_size']:<7} | {res['rate']:<15} | {res['sent']:<13} | {res['received']:<10} | {res['loss_rate']:.2f}%"
        )

    # Generate CSV
    with open("loss_curve.csv", "w") as f:
        f.write("batch_size,rate,sent_metrics,received,loss_rate\n")
        for res in results:
            f.write(f"{res['batch_size']},{res['rate']},{res['sent']},{res['received']},{res['loss_rate']}\n")

if __name__ == "__main__":
    main()
