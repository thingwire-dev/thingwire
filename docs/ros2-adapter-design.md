# ROS 2 adapter design (spike)

Status: spike. Nothing here is implemented in `src/`. The sim stack lives in `docker/ros2/` and the proof script is `scripts/ros2_mcp_smoke.py`.

## 1. Sim stack and findings

Run:

```bash
docker compose -f docker/ros2/compose.yaml up --build -d
uv run --with fastmcp python scripts/ros2_mcp_smoke.py
docker compose -f docker/ros2/compose.yaml down
```

| Item | Value |
| --- | --- |
| ROS distro | **Lyrical** (`ros:lyrical-ros-base`, Ubuntu 26.04.1, Python 3.14, multi-arch incl. arm64, apt packages `ros-lyrical-turtlesim`, `-rosbridge-server`, `-rosapi` 4.2.1) |
| Why Lyrical | The official arm64 image exists, so no VM is needed (the plan assumed Jazzy because of Ubuntu 26.04). Jazzy remains a one-line fallback (`FROM ros:jazzy-ros-base`, swap package prefix). |
| Docker | engine 28.3.0, arm64 |
| Pinned ros-mcp-server | `476591ac058f58cae810cc775e50b51f8d3d757c` (main HEAD on 2026-10-18, v3.1.2, committed 2026-09-23). Installed from the GitHub tarball of that SHA inside `Dockerfile.mcp`. Its PyPI dependencies are version ranges, not locked (`uv.lock` is not used by the image). |
| Lyrical gotcha | turtlesim messages moved to `turtlesim_msgs` (`turtlesim_msgs/msg/Pose`). The Jazzy name `turtlesim/msg/Pose` makes rosbridge log "Unable to import turtlesim.msg" and the subscribe silently returns nothing. |

Containment: no bind mounts, no privileged mode, no docker socket, `cap_drop: ALL`, `no-new-privileges`, non-root users. `ros` and `mcp` share an `internal: true` network (no internet egress, nothing published). A small socat relay is the only container on a routable network and publishes `127.0.0.1:9090` (rosbridge) and `127.0.0.1:9000` (MCP). Docker cannot publish ports from an internal-only network, hence the relay.

### Source review of ros-mcp-server @ 476591a

Scope: `server.py`, `ros_mcp/` (about 6,600 lines incl. prompt files, 31 tools).

- Network: the only outbound connection is a rosbridge WebSocket client (`websocket-client`) to an IP and port that the LLM can set at runtime via `connect_to_robot`. No HTTP client, no telemetry, no analytics, no update checks found by grep (`http`, `requests`, `urllib`, `telemetry`, `analytics`).
- `connect_to_robot` / `ping_robots` accept an arbitrary IP and port from the tool caller, then ping and TCP-probe it. That is an SSRF and internal port-scan primitive for any MCP client. `subprocess.run(["ping", ...])` uses an argument list (no shell), but the `ip` string is passed unvalidated as an argument.
- Filesystem: writes camera frames to `./camera/` (`utils/websocket.py`), and `view_saved_image` reads a caller-supplied path (`os.path.exists` then `PIL.Image.open`), an arbitrary image-file read primitive.
- Default HTTP bind is 127.0.0.1, but `--host 0.0.0.0` is documented. There is no auth on the MCP endpoint.
- Tool surface includes raw `publish_once`, `publish_for_durations`, `call_service`, `send_action_goal`, `set_parameter`, `delete_parameter`. No allowlist, bounds, rate limit or audit.
- No `eval`/`exec`/`pickle`/yaml-unsafe-load found.
- Not covered: transitive dependencies (fastmcp, mcp, opencv, pillow) were not audited.

### Gap proof

`scripts/ros2_mcp_smoke.py` sends no credentials, then publishes `linear.x = 1e9` to `/turtle1/cmd_vel`. Observed output:

```
pose BEFORE: {'x': 10.206, 'y': 10.602, 'theta': 0.504}
pose AFTER:  {'x': 10.929, 'y': 11.089, 'theta': 1.008}
publish_once linear.x=1e9 -> {'success': True, ...}
GAP unauthenticated client call accepted: True
GAP absurd velocity (1e9 m/s) accepted, no bound enforced: True
```

## 2. Mapping a WoT TD affordance to ROS 2

ROS 2 binding vocabulary on a TD form (extension prefix `tw:ros2`):

| TD affordance | ROS 2 target | Form fields |
| --- | --- | --- |
| property (read) | topic subscribe (latest value cache) | `tw:ros2Kind: "topic"`, `tw:ros2Name`, `tw:ros2Type` |
| property (read, slow) | parameter get or service call | `tw:ros2Kind: "service"` |
| event | topic subscribe, each message becomes an event | `topic` |
| action, fire and forget (`look_at`, `set_velocity`) | topic publish | `topic`, optional `tw:ros2Duration` |
| action, request/response | service call | `service` |
| action, long running (`move_to`, `follow_trajectory`) | ROS 2 action goal, feedback, cancel | `action` |

Example (turtlesim):

