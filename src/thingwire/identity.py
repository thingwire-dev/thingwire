"""Agent identity: strict JWT verification against the theauth-go JWKS and scope mapping.

Fail closed: any problem with a token (signature, issuer, audience, expiry, type, algorithm,
claims shape) rejects it. Only EdDSA is accepted, so alg=none and HS256 key-confusion tokens
never reach signature checking.
"""

import asyncio
import json
import logging
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import jwt
from fastmcp.server.auth import AccessToken, TokenVerifier
from fastmcp.server.dependencies import get_access_token
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

ALLOWED_ALG = "EdDSA"
ALLOWED_TYP = {"at+jwt", "application/at+jwt"}
FORBIDDEN_HEADERS = {"jku", "jwk", "x5u", "x5c", "crit"}
REQUIRED_CLAIMS = ["exp", "iat", "iss", "aud", "sub", "jti"]
AGENT_PREFIX = "agent:"
DEVICE_URN_PREFIX = "urn:thingwire:device:"
MAX_CHAIN_DEPTH = 3
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
_NAME_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
_ID_RE = re.compile(r"^[A-Za-z0-9]{1,64}$")


class IdentityError(Exception):
    """A token or configuration was rejected. `reason` is safe to log, never contains the token."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class AgentIdentity:
    """The verified acting agent behind a request."""

    agent_id: str
    subject: str
    delegation_chain: tuple[str, ...]
    scopes: frozenset[str]
    jti: str = ""


class IdentitySettings(BaseSettings):
    """Identity configuration, from THINGWIRE_AUTH_* environment variables."""

    issuer: str = ""
    audience: str = "http://localhost:8000/mcp"
    jwks_url: str = ""
    admin_url: str = ""
    admin_token: str = ""
    allow_http: bool = False
    leeway_seconds: int = 5
    max_token_lifetime: int = 3600
    approval_timeout: float = 120.0
    approval_poll: float = 2.0
    host: str = "127.0.0.1"
    port: int = 8000
    path: str = "/mcp"

    model_config = {"env_prefix": "THINGWIRE_AUTH_"}

    def validate_complete(self) -> None:
        """Raise IdentityError unless HTTP transport can run safely."""
        if not (self.issuer and self.audience and self.admin_token):
            raise IdentityError(
                "http transport needs THINGWIRE_AUTH_ISSUER, _AUDIENCE, _ADMIN_TOKEN"
            )
        for url in (self.issuer, self.jwks_url or self.issuer, self.admin_url or self.issuer):
            u = urlparse(url)
            local = (u.hostname or "") in LOOPBACK_HOSTS
            if u.scheme != "https" and not (u.scheme == "http" and (local or self.allow_http)):
                raise IdentityError(
                    f"identity URL must be https (or loopback): {u.scheme}://{u.hostname}"
                )


def scope_read(device_id: str) -> str:
    """Scope that allows reading a device."""
    return f"device:{device_id}:read"


def scope_act(device_id: str, action: str) -> str:
    """Scope that allows invoking one action on a device."""
    return f"device:{device_id}:act:{action}"


def scope_dangerous(device_id: str) -> str:
    """Scope that allows any dangerous action on a device (approval is still required)."""
    return f"device:{device_id}:act:dangerous"


def device_key(device_id: str) -> str:
    """Scope name of a device: a TD id such as urn:thingwire:device:abc becomes abc."""
    return device_id.removeprefix(DEVICE_URN_PREFIX)


def valid_name(name: str) -> bool:
    """Device and action names must not contain ':' or spaces, so scopes cannot collide."""
    return bool(_NAME_RE.fullmatch(name))


JwksFetcher = Callable[[], dict[str, Any]]


def fetch_jwks_http(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Fetch a JWKS document over HTTP(S). Redirects are not followed."""

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    with opener.open(url, timeout=timeout) as resp:
        data = json.loads(resp.read(1 << 20))
    if not isinstance(data, dict):
        raise IdentityError("jwks is not an object")
    return data


