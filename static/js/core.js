/* Saathi core: DOM helpers, safe storage, i18n, API client, forms and the hash router.
 * Feature modules register themselves with Saathi.register(init) and are started by main.js.
 * Security: every user- or AI-provided string is rendered with textContent (never innerHTML).
 */
"use strict";

window.Saathi = (() => {
  const LANGS = ["en", "hi", "pa", "ta", "bn", "mr"];
  const VIEWS = ["home", "ledger", "profile", "safety", "schemes", "documents", "grievance"];
  const STATES = [
    ["AN", "Andaman & Nicobar Islands"], ["AP", "Andhra Pradesh"], ["AR", "Arunachal Pradesh"], ["AS", "Assam"],
    ["BR", "Bihar"], ["CH", "Chandigarh"], ["CT", "Chhattisgarh"], ["DH", "Dadra & Nagar Haveli and Daman & Diu"],
    ["DL", "Delhi"], ["GA", "Goa"], ["GJ", "Gujarat"], ["HP", "Himachal Pradesh"], ["HR", "Haryana"],
    ["JH", "Jharkhand"], ["JK", "Jammu & Kashmir"], ["KA", "Karnataka"], ["KL", "Kerala"], ["LA", "Ladakh"],
    ["LD", "Lakshadweep"], ["MH", "Maharashtra"], ["ML", "Meghalaya"], ["MN", "Manipur"], ["MP", "Madhya Pradesh"],
    ["MZ", "Mizoram"], ["NL", "Nagaland"], ["OR", "Odisha"], ["PB", "Punjab"], ["PY", "Puducherry"],
    ["RJ", "Rajasthan"], ["SK", "Sikkim"], ["TG", "Telangana"], ["TN", "Tamil Nadu"], ["TR", "Tripura"],
    ["UP", "Uttar Pradesh"], ["UT", "Uttarakhand"], ["WB", "West Bengal"],
  ];
  const moneyFormat = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 });

  // ---------- DOM helpers ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /** Create an element. Children may be strings (inserted as text) or nodes. */
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat()) {
      if (child === null || child === undefined || child === false) continue;
      node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
    return node;
  }

  function clear(node) { node.replaceChildren(); return node; }

  // ---------- Safe storage ----------
  function makeStore(kind) {
    return {
      get(key, fallback = null) {
        try {
          const raw = window[kind].getItem(key);
          return raw === null ? fallback : JSON.parse(raw);
        } catch { return fallback; }
      },
      set(key, value) {
        try { window[kind].setItem(key, JSON.stringify(value)); return true; } catch { return false; }
      },
      remove(key) { try { window[kind].removeItem(key); } catch { /* storage unavailable */ } },
    };
  }
  const local = makeStore("localStorage");
  const session = makeStore("sessionStorage");

  // ---------- Worker profile (versioned, browser-only, no PII) ----------
  const PROFILE_KEY = "saathi.profile";
  const PROFILE_VERSION = 1;
  const WORK_TYPES = ["delivery", "ride_hailing", "bike_taxi", "home_services", "daily_wage", "domestic_work", "other"];
  const PLATFORMS = ["swiggy", "zomato", "uber", "ola", "rapido", "urban_company", "bharat_taxi", "porter", "blinkit", "zepto", "other"];

  const profile = {
    get() {
      const stored = local.get(PROFILE_KEY);
      if (!stored || stored.version !== PROFILE_VERSION) return { version: PROFILE_VERSION, work_types: [], platforms: [], onboarding: "pending" };
      return {
        version: PROFILE_VERSION,
        work_types: (stored.work_types || []).filter((w) => WORK_TYPES.includes(w)),
        platforms: (stored.platforms || []).filter((p) => PLATFORMS.includes(p)),
        onboarding: stored.onboarding === "done" || stored.onboarding === "skipped" ? stored.onboarding : "pending",
      };
    },
    save(data) { return local.set(PROFILE_KEY, { ...profile.get(), ...data, version: PROFILE_VERSION }); },
    workTypes() { return profile.get().work_types; },
  };

  // ---------- i18n ----------
  let lang = "en";
  let strings = {};
  const i18nCache = {};

  function lookup(key) {
    return key.split(".").reduce((obj, part) => (obj && typeof obj === "object" ? obj[part] : undefined), strings);
  }

  /** Translate a key, replacing {name} placeholders. Falls back to the key itself. */
  function t(key, vars = {}) {
    const value = lookup(key);
    const text = typeof value === "string" ? value : key;
    return text.replace(/\{(\w+)\}/g, (_, name) => (name in vars ? String(vars[name]) : `{${name}}`));
  }

  async function loadLanguage(code) {
    if (!LANGS.includes(code)) code = "en";
    if (!i18nCache[code]) i18nCache[code] = await api(`/api/i18n/${code}`);
    lang = code;
    strings = i18nCache[code];
    local.set("saathi.lang", code);
    document.documentElement.lang = code;
    $("#lang-select").value = code;
    applyTranslations();
  }

  function optionEntries(kind) {
    if (kind === "states") return STATES;
    const group = lookup(kind) || {};
    let keys = Object.keys(group);
    if (kind === "platforms") {
      const mine = profile.get().platforms;
      keys = [...mine, ...keys.filter((k) => !mine.includes(k))]; // the worker's own platforms first
    }
    return keys.map((k) => [k, t(`${kind}.${k}`)]);
  }

  function fillSelect(select) {
    const current = select.value;
    const emptyLabel = select.dataset.emptyLabel ? t(select.dataset.emptyLabel) : t("common.choose");
    clear(select).append(el("option", { value: "" }, emptyLabel));
    for (const [value, label] of optionEntries(select.dataset.options)) select.append(el("option", { value }, label));
    if (current) select.value = current;
  }

  function applyTranslations() {
    for (const node of $$("[data-i18n]")) node.textContent = t(node.dataset.i18n);
    for (const node of $$("[data-i18n-placeholder]")) node.placeholder = t(node.dataset.i18nPlaceholder);
    for (const node of $$("[data-i18n-label]")) node.setAttribute("aria-label", t(node.dataset.i18nLabel));
    for (const select of $$("select[data-options]")) fillSelect(select);
    for (const listener of languageListeners) listener(lang);
    rerenderCurrentView();
  }

  const languageListeners = [];

  // ---------- API ----------
  class ApiError extends Error {
    constructor(code, message, retryable = false) { super(message); this.code = code; this.retryable = retryable; }
  }

  async function request(path, init) {
    let response;
    try { response = await fetch(path, init); } catch { throw new ApiError("network", "network", true); }
    let data = null;
    try { data = await response.json(); } catch { /* non-JSON body */ }
    if (!response.ok) {
      const err = (data && data.error) || {};
      const code = err.code || (response.status === 429 ? "rate_limited" : response.status === 413 ? "payload_too_large" : "unknown");
      throw new ApiError(code, err.message || code, err.retryable || response.status === 429 || response.status >= 500);
    }
    return data;
  }

  /** GET when body is undefined, else POST JSON. */
  function api(path, body) {
    if (body === undefined) return request(path, {});
    return request(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  }

  /** POST raw bytes (a File or Blob) with query parameters; the server reads it in memory. */
  function apiUpload(path, blob, params) {
    const query = new URLSearchParams(params).toString();
    return request(`${path}?${query}`, { method: "POST", headers: { "Content-Type": blob.type }, body: blob });
  }

  function errorText(err) {
    const code = err && err.code ? err.code : "unknown";
    const key = `errors.${code}`;
    return lookup(key) ? t(key) : t("errors.unknown");
  }

  // ---------- Shared UI pieces ----------
  function showLoading(container) {
    clear(container).append(el("div", { class: "loading", role: "status" }, el("span", { class: "spinner", "aria-hidden": "true" }), t("app.loading")));
  }

  /** Honest error with a retry button. Never substitutes pre-written answers. */
  function showError(container, err, retry) {
    const box = el("div", { class: "error-box", role: "alert" }, el("p", {}, errorText(err)));
    if (retry) box.append(el("button", { type: "button", class: "btn btn-danger", onclick: retry }, "↻ ", t("app.retry")));
    clear(container).append(box);
  }

  function aiBlock(text) {
    return el("div", { class: "ai-answer" }, text, el("p", { class: "ai-note" }, "✨ ", t("app.ai_note")));
  }

  /** Disable a button while a task runs, preventing double submits. Without a button the task just runs. */
  async function withBusy(button, task) {
    if (!button) { await task(); return; }
    if (button.disabled) return;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    try { await task(); } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
  }

  /** Run an AI request inside `container` with loading, error + retry handling. */
  async function runAi(container, button, requestFn, render) {
    const attempt = () => withBusy(button, async () => {
      showLoading(container);
      try { render(await requestFn()); } catch (err) { showError(container, err, attempt); }
    });
    await attempt();
  }

  /** The worker's local calendar date (YYYY-MM-DD). */
  function todayISO() { return new Date().toLocaleDateString("en-CA"); }

  function newId() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
  }

  function downloadFile(filename, text, type) {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const link = el("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function copyText(text, button) {
    let ok = false;
    try { await navigator.clipboard.writeText(text); ok = true; } catch {
      const area = el("textarea", { readonly: true, class: "sr-copy" });
      area.value = text;
      document.body.append(area);
      area.select();
      try { ok = document.execCommand("copy"); } catch { ok = false; }
      area.remove();
    }
    if (ok && button) {
      const label = button.lastChild;
      const original = label.textContent;
      label.textContent = t("common.copied");
      setTimeout(() => { label.textContent = original; }, 2000);
    }
  }

  /** Signed, coloured amount with an arrow icon, so meaning never relies on colour alone. */
  function amountTag(kind, amount, srLabel) {
    const income = kind === "income";
    return el("span", { class: income ? "amount amount-income" : "amount amount-expense" },
      el("span", { "aria-hidden": "true" }, income ? "▲ " : "▼ "),
      el("span", { class: "sr-only" }, `${srLabel || t(income ? "ledger.income" : "ledger.expense")}: `),
      `${income ? "+" : "−"}${moneyFormat.format(Math.abs(amount))}`);
  }

  /** Net amount: green and "+" when positive, red and "−" when negative. */
  function netTag(value) {
    return amountTag(value >= 0 ? "income" : "expense", value, t("ledger.net"));
  }

  // ---------- Form helpers ----------
  function formValues(form) {
    const values = {};
    for (const field of form.elements) {
      if (!field.name || field.type === "submit" || field.type === "button") continue;
      if (field.type === "radio") { if (field.checked) values[field.name] = field.value; continue; }
      if (field.type === "checkbox") { (values[field.name] ||= []); if (field.checked) values[field.name].push(field.value); continue; }
      values[field.name] = field.value.trim();
    }
    return values;
  }

  function setFormValues(form, values) {
    for (const [name, value] of Object.entries(values)) {
      for (const field of $$(`[name="${name}"]`, form)) {
        if (field.type === "radio") field.checked = field.value === String(value);
        else if (field.type === "checkbox") field.checked = Array.isArray(value) && value.includes(field.value);
        else if (value !== null && value !== undefined) field.value = String(value);
      }
    }
  }

  /** Validate with simple rules; mark invalid fields and focus the first. */
  function validate(form, extra = () => null) {
    const errorBox = $(".form-error", form);
    let firstBad = null;
    let message = null;
    for (const field of form.elements) {
      if (!field.name || field.type === "hidden" || field.disabled || field.closest("[hidden]")) continue;
      field.removeAttribute("aria-invalid");
      let problem = null;
      const value = field.value.trim();
      if (field.type === "radio") {
        if (field.required && !$$(`[name="${field.name}"]`, form).some((r) => r.checked)) problem = "validation.required";
      } else if (field.type === "checkbox") {
        if (field.dataset.requiredGroup && !$$(`[name="${field.name}"]`, form).some((c) => c.checked)) problem = "validation.pick_one";
      } else if (field.required && !value) problem = "validation.required";
      else if (value && field.type === "number") {
        const n = Number(value);
        if (!Number.isFinite(n) || (field.min !== "" && n < Number(field.min)) || (field.max !== "" && n > Number(field.max))) {
          problem = field.name === "hours" ? "validation.hours" : field.name === "age" ? "validation.age" : "validation.number";
        }
      } else if (value && field.type === "date" && value > todayISO()) problem = "validation.future_date";
      else if (value && field.minLength > 0 && value.length < field.minLength) problem = "validation.min_text";
      if (problem) {
        field.setAttribute("aria-invalid", "true");
        if (!firstBad) { firstBad = field; message = problem; }
      }
    }
    if (!message) {
      const custom = extra();
      if (custom) { message = custom.key; firstBad = custom.field; firstBad.setAttribute("aria-invalid", "true"); }
    }
    if (message) {
      errorBox.textContent = t(message);
      errorBox.hidden = false;
      firstBad.focus();
      return false;
    }
    errorBox.hidden = true;
    return true;
  }

  const num = (value) => (value === "" || value === undefined || value === null ? 0 : Number(value));

  // ---------- Router & cross-feature prefill ----------
  let currentView = "home";
  let booted = false; // move focus to the view heading only on navigation, not on first load
  const renderers = {};
  const onEnter = {};
  const inits = [];

  function goTo(view, prefill) {
    if (prefill) session.set("saathi.prefill", { view, data: prefill });
    const hash = view === "home" ? "#/" : `#/${view}`;
    if (location.hash === hash) handleRoute(); else location.hash = hash;
  }

  function takePrefill(view) {
    const stored = session.get("saathi.prefill");
    if (!stored || stored.view !== view) return null;
    session.remove("saathi.prefill");
    $("#prefill-note").hidden = false;
    return stored.data;
  }

  function handleRoute() {
    const name = (location.hash.replace(/^#\/?/, "") || "home").split("/")[0];
    const view = VIEWS.includes(name) ? name : "home";
    currentView = view;
    $("#prefill-note").hidden = true;
    for (const section of $$("[data-view]")) section.hidden = section.dataset.view !== view;
    if (onEnter[view]) onEnter[view]();
    rerenderCurrentView();
    const heading = $(`[data-view="${view}"] h1`);
    if (booted && heading && document.activeElement !== $("#ask-input")) {
      heading.setAttribute("tabindex", "-1");
      heading.focus({ preventScroll: true });
      window.scrollTo(0, 0);
    }
  }

  function rerenderCurrentView() { if (renderers[currentView]) renderers[currentView](); }

  function showGlobalError(err) {
    const box = $("#global-error");
    box.textContent = errorText(err);
    box.hidden = false;
  }

  async function boot() {
    for (const init of inits) init();
    $("#lang-select").addEventListener("change", (event) => {
      loadLanguage(event.target.value).catch((err) => showGlobalError(err));
    });
    window.addEventListener("hashchange", handleRoute);
    const saved = local.get("saathi.lang");
    const browser = (navigator.language || "en").slice(0, 2);
    try {
      await loadLanguage(LANGS.includes(saved) ? saved : LANGS.includes(browser) ? browser : "en");
    } catch (err) { showGlobalError(err); }
    handleRoute();
    booted = true;
  }

  return Object.freeze({
    LANGS, STATES, WORK_TYPES, PLATFORMS,
    $, $$, el, clear, local, session, profile,
    t, lookup, lang: () => lang, onLanguageChange: (fn) => languageListeners.push(fn), fillSelect,
    api, apiUpload, errorText, showLoading, showError, aiBlock, withBusy, runAi,
    todayISO, newId, downloadFile, copyText, money: (v) => moneyFormat.format(v), amountTag, netTag,
    formValues, setFormValues, validate, num,
    goTo, takePrefill, onEnter, renderers, rerender: rerenderCurrentView,
    register: (init) => inits.push(init), boot,
  });
})();
