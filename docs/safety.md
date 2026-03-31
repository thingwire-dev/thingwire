# Safety Model

ThingWire takes safety seriously. When an AI agent can control physical hardware, every command must be checked, logged, and auditable.

## Safety checks (every actuator command)

### 1. Permission check

Every device has an allowlist of permitted actions. Commands not on the list are rejected.

```yaml
# safety_config.yaml
devices:
  thingwire-demo-001:
    allowed_actions:
      - read_temperature
      - read_humidity
      - read_motion
      - do_setRelay
```

Devices discovered via MQTT are auto-registered with allowlists derived from their WoT Thing Description.

### 2. Rate limiting

Sliding window rate limiter per action per device. Default: 10 calls per 60 seconds.

```yaml
global:
  default_rate_limit:
    max_calls: 10
    window_seconds: 60
```

### 3. Dangerous action detection

Actions marked `"safe": false` in the WoT Thing Description are flagged as dangerous. The safety layer logs a confirmation warning. In v2, this will integrate with MCP's user confirmation flow in Claude Desktop.

### 4. Deadman switch

If a device stops sending heartbeats for longer than the timeout (default: 300 seconds), all actuator commands to that device are blocked. This prevents an agent from sending commands to a device that may be in an unknown state.

### 5. Audit log

Every tool call is recorded to SQLite:

| Field | Description |
|-------|-------------|
| `timestamp` | When the command was executed |
| `device_id` | Target device |
| `action` | Tool name (e.g., `do_set_relay`) |
| `params_json` | Input parameters |
| `result_json` | Command result |
| `confirmed` | Whether confirmation was required |
| `source` | Where the call originated (e.g., `mcp`) |

Query the audit log via the `get_audit_log` MCP tool or directly via SQLite.

## Configuration

Edit `gateway/safety_config.yaml` to customize rules. The gateway auto-generates rules for newly discovered devices, but explicit config takes precedence.

## Future improvements (v2)

- MCP user confirmation dialog for dangerous actions
- Per-user/per-agent permission scoping
- Time-based access rules (e.g., no relay control after midnight)
- Value range validation from WoT TD input schemas
