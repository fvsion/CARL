"""The one error type users see: a bad setting, file or request, with a readable message. And the error of a broken
contract, which is a programming error, never the user's."""


class ConfigError(ValueError):
    """A user-facing problem (bad value, unknown model, unreadable file). The CLI prints
    it as "error: ..." and exits 1."""


class HubDenied(ConfigError):
    """Hugging Face answered 401 or 403: a gated or private repo, or a token it does not accept. code: the status;
    path: the API or file path that was asked, without a query. The application turns it into the text that says
    how to set the token (domain/catalog/hf_token.py denied_text); the message never holds a token."""

    def __init__(self, code: int, path: str) -> None:
        super().__init__(f"Hugging Face: HTTP {code} for {path}")
        self.code = code
        self.path = path


class SecretStoreError(ConfigError):
    """The system's secret store (Keychain, Credential Manager, Secret Service, a private file) could not keep, read
    or remove the secret. The message says what failed and never holds the secret."""


class FolderAccessError(PermissionError):
    """Windows refuses the user one of CARL's own folders (an elevated run made it with an access list that leaves the
    user out) and CARL could not give the access back. The message says what to run (adapters/common/private_dirs.py).
    It is a PermissionError: a caller that skips what it cannot write still does; the dispatch prints the rest."""


class ContractError(Exception):
    """A broken contract (C-NNN, decision A17): a caller broke a precondition, or a service or
    an adapter broke its postcondition. A programming error: no command catches it, so it shows with its traceback.
    It is not a ValueError, so code that handles bad input never takes it for one."""

    def __init__(self, contract_id: str, message: str) -> None:
        super().__init__(f"{contract_id}: {message}")
        self.contract_id = contract_id
        self.message = message
