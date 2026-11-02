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
{"action_id": "<uuid4>", "target": "<action name>", "command": "<action name>", "value": {}, "issued_at": 1761900000.0}
```

`value` is the action input object after the safety layer validated it (`Decision.params`,
clamped when `out_of_range: clamp`), for example `{"pan": 10, "tilt": 0}`. Inputs the caller
omitted are left out. Devices must accept the action name from `command`, falling back to
`action` (see `virtual_desk_bot.py::_action_name`). **PLANNED**: `issued_at` is the gateway wall clock
when it published the command, seconds since the Unix epoch (UTC, number); `action_id` doubles
as the nonce. Devices SHOULD refuse a command whose `issued_at` is older than their own validity
window or whose `action_id` they saw recently (see 5.3). Device side enforcement is PLANNED
separately; until it lands a replayed MQTT command is only stopped by the broker ACL.
`target` and `command` are the TD action
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

### 1.6 Desk bot `show_text` and `blink` messages (PLANNED)

Same topics, same envelope and same ack as 1.2 and 1.3; nothing new on the wire.

Command (`thingwire/{id}/command`, gateway to device), `target` and `command` equal the action name:

```json
{"action_id": "<uuid4>", "target": "show_text", "command": "show_text", "value": {"text": "hello", "duration_ms": 3000}, "issued_at": 1761900000.0}
{"action_id": "<uuid4>", "target": "blink", "command": "blink", "value": {"times": 2, "speed": "slow"}, "issued_at": 1761900000.0}
```

`value` is the validated input object (`Decision.params`). Omitted optional inputs are left out;
the device then uses its own defaults (2.3). The gateway never truncates, pads or rewrites
`text`; it forwards the exact string it validated or denies the call.

Ack (`thingwire/{id}/status`), exactly 1.3. Success: `{"action_id": "<id>", "status": "ok",
"action": "show_text"}`. Failure: `{"action_id": "<id>", "status": "error", "action": "show_text",
"error": "<message>"}`. The `ok` ack is sent when the device accepted and started the action, not
when it finished (same as `nod`). The ack never echoes `text`. Device `error` messages for these
actions are short fixed strings: `text too long`, `text charset`, `text blank`, `duration out of
range`, `times out of range`, `unknown speed`, `display busy`, `ESTOP_LATCHED`. A device in
`estop` answers with the 1.5 ack and changes nothing.

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
| action | `show_text` | `{text}` required, `{duration_ms}` optional | **PLANNED** (2.3) | true | idempotent; untrusted text |
| action | `blink` | `{times}` required, `{speed}` optional | **PLANNED** (2.3) | true | not idempotent |
| event | `wake_word` | `{phrase, confidence}` | none | n/a | stretch, not emitted |
| event | `person_seen` | `{confidence}` | none | n/a | |

### 2.1 TD extension `maxSlewDegPerSec`

A number on a property or on an action input axis. Maximum angular speed in degrees per
second. The device must enforce the same limit on its own (firmware `servo_head`).

**PLANNED** (code today reads only the action input axis; the desk bot declares it on the
properties, so `look_at` has no slew limit until this lands). Semantics:

1. Driven properties. An action input axis `x` drives a numeric property `p` of the same TD when
   `p == x` (exact), or, if no property is named exactly `x`, when `p` ends with `_x` (so axis
   `pan` drives `head_pan`, axis `tilt` drives `head_tilt`). Several matches are all driven.
   No match means the axis drives nothing and only its own declaration counts.
2. Effective slew for an axis is the smallest `maxSlewDegPerSec` declared on the axis itself and
   on every driven property. The default of 90 deg/s applies only when nothing is declared and
   the axis `unit` is an angle unit with a range, as before.
3. Last known state is tracked per (device, driven property), so two actions that drive the same
   property share one history; an axis that drives nothing is tracked per (device, action, axis).
4. The first command after gateway start has no previous state and is not slew checked. The
   gateway does not read the device's real position (residual risk, section 8).
5. A breach is `SLEW_EXCEEDED`. Limits come from the TD merged with pins (section 5.4).

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

### 2.3 Desk bot `show_text` and `blink` (PLANNED)

Both actions use the standard `invokeaction` form on `.../command` and `"safe": true`. Neither
drives a property and neither adds a TD property or event; the `expression` property keeps its
meaning (the face underneath). Limits come from one device config source, never a literal in two
places: `firmware/device-deskbot.yaml` gains `display.text_max_chars`, `display.text_default_ms`,
`display.text_max_ms` and `blink.max_times`. The generator writes them into both the TD and
`deskbot_gen.h` (as `nod` does today), and the generator fails if
`display.text_max_chars` exceeds `floor(OLED_WIDTH / glyph_width)`. Display geometry
(`docs/desk-bot-wiring.md`, `firmware/src/config.h`): SSD1306 128 x 64, I2C 0x3C, with the
built in 6 x 8 pixel font at size 1, which gives 21 columns on one line. Text is drawn at size 1,
one line, no wrapping, no scrolling. The default for `display.text_max_chars` is therefore 21.

`show_text`:

```json
{"title": "Show text", "safe": true, "idempotent": true,
 "input": {"type": "object", "additionalProperties": false, "required": ["text"], "properties": {
   "text": {"type": "string", "minLength": 1, "maxLength": "<display.text_max_chars>",
            "pattern": "^[ -~]+$"},
   "duration_ms": {"type": "integer", "minimum": 1, "maximum": "<display.text_max_ms>"}}}}
