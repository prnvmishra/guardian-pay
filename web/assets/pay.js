import { api, el, inWords, listen, rupees } from "./common.js";

const $ = (id) => document.getElementById(id);
const screens = [...document.querySelectorAll("[data-screen]")];

let customer = null;
let payee = null;      // { account, name, sub, saved }
let amount = 0;
let txn = null;
let session = null;
let ringTimer = null;

/* ------------------------------------------------------------ behaviour */
function startSession() {
  session = {
    start: performance.now(),
    switches: 0,
    pasted: false,
    edits: 0,
    lastAmount: null,
    retries: 0,
    confirmAt: null,
    hesitation: 0,
  };
  renderSignals();
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden && session) { session.switches += 1; renderSignals(); }
});
// laptops: switching windows does not hide the page, it only blurs it
window.addEventListener("blur", () => {
  if (!session) return;
  setTimeout(() => {
    if (!document.hidden && !document.hasFocus()) { session.switches += 1; renderSignals(); }
  }, 150);
});

function behaviour() {
  const now = performance.now();
  return {
    app_switches: session.switches,
    pasted_account: session.pasted,
    confirm_hesitation_s: Math.round(session.hesitation * 10) / 10,
    amount_edits: session.edits,
    retries_after_limit: session.retries,
    session_duration_s: Math.round((now - session.start) / 100) / 10,
  };
}

function renderSignals() {
  const list = $("sigList");
  list.replaceChildren();
  if (!session) {
    list.append(el("dt", {}, "Start a payment to see live signals"), el("dd"));
    return;
  }
  const b = behaviour();
  const rows = [
    ["Left the app", b.app_switches],
    ["Account pasted", b.pasted_account ? "Yes" : "No"],
    ["Amount changed", b.amount_edits],
    ["Retries after limit", b.retries_after_limit],
    ["Time on confirm", session.confirmAt ? `${Math.round((performance.now() - session.confirmAt) / 1000)}s` : "—"],
    ["Session time", `${Math.round(b.session_duration_s)}s`],
  ];
  for (const [k, v] of rows) list.append(el("dt", {}, k), el("dd", {}, v));
}
setInterval(() => { if (session && $("signals").open) renderSignals(); }, 1000);
$("signals").addEventListener("toggle", renderSignals);

/* ------------------------------------------------------------ navigation */
function show(name) {
  for (const s of screens) s.hidden = s.dataset.screen !== name;
  $("signals").hidden = name === "pause" || name === "safe";
  window.scrollTo(0, 0);
  if (name === "confirm") session.confirmAt = performance.now();
  if (name === "home") { session = null; payee = null; amount = 0; txn = null; stopRing(); renderSignals(); }
}

document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-back]");
  if (b) { e.preventDefault(); show(b.dataset.back); }
});

/* ------------------------------------------------------------ home */
function initials(name) {
  return name.split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
}

function renderHome() {
  $("custName").textContent = `${customer.name.split(" ")[0]} ji`;
  $("balance").textContent = rupees(customer.balance);
  $("fd").textContent = customer.fd > 0 ? rupees(customer.fd) : "Closed";
  $("guardianName").textContent = `${customer.guardian.name} (${customer.guardian.relation.toLowerCase()})`;
  const ul = $("payees");
  ul.replaceChildren();
  for (const p of customer.payees) {
    ul.append(el("li", {},
      el("button", {
        class: "payee-btn",
        onclick: () => {
          startSession();
          pickPayee({ account: p.account, name: p.name, sub: `${p.note}, a/c ${p.masked}`, saved: true });
        },
      },
        el("span", { class: "avatar", "aria-hidden": "true" }, initials(p.name)),
        el("span", {}, el("p", { class: "payee-name" }, p.name), el("p", { class: "payee-sub" }, `${p.note}, a/c ${p.masked}`)),
      )));
  }
}

async function loadMe() {
  customer = await api("/api/me");
  renderHome();
}

