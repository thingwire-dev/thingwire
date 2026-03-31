# Security Policy

## Supported Versions

| Version | Supported |
| ------- | --------- |
| 0.2.x   | Yes       |
| < 0.2   | No        |

## Reporting a Vulnerability

Do not open a public GitHub issue for security vulnerabilities.

Send a report to **security@thingwire.dev** with:

- A description of the vulnerability
- Steps to reproduce
- Affected versions
- Any known mitigations or workarounds

**Response timeline:**

- **48 hours** — acknowledgment of receipt
- **7 days** — initial assessment and severity classification
- **90 days** — target remediation window for confirmed vulnerabilities

We follow coordinated disclosure. We ask that you refrain from public
disclosure until a fix is available or the 90-day window has elapsed,
whichever comes first.

## Scope

Areas of particular concern for this project:

- MQTT broker authentication and authorization
- Safety layer bypass for actuator commands
- Malformed Thing Description injection
- MCP tool parameter validation