```

(The TD carries the config values as numbers; `<...>` marks where they land.)

- `text`: printable ASCII only, U+0020 to U+007E. Control characters (including `\n`, `\r`, `\t`
  and NUL), DEL, and every non ASCII character are disallowed. There is no newline because the
  display is one line. A string with no character other than space is blank and denied.
- `duration_ms`: how long the text stays. Omitted means `display.text_default_ms`. There is no
  "until further notice" value, so every text ends.
- When the time ends, or when the next `show_text`, `set_expression`, e-stop trip or deadman park
  arrives, the text is cleared and the device shows the current expression face again. A new
  `show_text` replaces the visible text and restarts the timer. `set_expression` during a text
  changes the stored expression and clears the text; it does not queue behind it.
- Text is rendered as opaque pixels. The device never parses, formats, logs as a format string,
  or acts on it (for example no "command in text" feature, and `%s` style fixed formats only).

`blink`:

```json
{"title": "Blink", "safe": true, "idempotent": false,
 "input": {"type": "object", "additionalProperties": false, "required": ["times"], "properties": {
   "times": {"type": "integer", "minimum": 1, "maximum": "<blink.max_times>"},
   "speed": {"type": "string", "enum": ["normal", "slow"]}}}}
```

- `speed` omitted means `normal`. `slow` doubles the lid close and open times, the same factor
  the firmware already applies to the `sleepy` face (`slowBlink`, `OLED_BLINK_CLOSE_MS`,
  `OLED_BLINK_OPEN_MS`). No other values exist.
- A blink is `times` full close-and-open cycles of the current face. Afterwards the lids return to
  the stored expression's frame and the random idle blink schedule resumes. The `expression`
  telemetry reading does not change.
- While a `show_text` is visible, `blink` is refused with the ack error `display busy` (the eyes
  are not drawn). A `show_text` that arrives during a blink cancels the blink.
- Neither action cancels or delays head motion (`nod`, `sweep`), and neither refreshes the
  firmware deadman command timer (`deadman_timeout_ms`), so display traffic cannot keep a head
  unparked.

Scopes: `device:<id>:act:show_text` and `device:<id>:act:blink` (section 3 convention); tools
`do_show_text` and `do_blink`; the safety layer and MCAP name them `do_show_text`, `do_blink`.

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
| `INVALID_INPUT` | no | `MotionGuard` | axis value is not a finite number; **PLANNED**: or not a member of the TD `enum` (5.1); **PLANNED**: or a `show_text` / `blink` input that breaks the rules of 5.6 |
| `OUT_OF_RANGE` | no | `MotionGuard` | outside TD `minimum`/`maximum` (default mode `reject`) |
| `SLEW_EXCEEDED` | no | `MotionGuard` | faster than `maxSlewDegPerSec` from last known state |
| `DEVICE_NOT_REGISTERED` | no | `SafetyLayer` | unknown device |
| `ACTION_NOT_ALLOWED` | no | `SafetyLayer` | not in the device allowlist |
| `RATE_LIMIT_EXCEEDED` | no | `SafetyLayer` | per action sliding window; **PLANNED**: also a per device window across all actions (5.2) |
| `DEADMAN_SWITCH` | no | `SafetyLayer` | no heartbeat within `deadman_switch_timeout_seconds` |
| `APPROVAL_REQUIRED` | no | `SafetyLayer` | `safe: false` action without approval |
| `SAFETY_CHECK_ERROR` | no | `SafetyLayer` | any unexpected exception (fail closed) |
| `STALE_COMMAND` | no | `SafetyLayer` | **PLANNED** (5.3) `issued_at` missing when required, malformed, too old or too far in the future |
| `REPLAYED_COMMAND` | no | `SafetyLayer` | **PLANNED** (5.3) the `nonce` was already used inside its window |
| `TD_NOT_PINNED` | no | `SafetyLayer` | **PLANNED** (5.4) actuation on a device with no pin while `td_trust.unpinned` resolves to deny |
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

### 5.1 Enum constraints (PLANNED)

A TD input property with `enum` is enforced by `MotionGuard` for any JSON type, not only
numbers. The value must equal one member, compared by JSON type (`true` is not `1`, `"1"` is
not `1`). Anything else is `INVALID_INPUT` with details `{axis, reason: "not_in_enum",
allowed: [...]}`. `out_of_range: clamp` never applies to an enum. An omitted optional input is
not checked. The effective enum is the intersection of the TD enum and the pinned enum (5.4).

### 5.2 Rate limit scope (PLANNED)

Two sliding windows must both have room; a call is recorded in both or in neither.

- Per action, as today: `devices.<id>.rate_limits.<action>`, else `global.default_rate_limit`.
- Per device across every action: `devices.<id>.device_rate_limit`, else
  `global.device_rate_limit`, else the value of `global.default_rate_limit`. Both use
  `{max_calls, window_seconds}`. Rotating actions therefore cannot multiply the budget.

A denial is `RATE_LIMIT_EXCEEDED` with details `{scope: "action" | "device", max_calls,
window_seconds}`. No threshold is hardcoded beyond the existing config defaults. Reads are not
rate limited here (unchanged).

### 5.3 Command freshness (PLANNED)

An action call may carry a stamp: `issued_at` (number, seconds since the Unix epoch, UTC) and
`nonce` (string of 8 to 128 characters from `[A-Za-z0-9_-]`). On the MCP tools they are optional
extra parameters named `issued_at` and `nonce`; they never reach the device `value`. The check
runs in the gateway after the identity check and before taint and approval, so a slow human
approval does not age the command. Config, `global.command_freshness`:

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | true | false skips the check entirely |
| `required` | false | true denies a call with no stamp |
| `max_age_seconds` | 30 | oldest accepted `now - issued_at` |
| `max_future_skew_seconds` | 5 | furthest accepted `issued_at - now` |
| `nonce_window_seconds` | 120 | how long a nonce is remembered; raised to at least `max_age_seconds + max_future_skew_seconds` so a nonce outlives its validity |
| `max_nonces` | 4096 | cache bound |

Rules, all fail closed:
1. `now` comes from an injectable epoch clock (`SafetyLayer(epoch_clock=...)`), never read inside
   the decision.
2. A stamp that is present but malformed (non finite, bool, wrong type, bad nonce shape), or
   only half present, is `STALE_COMMAND` with details `{reason: "malformed"}`. Missing stamp
   with `required: true` is `STALE_COMMAND` with `{reason: "missing"}`. Outside the window is
   `STALE_COMMAND` with `{reason: "too_old" | "from_future", age_seconds}`.
3. A nonce seen for the same device inside `nonce_window_seconds` is `REPLAYED_COMMAND`. A nonce
   is stored only when the stamp is otherwise valid. Expired nonces are pruned on each check.
4. If the cache holds `max_nonces` live entries the call is denied `SAFETY_CHECK_ERROR` with
   `{reason: "nonce_cache_full"}`. Live entries are never evicted, because evicting would let
   a replay through.
5. Both codes count towards denial flood only if listed in `denial_flood.counted_codes`.
6. Replay (`thingwire replay`) does not use stamps: the check is a gateway stage recorded with
   `stage: "freshness"` and skipped by replay.

### 5.4 TD trust and pinned policy (PLANNED)

Limits and the approval flag in a TD are claims by the device, which can be rogue or
poisoned. The operator pins what the gateway enforces, in `safety_config.yaml`:

```yaml
td_trust:
  unpinned: auto            # auto | deny | trust
  devices:
    desk-bot:
      actions:
        look_at:
          requires_approval: false
          inputs:
            pan: {minimum: -90, maximum: 90, max_slew_deg_per_sec: 90}
            tilt: {minimum: -30, maximum: 30, max_slew_deg_per_sec: 90}
        set_expression:
          inputs: {expression: {enum: [neutral, happy, curious, sleepy, surprised, alert]}}
        sweep:
          requires_approval: true
          inputs: {speed_deg_s: {minimum: 10, maximum: 120}}
