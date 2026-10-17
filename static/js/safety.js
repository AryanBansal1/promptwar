/* Safety Assistant: Gemini chat. Helplines are hardcoded in index.html, never generated. */
"use strict";

(() => {
  const S = window.Saathi;
  const { $, $$, el, clear, t } = S;
  const CHAT_KEY = "saathi.safety.chat";

  function chatTurns() { return S.session.get(CHAT_KEY, []); }

  function renderChat() {
    const log = clear($("#chat-log"));
    const turns = chatTurns();
    if (!turns.length) { log.append(el("li", { class: "empty" }, t("safety.empty"))); return; }
    turns.forEach((turn, index) => {
      const isUser = turn.role === "user";
      const item = el("li", { class: isUser ? "msg msg-user" : "msg msg-assistant" },
        el("span", { class: "msg-who" }, isUser ? t("safety.you") : `✨ ${t("safety.saathi")}`), turn.text);
      if (!isUser && turn.issue_type) {
        const question = turns[index - 1] ? turns[index - 1].text : "";
        item.append(el("div", { class: "row" }, el("button", {
          type: "button", class: "btn btn-small btn-primary",
          onclick: () => S.goTo("grievance", { issue_type: turn.issue_type, description: question }),
        }, "📝 ", el("span", {}, t("safety.draft_complaint")))));
      }
      if (!isUser) item.append(el("p", { class: "ai-note" }, t("app.ai_note")));
      log.append(item);
    });
  }

  function sendSafety(message) {
    const button = $("#safety-form button[type=submit]");
    const errorBox = $("#safety-error");
    const history = chatTurns().slice(-10).map((turn) => ({ role: turn.role, text: turn.text.slice(0, 1500) }));
    const attempt = () => S.withBusy(button, async () => {
      S.showLoading(errorBox);
      try {
        const res = await S.api("/api/safety/chat", { language: S.lang(), message, history, work_types: S.profile.workTypes() });
        const turns = chatTurns();
        turns.push({ role: "user", text: message }, { role: "assistant", text: res.reply, issue_type: res.suggest_complaint ? res.issue_type : null });
        S.session.set(CHAT_KEY, turns.slice(-20));
        $("#safety-input").value = "";
        clear(errorBox);
        renderChat();
      } catch (err) { S.showError(errorBox, err, attempt); }
    });
    return attempt();
  }

  function init() {
    $("#safety-form").addEventListener("submit", (event) => {
      event.preventDefault();
      const message = $("#safety-input").value.trim();
      if (message.length < 2) { $("#safety-input").focus(); return; }
      sendSafety(message);
    });
    for (const chip of $$("#safety-topics .chip")) {
      chip.addEventListener("click", () => {
        $("#safety-input").value = t(`safety.q_${chip.dataset.topic}`);
        $("#safety-input").focus();
      });
    }
    $("#chat-clear").addEventListener("click", () => { S.session.remove(CHAT_KEY); clear($("#safety-error")); renderChat(); });
    S.onEnter.safety = () => {
      const prefill = S.takePrefill("safety");
      const question = prefill && (prefill.question || prefill.message);
      if (question) { $("#safety-input").value = question.slice(0, 1000); sendSafety(question.slice(0, 1000)); }
    };
    S.renderers.safety = renderChat;
  }

  S.register(init);
})();
