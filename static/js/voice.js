/* Voice input: a mic button next to free-text fields marked with data-voice.
 * 1) Browser Web Speech API (live interim text), in the selected language.
 * 2) Fallback: MediaRecorder (max 60 s) -> POST /api/voice/transcribe (Gemini audio input).
 * 3) Neither available (or no microphone): the mic button is never shown.
 * The transcript is only inserted into the field for the worker to check; forms are never auto-submitted.
 */
"use strict";

window.SaathiVoice = (() => {
  const S = window.Saathi;
  const { $$, el, t } = S;
  const LOCALES = { en: "en-IN", hi: "hi-IN", pa: "pa-IN", ta: "ta-IN", bn: "bn-IN", mr: "mr-IN" };
  const MAX_SECONDS = 60;
  const MAX_AUDIO_BYTES = 2 * 1024 * 1024;
  const RECORDER_TYPES = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4", "audio/webm", "audio/ogg"];

  // Looked up when the mic is pressed, so a speech engine that loads late is still used.
  const speechCtor = () => window.SpeechRecognition || window.webkitSpeechRecognition || null;
  const canRecord = Boolean(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder);
  let available = Boolean(speechCtor() || canRecord); // refined once we know whether a microphone exists
  let useRecorder = false; // switched on if the browser's speech service fails (network / unsupported language)
  let active = null; // the one session that is currently listening

  const languageName = () => {
    const option = document.querySelector(`#lang-select option[value="${S.lang()}"]`);
    return option ? option.textContent : S.lang();
  };

  // ---------- UI per field ----------

  function setState(ui, state, message = "") {
    ui.button.dataset.state = state;
    ui.button.classList.toggle("listening", state === "listening");
    ui.button.setAttribute("aria-pressed", String(state === "listening"));
    ui.button.setAttribute("aria-label", t(state === "listening" ? "voice.stop" : "voice.start", { language: languageName() }));
    ui.button.disabled = state === "processing";
    ui.icon.textContent = state === "listening" ? "⏹" : state === "processing" ? "⏳" : "🎤";
    ui.status.textContent = message;
    ui.status.classList.toggle("voice-error", state === "error");
  }

  /** Insert text: appended (with a space) to what the worker already typed, within the field's maxlength. */
  function insert(field, base, text) {
    const joined = [base.trimEnd(), text.trim()].filter(Boolean).join(" ");
    const max = field.maxLength > 0 ? field.maxLength : Infinity;
    field.value = joined.slice(0, max);
    field.dispatchEvent(new Event("input", { bubbles: true })); // keeps saved drafts in sync; never submits
  }

  function fail(ui, code) {
    setState(ui, "error", t(`voice.${code}`));
    active = null;
  }

  // ---------- 1) Web Speech API ----------

  function startSpeech(ui) {
    const Ctor = speechCtor();
    const rec = new Ctor();
    rec.lang = LOCALES[S.lang()] || "en-IN";
    rec.interimResults = true;
    rec.continuous = true;
    const base = ui.field.value;
    let finalText = "";
    let interim = "";
    let errored = false;
    rec.onresult = (event) => {
      interim = "";
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) finalText += `${result[0].transcript} `;
        else interim += result[0].transcript;
      }
      insert(ui.field, base, `${finalText}${interim}`); // live text while speaking
    };
    rec.onerror = (event) => {
      errored = true;
      const code = event.error;
      if (code === "aborted") return;
      if ((code === "network" || code === "language-not-supported" || code === "service-not-allowed") && canRecord) {
        useRecorder = true; // the browser's speech service cannot help: use the server fallback from now on
        active = null;
        startRecorder(ui);
        return;
      }
      fail(ui, { "not-allowed": "permission_denied", "no-speech": "no_speech", "audio-capture": "no_mic",
        network: "network", "language-not-supported": "unsupported_language", "service-not-allowed": "unsupported_language" }[code] || "failed");
    };
    rec.onend = () => {
      if (errored || active?.ui !== ui) return;
      const heard = `${finalText}${interim}`.trim();
      insert(ui.field, base, heard);
      active = null;
      if (heard) setState(ui, "idle", t("voice.done"));
      else fail(ui, "no_speech");
    };
    active = { ui, stop: () => rec.stop() };
    setState(ui, "listening", t("voice.listening", { language: languageName() }));
    try { rec.start(); } catch { fail(ui, "failed"); }
  }

  // ---------- 2) MediaRecorder + Gemini fallback ----------

  async function startRecorder(ui) {
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (err) {
      fail(ui, err && err.name === "NotFoundError" ? "no_mic" : err && err.name === "NotAllowedError" ? "permission_denied" : "failed");
      return;
    }
    const mimeType = RECORDER_TYPES.find((type) => MediaRecorder.isTypeSupported(type)) || "";
    const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    const chunks = [];
    let left = MAX_SECONDS;
    const countdown = () => setState(ui, "listening", t("voice.listening_countdown", { language: languageName(), seconds: left }));
    const timer = setInterval(() => { left -= 1; if (left <= 0) recorder.stop(); else countdown(); }, 1000);
    recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
    recorder.onstop = async () => {
      clearInterval(timer);
      stream.getTracks().forEach((track) => track.stop());
      active = null;
      const blob = new Blob(chunks, { type: (recorder.mimeType || mimeType || "audio/webm").split(";")[0] });
      if (!blob.size) { fail(ui, "no_speech"); return; }
      if (blob.size > MAX_AUDIO_BYTES) { fail(ui, "too_long"); return; }
      setState(ui, "processing", t("voice.processing"));
      try {
        const res = await S.apiUpload("/api/voice/transcribe", blob, { language: S.lang() });
        insert(ui.field, ui.field.value, res.text);
        setState(ui, "idle", t("voice.done"));
      } catch (err) {
        const code = err && err.code;
        fail(ui, code === "no_speech" ? "no_speech" : code === "network" ? "network" : code === "rate_limited" ? "busy" : "failed");
      }
    };
    active = { ui, stop: () => { if (recorder.state !== "inactive") recorder.stop(); } };
    recorder.start();
    countdown();
  }

  // ---------- Wiring ----------

  function toggle(ui) {
    if (active && active.ui === ui) { active.stop(); return; } // tapping again stops listening
    if (active) active.stop();
    ui.status.textContent = "";
    if (useRecorder || !speechCtor()) startRecorder(ui); else startSpeech(ui);
  }

  function attach(field) {
    if (!available || field.dataset.voiceReady) return;
    field.dataset.voiceReady = "1";
    const icon = el("span", { "aria-hidden": "true" }, "🎤");
    const button = el("button", { type: "button", class: "btn-mic", "aria-pressed": "false", "aria-controls": field.id || null }, icon);
    const status = el("p", { class: "voice-status", "aria-live": "polite", role: "status" });
    const ui = { field, button, icon, status };
    button.addEventListener("click", () => toggle(ui));
    field.addEventListener("keydown", (event) => { if (event.key === "Escape" && active && active.ui === ui) active.stop(); });
    const wrap = el("div", { class: "voice-wrap" });
    field.replaceWith(wrap);
    wrap.append(field, button);
    wrap.after(status);
    setState(ui, "idle");
  }

  function attachAll(root = document) {
    for (const field of $$("[data-voice]", root)) attach(field);
  }

  function refreshLabels() {
    for (const button of $$(".btn-mic")) {
      const listening = button.dataset.state === "listening";
      button.setAttribute("aria-label", t(listening ? "voice.stop" : "voice.start", { language: languageName() }));
    }
  }

  /** Hide voice completely when there is no microphone at all. */
  async function detectMicrophone() {
    if (!available || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (devices.length && !devices.some((d) => d.kind === "audioinput")) {
        available = false;
        for (const button of $$(".btn-mic")) button.parentElement.replaceWith(button.previousElementSibling);
        for (const status of $$(".voice-status")) status.remove();
      }
    } catch { /* keep the button: errors are reported when it is used */ }
  }

  S.register(() => {
    attachAll();
    S.onLanguageChange(() => { if (active) active.stop(); refreshLabels(); });
    detectMicrophone();
  });

  return Object.freeze({ attachAll, isAvailable: () => available });
})();
