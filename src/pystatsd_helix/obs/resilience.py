"""
Resilience patterns: Circuit Breaker, Backpressure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    recovery_timeout: float = 30.0

class CircuitBreaker:
    """
    Simple Circuit Breaker implementation.
    """
    def __init__(self, name: str, config: CircuitBreakerConfig = CircuitBreakerConfig()):
        self.name = name
        self.config = config
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0.0

    def allow_request(self) -> bool:
        if self.state == CircuitState.CLOSED:
            return True
        
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.config.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                return True
            return False
            
        if self.state == CircuitState.HALF_OPEN:
            # Allow one request to probe
            return True
            
        return False

    def record_success(self):
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failures = 0

    def record_failure(self):
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.CLOSED:
            if self.failures >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
        
        elif self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN

@dataclass
class BackpressurePolicy:
    """
    Policy for handling overload.
    """
    max_queue_size: int = 10000
    drop_probability: float = 0.0

    def should_drop(self, current_queue_size: int) -> bool:
        if current_queue_size >= self.max_queue_size:
            return True
        return False

@dataclass
class DegradationProfile:
    """
    Profile for degraded mode.
    """
    enabled: bool = False
    disable_tags: bool = False
    reduce_flush_rate: bool = False
