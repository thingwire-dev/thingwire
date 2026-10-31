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

Sliding window rate limiter per action per device, plus one window per device across all
actions so rotating actions cannot multiply the budget. Default: 10 calls per 60 seconds for
both; set `global.device_rate_limit` (or `devices.<id>.device_rate_limit`) to change the device
wide window.

```yaml
global:
  default_rate_limit:
    max_calls: 10
    window_seconds: 60
```

### 2b. Command freshness

An action call may carry `issued_at` (Unix seconds, UTC) and a unique `nonce`. A stamp older
than `global.command_freshness.max_age_seconds`, dated too far ahead, malformed, or with a reused
nonce is denied with `STALE_COMMAND` or `REPLAYED_COMMAND` before taint and approval. Set
`required: true` to deny unstamped calls. The gateway also stamps each command it publishes with
`issued_at`; devices should refuse old ones. See `docs/contracts.md` 5.3.

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

## Gateway order

Every action tool call takes one path (`src/thingwire/gateway_actions.py`), and every step
fails closed:

1. Identity scope (http transport only).
2. Taint policy: numbers the same agent read from untrusted sensors reappearing in the call.
3. Human approval through theAuth for dangerous (`safe: false`) or tainted calls. Without
   identity (stdio) such calls are denied; a tool argument can never approve anything.
4. `SafetyLayer.evaluate`: e-stop latch, denial flood, allowlist, deadman, approval, range and
   slew, rate limit.
5. The validated `Decision.params` are sent to the TD action.

Each allow and deny is written to the audit log and, with `--record`, to MCAP.

## Emergency stop

Any non-retained message on `thingwire/<id>/estop` or `thingwire/estop`, the `emergency_stop`
MCP tool, or a deadman timeout latches the stop. The latch survives restarts. Only a human on
the gateway host can clear it: `thingwire estop reset --device <id> --who <name> --reason ...`
(see `docs/cli-reference.md`).

## Configuration

Edit `gateway/safety_config.yaml` to customize rules. The gateway auto-generates rules for newly discovered devices, but explicit config takes precedence.

## Future improvements (v2)

- MCP user confirmation dialog for dangerous actions
- Per-user/per-agent permission scoping
- Time-based access rules (e.g., no relay control after midnight)
- Value range validation from WoT TD input schemas
