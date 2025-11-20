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
    print("Initializing worker...")
    # We just want to see if init crashes
    # But run() does the init of backends and dispatcher
    # Let's call the parts of run() manually or just run() with a timeout
    
    try:
        # 1. Initialize Backends
        from pystatsd_helix.backends.loader import create_backends, setup_backends
        worker.backends = create_backends(worker.config)
        await setup_backends(worker.backends, worker.config)
        print(f"Loaded backends: {[b.__class__.__name__ for b in worker.backends]}")
        
        # 2. Initialize Dispatcher
        from pystatsd_helix.aggregator import FlushDispatcher
        worker.dispatcher = FlushDispatcher(worker.config, worker.backends)
        await worker.dispatcher.start()
        print("Dispatcher started.")
        
        print("Worker init success.")
    except Exception as e:
        print(f"Worker init failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
