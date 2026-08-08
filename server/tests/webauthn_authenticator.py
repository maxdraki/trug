"""A headless WebAuthn software authenticator for end-to-end ceremony tests.

``SoftwareAuthenticator`` is a real (if minimal) FIDO2 authenticator implemented
in Python: it holds a real P-256 (ES256/-7) credential key, produces a real
``fmt:"none"`` attestation object and a real ES256 assertion signature, and
emits exactly the JSON shape the browser's ``navigator.credentials`` would POST
to our ``/verify`` endpoints. Nothing here is faked or monkeypatched — the bytes
are fed through the unmodified py_webauthn verifier the server uses in
production, so a passing test means our real verification accepts a correct
authenticator (and, when we deliberately misbehave, rejects a bad one).

Wire shapes (matched against py_webauthn's parsers):

* registration → ``{id, rawId, response:{attestationObject, clientDataJSON},
  type:"public-key", transports:[...]}`` — id/rawId are base64url(credentialId).
* authentication → ``{id, rawId, response:{authenticatorData, clientDataJSON,
  signature}, type:"public-key"}``.

All binary fields are base64url with padding stripped, matching
``webauthn.helpers.bytes_to_base64url``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os

import cbor2
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

# authenticatorData flag bits (WebAuthn §6.1)
_FLAG_UP = 1 << 0  # user present
_FLAG_UV = 1 << 2  # user verified
_FLAG_AT = 1 << 6  # attested credential data included

# COSE key common/EC2 parameter labels (RFC 8152)
_COSE_KTY = 1
_COSE_ALG = 3
_COSE_CRV = -1
_COSE_X = -2
_COSE_Y = -3
_KTY_EC2 = 2
_ALG_ES256 = -7
_CRV_P256 = 1


def _b64url(raw: bytes) -> str:
    """Base64URL without padding — identical to webauthn.helpers.bytes_to_base64url."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("utf-8")


class SoftwareAuthenticator:
    """A single virtual passkey authenticator holding one credential.

    Instantiate one per distinct device — the e2e test uses a second instance
    for the invited member so each has its own key pair and credential id, just
    as two real phones would.
    """

    def __init__(self, origin: str = "http://localhost:8000") -> None:
        self.origin = origin
        # Real P-256 private key — the credential's signing key.
        self._private_key = ec.generate_private_key(ec.SECP256R1())
        # A random 32-byte credential id (what a resident key would mint).
        self.credential_id = os.urandom(32)
        # AAGUID is all-zero for a "none"-attested software authenticator.
        self.aaguid = b"\x00" * 16
        # Signature counter — monotonic across assertions from this device.
        self.sign_count = 0

    # -- public identity ------------------------------------------------

    @property
    def credential_id_b64(self) -> str:
        return _b64url(self.credential_id)

    def _cose_public_key(self) -> bytes:
        """The credential's public key as a CBOR-encoded COSE_Key (EC2/P-256).

        py_webauthn re-encodes this with plain ``cbor2.dumps`` while walking the
        authenticatorData, so we must encode it the same way (default cbor2, not
        canonical) or the length accounting drifts and parsing fails.
        """
        numbers = self._private_key.public_key().public_numbers()
        x = numbers.x.to_bytes(32, "big")
        y = numbers.y.to_bytes(32, "big")
        return cbor2.dumps(
            {
                _COSE_KTY: _KTY_EC2,
                _COSE_ALG: _ALG_ES256,
                _COSE_CRV: _CRV_P256,
                _COSE_X: x,
                _COSE_Y: y,
            }
        )

    def _authenticator_data(self, rp_id: str, flags: int, sign_count: int, *,
                            attested: bool) -> bytes:
        """Assemble raw authenticatorData: rpIdHash | flags | signCount | [ACD]."""
        rp_id_hash = hashlib.sha256(rp_id.encode("utf-8")).digest()
        data = rp_id_hash + bytes([flags]) + sign_count.to_bytes(4, "big")
        if attested:
            data += (
                self.aaguid
                + len(self.credential_id).to_bytes(2, "big")
                + self.credential_id
                + self._cose_public_key()
            )
        return data

    def _client_data_json(self, ceremony_type: str, challenge: str) -> bytes:
        """clientDataJSON bytes. ``challenge`` is passed through verbatim — it is
        already the base64url string the options endpoint handed the client, and
        the verifier compares base64url_to_bytes(challenge) to the expected."""
        return json.dumps(
            {
                "type": ceremony_type,
                "challenge": challenge,
                "origin": self.origin,
                "crossOrigin": False,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    # -- registration ---------------------------------------------------

    def make_credential(self, options: dict, *, uv: bool = True) -> dict:
        """Produce the JSON a browser would POST to a /register (or bootstrap)
        /verify endpoint, given the server's registration options dict.

        Builds a real ``fmt:"none"`` attestationObject over authenticatorData
        that carries this device's credential id and COSE public key.
        """
        rp_id = options["rp"]["id"]
        challenge = options["challenge"]

        flags = _FLAG_UP | _FLAG_AT
        if uv:
            flags |= _FLAG_UV
        # A freshly-minted credential reports signCount 0.
        auth_data = self._authenticator_data(rp_id, flags, 0, attested=True)
        attestation_object = cbor2.dumps(
            {"fmt": "none", "attStmt": {}, "authData": auth_data}
        )
        client_data = self._client_data_json("webauthn.create", challenge)

        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "response": {
                "attestationObject": _b64url(attestation_object),
                "clientDataJSON": _b64url(client_data),
            },
            "type": "public-key",
            "transports": ["internal"],
            "clientExtensionResults": {},
        }

    # -- authentication -------------------------------------------------

    def get_assertion(self, options: dict, *, uv: bool = True,
                      sign_count: int | None = None) -> dict:
        """Produce the JSON a browser would POST to /auth/login/verify.

        By default the internal signature counter is incremented and used, as a
        real authenticator would. Pass an explicit ``sign_count`` to forge a
        stale/equal counter and exercise the clone-detection path.
        """
        rp_id = options["rpId"]
        challenge = options["challenge"]

        if sign_count is None:
            self.sign_count += 1
            sign_count = self.sign_count

        flags = _FLAG_UP
        if uv:
            flags |= _FLAG_UV
        auth_data = self._authenticator_data(rp_id, flags, sign_count, attested=False)
        client_data = self._client_data_json("webauthn.get", challenge)

        # Signature is over authenticatorData || SHA-256(clientDataJSON).
        client_data_hash = hashlib.sha256(client_data).digest()
        signature = self._private_key.sign(
            auth_data + client_data_hash, ec.ECDSA(hashes.SHA256())
        )

        return {
            "id": self.credential_id_b64,
            "rawId": self.credential_id_b64,
            "response": {
                "authenticatorData": _b64url(auth_data),
                "clientDataJSON": _b64url(client_data),
                "signature": _b64url(signature),
            },
            "type": "public-key",
            "clientExtensionResults": {},
        }


__all__ = ["SoftwareAuthenticator"]
