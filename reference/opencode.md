# CARL Reference: OpenCode config

[Index](../REFERENCE.md) · the OpenCode config CARL writes.

## OpenCode config

The installer writes these settings into `~/.config/opencode/opencode.json` (template: `client/opencode/opencode.json`):

| Provider / model | Context | Thinking (`/variants`) | Default |
|---|---|---|---|
| `llamacpp/qwen3.6-35b-a3b` (also its IQ3) | server `--ctx` | `none` (off), `high` (on) | `high`; **the default model** |
| `llamacpp/qwen3.8-27b` (stock; also its Q3 and IQ3) | server `--ctx` | `none` (off), `low`, `medium`, `xhigh` | `low` |
| `llamacpp/qwen3.8-27b-abliterated-llama` (orcarouter; also its Q3) | server `--ctx` | `none` (off), `low`, `medium`, `xhigh` | `low` |

- **Tools and the prompt budget (Phase 8, measured 2026-10-03, OpenCode 1.18.34, Qwen3.6 35B-A3B IQ3's template and tokenizer):** the first request's system prompt + tool definitions, counted with `tools/req-capture-proxy.py --bodies DIR` (the full request) and `tools/prompt-size.py BODY --per-tool` (`/apply-template` + `/tokenize`):

  | Setup | System + tools | Note |
  |---|---|---|
  | as installed before Phase 8 | 8,564 | 11 tools: bash 1,336 · task 1,084 · todowrite 659 · read 477 · edit 462 · question 394 · webfetch 329 · grep 311 · glob 266 · write 250 · skill 165; system 2,634 |
  | + web search (`OPENCODE_ENABLE_EXA`) | +474 | `websearch`; the gate is `providerID opencode` or this flag |
  | + LSP tool (`OPENCODE_EXPERIMENTAL_LSP_TOOL`) | +577 | does nothing unless config `"lsp": true` (1.18 starts no language server by default) |
  | + background subagents | +97 | the task tool's `background` parameter |
  | + plan mode | 0 | its tool is for the `cli` client only |
  | + Playwright MCP in the main agent | +4,779 | 26 tools: hence the browser subagent (its own prompt: 7.5K) |
  | `experimental.batch_tool` | 0 | not in 1.18.34's registry; parallel tool calls instead (`parallel_tool_calls: true`, which llama.cpp honours: 2 calls in one turn vs 1) |
  | **as installed by Phase 8** | **9,870** | 13 tools + the browser and coder subagents' descriptions; target < 10.5K |

  OpenCode's tool registry (1.18.34): question (TUI clients or `OPENCODE_ENABLE_QUESTION_TOOL`), bash, read, glob, grep, edit, write, task, webfetch, todowrite, websearch (provider gate), skill, apply_patch (GPT models only), plus `lsp` (flag), `plan` (flag, cli client) and code mode (flag). Model options pass through to the request body as they are. Re-measure after an OpenCode update.
- **Timeouts:** the config sets `timeout: false` and `chunkTimeout: 900000` (15 min). A long cold prompt can take many minutes before the first token.
- **Title agent:** the title agent of OpenCode stays on (earlier versions disabled it). With 2 slots, it runs at the same time as the main session. It does not wait in a queue behind the main session.
- **Plugins:** `subagents-sidebar` and `session-switcher` are TUI plugins in `~/.config/opencode/tui.json`. To update them, run `install.sh` again and restart OpenCode.
- **Coder subagent:** see [The coder subagent](models.md#the-coder-subagent).
- **Pi** (`~/.pi/agent/models.json`, `~/.pi/agent/settings.json`): defaults (only if they are unset or still ours) are provider `llamacpp`, model `qwen3.6-35b-a3b`, thinking `low`. For the thinking format, see [section 6](thinking.md#thinking-by-client).
