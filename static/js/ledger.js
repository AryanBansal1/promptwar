/* Income & Expenses: a browser-only ledger of income and expense entries.
 * Totals are computed by the server in plain Python (POST /api/ledger/summary), never by the LLM.
 */
"use strict";

window.SaathiLedger = (() => {
  const S = window.Saathi;
  const { $, el, clear, t, api } = S;

  const STORAGE_KEY = "saathi.ledger";
  const LEGACY_KEY = "saathi.earnings";
  const VERSION = 2;
  const CATEGORIES = {
    income: ["earnings", "tips", "incentive", "other"],
    expense: ["fuel", "commission", "maintenance", "phone", "food", "other"],
  };
  const PLATFORM_WORK = {
    swiggy: "delivery", zomato: "delivery", blinkit: "delivery", zepto: "delivery", porter: "delivery",
    uber: "ride_hailing", ola: "ride_hailing", bharat_taxi: "ride_hailing", rapido: "bike_taxi",
    urban_company: "home_services",
  };

  // ---------- Storage (versioned, with migration from v1 "earnings") ----------

  /** v1 stored one row per work day (gross + expense breakdown); v2 stores one row per income/expense. */
  function migrateV1(entries) {
    const out = [];
    for (const old of entries) {
      if (!old || !old.id || !old.date) continue;
      const platform = old.platform === "daily_wage" ? "other" : old.platform || null;
      const workType = old.platform === "daily_wage" ? "daily_wage" : PLATFORM_WORK[old.platform] || null;
      if (Number(old.gross) > 0) {
        out.push({ id: old.id, date: old.date, kind: "income", amount: Number(old.gross), category: "earnings",
          platform, work_type: workType, hours: Number(old.hours) || 0, source: "manual", note: "" });
      }
      for (const category of ["fuel", "commission", "maintenance", "other"]) {
        const amount = Number((old.expenses || {})[category]);
        if (amount > 0) {
          out.push({ id: `${old.id}-${category}`, date: old.date, kind: "expense", amount, category,
            platform, work_type: workType, hours: 0, source: "manual", note: "" });
        }
      }
    }
    return out;
  }

  function load() {
    const stored = S.local.get(STORAGE_KEY);
    if (stored && stored.version === VERSION && Array.isArray(stored.entries)) return stored.entries;
    const legacy = S.local.get(LEGACY_KEY);
    if (legacy && legacy.version === 1 && Array.isArray(legacy.entries)) {
      const migrated = migrateV1(legacy.entries);
      if (save(migrated)) S.local.remove(LEGACY_KEY);
      return migrated;
    }
    return []; // missing or unknown future version: never guess its shape
  }

  function save(entries) {
    const ok = S.local.set(STORAGE_KEY, { version: VERSION, entries });
    if (!ok) $("#entry-status").textContent = t("ledger.storage_error");
    return ok;
  }

  /** Add confirmed entries; returns their ids (for Undo) or null if storage failed. */
  function addEntries(entries) {
    const withIds = entries.map((e) => ({ ...e, id: S.newId() }));
    return save([...load(), ...withIds]) ? withIds.map((e) => e.id) : null;
  }

  function removeIds(ids) {
    const drop = new Set(ids);
    return save(load().filter((e) => !drop.has(e.id)));
  }

  /** Server-computed summary, optionally including totals for the worker's local today. */
  function fetchSummary(entries = load()) {
    return api("/api/ledger/summary", { entries, today: S.todayISO() });
  }

  function defaultWorkType(platform) {
    const mine = S.profile.workTypes();
    const guess = PLATFORM_WORK[platform] || null;
    if (guess && (!mine.length || mine.includes(guess))) return guess;
    return mine.length === 1 ? mine[0] : guess;
  }

  // ---------- Form ----------

  function form() { return $("#entry-form"); }

  function fillCategories(kind, selected) {
    const select = $("#e-category");
    clear(select).append(el("option", { value: "" }, t("common.choose")));
    for (const c of CATEGORIES[kind] || []) select.append(el("option", { value: c }, t(`categories.${c}`)));
    if (selected && (CATEGORIES[kind] || []).includes(selected)) select.value = selected;
  }

  function syncKind() {
    const kind = S.formValues(form()).kind || "income";
    fillCategories(kind, $("#e-category").value || (kind === "income" ? "earnings" : ""));
    $("#hours-field").hidden = kind !== "income";
    if (kind !== "income") form().elements.hours.value = "";
  }

  function resetForm() {
    const f = form();
    f.reset();
    f.elements.id.value = "";
    f.elements.date.value = S.todayISO();
    f.elements.date.max = S.todayISO();
    const mine = S.profile.get().platforms;
    if (mine.length === 1) f.elements.platform.value = mine[0];
    f.elements.work_type.value = defaultWorkType(f.elements.platform.value) || "";
    $("#entry-form-title").textContent = t("ledger.add_title");
    $("#entry-cancel").hidden = true;
    $(".form-error", f).hidden = true;
    syncKind();
  }

  /** Fill the form from an entry or a partial draft (used by Edit and by Ask Saathi's Edit button). */
  function fillForm(entry, { editing = false } = {}) {
    const f = form();
    S.setFormValues(f, { kind: entry.kind || "income" });
    syncKind();
    S.setFormValues(f, {
      id: editing ? entry.id : "", date: entry.date || S.todayISO(), amount: entry.amount ?? "",
      category: entry.category || "", platform: entry.platform || "", work_type: entry.work_type || "",
      hours: entry.hours || "", note: entry.note || "",
    });
    $("#entry-form-title").textContent = t(editing ? "ledger.edit_title" : "ledger.add_title");
    $("#entry-cancel").hidden = !editing;
    f.elements.amount.focus();
  }

  function entryFromForm() {
    const v = S.formValues(form());
    const existing = v.id ? load().find((e) => e.id === v.id) : null;
    return {
      id: v.id || S.newId(), date: v.date, kind: v.kind, amount: Number(v.amount), category: v.category,
      platform: v.platform || null, work_type: v.work_type || null,
      hours: v.kind === "income" ? S.num(v.hours) : 0,
      source: existing ? existing.source : "manual", note: v.note || "",
    };
  }

  // ---------- List ----------

  function deleteButton(entry) {
    let armed = false;
    let timer = null;
    const label = el("span", {}, t("common.delete"));
    return el("button", {
      type: "button", class: "btn btn-small btn-danger",
      onclick: () => {
        if (!armed) {
          armed = true;
          label.textContent = t("common.confirm_delete");
          timer = setTimeout(() => { armed = false; label.textContent = t("common.delete"); }, 4000);
          return;
        }
        clearTimeout(timer);
        save(load().filter((e) => e.id !== entry.id));
        $("#entry-status").textContent = t("ledger.deleted");
        render();
      },
    }, "🗑️ ", label);
  }

  function entryLabel(entry) {
    const parts = [entry.date, t(`categories.${entry.category}`)];
    if (entry.platform) parts.push(t(`platforms.${entry.platform}`));
    if (entry.work_type) parts.push(t(`work_types.${entry.work_type}`));
    return parts.join(" · ");
  }

  function renderEntries(entries) {
    const list = clear($("#entries"));
    if (!entries.length) { list.append(el("li", { class: "empty" }, t("ledger.empty"))); return; }
    const sorted = [...entries].sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));
    for (const entry of sorted) {
      const details = [];
      if (entry.hours) details.push(`${entry.hours} ${t("ledger.hours_short")}`);
      if (entry.note) details.push(entry.note);
      list.append(el("li", { class: `entry entry-${entry.kind}` },
        el("div", { class: "entry-head" }, el("span", {}, entryLabel(entry)), S.amountTag(entry.kind, entry.amount)),
        details.length ? el("p", { class: "muted" }, details.join(" · ")) : null,
        entry.source !== "manual" ? el("p", { class: "badge badge-check" }, "⚠️ ", t(`ledger.source_${entry.source}`)) : null,
        el("div", { class: "row" },
          el("button", { type: "button", class: "btn btn-small", onclick: () => fillForm(entry, { editing: true }) }, "✏️ ", el("span", {}, t("common.edit"))),
          deleteButton(entry),
          entry.kind === "income" && entry.platform ? el("button", {
            type: "button", class: "btn btn-small",
            onclick: () => S.goTo("grievance", { issue_type: "unpaid_earnings", platform: entry.platform, start_date: entry.date, amount: entry.amount }),
          }, "⚠️ ", el("span", {}, t("ledger.report"))) : null)));
    }
  }

  // ---------- Summary ----------

  function stat(label, value) {
    return el("li", { class: "stat" }, el("span", { class: "stat-label" }, label), el("span", { class: "stat-value" }, value));
  }

  function table(caption, headers, rows) {
    return el("div", { class: "table-wrap" }, el("table", {},
      el("caption", {}, caption),
      el("thead", {}, el("tr", {}, headers.map((h) => el("th", { scope: "col" }, h)))),
      el("tbody", {}, rows.map((cells) => el("tr", {}, cells.map((c) => el("td", {}, c)))))));
  }

  function totalsRows(rows, labelOf) {
    return rows.map((r) => [labelOf(r), S.amountTag("income", r.income), S.amountTag("expense", r.expenses), S.netTag(r.net)]);
  }

  function drawSummary(container, s) {
    const dash = "—";
    const heads = (first) => [first, t("ledger.income"), t("ledger.expense"), t("ledger.net")];
    const blocks = [
      el("ul", { class: "stats" },
        stat(t("ledger.total_income"), S.amountTag("income", s.total_income)),
        stat(t("ledger.total_expenses"), S.amountTag("expense", s.total_expenses)),
        stat(t("ledger.total_net"), S.netTag(s.total_net)),
        stat(t("ledger.today"), s.today ? S.netTag(s.today.net) : dash),
        stat(t("ledger.per_hour"), s.net_per_hour === null ? dash : S.money(s.net_per_hour)),
        stat(t("ledger.total_hours"), String(s.total_hours)),
        stat(t("ledger.best_platform"), s.best_platform ? t(`platforms.${s.best_platform}`) : dash),
        stat(t("ledger.best_day"), s.best_weekday === null ? dash : t(`weekdays.${s.best_weekday}`))),
      table(t("ledger.weekly"), heads(t("ledger.period")), totalsRows(s.weekly.slice(0, 4), (r) => r.period)),
      table(t("ledger.monthly"), heads(t("ledger.period")), totalsRows(s.monthly.slice(0, 6), (r) => r.period)),
      table(t("ledger.by_platform"), heads(t("ledger.platform")),
        totalsRows(s.platforms, (r) => (r.platform ? t(`platforms.${r.platform}`) : t("ledger.unassigned")))),
    ];
    if (s.work_types.some((w) => w.work_type)) {
      blocks.push(table(t("ledger.by_work_type"), heads(t("ledger.work_type")),
        totalsRows(s.work_types, (r) => (r.work_type ? t(`work_types.${r.work_type}`) : t("ledger.unassigned")))));
    }
    const spend = Object.entries(s.expense_by_category);
    if (spend.length) {
      blocks.push(table(t("ledger.by_category"), [t("ledger.category"), t("ledger.expense")],
        spend.map(([c, v]) => [t(`categories.${c}`), S.amountTag("expense", v)])));
    }
    clear(container).append(...blocks);
  }

  let summaryTicket = 0;
  async function renderSummary(entries) {
    const container = $("#summary");
    $("#advice-btn").disabled = !entries.length;
    $("#export-btn").disabled = !entries.length;
    if (!entries.length) { clear(container).append(el("p", { class: "empty" }, t("ledger.empty"))); return; }
    const ticket = ++summaryTicket;
    S.showLoading(container);
    try {
      const summary = await fetchSummary(entries);
      if (ticket === summaryTicket) drawSummary(container, summary);
    } catch (err) {
      if (ticket === summaryTicket) S.showError(container, err, () => renderSummary(load()));
    }
  }

  function render() {
    const entries = load();
    renderEntries(entries);
    renderSummary(entries);
  }

  // ---------- Export ----------

  function csvCell(value) {
    let text = String(value ?? "");
    if (/^[=+\-@]/.test(text)) text = `'${text}`; // block spreadsheet formula injection
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function exportCsv() {
    const header = ["date", "type", "category", "platform", "work_type", "hours", "amount", "source", "note"];
    const rows = load().map((e) => [e.date, e.kind, e.category, e.platform || "", e.work_type || "", e.hours || 0,
      (e.kind === "income" ? e.amount : -e.amount).toFixed(2), e.source, e.note || ""]);
    const csv = [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n");
    S.downloadFile(`saathi-income-expenses-${S.todayISO()}.csv`, "﻿" + csv, "text/csv;charset=utf-8");
  }

  // ---------- Review (import and Ask Saathi "Edit"): nothing is saved until the worker taps Save ----------

  const REVIEW_KEY = "saathi.ledger.review"; // sessionStorage, so a refresh keeps the review
  const MAX_FILE_BYTES = 5 * 1024 * 1024;
  const FILE_TYPES = ["image/png", "image/jpeg", "image/webp", "application/pdf"];

  /** Show proposed rows for checking. rows: [{date, kind, amount, category, platform, note, low_confidence, needs_date}] */
  function openReview(rows, source, meta = {}) {
    const review = {
      source, found: meta.found ?? rows.length, dropped: meta.dropped || 0,
      rows: rows.map((r) => ({
        include: true, date: r.date || "", kind: r.kind, amount: r.amount, category: r.category,
        platform: r.platform || "", work_type: r.work_type || defaultWorkType(r.platform) || "", note: r.note || r.counterparty || "",
        low_confidence: Boolean(r.low_confidence), needs_date: !r.date,
      })),
    };
    S.session.set(REVIEW_KEY, review);
    renderReview();
    const panel = $("#review-panel");
    if (panel.firstChild) panel.scrollIntoView({ block: "start" });
  }

  function closeReview() { S.session.remove(REVIEW_KEY); clear($("#review-panel")); }

  function selectFrom(options, value, labelOf, attrs) {
    const select = el("select", attrs, options.map((o) => el("option", { value: o }, labelOf(o))));
    select.value = value;
    return select;
  }

  function reviewRow(review, row, index) {
    const id = (name) => `rv-${index}-${name}`;
    const update = (key, value) => { row[key] = value; S.session.set(REVIEW_KEY, review); };
    const include = el("input", { type: "checkbox", id: id("include"), checked: row.include });
    include.addEventListener("change", () => { update("include", include.checked); renderReviewCount(review); });
    const date = el("input", { type: "date", id: id("date"), value: row.date, max: S.todayISO(), required: true });
    date.addEventListener("input", () => update("date", date.value));
    const kind = selectFrom(["income", "expense"], row.kind, (k) => t(`ledger.${k}`), { id: id("kind") });
    const category = selectFrom(CATEGORIES[row.kind], row.category, (c) => t(`categories.${c}`), { id: id("category") });
    kind.addEventListener("change", () => {
      update("kind", kind.value);
      update("category", CATEGORIES[kind.value].includes(row.category) ? row.category : CATEGORIES[kind.value][0]);
      renderReview();
    });
    category.addEventListener("change", () => update("category", category.value));
    const amount = el("input", { type: "number", id: id("amount"), value: row.amount, min: "0.01", max: "1000000", step: "0.01", inputmode: "decimal" });
    amount.addEventListener("input", () => update("amount", amount.value));
    const platform = selectFrom(["", ...S.PLATFORMS], row.platform, (p) => (p ? t(`platforms.${p}`) : t("ledger.platform_none")), { id: id("platform") });
    platform.addEventListener("change", () => update("platform", platform.value));
    const note = el("input", { type: "text", id: id("note"), value: row.note, maxlength: "80" });
    note.addEventListener("input", () => update("note", note.value));
    const field = (name, label, control) => el("div", { class: "field" }, el("label", { for: id(name) }, label), control);
    return el("li", { class: `review-row${row.low_confidence || row.needs_date ? " review-flagged" : ""}`, "data-index": index },
      el("label", { class: "check review-include", for: id("include") }, include, " ", t("import.include"), " ", S.amountTag(row.kind, Number(row.amount) || 0)),
      row.low_confidence ? el("p", { class: "flag" }, "⚠️ ", t("import.low_confidence")) : null,
      row.needs_date ? el("p", { class: "flag" }, "📅 ", t("import.needs_date")) : null,
      el("div", { class: "grid2" },
        field("date", t("ledger.date"), date), field("kind", t("ledger.kind"), kind),
        field("amount", t("ledger.amount"), amount), field("category", t("ledger.category"), category),
        field("platform", t("ledger.platform"), platform), field("note", t("ledger.note"), note)));
  }

  function renderReviewCount(review) {
    const button = $("#review-save");
    if (button) button.lastChild.textContent = t("import.save_selected", { count: review.rows.filter((r) => r.include).length });
  }

  function renderReview() {
    const panel = $("#review-panel");
    const review = S.session.get(REVIEW_KEY);
    if (!review) { clear(panel); return; }
    const sourceLabel = t(review.source === "import" ? "ledger.source_import" : "ledger.source_voice");
    const card = el("section", { class: "card review", "aria-labelledby": "review-title" },
      el("h2", { id: "review-title", tabindex: "-1" }, "🧾 ", t("import.review_title")),
      el("p", { class: "badge badge-check" }, "⚠️ ", sourceLabel));
    if (review.dropped) card.append(el("p", { class: "alert alert-info" }, t("import.dropped", { count: review.dropped })));
    if (!review.rows.length) {
      card.append(el("p", { class: "empty" }, t("import.none_found")),
        el("button", { type: "button", class: "btn", onclick: closeReview }, t("common.close")));
      clear(panel).append(card);
      return;
    }
    const error = el("p", { class: "form-error", role: "alert", hidden: true });
    const save = el("button", { type: "button", class: "btn btn-primary", id: "review-save" }, "💾 ", el("span", {}));
    save.addEventListener("click", () => saveReview(review, error));
    card.append(el("ul", { class: "review-list" }, review.rows.map((row, i) => reviewRow(review, row, i))), error,
      el("div", { class: "row" }, save, el("button", { type: "button", class: "btn", onclick: closeReview }, t("common.cancel"))));
    clear(panel).append(card);
    renderReviewCount(review);
  }

  function reviewProblem(row) {
    const amount = Number(row.amount);
    if (!row.date) return "import.needs_date";
    if (row.date > S.todayISO()) return "validation.future_date";
    if (!Number.isFinite(amount) || amount <= 0 || amount > 1000000) return "validation.number";
    if (!CATEGORIES[row.kind].includes(row.category)) return "validation.required";
    return null;
  }

  function saveReview(review, error) {
    const chosen = review.rows.map((row, index) => ({ row, index })).filter(({ row }) => row.include);
    if (!chosen.length) { error.textContent = t("validation.pick_one"); error.hidden = false; return; }
    const bad = chosen.find(({ row }) => reviewProblem(row));
    if (bad) {
      error.textContent = t(reviewProblem(bad.row));
      error.hidden = false;
      const field = $(`#rv-${bad.index}-${bad.row.date ? "amount" : "date"}`);
      field.setAttribute("aria-invalid", "true");
      field.focus();
      return;
    }
    const ids = addEntries(chosen.map(({ row }) => ({
      date: row.date, kind: row.kind, amount: Number(row.amount), category: row.category,
      platform: row.platform || null, work_type: row.work_type || null, hours: 0, source: review.source, note: (row.note || "").slice(0, 80),
    })));
    if (!ids) return;
    closeReview();
    $("#entry-status").textContent = t("import.saved", { count: ids.length });
    render();
  }

  // ---------- Import (screenshot / PDF / pasted SMS) ----------

  function showFormError(form, key) {
    const box = $(".form-error", form);
    box.textContent = t(key);
    box.hidden = false;
  }

  function initImport() {
    const fileForm = $("#import-file-form");
    const textForm = $("#import-text-form");
    const status = $("#import-status");
    fileForm.addEventListener("submit", (event) => {
      event.preventDefault();
      $(".form-error", fileForm).hidden = true;
      const file = $("#import-file").files[0];
      if (!file) return showFormError(fileForm, "import.pick_file");
      if (!FILE_TYPES.includes(file.type)) return showFormError(fileForm, "errors.unsupported_file_type");
      if (file.size > MAX_FILE_BYTES) return showFormError(fileForm, "import.too_big");
      S.runAi(status, $("button[type=submit]", fileForm),
        () => S.apiUpload("/api/ledger/import/file", file, { language: S.lang(), today: S.todayISO() }),
        (res) => { clear(status); fileForm.reset(); openReview(res.rows, "import", res); });
    });
    textForm.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!S.validate(textForm)) return;
      const text = $("#import-text").value.trim();
      S.runAi(status, $("button[type=submit]", textForm),
        () => api("/api/ledger/import/text", { text, language: S.lang(), today: S.todayISO() }),
        (res) => { clear(status); openReview(res.rows, "import", res); });
    });
  }

  // ---------- Init ----------

  function init() {
    initImport();
    const f = form();
    for (const radio of f.elements.kind) radio.addEventListener("change", syncKind);
    f.elements.platform.addEventListener("change", () => {
      if (!f.elements.work_type.value) f.elements.work_type.value = defaultWorkType(f.elements.platform.value) || "";
    });
    f.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!S.validate(f)) return;
      const entry = entryFromForm();
      const entries = load().filter((e) => e.id !== entry.id);
      entries.push(entry);
      if (save(entries)) $("#entry-status").textContent = t("ledger.saved");
      resetForm();
      render();
    });
    $("#entry-cancel").addEventListener("click", resetForm);
    $("#export-btn").addEventListener("click", exportCsv);
    $("#advice-btn").addEventListener("click", (event) => {
      const entries = load();
      if (!entries.length) return;
      const box = $("#advice-result");
      S.runAi(box, event.currentTarget, () => api("/api/ledger/advice", { entries, today: S.todayISO(), language: S.lang() }), (res) => {
        clear(box).append(el("h3", {}, "✨ ", t("ledger.advice_title")),
          el("ol", {}, res.tips.map((tip) => el("li", {}, tip))),
          el("p", { class: "ai-note" }, t("app.ai_note")));
      });
    });
    S.onEnter.ledger = () => {
      if (!f.elements.id.value) resetForm();
      const prefill = S.takePrefill("ledger");
      if (prefill && prefill.review) openReview(prefill.review, prefill.source || "voice");
      else if (prefill) fillForm({ kind: "income", platform: prefill.platform, amount: prefill.amount });
    };
    S.onLanguageChange(() => syncKind());
    S.renderers.ledger = () => { render(); renderReview(); };
  }

  S.register(init);
  return Object.freeze({ CATEGORIES, load, addEntries, removeIds, fetchSummary, defaultWorkType, render, migrateV1, openReview });
})();
