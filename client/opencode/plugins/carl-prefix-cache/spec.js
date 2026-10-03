// The prefix spec (used by index.js): what OpenCode sends before the user's words, as the
// CARL dashboard needs it to rebuild the prompt with the model's own chat template.
// Pure. Kept out of index.js: older OpenCode versions call every export of a plugin's entry
// module as a plugin function.

/** @typedef {{ id: string, description: string, parameters: unknown }} ListedTool */

/**
 * The spec file's content.
 * @param {string} provider
 * @param {string} model
 * @param {string[]} system  the system prompt's parts (OpenCode joins them with newlines)
 * @param {ListedTool[]} tools  client.tool.list()
 */
export function prefixSpec(provider, model, system, tools) {
  return {
    schema: 1,
    provider,
    model,
    system: system.join("\n"),
    // as the request carries them: sorted by name (checked against OpenCode 1.18's requests);
    // "invalid" is internal (never sent)
    tools: tools.filter((t) => t && t.id !== "invalid")
      .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
      .map((t) => ({ type: "function", function: { name: t.id, description: t.description, parameters: t.parameters } })),
  };
}

/** A model id as a file name (the ids CARL serves are already safe; anything else is replaced). */
export function fileName(model) {
  return String(model).replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 120) + ".json";
}
