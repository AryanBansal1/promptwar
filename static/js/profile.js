/* My Work: the worker's kinds of work and platforms (multi-select), stored only in this browser.
 * Used across the app: platform lists show the worker's own platforms first, scheme answers are pre-ticked,
 * and safety chat / Ask Saathi receive the work types (never personal details) as context.
 */
"use strict";

(() => {
  const S = window.Saathi;
  const { $, $$, el, clear, t } = S;

  function checkboxes(container, name, values, selected, labelPrefix) {
    clear(container).append(...values.map((value, i) => el("label", { class: "check" },
      el("input", { type: "checkbox", name, value, checked: selected.includes(value), "data-required-group": i === 0 && name === "work_types" ? "true" : null }),
      ` ${t(`${labelPrefix}.${value}`)}`)));
  }

  function render() {
    const form = $("#profile-form");
    const current = form.dataset.dirty ? S.formValues(form) : S.profile.get();
    checkboxes($("#p-work-types"), "work_types", S.WORK_TYPES, current.work_types || [], "work_types");
    checkboxes($("#p-platforms"), "platforms", S.PLATFORMS, current.platforms || [], "platforms");
  }

  function renderOnboarding() {
    $("#onboarding").hidden = S.profile.get().onboarding !== "pending";
  }

  function init() {
    const form = $("#profile-form");
    form.addEventListener("change", () => { form.dataset.dirty = "1"; });
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (!S.validate(form)) return;
      const values = S.formValues(form);
      const ok = S.profile.save({ work_types: values.work_types || [], platforms: values.platforms || [], onboarding: "done" });
      delete form.dataset.dirty;
      $("#profile-status").textContent = t(ok ? "profile.saved" : "ledger.storage_error");
      for (const select of $$("select[data-options=platforms]")) S.fillSelect(select); // own platforms first
      renderOnboarding();
    });
    $("#onboarding-skip").addEventListener("click", () => {
      S.profile.save({ onboarding: "skipped" });
      renderOnboarding();
      $("#ask-input").focus();
    });
    S.onEnter.profile = () => { delete form.dataset.dirty; $("#profile-status").textContent = ""; };
    S.onEnter.home = renderOnboarding;
    S.renderers.profile = render;
  }

  S.register(init);
})();
