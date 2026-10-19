"""Gateway configuration via environment variables."""

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings


class GatewayConfig(BaseSettings):
    """ThingWire Gateway configuration.

    All settings can be overridden via environment variables
    prefixed with THINGWIRE_ (e.g., THINGWIRE_MQTT_BROKER).
    """

    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    device_topic_prefix: str = "thingwire"
    audit_db_path: str = "data/audit.db"
    safety_config_path: str = "safety_config.yaml"
    device_roster_path: str = "data/devices.json"
    log_level: str = "INFO"
    log_format: str = "text"  # "text" or "json"
    device_discovery_timeout: float = 30.0
    # Broker credentials for the ACL profile (docs/security-broker.md). Empty = anonymous.
    mqtt_username: str = ""
    mqtt_password: SecretStr = SecretStr("")
    # Latched e-stop state (survives restart) and the human reset inbox.
    safety_state_db_path: str = "data/safety_state.db"
    # Comma separated; overrides global.estop.authorized_resetters when set.
    estop_resetters: str = ""
    estop_reset_ttl_seconds: float = 60.0
    # How often the deadman sweep and reset inbox run.
    safety_sweep_seconds: float = 5.0
    # MCAP session recording; empty disables it.
    record_path: str = ""

    model_config = {"env_prefix": "THINGWIRE_"}

    @field_validator("mqtt_port")
    @classmethod
    def port_in_range(cls, v: int) -> int:
        """Validate MQTT port is in valid range."""
        if not 1 <= v <= 65535:
            msg = f"mqtt_port must be 1-65535, got {v}"
            raise ValueError(msg)
        return v

    @field_validator("safety_sweep_seconds", "estop_reset_ttl_seconds")
    @classmethod
    def positive_interval(cls, v: float) -> float:
        """Intervals must be positive."""
        if v <= 0:
            msg = f"interval must be positive, got {v}"
            raise ValueError(msg)
        return v

    def resetters(self) -> list[str] | None:
        """Authorized e-stop resetters from the environment, or None to use the YAML."""
        names = [n.strip() for n in self.estop_resetters.split(",") if n.strip()]
        return names or None

    @field_validator("log_level")
    @classmethod
    def valid_log_level(cls, v: str) -> str:
        """Validate log level is recognized."""
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            msg = f"log_level must be one of {valid}, got '{v}'"
            raise ValueError(msg)
        return upper

    @field_validator("log_format")
    @classmethod
    def valid_log_format(cls, v: str) -> str:
        """Validate log format."""
        valid = {"text", "json"}
        lower = v.lower()
        if lower not in valid:
            msg = f"log_format must be one of {valid}, got '{v}'"
            raise ValueError(msg)
        return lower
