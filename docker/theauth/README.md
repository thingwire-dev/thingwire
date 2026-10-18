# ThingWire identity service

A small Go service on [theauth-go](https://github.com/glincker/theauth-go): OAuth 2.1
authorization server, agent identities, token exchange and tool approvals, stored in SQLite.

```sh
export THEAUTH_ENCRYPTION_KEY=$(openssl rand -hex 32)   # keep stable, it seals the signing keys
export THEAUTH_ADMIN_TOKEN=$(openssl rand -hex 24)
docker compose up -d --build
```

| Variable | Default | Meaning |
| --- | --- | --- |
| `THEAUTH_ISSUER` | `http://localhost:9200` | `iss` claim; the gateway must use the same value |
| `THEAUTH_RESOURCE` | `http://localhost:8000/mcp` | gateway URL, becomes the token `aud` |
| `THEAUTH_SCOPES` | demo device and desk bot scopes | catalog of scopes agents may hold |
| `THEAUTH_APPROVAL_TTL` | `5m` | how long an approval stays open |
| `THEAUTH_TOKEN_TTL` | `15m` | access token lifetime |

Scopes follow `device:<id>:read`, `device:<id>:act:<action>` and `device:<id>:act:dangerous`.
Add the scopes for your own devices to `THEAUTH_SCOPES` (comma separated).

Operator endpoints (bearer `THEAUTH_ADMIN_TOKEN`): `POST /thingwire/agents`,
`POST /thingwire/approvals`, `POST /thingwire/approvals/{id}/spend`, and the theauth-go
approvals inbox at `/approvals`. Everything else is the standard OAuth surface
(`/oauth/token`, `/oauth/jwks`, `/.well-known/*`).

The gateway side: `thingwire identity init`, `thingwire serve --transport http`,
`thingwire approve <id>`.
