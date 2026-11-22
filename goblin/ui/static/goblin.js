const $ = s => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs = {}, parent) => { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; };
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
let toastT;
function toast(t) { const n = $("#toast"); n.textContent = t; n.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(() => n.classList.remove("show"), 2600); }

/* ---------- the useless machinery (it does nothing; the goblin insists) ---------- */
function gearPath(cx, cy, r, teeth, depth) {
  let d = ""; const n = teeth * 4;
  for (let i = 0; i <= n; i++) {
    const a = (i / n) * Math.PI * 2, out = (i % 4 === 1 || i % 4 === 2);
    const rr = out ? r : r - depth;
    d += (i ? "L" : "M") + (cx + rr * Math.cos(a)).toFixed(1) + " " + (cy + rr * Math.sin(a)).toFixed(1);
  }
  return d + "Z";
}
const LEDS = [];
function machinery() {
  const g = $("#gearsvg");
  const df = el("defs", {}, g);
  const pg = el("linearGradient", { id: "pipe", x1: 0, y1: 0, x2: 0, y2: 1 }, df);
  [["0", "#4a5254"], [".45", "#1e2324"], ["1", "#050606"]].forEach(([o, c]) => el("stop", { offset: o, "stop-color": c }, pg));
  const og = el("radialGradient", { id: "orbg" }, df);
  [["0", "#fff0b0"], [".35", "#f7ba0d"], [".7", "#f5651f"], ["1", "#5a1a08"]].forEach(([o, c]) => el("stop", { offset: o, "stop-color": c }, og));
  // gears behind
  [[150, 30, 26, 12], [205, 86, 20, 10], [330, 22, 22, 10], [470, 84, 18, 9]].forEach(([x, y, r, t], i) =>
    el("path", { d: gearPath(x, y, r, t, 5), class: "gear" + (i % 2 ? " rev" : ""), stroke: "#000", "stroke-width": 1.5 }, g));
  // main pipe with flanges
  el("rect", { x: 0, y: 50, width: 520, height: 14, fill: "url(#pipe)" }, g);
  [70, 180, 300, 440].forEach(x => el("rect", { x: x - 4, y: 46, width: 8, height: 22, rx: 2, fill: "#6e5434", stroke: "#000" }, g));
  // antenna towers
  [[18, 0], [502, 1]].forEach(([x]) => {
    el("line", { x1: x, y1: 4, x2: x, y2: 30, stroke: "#3a4041", "stroke-width": 2 }, g);
    for (let k = 0; k < 6; k++) el("ellipse", { cx: x, cy: 34 + k * 9, rx: 13 - k * .6, ry: 3.6, fill: "#1e2324", stroke: "#4a5254" }, g);
    const tip = el("circle", { cx: x, cy: 4, r: 3, class: "led" }, g); LEDS.push(tip);
  });
  // lantern cages with ember orbs
  [[66, ""], [452, " b"]].forEach(([x, c]) => {
    el("rect", { x: x - 20, y: 22, width: 40, height: 52, rx: 6, fill: "#0c0e0e", stroke: "#4a5254", "stroke-width": 2 }, g);
    el("circle", { cx: x, cy: 48, r: 15, fill: "url(#orbg)", class: "orb" + c }, g);
    for (let k = -2; k <= 2; k++) el("line", { x1: x + k * 8, y1: 24, x2: x + k * 8, y2: 72, stroke: "#2b3132", "stroke-width": 2.5 }, g);
    el("rect", { x: x - 24, y: 18, width: 48, height: 7, rx: 2, fill: "#3a4041" }, g);
    el("rect", { x: x - 24, y: 72, width: 48, height: 7, rx: 2, fill: "#3a4041" }, g);
  });
  // copper coil
  let d = "M118 18"; for (let k = 0; k < 9; k++) d += ` L132 ${22 + k * 8} L118 ${26 + k * 8}`;
  el("path", { d, fill: "none", stroke: "#b0643a", "stroke-width": 3, "stroke-linejoin": "round" }, g);
  // the tick counter (counts nothing)
  el("rect", { x: 196, y: 30, width: 164, height: 44, rx: 8, fill: "#0a0b0a", stroke: "#6e5434", "stroke-width": 2 }, g);
  el("text", { x: 278, y: 42, "text-anchor": "middle", "font-size": 8, fill: "#877968", "letter-spacing": 3, "font-family": "Alegreya Sans, sans-serif" }, g).textContent = "TICKS";
  const nx = el("text", { x: 278, y: 64, "text-anchor": "middle", class: "nixie", id: "ticks" }, g);
  let ticks = 2444923608; nx.textContent = ticks;
  setInterval(() => { ticks += 1 + Math.floor(Math.random() * 7); nx.textContent = ticks; }, 140);
  // jittery little gauge
  el("circle", { cx: 402, cy: 58, r: 20, fill: "#d9ccb0", stroke: "#6e5434", "stroke-width": 4 }, g);
  for (let k = 0; k < 9; k++) { const a = (-120 + k * 30) * Math.PI / 180; el("line", { x1: 402 + 14 * Math.sin(a), y1: 58 - 14 * Math.cos(a), x2: 402 + 18 * Math.sin(a), y2: 58 - 18 * Math.cos(a), stroke: "#2a1d0c" }, g); }
  const nd = el("g", { class: "jitter" }, g); el("line", { x1: 402, y1: 58, x2: 402, y2: 42, stroke: "#c2241a", "stroke-width": 2 }, nd);
  el("circle", { cx: 402, cy: 58, r: 3, fill: "#2a1d0c" }, g);
  setInterval(() => nd.style.transform = `rotate(${-60 + Math.random() * 120}deg)`, 700);
  // rows of indicator lamps
  for (let k = 0; k < 10; k++) LEDS.push(el("circle", { cx: 160 + k * 21, cy: 96, r: 3.2, class: "led" }, g));
  for (let k = 0; k < 6; k++) LEDS.push(el("circle", { cx: 300 + k * 16, cy: 10, r: 3, class: "led" }, g));
}
machinery();
(() => {
  const g = $("#under");
  el("rect", { x: 0, y: 14, width: 1320, height: 18, fill: "url(#pipe)" }, g);
  el("rect", { x: 0, y: 44, width: 1320, height: 10, fill: "url(#pipe)" }, g);
  [120, 340, 980, 1200].forEach(x => {
    el("rect", { x: x - 6, y: 8, width: 12, height: 30, rx: 2, fill: "#6e5434", stroke: "#000" }, g);
    el("circle", { cx: x, cy: 0, r: 0 }, g);
  });
  // valve wheels
  [[230, 23], [1090, 23]].forEach(([x, y]) => {
    el("circle", { cx: x, cy: y, r: 18, fill: "none", stroke: "#b0643a", "stroke-width": 4 }, g);
    el("path", { d: `M${x - 18} ${y} H${x + 18} M${x} ${y - 18} V${y + 18}`, stroke: "#b0643a", "stroke-width": 3 }, g);
    el("circle", { cx: x, cy: y, r: 4, fill: "#e3c48e" }, g);
  });
  // ember window in the middle, like the furnace door in the wallpaper
  el("rect", { x: 610, y: 0, width: 100, height: 140, rx: 50, fill: "#0a0b0a", stroke: "#4a5254", "stroke-width": 5 }, g);
  el("rect", { x: 622, y: 12, width: 76, height: 118, rx: 38, fill: "#2a1206" }, g);
  el("circle", { cx: 660, cy: 104, r: 22, fill: "url(#orbg)", class: "orb" }, g);
  el("path", { d: gearPath(660, 52, 18, 10, 4), class: "gear", fill: "#b0643a", stroke: "#000" }, g);
  for (let k = 0; k < 4; k++) { const l = el("circle", { cx: 640 + k * 13, cy: 22, r: 3, class: "led" }, g); LEDS.push(l); }
  // dangling copper chains
  [[80, 90], [180, 120], [430, 70], [520, 110], [800, 100], [890, 75], [1150, 115], [1260, 85]].forEach(([x, len]) => {
    const c = el("g", { class: "chain", style: `animation-delay:${-x % 5}s` }, g);
    for (let y = 30; y < 30 + len; y += 9) el("ellipse", { cx: x, cy: y, rx: (y / 9) % 2 < 1 ? 3 : 1.4, ry: 5, fill: "none", stroke: "#b0643a", "stroke-width": 1.6 }, c);
  });
  setInterval(() => { const v = $("#vent"); v.classList.add("venting"); setTimeout(() => v.classList.remove("venting"), 2800); }, 9000);
})();
document.querySelectorAll(".plate h2").forEach(h => {
  const box = document.createElementNS(NS, "svg"); box.setAttribute("viewBox", "0 0 70 10"); box.setAttribute("class", "h2leds"); box.setAttribute("aria-hidden", "true");
  for (let k = 0; k < 5; k++) LEDS.push(el("circle", { cx: 6 + k * 14, cy: 5, r: 3.2, class: "led" }, box));
  h.appendChild(box);
});
setInterval(() => { const l = LEDS[Math.floor(Math.random() * LEDS.length)]; l.classList.toggle("on"); }, 90);

