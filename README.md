# ThingWire

**WoT Thing Description → MCP tools. Point AI agents at real hardware.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![ESP32-S3](https://img.shields.io/badge/Platform-ESP32--S3-E7352C.svg?logo=espressif&logoColor=white)](https://www.espressif.com/)
[![MQTT](https://img.shields.io/badge/Protocol-MQTT-660066.svg?logo=eclipsemosquitto&logoColor=white)](https://mqtt.org/)
[![W3C WoT TD](https://img.shields.io/badge/Standard-W3C%20WoT%20TD-005A9C.svg?logo=w3c&logoColor=white)](https://www.w3.org/TR/wot-thing-description11/)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-8B5CF6.svg)](https://modelcontextprotocol.io/)

## What it does

Your ESP32 publishes a [W3C WoT Thing Description](https://www.w3.org/TR/wot-thing-description11/) over MQTT. ThingWire reads it and auto-generates MCP tools. Claude (or any MCP client) can then call `read_temperature`, `set_relay`, etc. with no hand-written tool definitions.

A safety layer sits between the AI and the hardware: per-device action allowlists, rate limits, dangerous-action confirmation, and a full audit log.

## How It Works

```
┌─────────────────────┐
│  Claude / Cursor /   │
│  Any MCP Client      │
└─────────┬───────────┘
          │ MCP tools (read_temperature, set_relay, ...)
          ▼
┌─────────────────────┐
│  ThingWire Gateway   │  Python, FastMCP, paho-mqtt
│  ┌────────────────┐  │
│  │  Safety Layer   │  │  allowlists, rate limits, audit log
│  └────────────────┘  │
└─────────┬───────────┘
          │ MQTT
          ▼
┌─────────────────────┐
│  ESP32-S3 Device     │  WoT Thing Description + sensors + actuators
│  DHT22 / PIR / Relay │
└─────────────────────┘
```

1. ESP32 boots and publishes its capabilities as a WoT Thing Description to MQTT
2. Gateway subscribes, parses the TD, and compiles it into typed MCP tools
3. AI agent calls tools like `read_temperature` or `set_relay`
4. Safety layer checks permissions and rate limits before passing the command on
5. Command goes to the device over MQTT; response comes back the same way

## Quickstart

You don't need hardware. The virtual device simulator gets you running without any physical devices.

### From source

```bash
git clone https://github.com/thingwire-dev/thingwire
cd thingwire

# Install
pip install -e ".[dev]"

# Start MQTT broker
docker compose up -d

# Start virtual device (simulates ESP32 with sensors + relay)
python scripts/virtual_device.py --broker localhost

# Start gateway (new terminal)
thingwire serve
```

### PyPI (coming soon)

```bash
pip install thingwire
thingwire serve
```

### Connect your MCP client

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "thingwire": {
      "command": "thingwire",
      "args": ["serve"],
      "env": {
        "THINGWIRE_MQTT_BROKER": "localhost"
      }
    }
  }
}
```

## Example queries

Once connected, you can ask things like:

- "What devices are online right now?"
- "What's the temperature and humidity?"
- "Turn on the relay."
- "Is there motion in the room?"
- "Show me the last 10 commands in the audit log."

The agent sees MCP tools generated from the device's own Thing Description, including units and safety constraints.

## Repository layout

```
thingwire/
├── src/thingwire/            Python gateway
│   ├── cli.py                    CLI entry point
│   ├── td_loader.py              Parse WoT Thing Descriptions
│   ├── tool_compiler.py          WoT TD → MCP tools
│   ├── mcp_server.py             MCP server wiring
│   ├── mqtt_bridge.py            MQTT device discovery + commands
│   ├── safety.py                 Permissions, rate limits, deadman switch
│   └── audit_log.py              SQLite audit trail
├── firmware/                 ESP32-S3 firmware (PlatformIO/Arduino)
│   └── src/
│       ├── main.cpp, wifi_manager.cpp, mqtt_client.cpp
│       ├── sensor_registry.cpp, actuator_controller.cpp
│       └── wot_td_generator.cpp
├── td-library/               Pre-built Thing Descriptions for common hardware
├── scripts/
│   └── virtual_device.py     Device simulator (no hardware needed)
└── examples/                 Demo scenarios
```

## Safety

Every actuator command passes through the safety layer before reaching any device.

- **Per-device allowlists** define exactly which actions each device permits
- **Rate limiting** with sliding windows prevents runaway commands
- **Dangerous action confirmation** for actions marked `safe: false` in the WoT TD
- **Deadman switch** disables actuators if the device heartbeat stops
- **SQLite audit log** records every command with timestamp, parameters, and result

The safety config lives in `safety_config.yaml`. The AI agent can only operate within the boundaries you define there.

## Hardware

**Tested with:**

| Component | Purpose |
|-----------|---------|
| ESP32-S3-DevKitC-1 | Microcontroller (any ESP32-S3 works) |
| DHT22 | Temperature and humidity sensor |
| PIR sensor | Motion detection |
| 5V relay module | Actuator output |

Any ESP32-S3 board works. Flash the firmware with PlatformIO, configure `firmware/src/config.h`, and it will start publishing its Thing Description on boot.

## MQTT Topics

```
thingwire/{device_id}/td          # WoT Thing Description (retained)
thingwire/{device_id}/telemetry   # Sensor readings (every 5s)
thingwire/{device_id}/command     # Actuator commands
thingwire/{device_id}/status      # Online/offline (LWT)
```

## Configuration

All settings via environment variables prefixed with `THINGWIRE_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `THINGWIRE_MQTT_BROKER` | `localhost` | MQTT broker hostname |
| `THINGWIRE_MQTT_PORT` | `1883` | MQTT broker port |
| `THINGWIRE_DEVICE_TOPIC_PREFIX` | `thingwire` | MQTT topic prefix for device discovery |
| `THINGWIRE_AUDIT_DB_PATH` | `data/audit.db` | Path to SQLite audit log |
| `THINGWIRE_SAFETY_CONFIG_PATH` | `safety_config.yaml` | Path to safety rules |
| `THINGWIRE_LOG_LEVEL` | `INFO` | Log level |

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Lint
ruff check src/

# Type check
mypy src/thingwire/ --strict
```

## Roadmap

- [ ] Multi-device orchestration (coordinate actions across devices)
- [ ] Prometheus metrics export
- [ ] OTA firmware updates via MCP tools
- [ ] BLE device support alongside MQTT
- [ ] Home Assistant integration
- [ ] Pre-built TD templates for common sensors

## Contributing

PRs welcome. If you're adding a new device type, include a WoT Thing Description and a virtual device simulator for it.

## License

[MIT](LICENSE)