/* ------------------------------------------------------------ new payee */
$("newPayeeBtn").addEventListener("click", () => {
  startSession();
  $("acct").value = ""; $("acctName").value = ""; $("ifsc").value = "";
  $("newPayeeErr").textContent = "";
  $("checkBadge").hidden = true;
  show("newpayee");
  $("acct").focus();
});
$("acct").addEventListener("paste", () => { if (session) { session.pasted = true; renderSignals(); } });
$("acct").addEventListener("input", (e) => {
  const digits = e.target.value.replace(/\D/g, "").slice(0, 18);
  e.target.value = digits.replace(/(\d{4})(?=\d)/g, "$1 ");
});
$("ifsc").addEventListener("input", (e) => { e.target.value = e.target.value.toUpperCase(); });

$("newPayeeNext").addEventListener("click", () => {
  const acct = $("acct").value.replace(/\D/g, "");
  const name = $("acctName").value.trim();
  if (acct.length < 9) { $("newPayeeErr").textContent = "Account numbers have at least 9 digits."; return; }
  if (!name) { $("newPayeeErr").textContent = "Enter the name of the account holder."; return; }
  const known = customer.payees.find((p) => p.account === acct);
  pickPayee({
    account: acct,
    name: known ? known.name : name,
    sub: `a/c xx${acct.slice(-4)}${known ? "" : ", new"}`,
    saved: Boolean(known),
  });
});

/* ------------------------------------------------------------ amount */
function pickPayee(p) {
  payee = p;
  $("amtAvatar").textContent = initials(p.name);
  $("amtName").textContent = p.name;
  $("amtSub").textContent = p.sub;
  $("amount").value = "";
  $("amtWords").textContent = "";
  $("amountErr").textContent = "";
  session.lastAmount = null;
  show("amount");
  $("amount").focus();
}

$("amount").addEventListener("input", (e) => {
  const n = Number(e.target.value.replace(/\D/g, "").slice(0, 7));
  e.target.value = n ? new Intl.NumberFormat("en-IN").format(n) : "";
  $("amtWords").textContent = inWords(n);
  $("amountErr").textContent = "";
});
$("amount").addEventListener("change", (e) => {
  const n = Number(e.target.value.replace(/\D/g, ""));
  if (!session || !n) return;
  if (session.lastAmount !== null && session.lastAmount !== n) { session.edits += 1; renderSignals(); }
  session.lastAmount = n;
});

$("amountNext").addEventListener("click", () => {
  const n = Number($("amount").value.replace(/\D/g, ""));
  if (!n) { $("amountErr").textContent = "Enter an amount."; return; }
  if (n > 1000000) { $("amountErr").textContent = "You can send up to ₹10,00,000 at a time."; return; }
  if (session.lastAmount !== null && session.lastAmount !== n) session.edits += 1;
  session.lastAmount = n;
  amount = n;
  $("cfName").textContent = payee.name;
  $("cfAcct").textContent = `xx${payee.account.slice(-4)}`;
  $("cfAmt").textContent = rupees(n);
  $("cfWords").textContent = inWords(n);
  $("payErr").textContent = "";
  show("confirm");
});

/* ------------------------------------------------------------ pay */
async function pay() {
  const btn = $("payBtn");
  session.hesitation = (performance.now() - session.confirmAt) / 1000;
  btn.disabled = true;
  btn.textContent = "Checking…";
  $("payErr").textContent = "";
  try {
    const res = await api("/api/pay", {
      payee_account: payee.account,
      payee_name: payee.name,
      amount,
      behaviour: behaviour(),
      message_check_id: session.check ? session.check.id : null,
    });
    if (res.error === "insufficient") {
      session.retries += 1;
      renderSignals();
      $("fdText").textContent = res.fd > 0
        ? `Your savings have ${rupees(res.balance)}. You can close your fixed deposit of ${rupees(res.fd)} and pay right away.`
        : `Your savings have ${rupees(res.balance)}. Enter a smaller amount.`;
      $("fdBreak").hidden = !(res.fd > 0);
      $("fdSheet").hidden = false;
      return;
    }
    customer = res.customer;
    renderHome();
    handle(res.txn);
  } catch (err) {
    $("payErr").textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Pay now";
  }
}
$("payBtn").addEventListener("click", pay);

