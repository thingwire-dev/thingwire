# Broker security

## Known limitation: the default broker is anonymous

The default Mosquitto config (a plain `docker compose up -d`) sets
`allow_anonymous true`. Any client that can reach port 1883 or the WebSocket
port 9001 can publish to any `thingwire/<id>/...` topic, including `command`
and `estop`, and can impersonate any device. Use the default broker only on a
trusted local network.

## ACL profile: one compromised body cannot publish to another

```bash
python scripts/gen_broker_acl.py --device desk-bot-1 --device bme-1
# or --roster data/roster.json, or --td path/to/td.json (urn suffix or file stem)
docker compose -f docker/docker-compose.yml --profile acl up -d mosquitto-acl
```

The ACL broker listens on host port 1884 (the default broker keeps 1883), so
both can run side by side.

Output goes to `docker/acl/` (git-ignored): `mosquitto.acl`, `mosquitto.passwd`
(PBKDF2-SHA512 `$7$` hashes) and `credentials.json` (plaintext, mode 0600).
Never commit or log `credentials.json`. Device ids must match
`[A-Za-z0-9._-]{1,64}`; `+`, `#`, `/`, `:` and whitespace are rejected.

| User | Publish | Subscribe |
| --- | --- | --- |
| device `<id>` | `thingwire/<id>/td\|telemetry\|status\|event\|safety_status` | `thingwire/<id>/command`, `thingwire/<id>/estop`, `thingwire/estop` |
| `thingwire-gateway` | `thingwire/+/command`, `thingwire/+/estop`, `thingwire/estop` | `thingwire/+/td\|telemetry\|status\|event\|safety_status` |

Everything else is denied. The ACL profile has no WebSocket listener, so the
browser viewer works only with the default broker. The gateway process does not yet
read MQTT credentials from its config, so it cannot connect to the ACL broker
until that support lands. Anonymous access is disabled and the password file
holds only hashes, but plaintext passwords must still be delivered to devices
out of band. There is no TLS yet, so credentials cross the network in clear.
