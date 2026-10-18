const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
export const rupees = (n) => "₹" + inr.format(Math.round(n || 0));

const ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen"];
const TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];

function upto99(n) {
  if (n < 20) return ONES[n];
  return TENS[Math.floor(n / 10)] + (n % 10 ? "-" + ONES[n % 10] : "");
}
function upto999(n) {
  const h = Math.floor(n / 100), r = n % 100;
  return (h ? ONES[h] + " hundred" + (r ? " " : "") : "") + (r ? upto99(r) : "");
}

/** 650000 -> "Six lakh fifty thousand rupees" */
export function inWords(n) {
  n = Math.floor(n || 0);
  if (!n) return "";
  const parts = [];
  const crore = Math.floor(n / 1e7); n %= 1e7;
  const lakh = Math.floor(n / 1e5); n %= 1e5;
  const thousand = Math.floor(n / 1e3); n %= 1e3;
  if (crore) parts.push(upto999(crore) + " crore");
  if (lakh) parts.push(upto99(lakh) + " lakh");
  if (thousand) parts.push(upto99(thousand) + " thousand");
  if (n) parts.push(upto999(n));
  const s = parts.join(" ") + " rupees";
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export async function api(path, body) {
  const res = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const err = new Error((data && (data.message || data.detail)) || `Request failed (${res.status})`);
    err.status = res.status;
    err.data = data;
    throw err;
  }
  return data;
}

/** Server-sent events with automatic reconnect. */
export function listen(onMessage, onState) {
  let es;
  let retry = 1000;
  const open = () => {
    es = new EventSource("/api/stream");
    es.onopen = () => { retry = 1000; onState && onState(true); };
    es.onmessage = (e) => {
      try { onMessage(JSON.parse(e.data)); } catch (err) { console.error(err); }
    };
    es.onerror = () => {
      onState && onState(false);
      es.close();
      setTimeout(open, retry);
      retry = Math.min(retry * 2, 10000);
    };
  };
  open();
}

export function timeIST(ts) {
  return new Date(ts * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata",
  });
}

export function el(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false || v == null) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return node;
}
