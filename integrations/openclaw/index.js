// Goblin Launcher for OpenClaw (Path Librarian context + lab_run). Every agent turn, any model (local Ollama or cloud):
//   before_prompt_build -> verified facts + knowledge notes as prependContext (only when relevant)
//   after_tool_call     -> trace the call (args/output are redacted by the service)
//   session_end         -> close the conversation's trace (agent_end would need conversation
//                          access, which this plugin doesn't want)
// Talks to the one service over its private unix socket. Fails soft: if the librarian is
// down or slow, the turn runs exactly as it would without this plugin.
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { definePluginEntry } from "openclaw/plugin-sdk/plugin-entry";

const SOCK = path.join(process.env.XDG_RUNTIME_DIR || `/tmp/goblin-${os.userInfo().uid}`, "goblin.sock");

function call(route, body, timeoutMs) {
  return new Promise((resolve) => {
    const data = JSON.stringify(body);
    const req = http.request(
      { socketPath: SOCK, path: route, method: "POST", timeout: timeoutMs,
        headers: { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) } },
      (res) => {
        let buf = "";
        res.on("data", (c) => (buf += c));
        res.on("end", () => {
          try { resolve(res.statusCode < 400 ? JSON.parse(buf || "{}") : null); } catch { resolve(null); }
        });
      });
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
    req.end(data);
  });
}

const agentOf = (ctx) => `openclaw:${ctx?.agentId || "main"}`;
const sessionOf = (ctx, ev) => String(ctx?.sessionKey || ev?.sessionKey || ctx?.sessionId || ev?.sessionId || "default");
const text = (v) => (typeof v === "string" ? v : (() => { try { return JSON.stringify(v); } catch { return String(v); } })());

export default definePluginEntry({
  id: "goblin",
  name: "Goblin Launcher",
  description: "Adds verified lab facts and knowledge-library notes to each turn; traces tool calls",
  register(api) {
    api.registerTool({
      name: "lab_run",
      description: "Run a shell command on one of the owner's lab hosts through goblin-vault; each host follows " +
        "its own rule and the owner approves (the call waits). Say what a change does before running it. " +
        "Targets: see librarian_context or ask the owner.",
      parameters: { type: "object", additionalProperties: false, required: ["target", "command"],
                    properties: { target: { type: "string" }, command: { type: "string" } } },
      async execute(_id, params, ctx) {
        const r = await call("/v1/lab_run", { target: String(params?.target || ""), command: String(params?.command || ""),
                                              agent: agentOf(ctx) }, 900000);
        const text = r ? `exit=${r.exit}\n${r.output || ""}` : "Goblin Launcher is not running.";
        return { content: [{ type: "text", text }] };
      },
    });

    api.on("before_prompt_build", async (event, ctx) => {
      const r = await call("/v1/context", { prompt: String(event?.prompt || ""), agent: agentOf(ctx),
                                             session: sessionOf(ctx) }, 20000);
      return r && r.text ? { prependContext: r.text } : undefined;
    }, { timeoutMs: 25000 });

    api.on("after_tool_call", async (event, ctx) => {
      await call("/v1/trace", { agent: agentOf(ctx), session: sessionOf(ctx), tool: event?.toolName || "?",
                                args: event?.params || {}, exit: event?.error ? 1 : 0,
                                output: event?.error ? String(event.error) : text(event?.result ?? "") }, 5000);
    }, { timeoutMs: 8000 });

    api.on("session_end", async (event, ctx) => {
      await call("/v1/end", { agent: agentOf(ctx), session: sessionOf(ctx, event),
                              status: String(event?.reason || "ended") }, 5000);
    }, { timeoutMs: 8000 });
  },
});
