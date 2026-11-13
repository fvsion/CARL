"""The Hugging Face HTTP API (implements HubClient). Read-only, TLS verified, with the user's access token when there is
one (decision A75: a gated repo answers 401 without it, and its file list holds other SHA-256s without it)."""
from __future__ import annotations

import json
import urllib.error

from ...domain.catalog.hf import HF_BASE
from ...domain.shared.errors import ConfigError, HubDenied
from ...domain.shared.types import JsonValue
from .hf_auth import HfAuth

API_TIMEOUT = 30


class HfHttpClient:
    """auth: the calls with the token (HfAuth); base: the API's host (Hugging Face; a test's local hub)."""

    def __init__(self, auth: HfAuth, base: str = HF_BASE) -> None:
        self.auth = auth
        self.base = base

    def get(self, api_path: str) -> JsonValue:
        """GET <base>/api/<api_path> (built by domain.hf from validated names). A 401 or 403 raises HubDenied."""
        shown = api_path.split("?")[0]
        try:
            with self.auth.open(f"{self.base}/api/{api_path}", API_TIMEOUT) as r:
                data: JsonValue = json.load(r)
                return data
        except urllib.error.HTTPError as e:
            e.close()
            if e.code in (401, 403):
                raise HubDenied(e.code, shown) from None
            raise ConfigError(f"Hugging Face: HTTP {e.code} for {shown}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ConfigError(f"Hugging Face is not reachable: {getattr(e, 'reason', e)}") from None
        except ValueError:
            raise ConfigError("Hugging Face: the answer is not JSON") from None