class JwksKeys:
    """Ed25519 public keys by kid. Unknown kids trigger a refresh, at most once per interval."""

    def __init__(self, fetch: JwksFetcher, ttl: float = 600.0, min_refresh: float = 30.0) -> None:
        self._fetch, self._ttl, self._min_refresh = fetch, ttl, min_refresh
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    def _load(self) -> None:
        keys: dict[str, Any] = {}
        for jwk in self._fetch().get("keys", []):
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or not jwk.get("kid"):
                continue
            keys[jwk["kid"]] = jwt.PyJWK.from_dict(jwk, algorithm=ALLOWED_ALG).key
        self._keys, self._fetched_at = keys, time.monotonic()

    async def key_for(self, kid: str) -> Any:
        """Return the public key for kid or raise IdentityError."""
        async with self._lock:
            age = time.monotonic() - self._fetched_at
            if kid not in self._keys and (age > self._min_refresh or not self._keys):
                try:
                    await asyncio.to_thread(self._load)
                except Exception as exc:
                    self._fetched_at = time.monotonic()
                    logger.warning("JWKS fetch failed: %s", type(exc).__name__)
                    raise IdentityError("jwks unavailable") from exc
            elif age > self._ttl:
                self._keys = {}
            key = self._keys.get(kid)
        if key is None:
            raise IdentityError("unknown signing key")
        return key


def _chain(claims: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Return (acting agent id, delegation chain) from sub and the nested act claim."""
    sub = claims["sub"]
    if not isinstance(sub, str) or not sub:
        raise IdentityError("bad sub")
    chain = [sub]
    act = claims.get("act")
    while act is not None:
        if (
            not isinstance(act, dict)
            or not isinstance(act.get("sub"), str)
            or len(chain) >= MAX_CHAIN_DEPTH
        ):
            raise IdentityError("bad actor chain")
        chain.append(act["sub"])
        act = act.get("act")
    actor = chain[-1] if len(chain) > 1 else chain[0]
    if not actor.startswith(AGENT_PREFIX) or not _ID_RE.fullmatch(actor[len(AGENT_PREFIX) :]):
        raise IdentityError("not an agent token")
    return actor[len(AGENT_PREFIX) :], tuple(chain)


class AgentTokenVerifier(TokenVerifier):
    """FastMCP token verifier: strict EdDSA JWT checks, then identity extraction."""

    def __init__(self, settings: IdentitySettings, keys: JwksKeys) -> None:
        super().__init__()
        self._s, self._keys = settings, keys

    async def verify(self, token: str) -> AgentIdentity:
        """Verify a bearer token and return the identity, or raise IdentityError."""
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise IdentityError("malformed token") from exc
        if header.get("alg") != ALLOWED_ALG:
            raise IdentityError("algorithm not allowed")
        if str(header.get("typ", "")).lower() not in ALLOWED_TYP:
            raise IdentityError("wrong token type")
        if FORBIDDEN_HEADERS & header.keys():
            raise IdentityError("forbidden header")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise IdentityError("missing kid")
        key = await self._keys.key_for(kid)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[ALLOWED_ALG],
                audience=self._s.audience,
                issuer=self._s.issuer,
                leeway=self._s.leeway_seconds,
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.PyJWTError as exc:
            raise IdentityError(f"invalid token: {type(exc).__name__}") from exc
        if claims["exp"] - claims["iat"] > self._s.max_token_lifetime:
            raise IdentityError("token lifetime too long")
        scope = claims.get("scope")
        if not isinstance(scope, str):
            raise IdentityError("missing scope claim")
        agent_id, chain = _chain(claims)
        return AgentIdentity(
            agent_id, claims["sub"], chain, frozenset(scope.split()), str(claims["jti"])
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        """FastMCP hook: None means 401."""
        try:
            ident = await self.verify(token)
        except IdentityError as exc:
            logger.warning("Token rejected: %s", exc.reason)
            return None
        return AccessToken(
            token="",
            client_id=ident.agent_id,
            scopes=sorted(ident.scopes),
            subject=ident.subject,
            claims={
                "agent_id": ident.agent_id,
                "delegation_chain": list(ident.delegation_chain),
                "jti": ident.jti,
            },
        )


def current_identity() -> AgentIdentity | None:
    """Identity of the in-flight request, or None when there is no verified token."""
    tok = get_access_token()
    if tok is None or not tok.claims.get("agent_id"):
        return None
    chain = tuple(str(c) for c in tok.claims.get("delegation_chain", []))
    return AgentIdentity(
        str(tok.claims["agent_id"]),
        tok.subject or "",
        chain,
        frozenset(tok.scopes),
        str(tok.claims.get("jti", "")),
    )
