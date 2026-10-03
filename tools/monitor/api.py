"""HTTP to the llama.cpp server (read-only GETs)."""
from __future__ import annotations

import http.client
import urllib.request
from dataclasses import dataclass
from typing import Dict

# What a GET can fail with: no server or a timeout (OSError, which URLError and HTTPError
# are), a broken HTTP exchange, or a body that is not UTF-8 / JSON (ValueError).
FETCH_ERRORS = (OSError, http.client.HTTPException, ValueError)


def auth_headers(key: str) -> Dict[str, str]:
    """The API key as a Bearer header (the server runs with --api-key-file)."""
    return {"Authorization": f"Bearer {key}"} if key else {}


def get(url: str, key: str, timeout: float) -> str:
    """The body of a GET to url. Raises one of FETCH_ERRORS."""
    req = urllib.request.Request(url, headers=auth_headers(key))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body: bytes = r.read()
    return body.decode()


@dataclass
class Endpoint:
    """Where the server is and the key it wants. The host follows the server when it
    listens on another address; the port changes when a start moves the server to another port."""
    host: str
    port: int
    key: str

    @property
    def base(self) -> str:
        """The server's URL without a path."""
        return f"http://{self.host}:{self.port}"

    def get(self, path: str, timeout: float = 2) -> str:
        """The body of GET path. Raises one of FETCH_ERRORS."""
        return get(self.base + path, self.key, timeout)


def health_ok(host: str, port: int, key: str, timeout: float = 2) -> bool:
    """True if http://host:port/health answers."""
    try:
        get(f"http://{host}:{port}/health", key, timeout)
        return True
    except FETCH_ERRORS:
        return False
