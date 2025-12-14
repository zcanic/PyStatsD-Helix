from pystatsd_helix.worker import run_worker_process
from pystatsd_helix.config import ServerConfig, LoggerConfig, BackendConfigs
import logging
import sys

# Configure logging to stdout
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)

print("Creating config...")
config = ServerConfig(
    host="127.0.0.1",
    port=8130,
    num_workers=1,
    active_backends=["logger"],
    backend_configs=BackendConfigs(logger=LoggerConfig())
)

print("Starting worker process directly...")
try:
    run_worker_process(config, 1)
except Exception as e:
    print(f"Crashed: {e}")
