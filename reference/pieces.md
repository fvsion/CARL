# CARL Reference: How the pieces fit

[Index](../REFERENCE.md) · how a saved prompt state is built, which parts are shared, and why.

## One sequence, in order

llama.cpp keeps a conversation as one sequence of tokens. A saved state is that sequence and the model's memory of it. The sequence always has the same order:

| Order | Piece | What it holds | Who makes it |
|---|---|---|---|
| 1 | The agent's prompt | The system text and the tool definitions | OpenCode or Pi, for each agent |
| 2 | The first user message | The environment block, the project's instructions, then your first message | OpenCode or Pi; CARL moves the first two parts here |
| 3 | The conversation | The replies, the tool calls, the tool results, your next messages | The model and the client |

- The chat template sets the order inside piece 1. Qwen puts the tools first and the system text after them. Gemma 4 puts the system text first and the tools after it. Both orders are in piece 1.
- A tool call is text that the model writes. The template writes it again, from the structured call, when the client sends the conversation back.
- A tool result is a message from the client.

## Prefixes, not puzzle pieces

The pieces are nested prefixes. You cannot put them together in a different order.

- The agent's prompt file holds piece 1.
- A session file holds pieces 1, 2 and 3, up to the end of the last turn that was saved.
- A new request continues a saved state only when the request starts with all the tokens of that state. One different token stops the reuse at that point. llama.cpp then reads the rest again.
- llama.cpp cannot put piece 3 of one file after piece 1 of another file. It cannot remove a piece from the middle.

Thus, CARL keeps these rules:

1. The parts that change between projects and days go into piece 2, not piece 1. Then piece 1 is the same for an agent in every project, and one prompt file serves every session.
2. A session file is used only by its own session. Its piece 3 belongs to that session only.
3. A prompt file is used by every new session of its agent and model.

## What a saved state holds

For each token, the state holds the KV cache of the attention layers. A hybrid model (Qwen 3.6 and 3.8) also holds a recurrent state for the whole sequence.

| Part | Size (35B IQ3, q4_0) | Shared between files |
|---|---|---|
| KV cache of the attention layers | ~5.8 KB per token | Yes: the same tokens at the same places give the same bytes |
| Recurrent state | ~59 MB, for any length | No: it is a summary of the whole sequence |

- A session of 8.6K tokens is 115 MB. About 45 MB of it is the prompt's KV cache. About 59 MB is the recurrent state.
- A session of 74K tokens is about 0.5 GB.
- A model without recurrent layers (Gemma 4) has only the KV cache. A larger part of its file is shared.

## How CARL shares the pieces

CARL stores a session file as a patch against the prompt file that it starts with. The page [The disk prompt cache](cache.md#shared-pieces-dedup) gives the details and the measurements.

- The prompt's KV cache is then stored one time, in the prompt file.
- Each session stores its recurrent state and its own tokens.
- The saving is about 40% for a short session and about 10% for a long session (35B).

## What a change does

| Change | Effect |
|---|---|
| A new tool, MCP server or global instruction | Piece 1 changes. A new prompt file is made at the next new session. Old session files no longer match. |
| A new day, a new folder, a changed AGENTS.md | Piece 2 changes for new sessions. Piece 1 and the prompt file stay the same. |
| An edited or removed earlier message | Piece 3 changes at that message. The reuse stops there. |
| Another model file or llama.cpp build | No saved file matches. The file names carry a hash of both. |