$("fdChange").addEventListener("click", () => { $("fdSheet").hidden = true; show("amount"); });
$("fdBreak").addEventListener("click", async () => {
  $("fdBreak").disabled = true;
  try {
    customer = await api("/api/fd/break", {});
    renderHome();
    $("fdSheet").hidden = true;
    session.confirmAt = performance.now() - session.hesitation * 1000;
    await pay();
  } catch (err) {
    $("fdText").textContent = err.message;
  } finally {
    $("fdBreak").disabled = false;
  }
});

function handle(t) {
  txn = t;
  if (t.status === "paid") return showPaid(t);
  if (t.status === "warned") return showCaution(t);
  if (t.status === "held" || t.status === "awaiting_guardian") return showPause(t);
  if (t.status === "blocked") return showSafe(t, "We stopped this payment",
    `This account is on the 1930 reported-fraud list. ${rupees(t.amount)} was not sent.`);
  if (t.status === "stopped") return showSafe(t, "Your money is safe",
    `${rupees(t.amount)} to ${t.payee.name} was not sent. Please don't pay anyone who calls you like this.`);
  if (t.status === "cancelled") return showSafe(t, "Payment cancelled", `${rupees(t.amount)} stays in your account.`);
  if (t.status === "failed") { $("payErr").textContent = t.status_note || "Payment failed."; show("confirm"); }
}

function showPaid(t) {
  quiet();
  $("paidTitle").textContent = `${rupees(t.amount)} sent`;
  $("paidSub").textContent = `to ${t.payee.name} ref ${t.id}`;
  stopRing();
  show("paid");
}

function reasonItems(list, reasons) {
  list.replaceChildren(...reasons.map((r) => el("li", {}, r.en, el("small", {}, r.hi))));
}

/* caution */
function showCaution(t) {
  reasonItems($("cautionReasons"), t.reasons.length ? t.reasons
    : [{ en: "This payment is different from how you usually pay", hi: "Aapke normal payment se alag" }]);
  $("cautionSheet").hidden = false;
}
$("cautionCancel").addEventListener("click", async () => {
  $("cautionSheet").hidden = true;
  const res = await api(`/api/txn/${txn.id}/cancel`, {});
  handle(res.txn);
});
$("cautionPay").addEventListener("click", async () => {
  $("cautionSheet").hidden = true;
  const res = await api(`/api/txn/${txn.id}/proceed`, {});
  customer = res.customer;
  renderHome();
  handle(res.txn);
});

/* pause */
const CIRC = 2 * Math.PI * 52;
function stopRing() { if (ringTimer) clearInterval(ringTimer); ringTimer = null; }

function showPause(t) {
  const fresh = !txn || $("pauseAmt").dataset.id !== t.id;
  $("pauseAmt").dataset.id = t.id;
  $("pauseAmt").textContent = `${rupees(t.amount)} to ${t.payee.name}`;
  reasonItems($("pauseReasons"), [
    ...t.rules.map((r) => ({ en: r, hi: "" })),
    ...t.reasons,
  ].slice(0, 4));
  const waiting = t.status === "awaiting_guardian";
  $("waiting").hidden = !waiting;
  $("waitName").textContent = customer.guardian.name;
  if (waiting) {
    $("saathi").hidden = true;
    $("checkForm").hidden = true;
  } else if (fresh) {
    $("checkForm").reset();
    $("checkForm").hidden = true;
    $("saathi").hidden = false;
    resetSaathi();
  }
  show("pause");
  if (fresh) startRing(t.hold.seconds, t.hold.until - t.ts);
  if (!waiting && fresh) startSaathi();
}

function startRing(total, _span) {
  stopRing();
  const fg = $("ringFg");
  fg.style.strokeDasharray = `${CIRC}`;
  const started = Date.now();
  const tick = () => {
    const left = Math.max(0, total - (Date.now() - started) / 1000);
    const m = Math.floor(left / 60), s = Math.floor(left % 60);
    $("ringTime").textContent = `${m}:${String(s).padStart(2, "0")}`;
    fg.style.strokeDashoffset = `${CIRC * (1 - left / total)}`;
    if (left <= 0) stopRing();
  };
  tick();
  ringTimer = setInterval(tick, 1000);
}

