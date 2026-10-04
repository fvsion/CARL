# CARL Reference

CARL: Can't Afford Remote LLMs.

CARL runs a local coding model (Qwen, Gemma) on an Apple Silicon Mac, for OpenCode or Pi. This reference gives the technical details of CARL: the architecture, the files, the llama.cpp server, the caches, and the measured results. It also tells how each model and client controls "thinking". For setup and daily use, see the [user guide](../USERGUIDE.md). For the short overview, see the [README](../README.md).

Each behaviour in this reference was checked against the source code (pi-ai 0.99.2, OpenCode 1.18.32, the GGUF chat templates). Other behaviours were measured on this Mac. If a statement is not verified, the text says so.

## Contents

| Page | What it covers |
|---|---|
| [Architecture and repository layout](architecture.md) | How the parts fit and every file in the repository |
| [The llama.cpp server](server.md) | The server, its settings, router mode, the network modes, Auto-tune and the flags |
| [Memory: what fits and what context costs](memory.md) | The GPU limit, context length and context memory |
| [Caching](caching.md) | Every cache: llama.cpp's slots and RAM prompt cache, the disk prompt cache of OpenCode and Pi, how a saved state is built, conversations stored as patches |
| [Clients on other computers](client-sync.md) | The connection file, the dashboard's API, the config push and the sync service |
| [Models and quantization](models.md) | The catalogue models and why, IQ quants, the coder subagent |
| [Thinking](thinking.md) | How thinking works, by model, by client, the full matrix |
| [Sampling, output limits and context limits](sampling.md) | The sampling values, what runs, context limits |
| [Verifying behaviour](verifying.md) | How to see what a client sends |
| [OpenCode and Pi configs](client-configs.md) | The OpenCode and Pi configs that CARL writes, and the prompt budget |
| [The plugins and extensions](plugins.md) | Every OpenCode plugin and Pi extension CARL installs: what it does, how, its switch |
| [Performance](performance.md) | Measured speeds |
