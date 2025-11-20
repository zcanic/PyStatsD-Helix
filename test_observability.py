import asyncio
import time
import socket
import requests
import subprocess
import sys
import os
import signal

def send_udp_packet(host, port, data):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.sendto(data.encode('ascii'), (host, port))
    sock.close()

import tempfile
import shutil

def test_observability():
    print("Starting PyStatsD server...")
    
    # Create temp dir for prometheus multiproc
    multiproc_dir = tempfile.mkdtemp()
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = multiproc_dir
    
    try:
        # Start server in a separate process
        process = subprocess.Popen(
            [sys.executable, "-m", "src.pystatsd_helix.main", "--config", "smoke_config.toml"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.getcwd(),
            env=os.environ.copy()
        )
        
        # Wait for server to start
        time.sleep(3)

        # Check Liveness
        print("Checking Liveness Probe...")
        resp = requests.get("http://127.0.0.1:9102/health/live")
        assert resp.status_code == 200
        print("Liveness OK")
        
        # Check Readiness
        print("Checking Readiness Probe...")
        resp = requests.get("http://127.0.0.1:9102/health/ready")
        assert resp.status_code == 200
        print("Readiness OK")
        
        # Send some metrics
        print("Sending metrics...")
        send_udp_packet("127.0.0.1", 8130, "test.counter:1|c")
        send_udp_packet("127.0.0.1", 8130, "test.gauge:10|g")
        
        time.sleep(1)
        
        # Check Metrics
        print("Checking Metrics Endpoint...")
        resp = requests.get("http://127.0.0.1:9102/metrics")
        assert resp.status_code == 200
        metrics_text = resp.text
        print("Metrics received:")
        print(metrics_text[:500] + "...")
        
        # Verify specific metrics
        assert "pystatsd_gateway_packets_total" in metrics_text
        assert "pystatsd_aggregator_received_total" in metrics_text
        
        print("Observability Test Passed!")
        
    except Exception as e:
        print(f"Test Failed: {e}")
        # Print server output for debugging
        outs, errs = process.communicate(timeout=1)
        print("Server Stdout:", outs.decode(errors='replace'))
        print("Server Stderr:", errs.decode(errors='replace'))
        raise
    finally:
        process.terminate()
        process.wait()

if __name__ == "__main__":
    test_observability()
