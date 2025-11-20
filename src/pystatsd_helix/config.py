from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Literal, Sequence

# Use tomllib for TOML parsing (Python 3.11+)
try:
    import tomllib
except ImportError:
    # Fallback for older python versions if needed, though project requires 3.12+
    import tomli as tomllib  # type: ignore

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    model_validator,
)

class LoggerConfig(BaseModel):
    """Configuration for the Logger backend."""
    model_config = ConfigDict(frozen=True)
    
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    pretty_print: bool = False
    sample_percent: float = Field(default=100.0, ge=0.0, le=100.0)
    destination: Literal["stdout", "stderr", "file"] = "stdout"
    file_path: Path | None = None
    max_bytes: int = Field(default=10 * 1024 * 1024)
    backup_count: int = 5


class GraphiteConfig(BaseModel):
    """Configuration for the Graphite backend."""
    model_config = ConfigDict(frozen=True)
    
    host: str = Field(default="127.0.0.1", description="Graphite plaintext endpoint")
    port: int = Field(default=2003, ge=1, le=65535)
    prefix: str = "statsd"
    tag_format: Literal["graphite", "datadog"] = "graphite"
    enable_tls: bool = False
    ca_file: Path | None = None
    connect_timeout: PositiveFloat = 5.0
    write_timeout: PositiveFloat = 5.0
    batch_size: PositiveInt = 1000
    batch_bytes: PositiveInt = 64 * 1024
    max_retries: PositiveInt = 3
    retry_backoff: PositiveFloat = 1.0
    timeout: PositiveFloat = 5.0 # Deprecated, keep for compat or remove? Blueprint says connect_timeout/write_timeout
    connect_retry_max: PositiveInt = 3 # Deprecated


class BackendConfigs(BaseModel):
    """Container for all backend configurations."""
    model_config = ConfigDict(frozen=True)
    
    logger: LoggerConfig = Field(default_factory=LoggerConfig)
    graphite: GraphiteConfig | None = None


class ServerConfig(BaseModel):
    """
    Main server configuration.
    Acts as the Single Source of Truth (SSOT) for the application.
    """
    model_config = ConfigDict(frozen=True)
    
    host: str = "0.0.0.0"
    port: int = Field(default=8125, ge=1, le=65535)
    num_workers: int = Field(default=0, ge=0, description="0 = auto-detect based on CPU cores")
    flush_interval: PositiveFloat = 10.0
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    
    # List of active backend names (must match keys in BackendConfigs)
    active_backends: Sequence[str] = Field(default_factory=lambda: ["logger"], min_length=1)
    
    backend_configs: BackendConfigs = Field(default_factory=BackendConfigs)
    
    # (min_val, max_val, significant_figures)
    timer_histogram_config: tuple[int, int, int] = (1, 3600000, 3)
    
    # Cardinality Guard
    max_series: int = Field(default=10000, ge=100, description="Maximum number of unique time series per worker")

    # Network Tuning
    socket_buffer_size: int | None = Field(
        default=4 * 1024 * 1024, 
        description="UDP receive buffer size in bytes. Set to 0 or None to use OS default."
    )

    # Observability
    obs_host: str = "0.0.0.0"
    obs_port: int = Field(default=9102, ge=1, le=65535)

    @model_validator(mode='after')
    def validate_active_backends(self) -> ServerConfig:
        """Ensure all active backends have corresponding configurations."""
        for backend_name in self.active_backends:
            if not hasattr(self.backend_configs, backend_name):
                raise ValueError(f"Unknown backend in active_backends: {backend_name}")
            
            # Check if the config for this backend is actually set (not None)
            # For logger, it has a default factory, but for graphite it defaults to None.
            backend_cfg = getattr(self.backend_configs, backend_name)
            if backend_cfg is None:
                raise ValueError(
                    f"Backend '{backend_name}' is active but its configuration is missing (None)."
                )
        return self

    @model_validator(mode='after')
    def validate_histogram_config(self) -> ServerConfig:
        """Sanity check for HdrHistogram parameters."""
        min_val, max_val, sigfigs = self.timer_histogram_config
        if min_val < 1:
            raise ValueError("Histogram min_val must be >= 1")
        if max_val < min_val * 10:
            raise ValueError("Histogram max_val must be at least 10x min_val")
        if not (1 <= sigfigs <= 5):
            raise ValueError("Histogram significant_figures must be between 1 and 5")
        return self

    def get_num_workers(self) -> int:
        """
        Calculate the actual number of workers to spawn.
        If num_workers is 0, use os.cpu_count().
        """
        if self.num_workers > 0:
            return self.num_workers
        
        count = os.cpu_count()
        # Fallback to 1 if cpu_count returns None
        return count if count else 1


