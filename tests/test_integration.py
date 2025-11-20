import asyncio
import socket
import subprocess
import sys
import time
import threading
import os
import signal
import pytest
import requests
from contextlib import closing
from pathlib import Path

# Constants
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8125
OBS_PORT = 9102
GRAPHITE_PORT = 2003
GRAPHITE_HOST = "127.0.0.1"

def wait_for_port(port, host='127.0.0.1', timeout=10.0):
    """Wait until a port is open."""
    start_time = time.time()
    while True:
        try:
            with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
                sock.settimeout(1)
                if sock.connect_ex((host, port)) == 0:
                    return True
        except:
            pass
        if time.time() - start_time > timeout:
            return False
        time.sleep(0.1)

class MockGraphiteServer:
    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))
        self.sock.listen(1)
        self.received_data = []
        self.running = True
        self.thread = threading.Thread(target=self._accept_loop)
        self.thread.start()

    def _accept_loop(self):
        self.sock.settimeout(1.0)
        while self.running:
            try:
                conn, addr = self.sock.accept()
                with conn:
                    conn.settimeout(1.0)
                    while self.running:
                        try:
                            data = conn.recv(1024)
                            if not data:
                                break
                            self.received_data.append(data.decode('utf-8'))
                        except socket.timeout:
                            continue
                        except OSError:
                            break
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self.running = False
        self.thread.join()
        self.sock.close()

    def get_received_lines(self):
        full_text = "".join(self.received_data)
        return [line for line in full_text.split('\n') if line.strip()]

@pytest.fixture
def mock_graphite():
    server = MockGraphiteServer(GRAPHITE_HOST, GRAPHITE_PORT)
    yield server
    server.stop()

@pytest.fixture
def pystatsd_server(tmp_path):
    # Create a config file
    # Note: On Windows, we must use num_workers=1
    config_content = f"""
[server]
host = "{SERVER_HOST}"
port = {SERVER_PORT}
num_workers = 1
flush_interval = 1.0
log_level = "DEBUG"
active_backends = ["graphite"]
obs_port = {OBS_PORT}

[backend_configs.graphite]
host = "{GRAPHITE_HOST}"
port = {GRAPHITE_PORT}
prefix = "integration_test"
    """
    config_file = tmp_path / "integration_config.toml"
    config_file.write_text(config_content, encoding="utf-8")

    # Start the server process
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    # Ensure we use a clean multiproc dir for metrics
    multiproc_dir = tmp_path / "multiproc_metrics"
    multiproc_dir.mkdir()
    env["PROMETHEUS_MULTIPROC_DIR"] = str(multiproc_dir)
    
    cmd = [sys.executable, "-m", "pystatsd_helix.main", "--config", str(config_file)]
    
    print(f"Starting server with command: {cmd}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True
    )
    
    # Wait for Observability port to be ready (indicates server is up)
    if not wait_for_port(OBS_PORT):
        stdout, stderr = proc.communicate()
        pytest.fail(f"Server failed to start (port {OBS_PORT} not open):\nSTDOUT: {stdout}\nSTDERR: {stderr}")

    # Give workers time to spawn and bind UDP port
    time.sleep(2.0)

    yield proc

    # Teardown
    print("Stopping server...")
    if sys.platform == 'win32':
        # Force kill process tree on Windows
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

def send_udp_metric(name, value, type_code):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    msg = f"{name}:{value}|{type_code}"
    sock.sendto(msg.encode('ascii'), (SERVER_HOST, SERVER_PORT))
    sock.close()

def test_end_to_end_flow(pystatsd_server, mock_graphite):
    """
    Test that a metric sent via UDP is aggregated and flushed to Graphite.
    """
    metric_name = "test.counter"
    
    # Send some metrics
    print(f"Sending metrics to {SERVER_HOST}:{SERVER_PORT}...")
    for _ in range(5):
        send_udp_metric(metric_name, 1, "c")
        time.sleep(0.01)
    
    # Wait for flush (flush_interval is 1.0s)
    print("Waiting for flush...")
    time.sleep(2.5)
    
    # Check Graphite
    lines = mock_graphite.get_received_lines()
    print("Received lines from Graphite:", lines)
    
    found = False
    for line in lines:
        # Graphite backend adds .counters. and .rate/.count suffixes for counters
        if "integration_test.counters.test.counter" in line:
            found = True
            break
            
    assert found, f"Metric {metric_name} not found in Graphite output: {lines}"

def test_observability_endpoint(pystatsd_server):
    """
    Test that the /metrics endpoint is reachable and returns valid data.
    """
    # Wait for worker to emit heartbeat (flush_interval is 1.0s)
    time.sleep(2.0)
    
    url = f"http://127.0.0.1:{OBS_PORT}/metrics"
    print(f"Fetching metrics from {url}...")
    try:
        response = requests.get(url)
        assert response.status_code == 200
        content = response.text
        print("Metrics content snippet:", content[:200])
        assert "pystatsd_" in content
    except Exception as e:
        pytest.fail(f"Failed to fetch metrics: {e}")

def test_health_check_endpoint(pystatsd_server):
    """
    Test the /health/ready endpoint.
    """
    url = f"http://127.0.0.1:{OBS_PORT}/health/ready"
    print(f"Checking health at {url}...")
    try:
        response = requests.get(url)
        assert response.status_code == 200
        assert response.text == "OK"
    except Exception as e:
        pytest.fail(f"Failed to check health: {e}")
