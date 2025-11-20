from __future__ import annotations

import asyncio
import importlib.metadata
import logging
from typing import List, Type, Dict, Any

from ..config import ServerConfig
from .base import Backend, BackendLoadError

logger = logging.getLogger("pystatsd.backends.loader")

def load_active_backends(config: ServerConfig) -> List[Type[Backend]]:
    """
    Load backend classes based on configuration.
    This runs in the worker process during initialization.
    """
    active_classes = []
    
    # 1. Load built-in backends explicitly (fast path)
    # We map names to module paths or classes
    # For MVP, we hardcode logger.
    # In full version, we use entry points.
    
    # Map of name -> (module, class_name)
    BUILTINS = {
        "logger": ("pystatsd_helix.backends.logger", "LoggerBackend"),
        "graphite": ("pystatsd_helix.backends.graphite", "GraphiteBackend"),
    }

    for name in config.active_backends:
        if name in BUILTINS:
            mod_name, cls_name = BUILTINS[name]
            try:
                mod = __import__(mod_name, fromlist=[cls_name])
                cls = getattr(mod, cls_name)
                active_classes.append(cls)
            except ImportError as e:
                raise BackendLoadError(f"Failed to import built-in backend '{name}': {e}")
            except AttributeError:
                raise BackendLoadError(f"Backend class '{cls_name}' not found in '{mod_name}'")
        else:
            # TODO: Load from entry points for external plugins
            logger.warning(f"External backend '{name}' loading not implemented in MVP yet.")
            
    return active_classes

def create_backends(config: ServerConfig) -> List[Backend]:
    """
    Instantiate and setup backends.
    """
    backend_classes = load_active_backends(config)
    instances = []
    loop = asyncio.get_running_loop()
    
    for cls in backend_classes:
        try:
            instance = cls()
            # Get specific config
            # config.backend_configs has fields like 'logger', 'graphite'
            # We assume backend name matches config field name
            backend_cfg = getattr(config.backend_configs, instance.name, None)
            
            # If backend is active, its config must be present (validated in ServerConfig)
            if backend_cfg is None and instance.name in config.active_backends:
                 # Should not happen due to validation
                 logger.error(f"Config for {instance.name} is missing!")
                 continue

            # We need to await setup, but we are in a sync function?
            # No, create_backends is called from async Worker.run usually?
            # Wait, Worker.run calls create_backends.
            # But setup is async.
            # We should probably return instances and let worker await setup, 
            # OR make create_backends async.
            # Blueprint 03 says: self.backends = create_backends(config) in __init__? 
            # No, in run().
            # Let's make this function async or handle setup outside.
            # Blueprint 08 says: "Loader... resolve() returns classes, then await backend.setup()"
            # So this function should probably just return instances, and caller calls setup.
            
            instances.append(instance)
            
            # We will call setup in the caller (Worker.run) to be clean?
            # Or we can do it here if we change signature to async.
            # Let's stick to returning instances and helper to setup.
            
        except Exception as e:
            raise BackendLoadError(f"Failed to instantiate backend {cls.name}: {e}")

    # We need to run setup for them.
    # Since we can't await here easily without changing signature, 
    # let's change signature to async or let caller do it.
    # Worker.run is async, so it can await.
    # But wait, Worker.__init__ calls create_backends in the blueprint snippet?
    # Blueprint 03:
    # class Worker:
    #    def __init__(...):
    #        self.backends = load_active_backends(config) # This loads classes/instances?
    # Blueprint 03 snippet shows: self.backends = load_active_backends(config) in __init__.
    # But setup needs loop.
    # So __init__ just loads, run() calls setup?
    # Actually Blueprint 03 snippet says:
    # "self.backends: list = load_active_backends(config)" in __init__
    # And then in run(): nothing explicit about setup?
    # Wait, Blueprint 08 says: "Worker start: setup".
    # So let's make create_backends return initialized (but not setup) instances.
    # And we add a helper `setup_backends`.
    
    return instances

async def setup_backends(backends: List[Backend], config: ServerConfig):
    loop = asyncio.get_running_loop()
    for b in backends:
        cfg = getattr(config.backend_configs, b.name)
        await b.setup(cfg, loop=loop)
