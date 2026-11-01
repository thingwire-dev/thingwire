# Task board

One row per workstream. Update your own row in your own PR (this file is writable by every
branch). Status: `queued`, `in-flight`, `review`, `merged`, `blocked`. PR numbers refer to
thingwire-dev/thingwire. Last full refresh: 2026-10-18.

| Workstream | Status | Owner agent / branch | PR | Files |
| --- | --- | --- | --- | --- |
| W0 housekeeping, first CI | merged (main) | `chore/w0-housekeeping` | #3 | `.github/workflows/ci.yml`, `.gitignore` |
| W1 virtual desk bot, viewer, desk-bot TD | merged (main) | `feat/w1-virtual-desk-bot` | #4 | `scripts/virtual_desk_bot.py`, `scripts/scene.yaml`, `viewer/`, `td-library/desk-bot.td.json` |
| W2 motion safety | merged (main) | `feat/w2-motion-safety` | #5 | `src/thingwire/safety_motion.py`, `safety_config.yaml` |
| W4 MCAP recorder and replay | merged (main) | `feat/w4-recorder-replay` | #2 | `src/thingwire/recorder.py`, `cli.py` |
| W5 desk bot firmware profile | merged (main) | `feat/w5-deskbot-firmware` | #6 | `firmware/`, `docs/desk-bot-wiring.md` |
| Deadman fix (injected clock, heartbeat replay) | merged (main) | `fix/deadman-injected-clock` | #7 | `src/thingwire/safety.py`, `tests/test_deadman.py` |
| Sensor provenance, taint policy, plausibility | merged (release) | `feat/provenance-taint` | #8 | `provenance.py`, `taint_policy.py`, `plausibility.py` |
| Per-device broker ACL | merged (release) | `feat/broker-acl-per-device` | #9 | `scripts/gen_broker_acl.py`, `docker/mosquitto-acl.conf` |
| Latched e-stop, safety_status, denial flood | merged (release) | `feat/estop-safety-status` | #11 | `safety_estop.py`, `safety_bridge.py`, `safety_status.py`, `safety_flood.py` |
| W3 theAuth identity and approvals | review | `feat/w3-theauth-identity` | #10 | `docker/theauth/`, `identity.py`, `mcp_server.py`, `cli.py` (shared: needs label) |
| W7 ROS 2 sim spike | review | `feat/w7-ros2-sim-spike` | #12 | `src/thingwire/ros2*.py`, docs |
| Agent environment (this change) | review | `chore/agent-environment` | see PR | `AGENTS.md`, `.agents/`, `Makefile`, CI, `docs/contracts.md`, `docs/agents/` |
| Integration: wire provenance, e-stop, identity into the gateway; gateway MQTT credentials for ACL broker | review | `feat/integration-gateway` | see PR | `mcp_server.py`, `cli.py`, `audit_log.py` (shared) |
| W6 injection demo | review | `feat/w6-injection-benchmark` | | `examples/injection-demo/`, `tests/test_injection*.py` |
| Ruff and mypy --strict cleanup | queued | unassigned (Haiku, mechanical) | | files not yet in `.agents/strict-files.txt`; add each file to the list when clean |
| Dash cleanup of legacy files | queued | unassigned (Haiku, mechanical) | | files in `.agents/dash-allowlist.txt`; remove an entry when its file is clean |
| First release PR `release/robot-stack-v0` to main | queued | owner | | see `RELEASE.md` |
| Integration: desk bot behaviour loop in simulation | review | `feat/integration-behaviour-loop` | not opened | `examples/desk-bot-behaviour/`, `tests/test_e2e_behaviour*.py` |

Notes
- Early PRs (#2 to #7) merged straight to main before the release branch existed. From now on
  everything goes through `release/robot-stack-v0`.
- Add a row when you start work; never delete a row, move it to `merged`.
