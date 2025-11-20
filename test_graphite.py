import asyncio
import logging
import sys
from pystatsd_helix.config import GraphiteConfig
from pystatsd_helix.backends.graphite import GraphiteBackend
from pystatsd_helix.metrics_types import CounterSnapshot, GaugeSnapshot, TimerSnapshot, SetSnapshot
from pystatsd_helix.aggregator import AggregatedBatch

# Configure logging
logging.basicConfig(level=logging.DEBUG)

async def mock_graphite_server(host, port):
    async def handle_client(reader, writer):
        print(f"MockServer: Client connected from {writer.get_extra_info('peername')}")
        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                print(f"MockServer received:\n{data.decode()}")
        except Exception as e:
            print(f"MockServer error: {e}")
        finally:
            print("MockServer: Client disconnected")
            writer.close()

    server = await asyncio.start_server(handle_client, host, port)
    print(f"MockServer listening on {host}:{port}")
    return server

async def main():
    # Start mock server
    server = await mock_graphite_server("127.0.0.1", 2003)
    asyncio.create_task(server.serve_forever())
    
    # Setup backend
    config = GraphiteConfig(host="127.0.0.1", port=2003, prefix="test")
    backend = GraphiteBackend()
    await backend.setup(config, loop=asyncio.get_running_loop())
    
    # Create dummy batch
    now_ns = time.time_ns()
    batch = AggregatedBatch(
        worker_id=1,
        window_start_ns=now_ns - 1000000000,
        window_end_ns=now_ns,
        counters={"my.counter": CounterSnapshot(count=10, value=1.5, sample_rate=1.0)},
        gauges={"my.gauge": GaugeSnapshot(value=42.0, last_seen_ns=now_ns)},
        timers={"my.timer": TimerSnapshot(count=100, min=1, max=100, mean=50, stddev=10, sum=5000, percentiles={"95": 95, "99": 99})},
        sets={"my.set": SetSnapshot(count=5)}
    )
    
    print("Flushing batch...")
    result = await backend.flush(batch)
    print(f"Flush result: {result}")
    
    await backend.shutdown()
    
    # Stop server (not really needed as we exit)

import time
if __name__ == "__main__":
    asyncio.run(main())
