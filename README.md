# ThingWire

**Give AI agents hands.** Control real hardware through natural language.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?logo=python&logoColor=white)](https://python.org)
[![ESP32-S3](https://img.shields.io/badge/Platform-ESP32--S3-E7352C.svg?logo=espressif&logoColor=white)](https://www.espressif.com/)
[![MQTT](https://img.shields.io/badge/Protocol-MQTT-660066.svg?logo=eclipsemosquitto&logoColor=white)](https://mqtt.org/)
[![W3C WoT TD](https://img.shields.io/badge/Standard-W3C%20WoT%20TD-005A9C.svg?logo=w3c&logoColor=white)](https://www.w3.org/TR/wot-thing-description11/)
[![MCP Compatible](https://img.shields.io/badge/MCP-Compatible-8B5CF6.svg)](https://modelcontextprotocol.io/)

<!-- demo gif here -->

## Why ThingWire

AI agents are great at reasoning. They're terrible at interacting with the physical world. ThingWire bridges that gap. Your ESP32 publishes a W3C WoT Thing Description over MQTT, the gateway reads it, and auto-generates MCP tools. Now Claude can read your temperature sensor and flip your relay, with a real safety layer between "AI said so" and "the relay clicked."

No custom integrations. No hand-written tool definitions. Plug in a new device, and the AI agent discovers it automatically.

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

1. ESP32 boots and publishes its capabilities as a [WoT Thing Description](https://www.w3.org/TR/wot-thing-description11/) to MQTT
2. Gateway subscribes, parses the TD, and compiles it into typed MCP tools
3. AI agent calls tools like `read_temperature` or `set_relay`
4. Safety layer checks permissions, rate limits, and flags dangerous actions
5. Command goes to the device over MQTT. Response comes back the same way.

## Quickstart

You don't need hardware. The virtual device simulator gets you running in under 5 minutes.

### Option A: pip install

```bash
pip install thingwire

# Initialize project files
thingwire init

# Start MQTT broker
docker compose up -d

# Start virtual device (simulates ESP32 with sensors + relay)
python -m thingwire.virtual_device  # OR: python scripts/virtual_device.py

# Start gateway (new terminal)
thingwire serve
```

### Option B: From source

```bash
git clone https://github.com/thingwire-dev/thingwire
cd thingwire
pip install -e ".[dev]"
thingwire serve
```

### Connect your MCP client

Add this to your `claude_desktop_config.json` (or equivalent for Cursor):

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

Done. Open Claude Desktop and start talking to your hardware.

## What You Can Ask Claude

Once connected, try these:

- *"What devices are online right now?"*
- *"What's the temperature and humidity?"*
- *"Turn on the relay."*
- *"Is there motion in the room?"*
- *"Turn on the relay when temperature goes above 30C."*
- *"Show me the last 10 commands in the audit log."*
- *"What actions are allowed on this device?"*

The AI agent sees real MCP tools generated from the device's own Thing Description. It knows what the device can do, what units the sensors report in, and what safety constraints exist.

## Architecture

```
thingwire/
├── src/thingwire/            Python gateway (pip install thingwire)
│   ├── cli.py                    CLI: thingwire serve / init / devices
│   ├── td_loader.py              Parse WoT Thing Descriptions
│   ├── tool_compiler.py          WoT TD → MCP tools (core differentiator)
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
│   └── virtual_device.py    Device simulator (no hardware needed)
└── examples/                 Demo scenarios
```

## Safety

This is not a toy. AI agents controlling physical hardware need guardrails. Every actuator command passes through the safety layer before reaching any device.

- **Per-device allowlists** configure exactly which actions each device permits
- **Rate limiting** with sliding windows prevents runaway commands
- **Dangerous action confirmation** for actions marked `safe: false` in the WoT TD
- **Deadman switch** disables actuators if the device heartbeat stops
- **SQLite audit log** records every command with timestamp, parameters, and result

The safety config lives in `safety_config.yaml`. You define what's allowed. The AI agent can only operate within those boundaries.

## Hardware

**For real hardware (MVP):**

| Component | Purpose |
|-----------|---------|
| ESP32-S3-DevKitC-1 | Microcontroller (any ESP32-S3 works) |
| DHT22 | Temperature and humidity sensor |
| PIR sensor | Motion detection |
| 5V relay module | Actuator output |

**No hardware?** The virtual device simulator generates realistic sensor data and responds to actuator commands. Everything works the same from the AI agent's perspective.

```bash
python3.11 scripts/virtual_device.py --broker localhost
```

## MQTT Topics

```
thingwire/{device_id}/td          # WoT Thing Description (retained)
thingwire/{device_id}/telemetry   # Sensor readings (every 5s)
thingwire/{device_id}/command     # Actuator commands
thingwire/{device_id}/status      # Online/offline (LWT)
```

## Configuration

All settings via environment variables, prefixed with `THINGWIRE_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `THINGWIRE_MQTT_BROKER` | `localhost` | MQTT broker hostname |
| `THINGWIRE_MQTT_PORT` | `1883` | MQTT broker port |
| `THINGWIRE_DEVICE_TOPIC_PREFIX` | `thingwire` | MQTT topic prefix for device discovery |
| `THINGWIRE_AUDIT_DB_PATH` | `data/audit.db` | Path to SQLite audit log |
| `THINGWIRE_SAFETY_CONFIG_PATH` | `safety_config.yaml` | Path to safety rules |
| `THINGWIRE_LOG_LEVEL` | `INFO` | Log level (DEBUG, INFO, WARNING, ERROR) |

## Development

```bash
cd gateway

# Install dependencies
python3.11 -m pip install -r requirements.txt
python3.11 -m pip install -e ".[dev]"

# Run tests
python3.11 -m pytest tests/ -v

# Lint
python3.11 -m ruff check gateway/

# Type check
python3.11 -m mypy gateway/ --strict
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

---

**Suggested GitHub topics:** `iot` `mcp` `ai-agent` `esp32` `home-automation` `wot` `claude` `mqtt` `hardware` `sensors`
