// "llm-deploy-mtplx" when the user already had a provider called "mtplx".
const MTPLX_IDS = new Set(["mtplx", "llm-deploy-mtplx"]);
const mtplxProviderID = (input) =>
  input?.model?.providerID || input?.provider?.id;

const mtplxInjectedOutputCap = 32000;
const mtplxInjectedQwenTemperature = 0.55;
const mtplxInjectedQwenTopP = 1;

export const MTPLXSessionHeaders = async () => ({
  "chat.headers": async (input, output) => {
    output.headers ||= {};
    const providerID = mtplxProviderID(input);
    if (providerID && !MTPLX_IDS.has(providerID)) return;
    output.headers["x-mtplx-client"] = "opencode";
    if (input?.sessionID) {
      output.headers["x-mtplx-session-id"] = String(input.sessionID);
    }
    if (input?.message?.id) {
      output.headers["x-mtplx-client-turn-id"] = String(input.message.id);
    }
  },
  "chat.params": async (input, output) => {
    const providerID = mtplxProviderID(input);
    if (providerID && !MTPLX_IDS.has(providerID)) return;
    // OpenCode injects maxOutputTokens = min(limit.output, 32000) on every
    // request even when the configured model advertises a larger native
    // context. Strip exactly that injected default so MTPLX owns the
    // uncapped generation contract; an explicit client cap (any other
    // value) passes through untouched.
    if (output.maxOutputTokens === mtplxInjectedOutputCap) {
      output.maxOutputTokens = undefined;
    }
    // OpenCode <= 1.18.20 (Desktop 1.18.18 included) injects a qwen-keyed
    // sampler (temperature 0.55, topP 1) for any model id containing
    // "qwen"; 1.18.21 removed the rule. Strip exactly that injected pair so
    // the MTPLX server's family-native sampler applies; any other value is
    // a deliberate client choice and passes through untouched.
    const modelID = String(input?.model?.id ?? input?.model?.modelID ?? "").toLowerCase();
    if (modelID.includes("qwen")) {
      if (output.temperature === mtplxInjectedQwenTemperature) {
        output.temperature = undefined;
      }
      if (output.topP === mtplxInjectedQwenTopP) {
        output.topP = undefined;
      }
    }
  }
});
export default MTPLXSessionHeaders;
