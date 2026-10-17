/* Ask Saathi: Gemini classifies the message (fixed intent enum) and routes with context pre-filled.
 * For money said in words ("Rapido se 150 kamaye"), it shows a confirmation card. Nothing is saved
 * until the worker taps Save, and Undo is offered for 10 seconds afterwards.
 */
"use strict";

(() => {
  const S = window.Saathi;
  const { $, el, clear, t } = S;
  const ROUTE_VIEW = { "#/ledger": "ledger", "#/safety": "safety", "#/schemes": "schemes", "#/documents": "documents", "#/grievance": "grievance" };
  const UNDO_SECONDS = 10;

  /** Proposed items as ledger entries (the worker has not confirmed them yet). */
  function toEntries(items) {
    return items.map((item) => ({
      date: item.date, kind: item.kind, amount: item.amount, category: item.category,
      platform: item.platform || null, work_type: window.SaathiLedger.defaultWorkType(item.platform) || null,
      hours: 0, source: "voice", note: "",
    }));
  }

  function itemLine(item) {
    const parts = [t(`categories.${item.category}`)];
    if (item.platform) parts.push(t(`platforms.${item.platform}`));
    parts.push(item.date === S.todayISO() ? t("ledger.today") : item.date);
    return el("li", { class: `confirm-item${item.low_confidence ? " review-flagged" : ""}` },
      S.amountTag(item.kind, item.amount), " ", el("span", {}, parts.join(" · ")),
      item.low_confidence ? el("span", { class: "flag" }, " ⚠️ ", t("import.low_confidence")) : null);
  }

  async function showSaved(result, ids) {
    let summary = null;
    try { summary = await window.SaathiLedger.fetchSummary(); } catch { summary = null; }
    const today = summary && summary.today;
    const totals = today
      ? el("p", {}, t("ask.today_total"), " ", S.amountTag("income", today.income), " ", S.amountTag("expense", today.expenses), " = ", S.netTag(today.net))
      : null;
    const undo = el("button", { type: "button", class: "btn" }, "↩️ ", el("span", {}));
    let left = UNDO_SECONDS;
    const tick = () => { undo.lastChild.textContent = t("ask.undo", { seconds: left }); };
    tick();
    const timer = setInterval(() => {
      left -= 1;
      if (left <= 0) { clearInterval(timer); undo.remove(); } else tick();
    }, 1000);
    undo.addEventListener("click", () => {
      clearInterval(timer);
      window.SaathiLedger.removeIds(ids);
      clear(result).append(el("p", { class: "alert alert-info", role: "status" }, t("ask.undone")));
    });
    clear(result).append(el("div", { class: "alert alert-success", role: "status" },
      el("p", {}, "✅ ", t("import.saved", { count: ids.length })), totals,
      el("div", { class: "row" }, undo, el("a", { class: "btn", href: "#/ledger" }, t("nav.ledger")))));
    undo.focus();
  }

  function showConfirmation(result, res) {
    const items = res.transactions;
    const card = el("section", { class: "confirm-card", "aria-labelledby": "confirm-title" },
      el("h2", { id: "confirm-title", tabindex: "-1" }, "🧾 ", t(items.length ? "ask.confirm_title" : "ask.no_amounts")));
    if (res.dropped) card.append(el("p", { class: "alert alert-info" }, t("ask.dropped", { count: res.dropped })));
    if (items.length) {
      const save = el("button", { type: "button", class: "btn btn-primary" }, "💾 ", el("span", {}, t("ask.save_all")));
      save.addEventListener("click", () => {
        const ids = window.SaathiLedger.addEntries(toEntries(items));
        if (ids) showSaved(result, ids);
      });
      const edit = el("button", { type: "button", class: "btn" }, "✏️ ", el("span", {}, t("common.edit")));
      edit.addEventListener("click", () => S.goTo("ledger", { review: items.map((i) => ({ ...i, note: "" })), source: "voice" }));
      card.append(el("p", { class: "badge badge-check" }, "⚠️ ", t("ledger.source_voice")),
        el("ul", { class: "plain-list" }, items.map(itemLine)),
        el("div", { class: "row" }, save, edit,
          el("button", { type: "button", class: "btn", onclick: () => clear(result) }, t("common.cancel"))));
    }
    clear(result).append(card);
    $("#confirm-title").focus();
  }

  function init() {
    const form = $("#ask-form");
    const result = $("#ask-result");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const message = $("#ask-input").value.trim();
      if (message.length < 2) { $("#ask-input").focus(); return; }
      const body = { language: S.lang(), message, today: S.todayISO(), work_types: S.profile.workTypes() };
      S.runAi(result, $("button[type=submit]", form), () => S.api("/api/ask", body), (res) => {
        if (res.intent === "log_transaction") { showConfirmation(result, res); return; }
        const view = ROUTE_VIEW[res.route];
        if (!view) {
          clear(result).append(el("h2", {}, t("ask.answer_title")), S.aiBlock(res.answer));
          return;
        }
        const section = t(`nav.${view}`);
        clear(result).append(el("p", {}, t("ask.opening", { section })),
          el("a", { class: "btn", href: `#/${view}` }, t("ask.open", { section })));
        S.goTo(view, { ...res.prefill, message });
      });
    });
  }

  S.register(init);
})();
