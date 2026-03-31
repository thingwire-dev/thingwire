# CLI Reference

ThingWire provides a single `thingwire` command with subcommands for running the gateway, managing devices, and working with Thing Descriptions.

Install: `pip install thingwire`

## Commands

### `thingwire serve`

Start the ThingWire gateway and MCP server.

```bash
thingwire serve [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--broker` | `localhost` | MQTT broker host |
| `--port` | `1883` | MQTT broker port |
| `--transport` | `stdio` | MCP transport (`stdio` or `sse`) |
| `--log-level` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `--ha-discovery` | off | Publish Home Assistant MQTT discovery messages |

```bash
# Basic usage
thingwire serve

# With custom broker and HA discovery
thingwire serve --broker 192.168.1.50 --ha-discovery

# SSE transport for web clients
thingwire serve --transport sse
```

### `thingwire init`

Initialize a ThingWire project in the current directory. Creates `docker-compose.yml`, `mosquitto.conf`, and `safety_config.yaml`.

```bash
thingwire init
```

Skips files that already exist.

```bash
mkdir my-project && cd my-project
thingwire init
docker compose up -d
thingwire serve
```

### `thingwire devices`

Discover devices currently connected to the MQTT broker.

```bash
thingwire devices [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--broker` | `localhost` | MQTT broker host |
| `--port` | `1883` | MQTT broker port |
| `--timeout` | `5.0` | Discovery timeout in seconds |

```bash
thingwire devices --broker 192.168.1.50 --timeout 10
```

Output:
```
Listening for devices on 192.168.1.50:1883 (10.0s)...
  thingwire-demo-001  ThingWire Demo Device  [online]  3 properties, 1 actions
```

### `thingwire sim`

Run a virtual device simulator. Publishes fake sensor data and responds to commands over MQTT. No physical hardware needed.

```bash
thingwire sim [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--device-id` | `thingwire-demo-001` | Device ID |
| `--broker` | `localhost` | MQTT broker host |
| `--port` | `1883` | MQTT broker port |

```bash
# Terminal 1: start broker
docker compose up -d

# Terminal 2: start virtual device
thingwire sim --device-id kitchen-sensor

# Terminal 3: start gateway
thingwire serve
```

### `thingwire validate`

Validate a WoT Thing Description JSON file.

```bash
thingwire validate <TD_FILE>
```

```bash
thingwire validate td-library/bme280.json
```

Output:
```
VALID: BME280 Environmental Sensor
  ID: urn:thingwire:device:{device_id}
  Properties: 3 (temperature, pressure, humidity)
  Actions: 0 ()
```

Exits with code 1 if the TD is invalid.

### `thingwire export`

Export a WoT Thing Description as AI tool definitions.

```bash
thingwire export <TD_FILE> [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--format` | `openai` | Output format (`openai` or `mcp`) |
| `--prefix` | none | Device prefix for tool names |

```bash
# OpenAI function calling format
thingwire export td-library/dht22-relay.json

# MCP format with device prefix
thingwire export td-library/dht22-relay.json --format mcp --prefix kitchen

# Pipe to file
thingwire export td-library/servo-motor.json > tools.json
```

### `thingwire firmware-config`

Generate firmware `config.h` from a YAML device configuration file.

```bash
thingwire firmware-config <YAML_FILE> [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `-o`, `--output` | `firmware/src/config.h` | Output file path |

```bash
# Edit device.yaml with your settings
thingwire firmware-config firmware/device.yaml

# Custom output path
thingwire firmware-config firmware/device.yaml -o /tmp/config.h
```

The YAML file format:

```yaml
device_id: kitchen-sensor-001

wifi:
  ssid: "MyNetwork"
  password: "secret"

mqtt:
  broker: "192.168.1.100"
  port: 1883

telemetry_interval_ms: 5000

pins:
  dht: 4
  pir: 5
  relay: 12
  led: 13
```

After generating, flash the firmware:

```bash
cd firmware && pio run --target upload
```

## Environment Variables

All gateway settings can also be set via environment variables prefixed with `THINGWIRE_`:

| Variable | Default | Description |
|----------|---------|-------------|
| `THINGWIRE_MQTT_BROKER` | `localhost` | MQTT broker hostname |
| `THINGWIRE_MQTT_PORT` | `1883` | MQTT broker port |
| `THINGWIRE_DEVICE_TOPIC_PREFIX` | `thingwire` | MQTT topic prefix |
| `THINGWIRE_AUDIT_DB_PATH` | `data/audit.db` | SQLite audit log path |
| `THINGWIRE_SAFETY_CONFIG_PATH` | `safety_config.yaml` | Safety rules path |
| `THINGWIRE_LOG_LEVEL` | `INFO` | Log level |
| `THINGWIRE_TRANSPORT` | `stdio` | MCP transport |
| `THINGWIRE_HA_DISCOVERY` | `0` | Enable HA discovery (`1` to enable) |

CLI flags take precedence over environment variables.
