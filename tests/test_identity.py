"""Token verification, scope mapping and JWKS handling. No running identity server needed."""

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa

from thingwire.identity import (
    AgentTokenVerifier,
    IdentityError,
    IdentitySettings,
    JwksKeys,
    fetch_jwks_http,
    scope_act,
    scope_read,
)

ISS, AUD, KID = "http://localhost:9200", "http://localhost:8000/mcp", "kid-1"
AGENT = "01M58E5C05G4XVCFK0PTF1A3N8"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwks_for(*pairs: tuple[str, ed25519.Ed25519PrivateKey]) -> dict[str, Any]:
    keys = []
    for kid, key in pairs:
        raw = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        keys.append({"kty": "OKP", "crv": "Ed25519", "x": b64(raw), "kid": kid, "alg": "EdDSA"})
    return {"keys": keys}


def claims(**over: Any) -> dict[str, Any]:
    now = int(time.time())
    base: dict[str, Any] = {
        "iss": ISS,
        "aud": AUD,
        "sub": f"agent:{AGENT}",
        "iat": now,
        "exp": now + 900,
        "jti": "j1",
        "scope": f"{scope_read('desk-bot')} {scope_act('desk-bot', 'look_at')}",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


@pytest.fixture
def key() -> ed25519.Ed25519PrivateKey:
    return ed25519.Ed25519PrivateKey.generate()


@pytest.fixture
def mint(key: ed25519.Ed25519PrivateKey) -> Callable[..., str]:
    def _mint(
        payload: dict[str, Any] | None = None,
        signer: ed25519.Ed25519PrivateKey | None = None,
        **headers: Any,
    ) -> str:
        hdr = {"kid": KID, "typ": "at+jwt", **headers}
        hdr = {k: v for k, v in hdr.items() if v is not None}
        return jwt.encode(payload or claims(), signer or key, algorithm="EdDSA", headers=hdr)

    return _mint


@pytest.fixture
def verifier(key: ed25519.Ed25519PrivateKey) -> AgentTokenVerifier:
    settings = IdentitySettings(issuer=ISS, audience=AUD, gateway_token="x")
    return AgentTokenVerifier(settings, JwksKeys(lambda: jwks_for((KID, key))))


async def rejected(verifier: AgentTokenVerifier, token: str, fragment: str = "") -> None:
    with pytest.raises(IdentityError) as exc:
        await verifier.verify(token)
    assert fragment in exc.value.reason
    assert await verifier.verify_token(token) is None  # the transport answers 401


class TestAccepts:
    async def test_valid_token(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str]
    ) -> None:
        ident = await verifier.verify(mint())
        assert ident.agent_id == AGENT
        assert ident.delegation_chain == (f"agent:{AGENT}",)
        assert scope_read("desk-bot") in ident.scopes and ident.jti == "j1"
        access = await verifier.verify_token(mint())
        assert access is not None and access.claims["agent_id"] == AGENT

    async def test_delegation_chain(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str]
    ) -> None:
        act = {"sub": "agent:B2", "act": {"sub": "agent:A1"}}
        ident = await verifier.verify(mint(claims(sub="USER1", act=act)))
        assert ident.agent_id == "B2"  # newest actor is the top-level act (theauth chain.Prepend)
        assert ident.delegation_chain == ("USER1", "agent:B2", "agent:A1")


