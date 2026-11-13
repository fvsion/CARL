"""The dashboard API on the server over HTTP (the DashboardApi port): /carl/ping without the key, then the client
routes with it (Bearer) and the client's report (X-Carl-Client). urllib only."""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any, Iterator, Mapping, Optional

from ..domain import remote
from ..domain.remote import MAX_CONFIG, VERSION_TEXT_RE, check_config
from . import package_data

READ_TIMEOUT = 75            # the dashboard sends a comment every 25 s: silence this long = reconnect
MAX_LINE = 1 << 16           # one line of the event stream
# What can go wrong when the dashboard is away or answers badly (the service then tries again).
NET_ERRORS = (OSError, ValueError, http.client.HTTPException)


def describe(e: BaseException) -> str:
    """An error for the state file (the /carl panel shows it): what went wrong and what to do."""
    if isinstance(e, urllib.error.HTTPError) and e.code in (401, 403):
        return remote.refused_text(package_data.server_words()["server"])
    if isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError):
        return remote.unreachable_text(e.reason)
    return str(e)[:200] or type(e).__name__


class DashboardHttp:
    """One client's view of the dashboard API; notes the server's CARL version from each reply."""

    def __init__(self) -> None:
        self.version = ""                   # the server's CARL version from the last reply (X-Carl-Version)

    @staticmethod
    def request(url: str, key: str, who: str, etag: str = "") -> urllib.request.Request:
        headers = {"Authorization": f"Bearer {key}", "X-Carl-Client": who}
        if etag:
            headers["If-None-Match"] = f'"{etag}"'
        return urllib.request.Request(url, headers=headers)

    def ping(self, api: str, timeout: float = 2.0) -> Optional[str]:
        """What answers /carl/ping (no key: Phase 23.5): CARL's ID; "" for an older CARL dashboard (no ping, but
        CARL's X-Carl-Version on its 401); None for anything else (another program, nothing)."""
        try:
            with urllib.request.urlopen(urllib.request.Request(api + "/carl/ping"), timeout=timeout) as r:
                doc = json.loads(r.read(4096))
            cid = doc.get("carl") if isinstance(doc, dict) else None
            return cid if isinstance(cid, str) else None
        except urllib.error.HTTPError as e:
            return "" if e.code == 401 and (e.headers.get("X-Carl-Version") or "").strip() else None
        except (OSError, ValueError, http.client.HTTPException):
            return None

    def server(self, api: str, key: str, who: str) -> Optional[Any]:
        """/carl/client/server (the server's port now), parsed; None when the dashboard is older (an HTTP error)."""
        try:
            with urllib.request.urlopen(self.request(api + "/carl/client/server", key, who), timeout=10) as r:
                return json.loads(r.read(4096))
        except urllib.error.HTTPError:
            return None

    def _note_version(self, headers: Any) -> None:
        v = (headers.get("X-Carl-Version") or "").strip() if headers is not None else ""
        if VERSION_TEXT_RE.fullmatch(v):
            self.version = v

    def server_version(self) -> str:
        return self.version

    @staticmethod
    def describe(e: BaseException) -> str:
        return describe(e)

    @staticmethod
    def no_api(api: str) -> Exception:
        """The error when no CARL dashboard answers at api or the next ports (an unreachable server)."""
        return urllib.error.URLError(remote.no_api_text(api))

    def config(self, api: str, key: str, who: str, etag: str) -> Optional[Mapping[str, Any]]:
        """The published config when it isn't `etag`; None when it is (304). Notes the server's CARL version."""
        try:
            with urllib.request.urlopen(self.request(api + "/carl/client/config", key, who, etag), timeout=10) as r:
                self._note_version(r.headers)
                body = r.read(MAX_CONFIG + 1)
        except urllib.error.HTTPError as e:
            if e.code == 304:
                self._note_version(e.headers)
                return None
            raise
        if len(body) > MAX_CONFIG:
            raise ValueError("The client config from the server is too large.")
        return check_config(json.loads(body))

    def events(self, api: str, key: str, who: str) -> Iterator[str]:
        """The event names the dashboard sends (a blank name for its keep-alive comments)."""
        with urllib.request.urlopen(self.request(api + "/carl/client/events", key, who), timeout=READ_TIMEOUT) as r:
            name = ""
            while True:
                raw = r.readline(MAX_LINE)
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith(":"):
                    yield ""
                elif not line and name:
                    yield name
                    name = ""
