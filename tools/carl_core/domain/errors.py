"""The one error type users see: a bad setting, file or request, with a readable message."""


class ConfigError(ValueError):
    """A user-facing problem (bad value, unknown model, unreadable file). The CLI prints
    it as "error: ..." and exits 1."""
