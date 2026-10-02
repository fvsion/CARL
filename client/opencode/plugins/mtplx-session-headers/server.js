// Older V1 imports index.js; modern V1 and V2 resolve this entrypoint.
// No prompt, tool-schema or generation-option rewriting belongs here.
import { MTPLXSessionHeaders } from "./index.js";
export default {
  id: "mtplx.session-headers",
  server: MTPLXSessionHeaders,
  async setup(ctx) {
    await ctx.session.hook("model.request", (event) => {
      event.headers["x-mtplx-client"] = "opencode";
      event.headers["x-mtplx-session-id"] = String(event.sessionID);
    }, { providerID: "mtplx" });
  }
};