$("checkForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = {
    on_call: f.get("on_call") === "yes",
    keep_secret: f.get("keep_secret") === "yes",
    threatened: f.get("threatened") === "yes",
  };
  const res = await api(`/api/txn/${txn.id}/answers`, body);
  txn = res.txn;
  if (txn.status === "awaiting_guardian") {
    $("checkForm").hidden = true;
    $("waiting").hidden = false;
  } else {
    handle(txn);
  }
});

async function cancelHeld() {
  const res = await api(`/api/txn/${txn.id}/cancel`, {});
  handle(res.txn);
}
$("pauseCancel").addEventListener("click", cancelHeld);
$("waitCancel").addEventListener("click", cancelHeld);

/* ------------------------------------------------------------ Saathi */
let speakOn = true;
let saathiBusy = false;
const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recog = null;

function hindiVoice() {
  const voices = window.speechSynthesis ? speechSynthesis.getVoices() : [];
  return voices.find((v) => v.lang === "hi-IN") || voices.find((v) => v.lang.startsWith("hi")) || null;
}
function speak(text) {
  if (!speakOn || !window.speechSynthesis || !text) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const v = hindiVoice();
  if (v) u.voice = v;
  u.lang = v ? v.lang : "hi-IN";
  u.rate = 0.92;
  speechSynthesis.speak(u);
}
$("speakToggle").addEventListener("click", () => {
  speakOn = !speakOn;
  $("speakToggle").setAttribute("aria-pressed", String(speakOn));
  if (!speakOn && window.speechSynthesis) speechSynthesis.cancel();
});

function renderChat(turns, typing) {
  const items = (turns || []).map((t) => el("li", { class: t.role === "user" ? "me" : "bot" }, t.text));
  if (typing) items.push(el("li", { class: "bot typing" }, "साथी सोच रहा है…"));
  $("chat").replaceChildren(...items);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function resetSaathi() {
  saathiBusy = false;
  renderChat([]);
  $("saathiErr").textContent = "";
  $("saathiVerdict").hidden = true;
  $("composer").hidden = false;
  $("quick").hidden = false;
  $("saathiInput").value = "";
  $("useQuestions").hidden = false;
}

function saathiDone(risk) {
  $("composer").hidden = true;
  $("quick").hidden = true;
  $("useQuestions").hidden = true;
  if (recog) recog.abort();
  if (risk === "high") {
    $("saathiVerdict").classList.remove("calm");
    $("verdictTitle").textContent = "साथी को ये धोखाधड़ी लग रही है";
    $("verdictText").textContent = "It looks like someone is pressuring you. Please cancel and talk to your family first.";
    $("verdictCancel").textContent = "Cancel this payment";
    $("verdictAsk").textContent = `Ask ${customer.guardian.name.split(" ")[0]} to decide`;
    $("saathiVerdict").hidden = false;
  } else {
    // nothing worrying came up: the family contact still confirms
    decideSaathi("ask_guardian");
  }
}

async function talk(text) {
  if (saathiBusy || !txn) return;
  saathiBusy = true;
  $("saathiErr").textContent = "";
  $("composer").setAttribute("aria-busy", "true");
  const shown = [...((txn.saathi && txn.saathi.turns) || [])];
  if (text) shown.push({ role: "user", text });
  renderChat(shown, true);
  try {
    const res = await api(`/api/txn/${txn.id}/saathi`, text ? { text } : {});
    txn = res.txn;
    renderChat(txn.saathi.turns);
    $("saathiSub").textContent = res.result.provider === "watsonx" ? "IBM watsonx Granite"
      : res.result.provider === "ollama" ? "Granite on this device" : "Bank safety check";
    speak(res.result.reply);
    if (res.result.done) saathiDone(res.result.risk);
  } catch (err) {
    renderChat(shown);
    $("saathiErr").textContent = err.message;
    if (err.status === 409) api(`/api/txn/${txn.id}`).then(handle).catch(() => {});
  } finally {
    saathiBusy = false;
    $("composer").removeAttribute("aria-busy");
  }
}

function startSaathi() {
  if (txn && txn.saathi && txn.saathi.turns.length) {
    renderChat(txn.saathi.turns);
    if (txn.saathi.done) saathiDone(txn.saathi.risk);
    return;
  }
  talk(null);
}

$("composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("saathiInput").value.trim();
  if (!text) { $("saathiErr").textContent = "बोलिए या कुछ लिखिए।"; return; }
  $("saathiInput").value = "";
  talk(text);
});
document.querySelectorAll("[data-say]").forEach((b) => b.addEventListener("click", () => talk(b.dataset.say)));

