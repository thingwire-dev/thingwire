# ThingWire identity service

A small Go service on [theauth-go](https://github.com/glincker/theauth-go): OAuth 2.1
authorization server, agent identities, token exchange and tool approvals, stored in SQLite.

```sh
export THEAUTH_ENCRYPTION_KEY=$(openssl rand -hex 32)   # keep stable, it seals the signing keys
export THEAUTH_GATEWAY_TOKEN=$(openssl rand -hex 24)    # given to the gateway only
export THEAUTH_OPERATOR_TOKEN=$(openssl rand -hex 24)   # given to humans only
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

## Two credentials, two roles

| Token | Holder | Allowed |
| --- | --- | --- |
| `THEAUTH_GATEWAY_TOKEN` | the gateway process (`THINGWIRE_AUTH_GATEWAY_TOKEN`) | `POST /thingwire/approvals`, `POST /thingwire/approvals/{id}/spend` |
| `THEAUTH_OPERATOR_TOKEN` | the human operator (`THINGWIRE_OPERATOR_TOKEN` in their own shell) | `POST /thingwire/agents`, the `/approvals` inbox (list, approve, deny) |

The service enforces the split: the gateway token is refused on operator routes and the operator
token on gateway routes. The operator token must never be set in the gateway's environment, in a
container the gateway runs in, or in any shell or config a coding agent can read. The gateway
refuses to start if it sees `THINGWIRE_OPERATOR_TOKEN` or `THEAUTH_OPERATOR_TOKEN`. An agent that
can read the gateway's environment gets only the right to open approvals (capped per agent by
`THEAUTH_MAX_PENDING`, default 3), never to approve them.

Everything else is the standard OAuth surface (`/oauth/token`, `/oauth/jwks`, `/.well-known/*`).

## Revocation

The gateway verifies tokens offline, so revoking or suspending an agent takes effect when its
token expires. Tokens older than 900 s are refused (`THINGWIRE_AUTH_MAX_TOKEN_LIFETIME`) and the
service issues 15 minute tokens, so the residual window is at most 15 minutes. Introspection would
close it but needs a registered confidential client; not done yet.

## Dependency pin

`go.mod` pins theauth-go and storage/sqlite to commit 9a8595de1c1a (branch release/agent-wave9).
The approvals admin store, `StorageApprovals.Spend` and `WithApprovalAudit` are not in any tag:
v2.12.0 lacks them and storage/sqlite v0.3.0 does not build against v2.12.0. Replace the pin with
the next tags once published.

The gateway side: `thingwire identity init`, `thingwire serve --transport http`,
`thingwire approve <id>`.
