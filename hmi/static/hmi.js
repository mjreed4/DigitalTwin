// Pump station HMI page. Polls /api/state once a second and redraws.
// All Modbus traffic happens in the Flask app; this page only talks to it.
"use strict";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

let trend = [];          // samples {t, disch, demand, sp, lag_stop, p1, p2}
let lastT = 0;
let state = null;

// ---------------------------------------------------------------- polling
async function refresh() {
  try {
    const r = await fetch(`/api/state?since=${lastT}`);
    state = await r.json();
  } catch (e) {
    showStale("The HMI web server is not answering.");
    return;
  }
  for (const p of state.trend) trend.push(p);
  if (trend.length) lastT = trend[trend.length - 1].t;
  const cutoff = Date.now() / 1000 - 900;
  while (trend.length && trend[0].t < cutoff) trend.shift();
  render(state);
}

function showStale(why) {
  $("#stale").classList.remove("hidden");
  $("#stale-why").textContent = why || "";
}

// ---------------------------------------------------------------- render
function setInd(el, ok, okText, badText) {
  el.classList.toggle("ok", ok);
  el.classList.toggle("bad", !ok);
  el.textContent = ok ? okText : badText;
}

function render(s) {
  $("#clock").textContent = new Date().toLocaleTimeString();
  setInd($("#ind-link"), s.link_ok, "PLC LINK OK", "PLC LINK LOST");
  $("#ind-link").title = `HMI to PLC Modbus TCP link (${s.plc})` + (s.link_error ? `: ${s.link_error}` : "");
  setInd($("#ind-scan"), s.link_ok && s.plc_scanning, "PLC SCANNING", "PLC NOT SCANNING");

  if (!s.link_ok) showStale(s.link_error);
  else if (!s.plc_scanning) showStale("The PLC heartbeat (HR 7) has stopped changing. Is the PLC program running?");
  else $("#stale").classList.add("hidden");

  renderEvents(s.events);
  if (!s.status) return;
  const st = s.status;

  setInd($("#ind-sim"), st.sim_comms_ok, "FIELD I/O OK", "FIELD I/O LOST");
  $("#mode-text").textContent = st.mode_text;

  // ---- overview
  const sp = s.settings.SP_PRESSURE.value;
  $("#v-disch").textContent = st.disch_psi.toFixed(1);
  $("#v-suct").textContent = st.suct_psi.toFixed(1);
  $("#v-demand").textContent = st.demand_pct.toFixed(1);
  $("#v-sp").textContent = `SP ${sp.toFixed(1)}  ·  lag stop ${s.settings.SP_LAG_STOP.value.toFixed(1)}`;
  $("#demand-bar").setAttribute("width", Math.max(0, Math.min(120, st.demand_pct * 1.2)));
  // The value box turns red while pressure is outside the alarm limits right now;
  // the alarm list shows the PLC's latched alarms.
  const outside = st.disch_psi < s.settings.SP_LOW_ALARM.value || st.disch_psi > s.settings.SP_HIGH_ALARM.value;
  $("#disch-g").classList.toggle("alarm", outside && st.mode !== 0);

  for (const n of [1, 2]) {
    const cmd = st[`p${n}_cmd`], run = st[`p${n}_running`];
    const fault = s.alarms.find((a) => a.key === `p${n}_fault`).active;
    const g = $(`#pump${n}`);
    g.classList.toggle("running", run);
    g.classList.toggle("transition", cmd !== run);
    g.classList.toggle("fault", fault);
    let text = run ? "RUNNING" : "STOPPED";
    if (cmd && !run) text = "STARTING";
    if (!cmd && run) text = "STOPPING";
    if (fault) text = "FAULT";
    $(`#p${n}-state`).textContent = text;
    let role = st.mode === 1 ? (st.lead_pump === n ? "LEAD" : "LAG") : "";
    if (fault) role = "locked out until Reset";
    $(`#p${n}-role`).textContent = role;
  }

  // ---- alarms
  const tb = $("#alarm-table tbody");
  tb.innerHTML = "";
  for (const a of s.alarms) {
    const tr = document.createElement("tr");
    tr.className = (a.active ? "active" : "") + (a.active && s.unacked ? " unacked" : "");
    tr.innerHTML = `<td class="st">${a.active ? "ALARM" : "normal"}</td><td></td>`;
    tr.lastChild.textContent = a.text;
    tb.appendChild(tr);
  }
  $("#unack").classList.toggle("hidden", !s.unacked);

  // ---- controls: highlight what the PLC reports, not what was clicked
  const active = {
    mode: st.mode,
    lead: st.lead_pump,
    manual_p1: st.p1_cmd ? 1 : 0,
    manual_p2: st.p2_cmd ? 1 : 0,
  };
  for (const b of $$("button[data-cmd]")) {
    const cmd = b.dataset.cmd, val = Number(b.dataset.val);
    b.classList.toggle("on", active[cmd] === val);
    if (cmd.startsWith("manual")) b.disabled = st.mode !== 2 || !s.link_ok;
    else b.disabled = !s.link_ok;
  }
  const leadSel = s.commands.lead_select;
  $("#lead-note").textContent = leadSel !== st.lead_pump
    ? `P${leadSel} selected; swaps when the lag pump is stopped` : "";
  $("#manual-note").classList.toggle("hidden", st.mode === 2);
  $("#btn-reset").disabled = !s.link_ok;

  // ---- settings: current values (inputs are left alone while the operator types)
  for (const tr of $$(".settings tr[data-name]")) {
    const v = s.settings[tr.dataset.name];
    const dec = tr.querySelector("input").step === "1" ? 0 : 1;
    tr.querySelector(".cv").textContent = v.value.toFixed(dec);
    tr.querySelector(".send").disabled = !s.link_ok;
  }

  renderRaw(s.raw);
  drawTrend();
}

