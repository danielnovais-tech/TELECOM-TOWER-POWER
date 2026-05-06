# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""
auth/saml_service.py — SAML 2.0 SP-initiated SSO (Okta + Azure AD).

Scope:
  * Build a SAML AuthnRequest for the HTTP-Redirect binding (no SP
    private key required by default — Okta/Azure accept unsigned
    AuthnRequests when their tenant is configured for it).
  * Verify a SAMLResponse delivered via the HTTP-POST binding:
      - XML signature on the Response or the Assertion (XML-DSig),
      - Issuer matches the configured IdP EntityID,
      - Status = urn:oasis:names:tc:SAML:2.0:status:Success,
      - Audience restriction matches the SP EntityID,
      - NotBefore / NotOnOrAfter window is current (clock skew = 60s),
      - Recipient (SubjectConfirmationData @Recipient) matches the ACS URL.
  * Extract NameID + a flat dict of attribute statements (email, name, …).

Design rules:
  * Pure-Python (lxml + signxml + defusedxml). No xmlsec1 system library
    required — works inside the existing ECS image and Lambda runtime.
  * Lazy imports so a missing dep at import time never breaks the API
    process; the /auth/saml endpoints surface a 503 instead.
  * Verification raises :class:`SamlError`. The caller maps this to
    HTTP 401 with a generic message; details are logged at WARNING.
  * Defaults are conservative: signed assertions required, RSA-SHA256+,
    canonicalisation per the XML-DSig spec via ``signxml``.

Env contract (all strings, all optional unless noted):

  SAML_SP_BASE_URL          (required) e.g. https://api.telecomtowerpower.com.br
  SAML_SP_ENTITY_ID         optional; defaults to SAML_SP_BASE_URL
  SAML_SP_ACS_PATH          optional; defaults to "/auth/saml/callback"

  # Okta
  SAML_OKTA_ENTITY_ID       e.g. http://www.okta.com/exk1abcd...
  SAML_OKTA_SSO_URL         e.g. https://acme.okta.com/app/.../sso/saml
  SAML_OKTA_X509_CERT       PEM block OR raw base64 DER of IdP signing cert

  # Azure AD (Microsoft Entra ID)
  SAML_AZURE_ENTITY_ID      e.g. https://sts.windows.net/<tenant-id>/
  SAML_AZURE_SSO_URL        e.g. https://login.microsoftonline.com/<tid>/saml2
  SAML_AZURE_X509_CERT      PEM block OR raw base64 DER of IdP signing cert

NOT in scope (deliberate, MVP):
  * SP-side AuthnRequest signing (requires SP private key + cert mgmt).
  * Encrypted assertions (``EncryptedAssertion``); plaintext only.
  * Replay protection via InResponseTo store — call sites should add a
    Redis-backed nonce when stricter SOC2 evidence is needed.
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
import time
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin

logger = logging.getLogger("saml_service")


class SamlError(Exception):
    """Raised when SAML config, request or response handling fails."""


# Allow up to 60 seconds clock skew between SP and IdP (matches Okta/Azure docs).
_CLOCK_SKEW_S = int(os.getenv("SAML_CLOCK_SKEW_S", "60"))

# SAML namespaces.
_NS = {
    "saml":  "urn:oasis:names:tc:SAML:2.0:assertion",
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "ds":    "http://www.w3.org/2000/09/xmldsig#",
}


# ─── Lazy imports ──────────────────────────────────────────────────────

def _import_lxml():
    try:
        from lxml import etree  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SamlError(f"lxml not installed: {exc}") from exc
    return etree


def _import_defusedxml():
    try:
        from defusedxml.lxml import fromstring  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SamlError(f"defusedxml not installed: {exc}") from exc
    return fromstring


def _import_signxml():
    try:
        from signxml import XMLVerifier  # type: ignore[import-untyped]
    except ImportError as exc:
        raise SamlError(f"signxml not installed: {exc}") from exc
    return XMLVerifier


# ─── IdP registry ───────────────────────────────────────────────────────

@dataclass(frozen=True)
class IdpConfig:
    name: str
    entity_id: str
    sso_url: str
    x509_cert_pem: str
    want_signed_assertion: bool = True


