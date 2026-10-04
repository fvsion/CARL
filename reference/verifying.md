# CARL Reference: Verifying behaviour

[Index](../REFERENCE.md) · how to see what a client sends.

## Verifying behaviour

```bash
K=$(cat ~/.config/carl/api-key)

# Is thinking off? Look at reasoning_content length (llama.cpp)
curl -s -H "Authorization: Bearer $K" -H 'Content-Type: application/json' http://192.168.42.1:8080/v1/chat/completions \
  -d '{"model":"x","reasoning_effort":"none","messages":[{"role":"user","content":"Is 91 prime?"}]}' \
  | python3 -c 'import json,sys; m=json.load(sys.stdin)["choices"][0]["message"]; print(len(m.get("reasoning_content") or ""), "reasoning chars")'

# Is the patched template loaded?
PID=$(netstat -anv -p tcp | awk '$6=="LISTEN" && $4 ~ /[.]8080$/ {n=split($(NF-8),a,":"); print a[n]; exit}')
ps -o command= -p $PID | grep -o -- '--chat-template-file [^ ]*'
head -2 ~/models/templates/*.thinking-toggle.jinja

# Server sampling defaults and context
curl -s -H "Authorization: Bearer $K" http://192.168.42.1:8080/props | python3 -c 'import json,sys; d=json.load(sys.stdin)["default_generation_settings"]; print(d["n_ctx"], {k: d["params"][k] for k in ("temperature","top_k","top_p","min_p")})'
```

**To capture what a client really sends** (thinking fields, tool count, reasoning returned): Run `tools/req-capture-proxy.py` in front of the server.

```bash
HOST=127.0.0.1 PORT=8081 ./carl.sh llama &      # the server, behind the proxy
python3 tools/req-capture-proxy.py 192.168.42.1:8080 127.0.0.1:8081 ~/models/logs/req-capture.jsonl
```

- The proxy does not log the message text.
- Each JSON line has `req` (`reasoning_effort`, `chat_template_kwargs` and the other request fields), `n_messages`, `n_tools`, `reasoning_chars` and `content_chars`.
- The proxy writes an entry when the response is complete. Thus, a long cold prompt shows nothing until it is complete. The first OpenCode prompt (~9K tokens) takes approximately 2 min on the 27B.
- If the request has only the base `reasoning_effort`, the client did not apply the variant. Run `client/install.sh` again and fully restart OpenCode. Then capture again.
