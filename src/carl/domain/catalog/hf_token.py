"""The Hugging Face access token: what a token looks like, how it is shown, which hosts may get it, and the texts that
say a repo is gated. Pure: where the token is kept is the SecretStore port (ports/secrets.py), and the rules of
which source wins are in application/hub_token.py.

A gated repo answers 401 (or 403) to a request without the token, and its file list answers other SHA-256s (lfs.oid)
without the token than with it: only the token's match the file that is served (decision A75). So every Hugging Face
call carries the token when there is one.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from ..shared.errors import ConfigError

TOKEN_ENV = "HF_TOKEN"                      # the environment variable of the Hugging Face tools; it wins
SOURCE_ENV = "environment"                  # TokenStatus.source: HF_TOKEN in the environment
SOURCE_CARL = "carl"                        # the token `carl hf token set` keeps
SOURCE_SHARED = "huggingface"               # the file of the Hugging Face tools (~/.cache/huggingface/token)
SOURCE_NONE = ""
DENIED_EXIT = 401                           # the Downloader's answer to a 401 or 403 (no process exits with 401)
# A token: letters, digits and - _ . ~ + / = only (hf_ and 34 letters and digits today); no space, no quote.
_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{8,512}")
_HOSTS = ("huggingface.co", "hf.co")
SHOWN_CHARS = 4                             # the characters of the end that a status shows


class TokenStatus(NamedTuple):
    """Which token a call uses: source (SOURCE_*; SOURCE_NONE: none), its last characters, and where CARL keeps it
    (the store's words, for SOURCE_CARL; "" otherwise)."""
    source: str
    last4: str
    where: str = ""

    @property
    def is_set(self) -> bool:
        return self.source != SOURCE_NONE


def is_token(text: str) -> bool:
    """text has the form of a token."""
    return bool(_TOKEN.fullmatch(text))


def validate_token(text: str) -> str:
    """The token in text (surrounding whitespace dropped). The message never repeats what was typed."""
    token = text.strip()
    if not is_token(token):
        raise ConfigError("that is not a Hugging Face token. A token has 8 to 512 letters, digits and - _ . ~ + / = "
                          "(no space). Make one on https://huggingface.co/settings/tokens.")
    return token


def last_chars(token: str) -> str:
    """The end of a token, the only part a status shows."""
    return token[-SHOWN_CHARS:] if token else ""


def is_hf_host(host: str) -> bool:
    """host (no port) is huggingface.co, hf.co or a host below one of them: the hosts that get the token."""
    h = host.lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in _HOSTS)


def redact(text: str, token: str) -> str:
    """text without the token (a message that came from outside might repeat a header)."""
    return text.replace(token, "<token>") if token and len(token) >= 8 else text


def denied_text(repo: str, code: int, has_token: bool, command: str) -> str:
    """What a 401 or 403 means for a repo, and what to do (the repo can be "")."""
    page = f"https://huggingface.co/{repo}" if repo else "https://huggingface.co"
    what = f"Hugging Face refused the request ({code})" + (f" for {repo}." if repo else ".")
    if has_token:
        return (f"{what} The repo is gated or private, and your token is not enough (or Hugging Face does not "
                f"accept it). Accept the terms of the repo on its page ({page}), and check that your token is "
                "valid and may read it.")
    return (f"{what} The repo is gated or private. Accept its terms on its page ({page}), then give CARL your "
            f"access token: {command} hf token set, or Settings > Models in the dashboard (or set HF_TOKEN).")
