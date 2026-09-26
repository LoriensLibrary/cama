"""Signing keys and outbound address policy for webhook delivery."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import socket

import httpx


class WebhookConfigurationError(ValueError):
    """The operator has not configured webhook authority."""


def signing_secret(nonce: str) -> str:
    """Derive a subscription key without storing it in the database."""
    master = os.environ.get("CAMA_WEBHOOK_MASTER_SECRET", "")
    if len(master.encode("utf-8")) < 32:
        raise WebhookConfigurationError("Configure CAMA_WEBHOOK_MASTER_SECRET with at least 32 random bytes.")
    return hmac.new(master.encode("utf-8"), ("cama-webhook-v1:" + nonce).encode("ascii"), hashlib.sha256).hexdigest()


def checked_url(value: str) -> httpx.URL:
    """Require an exact operator-allowed hostname and standard HTTPS port."""
    if not isinstance(value, str) or len(value) > 2048 or any(ord(c) <= 32 for c in value):
        raise ValueError("Webhook URL is invalid.")
    try:
        url = httpx.URL(value)
    except httpx.InvalidURL as exc:
        raise ValueError("Webhook URL is invalid.") from exc
    if url.scheme != "https" or not url.host or url.port not in (None, 443) or url.userinfo or url.fragment:
        raise ValueError("Webhook URL must be HTTPS on port 443, without credentials or fragments.")
    allowed = {h.strip().lower() for h in os.environ.get("CAMA_WEBHOOK_ALLOWED_HOSTS", "").split(",") if h.strip()}
    if not allowed:
        raise WebhookConfigurationError("Configure CAMA_WEBHOOK_ALLOWED_HOSTS with exact destination hostnames.")
    if url.raw_host.decode("ascii").lower() not in allowed:
        raise ValueError("Webhook hostname is not allowed by the operator.")
    return url


def public_address(url: httpx.URL) -> str:
    """Validate every DNS answer, returning a numeric address for connection.

    The caller must connect to this address, not resolve the hostname again.
    IPv4-mapped and transition IPv6 addresses are rejected conservatively.
    """
    answers = socket.getaddrinfo(url.raw_host.decode("ascii"), 443, type=socket.SOCK_STREAM)
    addresses = [ipaddress.ip_address(answer[4][0]) for answer in answers]
    if not addresses:
        raise ValueError("Webhook hostname has no addresses.")
    for address in addresses:
        if (not address.is_global or address.is_multicast or address.is_reserved
                or (isinstance(address, ipaddress.IPv6Address)
                    and (address.ipv4_mapped or address.sixtofour or address.teredo))):
            raise ValueError("Webhook destination must resolve only to public unicast addresses.")
    return str(addresses[0])
