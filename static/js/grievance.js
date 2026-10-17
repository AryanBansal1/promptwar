/* Grievance drafter: Gemini writes the letters; where to send them comes from escalation.json. */
"use strict";

(() => {
  const S = window.Saathi;
  const { $, el, clear, t } = S;
  const FORM_KEY = "saathi.grievance.form";
  const RESULT_KEY = "saathi.grievance.result";

  const external = { target: "_blank", rel: "noopener noreferrer" };

  function contactItem(contact) {
    const help = S.lookup(`escalation.${contact.kind}`) ? t(`escalation.${contact.kind}`) : "";
    return el("li", { class: "contact" },
      el("span", { class: "contact-name" }, contact.name), help ? el("span", { class: "muted" }, help) : null,
      contact.email ? el("span", { class: "muted" }, "✉️ ", contact.email) : null,
      el("div", { class: "row" },
        contact.phone ? el("a", { class: "btn btn-small", href: `tel:${contact.phone}` }, "📞 ", t("common.call", { number: contact.phone })) : null,
        contact.help_url ? el("a", { class: "btn btn-small", href: contact.help_url, ...external }, "🛟 ", t("grievance.help_page")) : null,
        contact.url ? el("a", { class: "btn btn-small", href: contact.url, ...external }, "🔗 ", t("common.official_site")) : null));
  }

  /** The letter's own "Subject:" line (any script), else a neutral fallback. */
  function subjectOf(letter) {
    const line = letter.split("\n").find((l) => /^\s*(subject|subj|विषय|ਵਿਸ਼ਾ|பொருள்|বিষয়)\s*[:：-]/i.test(l));
    return line ? line.replace(/^[^:：-]*[:：-]\s*/, "").trim().slice(0, 150) : t("grievance.email_subject");
  }

  /** Real share actions only: mailto to a confirmed official email, WhatsApp share, copy and download. */
  function letterBlock(title, text, filename, fullText, platformEmail) {
    const copyBtn = el("button", { type: "button", class: "btn btn-small" }, "📋 ", el("span", {}, t("common.copy")));
    copyBtn.addEventListener("click", () => S.copyText(text, copyBtn));
    const downloadBtn = el("button", { type: "button", class: "btn btn-small", onclick: () => S.downloadFile(filename, fullText(text), "text/plain;charset=utf-8") },
      "⬇️ ", el("span", {}, t("common.download")));
    const mail = platformEmail
      ? el("a", { class: "btn btn-small", href: `mailto:${platformEmail}?subject=${encodeURIComponent(subjectOf(text))}&body=${encodeURIComponent(text)}` },
        "✉️ ", t("grievance.send_email"))
      : null;
    const whatsapp = el("a", { class: "btn btn-small", href: `https://wa.me/?text=${encodeURIComponent(text)}`, ...external }, "💬 ", t("grievance.share_whatsapp"));
    return el("section", { class: "card" }, el("h2", {}, title), el("div", { class: "letter" }, text),
      el("div", { class: "row" }, mail, whatsapp, copyBtn, downloadBtn),
      platformEmail ? el("p", { class: "muted" }, t("grievance.email_to", { email: platformEmail })) : el("p", { class: "muted" }, t("grievance.no_email")));
  }

  function render() {
    const container = $("#grievance-result");
    const res = S.session.get(RESULT_KEY);
    if (!res) { clear(container); return; }
    const sendTo = res.send_to.map((c) => [c.name, c.phone || "", c.url || ""].filter(Boolean).join(" – ")).join("\n");
    const withContacts = (letter) => `${letter}\n\n----\n${t("grievance.send_to")}:\n${sendTo}\n`;
    const base = `${t("grievance.file_name")}-${S.todayISO()}`;
    const platform = res.send_to.find((c) => c.kind === "platform_support");
    const email = platform ? platform.email : null;
    clear(container).append(
      el("p", { class: "alert alert-info" }, "✏️ ", t("grievance.placeholders")),
      letterBlock(t("grievance.letter_en"), res.letter_english, `${base}-en.txt`, withContacts, email),
      res.letter_local ? letterBlock(t("grievance.letter_local"), res.letter_local, `${base}-${res.language}.txt`, withContacts, email) : null,
      el("section", { class: "card" }, el("h2", {}, "✨ ", t("grievance.next_steps")),
        el("ol", {}, res.next_steps.map((s) => el("li", {}, s))), el("p", { class: "ai-note" }, t("app.ai_note"))),
      el("section", { class: "card" }, el("h2", {}, "📮 ", t("grievance.send_to")), el("ul", { class: "plain-list" }, res.send_to.map(contactItem))));
  }

  function payload(v) {
    const body = {
      language: S.lang(), issue_type: v.issue_type, platform: v.platform, employer_name: v.employer_name,
      start_date: v.start_date, description: v.description,
    };
    if (v.state) body.state = v.state;
    if (v.end_date) body.end_date = v.end_date;
    if (v.amount !== "") body.amount = Number(v.amount);
    return body;
  }

  function init() {
    const form = $("#grievance-form");
    const result = $("#grievance-result");
    form.addEventListener("input", () => S.session.set(FORM_KEY, S.formValues(form)));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const ok = S.validate(form, () => {
        const v = S.formValues(form);
        return v.end_date && v.start_date && v.end_date < v.start_date ? { key: "validation.end_before_start", field: form.elements.end_date } : null;
      });
      if (!ok) return;
      const values = S.formValues(form);
      S.session.set(FORM_KEY, values);
      S.runAi(result, $("button[type=submit]", form), () => S.api("/api/grievance/draft", payload(values)), (res) => {
        S.session.set(RESULT_KEY, res);
        render();
        const heading = $("#grievance-result h2");
        if (heading) heading.scrollIntoView({ block: "start" });
      });
    });
    S.onEnter.grievance = () => {
      form.elements.start_date.max = S.todayISO();
      form.elements.end_date.max = S.todayISO();
      const saved = S.session.get(FORM_KEY);
      if (saved) S.setFormValues(form, saved);
      const prefill = S.takePrefill("grievance");
      if (prefill) {
        S.session.remove(RESULT_KEY);
        form.reset();
        S.setFormValues(form, {
          issue_type: prefill.issue_type || "", platform: prefill.platform || "",
          start_date: prefill.start_date || "", amount: prefill.amount ?? "",
          description: (prefill.description || prefill.message || "").slice(0, 2000),
        });
        S.session.set(FORM_KEY, S.formValues(form));
      }
    };
    S.renderers.grievance = render;
  }

  S.register(init);
})();
