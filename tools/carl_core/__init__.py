"""CARL core: models, settings, memory fit and Auto-tune.

domain/    pure logic and the ports (Protocols) it needs; no I/O
adapters/  the outside world: JSON files, the models folder, Hugging Face, aria2c/curl,
           sysctl/netstat/ps, the llama-server used by Auto-tune
app.py     application services that combine the domain with ports
wiring.py  builds the real adapters (the composition root for tools/carl.py & co.)

tools/carl.py, tools/carl-tune.py, tools/llama-fit.py and tools/gguf_shape.py are the
command-line edges and keep the public API the launchers and the monitor use.
"""