function renderEvents(events) {
  const ul = $("#event-list");
  ul.innerHTML = "";
  for (const e of events) {
    const li = document.createElement("li");
    li.className = e.kind;
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = e.time;
    li.append(t, document.createTextNode(e.text));
    ul.appendChild(li);
  }
}

function renderRaw(raw) {
  if (!$(".raw details").open) return;
  const fill = (table, start, names, values) => {
    table.innerHTML = "";
    values.forEach((v, i) => {
      const tr = table.insertRow();
      tr.insertCell().textContent = `HR ${start + i}`;
      tr.insertCell().textContent = names[i];
      const d = tr.insertCell(); d.className = "n"; d.textContent = v;
      const h = tr.insertCell(); h.className = "n"; h.textContent = "0x" + v.toString(16).padStart(4, "0");
      const b = tr.insertCell(); b.className = "n"; b.textContent = v.toString(2).padStart(16, "0").replace(/(.{4})(?!$)/g, "$1 ");
    });
  };
  fill($("#raw-status"), 0, raw.names0_7, raw.hr0_7);
  fill($("#raw-settings"), 1024, raw.names1024_1034, raw.hr1024_1034);
}

// ---------------------------------------------------------------- trend
function drawTrend() {
  const cv = $("#trend");
  const dpr = window.devicePixelRatio || 1;
  const W = cv.clientWidth, H = 260;
  if (cv.width !== W * dpr) { cv.width = W * dpr; cv.height = H * dpr; }
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);

  const L = 40, R = W - 40, T = 10, B = H - 50;          // plot area
  const span = Number($("#trend-span").value);
  const tEnd = Date.now() / 1000, tStart = tEnd - span;
  const pMax = 180;                                       // psi axis 0..180, demand axis 0..100 %
  const x = (t) => L + (t - tStart) / span * (R - L);
  const yP = (p) => B - Math.max(0, Math.min(pMax, p)) / pMax * (B - T);
  const yD = (d) => B - d / 100 * (B - T);

  g.font = "11px Segoe UI, Arial, sans-serif";
  g.strokeStyle = "#d4d4d4"; g.fillStyle = "#5a5a5a"; g.lineWidth = 1;
  for (let p = 0; p <= pMax; p += 20) {
    g.beginPath(); g.moveTo(L, yP(p)); g.lineTo(R, yP(p)); g.stroke();
    g.textAlign = "right"; g.fillText(p, L - 4, yP(p) + 4);
  }
  g.textAlign = "left";
  for (let d = 0; d <= 100; d += 25) g.fillText(d + "%", R + 4, yD(d) + 4);
  g.textAlign = "center";
  const step = span <= 120 ? 30 : span <= 300 ? 60 : 180;
  for (let t = Math.ceil(tStart / step) * step; t <= tEnd; t += step) {
    g.fillText(new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }), x(t), B + 14);
  }

  const pts = trend.filter((p) => p.t >= tStart - 2);
  const line = (key, y, color, width, dash) => {
    g.strokeStyle = color; g.lineWidth = width; g.setLineDash(dash || []);
    g.beginPath();
    pts.forEach((p, i) => (i ? g.lineTo(x(p.t), y(p[key])) : g.moveTo(x(p.t), y(p[key]))));
    g.stroke();
    g.setLineDash([]);
  };
  g.save();
  g.beginPath(); g.rect(L, T - 2, R - L, B - T + 4); g.clip();
  line("demand", yD, "#a0a0a0", 1.5);
  line("lag_stop", yP, "#7aa0c4", 1.5, [6, 4]);
  line("sp", yP, "#2f5d8a", 1.5, [6, 4]);
  line("disch", yP, "#1f1f1f", 2);
  g.restore();

  // pump run bars under the plot
  [["p1", "P1", B + 22], ["p2", "P2", B + 36]].forEach(([key, label, y]) => {
    g.fillStyle = "#5a5a5a"; g.textAlign = "right"; g.fillText(label, L - 4, y + 9);
    g.fillStyle = "#f0f0f0"; g.fillRect(L, y, R - L, 10);
    g.fillStyle = "#3a3a3a";
    for (let i = 0; i < pts.length - 1; i++) {
      if (pts[i][key]) {
        const x0 = Math.max(L, x(pts[i].t)), x1 = Math.min(R, x(pts[i + 1].t));
        if (x1 > x0) g.fillRect(x0, y, x1 - x0 + 0.5, 10);
      }
    }
  });
}

