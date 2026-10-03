// Pi <-> MTPLX request bridge (adapted from MTPLX's generated
// mtplx-request-policy.ts to cover more than one served model).
//
// 1. Sends Pi's session/entry ids so MTPLX can reuse its prompt cache.
// 2. Pi serializes each model's advertised maxTokens on every request; for
//    the MTPLX models below that default ceiling is stripped so the server
//    owns the output budget. An explicit user cap (any other value) is kept.
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

/** Model id -> the maxTokens value models.json advertises for it. */
const MTPLX_MODELS: ReadonlyMap<string, number> = new Map([
	["qwen3.8-27b-abliterated-grant", 49152],
	["qwen3.8-27b-abliterated", 49152],
]);

/** The fields Pi uses for the output cap, depending on the provider's maxTokensField. */
const MAX_TOKENS_FIELDS = ["max_tokens", "max_completion_tokens"] as const;

// Pi's ids are UUIDs / short hex; anything else (CR/LF, spaces) is dropped
// rather than sent, so a header can never be split.
const SAFE_HEADER_VALUE = /^[A-Za-z0-9._:-]{1,128}$/;

type Headers = Record<string, string | null>;
type Payload = Record<string, unknown>;

function headerValue(value: string | null): string | undefined {
	return value !== null && SAFE_HEADER_VALUE.test(value) ? value : undefined;
}

/** Only requests to the MTPLX provider carry `x-mtplx-client: pi` (set in models.json). */
function isMtplxRequest(headers: Headers): boolean {
	const client = Object.entries(headers).find(([key]) => key.toLowerCase() === "x-mtplx-client")?.[1];
	return client === "pi";
}

function addSessionHeaders(headers: Headers, ctx: ExtensionContext): void {
	const session = headerValue(ctx.sessionManager.getSessionId());
	if (session) headers["x-mtplx-session-id"] = session;
	const entry = headerValue(ctx.sessionManager.getLeafId());
	if (entry) headers["x-mtplx-client-entry-id"] = entry;
}

function isPayload(value: unknown): value is Payload {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** The payload without Pi's default output cap, or undefined when there is nothing to strip. */
function withoutDefaultCap(payload: Payload): Payload | undefined {
	const injected = typeof payload.model === "string" ? MTPLX_MODELS.get(payload.model) : undefined;
	if (injected === undefined) return undefined;
	const capped = MAX_TOKENS_FIELDS.filter((field) => payload[field] === injected);
	if (!capped.length) return undefined;
	const request = { ...payload };
	for (const field of capped) delete request[field];
	return request;
}

export default function (pi: ExtensionAPI): void {
	pi.on("before_provider_headers", (event, ctx) => {
		const headers = event?.headers;
		if (!headers || typeof headers !== "object") return;
		if (isMtplxRequest(headers)) addSessionHeaders(headers, ctx);
	});

	pi.on("before_provider_request", (event) => {
		const payload = event?.payload;
		return isPayload(payload) ? withoutDefaultCap(payload) : undefined;
	});
}
