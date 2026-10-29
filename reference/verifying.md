# CARL Reference: Verifying behaviour

[Index](README.md) · how to check what the server does with a request.

## Verifying behaviour

The examples use the default address, 127.0.0.1. With `--vm`, use 192.168.42.1.

```bash
K=$(cat ~/.config/carl/api-key)
ADDR=127.0.0.1

# Is thinking off? Look at the reasoning_content length.
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' http://$ADDR:9275/v1/chat/completions \
  -d '{"model":"x","reasoning_effort":"none","messages":[{"role":"user","content":"Is 91 prime?"}]}' \
  | python3 -c 'import json,sys; m=json.load(sys.stdin)["choices"][0]["message"]; print(len(m.get("reasoning_content") or ""), "reasoning chars")'

# Is the patched template loaded?
PID=$(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]9275$/ {n=split($(NF-8),a,":"); print a[n]; exit}')
ps -o command= -p $PID | grep -o -- '--chat-template-file [^ ]*'
head -2 ~/models/templates/*.thinking-toggle.jinja

# The server's sampling defaults and context
curl -s -H "Authorization: Bearer $K" http://$ADDR:9275/props | python3 -c 'import json,sys; d=json.load(sys.stdin)["default_generation_settings"]; print(d["n_ctx"], {k: d["params"][k] for k in ("temperature","top_k","top_p","min_p")})'
```

- With a single model, the server ignores the `"model"` field. In router mode, use the CARL name of a model.
- In router mode, `/props` needs `?model=NAME`. Without it, it answers `role: router`.
