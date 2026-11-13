"""The user's Hugging Face access token (decision A75): which token a call uses, and setting and clearing CARL's own.

Three places can hold a token, in this order: HF_TOKEN in the environment, the token CARL keeps in the system's secret
store (`carl hf token set`, the Settings > Models row), and the file of the Hugging Face tools
(~/.cache/huggingface/token) when CARL has none. The environment and that file are the user's own: CARL reads them and
never changes them. The token is never in config.json, a log, an error text, a URL or a command line. Contract C-136.
"""
from __future__ import annotations

import time
from typing import Callable, NamedTuple, Tuple

from ..domain.catalog.hf_token import (SOURCE_CARL, SOURCE_ENV, SOURCE_NONE, SOURCE_SHARED, TokenStatus, is_token,
                                       last_chars, validate_token)
from ..domain.shared.contracts import ensure
from ..ports import SecretStore

STORE_TTL = 5.0                     # seconds a read of the store stands (another program may have changed it)


class Cleared(NamedTuple):
    """The result of clear: whether CARL had a token of its own, the token a call uses now (none: SOURCE_NONE), and
    the place CARL kept its own."""
    had: bool
    now: TokenStatus
    where: str


class HubToken:
    """env: HF_TOKEN now ("" when it is not set); shared: the token of the Hugging Face tools' file ("" when there is
    none); store: where CARL keeps its own."""

    def __init__(self, store: SecretStore, env: Callable[[], str], shared: Callable[[], str],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.store = store
        self.env = env
        self.shared = shared
        self.clock = clock
        self._kept = ("", -STORE_TTL)               # (the store's token, when it was read)

    def _own(self) -> str:
        """The token CARL keeps (read from the store at most every STORE_TTL seconds)."""
        now = self.clock()
        if now - self._kept[1] >= STORE_TTL or now < self._kept[1]:
            self._kept = (self.store.read().strip(), now)
        return self._kept[0] if is_token(self._kept[0]) else ""

    def _pick(self) -> Tuple[str, str]:
        for source, read in ((SOURCE_ENV, lambda: self.env().strip()), (SOURCE_CARL, self._own),
                             (SOURCE_SHARED, lambda: self.shared().strip())):
            token = read()
            if is_token(token):
                return source, token
        return SOURCE_NONE, ""

    def current(self) -> str:
        """The token a call to Hugging Face carries ("" when there is none)."""
        return self._pick()[1]

    def status(self) -> TokenStatus:
        """Where the token comes from and its last characters; where the store keeps CARL's own."""
        source, token = self._pick()
        return TokenStatus(source, last_chars(token), self.store.where() if source == SOURCE_CARL else "")

    def set(self, text: str) -> str:
        """Keep a token (what the user typed or piped; surrounding whitespace is dropped); where it is kept. Raises
        ConfigError when text is no token and SecretStoreError when the store refuses it."""
        token = validate_token(text)
        self.store.write(token)
        self._kept = (token, self.clock())
        ensure(self._own() == token, "C-136", "the token that was set is the one CARL reads")
        return self.store.where()

    def clear(self) -> Cleared:
        """Remove the token CARL keeps. HF_TOKEN and the Hugging Face tools' file stay: they are not CARL's."""
        had = bool(self._own())
        where = self.store.where()
        self.store.remove()
        self._kept = ("", self.clock())
        ensure(not self.store.read(), "C-136", "no token of CARL's own is left after clear")
        return Cleared(had, self.status(), where)
