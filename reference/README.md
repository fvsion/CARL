# CARL Reference

CARL: Can't Afford Remote LLMs.

CARL runs a local coding model (Qwen, Gemma) on an Apple Silicon Mac, or on a Windows or Linux PC with a GPU, for OpenCode or Pi. This reference gives the technical details of CARL: the architecture, the files, the llama.cpp server, the PC setup, the caches, and the measured results. It also tells how each model and client controls "thinking". For setup and daily use, see the [user guide](../USERGUIDE.md). For the short overview, see the [README](../README.md).

CARL is tested with OpenCode 1.19.0 and Pi 1.1.0 (`client/client-versions.env`), and with llama.cpp 0.6.0 (build 11429) on a Mac and 0.7.0 (build 11738) on a Windows or Linux PC. Many behaviours in this reference were checked against the source code of earlier versions (pi-ai 0.99.2, OpenCode 1.18.32) and the GGUF chat templates. Other behaviours were measured on the development Macs, an M3 Pro 36 GB and an M2 Max 32 GB, with llama.cpp 0.4.1 to 0.6.0. A measurement names its Mac and its date. If a statement is not verified, the text says so.

## Contents

| Page | What it covers |
|---|---|
| [Architecture and repository layout](architecture.md) | How the parts fit and every module of CARL |
| [The llama.cpp server](server.md) | The server on each OS, its settings, router mode, the network modes, Auto-tune and the flags |
| [Windows and Linux](windows-linux.md) | The setup of the server on a PC with a GPU: the command, the llama.cpp install, the GPU's memory, the builds |
| [Memory: what fits and what context costs](memory.md) | The GPU memory limit, a PC's discrete GPU (VRAM kept free, the split, experts in the RAM, the MoE plugin), the context length and the context memory |
| [Caching](caching.md) | Every cache: llama.cpp's slots and RAM cache, the disk cache of OpenCode and Pi, how a saved state is built, shared storage of saved sessions |
| [Clients on other computers](client-sync.md) | The client package, the dashboard API, the config push and the sync service |
| [Models and quantization](models.md) | The catalogue models (Qwen and Gemma 4) and why, the quality ranks, IQ quants, the coder subagent |
| [Thinking](thinking.md) | How thinking works, by model, by client, the full matrix |
| [Sampling, output limits and context limits](sampling.md) | The sampling values, what runs, context limits |
| [Verifying behaviour](verifying.md) | How to check thinking, the template and the sampling of the server |
| [OpenCode and Pi configs](client-configs.md) | The OpenCode and Pi configs that CARL writes, and the prompt budget |
| [The plugins and extensions](plugins.md) | Every OpenCode plugin and Pi extension CARL installs: what it does, how, its switch; and CARL's own plugin seam |
| [The hand-off to the coder](delegation.md) | How the main agent gives tasks to the coder: the rule, the reminder, the two modes, the TOML brief and its check, the coder's gates, the chain (the code and the tests, in separate sessions), `/code`, the new-file gate, and what was measured |
| [Glossary](glossary.md) | The words and units on every screen and in the docs: one name for each thing |
| [Performance](performance.md) | Measured speeds of the Qwen 27B and 35B |
