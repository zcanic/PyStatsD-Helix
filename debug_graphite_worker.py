from __future__ import annotations

import asyncio
import logging
import sys
from pystatsd_helix.config import load_config
from pystatsd_helix.worker import Worker

# Configure logging
logging.basicConfig(level=logging.DEBUG)
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

async def main():
    try:
        config = load_config("smoke_graphite_config.toml")
        print("Config loaded.")
        worker = Worker(worker_id=1, config=config)
        print("Worker initialized.")
        
        print("Running worker for 5 seconds...")
        task = asyncio.create_task(worker.run())
        
        # Wait for a bit to let flush happen
        await asyncio.sleep(3)
        print("Sending metrics manually...")
        from pystatsd_helix.metrics_types import CounterMetric, MetricType
        worker.aggregator.receive(CounterMetric(name="test.counter", type=MetricType.COUNTER, value=1.0))
        
        await asyncio.sleep(3)
        print("Stopping worker...")
        worker._handle_sigterm()
        await task
        print("Worker done.")
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