```json
{"actions": {"drive": {
  "input": {"type": "object", "properties": {
    "linear":  {"type": "number", "minimum": -2.0, "maximum": 2.0, "unit": "m/s"},
    "angular": {"type": "number", "minimum": -2.0, "maximum": 2.0, "unit": "rad/s"}}},
  "safe": false,
  "forms": [{"href": "ros2:///turtle1/cmd_vel",
             "tw:ros2Kind": "topic", "tw:ros2Type": "geometry_msgs/msg/Twist",
             "tw:ros2Template": {"linear": {"x": "$linear"}, "angular": {"z": "$angular"}}}]}}}
```

The TD input schema already carries the numeric bounds ThingWire's existing `safety.py` enforces, so the same min/max become the proxy's message bounds. Axes outside the TD schema are not expressible, so the template cannot produce them. Action goals map to the existing "approval needed" path (`safe: false`) and `cancel` maps to the deadman switch.

## 3. Where safety and identity sit

```
LLM agent
  | MCP (streamable HTTP, bearer token from theauth-go)      <- identity, scopes
ThingWire gateway  (tool_compiler, safety.py, audit_log)     <- allow/deny, bounds, rate, approval, audit
  | Ros2Transport
ROS policy proxy  (WebSocket, rosbridge protocol)            <- last line: per-topic allowlist + message bounds
  | ws://127.0.0.1:9090 on an internal network only
rosbridge_server -> ROS graph
```

- ThingWire is the only client allowed to reach rosbridge. rosbridge never publishes on a host port in production; the sim publishes on loopback for the demo only.
- **Policy proxy** (separate small process, deny by default) speaks the rosbridge JSON protocol and inspects every frame before forwarding:
  - op allowlist: `subscribe`, `unsubscribe`, `publish`, `advertise`, `call_service`, `send_action_goal`, `cancel_action_goal`. Everything else (incl. rosapi parameter ops unless granted) is dropped.
  - per-topic/service/action allowlist with expected type (from the TD forms).
  - message bounds: per-field min/max from the TD schema, NaN/inf rejected, plus slew limits (`maxSlewDegPerSec` style extension) and a max publish rate and duration.
  - reject, never clamp, by default (matches `out_of_range: reject`).
  - audit row per decision with `agent_id`, `delegation_chain`, `scopes`, `decision`, `reason`.
- Why both layers: the gateway checks intent and identity; the proxy protects against a bypass (a compromised gateway, a rogue local client, or ros-mcp-server running next to it). Third-party ros-mcp-server can sit behind the proxy for the demo: point its `connect_to_robot` at the proxy instead of rosbridge, and the 1e9 call above becomes a denied frame.
- Scopes: `device:<id>:act:drive` for the action, plus `device:<id>:act:dangerous` and human approval for `safe: false` ones.

## 4. `Ros2Transport` beside the MQTT transport (not implemented)

Today `MqttBridge` is used directly. The adapter needs a small shared interface extracted from its public surface:

```python
class DeviceTransport(Protocol):
    state: ConnectionState
    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def wait_for_devices(self, timeout: float = 30.0) -> list[str]: ...
    def get_devices(self) -> list[str]: ...
    def get_td(self, device_id: str) -> ThingDescription | None: ...
    def get_latest_reading(self, device_id: str, property_name: str) -> dict[str, Any]: ...
    def is_device_online(self, device_id: str) -> bool: ...
    async def send_command(self, device_id: str, action: str, payload: dict[str, Any]) -> dict[str, Any]: ...


class Ros2Transport:
    """Talks to the policy proxy (never raw rosbridge) over a rosbridge WebSocket."""

    def __init__(self, config: Ros2Config) -> None: ...  # url, TD paths, topic cache size, timeouts
    # connect(): open WS, load TDs from td-library (ROS devices are static, no MQTT announce),
    #            subscribe to every readable property/event topic, fill a latest-value cache.
    # get_latest_reading(): serve from cache; staleness feeds is_device_online().
    # send_command(): resolve form -> render template -> publish | call_service | send_action_goal.
    #   For goals: return a goal id, stream feedback into the audit log, cancel on deadman timeout.
    # Reconnect with backoff; rosbridge drop marks every device offline.
```

Open points: the safety layer currently runs before `send_command`, so no change there; `Ros2Config` goes in `config.py`; a TD `tw:ros2*` vocabulary section belongs in `docs/wot-td-guide.md`; service/action QoS and message-type discovery can use `rosapi` through the proxy.

## 5. Findings that change the plan

1. The plan said "tested on turtlesim inside the OrbStack `ros2dev` VM (Jazzy)". Lyrical arm64 images exist, so the Docker stack replaces the VM for CI and demos.
2. Pin message type names per distro in TDs (`turtlesim_msgs` vs `turtlesim`), or discover through rosapi.
3. The SSRF-like `connect_to_robot` and arbitrary-path `view_saved_image` make ros-mcp-server unsafe to expose even behind the proxy unless those two tools are blocked; the proxy only governs rosbridge frames, not MCP tools.