if (!Recognition) {
  $("micBtn").hidden = true;
} else {
  $("micBtn").addEventListener("click", () => {
    if (recog) { recog.stop(); return; }
    if (window.speechSynthesis) speechSynthesis.cancel();
    recog = new Recognition();
    recog.lang = "hi-IN";
    recog.interimResults = true;
    recog.maxAlternatives = 1;
    let finalText = "";
    recog.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        if (e.results[i].isFinal) finalText += e.results[i][0].transcript;
        else interim += e.results[i][0].transcript;
      }
      $("saathiInput").value = (finalText + interim).trim();
    };
    recog.onerror = (e) => {
      $("saathiErr").textContent = e.error === "not-allowed"
        ? "Allow the microphone to speak, or type your answer."
        : e.error === "no-speech" ? "Didn't hear anything. Tap the mic and speak." : "Couldn't hear clearly. Type your answer instead.";
    };
    recog.onend = () => {
      $("micBtn").classList.remove("listening");
      recog = null;
      const said = finalText.trim();
      if (said) { $("saathiInput").value = ""; talk(said); }
    };
    $("micBtn").classList.add("listening");
    $("saathiErr").textContent = "";
    recog.start();
  });
}

async function decideSaathi(action) {
  try {
    const res = await api(`/api/txn/${txn.id}/saathi/decide`, { action });
    txn = res.txn;
    handle(txn);
  } catch (err) {
    $("saathiErr").textContent = err.message;
  }
}
$("verdictCancel").addEventListener("click", () => decideSaathi("cancel"));
$("verdictAsk").addEventListener("click", () => decideSaathi("ask_guardian"));

$("useQuestions").addEventListener("click", () => {
  $("saathi").hidden = true;
  $("checkForm").hidden = false;
  if (recog) recog.abort();
  if (window.speechSynthesis) speechSynthesis.cancel();
});
$("useSaathi").addEventListener("click", () => {
  $("checkForm").hidden = true;
  $("saathi").hidden = false;
});

/* ------------------------------------------------------------ message checker */
let checkFromPayee = false;
let checkImage = null;
const TYPES = {
  digital_arrest: "Fake police / CBI “digital arrest”", relative_impersonation: "Someone pretending to be family",
  investment_or_task: "Job, task or investment scam", prize_or_lottery: "Prize or lottery scam",
  kyc_or_account_block: "Fake KYC or account block", utility_cutoff: "Fake bill or power cut",
  other: "Other scam pattern", none: "",
};
const VERDICT = {
  scam: "This looks like a scam", suspicious: "Be careful: warning signs found", looks_safe: "No common scam signs found",
};

function openCheck(fromPayee) {
  checkFromPayee = fromPayee;
  checkImage = null;
  $("checkText").value = "";
  $("checkImage").value = "";
  $("shotName").textContent = "";
  $("checkErr").textContent = "";
  $("checkForm2").hidden = false;
  $("checkResult").hidden = true;
  $("checkSheet").hidden = false;
  $("checkText").focus();
}
$("checkHomeBtn").addEventListener("click", () => openCheck(false));
$("checkFromPayee").addEventListener("click", () => openCheck(true));
$("checkClose").addEventListener("click", () => { $("checkSheet").hidden = true; });
$("checkDone").addEventListener("click", () => { $("checkSheet").hidden = true; });
$("checkAgain").addEventListener("click", () => openCheck(checkFromPayee));

