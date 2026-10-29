"""The Hugging Face HTTP API (implements HubClient). Read-only, anonymous, TLS verified."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..domain.errors import ConfigError
from ..domain.hf import HF_BASE
from ..domain.types import JsonValue

API_TIMEOUT = 30


class HfHttpClient:
    def get(self, api_path: str) -> JsonValue:
        """GET https://huggingface.co/api/<api_path> (built by domain.hf from validated names)."""
        req = urllib.request.Request(f"{HF_BASE}/api/{api_path}", headers={"User-Agent": "carl"})
        try:
            with urllib.request.urlopen(req, timeout=API_TIMEOUT) as r:
                data: JsonValue = json.load(r)
                return data
        except urllib.error.HTTPError as e:
            raise ConfigError(f"Hugging Face: HTTP {e.code} for {api_path.split('?')[0]}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ConfigError(f"Hugging Face is not reachable: {getattr(e, 'reason', e)}") from None
        except ValueError:
            raise ConfigError("Hugging Face: the answer is not JSON") from None
