# ThingWire: Claude Code entry point

@AGENTS.md

Read AGENTS.md first. It holds every rule (branching, ownership, contracts, definition of done).
This file only adds the repo map and pointers.

## Repo layout (real)

```
src/thingwire/   Python 3.11 gateway: FastMCP server, MQTT bridge, safety layer, audit log, recorder
tests/           pytest suite (asyncio_mode=auto)
firmware/        ESP32-S3 C++ (PlatformIO, envs in platformio.ini); firmware/src/config.h is shared
scripts/         virtual devices, scene.yaml, repo tooling (check_*.py, agent_worktree.sh)
docker/          Mosquitto, Dockerfile, compose; docker/theauth/ is the identity service
viewer/          static face viewer over MQTT websockets
td-library/      Thing Descriptions (shared; desk-bot.td.json is the contract example)
docs/            public docs; docs/contracts.md is the contract source of truth
docs/agents/     TASKS.md (task board), RELEASE.md (release process)
.agents/         ownership.yaml, strict-files.txt, dash-allowlist.txt
```

Gitignored, never commit: `docs-local/` (private plans), `repo-ref/` (untrusted reference clones),
`phases/`, `data/`, `.env*`, `.claude/worktrees/`.

## Commands

`make verify` (definition of done), `make verify-fast`, `make firmware`,
`make worktree NAME= BRANCH= BASE=`, `make worktree-clean`.
