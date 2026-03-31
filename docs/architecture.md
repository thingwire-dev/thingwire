# Architecture

## System overview

```
┌─────────────────────┐
│  AI Agent / LLM     │  (Claude Desktop, Cursor, any MCP client)
└────────┬────────────┘
         │ MCP (stdio or SSE)
         ▼
┌─────────────────────┐
│  ThingWire Gateway   │  (Python, Docker)
│                     │
│  WoT TD Loader      │  ← Parse device capability descriptions
│  Tool Compiler       │  ← Convert WoT TD → MCP tools
│  Safety Layer        │  ← Permissions, rate limits, audit
│  MQTT Bridge         │  ← Device communication
│  MCP Server          │  ← Expose tools to agents
└────────┬────────────┘
         │ MQTT
         ▼
┌─────────────────────┐
│  ESP32-S3 Device    │  (or virtual device simulator)
│  Sensors + Actuators │
└─────────────────────┘
```

## Data flow

### Reading a sensor

1. Agent calls `read_temperature` MCP tool
2. MCP server looks up latest telemetry from MQTT bridge
3. MQTT bridge returns cached reading (updated every 5s from device)
4. Audit log records the read
5. Result returned to agent

### Controlling an actuator

1. Agent calls `do_set_relay(state=true)` MCP tool
2. Safety layer checks: permission → rate limit → deadman switch
3. If dangerous action: log confirmation warning
4. MQTT bridge publishes command to `thingwire/{id}/command`
5. Device receives command, actuates relay, publishes ack
6. Audit log records command + result
7. Result returned to agent

## Key design decisions

- **WoT Thing Description** as the device capability format (W3C standard, not custom)
- **MQTT only** for device transport (universal IoT standard)
- **MCP only** for agent transport (works with Claude Desktop, Cursor)
- **Local-first** — no cloud required, gateway runs on your machine
- **Safety is P0** — every actuator command is checked and logged

## Module responsibilities

| Module | Responsibility | I/O? |
|--------|---------------|------|
| `td_loader.py` | Parse WoT TD JSON → Pydantic models | No |
| `tool_compiler.py` | WoT TD → MCP tool definitions | No |
| `mqtt_bridge.py` | All MQTT communication | Yes |
| `safety.py` | Permission checks, rate limiting | No (state is injectable) |
| `audit_log.py` | SQLite command logging | Yes |
| `mcp_server.py` | Wire everything, register tools | Orchestration only |
| `config.py` | Environment-based configuration | No |