class TestRejects:
    @pytest.mark.parametrize(
        ("over", "fragment"),
        [
            ({"exp": int(time.time()) - 3600, "iat": int(time.time()) - 4500}, "Expired"),
            ({"aud": "http://evil.example/mcp"}, "InvalidAudience"),
            ({"aud": ["http://other", "http://another"]}, "InvalidAudience"),
            ({"iss": "http://evil.example"}, "InvalidIssuer"),
            ({"exp": None}, "MissingRequired"),
            ({"iat": None}, "MissingRequired"),
            ({"jti": None}, "MissingRequired"),
            ({"iat": int(time.time()) + 3600, "exp": int(time.time()) + 4000}, "Immature"),
            ({"nbf": int(time.time()) + 3600}, "Immature"),
            ({"exp": int(time.time()) + 90000}, "lifetime"),
            ({"scope": ["a", "b"]}, "scope"),
            ({"scope": None}, "scope"),
            ({"sub": "user-123"}, "not an agent"),
            ({"sub": "agent:"}, "not an agent"),
            ({"sub": "agent:../etc"}, "not an agent"),
            (
                {"act": {"sub": "agent:A", "act": {"sub": "agent:B", "act": {"sub": "agent:C"}}}},
                "chain",
            ),
            ({"act": "agent:A"}, "chain"),
        ],
    )
    async def test_bad_claims(
        self,
        verifier: AgentTokenVerifier,
        mint: Callable[..., str],
        over: dict[str, Any],
        fragment: str,
    ) -> None:
        await rejected(verifier, mint(claims(**over)), fragment)

    async def test_tampered_payload(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str]
    ) -> None:
        h, _, s = mint().split(".")
        evil = b64(json.dumps(claims(scope="device:desk-bot:act:sweep")).encode())
        await rejected(verifier, f"{h}.{evil}.{s}", "InvalidSignature")

    async def test_tampered_signature(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str]
    ) -> None:
        h, p, s = mint().split(".")
        raw = bytearray(base64.urlsafe_b64decode(s + "=="))
        raw[0] ^= 0x01
        await rejected(verifier, f"{h}.{p}.{b64(bytes(raw))}", "InvalidSignature")

    async def test_other_key_same_kid(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str]
    ) -> None:
        forged = mint(signer=ed25519.Ed25519PrivateKey.generate())
        await rejected(verifier, forged, "InvalidSignature")

    async def test_unknown_kid(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str]
    ) -> None:
        await rejected(verifier, mint(kid="other"), "unknown signing key")

    @pytest.mark.parametrize(
        "hdr",
        [
            {"kid": None},
            {"typ": "JWT"},
            {"typ": None},
            {"jku": "http://x/jwks"},
            {"jwk": {"kty": "OKP"}},
            {"x5u": "http://x"},
            {"crit": ["b64"]},
        ],
    )
    async def test_bad_headers(
        self, verifier: AgentTokenVerifier, mint: Callable[..., str], hdr: dict[str, Any]
    ) -> None:
        await rejected(verifier, mint(**hdr))

    async def test_alg_none(self, verifier: AgentTokenVerifier) -> None:
        head = b64(json.dumps({"alg": "none", "typ": "at+jwt", "kid": KID}).encode())
        body = b64(json.dumps(claims()).encode())
        await rejected(verifier, f"{head}.{body}.", "algorithm not allowed")
        await rejected(verifier, f"{head}.{body}", "")

    async def test_hs256_key_confusion(
        self, key: ed25519.Ed25519PrivateKey, verifier: AgentTokenVerifier
    ) -> None:
        """Sign with HMAC using the public key bytes as the secret."""
        pub = key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        head = b64(json.dumps({"alg": "HS256", "typ": "at+jwt", "kid": KID}).encode())
        body = b64(json.dumps(claims()).encode())
        sig = b64(hmac.new(pub, f"{head}.{body}".encode(), hashlib.sha256).digest())
        await rejected(verifier, f"{head}.{body}.{sig}", "algorithm not allowed")

    async def test_rs256_token(self, verifier: AgentTokenVerifier) -> None:
        rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        tok = jwt.encode(
            claims(), rsa_key, algorithm="RS256", headers={"kid": KID, "typ": "at+jwt"}
        )
        await rejected(verifier, tok, "algorithm not allowed")

    @pytest.mark.parametrize("junk", ["", "abc", "a.b.c", "....", "Bearer x", "\x00"])
    async def test_garbage(self, verifier: AgentTokenVerifier, junk: str) -> None:
        await rejected(verifier, junk)


class TestJwks:
    async def test_refetch_is_rate_limited(self, key: ed25519.Ed25519PrivateKey) -> None:
        calls = []

        def fetch() -> dict[str, Any]:
            calls.append(1)
            return jwks_for((KID, key))

        keys = JwksKeys(fetch, min_refresh=60)
        await keys.key_for(KID)
        for _ in range(5):
            with pytest.raises(IdentityError):
                await keys.key_for("random-kid")  # attacker-chosen kids must not hammer the AS
        assert len(calls) == 1

    async def test_rotation_picks_up_new_key(self, key: ed25519.Ed25519PrivateKey) -> None:
        doc = {"v": jwks_for((KID, key))}
        keys = JwksKeys(lambda: doc["v"], min_refresh=0)
        await keys.key_for(KID)
        new = ed25519.Ed25519PrivateKey.generate()
        doc["v"] = jwks_for((KID, key), ("kid-2", new))
        assert await keys.key_for("kid-2") is not None

    async def test_fetch_failure_denies(self) -> None:
        def boom() -> dict[str, Any]:
            raise OSError("down")

        with pytest.raises(IdentityError, match="jwks unavailable"):
            await JwksKeys(boom).key_for(KID)

    async def test_non_ed25519_keys_ignored(self, key: ed25519.Ed25519PrivateKey) -> None:
        doc = jwks_for((KID, key))
        doc["keys"].append({"kty": "oct", "k": "c2VjcmV0", "kid": "hmac"})
        keys = JwksKeys(lambda: doc)
        with pytest.raises(IdentityError):
            await keys.key_for("hmac")

    def test_http_fetch_and_no_redirect(self, key: ed25519.Ed25519PrivateKey) -> None:
        doc = json.dumps(jwks_for((KID, key))).encode()

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/jwks":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(doc)
                else:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/x")
                    self.end_headers()

            def log_message(self, *_: Any) -> None:
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        try:
            assert fetch_jwks_http(f"{base}/jwks")["keys"][0]["kid"] == KID
            with pytest.raises(OSError):
                fetch_jwks_http(f"{base}/moved")
        finally:
            srv.shutdown()
