"""Prompt-injection exfiltration guard helpers.

This module is intentionally small and regex/path based. It is a defense-in-
depth layer under the model prompt: content from web pages, PDFs, issues, or
screenshots must not be able to trick the agent into reading credential files
or sending secret-looking payloads to external destinations.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

from agent.redact import redact_sensitive_text


_SENSITIVE_BASENAMES = {
    ".env",
    ".env.local",
    ".envrc",
    ".netrc",
    ".pgpass",
    ".npmrc",
    ".pypirc",
    "auth.json",
    "credentials.json",
    "service-account.json",
    "service_account.json",
    "client_secret.json",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

_SENSITIVE_SUFFIXES = (
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".kdbx",
)

_SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[._-])(?:secret|secrets|credential|credentials|token|tokens|private[_-]?key)(?:[._-]|$)",
    re.IGNORECASE,
)

# Shell command detector for reading credential material and piping/posting it
# outward. It is deliberately conservative and anchored to common exfil paths;
# sensitive write protection already lives in tools.approval.
_SENSITIVE_PATH_FRAGMENT = (
    r"(?:~|\$HOME|\$\{HOME\}|/root|/home/[^\s/'\"`]+)?/\.hermes/(?:\.env|auth\.json)\b|"
    r"(?:~|\$HOME|\$\{HOME\}|/root|/home/[^\s/'\"`]+)?/\.ssh/(?:id_rsa|id_dsa|id_ecdsa|id_ed25519|[^\s/'\"`]+\.pem)\b|"
    r"(?:^|\s|[\"'`])\.env(?:\.[^\s/'\"`]*)?\b|"
    r"credentials?\.json\b|client_secret\.json\b|service[_-]?account\.json\b|"
    r"[^\s/'\"`]+\.(?:pem|key|p12|pfx|kdbx)\b"
)

_SENSITIVE_READ_CMD_RE = re.compile(
    rf"\b(?:cat|less|more|tail|head|sed|awk|grep|rg|python[23]?|node|perl|ruby|base64|xxd|openssl)\b[^\n;&|]*({_SENSITIVE_PATH_FRAGMENT})",
    re.IGNORECASE,
)

_NETWORK_EGRESS_RE = re.compile(
    r"\b(?:curl|wget|nc|ncat|netcat|socat|scp|rsync|ftp|sftp|ssh)\b|"
    r"https?://|wss?://|\brequests\.(?:post|put|get)\b|\burllib\.request\b",
    re.IGNORECASE,
)


def _normalize_path_string(path: str) -> str:
    text = unquote(str(path or "")).replace("\\", "/")
    try:
        text = os.path.expandvars(os.path.expanduser(text))
    except Exception:
        pass
    return text


def is_sensitive_path(path: str | os.PathLike[str]) -> bool:
    """Return True when a path points at credential/secret-bearing material."""
    raw = _normalize_path_string(str(path))
    if not raw:
        return False
    lower = raw.lower()
    p = Path(raw)
    name = p.name.lower()
    parts = [part.lower() for part in p.parts]

    if name in _SENSITIVE_BASENAMES:
        return True
    if name.startswith(".env."):
        return True
    if name.endswith(_SENSITIVE_SUFFIXES):
        return True
    if ".ssh" in parts and name.startswith("id_"):
        return True
    if ".hermes" in parts and name in {".env", "auth.json"}:
        return True
    if ".config" in parts and name in {"credentials.json", "token.json", "auth.json"}:
        return True
    if _SENSITIVE_NAME_RE.search(name):
        return True
    # Browser cookie/session stores are common indirect credential containers.
    if name in {"cookies", "cookies.sqlite", "login data", "web data"}:
        return True
    if "/browser/" in lower and ("cookie" in lower or "session" in lower):
        return True
    return False


def sensitive_path_block_message(path: str | os.PathLike[str]) -> str:
    return (
        f"BLOCKED: refusing to read sensitive credential path '{path}'. "
        "External content can contain prompt injection, so credential files "
        "(.env, auth.json, SSH keys, cookies, service-account files, etc.) "
        "are not exposed through file tools. Use purpose-built config/status "
        "commands or inspect it manually outside the agent if absolutely needed."
    )


def message_contains_unredacted_secret(text: str) -> bool:
    """Detect whether text contains secret-looking material redaction would mask."""
    if not text:
        return False
    return redact_sensitive_text(text, force=True) != text


def egress_block_message(surface: str = "outbound message") -> str:
    return (
        f"Blocked: {surface} contains secret-looking material (BLOCKED). "
        "This may be prompt-injection-driven exfiltration. Redact the secret "
        "or ask Tim explicitly before moving credentials across a boundary."
    )


def detect_sensitive_terminal_exfil(command: str) -> str | None:
    """Return a description when a shell command appears to exfiltrate secrets.

    Blocks two high-risk shapes:
    - read/encode a known credential path and send it to a network sink
    - curl/wget/scp/etc. command line contains raw secret-looking material
    """
    cmd = command or ""
    decoded = unquote(cmd)
    if _SENSITIVE_READ_CMD_RE.search(decoded) and _NETWORK_EGRESS_RE.search(decoded):
        return "read sensitive credential file and send it to a network/external sink"
    if _NETWORK_EGRESS_RE.search(decoded) and message_contains_unredacted_secret(decoded):
        return "network/external command contains secret-looking material"
    return None