def _normalise_pem(raw: str) -> str:
    """Accept either a PEM block or single-line base64 DER and return PEM."""
    s = (raw or "").strip()
    if not s:
        return ""
    if "-----BEGIN CERTIFICATE-----" in s:
        return s
    # Single-line base64 DER → wrap as PEM.
    body = "".join(s.split())
    chunks = [body[i:i + 64] for i in range(0, len(body), 64)]
    return "-----BEGIN CERTIFICATE-----\n" + "\n".join(chunks) + "\n-----END CERTIFICATE-----\n"


def _idp_from_env(prefix: str, name: str) -> Optional[IdpConfig]:
    entity = os.getenv(f"SAML_{prefix}_ENTITY_ID", "").strip()
    sso = os.getenv(f"SAML_{prefix}_SSO_URL", "").strip()
    cert = os.getenv(f"SAML_{prefix}_X509_CERT", "")
    if not entity or not sso or not cert.strip():
        return None
    return IdpConfig(
        name=name,
        entity_id=entity,
        sso_url=sso,
        x509_cert_pem=_normalise_pem(cert),
        want_signed_assertion=os.getenv(
            f"SAML_{prefix}_WANT_SIGNED_ASSERTION", "true"
        ).strip().lower() not in ("0", "false", "no"),
    )


def get_idp(name: str) -> Optional[IdpConfig]:
    name = (name or "").strip().lower()
    if name == "okta":
        return _idp_from_env("OKTA", "okta")
    if name in ("azure", "entra", "azuread"):
        return _idp_from_env("AZURE", "azure")
    return None


def configured_idps() -> List[str]:
    out = []
    for n in ("okta", "azure"):
        if get_idp(n) is not None:
            out.append(n)
    return out


def is_saml_configured() -> bool:
    return bool(configured_idps())


# ─── Service Provider settings ──────────────────────────────────────────

def sp_entity_id() -> str:
    base = os.getenv("SAML_SP_BASE_URL", "").strip().rstrip("/")
    explicit = os.getenv("SAML_SP_ENTITY_ID", "").strip().rstrip("/")
    if explicit:
        return explicit
    if base:
        return base
    raise SamlError("SAML_SP_BASE_URL is not configured")


def sp_acs_url() -> str:
    base = os.getenv("SAML_SP_BASE_URL", "").strip().rstrip("/")
    if not base:
        raise SamlError("SAML_SP_BASE_URL is not configured")
    path = os.getenv("SAML_SP_ACS_PATH", "/auth/saml/callback").strip()
    if not path.startswith("/"):
        path = "/" + path
    return base + path


# ─── AuthnRequest (HTTP-Redirect binding) ───────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_authn_request(idp: IdpConfig, *, relay_state: Optional[str] = None,
                        force_authn: bool = False) -> Tuple[str, str]:
    """Return ``(redirect_url, request_id)`` for ``idp``.

    The caller should respond with HTTP 302 → ``redirect_url`` and remember
    ``request_id`` if a callback-side InResponseTo check is desired.
    """
    request_id = "_" + uuid.uuid4().hex
    issue_instant = _now_iso()
    issuer = sp_entity_id()
    acs = sp_acs_url()
    force_attr = ' ForceAuthn="true"' if force_authn else ""
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="{_NS["samlp"]}" '
        f'xmlns:saml="{_NS["saml"]}" '
        f'ID="{request_id}" Version="2.0" IssueInstant="{issue_instant}" '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'AssertionConsumerServiceURL="{acs}" '
        f'Destination="{idp.sso_url}"{force_attr}>'
        f'<saml:Issuer>{issuer}</saml:Issuer>'
        f'<samlp:NameIDPolicy '
        f'Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress" '
        f'AllowCreate="true"/>'
        f'</samlp:AuthnRequest>'
    )
    deflated = zlib.compress(xml.encode("utf-8"))[2:-4]  # raw DEFLATE
    encoded = base64.b64encode(deflated).decode("ascii")
    params = {"SAMLRequest": encoded}
    if relay_state:
        params["RelayState"] = relay_state
    sep = "&" if "?" in idp.sso_url else "?"
    return idp.sso_url + sep + urlencode(params), request_id


