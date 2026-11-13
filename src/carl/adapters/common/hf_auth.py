"""The Hugging Face token on the wire (decision A75): the Authorization header of every call to Hugging Face, and
where it may go.

The token goes to huggingface.co and the hosts below it (and hf.co), over https, and to no other host. A redirect to
another host drops it: the download hosts answer with a signed address that needs no token (and refuses a request that
has two ways to sign in). The token is read through `token()` at each call, so a token that was set or cleared a
moment ago counts at once. Nothing here logs, prints or puts the token in a URL or on a command line.
"""
from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from ...domain.catalog.hf_token import is_hf_host

USER_AGENT = "carl"
MAX_HOPS = 5
REDIRECTS = (301, 302, 303, 307, 308)


class _SameHostOnly(urllib.request.HTTPRedirectHandler):
    """A redirect keeps the Authorization header only when it stays on the same scheme and host."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and _origin(newurl) != _origin(req.full_url):
            new.remove_header("Authorization")
        return new


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an answer to read, not one to follow."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        return None


def _origin(url: str) -> Tuple[str, str]:
    p = urllib.parse.urlsplit(url)
    return p.scheme.lower(), p.netloc.lower()


class HfAuth:
    """The calls to Hugging Face with the user's token. token: the token now ("" when there is none); trusted: the
    hosts that may get it (Hugging Face's); secure_only: only over https (a test's local hub sets False)."""

    def __init__(self, token: Callable[[], str], trusted: Callable[[str], bool] = is_hf_host,
                 secure_only: bool = True) -> None:
        self.token = token
        self.trusted = trusted
        self.secure_only = secure_only

    def gets_token(self, url: str) -> bool:
        """The token may go to this address."""
        p = urllib.parse.urlsplit(url)
        return bool(p.hostname) and self.trusted(p.hostname or "") and (p.scheme == "https" or not self.secure_only)

    def headers(self, url: str, extra: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
        """The headers of a call to url: ours, and the Authorization header when the token may go there."""
        out = {"User-Agent": USER_AGENT, **(extra or {})}
        token = self.token() if self.gets_token(url) else ""
        if token:
            out["Authorization"] = f"Bearer {token}"
        return out

    def open(self, url: str, timeout: float, extra: Optional[Mapping[str, str]] = None, method: str = "GET") -> Any:
        """The response of a call (redirects followed; the token stays on the first host). urllib's errors rise."""
        req = urllib.request.Request(url, headers=self.headers(url, extra), method=method)
        return urllib.request.build_opener(_SameHostOnly).open(req, timeout=timeout)

    def resolve(self, url: str, timeout: float) -> Tuple[int, str]:
        """Where a download address leads: (status, address). Hops on the same host are followed with the token; the
        first answer that is not a redirect on it is the result: (200, the address that serves the file), the
        redirect to another host as (302, its address), or an error's status with "" (401 and 403 mean the repo is
        gated or the token is not valid). An unreachable host raises OSError."""
        opener = urllib.request.build_opener(_NoRedirect)
        for _ in range(MAX_HOPS):
            req = urllib.request.Request(url, headers=self.headers(url), method="HEAD")
            try:
                with opener.open(req, timeout=timeout) as r:
                    return r.status, url
            except urllib.error.HTTPError as e:
                where = e.headers.get("Location", "") if e.headers else ""
                e.close()
                if e.code in REDIRECTS and where:
                    nxt = urllib.parse.urljoin(url, where)
                    if _origin(nxt) != _origin(url):
                        return e.code, nxt
                    url = nxt
                    continue
                return e.code, ""
        return 310, ""
