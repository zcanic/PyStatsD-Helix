"""
Health check endpoints and logic.
"""
from __future__ import annotations

class HealthCheck:
    """
    Health check logic.
    """
    
    @staticmethod
    def is_live() -> bool:
        """
        Liveness probe.
        Returns True if the process is running.
        """
        return True
    
    @staticmethod
    def is_ready() -> bool:
        """
        Readiness probe.
        Returns True if the application is ready to accept traffic.
        
        Note: This is a static local check. The ObsServer injects a more comprehensive
        check that verifies worker process liveness.
        """
        return True
