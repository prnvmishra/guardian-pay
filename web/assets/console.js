import { api, el, listen, rupees, timeIST } from "./common.js";

const $ = (id) => document.getElementById(id);
const COLORS = { allow: "#1f7a5a", caution: "#c98a06", pause: "#b5312a", block: "#b5312a" };
const LABEL = { allow: "Allowed", caution: "Warned", pause: "Paused", block: "Blocked" };
const STATUS = {
  paid: "Paid", held: "On hold, cooling off", awaiting_guardian: "Waiting for family",
  stopped: "Stopped, not sent", cancelled: "Cancelled by customer", warned: "Customer warned",
  blocked: "Not sent", failed: "Failed",
};

let txns = [];          // newest first
let selected = null;
let filter = "all";
const fresh = new Set();

/* ------------------------------------------------------------ helpers */
function took(us) {
  return us >= 1000 ? `${(us / 1000).toFixed(1)} ms` : `${us} µs`;
}
function short(name) {
  const [a, b] = name.split(" ");
  return b ? `${a} ${b[0]}.` : a;
}

/* ------------------------------------------------------------ stats + pulse */
function renderStats(s) {
  if (!s) return;
  $("stats").replaceChildren(
    el("span", {}, el("b", {}, s.scored.toLocaleString("en-IN")), "payments scored"),
    el("span", {}, el("b", {}, s.paused.toLocaleString("en-IN")), "paused for a check"),
    el("span", {}, el("b", {}, s.blocked.toLocaleString("en-IN")), "blocked"),
    el("span", { class: "saved" }, el("b", {}, rupees(s.saved)), "kept with customers"),
    el("span", {}, el("b", {}, took(s.latency_median_us)), "median decision time"),
  );
}

const canvas = $("pulse");
function drawPulse() {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  if (canvas.width !== w * dpr) { canvas.width = w * dpr; canvas.height = h * dpr; }
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);

  const top = 10, base = h - 4, span = base - top;
  const y = (score) => base - (score / 100) * span;

  // thresholds
  g.font = "12px 'IBM Plex Sans', sans-serif";
  for (const [score, label] of [[70, "pause at 70"], [40, "warn at 40"]]) {
    g.strokeStyle = "rgba(255,255,255,.18)";
    g.setLineDash([3, 5]);
    g.beginPath(); g.moveTo(0, y(score)); g.lineTo(w, y(score)); g.stroke();
    g.setLineDash([]);
    g.fillStyle = "rgba(255,255,255,.45)";
    g.fillText(label, 0, y(score) - 5);
  }

  const bar = 5, gap = 3, step = bar + gap;
  const fit = Math.floor(w / step);
  const list = txns.slice(0, fit);
  list.forEach((t, i) => {
    const x = w - (i + 1) * step;
    const s = Math.max(t.score, 3);
    g.fillStyle = COLORS[t.decision];
    g.globalAlpha = t.decision === "allow" ? 0.75 : 1;
    g.fillRect(x, y(s), bar, base - y(s));
    if (t.source === "live") {
      g.globalAlpha = 1;
      g.fillStyle = "#ffffff";
      g.beginPath(); g.arc(x + bar / 2, y(s) - 7, 3, 0, Math.PI * 2); g.fill();
    }
  });
  g.globalAlpha = 1;
}
window.addEventListener("resize", drawPulse);

/* ------------------------------------------------------------ table */
function visible(t) {
  if (filter === "risky") return t.decision === "pause" || t.decision === "block";
  if (filter === "live") return t.source === "live";
  return true;
}

