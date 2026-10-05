# CARL Reference: Verifying behaviour

[Index](README.md) · how to see what a client sends.

## Verifying behaviour

The examples use the default address, 127.0.0.1. With `--vm`, use 192.168.42.1.

```bash
K=$(cat ~/.config/carl/api-key)
ADDR=127.0.0.1

# Is thinking off? Look at the reasoning_content length.
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' http://$ADDR:8080/v1/chat/completions \
  -d '{"model":"x","reasoning_effort":"none","messages":[{"role":"user","content":"Is 91 prime?"}]}' \
  | python3 -c 'import json,sys; m=json.load(sys.stdin)["choices"][0]["message"]; print(len(m.get("reasoning_content") or ""), "reasoning chars")'

# Is the patched template loaded?
PID=$(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')
ps -o command= -p $PID | grep -o -- '--chat-template-file [^ ]*'
head -2 ~/models/templates/*.thinking-toggle.jinja

# The server's sampling defaults and context
curl -s -H "Authorization: Bearer $K" http://$ADDR:8080/props | python3 -c 'import json,sys; d=json.load(sys.stdin)["default_generation_settings"]; print(d["n_ctx"], {k: d["params"][k] for k in ("temperature","top_k","top_p","min_p")})'
```

- With a single model, the server ignores the `"model"` field. In router mode, use the CARL name of a model.
- In router mode, `/props` needs `?model=NAME`. Without it, it answers `role: router`.

### Capture what a client sends

To see the thinking fields, the tool count and the reasoning that comes back, put `tools/req-capture-proxy.py` in front of the server:

```bash
HOST=127.0.0.1 PORT=8081 ./carl.sh llama &      # the server, behind the proxy
python3 tools/req-capture-proxy.py 127.0.0.1:8080 127.0.0.1:8081 ~/models/logs/req-capture.jsonl
```

- The arguments are LISTEN, UPSTREAM and LOG. The defaults are `192.168.42.1:8080`, `127.0.0.1:8081` and `/tmp/req-capture.jsonl`.
- The proxy refuses wildcard listen addresses. It writes its log with mode 600.
- The proxy does not log the message text or the headers (the API key).
- Each JSON line has `req` (`reasoning_effort`, `chat_template_kwargs` and the other request fields), `n_messages`, `n_tools`, `status`, `reasoning_chars` and `content_chars`.
- `--bodies DIR` also saves the full body of each chat request (mode 600). `tools/prompt-size.py BODY --per-tool` counts its tokens ([OpenCode config](client-configs.md)).
- The proxy writes an entry when the response is complete. Thus, a long cold prompt shows nothing until it is complete. The first OpenCode prompt (~9K tokens) takes approximately 2 min on the 27B (M3 Pro 36 GB).
- If the request has only the base `reasoning_effort`, the client did not apply the variant. Run the setup again and fully restart OpenCode. Then capture again.