/* ---------- GPU gauge ---------- */
(() => {
  const t = $("#ticks");
  for (let i = 0; i <= 16; i += 2) {
    const a = Math.PI * (1 - i / 16), x1 = 75 + 52 * Math.cos(a), y1 = 78 - 52 * Math.sin(a), x2 = 75 + 44 * Math.cos(a), y2 = 78 - 44 * Math.sin(a);
    el("line", { x1, y1, x2, y2, stroke: "#b08a52", "stroke-width": 1.5 }, t);
  }
})();

const pt = (a, r) => [200 + r * Math.sin(a * Math.PI / 180), 200 - r * Math.cos(a * Math.PI / 180)];

function defs(svg) {
  const d = el("defs", {}, svg);
  const lin = (id, stops) => { const g = el("radialGradient", { id, cx: "50%", cy: "50%", r: "55%" }, d); stops.forEach(([o, c]) => el("stop", { offset: o, "stop-color": c }, g)); };
  lin("gIdle", [["40%", "#2b3132"], ["100%", "#161a1b"]]);
  lin("gHot", [["30%", "#ffd36a"], ["70%", "#f5651f"], ["100%", "#5a1a08"]]);
  lin("gCool", [["30%", "#fbf7ec"], ["70%", "#a8a99c"], ["100%", "#2b3132"]]);
  const f = el("filter", { id: "glow", x: "-30%", y: "-30%", width: "160%", height: "160%" }, d);
  el("feGaussianBlur", { stdDeviation: 7, result: "b" }, f);
  const m = el("feMerge", {}, f); el("feMergeNode", { in: "b" }, m); el("feMergeNode", { in: "SourceGraphic" }, m);
  const bz = el("linearGradient", { id: "bezel", x1: 0, y1: 0, x2: 0, y2: 1 }, d);
  el("stop", { offset: "0", "stop-color": "#e8cd98" }, bz); el("stop", { offset: ".5", "stop-color": "#7d6040" }, bz); el("stop", { offset: "1", "stop-color": "#2e241a" }, bz);
}

