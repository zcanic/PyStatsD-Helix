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
        Checks:
        - Worker status
        - Flush queue size (TODO)
        - Circuit breaker status (TODO)
        """
        return True
