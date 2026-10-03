// OpenCode server plugin (V1 hooks) for the MTPLX provider: sends the session
// and turn ids as headers so MTPLX can reuse its prompt cache, and strips the
// sampling defaults OpenCode injects on its own so the server's values apply.
// Export nothing else from this file: older OpenCode versions call every
// export of the entry module as a plugin function (helpers live in mtplx.js).
import { isMtplxRequest, sessionHeaders, stripInjectedDefaults } from "./mtplx.js";

/** @type {import("@opencode-ai/plugin").Plugin} */
export const MTPLXSessionHeaders = async () => ({
  "chat.headers": async (input, output) => {
    output.headers ||= {};
    if (!isMtplxRequest(input)) return;
    Object.assign(output.headers, sessionHeaders(input?.sessionID, input?.message?.id));
  },
  "chat.params": async (input, output) => {
    if (!isMtplxRequest(input)) return;
    stripInjectedDefaults(input, output);
  },
});
export default MTPLXSessionHeaders;
