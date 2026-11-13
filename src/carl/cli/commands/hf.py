"""`carl hf token [show|set|clear]` (decision A75): the Hugging Face access token that gated repos need.

  token, token show  say whether a token is set, where it comes from and its last 4 characters (never the token)
  token set          keep a token in the system's secret store (the macOS Keychain, the Windows Credential Manager, the
                     Secret Service, else a private file in the settings folder). The token is read from standard input
                     when it is piped, else asked for with nothing shown as it is typed. It is never an argument: a
                     process list shows every argument.
  token clear        remove the token CARL keeps (HF_TOKEN in the environment and the file of the Hugging Face tools
                     are the user's own: CARL does not change them)

HF_TOKEN in the environment wins over CARL's token; CARL's token wins over the file of the Hugging Face tools. Contract
C-424.
"""
from __future__ import annotations

from typing import List

from ...domain.catalog.hf_token import SOURCE_CARL, SOURCE_ENV, SOURCE_SHARED, TOKEN_ENV, TokenStatus, last_chars
from ...domain.shared.errors import ConfigError
from .base import Cli

SUBCOMMANDS = ("show", "set", "clear")
PROMPT = "Hugging Face token (nothing shows as you type): "


def usage(cli: Cli) -> str:
    return f"usage: {cli.words.command} hf token [{'|'.join(SUBCOMMANDS)}]"


def run(cli: Cli, argv: List[str]) -> int:
    if argv[:1] != ["token"]:
        raise ConfigError(usage(cli))
    sub, rest = (argv[1] if len(argv) > 1 else "show"), argv[2:]
    if sub not in SUBCOMMANDS:
        raise ConfigError(usage(cli))
    if rest:
        if sub == "set":
            raise ConfigError("CARL does not take the token as an argument, because a process list shows every "
                              "argument. Pipe it in, or run the command and type it when CARL asks.")
        raise ConfigError(usage(cli))
    token = cli.api().hub_token()
    if sub == "set":
        secret = cli.tools.read_secret(PROMPT)
        where = token.set(secret)
        print(f"CARL keeps your Hugging Face token in {where} (it ends with {last_chars(secret.strip())}).")
        status = token.status()
        if status.source != SOURCE_CARL:
            print(f"{source_text(status)} It wins over the token that you gave CARL.")
        return 0
    if sub == "clear":
        cleared = token.clear()
        print(f"CARL removed your Hugging Face token from {cleared.where}." if cleared.had
              else "CARL keeps no Hugging Face token of its own.")
        if cleared.now.is_set:
            print(f"A token is still in use. {source_text(cleared.now)} CARL does not change it.")
        return 0
    status = token.status()
    if not status.is_set:
        print(f"Hugging Face token: not set. A gated repo needs one. To set it: {cli.words.command} hf token set")
        return 0
    print(f"Hugging Face token: set (it ends with {status.last4}).")
    print(source_text(status))
    return 0


def source_text(status: TokenStatus) -> str:
    """Where the token in use comes from, in a sentence."""
    if status.source == SOURCE_ENV:
        return f"It comes from {TOKEN_ENV} in the environment."
    if status.source == SOURCE_SHARED:
        return "It comes from the file of the Hugging Face tools (~/.cache/huggingface/token)."
    if status.source == SOURCE_CARL:
        return f"CARL keeps it in {status.where}."
    return ""