function shrink(file) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const url = URL.createObjectURL(file);
    img.onload = () => {
      const max = 1400;
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k);
      c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      URL.revokeObjectURL(url);
      resolve(c.toDataURL("image/jpeg", 0.85));
    };
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That file isn't an image we can read.")); };
    img.src = url;
  });
}
$("checkImage").addEventListener("change", async (e) => {
  const f = e.target.files[0];
  $("checkErr").textContent = "";
  if (!f) return;
  try {
    checkImage = await shrink(f);
    if (checkImage.length > 5_500_000) throw new Error("That screenshot is too large. Try a smaller one.");
    $("shotName").textContent = f.name;
  } catch (err) {
    checkImage = null;
    $("shotName").textContent = "";
    $("checkErr").textContent = err.message;
  }
});

$("checkRun").addEventListener("click", async () => {
  const text = $("checkText").value.trim();
  if (!text && !checkImage) { $("checkErr").textContent = "Paste the message or add a screenshot."; return; }
  const btn = $("checkRun");
  btn.disabled = true;
  btn.textContent = "Checking…";
  $("checkErr").textContent = "";
  try {
    const r = await api("/api/ai/check", { text: text || null, image: checkImage });
    $("vBox").className = `verdict-box ${r.verdict}`;
    $("vTitle").textContent = VERDICT[r.verdict] || r.verdict;
    $("vType").textContent = TYPES[r.scam_type] ?? "";
    $("vFlags").replaceChildren(...r.red_flags.map((f) => el("li", {}, f)));
    $("vAdviceHi").textContent = r.advice_hi;
    $("vAdviceEn").textContent = r.advice_en;
    $("vSource").textContent = (r.provider === "watsonx" ? "Checked by IBM watsonx Granite."
      : r.provider === "ollama" ? "Checked by Granite on this device."
      : "Checked by the bank's built-in rules.") + (r.note ? ` ${r.note}` : "");
    $("checkDone").textContent = checkFromPayee && r.verdict === "scam" ? "Don't pay this account" : "Done";
    $("checkForm2").hidden = true;
    $("checkResult").hidden = false;
    if (checkFromPayee && session) {
      session.check = { id: r.id, verdict: r.verdict };
      const digits = r.accounts_found.find((a) => /^\d{9,18}$/.test(a));
      if (digits && !$("acct").value) {
        $("acct").value = digits.replace(/(\d{4})(?=\d)/g, "$1 ");
        session.pasted = true;
      }
      const badge = $("checkBadge");
      badge.hidden = false;
      badge.className = `check-done ${r.verdict === "looks_safe" ? "ok" : "scam"}`;
      badge.textContent = `Message checked: ${VERDICT[r.verdict].toLowerCase()}.`;
    }
  } catch (err) {
    $("checkErr").textContent = err.message;
  } finally {
    btn.disabled = false;
    btn.textContent = "Check message";
  }
});
$("checkDone").addEventListener("click", () => {
  if (checkFromPayee && session && session.check && session.check.verdict === "scam") show("home");
});

/* stopped */
function quiet() {
  if (window.speechSynthesis) speechSynthesis.cancel();
  if (recog) recog.abort();
}

function showSafe(t, title, sub) {
  quiet();
  $("safeTitle").textContent = title;
  $("safeSub").textContent = sub;
  stopRing();
  show("safe");
}

/* ------------------------------------------------------------ live updates */
listen((msg) => {
  if (msg.type === "reset") { customer = msg.customer; renderHome(); show("home"); return; }
  if (msg.type === "customer" && msg.customer) { customer = msg.customer; renderHome(); return; }
  if (msg.type !== "update" || !txn || msg.txn.id !== txn.id) return;
  const before = txn.status;
  txn = msg.txn;
  if (txn.status !== before) {
    api("/api/me").then((c) => { customer = c; renderHome(); }).catch(() => {});
    handle(txn);
  }
});

loadMe().catch(() => {
  $("custName").textContent = "—";
  $("balance").textContent = "Offline";
});
renderSignals();
