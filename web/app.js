"use strict";

/* ---------- Config ---------- */
const CONFIG = window.APP_CONFIG || { apiKey: "", baseUrl: "" };

/* ---------- API client: the only place that calls fetch ---------- */
class ApiError extends Error {
  constructor(status, message, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter || null;
  }
}

async function api(method, path, { body, headers, query } = {}) {
  let url = CONFIG.baseUrl + path;
  if (query) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(query)) {
      if (v !== undefined && v !== null && v !== "") qs.set(k, v);
    }
    const s = qs.toString();
    if (s) url += "?" + s;
  }
  const opts = {
    method,
    headers: Object.assign({ "X-API-Key": CONFIG.apiKey }, headers || {}),
  };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  let res;
  try {
    res = await fetch(url, opts);
  } catch (e) {
    throw new ApiError(0, "Can't reach the server. Check your connection and try again.");
  }

  let data = null;
  try { data = await res.json(); } catch (e) { /* non-JSON body */ }

  if (!res.ok) {
    const retry = parseInt(res.headers.get("Retry-After"), 10);
    // Level 2: the API rejects requests without a valid key.
    const fallback = res.status === 401
      ? "Access denied: the API key is missing or invalid."
      : "Something went wrong. Please try again.";
    throw new ApiError(
      res.status,
      res.status === 401 ? fallback : ((data && data.error) || fallback),
      Number.isFinite(retry) ? retry : null
    );
  }
  return data;
}

/* ---------- State ---------- */
const state = {
  slots: [],
  providerFilter: "",
  selectedSlot: null,
  idempotencyKey: null,   // reused for retries of the same submission
  lastFingerprint: null,  // name|email|slot the key belongs to
  submitting: false,
  throttleTimer: null,
  lastBookingId: null,
  refBooking: null,
  refSlot: null,
  retryLabel: false,      // true after a network failure: safe to resend
  lookupEmail: "",
  nextCursor: null,
  bookings: [],
};

/* ---------- DOM helpers ---------- */
const $ = (id) => document.getElementById(id);

function el(tag, props, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "onclick") node.addEventListener("click", v);
      else node.setAttribute(k, v);
    }
  }
  for (const c of children) {
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}

function newKey() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
  return "k-" + Date.now() + "-" + Math.random().toString(16).slice(2);
}

const dayFmt = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" });
const fmtDay = (iso) => dayFmt.format(new Date(iso));
const fmtRange = (s, e) => `${timeFmt.format(new Date(s))}–${timeFmt.format(new Date(e))}`;

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/* ---------- Status banner ---------- */
function showStatus(message, kind) {
  const box = $("status");
  box.textContent = message || "";
  box.className = "status" + (kind ? " " + kind : "");
}

/* ---------- Slots ---------- */
async function loadSlots() {
  try {
    const data = await api("GET", "/slots", { query: { provider: state.providerFilter } });
    state.slots = data.slots || [];
    renderProviders();
    renderSlots();
  } catch (e) {
    $("slots-list").replaceChildren(el("p", { class: "empty" }, e.message));
  }
}

function renderProviders() {
  // Keep every provider ever seen so the filter doesn't shrink when filtered.
  const select = $("provider-filter");
  const known = new Set(Array.from(select.options).map((o) => o.value));
  for (const s of state.slots) {
    if (!known.has(s.provider)) {
      select.append(el("option", { value: s.provider }, s.provider));
      known.add(s.provider);
    }
  }
  select.value = state.providerFilter;
}

function renderSlots() {
  const list = $("slots-list");
  if (!state.slots.length) {
    list.replaceChildren(el("p", { class: "empty" }, "No slots to show."));
    return;
  }
  const frag = document.createDocumentFragment();
  let currentDay = null;
  for (const slot of state.slots) {
    const day = fmtDay(slot.start);
    if (day !== currentDay) {
      currentDay = day;
      frag.append(el("h3", { class: "day-heading" }, day));
    }
    const right = slot.available
      ? el("button", { type: "button", class: "btn primary", onclick: () => selectSlot(slot) }, "Book")
      : el("span", { class: "taken" }, "Unavailable");
    frag.append(
      el("div", { class: "slot" },
        el("div", null,
          el("span", { class: "when" }, fmtRange(slot.start, slot.end)),
          el("span", { class: "who" }, slot.provider)),
        right)
    );
  }
  list.replaceChildren(frag);
}

