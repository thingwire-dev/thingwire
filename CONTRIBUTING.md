# Contributing to ThingWire

## Dev Environment Setup

Requires Python 3.11+ and (optionally) PlatformIO for firmware work.

```bash
git clone https://github.com/thingwire-dev/thingwire.git
cd thingwire
pip install -e ".[dev]"
```

For firmware (ESP32-S3), install PlatformIO and run:

```bash
cd firmware
pio run
```

## Running Tests

```bash
pytest tests/ -v
```

For coverage:

```bash
pytest tests/ -v --cov=thingwire --cov-report=term-missing
```

## Linting

```bash
ruff check src/
ruff format --check src/
```

Fix automatically:

```bash
ruff check --fix src/
ruff format src/
```

## Commit Message Format

```
<type>: <short description>
```

Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`

Examples:
- `feat: add humidity sensor TD loader`
- `fix: handle missing affordance in tool compiler`
- `test: add coverage for safety layer rejection`

One subject line. No trailing period. Keep it under 72 characters.

## Adding a New Device Type

1. Write a WoT Thing Description (JSON-LD) for the device under `examples/`.
2. Add a virtual simulator script under `scripts/` that publishes telemetry
   over MQTT so the device can be tested without hardware.
3. Add tests covering TD parsing, tool generation, and any actuator commands
   through the safety layer.
4. Document the device in `docs/`.

## Pull Requests

- Open against `main`.
- Keep PRs focused — one feature or fix per PR.
- All tests must pass and lint must be clean before requesting review.
- New actuator support requires a safety layer test demonstrating rejection
  of out-of-range commands.

## Reporting Issues

Use GitHub Issues. Include steps to reproduce, expected vs. actual behavior,
and relevant logs or Thing Description JSON.
