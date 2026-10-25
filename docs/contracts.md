# ThingWire contracts

Single source of truth for interfaces shared between workstreams. Extracted from the code at
the head of `release/robot-stack-v0` on 2026-10-18. If code and this file disagree, open a
`docs/contracts-<slug>` PR to settle it before writing more code (see `AGENTS.md`, section 4).

Status marks: no mark means implemented and tested. **PLANNED** means not in the code yet; do
not depend on it until the mark is removed.

## 1. MQTT topics

Prefix is `device_topic_prefix` (default `thingwire`, `src/thingwire/config.py`). `{id}` is the
device id.

| Topic | Direction | Retained | Payload | Status |
| --- | --- | --- | --- | --- |
| `thingwire/{id}/td` | device to gateway | yes | Thing Description JSON | implemented |
| `thingwire/{id}/telemetry` | device to gateway | no | see 1.1 | implemented |
| `thingwire/{id}/status` | device to gateway | yes | `online` / `offline` (LWT), or an ack JSON (1.3) | implemented |
| `thingwire/{id}/command` | gateway to device | no | command JSON (1.2) | implemented |
| `thingwire/{id}/event` | device to gateway | no | event JSON (1.4) | implemented on the virtual bot; the gateway bridge does not subscribe to it yet |
| `thingwire/{id}/scene` | virtual bot to viewer | no | `{timestamp, people[], visible_text[]}` | implemented (virtual bot only, demo input) |
| `thingwire/{id}/safety_status` | gateway to device and viewer | yes | safety state JSON (1.5) | implemented (`safety_bridge.py`) |
| `thingwire/safety_status` | gateway to viewer | yes | same, global scope | implemented |
| `thingwire/{id}/estop` | both ways | no | any non-retained message trips the stop; `{"reset": true}` clears it | implemented |
| `thingwire/estop` | to gateway | no | global trip, same rules | implemented |

The gateway routes on `topic.split("/")` as `[prefix, device_id, subtopic]` and handles
`td`, `telemetry`, `status`, `estop` (`mqtt_bridge.py`). Unknown subtopics are ignored. A retained
estop message is ignored on purpose, so a stale trip cannot undo an authorized reset. After an
authorized reset the gateway publishes `{"reset": true}` (not retained) to `{id}/estop`.

Broker ACL profile (`docker/mosquitto-acl.conf`, `docs/security-broker.md`): a device may publish
only `td|telemetry|status|event|safety_status` under its own id and subscribe to `command` and
`estop`; user `thingwire-gateway` publishes `command` and `estop`. The gateway logs in with
`THINGWIRE_MQTT_USERNAME` / `THINGWIRE_MQTT_PASSWORD` (empty username means anonymous).

### 1.1 Telemetry payload

```json
{"timestamp": "<ISO 8601>", "readings": {"<property>": {"value": <any>, "unit": "<str>"}}}
```

Desk bot readings: `head_pan`, `head_tilt` (degrees), `expression` (unit `enum`),
`person_detected` (unit `boolean`). Telemetry rate is 10 Hz on the virtual bot. A telemetry
message with a timestamp also counts as a heartbeat for the deadman switch.

### 1.2 Command payload (gateway to device)

```json
{"action_id": "<uuid4>", "target": "<action name>", "command": "<action name>", "value": {}}
```

`value` is the action input object after the safety layer validated it (`Decision.params`,
clamped when `out_of_range: clamp`), for example `{"pan": 10, "tilt": 0}`. Inputs the caller
omitted are left out. Devices must accept the action name from `command`, falling back to
`action` (see `virtual_desk_bot.py::_action_name`). `target` and `command` are the TD action
name unless the action's `invokeaction` form declares `thingwire:target` and/or
`thingwire:command`, which then replace them. The gateway never derives them from the name.

### 1.3 Ack payload (device to gateway, on `status`)

`{"action_id": "<id>", "status": "ok" | "error", "action": "<name>", "error": "<message>"}`
(`error` only when `status` is `error`).

### 1.4 Event payload

`{"event": "<name>", "confidence": <number>}` for `person_seen`. `wake_word` carries
`{phrase, confidence}` (stretch goal, not emitted yet).