// ---------------------------------------------------------------- actions
let toastTimer;
function toast(msg, kind) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast " + (kind || "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), kind ? 7000 : 3000);
}

async function post(url, body) {
  try {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    if (!j.ok) toast(j.message, "err");
    return j;
  } catch (e) {
    toast("The HMI web server is not answering.", "err");
    return { ok: false };
  }
}

for (const b of $$("button[data-cmd]")) {
  b.addEventListener("click", async () => {
    b.classList.add("pending");
    const j = await post("/api/command", { command: b.dataset.cmd, value: Number(b.dataset.val) });
    b.classList.remove("pending");
    if (j.ok) toast(j.message);
    refresh();
  });
}

$("#btn-reset").addEventListener("click", async () => {
  const j = await post("/api/command", { command: "reset", value: 1 });
  if (j.ok) toast(j.message);
  refresh();
});

for (const tr of $$(".settings tr[data-name]")) {
  const input = tr.querySelector("input"), btn = tr.querySelector(".send");
  const send = async () => {
    if (input.value === "") return;
    btn.disabled = true;
    const j = await post("/api/setting", { name: tr.dataset.name, value: Number(input.value) });
    btn.disabled = false;
    if (j.ok) {
      toast(j.message, j.clamped ? "warn" : "");
      input.value = "";
    }
    refresh();
  };
  btn.addEventListener("click", send);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });
}

$("#trend-span").addEventListener("change", drawTrend);
$(".raw details").addEventListener("toggle", () => state && state.raw && renderRaw(state.raw));
window.addEventListener("resize", drawTrend);

refresh();
setInterval(refresh, 1000);