function row(t) {
  const tr = el("tr", {
    class: [t.id === selected ? "sel" : "", fresh.has(t.id) ? "fresh" : "", t.source === "live" ? "live" : ""].join(" "),
    onclick: () => { selected = t.id; renderRows(); renderDetail(); },
    tabindex: "0",
    onkeydown: (e) => { if (e.key === "Enter") { selected = t.id; renderRows(); renderDetail(); } },
  },
    el("td", {}, timeIST(t.ts)),
    el("td", { class: "who" }, short(t.sender.name), t.source === "live" ? el("span", { class: "tag phone" }, "Phone") : null,
      el("small", {}, `${t.sender.age}, ${t.sender.city}`)),
    el("td", { class: "to" }, t.payee.name, el("small", {}, `a/c ${t.payee.masked}`)),
    el("td", { class: "num" }, rupees(t.amount)),
    el("td", {}, el("span", { class: "meter" },
      el("i", { style: `--w:${Math.max(t.score, 2)}%;--c:${COLORS[t.decision]}` }),
      el("span", {}, t.score))),
    el("td", { class: `dec ${t.decision}` }, LABEL[t.decision],
      t.caught_by_flag ? el("span", { class: "tag flag" }, "Flag caught it") : null,
      el("small", {}, t.status_note || STATUS[t.status] || "")),
    el("td", { class: "num" }, took(t.latency_us)),
  );
  return tr;
}

function renderRows() {
  const list = txns.filter(visible).slice(0, 150);
  $("rows").replaceChildren(...list.map(row));
  $("empty").hidden = list.length > 0;
  $("empty").textContent = filter === "live"
    ? "No phone payments yet. Open the customer app and send one."
    : filter === "risky" ? "Nothing paused or blocked yet." : "Waiting for the first payment…";
}

document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  filter = b.dataset.filter;
  document.querySelectorAll("[data-filter]").forEach((x) => x.classList.toggle("on", x === b));
  renderRows();
}));

/* ------------------------------------------------------------ detail */
function fmtSignal(k, v) {
  switch (k) {
    case "pasted_account": case "new_payee": case "fd_broken_24h": case "night_hour": return v ? "Yes" : "No";
    case "balance_drain": case "recv_senior_share": case "recv_forward_ratio": return `${Math.round(v * 100)}%`;
    case "amount_ratio": return `${v.toFixed(1)}x`;
    case "confirm_hesitation_s": case "session_duration_s": return `${Math.round(v)}s`;
    default: return Math.round(v).toLocaleString("en-IN");
  }
}
const SHOW = [
  ["app_switches", "Left the app"], ["pasted_account", "Account pasted"], ["confirm_hesitation_s", "Time on confirm"],
  ["retries_after_limit", "Retries after limit"], ["amount_ratio", "Versus usual payment"], ["balance_drain", "Share of balance"],
  ["new_payee", "First payment to them"], ["fd_broken_24h", "FD broken today"],
  ["recv_account_age_days", "Receiver account age (days)"], ["recv_distinct_senders_24h", "Receiver's senders today"],
  ["recv_senior_share", "Of them aged 60+"], ["recv_forward_ratio", "Money passed on"],
];

const SCAM_TYPE = {
  digital_arrest: "Digital arrest", relative_impersonation: "Fake relative", investment_or_task: "Job or investment scam",
  account_takeover: "Account takeover", genuine: "Looks genuine", unclear: "Unclear",
};
const writing = new Set();

async function writeSummary(id) {
  writing.add(id);
  renderDetail();
  try {
    const out = await api(`/api/ai/summary/${id}`, {});
    const t = txns.find((x) => x.id === id);
    if (t) t.ai_summary = out;
  } catch (err) {
    const t = txns.find((x) => x.id === id);
    if (t) t.ai_summary = { summary: "", error: err.message };
  } finally {
    writing.delete(id);
    renderDetail();
  }
}

