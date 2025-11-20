import asyncio
import subprocess
import socket
import time
import sys
import os
import signal

async def mock_graphite_server(host, port, stop_event):
    received_data = []
    
    async def handle_client(reader, writer):
        print(f"MockGraphite: Client connected")
        try:
            while not stop_event.is_set():
                try:
                    data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                    if not data:
                        break
                    print(f"MockGraphite received: {len(data)} bytes")
                    received_data.append(data.decode())
                except asyncio.TimeoutError:
                    continue
        except Exception as e:
            print(f"MockGraphite error: {e}")
        finally:
            writer.close()

    server = await asyncio.start_server(handle_client, host, port)
    print(f"MockGraphite listening on {host}:{port}")
    
    async with server:
        await stop_event.wait()
        
    return "".join(received_data)

async def run_smoke_test():
    print("Starting Mock Graphite Server...")
    stop_event = asyncio.Event()
    
    # Run mock server in background task
    server_task = asyncio.create_task(mock_graphite_server("127.0.0.1", 2003, stop_event))
    
    # Give server time to start
    await asyncio.sleep(1)

    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.join(os.getcwd(), "src")
    
    print("Starting PyStatsD-Helix server...")
    server_process = subprocess.Popen(
        [sys.executable, "-m", "pystatsd_helix.main", "--config", "smoke_graphite_config.toml"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=os.getcwd(),
        env=env
    )

    try:
        await asyncio.sleep(3)
        if server_process.poll() is not None:
            print("Server died immediately.")
            stdout, stderr = server_process.communicate()
            print("STDOUT:", stdout.decode(errors='replace') if stdout else "")
            # Print stderr safely
            if stderr:
                try:
                    print("STDERR:", stderr.decode(errors='replace'))
                except Exception:
                    print("STDERR (repr):", repr(stderr))
            else:
                print("STDERR: <empty>")
            stop_event.set()
            await server_task
            return

        print("Sending metrics...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        for i in range(5):
            sock.sendto(f"test.counter:{i+1}|c".encode(), ("127.0.0.1", 8126))
            await asyncio.sleep(0.1)
        
        print("Metrics sent. Waiting for flush (interval 2s)...")
        await asyncio.sleep(5)
        
    finally:
        print("Stopping server...")
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
            
        stop_event.set()
        received = await server_task
        
        print("\n--- Graphite Received Data ---")
        print(received)
        print("------------------------------")
        
        if "smoke.counters.test.counter.count" in received:
            print("SUCCESS: Found metrics in Graphite.")
        else:
            print("FAILURE: Metrics not found in Graphite.")

if __name__ == "__main__":
    # Windows selector loop policy fix if needed, but we are running simple script
    asyncio.run(run_smoke_test())