/* ---------- Booking form ---------- */
function selectSlot(slot) {
  state.selectedSlot = slot;
  $("confirmation").hidden = true;
  $("booking-form-section").hidden = false;
  $("selected-slot").textContent = `${slot.provider}, ${fmtDay(slot.start)}, ${fmtRange(slot.start, slot.end)}`;
  clearFormErrors();
  showStatus("");
  $("name").focus();
  $("booking-form-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function closeForm() {
  state.selectedSlot = null;
  state.retryLabel = false;
  $("booking-form-section").hidden = true;
  $("booking-form").reset();
  clearFormErrors();
  resetThrottle();
}

function clearFormErrors() {
  $("name-error").textContent = "";
  $("email-error").textContent = "";
  $("form-error").textContent = "";
}

function setSubmitting(on) {
  state.submitting = on;
  const btn = $("submit-btn");
  btn.disabled = on || state.throttleTimer !== null;
  btn.textContent = on ? "Booking…" : (state.retryLabel ? "Retry booking" : "Confirm booking");
}

function resetThrottle() {
  if (state.throttleTimer) clearInterval(state.throttleTimer);
  state.throttleTimer = null;
  $("submit-btn").disabled = false;
  $("submit-btn").textContent = "Confirm booking";
}

function startThrottle(seconds) {
  let left = Math.max(1, seconds);
  const btn = $("submit-btn");
  if (state.throttleTimer) clearInterval(state.throttleTimer);
  const tick = () => {
    if (left <= 0) { resetThrottle(); $("form-error").textContent = ""; return; }
    btn.disabled = true;
    btn.textContent = `Try again in ${left}s`;
    $("form-error").textContent = `Too many attempts. Please wait ${left} second${left === 1 ? "" : "s"}.`;
    left -= 1;
  };
  tick();
  state.throttleTimer = setInterval(tick, 1000);
}

async function onSubmit(ev) {
  ev.preventDefault();
  if (state.submitting || state.throttleTimer || !state.selectedSlot) return;
  clearFormErrors();

  const name = $("name").value.trim();
  const email = $("email").value.trim();
  let ok = true;
  if (!name) { $("name-error").textContent = "Please enter your name."; ok = false; }
  if (!email) { $("email-error").textContent = "Please enter your email."; ok = false; }
  else if (!EMAIL_RE.test(email)) { $("email-error").textContent = "That email doesn't look valid."; ok = false; }
  if (!ok) return;

  // Same submission => same key (safe retry). Changed data => new key.
  const fingerprint = [state.selectedSlot.id, name, email.toLowerCase()].join("|");
  if (state.lastFingerprint !== fingerprint || !state.idempotencyKey) {
    state.idempotencyKey = newKey();
    state.lastFingerprint = fingerprint;
  }

  setSubmitting(true);
  state.retryLabel = false;
  try {
    const booking = await api("POST", "/bookings", {
      body: { slot_id: state.selectedSlot.id, name, email },
      headers: { "Idempotency-Key": state.idempotencyKey },
    });
    showConfirmation(booking, state.selectedSlot);
    state.idempotencyKey = null;
    state.lastFingerprint = null;
    closeForm();
    loadSlots();
  } catch (e) {
    handleBookingError(e);
  } finally {
    setSubmitting(false);
  }
}

function handleBookingError(e) {
  switch (e.status) {
    case 409:
      showStatus("Sorry, that slot was just taken. Please pick another.", "error");
      state.idempotencyKey = null;
      closeForm();
      loadSlots();
      break;
    case 404:
      showStatus("That slot no longer exists. Please pick another.", "error");
      closeForm();
      loadSlots();
      break;
    case 400:
      $("form-error").textContent = e.message;
      break;
    case 422:
      // Key clash with different data: start fresh next time.
      state.idempotencyKey = null;
      $("form-error").textContent = "Please review your details and try again.";
      break;
    case 429:
      startThrottle(e.retryAfter || 60);
      break;
    case 0:
      // Level 4: retry reuses the same Idempotency-Key, so it can't double-book.
      state.retryLabel = true;
      $("form-error").textContent = e.message + " Retrying is safe: it won't create a duplicate booking.";
      break;
    case 401:
      $("form-error").textContent = e.message;
      break;
    default:
      $("form-error").textContent = "Something went wrong. Please try again.";
  }
}

/* ---------- Confirmation ---------- */
function showConfirmation(booking, slot) {
  state.lastBookingId = booking.id;
  $("cancel-booking").hidden = false;
  const dl = $("confirmation-details");
  const rows = [
    ["Reference", booking.id],
    ["Provider", slot.provider],
    ["When", `${fmtDay(slot.start)}, ${fmtRange(slot.start, slot.end)}`],
    ["Name", booking.name],
    ["Email", booking.email],
    ["Status", booking.status],
  ];
  const frag = document.createDocumentFragment();
  for (const [k, v] of rows) frag.append(el("dt", null, k), el("dd", null, v));
  dl.replaceChildren(frag);
  $("confirmation").hidden = false;
  showStatus("");
  $("confirmation").scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/* ---------- Cancel (Level 3), shared by every place that shows a booking ---------- */
async function cancelById(id) {
  try {
    const updated = await api("DELETE", "/bookings/" + encodeURIComponent(id));
    showStatus("Booking cancelled. The slot is available again.", "ok");
    return updated;
  } catch (e) {
    if (e.status === 409) {
      showStatus("That booking was already cancelled.", "error");
      return { id, status: "cancelled", already: true };
    }
    if (e.status === 404) showStatus("No booking found with that reference.", "error");
    else showStatus(e.message, "error");
    return null;
  } finally {
    loadSlots();
  }
}

function renderRef(b, slot) {
  state.refBooking = b;
  state.refSlot = slot;
  const rows = [["Reference", b.id]];
  if (slot) {
    rows.push(["Provider", slot.provider],
              ["When", `${fmtDay(slot.start)}, ${fmtRange(slot.start, slot.end)}`]);
  } else {
    rows.push(["Slot", b.slot_id]);
  }
  rows.push(["Name", b.name], ["Email", b.email], ["Status", b.status],
            ["Booked at", new Date(b.created_at).toLocaleString()]);
  const frag = document.createDocumentFragment();
  for (const [k, v] of rows) frag.append(el("dt", null, k), el("dd", null, v));
  $("ref-result").replaceChildren(frag);
  $("ref-result").hidden = false;
  $("ref-cancel").hidden = b.status !== "confirmed";
}

/* ---------- Look up one booking by reference (Level 1) ---------- */
async function lookupBooking(id) {
  const err = $("ref-error");
  const out = $("ref-result");
  err.textContent = "";
  out.hidden = true;
  $("ref-cancel").hidden = true;
  id = id.trim();
  if (!id) { err.textContent = "Enter a booking reference."; return; }
  try {
    const b = await api("GET", "/bookings/" + encodeURIComponent(id));
    // Slot details come from the slots list (all slots, unfiltered).
    let slot = state.slots.find((s) => s.id === b.slot_id);
    if (!slot) {
      try {
        const all = await api("GET", "/slots");
        slot = (all.slots || []).find((s) => s.id === b.slot_id);
      } catch (e) { /* show booking without slot details */ }
    }
    renderRef(b, slot);
  } catch (e) {
    err.textContent = e.status === 404 ? "No booking found with that reference." : e.message;
  }
}

/* ---------- My bookings ---------- */
async function fetchBookings(append) {
  $("lookup-error").textContent = "";
  try {
    const data = await api("GET", "/bookings", {
      query: { email: state.lookupEmail, limit: $("page-size").value, cursor: append ? state.nextCursor : "" },
    });
    state.bookings = append ? state.bookings.concat(data.bookings) : data.bookings;
    state.nextCursor = data.next_cursor || null;
    renderBookings();
  } catch (e) {
    $("lookup-error").textContent = e.message;
  }
}

function renderBookings() {
  const list = $("bookings-list");
  if (!state.bookings.length) {
    list.replaceChildren(el("li", null, el("span", { class: "empty" }, "No bookings found for that email.")));
  } else {
    const frag = document.createDocumentFragment();
    for (const b of state.bookings) {
      const right = el("div", null, el("span", { class: "badge " + b.status }, b.status));
      if (b.status === "confirmed") {
        right.append(" ", el("button", { type: "button", class: "btn", onclick: () => cancelBooking(b) }, "Cancel"));
      }
      frag.append(
        el("li", null,
          el("div", null, el("div", null, "Ref ", b.id), el("small", { class: "empty" }, `Slot ${b.slot_id}`)),
          right)
      );
    }
    list.replaceChildren(frag);
  }
  $("load-more").hidden = !state.nextCursor;
}

async function cancelBooking(b) {
  $("lookup-error").textContent = "";
  const updated = await cancelById(b.id);
  if (!updated) return;
  const i = state.bookings.findIndex((x) => x.id === b.id);
  if (i >= 0) state.bookings[i] = Object.assign({}, state.bookings[i], { status: "cancelled" });
  renderBookings();
}

/* ---------- Wiring ---------- */
function init() {
  $("booking-form").addEventListener("submit", onSubmit);
  $("cancel-form").addEventListener("click", closeForm);
  $("book-another").addEventListener("click", () => { $("confirmation").hidden = true; });
  $("view-booking").addEventListener("click", () => {
    if (!state.lastBookingId) return;
    $("ref-input").value = state.lastBookingId;
    lookupBooking(state.lastBookingId);
    $("lookup-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
  });
  $("ref-cancel").addEventListener("click", async () => {
    if (!state.refBooking) return;
    const updated = await cancelById(state.refBooking.id);
    if (updated) renderRef(Object.assign({}, state.refBooking, { status: "cancelled" }), state.refSlot);
  });
  $("cancel-booking").addEventListener("click", async () => {
    if (!state.lastBookingId) return;
    const updated = await cancelById(state.lastBookingId);
    if (updated) {
      const rows = document.querySelectorAll("#confirmation-details dd");
      if (rows.length) rows[rows.length - 1].textContent = "cancelled";
      $("cancel-booking").hidden = true;
      $("confirmation").hidden = false;
    }
  });
  $("page-size").addEventListener("change", () => {
    if (state.lookupEmail) { state.nextCursor = null; fetchBookings(false); }
  });
  $("ref-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    lookupBooking($("ref-input").value);
  });
  $("provider-filter").addEventListener("change", (e) => {
    state.providerFilter = e.target.value;
    loadSlots();
  });
  $("lookup-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const email = $("lookup-email").value.trim();
    if (!EMAIL_RE.test(email)) {
      $("lookup-error").textContent = "Enter a valid email address.";
      return;
    }
    state.lookupEmail = email;
    state.nextCursor = null;
    fetchBookings(false);
  });
  $("load-more").addEventListener("click", () => fetchBookings(true));
  loadSlots();
}

document.addEventListener("DOMContentLoaded", init);
