/* Rights & Welfare Schemes: rule-based matching in code, then a Gemini explanation of matched records only. */
"use strict";

(() => {
  const S = window.Saathi;
  const { $, el, clear, t } = S;
  const PROFILE_KEY = "saathi.schemes.answers";
  let matches = null;
  let explainInFlight = null; // cache key of the explanation request currently running

  function schemeCard(match) {
    const s = match.scheme;
    return el("li", { class: "scheme" },
      el("h3", {}, s.kind === "law" ? "⚖️ " : "🏛️ ", s.name),
      s.kind === "law" ? el("span", { class: "badge" }, t("schemes.law")) : null,
      el("p", {}, el("strong", {}, `${t("schemes.why")}: `), match.reason),
      el("details", {}, el("summary", {}, t("schemes.details")),
        el("p", {}, s.summary), el("ul", {}, s.benefits.map((b) => el("li", {}, b))),
        el("ol", {}, s.how_to_apply.map((step) => el("li", {}, step))),
        el("p", { class: "muted" }, t("schemes.verified", { date: s.last_verified }))),
      el("p", { class: "warn" }, "⚠️ ", t("schemes.check_official")),
      el("div", { class: "row" },
        el("a", { class: "btn btn-small", href: s.official_url, target: "_blank", rel: "noopener noreferrer" }, "🔗 ", t("common.official_site")),
        el("button", { type: "button", class: "btn btn-small btn-primary", onclick: () => S.goTo("documents", { kind: "scheme", id: s.id }) },
          "📄 ", el("span", {}, t("schemes.docs_button")))));
  }

  function render() {
    renderWorkTypeOptions();
    const container = $("#scheme-results");
    if (!matches) { clear(container); return; }
    const { matches: found, unmatched_needs_bank: needsBank, answers } = matches;
    const card = el("section", { class: "card", "aria-labelledby": "scheme-results-title" },
      el("h2", { id: "scheme-results-title" }, t("schemes.results")));
    if (!found.length) card.append(el("p", { class: "empty" }, t("schemes.none")));
    else card.append(el("ul", { class: "plain-list" }, found.map(schemeCard)));
    if (needsBank.length) card.append(el("p", { class: "alert alert-info" }, t("schemes.needs_bank", { list: needsBank.map((id) => id.toUpperCase().replace(/_/g, "-")).join(", ") })));
    clear(container).append(card);
    if (!found.length) return;
    const explainBox = el("div", { "aria-live": "polite" });
    card.append(el("h3", {}, "✨ ", t("schemes.explain_title")), explainBox);
    const cacheKey = `saathi.schemes.explain.${S.lang()}.${found.map((m) => m.scheme.id).join(",")}`;
    const cached = S.session.get(cacheKey);
    if (cached) { explainBox.append(S.aiBlock(cached)); return; }
    if (explainInFlight === cacheKey) { S.showLoading(explainBox); return; }
    explainInFlight = cacheKey;
    S.runAi(explainBox, null, () => S.api("/api/schemes/explain", { ...answers, language: S.lang() }), (res) => {
      S.session.set(cacheKey, res.explanation);
      clear(explainBox).append(S.aiBlock(res.explanation));
    }).finally(() => {
      if (explainInFlight === cacheKey) explainInFlight = null;
      if (!explainBox.isConnected) render(); // view was re-rendered meanwhile: refresh it
    });
  }

  /** Work-type checkboxes, pre-ticked from the worker's profile. */
  function renderWorkTypeOptions() {
    const box = $("#s-work-types");
    const checked = new Set(S.formValues($("#schemes-form")).work_types || []);
    if (!box.children.length && !checked.size) S.profile.workTypes().forEach((w) => checked.add(w));
    clear(box).append(...S.WORK_TYPES.map((w, i) => el("label", { class: "check" },
      el("input", { type: "checkbox", name: "work_types", value: w, checked: checked.has(w), "data-required-group": i === 0 ? "true" : null }),
      ` ${t(`work_types.${w}`)}`)));
  }

  function toAnswers(values) {
    return {
      age: Number(values.age), monthly_income: Number(values.monthly_income), work_types: values.work_types || [],
      state: values.state, has_bank_account: values.has_bank_account === "true",
    };
  }

  async function find(answers) {
    const container = $("#scheme-results");
    S.showLoading(container);
    try {
      const res = await S.api("/api/schemes/match", answers);
      matches = { ...res, answers };
      render();
    } catch (err) { S.showError(container, err, () => find(answers)); }
  }

  function init() {
    const form = $("#schemes-form");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!S.validate(form)) return;
      const values = S.formValues(form);
      S.session.set(PROFILE_KEY, values);
      S.withBusy($("button[type=submit]", form), () => find(toAnswers(values)));
    });
    S.onEnter.schemes = () => {
      S.takePrefill("schemes");
      renderWorkTypeOptions();
      const saved = S.session.get(PROFILE_KEY);
      if (saved && !matches) { S.setFormValues(form, saved); find(toAnswers(saved)); }
    };
    S.renderers.schemes = render;
  }

  S.register(init);
})();