### 1.5 safety_status payload

`{"state": "ok" | "degraded" | "estop", "drop_reason": <str|null>, "last_decision_code": "<code>",
"trace_id": "<id>", "timestamp": "<ISO 8601 Z>"}`, retained, published only when
`(state, drop_reason)` changes (`safety_status.py`). A device in `estop` refuses commands with the
ack `{"status": "error", "error": "ESTOP_LATCHED"}` and parks at pan 0, tilt 0.

## 2. Desk bot Thing Description

File: `td-library/desk-bot.td.json`, id `urn:thingwire:device:{device_id}`. Firmware generates the
same document from `firmware/device-deskbot.yaml`; `tests/test_deskbot_td_match.py` diffs them.
Forms use `mqtt://{broker}/thingwire/{device_id}/<telemetry|command|event>`.

| Kind | Name | Shape | Limits | `safe` | Notes |
| --- | --- | --- | --- | --- | --- |
| property | `head_pan` | number, degrees, read only | -90 to 90, `maxSlewDegPerSec` 90 | n/a | |
| property | `head_tilt` | number, degrees, read only | -30 to 30, `maxSlewDegPerSec` 90 | n/a | |
| property | `expression` | string enum, read only | neutral, happy, curious, sleepy, surprised, alert | n/a | |
| property | `person_detected` | boolean, read only | none | n/a | PIR now, vision later |
| action | `look_at` | `{pan, tilt}` both required | pan -90..90, tilt -30..30 | true | idempotent |
| action | `set_expression` | `{expression}` required | enum above | true | idempotent |
| action | `nod` | `{times}` required | 1..3 | true | not idempotent |
| action | `sweep` | `{speed_deg_s}` required | 10..120 | **false** | needs approval; continues until the next motion command |
| event | `wake_word` | `{phrase, confidence}` | none | n/a | stretch, not emitted |
| event | `person_seen` | `{confidence}` | none | n/a | |

### 2.1 TD extension `maxSlewDegPerSec`

A number on a property (per axis). Maximum angular speed in degrees per second. Default 90
when absent. Read by `extract_limits` in `safety_motion.py`, which pairs each action input axis
(`pan`, `tilt`) with the property of the matching name (`head_pan`, `head_tilt`) for slew
checks. The device must enforce the same limit on its own (firmware `servo_head`).

### 2.2 Gateway tool names

Each property becomes `read_<slug>` and each action `do_<slug>`; with a device prefix the name is
`<prefix>_read_<slug>` / `<prefix>_do_<slug>` (`tool_compiler.py`). Example: `do_look_at`,
`do_sweep`. The slug lowercases and replaces non-alphanumerics with `_`. Tools are keyed by the
MQTT device id. The safety layer and MCAP name an action `do_<TD action name>` (unslugged); the
audit log `action` column holds the tool name.

Read results carry provenance: `origin` (`device_telemetry`, or `untrusted_perception` for
properties in `taint.perception_properties` and implausible scans) and `taint_id`. Properties
under `taint.scans` also carry `plausible` and, when false, `plausibility_findings`.

Meta tools: `list_devices`, `get_device_status(device_id)`, `get_audit_log(device_id, limit)`
(scope `audit:read`), and `emergency_stop(device_id, reason)` (scope `device:<id>:read`), which
latches the e-stop for a known device. No MCP tool resets an e-stop; only
`thingwire estop reset` on the gateway host does (section 7).

## 3. Scopes (issued by theAuth)

| Scope | Meaning | Status |
| --- | --- | --- |
| `taint:override` | lets a tainted-context action proceed without interactive approval | checked by the gateway (`taint.extra_scope`) |
| `device:<id>:read` | read properties and events, list the device, trip its e-stop | checked by the gateway (http transport) |
| `device:<id>:act:<action>` | call one action | checked by the gateway (http transport) |
| `device:<id>:act:dangerous` | alternative to `act:<action>` for `safe: false` actions; human approval still required | checked by the gateway (http transport) |
| `audit:read` | `get_audit_log` | checked by the gateway (http transport) |

The gateway verifies scopes only on the identity-enabled http transport. In stdio dev mode
there is no identity: safe actions run, and any call that needs a human approval (dangerous or
tainted) is denied with `APPROVAL_REQUIRED`.

