# Demo: Temperature → Relay Control

AI reads temperature sensor. If too hot (>28°C), it turns on the fan relay.

## What it shows

- End-to-end agent control of physical hardware
- Safety confirmation for relay actuation
- Audit logging of every command

## Prerequisites

- Docker (for Mosquitto broker)
- Python 3.11+

## Run it

```bash
# 1. Start the MQTT broker
cd ../../gateway
docker-compose up mosquitto -d

# 2. Start the virtual device (simulates ESP32)
cd ../../
python3.11 scripts/virtual_device.py --broker localhost

# 3. In another terminal, start the gateway
cd gateway
python3.11 -m gateway

# 4. Connect Claude Desktop (see config below) or run the agent script
python3.11 ../../examples/demo-temperature-relay/agent-script.py
```

## Claude Desktop config

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "thingwire": {
      "command": "python3.11",
      "args": ["-m", "gateway"],
      "cwd": "/path/to/thingwire/gateway",
      "env": {
        "THINGWIRE_MQTT_BROKER": "localhost"
      }
    }
  }
}
```

## Try these prompts in Claude

1. "What devices are connected?"
2. "What's the current temperature?"
3. "Is it too hot? If above 28°C, turn on the fan relay."
4. "Show me the audit log of recent commands."