function aiBlock(t) {
  const out = [];
  if (t.decision === "pause" || t.decision === "block") {
    out.push(el("h4", {}, "AI case note"));
    const a = t.ai_summary;
    if (a && a.summary) {
      out.push(el("div", { class: "ai-note" },
        el("p", {}, a.summary),
        el("p", { class: "ai-meta" }, `${SCAM_TYPE[a.likely_scam_type] || a.likely_scam_type}, ${a.confidence} confidence`),
        el("p", { class: "ai-action" }, a.recommended_action),
        el("p", { class: "ai-src" }, a.provider === "watsonx" ? `Written by IBM watsonx (${a.model})`
          : a.provider === "ollama" ? `Written by ${a.model} on this server` : "Written from the engine's signals (AI unavailable)")));
    } else {
      out.push(el("button", { class: "tbtn small", disabled: writing.has(t.id), onclick: () => writeSummary(t.id) },
        writing.has(t.id) ? "Writing…" : "Write case note"));
      if (a && a.error) out.push(el("p", { class: "ai-src" }, a.error));
    }
  }
  if (t.saathi && t.saathi.turns.length) {
    out.push(el("h4", {}, `Saathi conversation, risk ${t.saathi.risk}`),
      el("ol", { class: "talk" }, ...t.saathi.turns.slice(-6).map((x) =>
        el("li", { class: x.role === "user" ? "me" : "" }, el("b", {}, x.role === "user" ? "Customer: " : "Saathi: "), x.text))));
  }
  if (t.message_check) {
    const m = t.message_check;
    out.push(el("h4", {}, "Message the customer checked"),
      el("p", { class: "ai-meta" }, `${m.verdict.replace("_", " ")}${m.red_flags.length ? ": " + m.red_flags.slice(0, 3).join("; ") : ""}`));
  }
  return out;
}

function renderDetail() {
  const box = $("detail");
  const t = txns.find((x) => x.id === selected);
  if (!t) {
    box.replaceChildren(el("p", { class: "panel-empty" }, "Pick a payment to see why the engine decided what it did."));
    return;
  }
  const maxW = Math.max(...t.reasons.map((r) => r.weight), 1);
  const why = [
    ...t.rules.map((r) => el("li", { class: "rule" }, r)),
    ...t.reasons.map((r) => el("li", {}, r.en, el("i", { style: `--w:${Math.round((r.weight / maxW) * 100)}%` }), el("small", {}, r.hi))),
  ];
  const sig = el("dl", { class: "sig" });
  for (const [k, label] of SHOW) {
    if (t.features && k in t.features) sig.append(el("dt", {}, label), el("dd", {}, fmtSignal(k, t.features[k])));
  }
  box.replaceChildren(...[
    el("div", { class: "detail-top" },
      el("div", {},
        el("h3", {}, t.sender.name, t.source === "live" ? el("span", { class: "tag phone" }, "Phone") : null),
        el("p", {}, `${t.sender.age}, ${t.sender.city} paying ${t.payee.name}`)),
      el("div", { class: "big-score" },
        el("b", { style: `color:${COLORS[t.decision]}` }, t.score),
        el("small", {}, LABEL[t.decision]))),
    el("p", { class: "amt" }, rupees(t.amount)),
    el("p", { class: "note" }, t.status_note || STATUS[t.status] || ""),
    t.caught_by_flag ? el("p", { class: "callout" },
      `On its own the model scored this ${t.score_without_flag}. A customer's answer earlier today is what stopped it.`) : null,
    why.length ? el("h4", {}, "Why") : null,
    why.length ? el("ul", { class: "why" }, ...why) : null,
    ...aiBlock(t),
    el("h4", {}, "Signals"),
    sig,
    el("p", { class: "path" }, `Decided by ${t.path.replace("+", " and ")} in ${took(t.latency_us)}, ref ${t.id}`),
  ].filter(Boolean));
}