```

Merge, at device registration, per action input axis:
- `minimum` is the larger of the two, `maximum` the smaller, `max_slew_deg_per_sec` the smaller,
  `enum` the intersection. A bound present on only one side is used as is. The stricter value
  always wins; the gateway never takes a looser value than the pin.
- Approval is required when the pin says `requires_approval: true` OR the TD says `safe: false`.
  A pin can add an approval requirement, a TD can never remove one.
- On a pinned device only pinned actions are allowed; a TD action with no pin is
  `ACTION_NOT_ALLOWED`. Reads are unaffected.
- Unknown pinned actions or axes (in the pin, absent from the TD) are ignored for the action
  list but their pinned limits still apply if the axis appears.

Flags. Each of the following is logged at WARNING and emitted as an audit event (`source`
`safety`, `action` `safety:<event>`, `decision` `flag`, `reason` the event name, details name
the action, axis and both values); the pinned value wins either way:
`td_limit_widened` (TD looser than the pin on any bound, slew or enum), `td_approval_lowered`
(pin requires approval, TD says `safe: true`), `td_action_unpinned` (TD publishes an action the
pin does not list).

Unpinned devices. `unpinned: auto` (default) resolves to `deny` as soon as `td_trust.devices` has
at least one entry and to `trust` otherwise, so turning pinning on is fail closed for every
device you did not list. `deny` registers only the read tools' allowlist; any action is
`TD_NOT_PINNED`. `trust` keeps today's behavior (TD values used as published) and logs one
WARNING per device. `td_trust` malformed (wrong types, non numeric bounds) makes the gateway
refuse to start; a missing section equals `unpinned: trust`.

Limit: a pin protects a registered device id. It does not authenticate the device; spoofing an id
is the broker ACL's job (`docs/security-broker.md`). The first TD for an id wins until the gateway
restarts (unchanged); a later differing TD is not applied.

### 5.5 Taint matching (PLANNED)

Extends the gateway ledger in `gateway_taint.py`. Config, under `taint:`:

| Key | Default | Meaning |
| --- | --- | --- |
| `match_tolerance` | 1.0 | a numeric argument is linked to a read value when `abs(arg - value) <= match_tolerance`; 0 means exact |
| `max_leaves_per_reading` | 64 | structured readings are flattened to at most this many scalars |
| `max_string_chars` | 4096 | longer strings are cut before scanning |

What enters the ledger for a read, besides what already does:
- Numbers inside string readings and string leaves of structured readings (regex for signed
  decimal numbers), as numeric entries.
- Numeric leaves of lists and objects, as numeric entries.
- A stable digest (`provenance.content_hash` of the casefolded, stripped string) of each string
  leaf and of the whole reading, as digest entries.

A call is linked to a ledger entry when a numeric argument is within `match_tolerance` of a
numeric entry, or the digest of a string argument (casefolded, stripped) equals a digest entry.
Provenance and expiry are unchanged (`context_window_seconds`, `max_context_entries`). A link to
an entry whose origin is untrusted makes the call a tainted action: it needs a theAuth approval
or the `taint:override` scope (`TAINTED_CONTEXT_REQUIRES_APPROVAL`), as for exact matches today.
Origin is unchanged: only properties in `taint.perception_properties`, implausible scans and
configured untrusted origins are untrusted. A tolerance is a heuristic: arithmetic larger than
the tolerance, or an agent that retypes a value in words, still escapes (section 8).

### 5.6 Text and blink safety (PLANNED)

Neither action is dangerous: `safe: true`, no `requires_approval` in the pin unless the operator
adds one, and no new decision or reason code. The usual order applies (identity, freshness,
taint, then `SafetyLayer`).

1. Fail closed, gateway side. `MotionGuard` validates these inputs before the device sees them.
   Any violation is a deny, the call is never forwarded, and nothing is truncated or clamped
   (`out_of_range: clamp` never applies to `text`, `speed`, or an integer `times`):
   - `INVALID_INPUT`, details `{axis: "text", reason}` with `reason` one of `missing`,
     `not_string`, `too_long` (with `max_length`), `charset` (outside U+0020 to U+007E),
     `control_char`, `blank`; `{axis: "speed", reason: "not_in_enum"}` (5.1); `{axis: "times" |
     "duration_ms", reason: "not_integer"}` for a non integer or bool; `{reason:
     "unknown_property"}` for an input key the TD does not declare. The details never contain the
     rejected text.
   - `OUT_OF_RANGE` for `times` or `duration_ms` outside the effective bounds, as for `nod`.
   - The charset rule is fixed by this contract and enforced regardless of what a TD claims. The
     effective `maxLength` is the smaller of the TD `maxLength` and a pin (5.4) `max_length`;
     the effective numeric bounds follow the existing merge. A TD can narrow, never widen.
2. Fail closed, device side. Firmware re-validates with the compiled in limits
   (`display.text_max_chars`, `display.text_max_ms`, `blink.max_times`) and the same charset, and
   answers `error` (1.6) without drawing anything. It must not rely on the gateway
   (`AGENTS.md`, section 9). A payload that is not valid JSON, has a non string `text`, or lacks
   `value` is an `error` ack.
3. Untrusted text. `text` is data. Text that came from a sensor, scene, OCR, STT, web page, a
   device property read, or model output is tainted: the existing taint policy and the ledger
   (5.5, digest match of a string argument) apply, so a `show_text` linked to untrusted context
   needs a theAuth approval or `taint:override`, per `taint.default_mode` or
   `taint.actions.show_text`. The gateway and device never interpret `text` as an instruction,
   tool call, config or path. Display is not a trust upgrade: showing text does not mark it safe,
   and a read of the visible text elsewhere keeps its own provenance. Text with no ledger link
   (for example freshly composed model output) cannot be detected as tainted; section 8 states
   the residual risk.
4. Rate limits. Both actions are ordinary actions for 5.2 and the current per action window:
   `devices.<id>.rate_limits.show_text`, `devices.<id>.rate_limits.blink`, else
   `global.default_rate_limit`; and, once 5.2 lands, `devices.<id>.device_rate_limit`, else
   `global.device_rate_limit`. Both windows must have room, a call counts in both or neither,
   and a breach is `RATE_LIMIT_EXCEEDED` with the 5.2 details. Defaults come from
   `safety_config.yaml`; this section sets no number. Denied calls count towards
   `global.denial_flood` when the code is in `denial_flood.counted_codes`.
5. E-stop and deadman. Both are refused while the e-stop is latched: gateway
   `ESTOP_LATCHED`, device ack `ESTOP_LATCHED` (1.5). Gateway `DEADMAN_SWITCH` applies as for
   every actuator action. A trip clears visible text and any blink on the device and shows the
   expression face. These paths do not need the broker, identity or the recorder to work on the
   device.
6. Audit. One `commands` row per call, no new column. `action` is `do_show_text` or `do_blink`,
   `params_json` holds the input object, `decision` / `reason` hold the code above (or `OK`).
   `text` is stored as given up to `taint.max_string_chars` characters (5.5); longer input is cut
   in the log only, never in the command. Rows and MCAP records treat `text` as untrusted data:
   consumers must not render it as markup or follow it as instructions.

New codes are UPPER_SNAKE_CASE strings. Adding one is a contract change. PLANNED config keys are
listed with their sections: `global.device_rate_limit` (5.2), `global.command_freshness` (5.3),
`td_trust` (5.4), `taint.match_tolerance` and friends (5.5), and the 5.6 pin key `max_length`. Device config keys in `firmware/device-deskbot.yaml`: `display.text_max_chars`, `display.text_default_ms`, `display.text_max_ms`, `blink.max_times` (2.3). Config keys
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

## 8. Residual risk the gateway cannot remove

Honest limits, so nobody reads the benchmark as a guarantee:

- In range, authorized actions caused by scene or sensor text (benchmark SCN-03, SEN-03). If an
  agent holds the scope and reads text that says "look at pan 60", the resulting `look_at` is a
  legal call with no numeric link to the text and no way for the gateway to know why it was
  made. Taint only covers values that provably flow from a read the operator marked as
  perception (5.5); free text from a device property that is not marked perception is treated
  as device telemetry. Mitigations are outside the gateway: narrow scopes, approval policy on
  perception data, and an agent that does not obey text from sensors.
- Text shown on the display (5.6). A legal `show_text` caused by perception text that the agent
  retyped, or by model output with no ledger link, cannot be told from a deliberate one. The
  harm is bounded to `display.text_max_chars` printable characters for a bounded time, but a human
  reading it may still be misled; the gateway cannot prevent that.
- Taint matching is a heuristic (5.5). A value rewritten beyond the tolerance, or retyped in
  words, is not linked.
- Freshness (5.3) stops an old captured stamp from being reused. It does not stop an attacker who
  can get the caller to stamp a new command, and it cannot authenticate the stamping caller;
  identity does that.
- The first command after start is not slew checked (2.1) and the gateway does not read the
  device's actual position.
- Pins (5.4) protect registered ids only; a device that spoofs an id is a broker ACL problem.
- Device side enforcement (firmware range, slew and freshness checks) is separate work and the
  gateway must not be the only line (`AGENTS.md`, section 9).

## 9. Desk bot text and blink: test pins and scope (PLANNED)

Cross-component checks that must stay green: `tests/test_deskbot_td_match.py` (its expected
action set grows to include `show_text` and `blink` in the implementing PR, and the generated
`deskbot_gen.h` and the library TD stay identical), the TD tool compiler tests, replay
determinism, the safety and e-stop tests, and the dash check.

New cases the implementing PRs must add:
- Length boundary: `text` of exactly `maxLength` allowed, one more `INVALID_INPUT` /
  `too_long`; empty and all space denied (`missing` or `blank`). Same boundary in firmware.
- Charset: U+0020 and U+007E allowed, U+001F, U+007F, `\n`, NUL, U+00E9 and an emoji denied
  with `charset` or `control_char`; non string `text` denied.
- `duration_ms` and `times` at, below and above bounds (`OUT_OF_RANGE`), non integers denied,
  `speed` outside the enum denied, unknown input key denied, and no clamping in `clamp` mode.
- Rate limit: per action and per device windows, with a config override, and no hardcoded number.
- E-stop: both actions refused while latched (gateway and virtual bot), trip clears text.
- Taint: a `show_text` whose string equals a perception read needs approval; with
  `taint:override` it proceeds.
- Firmware mirror: a test that parses `device-deskbot.yaml`, `deskbot_gen.h` and the library TD
  and asserts one value for `text_max_chars`, `text_max_ms` and `blink.max_times`, and that the
  virtual bot enforces the same limits and acks with the 1.6 error strings.
- Restore: after `blink` and after text expiry the stored expression is shown (virtual bot).

Not in scope: speech or any audio, graphics or bitmap upload, markup or styling of any kind,
multi-line layout, scrolling, fonts, non ASCII text, and any new telemetry property or event for
the display. The simulator viewer cannot show text until a separate contract adds a way to
publish it.