## 4. Audit log (SQLite, `data/audit.db`, table `commands`)

Created in `audit_log.py`. The last five columns are added by migration if missing.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | |
| `timestamp` | TEXT NOT NULL | ISO 8601 |
| `device_id` | TEXT NOT NULL | |
| `action` | TEXT NOT NULL | tool name, e.g. `do_look_at` |
| `params_json` | TEXT | JSON |
| `result_json` | TEXT | JSON |
| `confirmed` | INTEGER NOT NULL DEFAULT 0 | 1 if a human approved a dangerous action |
| `source` | TEXT NOT NULL DEFAULT `mcp` | |
| `agent_id` | TEXT | theAuth agent identity; null until W3 |
| `delegation_chain` | TEXT | JSON list of agent ids |
| `scopes` | TEXT | JSON list |
| `decision` | TEXT | `allow` or `deny` (`Decision.decision`) |
| `reason` | TEXT | a code from section 5 |

Index: `idx_commands_device_ts`. Safety transitions are also logged (`record_transition`) as rows
with `source` `safety`, `action` `safety:<event>`, `decision` `transition`,
`reason` `<state>:<drop_reason or last_decision_code>`, `agent_id` = actor.
**PLANNED**: a provenance column; `Provenance` (`provenance.py`: `origin` of `trusted_operator`,
`device_telemetry`, `untrusted_perception`; `source_device`; `timestamp`; `content_hash`;
`taint_id`) exists but is not yet written to the audit log or MCAP.

## 5. Decision and reason codes

`Decision` (`safety_motion.py`, frozen dataclass): `allowed: bool`, `code: str`, `message: str`,
`details: dict`, `params: dict` (possibly clamped input). `decision` property is `allow`/`deny`.

| Code | Allowed | Raised by | Meaning |
| --- | --- | --- | --- |
| `OK` | yes | `MotionGuard` | within limits |
| `RANGE_CLAMPED` | yes | `MotionGuard` | out-of-range input clamped (only when `out_of_range: clamp`) |
| `INVALID_INPUT` | no | `MotionGuard` | axis value is not a finite number |
| `OUT_OF_RANGE` | no | `MotionGuard` | outside TD `minimum`/`maximum` (default mode `reject`) |
| `SLEW_EXCEEDED` | no | `MotionGuard` | faster than `maxSlewDegPerSec` from last known state |
| `DEVICE_NOT_REGISTERED` | no | `SafetyLayer` | unknown device |
| `ACTION_NOT_ALLOWED` | no | `SafetyLayer` | not in the device allowlist |
| `RATE_LIMIT_EXCEEDED` | no | `SafetyLayer` | per device and action sliding window |
| `DEADMAN_SWITCH` | no | `SafetyLayer` | no heartbeat within `deadman_switch_timeout_seconds` |
| `APPROVAL_REQUIRED` | no | `SafetyLayer` | `safe: false` action without approval |
| `SAFETY_CHECK_ERROR` | no | `SafetyLayer` | any unexpected exception (fail closed) |
| `ESTOP_LATCHED` | no | `safety_estop.py` | e-stop latched for the device or globally |
| `DENIAL_FLOOD` | no | `safety_flood.py` | repeated denials throttled per (device, action) |
| `RESET_NOT_AUTHORIZED` | no | `safety_estop.py` | caller not in `global.estop.authorized_resetters` |
| `ESTOP_STATE_UNAVAILABLE` | no | `safety_estop.py` | latch state unreadable, fail closed |
| `MISSING_SCOPE` | no | gateway (identity) | token lacks the scope; audit `reason` is `missing scope` |
| `IDENTITY_DENIED` | no | gateway (identity) | unauthenticated, invalid or reserved name |
| `APPROVAL_DENIED` | no | gateway (theAuth) | approval refused, expired, timed out or unavailable |
| `DEVICE_OFFLINE` | no | gateway | device went offline while the approval was pending |