function buildDial(key) {
  const svg = $("#dial" + key), list = MODELS[key], n = list.length, step = 360 / n, R = 168, r0 = 78;
  defs(svg);
  el("circle", { cx: 200, cy: 200, r: 196, fill: "url(#bezel)" }, svg);
  el("circle", { cx: 200, cy: 200, r: 184, fill: "#0c0e0e" }, svg);
  for (let i = 0; i < 60; i++) { const [x1, y1] = pt(i * 6, 190), [x2, y2] = pt(i * 6, i % 5 ? 186 : 182); el("line", { x1, y1, x2, y2, stroke: "#2a1d0c", "stroke-width": i % 5 ? 1 : 2 }, svg); }
  for (let i = 0; i < 8; i++) { const [x, y] = pt(i * 45 + 22.5, 190); const j = el("circle", { cx: x, cy: y, r: 4, class: "led", stroke: "#2e241a" }, svg); LEDS.push(j); }
  for (let i = 1; i <= 28; i++) { const [x, y] = pt(i * 360 / 28, 176.5); el("text", { x, y: y + 3, "text-anchor": "middle", "font-size": 8.5, fill: "#877968", "font-family": "Special Elite, monospace" }, svg).textContent = i; }
  const rotor = el("g", { class: "rotor" }, svg);
  const st = { key, list, step, rot: 0, sel: null, state: {}, slices: {}, labels: [] };
  list.forEach((m, i) => {
    const a = i * step, a0 = a - step / 2 + 1.2, a1 = a + step / 2 - 1.2, large = step > 180 ? 1 : 0;
    const [x0, y0] = pt(a0, R), [x1, y1] = pt(a1, R), [x2, y2] = pt(a1, r0), [x3, y3] = pt(a0, r0);
    const p = el("path", { d: `M${x0} ${y0} A${R} ${R} 0 ${large} 1 ${x1} ${y1} L${x2} ${y2} A${r0} ${r0} 0 ${large} 0 ${x3} ${y3} Z`,
      class: "slice " + (m.empty ? "s-empty" : "s-idle"), tabindex: m.empty ? -1 : 0, role: "button", "aria-label": `${m.full}: ${m.use}` }, rotor);
    st.slices[m.id] = p; st.state[m.id] = m.empty ? "empty" : "idle";
    const [lx, ly] = pt(a, (R + r0) / 2 + 4);
    const g = el("g", { transform: `translate(${lx} ${ly})` }, rotor);
    const inner = el("g", { class: "lbl" }, g);
    el("text", { class: "slice-txt", "text-anchor": "middle", y: 2 }, inner).textContent = m.name;
    el("text", { class: "slice-sub", "text-anchor": "middle", y: 18 }, inner).textContent = m.use;
    st.labels.push(inner); (st.lbl = st.lbl || {})[m.id] = inner;
    const pick = () => { if (m.empty) return toast("Empty berth. Nothing lives here yet."); if (key === "F") dials.F.userTurned = true; select(key, m.id); };
    p.addEventListener("click", pick);
    p.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
  });
  // pointer notch at 12 o'clock
  el("path", { d: "M186 4 L214 4 L200 30 Z", fill: "url(#bezel)", stroke: "#2a1d0c", "stroke-width": 2 }, svg);
  // fixed hub
  el("circle", { cx: 200, cy: 200, r: 74, fill: "#101313", stroke: "url(#bezel)", "stroke-width": 6 }, svg);
  st.hName = el("text", { class: "hub-name", x: 200, y: 196 }, svg);
  st.hUse = el("text", { class: "hub-use", x: 200, y: 215 }, svg);
  st.hState = el("text", { class: "hub-state", x: 200, y: 238 }, svg);
  st.hName.textContent = "—"; st.hUse.textContent = "turn the dial"; st.hState.textContent = key === "F" ? "fire banked" : "sky quiet";
  dials[key] = st;
}