# ─── SAMLResponse verification (HTTP-POST binding) ──────────────────────

def _parse_saml_datetime(s: str) -> datetime:
    # SAML uses ISO 8601 with optional fractional seconds; tolerate "Z".
    s = (s or "").strip()
    if not s:
        raise SamlError("missing SAML datetime")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except ValueError as exc:
        raise SamlError(f"invalid SAML datetime: {s}") from exc


def _xpath_one(node, path: str):
    found = node.xpath(path, namespaces=_NS)
    return found[0] if found else None


def _xpath_all(node, path: str):
    return node.xpath(path, namespaces=_NS)


def parse_and_verify_response(
    saml_response_b64: str,
    idp: IdpConfig,
    *,
    expected_request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Decode + verify a SAMLResponse and return a normalised claims dict.

    Returned shape::

        {
            "name_id": "...",
            "email": "...",
            "issuer": "...",
            "session_index": "...",
            "attributes": {"email": [...], "displayName": [...], ...},
        }

    Raises :class:`SamlError` on any failure.
    """
    if not saml_response_b64:
        raise SamlError("empty SAMLResponse")
    try:
        xml_bytes = base64.b64decode(saml_response_b64, validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SamlError("SAMLResponse is not valid base64") from exc

    fromstring = _import_defusedxml()
    XMLVerifier = _import_signxml()
    etree = _import_lxml()

    try:
        # defusedxml protects against XXE / billion-laughs.
        root = fromstring(xml_bytes, forbid_dtd=True, forbid_entities=True,
                          forbid_external=True)
    except Exception as exc:  # noqa: BLE001
        raise SamlError("malformed SAML XML") from exc

    if root.tag != f'{{{_NS["samlp"]}}}Response':
        raise SamlError("not a samlp:Response element")

    # 1. Status = Success.
    status_code = _xpath_one(root, "samlp:Status/samlp:StatusCode")
    if status_code is None:
        raise SamlError("missing Status/StatusCode")
    if status_code.get("Value") != "urn:oasis:names:tc:SAML:2.0:status:Success":
        raise SamlError(f"non-success status: {status_code.get('Value')}")

    # 2. Issuer = configured IdP entity ID.
    response_issuer = _xpath_one(root, "saml:Issuer")
    if response_issuer is None or (response_issuer.text or "").strip() != idp.entity_id:
        # Some IdPs only put Issuer on the Assertion. Re-check below.
        pass

    # 3. Signature verification (Response *or* Assertion).
    verifier = XMLVerifier()
    try:
        verified = verifier.verify(
            etree.tostring(root),
            x509_cert=idp.x509_cert_pem,
            require_x509=True,
            ignore_ambiguous_key_info=True,
        )
        verified_root = verified.signed_xml if hasattr(verified, "signed_xml") else verified
    except Exception as exc:  # noqa: BLE001
        logger.warning("SAML signature verification failed: %s", exc)
        raise SamlError("invalid SAML signature") from exc

    # signxml may return either the full Response or just the signed
    # Assertion. Walk to find the Assertion element either way.
    if verified_root.tag == f'{{{_NS["saml"]}}}Assertion':
        assertion = verified_root
    else:
        assertion = _xpath_one(verified_root, "saml:Assertion")
    if assertion is None:
        # Fall back to original parsed tree (some IdPs sign Response only).
        assertion = _xpath_one(root, "saml:Assertion")
    if assertion is None:
        raise SamlError("missing saml:Assertion")

    # 4. Assertion issuer.
    a_issuer = _xpath_one(assertion, "saml:Issuer")
    if a_issuer is None or (a_issuer.text or "").strip() != idp.entity_id:
        raise SamlError("assertion issuer mismatch")

    # 5. Conditions: NotBefore / NotOnOrAfter / AudienceRestriction.
    conditions = _xpath_one(assertion, "saml:Conditions")
    now = datetime.now(tz=timezone.utc)
    skew = timedelta(seconds=_CLOCK_SKEW_S)
    if conditions is not None:
        nb = conditions.get("NotBefore")
        nooa = conditions.get("NotOnOrAfter")
        if nb and now + skew < _parse_saml_datetime(nb):
            raise SamlError("assertion not yet valid")
        if nooa and now - skew >= _parse_saml_datetime(nooa):
            raise SamlError("assertion expired")
        audiences = [
            (a.text or "").strip()
            for a in _xpath_all(conditions, "saml:AudienceRestriction/saml:Audience")
        ]
        if audiences and sp_entity_id() not in audiences:
            raise SamlError("audience restriction does not match SP entity id")

    # 6. SubjectConfirmation: Recipient + (optional) InResponseTo.
    scd = _xpath_one(
        assertion, "saml:Subject/saml:SubjectConfirmation/saml:SubjectConfirmationData"
    )
    if scd is not None:
        recipient = scd.get("Recipient")
        if recipient and recipient != sp_acs_url():
            raise SamlError("SubjectConfirmation recipient mismatch")
        nooa = scd.get("NotOnOrAfter")
        if nooa and now - skew >= _parse_saml_datetime(nooa):
            raise SamlError("subject confirmation expired")
        irt = scd.get("InResponseTo")
        if expected_request_id and irt and irt != expected_request_id:
            raise SamlError("InResponseTo does not match request id")

    # 7. NameID + attribute extraction.
    name_id_el = _xpath_one(assertion, "saml:Subject/saml:NameID")
    name_id = (name_id_el.text or "").strip() if name_id_el is not None else ""

    attrs: Dict[str, List[str]] = {}
    for attr in _xpath_all(assertion, "saml:AttributeStatement/saml:Attribute"):
        key = attr.get("FriendlyName") or attr.get("Name") or ""
        if not key:
            continue
        values = [
            (v.text or "").strip()
            for v in _xpath_all(attr, "saml:AttributeValue")
            if v is not None and v.text is not None
        ]
        attrs[key] = values

    # 8. Resolve email: prefer explicit attributes, fall back to NameID
    #    when the format is emailAddress.
    email = ""
    for k in ("email", "emailaddress", "Email", "mail",
              "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"):
        if k in attrs and attrs[k]:
            email = attrs[k][0]
            break
    if not email and name_id_el is not None:
        fmt = name_id_el.get("Format", "")
        if fmt.endswith(":emailAddress") or "@" in name_id:
            email = name_id
    email = email.strip().lower()
    if not email:
        raise SamlError("assertion has no email attribute")

    session_index = ""
    authn = _xpath_one(assertion, "saml:AuthnStatement")
    if authn is not None:
        session_index = authn.get("SessionIndex", "") or ""

    return {
        "name_id": name_id,
        "email": email,
        "issuer": idp.entity_id,
        "session_index": session_index,
        "attributes": attrs,
    }


# ─── SP metadata (for IdP onboarding) ───────────────────────────────────

def sp_metadata_xml() -> str:
    """Return a minimal SP metadata XML document.

    IdP admins can paste this into Okta/Azure to register the SP. We do
    not embed an SP signing certificate (no SP-side signing in this MVP).
    """
    entity = sp_entity_id()
    acs = sp_acs_url()
    valid_until = (datetime.now(tz=timezone.utc) + timedelta(days=365)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata" '
        f'entityID="{entity}" validUntil="{valid_until}">'
        f'<md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        f'protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">'
        f'<md:NameIDFormat>'
        f'urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress'
        f'</md:NameIDFormat>'
        f'<md:AssertionConsumerService '
        f'Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" '
        f'Location="{acs}" index="0" isDefault="true"/>'
        f'</md:SPSSODescriptor>'
        f'</md:EntityDescriptor>'
    )


def make_relay_state() -> str:
    """Cryptographically random RelayState (URL-safe, 32 bytes → 43 chars)."""
    return secrets.token_urlsafe(32)


__all__ = [
    "SamlError",
    "IdpConfig",
    "get_idp",
    "configured_idps",
    "is_saml_configured",
    "sp_entity_id",
    "sp_acs_url",
    "sp_metadata_xml",
    "build_authn_request",
    "parse_and_verify_response",
    "make_relay_state",
]
