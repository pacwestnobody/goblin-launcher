/* The Ledger: Path Librarian jobs (streamed), route cards, the shadow report, the sandbox.
   Uses $, api, esc and toast from goblin.js. */
(() => {
  let status = null, current = null, polling = 0;

  document.querySelectorAll(".ledger-tabs button").forEach(b => b.addEventListener("click", () => {
    document.querySelectorAll(".ledger-tabs button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
    document.querySelectorAll(".lt-pane").forEach(p => p.hidden = p.dataset.lpane !== b.dataset.lt);
    ({ cards: loadCards, report: loadReport, sandbox: sandboxState }[b.dataset.lt] || (() => {}))();
  }));

  const fill = (sel, items, value) => {
    const keep = sel.value;
    sel.innerHTML = items.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join("");
    sel.value = keep && items.some(([v]) => v === keep) ? keep : value;
  };

  async function loadStatus() {
    try { status = await api("/api/ledger/status"); } catch (e) { $("#jhint").textContent = e.message; return; }
    $("#lmode").textContent = `${status.mode} mode`;
    $("#lcards").textContent = status.cards ? `(${status.cards})` : "";
    fill($("#jtarget"), Object.entries(status.targets).map(([n, t]) => [n, `${n} (${t})`]), status.default_target);
    fill($("#jtalker"), status.models.map(n => [n, n]), status.roles.talker);
    fill($("#jkind"), [["", "(none: trace only)"], ...Object.entries(status.job_kinds).map(([k, v]) => [k, `${k}${v.stable === false ? " (never cards)" : ""}`])], "");
    $("#japprove").value = $("#jtarget").value === "sandbox" ? "auto" : "readonly";
    renderJobs();
  }
  $("#jtarget").addEventListener("change", () => $("#japprove").value = $("#jtarget").value === "sandbox" ? "auto" : "readonly");

  function renderJobs() {
    const live = status.live_jobs.map(j => ({ id: j.id, live: true, task: j.task, state: j.state, meta: `${j.target} · ${j.talker}`, job_id: j.result?.job_id }));
    const liveIds = new Set(live.map(j => j.job_id).filter(Boolean));
    const past = status.recent.filter(r => !liveIds.has(r.job_id)).map(r => ({ id: r.job_id, live: false, task: r.kind || "(no kind)", state: r.status,
      meta: `${r.steps} steps${r.write ? " · " + r.write.outcome : ""}${r.injected_card ? " · card " + r.injected_card : ""}` }));
    const all = [...live, ...past];
    $("#joblist").innerHTML = all.map(j => `<li data-id="${esc(j.id)}" data-live="${j.live}" class="${current && current.id === j.id ? "sel" : ""}">
      <span>${esc(j.task)}</span><span class="m">${esc(j.state)} · ${esc(j.meta)}</span></li>`).join("") || `<li class="m">No jobs yet.</li>`;
  }
  $("#joblist").addEventListener("click", e => { const li = e.target.closest("li[data-id]"); if (li) (li.dataset.live === "true" ? openLive : openPast)(li.dataset.id); });

  const exitClass = x => x === 0 ? "exit0" : x == null || x === "couldnt_run" ? "exitc" : "exitn";
  function renderEvent(e) {
    const d = document.createElement("div");
    switch (e.event) {
      case "start":
        d.className = "ev librarian";
        d.innerHTML = `<div class="h">Started on <b>${esc(e.target)}</b> with <b>${esc(e.talker)}</b> · asks: ${esc({ readonly: "only for changes", ask: "every time", auto: "never" }[e.approve] || e.approve || "-")} · ${esc(e.mode)} mode${e.kind ? " · " + esc(e.kind) : ""}${e.artifact ? " · " + esc(e.artifact) : ""}</div>`; break;
      case "assistant":
        d.className = "ev assistant"; d.innerHTML = `<div class="h"></div>`; d.firstChild.textContent = e.text || ""; break;
      case "tool": {
        d.className = "ev";
        const args = e.args && Object.keys(e.args).length ? JSON.stringify(e.args.args ?? e.args) : "";
        d.innerHTML = `<div class="h"><span class="note">#${esc(e.step)}</span><b>${esc(e.name)}</b>${e.read ? '<span class="note">checkable read</span>' : ""}<span class="${exitClass(e.exit)}">exit ${esc(e.exit)}</span><span class="note">${esc(e.t)}${e.t !== "" ? "s" : ""}</span></div>
          ${args ? `<pre>${esc(args)}</pre>` : ""}${e.output ? `<details><summary class="note">output</summary><pre>${esc(e.output)}</pre></details>` : ""}`; break; }
      case "librarian":
        d.className = "ev librarian"; d.innerHTML = `<div class="h">Librarian (${esc(e.phase)}): <b>${esc(e.result)}</b></div>`; break;
      case "shelf_stale":
        d.className = "ev librarian"; d.innerHTML = `<div class="h">Shelf facts withheld (probe failed): ${esc((e.ids || []).join(", "))}</div>`; break;
      case "confirm":
        d.className = "ev confirm";
        d.innerHTML = `<div class="h"><b>Run ${esc(e.name)}?</b></div><pre>${esc(e.args.command ?? JSON.stringify(e.args, null, 2))}</pre>
          <div class="inline"><button class="brass" data-ok="1">Run it</button><button class="iron" data-ok="0">Decline</button></div>`;
        d.querySelectorAll("button").forEach(b => b.onclick = async () => {
          d.querySelectorAll("button").forEach(x => x.disabled = true);
          await api(`/api/ledger/jobs/${current.id}/confirm`, { ok: b.dataset.ok === "1" }).catch(err => toast(err.message));
        }); break;
      case "confirmed": case "declined":
        d.className = "ev librarian"; d.innerHTML = `<div class="h">${esc(e.name)}: ${esc(e.event)}</div>`;
        document.querySelectorAll(".ev.confirm button").forEach(x => x.disabled = true); break;
      case "auto_ok":
        d.className = "note"; d.textContent = "↳ read-only: ran without asking"; break;
      case "done": {
        const w = e.write || {};
        d.className = "ev done";
        d.innerHTML = `<div class="h"><b>Done: ${esc(e.status)}</b></div><pre></pre>
          <div class="note">${esc(e.job_id)} · ${esc(e.steps)} steps · ${esc(e.tokens_in)} in / ${esc(e.tokens_out)} out · card ${e.injected_card ? "#" + esc(e.injected_card) + " → " + esc(e.card_verdict) : "none"} · write ${esc(w.outcome || "-")}${w.reason ? " (" + esc(w.reason) + ")" : ""} · verify ${e.verify_ok == null ? "-" : e.verify_ok ? "passed" : "FAILED"}</div>`;
        d.querySelector("pre").textContent = e.summary || ""; break; }
      case "error":
        d.className = "ev error"; d.innerHTML = `<div class="h"><b>Error</b></div><pre></pre>`; d.querySelector("pre").textContent = `${e.error}\n${e.trace || ""}`; break;
      default:
        d.className = "ev"; d.innerHTML = `<pre></pre>`; d.querySelector("pre").textContent = JSON.stringify(e);
    }
    return d;
  }

  async function openLive(id) {
    current = { id, live: true, after: 0 };
    const my = ++polling;
    $("#jstream").innerHTML = ""; $("#jstop").hidden = false; $("#jtitle").textContent = id; renderJobs();
    while (my === polling) {
      let r;
      try { r = await api(`/api/ledger/jobs/${id}/events?after=${current.after}`); } catch (e) { await new Promise(s => setTimeout(s, 2000)); continue; }
      for (const e of r.events) { $("#jstream").appendChild(renderEvent(e)); current.after = e.i + 1; }
      $("#jtitle").textContent = r.job.task.slice(0, 90);
      if (r.events.length) $("#jstream").scrollTop = $("#jstream").scrollHeight;
      if (r.job.state === "done" || r.job.state === "error") { $("#jstop").hidden = true; loadStatus(); break; }
    }
  }

  async function openPast(jobId) {
    polling++; current = { id: jobId, live: false };
    $("#jstop").hidden = true; $("#jtitle").textContent = jobId; renderJobs();
    const s = $("#jstream"); s.innerHTML = "";
    try {
      const tr = await api(`/api/ledger/trace/${encodeURIComponent(jobId)}`);
      for (const r of tr) {
        if (r.kind === "call") s.appendChild(renderEvent({ event: "tool", step: r.step, name: r.tool, exit: r.exit, args: r.args, read: !!r.read_id, output: r.output, t: "" }));
        else if (r.kind === "fork") s.appendChild(renderEvent({ event: "librarian", phase: "fork", result: `abandoned at step ${r.at_step}: ${r.reason}` }));
        else if (r.kind === "finish") s.appendChild(renderEvent({ event: "librarian", phase: "finish", result: `${r.status} (evidence step ${r.evidence_step ?? "-"})` }));
      }
      if (!tr.length) s.innerHTML = `<div class="note">Empty trace.</div>`;
    } catch (e) { s.innerHTML = `<div class="note">No trace for ${esc(jobId)}.</div>`; }
  }

  $("#jobform").addEventListener("submit", async e => {
    e.preventDefault();
    $("#jrun").disabled = true; $("#jhint").textContent = "";
    try {
      const j = await api("/api/ledger/jobs", { task: $("#jtask").value, target: $("#jtarget").value, approve: $("#japprove").value,
        talker: $("#jtalker").value, kind: $("#jkind").value, artifact: $("#jartifact").value, error: $("#jerror").value, verify: $("#jverify").value });
      await loadStatus(); openLive(j.id);
    } catch (err) { $("#jhint").textContent = err.message; }
    finally { $("#jrun").disabled = false; }
  });
  $("#jstop").addEventListener("click", () => current && api(`/api/ledger/jobs/${current.id}/stop`, {}).catch(e => toast(e.message)));

  async function loadCards() {
    let cards; try { cards = await api("/api/ledger/cards"); } catch (e) { $("#cardlist").innerHTML = `<div class="note">${esc(e.message)}</div>`; return; }
    $("#cardlist").innerHTML = cards.map(c => `<div class="cardbox"><b>#${esc(c.id)}</b> <span class="note">${esc(c.card.type || "card")} · ${esc(c.hits)} hits · ${esc(c.bytes)} B</span>
      <div class="key">${esc(c.key)}</div><ol>${(c.card.route || []).map(s => `<li>${esc(s)}</li>`).join("")}</ol>
      ${c.card.dead_fork ? `<div class="note">Dead end: ${esc(c.card.dead_fork.path)}</div>` : ""}</div>`).join("")
      || `<div class="note">None yet. In shadow mode cards are only counted, never stored.</div>`;
  }
  async function loadReport() { $("#reporttxt").textContent = "…"; try { $("#reporttxt").textContent = (await api("/api/ledger/report")).text; } catch (e) { $("#reporttxt").textContent = e.message; } }
  async function sandboxState() {
    $("#sbstate").textContent = "checking…";
    try { const r = await api("/api/ledger/sandbox/status", {}); $("#sbstate").textContent = r.ok ? r.out.replace("status: ", "sandbox: ") : r.out; }
    catch (e) { $("#sbstate").textContent = e.message; }
  }
  $("#sbcheck").addEventListener("click", sandboxState);
  $("#sbreset").addEventListener("click", e => {
    const b = e.currentTarget;
    if (b.dataset.armed !== "1") { b.dataset.armed = "1"; b.textContent = "Sure? Press again"; setTimeout(() => { b.dataset.armed = ""; b.textContent = "Reset to clean"; }, 4000); return; }
    b.dataset.armed = ""; b.disabled = true; b.textContent = "Resetting…";
    api("/api/ledger/sandbox/reset", {}).then(r => toast(r.ok ? "Sandbox rolled back to clean and started." : r.out)).catch(err => toast(err.message))
      .finally(() => { b.disabled = false; b.textContent = "Reset to clean"; sandboxState(); });
  });

  loadStatus();
  setInterval(() => { if (!current || !current.live) loadStatus(); }, 15000);
})();
