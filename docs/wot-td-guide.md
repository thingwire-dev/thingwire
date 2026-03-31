# WoT Thing Description Guide

ThingWire uses the [W3C Web of Things Thing Description](https://www.w3.org/TR/wot-thing-description11/) standard to describe device capabilities. This guide explains how to write a TD for your own devices.

## What is a Thing Description?

A JSON document that tells the gateway:
- What sensors the device has (properties)
- What the device can do (actions)
- How to communicate with it (forms)

## Minimal example

```json
{
  "@context": "https://www.w3.org/2019/wot/td/v1.1",
  "@type": "Thing",
  "id": "urn:thingwire:device:my-device",
  "title": "My Custom Device",
  "description": "A device with one sensor and one actuator",
  "securityDefinitions": { "nosec_sc": { "scheme": "nosec" } },
  "security": ["nosec_sc"],
  "properties": {
    "temperature": {
      "type": "number",
      "unit": "celsius",
      "readOnly": true,
      "description": "Temperature sensor reading",
      "forms": [{ "href": "mqtt://broker/thingwire/my-device/telemetry", "op": "observeproperty" }]
    }
  },
  "actions": {
    "setLED": {
      "title": "Control LED",
      "description": "Turn the status LED on or off",
      "input": {
        "type": "object",
        "properties": {
          "state": { "type": "boolean", "description": "true = on, false = off" }
        },
        "required": ["state"]
      },
      "safe": true,
      "idempotent": true,
      "forms": [{ "href": "mqtt://broker/thingwire/my-device/command", "op": "invokeaction" }]
    }
  }
}
```

## How ThingWire uses it

The **tool compiler** reads your TD and generates MCP tools:

| TD Element | Generated Tool | Example |
|-----------|---------------|---------|
| Property `temperature` | `read_temperature` | Returns `{"value": 23.5, "unit": "celsius"}` |
| Action `setLED` | `do_set_led` | Takes `state: bool`, sends MQTT command |

## Properties (sensors)

Each property becomes a `read_*` tool. Include:

- `type` — `"number"`, `"boolean"`, `"string"`
- `unit` — helps the AI understand the reading (e.g., `"celsius"`, `"percent"`)
- `readOnly` — should be `true` for sensors
- `description` — clear, concise description (the AI reads this)

## Actions (actuators)

Each action becomes a `do_*` tool. Important fields:

- `safe` — set to `false` for actions that affect the physical world (motors, relays, locks). This triggers the safety layer's confirmation requirement.
- `idempotent` — set to `true` if calling the action twice has the same effect as once
- `input.properties` — define the parameters the AI can pass

## Publishing the TD

Your device (or the virtual simulator) publishes the TD as a retained MQTT message:

```
Topic: thingwire/{device_id}/td
Payload: <JSON TD>
Retained: true
```

The gateway subscribes to `thingwire/+/td` and automatically discovers new devices.

## Tips

- Keep descriptions short and clear — the AI uses them to understand what tools do
- Mark any action that touches the physical world as `safe: false`
- Use standard units (celsius, percent, boolean) for consistency
- The `id` field should be a URN: `urn:thingwire:device:{your-device-id}`
