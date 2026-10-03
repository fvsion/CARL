"""Resumable downloads with aria2c (16 connections), or curl when aria2c is missing
(implements Downloader). Both get argument lists: the URL and file name are never parsed
by a shell."""
from __future__ import annotations

import os
import shutil
import subprocess


class Aria2OrCurl:
    def fetch(self, url: str, directory: str, file_name: str) -> int:
        if os.path.basename(file_name) != file_name:
            raise ValueError(f"not a file name: {file_name!r}")
        if shutil.which("aria2c"):
            return subprocess.call(["aria2c", "-x16", "-s16", "-k1M", "--continue=true", "--file-allocation=none",
                                    "--summary-interval=30", "--console-log-level=warn",
                                    "-d", directory, "-o", file_name, url])
        return subprocess.call(["curl", "-fL", "-C", "-", "--progress-bar", "-o", os.path.join(directory, file_name), url])
