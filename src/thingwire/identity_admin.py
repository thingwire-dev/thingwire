"""Operator-side identity helpers behind `thingwire identity init|token` and `thingwire approve`."""

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from thingwire.identity import IdentityError, scope_act, scope_read, valid_name
from thingwire.identity_gate import TheAuthClient

RESERVED_ACTIONS = {"dangerous"}


def safe_scopes(device_id: str, safe_actions: list[str]) -> list[str]:
    """Read plus safe-act scopes only. Dangerous scopes can never be requested here."""
    if not valid_name(device_id) or not all(valid_name(a) for a in safe_actions):
        raise IdentityError("device and action names may only use letters, digits, '_', '.', '-'")
    if RESERVED_ACTIONS & set(safe_actions):
        raise IdentityError("the dangerous scope cannot be granted by identity init")
    return [scope_read(device_id), *(scope_act(device_id, a) for a in safe_actions)]


def check_resource(client: TheAuthClient, resource: str, scopes: list[str]) -> None:
    """Confirm the service advertises the gateway as a resource with these scopes."""
    path = urlparse(resource).path
    status, doc = client.request("GET", f"/.well-known/oauth-protected-resource{path}", admin=False)
    if status != 200 or doc.get("resource") != resource:
        raise IdentityError(f"service does not know resource {resource}; set THEAUTH_RESOURCE")
    missing = sorted(set(scopes) - set(doc.get("scopes_supported", [])))
    if missing:
        raise IdentityError(
            f"scopes not in the service catalog (THEAUTH_SCOPES): {', '.join(missing)}"
        )


def create_agent(client: TheAuthClient, name: str, scopes: list[str]) -> dict[str, Any]:
    """Register a demo agent limited to the given scopes."""
    status, body = client.request("POST", "/thingwire/agents", {"name": name, "scopes": scopes})
    if status == 401:
        raise IdentityError("operator token rejected")
    if status != 201 or "client_secret" not in body:
        raise IdentityError(f"agent creation failed (HTTP {status})")
    return body


def mint_token(
    client: TheAuthClient, creds: dict[str, Any], resource: str, scopes: list[str]
) -> dict[str, Any]:
    """Get an access token for the agent with exactly the requested scopes."""
    status, body = client.request(
        "POST",
        "/oauth/token",
        form={"grant_type": "client_credentials", "resource": resource, "scope": " ".join(scopes)},
        basic=f"{creds['client_id']}:{creds['client_secret']}",
        admin=False,
    )
    if status != 200 or "access_token" not in body:
        raise IdentityError(f"token request failed (HTTP {status}): {body.get('error', 'unknown')}")
    return body


def save_credentials(path: str, creds: dict[str, Any]) -> None:
    """Write agent credentials readable only by the owner. Refuses to overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f)


def load_credentials(path: str) -> dict[str, Any]:
    """Read credentials saved by save_credentials."""
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict) or "client_id" not in data or "client_secret" not in data:
        raise IdentityError("credentials file is malformed")
    return data


def decide_approval(client: TheAuthClient, approval_id: str, approve: bool) -> dict[str, Any]:
    """Approve or deny a pending approval through the theauth-go inbox."""
    if not valid_name(approval_id):
        raise IdentityError("invalid approval id")
    verb = "approve" if approve else "deny"
    status, body = client.request("POST", f"/approvals/{approval_id}/{verb}")
    if status in (401, 403):
        raise IdentityError("operator token rejected")
    if status == 404:
        raise IdentityError("no such approval")
    if status == 409:
        raise IdentityError("approval already decided, spent or expired")
    if status != 200:
        raise IdentityError(f"{verb} failed (HTTP {status})")
    return body


def pending_approvals(client: TheAuthClient) -> list[dict[str, Any]]:
    """List pending approvals from the operator inbox."""
    status, body = client.request("GET", "/approvals?status=pending")
    if status in (401, 403):
        raise IdentityError("operator token rejected")
    if status != 200:
        raise IdentityError(f"list failed (HTTP {status})")
    return [a for a in body.get("approvals", []) if isinstance(a, dict)]
