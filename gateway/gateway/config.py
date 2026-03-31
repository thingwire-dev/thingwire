"""Gateway configuration via environment variables."""

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
    log_level: str = "INFO"

    model_config = {"env_prefix": "THINGWIRE_"}
