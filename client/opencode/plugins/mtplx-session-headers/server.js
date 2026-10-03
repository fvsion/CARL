// Entry point for OpenCode versions that resolve exports["./server"] (1.18.x);
// older versions import index.js instead. Both use the same V1 hooks, which send
// the session headers and strip the injected sampling defaults.
// No prompt, tool-schema or generation-option rewriting belongs here.
import { MTPLXSessionHeaders } from "./index.js";

export default {
  id: "mtplx.session-headers",
  server: MTPLXSessionHeaders,
};
