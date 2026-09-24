"""Validation for user-supplied crawl targets and SSRF-sensitive URLs."""

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = {"http", "https"}
_BLOCKED_HOSTS = {
    "localhost", "localhost.localdomain", "0.0.0.0", "metadata.google.internal",
}


def _blocked_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


def validate_crawl_url(url: str) -> str:
    value = (url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
        raise ValueError("Only http and https URLs are allowed")
    if parsed.username or parsed.password:
        raise ValueError("URLs containing credentials are not allowed")
    host = parsed.hostname
    if not host:
        raise ValueError("URL must contain a hostname")
    host = host.rstrip(".").lower()
    if host in _BLOCKED_HOSTS or _blocked_address(host):
        raise ValueError("Private, loopback, link-local, or metadata hosts are blocked")
    try:
        addresses = {
            result[4][0]
            for result in socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        }
    except socket.gaierror as exc:
        raise ValueError(f"Hostname cannot be resolved: {host}") from exc
    if any(_blocked_address(address) for address in addresses):
        raise ValueError("Hostname resolves to a private or reserved address")
    return value
