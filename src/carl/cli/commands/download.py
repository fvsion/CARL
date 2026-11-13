"""`carl download NAME...|default|all|hf:OWNER/REPO[/FILE.gguf] [--vision]`: catalogue models (pinned and checked, with
their drafters of every kind, and with --vision, or when llama.vision is on, their vision projector), Auto fit's pick, a
file from Hugging Face, or the MTP drafter of a custom Gemma 4 model. Exit 1 when a download fails. Contract C-405."""
from __future__ import annotations

from typing import List

from ...domain.shared.errors import ConfigError
from .base import Cli, unknown_model


def run(cli: Cli, names: List[str]) -> int:
    words = cli.words
    vision = True if "--vision" in names else None             # None: when llama.vision is on
    names = [n for n in names if n != "--vision"]
    if not names:
        raise ConfigError("download needs a model: NAME, default, all or hf:OWNER/REPO/FILE.gguf. "
                          f"{words.command} download --help tells more.")
    api = cli.api()
    models = api.all_models()
    if names == ["all"]:
        names = [m.get("name", "") for m in models if m.get("source") == "catalog"]
    ok = True
    for n in names:
        if n == "default":
            n = api.pick_default(models)
            print(f"Auto fit chooses {n} for {words.machine}.")
        if n.startswith(("hf:", "http")) or (n.count("/") >= 1 and not api.find(n, models)):
            ok = api.download_hf(n) and ok
            continue
        m = api.find(n, models)
        if m and m.get("custom") and m.get("status") == "downloaded" and (m.get("draft_offer") or m.get("draft")):
            ok = api.download_drafter(m) and ok             # a custom Gemma 4 model: its drafter
            continue
        if not m or not m.get("hf"):
            raise ConfigError(unknown_model(n, words))
        ok = api.download(m, api.models_dir(), vision) and ok
    return 0 if ok else 1
