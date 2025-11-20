import asyncio
import logging
import sys
from pystatsd_helix.config import ServerConfig
from pystatsd_helix.worker import Worker

# Configure logging
logging.basicConfig(level=logging.DEBUG)

async def main():
    config = ServerConfig(num_workers=1, log_level="DEBUG")
    worker = Worker(worker_id=1, config=config)
    print("Running worker...")
    
    # We want to run it for a few seconds then stop
    task = asyncio.create_task(worker.run())
    
    await asyncio.sleep(5)
    print("Stopping worker...")
    worker._handle_sigterm()
    await task
    print("Worker stopped.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        # print(f"Crashed: {e}") # Avoid printing to stdout if it's closed
        import traceback
        traceback.print_exc()
