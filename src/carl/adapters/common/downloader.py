"""Resumable downloads with aria2c (16 connections), or curl when aria2c is missing
(implements Downloader). Both get argument lists: the URL and file name are never parsed
by a shell. HubDownloader adds the Hugging Face token (decision A75)."""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error

from ...domain.catalog.hf_token import DENIED_EXIT
from ...ports import Downloader
from .hf_auth import HfAuth


class Aria2OrCurl:
    def fetch(self, url: str, directory: str, file_name: str) -> int:
        if os.path.basename(file_name) != file_name:
            raise ValueError(f"not a file name: {file_name!r}")
        if shutil.which("aria2c"):
            return subprocess.call(["aria2c", "-x16", "-s16", "-k1M", "--continue=true", "--file-allocation=none",
                                    "--summary-interval=30", "--console-log-level=warn",
                                    "-d", directory, "-o", file_name, url])
        return subprocess.call(["curl", "-fL", "-C", "-", "--progress-bar", "-o", os.path.join(directory, file_name), url])


PROBE_TIMEOUT = 30
CHUNK = 1 << 20
TRIES = 3                                   # a signed address that ran out is asked for again


class HubDownloader:
    """Downloads from Hugging Face with the user's token (implements Downloader, contract C-008, decision A75).

    aria2c and curl get the address on their command line, so the token can never go to them: a process list would
    show it. This class asks Hugging Face for the file itself, with the token in a header. Hugging Face answers a
    request for a file with a redirect to a signed address of a download host, and that address needs no token: the
    resumable download (the inner Downloader) gets the signed address. A signed address that ran out in the middle of
    a long download is asked for again (the part file is resumed). A file that Hugging Face serves itself is read here
    with the Range header. A 401 or 403 is the exit code DENIED_EXIT: the repo is gated or the token is not valid.
    An address that is not Hugging Face's goes to the inner Downloader as it is, and so does a download with no
    token that Hugging Face does not refuse."""

    def __init__(self, inner: Downloader, auth: HfAuth) -> None:
        self.inner = inner
        self.auth = auth

    def fetch(self, url: str, directory: str, file_name: str) -> int:
        if os.path.basename(file_name) != file_name:
            raise ValueError(f"not a file name: {file_name!r}")
        if not self.auth.gets_token(url):
            return self.inner.fetch(url, directory, file_name)
        has_token = bool(self.auth.token())
        rc = 1
        for _ in range(TRIES if has_token else 1):
            try:
                code, where = self.auth.resolve(url, PROBE_TIMEOUT)
            except OSError:
                return self.inner.fetch(url, directory, file_name)      # not reachable now: the inner one says so
            if code in (401, 403):
                return DENIED_EXIT
            if not has_token or (code != 200 and not where):
                return self.inner.fetch(url, directory, file_name)      # public, or an answer to leave to the inner one
            rc = self._direct(url, directory, file_name) if code == 200 else self.inner.fetch(where, directory,
                                                                                            file_name)
            if rc == 0 or rc == DENIED_EXIT:
                return rc
        return rc

    def _direct(self, url: str, directory: str, file_name: str) -> int:
        """A file that Hugging Face serves itself, read with the token (a Range request resumes a part file)."""
        path = os.path.join(directory, file_name)
        have = os.path.getsize(path) if os.path.isfile(path) else 0
        extra = {"Range": f"bytes={have}-"} if have else None
        try:
            with self.auth.open(url, PROBE_TIMEOUT, extra) as r:
                partial = getattr(r, "status", 200) == 206
                with open(path, "ab" if partial else "wb") as f:
                    while True:
                        block = r.read(CHUNK)
                        if not block:
                            break
                        f.write(block)
        except urllib.error.HTTPError as e:
            e.close()
            if e.code == 416:                       # the file is complete already
                return 0
            return DENIED_EXIT if e.code in (401, 403) else 1
        except OSError:
            return 1
        return 0
