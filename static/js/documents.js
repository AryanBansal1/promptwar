/* Document Help: curated checklists with saved ticks, plus grounded Gemini steps and Q&A. */
"use strict";

(() => {
  const S = window.Saathi;
  const { $, el, clear, t } = S;
  const TARGET_KEY = "saathi.docs.target";
  let checklist = null;
  let pendingQuestion = "";

  const progressKey = (target) => `saathi.docs.progress.${target.kind}.${target.id}`;
  const qaKey = (target) => `saathi.docs.qa.${target.kind}.${target.id}`;

  function docName(item) { return S.lookup(`doc_items.${item.id}`) ? t(`doc_items.${item.id}`) : item.name; }

  function render() {
    const panel = $("#doc-panel");
    const target = S.session.get(TARGET_KEY);
    $("#d-goal").value = target && target.kind === "goal" ? target.id : "";
    if (!target) { clear(panel).append(el("p", { class: "empty card" }, t("documents.pick_goal"))); return; }
    if (!checklist || checklist.id !== target.id || checklist.kind !== target.kind) { loadChecklist(target); return; }

    const done = new Set(S.local.get(progressKey(target), []));
    const title = target.kind === "goal" ? t(`goals.${target.id}`) : t("documents.for_scheme", { name: checklist.title });
    const progressText = el("p", { id: "doc-progress-text" });
    const bar = el("progress", { max: checklist.items.length, "aria-labelledby": "doc-progress-text" });
    const updateProgress = () => {
      const count = checklist.items.filter((i) => done.has(i.id)).length;
      bar.value = count;
      progressText.textContent = t("documents.progress", { done: count, total: checklist.items.length });
    };
    const items = checklist.items.map((item) => {
      const id = `doc-${item.id}`;
      const box = el("input", { type: "checkbox", id, checked: done.has(item.id) });
      box.addEventListener("change", () => {
        if (box.checked) done.add(item.id); else done.delete(item.id);
        S.local.set(progressKey(target), [...done]);
        updateProgress();
      });
      return el("li", { class: "check-item" }, box, el("label", { for: id }, docName(item), el("span", { class: "muted" }, item.description)));
    });
    updateProgress();

    const explainBox = el("div", { "aria-live": "polite" });
    const explainBtn = el("button", { type: "button", class: "btn btn-primary" }, "✨ ", el("span", {}, t("documents.explain")));
    const explainKey = `saathi.docs.explain.${S.lang()}.${target.kind}.${target.id}`;
    const cachedExplain = S.session.get(explainKey);
    if (cachedExplain) explainBox.append(S.aiBlock(cachedExplain));
    explainBtn.addEventListener("click", () => S.runAi(explainBox, explainBtn,
      () => S.api("/api/documents/explain", { kind: target.kind, id: target.id, language: S.lang() }),
      (res) => { S.session.set(explainKey, res.text); clear(explainBox).append(S.aiBlock(res.text)); }));

    clear(panel).append(
      el("section", { class: "card", "aria-labelledby": "doc-title" },
        el("h2", { id: "doc-title" }, title), progressText, bar,
        el("h3", {}, t("documents.checklist")), el("ul", { class: "checklist" }, items),
        el("details", {}, el("summary", {}, t("documents.steps")), el("ol", {}, checklist.steps.map((s) => el("li", {}, s)))),
        el("div", { class: "row" },
          el("a", { class: "btn btn-small", href: checklist.official_url, target: "_blank", rel: "noopener noreferrer" }, "🔗 ", t("common.official_site")))),
      el("section", { class: "card", "aria-labelledby": "doc-explain-title" },
        el("h2", { id: "doc-explain-title" }, t("documents.explain_title")), explainBtn, explainBox),
      qaSection(target));
    if (pendingQuestion && target.kind === "goal") {
      $("#doc-question").value = pendingQuestion;
      pendingQuestion = "";
    }
    if (window.SaathiVoice) window.SaathiVoice.attachAll(panel);
  }

  function qaSection(target) {
    const turns = S.session.get(qaKey(target), []);
    const log = el("ol", { class: "chat-log", "aria-live": "polite" }, turns.map((turn) => el("li", {
      class: turn.role === "user" ? "msg msg-user" : "msg msg-assistant",
    }, el("span", { class: "msg-who" }, turn.role === "user" ? t("safety.you") : `✨ ${t("safety.saathi")}`), turn.text)));
    const input = el("textarea", { id: "doc-question", name: "question", rows: "2", maxlength: "1000", placeholder: t("documents.ask_placeholder"), "data-voice": "" });
    const button = el("button", { type: "submit", class: "btn btn-primary" }, "➤ ", el("span", {}, t("documents.ask_button")));
    const status = el("div", { "aria-live": "assertive" });
    const form = el("form", { novalidate: true }, el("label", { for: "doc-question" }, t("documents.ask_label")), input, el("div", { class: "row" }, button), status);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const question = input.value.trim();
      if (question.length < 2) { input.focus(); return; }
      const history = turns.slice(-10);
      S.runAi(status, button, () => S.api("/api/documents/ask", { kind: target.kind, id: target.id, language: S.lang(), question, history }), (res) => {
        turns.push({ role: "user", text: question }, { role: "assistant", text: res.text });
        S.session.set(qaKey(target), turns.slice(-20));
        render();
        const again = $("#doc-question");
        if (again) again.focus();
      });
    });
    return el("section", { class: "card", "aria-labelledby": "doc-qa-title" }, el("h2", { id: "doc-qa-title" }, t("documents.ask_label")), log, form);
  }

  async function loadChecklist(target) {
    const panel = $("#doc-panel");
    S.showLoading(panel);
    try {
      checklist = await S.api(`/api/documents/checklist/${encodeURIComponent(target.kind)}/${encodeURIComponent(target.id)}`);
      render();
    } catch (err) { S.showError(panel, err, () => loadChecklist(target)); }
  }

  function init() {
    $("#d-goal").addEventListener("change", (event) => {
      const id = event.target.value;
      if (id) S.session.set(TARGET_KEY, { kind: "goal", id }); else S.session.remove(TARGET_KEY);
      render();
    });
    S.onEnter.documents = () => {
      const prefill = S.takePrefill("documents");
      if (!prefill) return;
      if (prefill.kind && prefill.id) S.session.set(TARGET_KEY, { kind: prefill.kind, id: prefill.id });
      else if (prefill.document_goal) S.session.set(TARGET_KEY, { kind: "goal", id: prefill.document_goal });
      pendingQuestion = (prefill.question || prefill.message || "").slice(0, 1000);
    };
    S.renderers.documents = render;
  }

  S.register(init);
})();
