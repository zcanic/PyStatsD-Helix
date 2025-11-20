from __future__ import annotations

import argparse
import logging
import multiprocessing as mp
import signal
import sys
import time
from typing import List, Optional

from .config import ServerConfig, load_config, ConfigError
from .worker import run_worker_process
from .obs.http import ObsServer

SHUTDOWN_TIMEOUT = 10.0

# Configure basic logging for the main process
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MainProcess] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("pystatsd.main")


class WorkerProcess(mp.Process):
    """
    Wrapper around multiprocessing.Process for pystatsd workers.
    """
    def __init__(self, worker_id: int, config: ServerConfig) -> None:
        super().__init__(name=f"Worker-{worker_id}")
        self.worker_id = worker_id
        self.config = config
        # Shared memory value for heartbeat timestamp (double)
        self.heartbeat_ts = mp.Value('d', time.time())
        # Ensure daemon is False so signals propagate correctly and we can join them
        self.daemon = False

    def run(self) -> None:
        # This runs in the child process
        run_worker_process(self.config, self.worker_id, self.heartbeat_ts)


class Supervisor:
    """
    Manages the lifecycle of worker processes.
    """
    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.processes: List[WorkerProcess] = []
        self.shutdown_event = mp.Event()
        self._stopping = False
        self.obs_server: Optional[ObsServer] = None

    def start(self) -> None:
        """Start all worker processes."""
        # Start Observability Server
        # Pass a lambda that checks if all workers are alive
        self.obs_server = ObsServer(
            self.config.obs_host, 
            self.config.obs_port,
            readiness_check=self.check_workers_health
        )
        self.obs_server.start()

        num_workers = self.config.get_num_workers()
        if num_workers == 0:
            logger.critical("Number of workers is 0. Exiting.")
            sys.exit(1)

        logger.info(f"Starting {num_workers} worker processes...")
        logger.info(f"Listening on {self.config.host}:{self.config.port}")
        
        # Ensure we use 'spawn' for clean process state
        try:
            mp.set_start_method("spawn", force=True)
        except RuntimeError:
            pass  # Context might already be set

        for i in range(num_workers):
            # Pass a deep copy of config to ensure immutability across processes
            # Pydantic models are immutable by default if frozen=True, 
            # but model_copy(deep=True) is a good practice for IPC.
            worker_config = self.config.model_copy(deep=True)
            p = WorkerProcess(worker_id=i + 1, config=worker_config)
            p.start()
            self.processes.append(p)
            logger.info(f"Started worker {i + 1} (PID: {p.pid})")

    def check_workers_health(self) -> bool:
        """
        Check if all worker processes are alive and healthy.
        Returns False if any worker has died unexpectedly or is stuck.
        """
        if not self.processes:
            # If no processes started yet, we are not ready
            return False
            
        now = time.time()
        # Allow 3 missed heartbeats + 5s buffer
        timeout_threshold = (self.config.flush_interval * 3) + 5.0
        
        for p in self.processes:
            if not p.is_alive():
                logger.error(f"Worker {p.worker_id} (PID: {p.pid}) is dead!")
                return False
            
            # Check heartbeat timestamp
            last_beat = p.heartbeat_ts.value
            if now - last_beat > timeout_threshold:
                logger.error(f"Worker {p.worker_id} (PID: {p.pid}) is stuck! Last heartbeat: {now - last_beat:.1f}s ago")
                return False
                
        return True
        return True

    def stop(self, reason: str, timeout: float = SHUTDOWN_TIMEOUT) -> None:
        """Stop all workers gracefully."""
        if self._stopping:
            return
        self._stopping = True
        logger.info(f"Stopping supervisor: {reason}")

        # Stop Observability Server
        if self.obs_server:
            self.obs_server.stop()

        # Terminate workers
        for p in self.processes:
            if p.is_alive():
                logger.info(f"Terminating worker {p.worker_id} (PID: {p.pid})...")
                p.terminate()

        # Wait for them to exit
        start_time = time.time()
        for p in self.processes:
            join_timeout = max(0.0, timeout - (time.time() - start_time))
            p.join(join_timeout)
            if p.is_alive():
                logger.error(f"Worker {p.worker_id} did not exit in time. Killing...")
                p.kill()
        
        logger.info("All workers stopped.")

    def monitor(self) -> int:
        """
        Main loop to monitor worker health.
        Returns exit code.
        """
        try:
            while not self.shutdown_event.is_set():
                all_dead = True
                for p in self.processes:
                    if p.is_alive():
                        all_dead = False
                    else:
                        # A worker has died
                        logger.critical(f"Worker {p.worker_id} (PID: {p.pid}) died unexpectedly with exit code {p.exitcode}.")
                        # For MVP, if one worker dies, we shut down everything to avoid partial state.
                        # Future versions might implement restart logic.
                        self.stop(reason="Worker crash")
                        return 3
                
                if all_dead and self.processes:
                    logger.error("All workers are dead.")
                    return 3
                
                time.sleep(1.0)
        except KeyboardInterrupt:
            self.stop(reason="KeyboardInterrupt in monitor loop")
        
        return 0


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PyStatsD-Helix Server")
    parser.add_argument("-c", "--config", help="Path to configuration file")
    parser.add_argument("-w", "--workers", type=int, help="Number of worker processes")
    parser.add_argument("--host", help="Bind host")
    parser.add_argument("--port", type=int, help="Bind port")
    parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Log level")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_cli()
    args = parser.parse_args(argv)

    # Prepare CLI overrides
    cli_overrides = {}
    if args.workers is not None:
        cli_overrides["num_workers"] = args.workers
    if args.host:
        cli_overrides["host"] = args.host
    if args.port:
        cli_overrides["port"] = args.port
    if args.log_level:
        cli_overrides["log_level"] = args.log_level

    # Load Config
    try:
        config = load_config(path=args.config, cli_overrides=cli_overrides)
    except ConfigError as e:
        print(f"Error loading configuration: {e}", file=sys.stderr)
        return 2

    # Update main process logging level
    logger.setLevel(config.log_level)

    supervisor = Supervisor(config)

    # Signal Handling
    def handle_signal(signum, frame):
        logger.info(f"Received signal {signal.Signals(signum).name}")
        supervisor.shutdown_event.set()
        supervisor.stop(reason=f"Signal {signum}")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Start
    try:
        supervisor.start()
        return supervisor.monitor()
    except Exception as e:
        logger.critical(f"Supervisor crashed: {e}", exc_info=True)
        supervisor.stop(reason="Supervisor crash")
        return 1


def cli_entry():
    """Entry point for setuptools console_scripts."""
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())
