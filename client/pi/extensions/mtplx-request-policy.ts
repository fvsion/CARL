// Pi <-> MTPLX request bridge (adapted from MTPLX's generated
// mtplx-request-policy.ts to cover more than one served model).
//
// 1. Sends Pi's session/entry ids so MTPLX can reuse its prompt cache.
// 2. Pi serializes each model's advertised maxTokens on every request; for
//    the MTPLX models below that default ceiling is stripped so the server
//    owns the output budget. An explicit user cap (any other value) is kept.
const mtplxModels: Record<string, number> = {
  // model id -> the maxTokens value advertised in models.json
  "qwen3.8-27b-abliterated-grant": 49152,
  "qwen3.8-27b-abliterated": 49152,
};

export default function (pi: any) {
  pi.on("before_provider_headers", (event: any, ctx: any) => {
    const headers = event?.headers;
    if (!headers || typeof headers !== "object") return;
    const client = Object.entries(headers).find(
      ([key]) => key.toLowerCase() === "x-mtplx-client",
    )?.[1];
    if (client !== "pi") return;
    event.headers["x-mtplx-session-id"] = String(
      ctx.sessionManager.getSessionId(),
    );
    const leaf = ctx.sessionManager.getLeafId();
    if (leaf) event.headers["x-mtplx-client-entry-id"] = String(leaf);
  });

  pi.on("before_provider_request", (event: any) => {
    const payload = event?.payload;
    if (!payload || typeof payload !== "object") return;
    const injected = mtplxModels[payload.model];
    if (injected === undefined) return;
    const request = { ...payload };
    let changed = false;
    if (request.max_tokens === injected) {
      delete request.max_tokens;
      changed = true;
    }
    if (request.max_completion_tokens === injected) {
      delete request.max_completion_tokens;
      changed = true;
    }
    if (!changed) return;
    return request;
  });
}