/* ====================== live wiring ====================== */
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (path, body) => {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || (typeof j.detail === "string" ? j.detail : j.detail?.message) || `error ${r.status}`);
  return j;
};
const store = { get(k, d) { try { const v = localStorage.getItem(k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
                set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} } };
const ROOM = "/room";   // same origin: the launcher passes room calls through
const roomApi = async (path, body) => {
  const r = await fetch(ROOM + path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `room error ${r.status}`);
  return j;
};

/* ---------- GPU gauge ---------- */
let gpu = { loaded: [], loading: [] };
function setVram(used, total) {
  const t = total || 16, u = Math.max(0, Math.min(used ?? 0, t));
  $("#needle").style.transform = `rotate(${-90 + (u / t) * 180}deg)`;
  $("#vramtxt").textContent = used == null ? `– / ${t} GB` : `${u.toFixed(1)} / ${t} GB`;
}

/* ---------- dials ---------- */
let MODELS = { F: [], A: [] };
const dials = {};
let modeOn = store.get("furnaceMode", false);
const furnaceModel = m => (m.mode && modeOn ? m.mode.model : m.model);
const pickAgent = m => (m.mode && modeOn ? m.mode.agent : m.agent);
const pickSeat = m => (m.mode && modeOn ? m.mode.seat : m.seat);

function setState(st, id, s) {
  if (st.state[id] === s) return;
  st.state[id] = s;
  st.slices[id].setAttribute("class", "slice s-" + s);
  st.lbl[id].classList.toggle("lit", ["loading", "ready", "thinking"].includes(s));
  hub(st);
  syncAmbience();
}
function hub(st) {
  const m = st.list.find(x => x.id === st.sel);
  if (!m) { st.hName.textContent = "—"; st.hUse.textContent = "turn the dial"; st.hState.textContent = st.key === "F" ? "fire banked" : "sky quiet"; return; }
  st.hName.textContent = m.name;
  st.hUse.textContent = m.mode && modeOn ? m.mode.label : m.use;
  const s = st.state[m.id];
  const vr = (gpu.loaded.find(x => x.name.split(":")[0] === furnaceModel(m).split(":")[0]) || {}).vram;
  st.hState.textContent = st.key === "F"
    ? ({ loading: "stoking…", ready: vr ? `burning · ${(vr / 1073741824).toFixed(1)} GB` : "burning", thinking: "working…" }[s] || "cold")
    : ({ ready: "aloft · ready", thinking: "working…" }[s] || "idle");
}
function rotateTo(key, id) {
  const st = dials[key], i = st.list.findIndex(m => m.id === id);
  if (i < 0) return;
  const diff = (((-i * st.step) - st.rot) % 360 + 540) % 360 - 180;
  st.rot += diff;
  $("#dial" + key).querySelector(".rotor").style.transform = `rotate(${st.rot}deg)`;
  st.labels.forEach(l => l.style.transform = `rotate(${-st.rot}deg)`);
  st.sel = id; hub(st);
  if (key === "F") $("#modebox").hidden = !st.list.find(m => m.id === id)?.mode;
}
async function select(key, id) {
  rotateTo(key, id);
  if (key === "A") {
    const st = dials.A; st.list.forEach(m => !m.empty && setState(st, m.id, m.id === id ? "ready" : "idle"));
    store.set("aether", id); return;
  }
  const st = dials.F, m = st.list.find(x => x.id === id);
  if (st.state[id] === "ready" && isLoaded(furnaceModel(m))) return;   // already burning
  st.list.forEach(x => { if (x.id !== id && st.state[x.id] !== "idle") setState(st, x.id, "unloading"); });
  setState(st, id, "loading"); vent(true);
  try { await api("/api/furnace/load", { model: furnaceModel(m) }); toast(`Stoking the furnace with ${m.name}. Anything else on the GPU is unloaded first.`); }
  catch (e) { toast(e.message); setState(st, id, "idle"); vent(false); }
}
const norm = n => (n.includes(":") ? n : n + ":latest");
const isLoaded = model => gpu.loaded.some(x => norm(x.name) === norm(model));
function syncFurnace() {
  const st = dials.F; if (!st) return;
  let any = false;
  st.list.forEach(m => {
    const model = furnaceModel(m);
    const s = gpu.loading.includes(model) ? "loading" : isLoaded(model) ? (roomBusy && seated.has(pickSeat(m)) ? "thinking" : "ready") : "idle";
    if (s !== "idle") any = true;
    if (s === "idle" && st.state[m.id] === "unloading") setTimeout(() => setState(st, m.id, "idle"), 900);
    else setState(st, m.id, s);
    if (s !== "idle" && st.sel !== m.id && !st.userTurned) rotateTo("F", m.id);
  });
  vent(st.list.some(m => st.state[m.id] === "loading"));
  hub(st);
  return any;
}
function vent(on) { ["#steamL", "#steamR"].forEach(s => $(s).classList.toggle("venting", on)); }
function syncAmbience() {
  const busy = Object.values(dials).some(d => Object.values(d.state).some(s => s === "loading" || s === "thinking"));
  document.body.classList.toggle("working", busy);
  mood();
}
async function pollGpu() {
  try { gpu = await api("/api/gpu"); } catch (e) { return; }
  setVram(gpu.used_gb, gpu.total_gb);
  syncFurnace();
}
$("#modechk").checked = modeOn;
$("#modechk").addEventListener("change", e => {
  modeOn = e.target.checked; store.set("furnaceMode", modeOn);
  const st = dials.F, m = st.list.find(x => x.id === st.sel);
  hub(st);
  if (m && m.mode) select("F", m.id);   // load the variant now that the owner switched it
});

document.querySelectorAll("[data-act]").forEach(b => b.addEventListener("click", async () => {
  const st = dials[b.dataset.dial], m = st.list.find(x => x.id === st.sel);
  if (!m) return toast("Turn the dial to a model first.");
  if (b.dataset.act === "term") {
    try { await api("/api/agents/launch", { id: pickAgent(m), cwd: $("#cwd").value.trim() || null }); toast(`Terminal opened with ${m.name}${m.mode && modeOn ? " (" + m.mode.label + ")" : ""}.`); }
    catch (e) { toast(e.message); }
  } else {
    const seat = pickSeat(m); seated.add(seat); store.set("seats", [...seated]); renderSeats();
    toast(`${m.name} will sit in the next room you open.`);
  }
}));
$("#cwd").value = store.get("cwd", ""); $("#cwd").addEventListener("change", e => store.set("cwd", e.target.value.trim()));

/* ---------- the counting room (through /room on this server) ---------- */
let seatInfo = {}, seated = new Set(store.get("seats", [])), roomId = null, roomBusy = false, shown = new Set();
function renderSeats() {
  const chips = [`<span class="seat" style="color:#d9ccb0;border-color:#d9ccb066">You</span>`];
  for (const id of seated) {
    const s = seatInfo[id] || { label: id, color: "#877968" };
    chips.push(`<button class="seat" data-seat="${esc(id)}" title="Remove from the next room" style="color:${esc(s.color)};border-color:${esc(s.color)}66">${esc(s.label)} ×</button>`);
  }
  $("#seats").innerHTML = chips.join("");
}
$("#seats").addEventListener("click", e => {
  const b = e.target.closest("[data-seat]"); if (!b) return;
  seated.delete(b.dataset.seat); store.set("seats", [...seated]); renderSeats();
});
async function loadRoomList() {
  try {
    const st = await roomApi("/api/status");
    (st.seats || []).forEach(s => seatInfo[s.id] = s); renderSeats();
    const d = await roomApi("/api/rooms");
    const rows = (Array.isArray(d) ? d : d.rooms || []).slice(0, 30);
    const want = roomId || store.get("room", null) || (rows[0] || {}).id;
    $("#roomsel").innerHTML = rows.map(r => {
      const who = (r.seats || []).filter(s => s !== "you").map(s => (seatInfo[s] || {}).label || s).join(", ");
      const name = r.bridge ? "Phone bridge" : (r.brief || "Room").slice(0, 32);
      return `<option value="${esc(r.id)}">${esc(name)} · ${esc(who)}</option>`;
    }).join("");
    if (want && rows.some(r => r.id === want)) { $("#roomsel").value = want; if (want !== roomId) openRoom(want); }
  } catch (e) { $("#screen").innerHTML = `<div class="msg"><b style="color:#877968">The Goblin</b><p>The room isn't answering. Is goblin.service running?</p></div>`; }
}
function openRoom(id) {
  roomId = id; store.set("room", id); shown = new Set(); $("#screen").innerHTML = "";
  pollRoom();
}
$("#roomsel").addEventListener("change", e => openRoom(e.target.value));
$("#roomnew").addEventListener("click", async () => {
  if (!seated.size) return toast("Seat someone first: turn a dial and press Seat in room.");
  try { const r = await roomApi("/api/rooms", { brief: "", seats: [...seated], mode: "sequential" }); roomId = null; await loadRoomList(); $("#roomsel").value = r.id; openRoom(r.id); toast("New room open."); }
  catch (e) { toast(e.message); }
});
async function pollRoom() {
  if (!roomId) return;
  let r; try { r = await roomApi(`/api/rooms/${encodeURIComponent(roomId)}`); } catch (e) { return; }
  const scr = $("#screen"), atBottom = scr.scrollHeight - scr.scrollTop - scr.clientHeight < 60;
  for (const m of r.messages || []) {
    if (shown.has(m.id) || m.role === "system") continue;
    shown.add(m.id);
    const d = document.createElement("div"); d.className = "msg";
    const color = m.author_id === "you" ? "#d9ccb0" : (m.color || (seatInfo[m.author_id] || {}).color || "#877968");
    d.innerHTML = `<b style="color:${esc(color)}">${esc(m.author_label || m.author_id)}</b><p></p>`;
    d.querySelector("p").textContent = m.text || "";
    scr.appendChild(d);
  }
  const busy = r.status === "thinking";
  let w = $("#working");
  if (busy && !w) { w = document.createElement("div"); w.id = "working"; w.className = "msg"; w.innerHTML = `<p class="typing">gears turning</p>`; scr.appendChild(w); }
  if (!busy && w) w.remove();
  if (busy && w) scr.appendChild(w);
  if (busy !== roomBusy) { roomBusy = busy; syncFurnace(); }
  $("#roommode").textContent = r.mode || "sequential";
  if (atBottom) scr.scrollTop = scr.scrollHeight;
}
$("#composer").addEventListener("submit", async e => {
  e.preventDefault();
  const v = $("#say").value.trim(); if (!v || !roomId) return;
  $("#say").value = "";
  try { await roomApi(`/api/rooms/${encodeURIComponent(roomId)}/message`, { text: v }); pollRoom(); }
  catch (err) { toast(err.message); $("#say").value = v; }
});

/* ---------- vault door ---------- */
let vault = { reachable: false }, vaultOpen = false, pin = "", expires = null;
const fmt = n => `${Math.floor(n / 60)}:${String(n % 60).padStart(2, "0")}`;
function showPad(on) {
  $("#keypad").hidden = !on; $("#doorbtn").setAttribute("aria-expanded", on);
  if (on) { pin = ""; $("#pin").value = ""; lamps(); $("#kphint").textContent = "6 digits · Enter to turn"; setTimeout(() => $("#pin").focus(), 30); }
}
function lamps() { [...$("#lamps").children].forEach((l, i) => l.classList.toggle("on", i < pin.length)); }
function setOpen(open) {
  if (open === vaultOpen) return;
  vaultOpen = open;
  $("#vault").classList.toggle("open", open);
  $("#wheel").style.transform = `rotate(${open ? 540 : 0}deg)`;
  $("#extend").hidden = !open;
  $("#workshop").hidden = !open;
  if (open) { loadWorkshop(); } else { closeConfirm(); showTube([]); }
  mood();
}
function tickVault() {
  if (!vault.reachable) { $("#vstate").textContent = "Missing"; $("#vsub").textContent = "goblin-vault isn't answering"; return; }
  if (!vault.initialized) { $("#vstate").textContent = "Empty"; $("#vsub").textContent = "no vault yet: run goblin vault init"; return; }
  if (vault.locked_out) { $("#vstate").textContent = "Barred"; $("#vsub").textContent = "too many wrong PINs · recover with a USB key"; return; }
  $("#vstate").textContent = vaultOpen ? "Open" : "Locked";
  if (vaultOpen && expires) { const left = Math.max(0, Math.round((expires - Date.now()) / 1000)); $("#vsub").textContent = `locks itself in ${fmt(left)}`; }
  else if (!vaultOpen) $("#vsub").textContent = "Turn the wheel to open";
}
async function pollVault() {
  try { vault = await api("/api/vault/status"); } catch (e) { vault = { reachable: false }; }
  expires = vault.expires_at ? new Date(vault.expires_at).getTime() : null;
  setOpen(!!(vault.reachable && vault.unlocked));
  $("#doorbtn").setAttribute("aria-label", vaultOpen ? "Vault door: open. Press to lock." : "Vault door: locked. Press to enter your PIN.");
  tickVault();
  if (vaultOpen) pollApprovals();
}
setInterval(tickVault, 1000);
async function submitPin() {
  if (pin.length < 6) { $("#kphint").textContent = `${6 - pin.length} more digit${pin.length === 5 ? "" : "s"}`; return; }
  try {
    await api("/api/vault/unlock", { pin });
    showPad(false); toast("Click-clack-CLUNK. The vault swings open."); pollVault();
  } catch (err) {
    const L = $("#lamps"), v = $("#vault");
    L.classList.add("bad"); v.classList.add("shake");
    $("#kphint").textContent = err.message.slice(0, 90);
    setTimeout(() => { L.classList.remove("bad"); v.classList.remove("shake"); pin = ""; $("#pin").value = ""; lamps(); $("#pin").focus(); }, 700);
    pollVault();
  }
}
$("#doorbtn").addEventListener("click", async () => {
  if (vaultOpen) { try { await api("/api/vault/lock", {}); toast("Vault locked."); } catch (e) { toast(e.message); } pollVault(); return; }
  if (!vault.reachable || !vault.initialized) return toast($("#vsub").textContent);
  showPad($("#keypad").hidden);
});
$("#pin").addEventListener("input", e => {
  const v = e.target.value.replace(/\D/g, "").slice(0, 6); e.target.value = v; pin = v; lamps();
  if (v.length === 6) setTimeout(submitPin, 150);
});
$("#pinform").addEventListener("submit", e => { e.preventDefault(); submitPin(); });
$("#keypad").addEventListener("keydown", e => { if (e.key === "Escape") { showPad(false); $("#doorbtn").focus(); } });
document.addEventListener("click", e => { if (!$("#keypad").hidden && !$("#vault").contains(e.target)) showPad(false); });
$("#extend").addEventListener("click", () => askPin("Wind the spring for 15 more minutes?", async p => {
  await api("/api/vault/extend", { minutes: 15, pin: p });
  const w = $("#wheel"); w.style.transition = "transform .6s cubic-bezier(.3,1.4,.5,1)"; w.style.transform = "rotate(630deg)";
  setTimeout(() => { w.style.transform = "rotate(540deg)"; setTimeout(() => w.style.transition = "", 650); }, 600);
  toast("Spring wound. 15 more minutes."); pollVault();
}));

/* ---------- pneumatic tube: real approvals ---------- */
let pending = [];
async function pollApprovals() {
  let a; try { a = await api("/api/vault/approvals"); } catch (e) { return; }
  showTube(a.pending || []);
}
function showTube(list) {
  const ids = list.map(p => p.id).join(",");
  pending = list; mood();
  if ($("#tube").dataset.ids === ids) return;
  $("#tube").dataset.ids = ids;
  if (!list.length) {
    $("#tube").innerHTML = `<div class="empty-tube">${vaultOpen ? "The tube is quiet. Requests drop in here when an agent wants into a lab host." : "The vault is locked. Agents can't get a yes until you open it."}</div>`;
    return;
  }
  const p = list[0];
  $("#tube").innerHTML = `<div class="capsule" id="cap"><div class="who">${esc(p.agent)} → ${esc(p.target_id)} · ${esc(p.kind)}${list.length > 1 ? ` · +${list.length - 1} more` : ""}</div>
    <code>${esc(p.summary)}</code>
    <div class="inline capin"><input id="appin" type="password" inputmode="numeric" maxlength="6" placeholder="PIN" autocomplete="off" aria-label="Vault PIN to approve">
      <label class="remember"><input type="checkbox" id="apremember"> remember this session</label></div>
    <div class="dialbtns" style="justify-content:flex-start"><button class="brass" id="ap">Stamp it</button><button class="iron" id="dn">Chuck it back</button></div>
    <div class="seal">OK!<br>⚙</div></div>`;
  const decide = async ok => {
    try {
      await api(`/api/vault/approvals/${p.id}`, { approve: ok, pin: $("#appin").value, remember_minutes: $("#apremember").checked ? 15 : 0 });
      if (ok) $("#cap").classList.add("sealed");
      setTimeout(() => { $("#cap")?.classList.add("gone"); toast(ok ? "Stamped! Off it goes to run." : "Chucked back up the tube."); }, ok ? 600 : 0);
      setTimeout(() => { $("#tube").dataset.ids = ""; pollApprovals(); }, 1300);
    } catch (e) { toast(e.message); }
  };
  $("#ap").onclick = () => decide(true);
  $("#dn").onclick = () => decide(false);
  $("#appin").addEventListener("keydown", e => { if (e.key === "Enter") decide(true); });
}

/* ---------- levers: the real stack ---------- */
let stack = {};
async function pollStack() {
  try { stack = await api("/api/stack/status"); } catch (e) { return; }
  const o = $("#lvOllama"); o.classList.toggle("on", !!stack.ollama_gpu); $("#ollamaState").textContent = stack.ollama_gpu ? "Running" : "Stopped";
  const s = $("#lvStack"); if (!s.classList.contains("armed")) { s.classList.toggle("on", stack.power === "on"); $("#stackState").textContent = (stack.power === "on" ? "On" : "Off") + " · lift guard"; }
  const light = (on, label) => `<span><i class="${on ? "on" : ""}"></i>${label}</span>`;
  const lights = [...Object.entries(stack.ollama_other || {}).map(([n, on]) => light(on, `Ollama ${n}`)),
                  ...(stack.webui_url ? [light(stack.webui, "Open WebUI")] : []),
                  ...Object.entries(stack.watch || {}).map(([n, on]) => light(on, n))];
  $("#stacklights").innerHTML = lights.join("") +
    (stack.webui ? `<a class="linkbtn" href="${esc(stack.webui_url)}" target="_blank" rel="noopener">WebUI ↗</a>` : "");
}
async function lever(act, msg) {
  try { const r = await api(`/api/stack/${act}`, {}); toast(msg || (r.log || []).join(" · ") || "Done."); } catch (e) { toast(e.message); }
  pollStack(); pollGpu();
}
$("#lvOllama").addEventListener("click", () => stack.ollama_gpu ? toast("GPU Ollama is running. Bank the fire to unload, or pull the stack lever to shut it all down.") : lever("ollama", "Starting the GPU Ollama."));
$("#lvUnload").addEventListener("click", e => { const l = e.currentTarget; l.classList.add("on"); setTimeout(() => l.classList.remove("on"), 600); dials.F && (dials.F.userTurned = false); lever("unload", "Fire banked. Every model unloaded."); });
$("#lvStack").addEventListener("click", e => {
  const l = e.currentTarget;
  if (!l.classList.contains("armed")) { l.classList.add("armed"); $("#stackState").textContent = "Guard up · pull again"; setTimeout(() => { if (l.classList.contains("armed")) { l.classList.remove("armed"); pollStack(); } }, 4000); return; }
  l.classList.remove("armed"); l.classList.toggle("on");
  lever(stack.power === "on" ? "off" : "on");
});

/* ---------- the goblin's notes: the verified shelf ---------- */
function renderNotes(sh) {
  if (!sh) return;
  const rows = sh.rows || [], ok = rows.filter(r => r.state === "pass").length;
  const order = [...rows].sort((a, b) => (a.state === "pass") - (b.state === "pass"));
  $("#notes").innerHTML = order.map(r => `<li title="${esc(r.id)}${r.detail ? " · " + esc(r.detail) : ""}"><span class="${r.state === "pass" ? "ok" : "no"}">${r.state === "pass" ? "✓" : r.state === "fail" ? "✗" : "?"}</span>${esc(r.fact)}</li>`).join("");
  $("#tally").textContent = `${ok} of ${rows.length} held · poked ${new Date(sh.checked * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
}
async function loadNotes() { try { const s = await api("/api/shelf"); if (s.checked) renderNotes(s); else setTimeout(loadNotes, 6000); } catch (e) {} }
$("#recheck").addEventListener("click", async () => { $("#tally").textContent = "poking…"; try { renderNotes(await api("/api/shelf/check", {})); } catch (e) { toast(e.message); } });

/* ---------- the workshop ---------- */
document.querySelectorAll("#workshop .ws-tabs button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll("#workshop .ws-tabs button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-selected", x === b); });
  document.querySelectorAll("#workshop .ws-pane").forEach(p => p.hidden = p.dataset.pane !== b.dataset.ws);
  loadWorkshop(b.dataset.ws);
}));
let confirmFn = null;
function askPin(what, fn) { confirmFn = fn; $("#confirmWhat").textContent = what + " Enter your PIN to confirm."; $("#confirm").hidden = false; $("#confirmPin").value = ""; $("#confirmPin").focus(); }
function closeConfirm() { $("#confirm").hidden = true; confirmFn = null; }
$("#confirmNo").addEventListener("click", closeConfirm);
$("#confirmGo").addEventListener("click", async () => {
  const v = $("#confirmPin").value; if (v.length !== 6) { $("#confirmPin").focus(); return toast("Six digits, please."); }
  const fn = confirmFn; $("#confirmGo").disabled = true;
  try { await fn(v); closeConfirm(); } catch (e) { toast(e.message); $("#confirmPin").value = ""; }
  finally { $("#confirmGo").disabled = false; }
});
$("#confirmPin").addEventListener("keydown", e => { if (e.key === "Enter") $("#confirmGo").click(); if (e.key === "Escape") closeConfirm(); });
const when = t => { if (!t) return ""; const d = new Date(t); return isNaN(d) ? String(t) : d.toLocaleString([], { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); };

let keys = [];
async function loadKeys() {
  const [dv, rm] = await Promise.all([api("/api/vault/devices").catch(() => ({ devices: [] })), api("/api/vault/removable").catch(() => ({ drives: [] }))]);
  keys = dv.devices || [];
  $("#keyrows").innerHTML = keys.map(d => `<div class="row"><span class="ico">🔑</span><div class="what"><b>${esc(!d.label || d.label === "disk" ? "USB key" : d.label)}${d.revoked ? " · revoked" : ""}</b>
      <span>${esc(d.device_id)}${d.created_at ? " · enrolled " + esc(when(d.created_at)) : ""}${d.backend ? " · " + esc(d.backend) : ""}</span></div>
      ${d.revoked ? "" : `<button class="iron danger" data-revoke="${esc(d.device_id)}">Revoke</button>`}</div>`).join("") || `<div class="note">No keys enrolled yet.</div>`;
  const drives = rm.drives || [];
  $("#keydisk").innerHTML = drives.length ? drives.map(d => `<option value="${esc(d.path)}" ${d.eligible_for_wipe === false ? "disabled" : ""}>${esc(d.display_id || d.path)}${d.warning ? " (" + esc(d.warning) + ")" : ""}</option>`).join("") : `<option value="">No USB stick plugged in</option>`;
  const first = drives.find(d => d.eligible_for_wipe !== false);
  $("#keydisk").value = first ? first.path : "";
  $("#keyconfirm").placeholder = first ? `Type: WIPE ${first.path}` : "Plug in a stick first";
  $("#pairkey").innerHTML = keys.filter(d => !d.revoked).map(d => `<option value="${esc(d.device_id)}">${esc(!d.label || d.label === "disk" ? "USB key" : d.label)} · ${esc(when(d.created_at))}</option>`).join("") || `<option value="">Enroll a USB key first</option>`;
}
$("#keydisk").addEventListener("change", () => $("#keyconfirm").placeholder = `Type: WIPE ${$("#keydisk").value}`);
$("#keyrows").addEventListener("click", e => {
  const b = e.target.closest("[data-revoke]"); if (!b) return;
  askPin(`Revoke key ${b.dataset.revoke}?`, async p => { await api(`/api/vault/revoke/${b.dataset.revoke}`, { pin: p }); toast("Key revoked."); loadKeys(); });
});
$("#keyenroll").addEventListener("click", () => {
  if (!$("#keydisk").value) return toast("Plug in a USB stick first.");
  askPin(`Wipe ${$("#keydisk").value} and enroll it as a key?`, async p => {
    toast("Enrolling: the stick is being wiped and written…");
    const r = await api("/api/vault/enroll-usb", { disk_path: $("#keydisk").value, confirm: $("#keyconfirm").value.trim(), label: $("#keylabel").value.trim(), pin: p });
    toast(`Enrolled ${r.device?.device_id || r.device_id || "the key"}.`); $("#keyconfirm").value = ""; loadKeys();
  });
});

async function loadPhones() {
  const l = await api("/api/vault/phones").catch(e => ({ error: e.message }));
  const links = (l.phones || []).filter(x => !x.revoked);
  $("#phonerows").innerHTML = links.map(x => `<div class="row"><span class="ico">📱</span><div class="what"><b>${esc(x.phone_label || x.label || x.client_label || "Phone")}</b>
      <span>${esc(x.client_kind || "phone")} · linked ${esc(when(x.created_at))}${x.last_seen_at ? " · last seen " + esc(when(x.last_seen_at)) : ""}</span></div>
      <div class="btns"><input class="mpin" type="password" inputmode="numeric" maxlength="12" placeholder="new PIN" data-mpin="${esc(x.link_id || x.id)}" aria-label="New phone PIN"><button class="iron" data-setmpin="${esc(x.link_id || x.id)}">Set phone PIN</button><button class="iron danger" data-unpair="${esc(x.link_id || x.id)}">Unpair</button></div></div>`).join("")
    || `<div class="note">${l.error ? esc(l.error) : "No phones paired."}</div>`;
  phoneConn();
}
let phoneReady = false;
async function phoneConn() {
  const r = await api("/api/phone/status").catch(e => ({ phones: [], error: e.message }));
  const ready = (r.phones || []).filter(p => p.state === "device"), unauth = (r.phones || []).filter(p => p.state === "unauthorized");
  let txt, lamp = "";
  if (r.error) txt = r.error;
  else if (!r.adb) txt = r.adb_help;
  else if (!r.apk) txt = "The app package isn't built yet (android/: ./gradlew :app:assembleDebug).";
  else if (ready.length > 1) { txt = "More than one phone is plugged in. Unplug the others."; }
  else if (ready.length) { lamp = "on"; txt = `Ready: ${ready[0].model}${ready[0].app_installed ? " (app already installed: it will be updated)" : ""}.`; }
  else if (unauth.length) { lamp = "wait"; txt = "Phone found. Tap Allow on its \"Allow USB debugging?\" prompt."; }
  else txt = "No phone yet. Plug it in with USB debugging on.";
  $("#phoneconn").textContent = txt; $("#plamp").className = lamp;
  phoneReady = ready.length === 1 && !!r.apk;
  $("#pairgo").disabled = !phoneReady;
}
$("#phonerows").addEventListener("click", e => {
  const m = e.target.closest("[data-setmpin]");
  if (m) {
    const id = m.dataset.setmpin, v = document.querySelector(`[data-mpin="${CSS.escape(id)}"]`).value;
    if (!/^\d{4,12}$/.test(v)) return toast("The phone PIN is 4 to 12 digits.");
    return askPin("Give this phone a new PIN?", async p => { await api("/api/vault/phone-pin", { link_id: id, phone_pin: v, pin: p }); toast("Phone PIN changed. Use the new one on the phone."); loadPhones(); });
  }
  const b = e.target.closest("[data-unpair]"); if (!b) return;
  askPin("Unpair this phone? It stops working at once.", async p => { await api(`/api/vault/unpair/${encodeURIComponent(b.dataset.unpair)}`, { pin: p }); toast("Phone unpaired."); loadPhones(); });
});
$("#pairgo").addEventListener("click", () => {
  const mp = $("#pairmpin").value;
  if (!/^\d{4,12}$/.test(mp)) return toast("The phone PIN is 4 to 12 digits.");
  if (mp !== $("#pairmpin2").value) return toast("The two phone PINs don't match.");
  if (!$("#pairkey").value) return toast("Enroll a USB key first (USB keys tab).");
  askPin("Pair this phone and install the app on it?", async p => {
    await api("/api/phone/pair", { device_id: $("#pairkey").value, label: $("#pairlabel").value.trim() || "phone", phone_pin: mp, pin: p });
    toast("Paired. Installing the app over USB…");
    $("#pairmpin").value = $("#pairmpin2").value = "";
    const r = await api("/api/phone/push", {});
    toast((r.log || []).slice(-1)[0] || "Installed."); loadPhones();
  });
});
setInterval(() => { if (vaultOpen && !$("[data-pane=phones]").hidden) phoneConn(); }, 3000);

const RULE_NAMES = { ask: "Ask me", session: "Ask once per session", auto: "Always allow", never: "Never" };
async function loadRules() {
  let r; try { r = await api("/api/vault/rules"); } catch (e) { $("#rulerows").innerHTML = `<tr><td colspan="2" class="note">${esc(e.message)}</td></tr>`; return; }
  const choices = r.choices || Object.keys(RULE_NAMES), wt = new Set(r.write_targets || []);
  $("#rulerows").innerHTML = (r.hosts || []).map(id => {
    const cur = (r.targets || {})[id] || r.default || "ask";
    return `<tr><td>${esc(id)}${wt.has(id) ? ` <span class="note">· may write</span>` : ""}</td><td><select data-rule="${esc(id)}">${choices.map(c => `<option value="${c}" ${c === cur ? "selected" : ""}>${esc(RULE_NAMES[c] || c)}</option>`).join("")}</select></td></tr>`;
  }).join("") || `<tr><td colspan="2" class="note">No lab hosts in the vault.</td></tr>`;
  $("#rulenote").textContent = `Hosts not listed follow "${RULE_NAMES[r.default] || r.default}". Commands stay read-only unless a host may write.`;
}
$("#rulesave").addEventListener("click", () => {
  const targets = {}; document.querySelectorAll("[data-rule]").forEach(s => targets[s.dataset.rule] = s.value);
  if (!Object.keys(targets).length) return;
  const loose = Object.entries(targets).filter(([, v]) => v === "auto").map(([k]) => k);
  askPin(loose.length ? `Save rules? ${loose.join(", ")} will run agent requests without asking.` : "Save these rules?",
    async p => { await api("/api/vault/rules", { targets, pin: p }); toast("Rules saved."); loadRules(); });
});
$("#pinchange").addEventListener("click", () => {
  const a = $("#newpin1").value, b = $("#newpin2").value;
  if (!/^\d{6,12}$/.test(a)) return toast("The new PIN is 6 to 12 digits.");
  if (a !== b) return toast("The two new PINs don't match.");
  askPin("Change the vault PIN? Enter your CURRENT PIN.", async p => { await api("/api/vault/change-pin", { new_pin: a, pin: p }); $("#newpin1").value = $("#newpin2").value = ""; toast("PIN changed. Use the new one from now on."); });
});
$("#rotate").addEventListener("click", () => askPin("Make a new phone certificate? The phone stops connecting until you re-pair it.",
  async p => { const r = await api("/api/phone/rotate-cert", { pin: p }); toast(r.next || "New certificate made."); }));
async function loadLog() {
  const a = await api("/api/vault/log").catch(e => ({ error: e.message }));
  const rows = a.entries || a.events || a.items || (Array.isArray(a) ? a : []);
  $("#logbook").innerHTML = rows.slice(0, 60).map(r => {
    const t = r.ts || r.time || r.created_at || r.at;
    const what = [r.event, r.target_id || r.target, r.action, r.result].filter(Boolean).join(" · ");
    return `<li><time>${esc(when(t))}</time><span>${esc(what || JSON.stringify(r).slice(0, 140))}</span></li>`;
  }).join("") || `<li><time></time><span>${esc(a.error || "Nothing logged yet.")}</span></li>`;
}
$("#backup").addEventListener("click", () => askPin("Snapshot the vault?", async p => { const r = await api("/api/vault/backup", { pin: p }); toast(`Snapshot saved${r.path ? ": " + r.path : ""}.`); }));
function loadWorkshop(which) {
  if (!vaultOpen) return;
  which = which || document.querySelector("#workshop .ws-tabs .on")?.dataset.ws || "keys";
  ({ keys: loadKeys, phones: () => { loadKeys(); loadPhones(); }, rules: loadRules, log: loadLog }[which] || (() => {}))();
}

/* ---------- goblin mood ---------- */
function mood() {
  const g = $("#goblin"); if (!g) return;
  const bt = $("#bot"); if (bt) bt.classList.toggle("alert", pending.length > 0);
  g.classList.toggle("eager", pending.length > 0 && vaultOpen);
  g.classList.toggle("grump", !vaultOpen && !document.body.classList.contains("working"));
  g.classList.toggle("glee", vaultOpen || document.body.classList.contains("working"));
}

/* ---------- boot ---------- */
(async () => {
  let d; try { d = await api("/api/dials"); } catch (e) { d = { furnace: [], aether: [] }; }
  if (d.brand) $("#tagline").textContent = d.brand;
  MODELS = { F: (d.furnace || []).map(m => ({ ...m, id: m.slot })), A: (d.aether || []).map(m => ({ ...m, id: m.slot })) };
  buildDial("F"); buildDial("A");
  const withMode = MODELS.F.find(m => m.mode); if (withMode) $("#modelbl").textContent = withMode.mode.label;
  dials.F.userTurned = false;
  const a = store.get("aether", "claude"); if (MODELS.A.some(m => m.id === a && !m.empty)) select("A", a);
  await pollGpu();
  setInterval(pollGpu, 3000);
  pollVault(); setInterval(pollVault, 3000);
  pollStack(); setInterval(pollStack, 10000);
  loadNotes();
  await loadRoomList(); setInterval(pollRoom, 2000); setInterval(loadRoomList, 30000);
  showTube([]);
})();