The enum is `DecisionCode` in `src/thingwire/safety_codes.py`. Taint policy codes
(`taint_policy.py`, strings, not in the enum yet): `TAINTED_CONTEXT_REQUIRES_APPROVAL`,
`TAINTED_CONTEXT_DENIED`, `TAINT_OK`, `TAINT_READ_ALLOWED`, `TAINT_OVERRIDDEN`,
`TAINT_MALFORMED_INPUT`. Scan plausibility codes (`plausibility.py`): `SCAN_MALFORMED`,
`SCAN_NON_FINITE`, `SCAN_OUT_OF_RANGE`, `SCAN_ALL_BLOCKED`, `SCAN_ALL_IDENTICAL`,
`SCAN_IMPLAUSIBLE_JUMP`, `SCAN_BAD_TIMESTAMP`, `SCAN_LENGTH_CHANGED`.

New codes are UPPER_SNAKE_CASE strings. Adding one is a contract change. Config keys
(`safety_config.yaml`): `global.require_confirmation_for_dangerous`,
`global.deadman_switch_timeout_seconds` (default 300), `global.default_rate_limit`
(`max_calls` 10, `window_seconds` 60), `global.out_of_range` (`reject` or `clamp`), `global.estop.authorized_resetters` (default
`[operator]`), `global.denial_flood` (`threshold` 20, `window_seconds` 60, `counted_codes`), and the
top-level `taint:` section (`enabled`, `extra_scope` `taint:override`, `untrusted_origins`,
`default_mode`, `read_actions`, `actions`, and for the gateway taint tracker
`perception_properties`, `context_window_seconds` (300), `max_context_entries` (256), `scans`
(property name to `ScanSpec` fields)). The gateway evaluates `TaintPolicy` on every action call:
the context is the provenance of values the same agent read within the window whose numbers
reappear in the call's numeric arguments. `require_approval` asks theAuth; `approved` comes only
from a theAuth approval, never from tool arguments.

## 6. MCAP recording (`recorder.py`)

Each channel is registered with topic = channel name, message encoding `json`, schema name
`thingwire.<channel>`, schema encoding `jsonschema`. Timestamps come from an injectable
`clock_ns`. Every record is a JSON object.

| Channel | Fields |
| --- | --- |
| `tool_call` | `seq` number, `device_id`, `action`, `params` object, `approved` bool, `agent_id`, `delegation_chain[]`, `scopes[]`, optional `stage` |
| `decision` | `call_seq` number (matches `tool_call.seq`), `device_id`, `action`, `allowed` bool, `code`, `message`, `agent_id`, `delegation_chain[]`, `scopes[]` |
| `telemetry` | `device_id`, `readings` object |
| `td` | `device_id`, `td` object |
| `heartbeat` | `device_id` |

Replay (`thingwire replay <file.mcap>`) re-runs `tool_call` records through the safety layer and
compares each result to the recorded `decision` by `call_seq`; it must be deterministic.
Identity fields are optional and only present when known.

The gateway records every action call and its decision, allowed or denied. A call decided
before the safety layer carries `stage`: `identity`, `taint`, `approval` or `precheck` (a
safety check run before asking a human); replay skips those. Every telemetry message is
recorded on `telemetry`, and each one with a `timestamp` is also the deadman heartbeat, recorded
on `heartbeat`, so replay sees the same heartbeats as the live gateway. `td` is recorded when a
device is registered. Recording is on when `THINGWIRE_RECORD_PATH` (or `serve --record`) is set.

## 7. E-stop state and human reset

The latch persists in SQLite at `THINGWIRE_SAFETY_STATE_DB_PATH` (default
`data/safety_state.db`, mode 0600), table `estop_latch`. Agents trip through MQTT or the
`emergency_stop` tool; reset is human only: `thingwire estop reset` inserts a row into
`estop_reset_requests` (`id`, `scope` device id or `*`, `who`, `reason`, `requested_at` epoch
seconds, `outcome`, `handled_at`). The gateway's safety sweep (every
`THINGWIRE_SAFETY_SWEEP_SECONDS`) also trips the deadman, and applies each pending request:
`outcome` is `reset`, `not_latched`, `expired` (older than `THINGWIRE_ESTOP_RESET_TTL_SECONDS`)
or `refused:<code>`. `THINGWIRE_ESTOP_RESETTERS` (comma separated) overrides
`global.estop.authorized_resetters`.
