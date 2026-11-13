"""The llama-server HTTP API, as CARL calls it (one client, with the server's key)."""
from __future__ import annotations

from typing import Mapping, Protocol

from ..domain.shared.types import JsonObject, JsonValue


class LlamaServerApi(Protocol):
    """The API of a running llama-server. Contract C-031."""

    def get(self, path: str, timeout: float) -> JsonValue:
        """The JSON answer of a GET. Raises OSError when the server can't be reached."""
        ...

    def post(self, path: str, body: JsonObject, timeout: float) -> JsonValue:
        """The JSON answer of a POST. Raises OSError when the server can't be reached."""
        ...

    def healthy(self) -> bool:
        """True when /health says ok (False while the model loads)."""
        ...


class ServerEndpoint(Protocol):
    """Where the server the dashboard watches is, the key it wants, and its API's answers as text (/metrics is not
    JSON). The host and the port change when the server moves; the key when its file changes. Contract C-052."""

    host: str
    port: int
    key: str

    @property
    def base(self) -> str:
        """The server's URL without a path."""
        ...

    def get(self, path: str, timeout: float = 2) -> str:
        """The body of GET path. Raises OSError when the server can't be reached or its answer is cut off, ValueError
        when the URL can't be used."""
        ...

    def post(self, path: str, body: Mapping[str, object], timeout: float = 10) -> str:
        """The body of a JSON POST to path (a slot's save, a router's load): the same errors as get."""
        ...