/* ------------------------------------------------------------ flags */
function renderFlags(list) {
  const box = $("flags");
  box.parentElement.classList.toggle("has", Boolean(list && list.length));
  if (!list || !list.length) {
    box.replaceChildren(el("p", { class: "panel-empty" },
      "None yet. When a customer answers yes on the pause screen, or a family member taps Stop, the receiving account lands here and every other customer is protected from it at once."));
    return;
  }
  box.replaceChildren(...list.map((f) => {
    const mins = Math.max(0, Math.round((Date.now() / 1000 - f.ts) / 60));
    return el("div", { class: "flag" },
      el("b", {}, `${f.name}, a/c ${f.masked}`),
      el("p", {}, `Flagged by ${f.by}, ${mins < 1 ? "just now" : `${mins} min ago`}`),
      el("p", { class: "muted" }, f.reason),
      el("p", { class: "kept" }, f.saved_count
        ? `${f.saved_count} more ${f.saved_count === 1 ? "payment" : "payments"} stopped since, ${rupees(f.saved_amount)} kept safe`
        : "Watching for the next payment to this account"));
  }));
}

/* ------------------------------------------------------------ top bar */
let simOn = true;
function setSim(on) {
  simOn = on;
  $("simBtn").textContent = on ? "Pause simulated traffic" : "Resume simulated traffic";
  $("simBtn").setAttribute("aria-pressed", String(on));
}
$("simBtn").addEventListener("click", () => api("/api/sim", { on: !simOn }).then((r) => setSim(r.sim_on)));
$("resetBtn").addEventListener("click", () => {
  if (confirm("Clear all payments and flags and start the demo again?")) api("/api/reset", {});
});

let health = null;
function renderEngine(up) {
  const box = $("engine");
  box.classList.toggle("down", !up);
  const aiText = !health ? "" : health.ai.provider === "watsonx" ? `, AI: watsonx ${health.ai.chat_model || "Granite"}`
    : health.ai.provider === "ollama" ? `, AI: ${health.ai.chat_model} (local)` : ", AI: offline safety net";
  const bits = up && health
    ? `Live on ${health.arch}, ${health.trees} trees${aiText}`
    : up ? "Live" : "Reconnecting…";
  box.replaceChildren(el("span", { class: "dot" }), bits);
}
api("/api/health").then((h) => { health = h; renderEngine(true); }).catch(() => renderEngine(false));

/* ------------------------------------------------------------ demo kit */
document.querySelectorAll("[data-copy]").forEach((b) => b.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(b.dataset.copy);
    b.textContent = "Copied";
  } catch {
    b.textContent = "Copy failed";
  }
  setTimeout(() => { b.textContent = "Copy"; }, 1500);
}));

window.addEventListener("load", () => {
  if (!window.QRCode) {
    $("qrPay").replaceWith(el("span", { class: "muted" }, location.host + "/pay"));
    $("qrGuard").replaceWith(el("span", { class: "muted" }, location.host + "/guardian"));
    return;
  }
  for (const [id, path] of [["qrPay", "/pay"], ["qrGuard", "/guardian"]]) {
    new window.QRCode($(id), { text: location.origin + path, width: 208, height: 208, colorDark: "#14213d", colorLight: "#ffffff" });
  }
});

/* ------------------------------------------------------------ stream */
listen((msg) => {
  if (msg.type === "snapshot") {
    txns = [...msg.txns].reverse();
    setSim(msg.sim_on);
  } else if (msg.type === "reset") {
    txns = [];
    selected = null;
  } else if (msg.type === "sim") {
    setSim(msg.sim_on);
  } else if (msg.type === "txn") {
    txns.unshift(msg.txn);
    if (txns.length > 600) txns.length = 600;
    fresh.add(msg.txn.id);
    setTimeout(() => fresh.delete(msg.txn.id), 1700);
    // a payment from the phone is what the room is watching
    if (msg.txn.source === "live" || !selected) selected = msg.txn.id;
  } else if (msg.type === "update") {
    const i = txns.findIndex((t) => t.id === msg.txn.id);
    if (i >= 0) txns[i] = msg.txn;
  }
  renderStats(msg.stats);
  if (msg.flagged) renderFlags(msg.flagged);
  renderRows();
  renderDetail();
  drawPulse();
}, (up) => renderEngine(up));

