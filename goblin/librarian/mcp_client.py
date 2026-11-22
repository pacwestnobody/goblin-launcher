"""Minimal MCP stdio client: initialize, tools/list, tools/call.

Lets the talker use any MCP tool server you already run, unchanged.
Config:
  mcp_servers:
    files:
      command: ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/srv"]
      env: {}
      confirm: false
"""
import json
import os
import queue
import subprocess
import threading


class MCPError(RuntimeError):
    pass


class MCPServer:
    def __init__(self, name, spec):
        self.name = name
        self.spec = spec
        env = dict(os.environ)
        env.update({k: str(v) for k, v in (spec.get("env") or {}).items()})
        self.proc = subprocess.Popen(
            spec["command"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1, env=env,
        )
        self._q = queue.Queue()
        self._id = 0
        threading.Thread(target=self._reader, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "goblin-launcher", "version": "2.0"},
        })
        self._notify("notifications/initialized")

    def _reader(self):
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                continue
        self._q.put(None)

    def _send(self, obj):
        if self.proc.poll() is not None:
            raise MCPError(f"MCP server '{self.name}' exited")
        self.proc.stdin.write(json.dumps(obj) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _request(self, method, params, timeout=60):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                msg = self._q.get(timeout=timeout)
            except queue.Empty:
                raise MCPError(f"MCP '{self.name}' timed out on {method}")
            if msg is None:
                raise MCPError(f"MCP server '{self.name}' closed its output")
            if msg.get("id") != rid:
                continue  # notifications or stale replies
            if "error" in msg:
                raise MCPError(f"MCP '{self.name}' {method}: {msg['error']}")
            return msg.get("result", {})

    def list_tools(self):
        return self._request("tools/list", {}).get("tools", [])

    def call(self, tool, args, timeout=120):
        res = self._request("tools/call", {"name": tool, "arguments": args}, timeout=timeout)
        parts = []
        for c in res.get("content", []):
            if c.get("type") == "text":
                parts.append(c["text"])
            else:
                parts.append(f"[{c.get('type')} content omitted]")
        return (1 if res.get("isError") else 0), "\n".join(parts)

    def close(self):
        try:
            self.proc.terminate()
        except Exception:
            pass
