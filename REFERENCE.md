# CARL Reference

CARL: Can't Afford Remote LLMs.

CARL runs a local coding model (Qwen, Gemma) on an Apple Silicon Mac, for OpenCode or Pi. This reference gives the technical details of CARL: the architecture, the files, the llama.cpp server, and the measured results. It also tells how each model and client controls "thinking". For setup and daily use, see [USERGUIDE.md](USERGUIDE.md). For the short overview, see [README.md](README.md).

Each behaviour in this reference was checked against the source code (pi-ai 0.99.2, OpenCode 1.18.32, the GGUF chat templates). Other behaviours were measured on this Mac. If a statement is not verified, the text says so.


## Contents

| Page | What it covers |
|---|---|
| [Architecture and repository layout](reference/architecture.md) | How the parts fit and every file in the repository |
| [The llama.cpp server](reference/server.md) | The server, its settings, router mode, the network modes, Auto-tune and the flags |
| [Memory: what fits and what context costs](reference/memory.md) | The GPU limit, context length and context memory, the RAM prompt cache |
| [The disk prompt cache](reference/cache.md) | Saving and restoring prompt states for OpenCode and Pi, and the cache settings |
| [How the pieces fit](reference/pieces.md) | How a saved state is built, which parts are shared, and why |
| [Clients on other computers](reference/client-sync.md) | The connection file, the dashboard's API, the config push and the sync service |
| [Models and quantization](reference/models.md) | The catalogue models and why, IQ quants, the coder subagent |
| [Thinking](reference/thinking.md) | How thinking works, by model, by client, the full matrix |
| [Sampling, output limits and context limits](reference/sampling.md) | Qwen's sampling values, what runs, context limits |
| [Verifying behaviour](reference/verifying.md) | How to see what a client sends |
| [OpenCode config](reference/opencode.md) | The OpenCode and Pi configs that CARL writes, and the prompt budget |
| [The plugins and extensions](reference/plugins.md) | Every OpenCode plugin and Pi extension CARL installs: what it does, how, its switch |
| [Performance](reference/performance.md) | Measured speeds |