class ConfigError(RuntimeError):
    """Raised when configuration cannot be loaded or validated."""
    pass


def _merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries.
    Override values overwrite base values.
    """
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_dicts(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None, cli_overrides: dict[str, Any] | None = None) -> ServerConfig:
    """
    Load configuration from multiple sources with the following precedence:
    1. CLI Overrides
    2. Environment Variables (PYSTATSD__*)
    3. Config File (TOML/YAML)
    4. Defaults
    
    Args:
        path: Path to the configuration file.
        cli_overrides: Dictionary of overrides from command line arguments.
        
    Returns:
        Validated ServerConfig object.
        
    Raises:
        ConfigError: If loading or validation fails.
    """
    config_data: dict[str, Any] = {}

    # 1. Load from file if provided
    if path:
        file_path = Path(path)
        if not file_path.exists():
            raise ConfigError(f"Configuration file not found: {file_path}")
        
        try:
            with open(file_path, "rb") as f:
                if file_path.suffix == ".toml":
                    config_data = tomllib.load(f)
                elif file_path.suffix in (".yaml", ".yml"):
                    config_data = yaml.safe_load(f) or {}
                else:
                    raise ConfigError(f"Unsupported configuration format: {file_path.suffix}")
        except Exception as e:
            raise ConfigError(f"Failed to parse configuration file: {e}") from e

    # Flatten [server] section if present
    # The TOML/YAML might have a [server] section, but our ServerConfig model is flat for those fields.
    if "server" in config_data and isinstance(config_data["server"], dict):
        server_section = config_data.pop("server")
        config_data.update(server_section)

    # 2. Load from Environment Variables
    # Convention: PYSTATSD__SECTION__KEY (double underscore separator)
    # Example: PYSTATSD__SERVER__PORT=8126 -> {'server': {'port': 8126}}
    # Note: Since our ServerConfig is flat for server params but nested for backends,
    # we need to map carefully.
    # Actually, ServerConfig has fields like 'host', 'port' at top level, 
    # and 'backend_configs' nested.
    # Let's implement a simple env loader that maps PYSTATSD__HOST to host,
    # and PYSTATSD__BACKEND_CONFIGS__LOGGER__LEVEL to backend_configs.logger.level
    
    env_config: dict[str, Any] = {}
    prefix = "PYSTATSD__"
    for k, v in os.environ.items():
        if k.startswith(prefix):
            key_path = k[len(prefix):].lower().split("__")
            # Convert value to appropriate type if possible (basic int/float/bool)
            # This is a simple heuristic; Pydantic will do strict validation later.
            if v.lower() in ("true", "yes"):
                val: Any = True
            elif v.lower() in ("false", "no"):
                val = False
            else:
                try:
                    val = int(v)
                except ValueError:
                    try:
                        val = float(v)
                    except ValueError:
                        val = v
            
            # Build nested dict
            current_level = env_config
            for part in key_path[:-1]:
                if part not in current_level:
                    current_level[part] = {}
                current_level = current_level[part]
                if not isinstance(current_level, dict):
                     # Conflict: trying to use a value as a dict
                     break
            else:
                current_level[key_path[-1]] = val

    config_data = _merge_dicts(config_data, env_config)

    # 3. Apply CLI Overrides
    if cli_overrides:
        # Filter out None values
        clean_overrides = {k: v for k, v in cli_overrides.items() if v is not None}
        config_data = _merge_dicts(config_data, clean_overrides)

    # 4. Validate and Return
    try:
        return ServerConfig.model_validate(config_data)
    except ValidationError as e:
        # Format validation errors nicely
        error_messages = []
        for error in e.errors():
            loc = " -> ".join(str(l) for l in error["loc"])
            msg = error["msg"]
            error_messages.append(f"- {loc}: {msg}")
        raise ConfigError("Configuration validation failed:\n" + "\n".join(error_messages)) from e
