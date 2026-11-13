"""The Hugging Face side of the wiring (decision A75): the user's access token and the calls that carry it.

The token is kept in the place the system keeps secrets (SecretStore, contract C-065): the macOS Keychain (security),
the Windows Credential Manager (ctypes), the Secret Service through secret-tool where it is installed; a private file
of mode 0600 in the settings folder on macOS and Linux when that store is not there or refuses. HF_TOKEN in the
environment wins, and a token in the Hugging Face tools' file (HF_HOME/token, else ~/.cache/huggingface/token) is read
when CARL has none. Built when asked (rule R6); one HubToken for each settings folder in a process, so a token that was
set a moment ago counts for every call at once.
"""
from __future__ import annotations

import os
import shutil
from typing import Callable, Dict, Mapping

from ..adapters.common.hf_auth import HfAuth
from ..adapters.common.secret_chain import StoreChain
from ..adapters.common.token_file import PrivateFile
from ..application.hub_token import HubToken
from ..domain.catalog.hf_token import TOKEN_ENV
from ..ports import SecretStore

SERVICE = "carl"                            # the secret's service name in a system store
ACCOUNT = "huggingface-token"               # and its account
TARGET = "CARL/huggingface-token"           # the Windows Credential Manager's target name
LABEL = "CARL Hugging Face token"           # the name a keyring shows
TOKEN_FILE = "hf-token"                     # in the settings folder
SHARED_FOLDER = os.path.join(".cache", "huggingface")

_TOKENS: Dict[str, HubToken] = {}


def token_path(conf_dir: str) -> str:
    return os.path.join(conf_dir, TOKEN_FILE)


def secret_store(os_name: str, conf_dir: str, home: str, which: Callable[[str], str] = lambda p: shutil.which(p) or "",
                 keychain: str = "") -> SecretStore:
    """The store of the token on this OS. which: finds a program (secret-tool); keychain: a Keychain file (the tests
    use their own; "" is the login Keychain)."""
    shown = conf_dir.replace(home, "~", 1) if home and conf_dir.startswith(home) else conf_dir
    file = PrivateFile(token_path(conf_dir), f"the file {shown}/{TOKEN_FILE}")
    if os_name == "windows":                    # the file takes over in a session that has no Credential Manager (ssh)
        from ..adapters.windows.credentials import AclFile, CredentialManager
        return StoreChain([CredentialManager(TARGET, SERVICE), AclFile(token_path(conf_dir), file.label)])
    if os_name == "macos":
        from ..adapters.macos.keychain import Keychain
        return StoreChain([Keychain(SERVICE, ACCOUNT, keychain), file])
    tool = which("secret-tool")
    if tool:
        from ..adapters.linux.secret_tool import SecretTool
        return StoreChain([SecretTool(tool, SERVICE, ACCOUNT, LABEL), file])
    return file


def shared_token_file(env: Mapping[str, str], home: str) -> str:
    """The file the Hugging Face tools keep their token in."""
    return os.path.join(env.get("HF_HOME") or os.path.join(home, SHARED_FOLDER), "token")


def hub_token(env: Mapping[str, str], home: str, os_name: str, conf_dir: str) -> HubToken:
    """The HubToken of a settings folder (built at the first ask, then kept; HF_TOKEN is read from env at each use)."""
    def read_env() -> str:
        return env.get(TOKEN_ENV, "")

    if conf_dir not in _TOKENS:
        _TOKENS[conf_dir] = HubToken(secret_store(os_name, conf_dir, home), read_env,
                                     PrivateFile(shared_token_file(env, home)).read)
    found = _TOKENS[conf_dir]
    found.env = read_env
    found.shared = PrivateFile(shared_token_file(env, home)).read
    return found


def hf_auth(token: HubToken) -> HfAuth:
    """The calls to Hugging Face with the token that counts at the time of each call."""
    return HfAuth(token.current)


def forget() -> None:
    """The tests: build the tokens again."""
    _TOKENS.clear()
