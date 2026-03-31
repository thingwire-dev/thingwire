# Getting Started

Get from zero to AI-controlled hardware in under 30 minutes.

## Prerequisites

- Python 3.11+
- Docker (for Mosquitto MQTT broker)
- Optional: ESP32-S3 + sensors (or use the virtual device)

## Step 1: Clone the repo

```bash
git clone https://github.com/thegdsks/thingwire
cd thingwire
```

## Step 2: Start the MQTT broker

```bash
cd gateway
docker-compose up mosquitto -d
```

Verify it's running:
```bash
docker-compose logs mosquitto
# Should show: mosquitto version X.X.X running
```

## Step 3: Start a device

### Option A: Virtual device (no hardware needed)

```bash
cd ..
python3.11 scripts/virtual_device.py --broker localhost
```

You'll see:
```
Connected to MQTT broker at localhost:1883
Published WoT TD to thingwire/thingwire-demo-001/td
Subscribed to thingwire/thingwire-demo-001/command
```

### Option B: Real ESP32 hardware

```bash
cd firmware
pio run --target upload
```

The device will create a WiFi hotspot "ThingWire-Setup". Connect and enter your WiFi credentials at 192.168.4.1.

## Step 4: Start the gateway

```bash
cd gateway
python3.11 -m pip install -r requirements.txt
python3.11 -m gateway
```

You'll see:
```
Connecting to MQTT broker at localhost:1883...
Waiting for device discovery (30s timeout)...
Discovered device: thingwire-demo-001 (ThingWire Demo Device)
Registered 4 tools for device thingwire-demo-001
Starting MCP server (transport: stdio)...
```

## Step 5: Connect Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "thingwire": {
      "command": "python3.11",
      "args": ["-m", "gateway"],
      "cwd": "/absolute/path/to/thingwire/gateway",
      "env": {
        "THINGWIRE_MQTT_BROKER": "localhost"
      }
    }
  }
}
```

Restart Claude Desktop. You should see ThingWire tools available.

## Step 6: Talk to your hardware

Try these prompts:

1. **"What ThingWire devices are connected?"** → calls `list_devices`
2. **"What's the current temperature?"** → calls `read_temperature`
3. **"Check humidity and motion too"** → calls `read_humidity`, `read_motion`
4. **"It's too hot, turn on the fan"** → calls `do_set_relay` (with safety confirmation)
5. **"Show me the audit log"** → calls `get_audit_log`

## Troubleshooting

### "No devices discovered"
- Is the virtual device or ESP32 running?
- Is the MQTT broker reachable? Try `mosquitto_sub -t "thingwire/#"` to verify messages

### "Could not connect to MQTT broker"
- Is Docker running? `docker-compose up mosquitto -d`
- Check port 1883 isn't blocked

### Tools not showing in Claude Desktop
- Restart Claude Desktop after editing config
- Check the `cwd` path is absolute and correct
- Check gateway logs in stderr for errors
