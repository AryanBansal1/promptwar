/* Theme: follows the phone's light/dark setting until the worker picks one; the choice is saved.
 * Loaded synchronously in <head> (not deferred) so the page never flashes the wrong theme.
 */
"use strict";

(() => {
  const KEY = "saathi.theme";
  const root = document.documentElement;
  const media = window.matchMedia("(prefers-color-scheme: dark)");

  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch { saved = null; }
  if (saved === "light" || saved === "dark") root.dataset.theme = saved;

  const isDark = () => (root.dataset.theme ? root.dataset.theme === "dark" : media.matches);

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("theme-toggle");
    if (!button) return;
    const sync = () => button.setAttribute("aria-pressed", String(isDark()));
    sync();
    button.addEventListener("click", () => {
      const next = isDark() ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem(KEY, next); } catch { /* not saved; still applied for this visit */ }
      sync();
    });
    media.addEventListener("change", sync);
  });
})();
