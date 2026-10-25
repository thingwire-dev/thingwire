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

### `thingwire estop`

Emergency stop status and the human-only reset. Agents can trip a stop through the
`emergency_stop` MCP tool; no MCP tool can reset one.

```bash
thingwire estop status
thingwire estop reset --device desk-bot-1 --who operator --reason "area clear"
thingwire estop reset --all --who operator --reason "global stop cleared"
```

`reset` runs on the gateway host: it files a request in the safety state DB and waits
(`--wait`, default 15 s) for the running gateway to apply it on its next sweep. The gateway
checks `--who` against the authorized resetters; a request older than
`THINGWIRE_ESTOP_RESET_TTL_SECONDS` expires. Exit code 0 means reset (or nothing latched).

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
| `THINGWIRE_MQTT_USERNAME` | empty | Broker login (ACL profile user `thingwire-gateway`); empty means anonymous |
| `THINGWIRE_MQTT_PASSWORD` | empty | Broker password; never logged |
| `THINGWIRE_SAFETY_STATE_DB_PATH` | `data/safety_state.db` | Latched e-stop state and the reset inbox (mode 0600) |
| `THINGWIRE_ESTOP_RESETTERS` | empty | Comma separated names allowed to reset; overrides `global.estop.authorized_resetters` |
| `THINGWIRE_ESTOP_RESET_TTL_SECONDS` | `60` | A reset request older than this expires unapplied |
| `THINGWIRE_SAFETY_SWEEP_SECONDS` | `5` | Deadman sweep and reset inbox interval |
| `THINGWIRE_RECORD_PATH` | empty | Record the session to this MCAP file (`serve --record`) |

CLI flags take precedence over environment variables.
