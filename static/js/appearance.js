/* The theme picker and the light/dark toggle.
 *
 * Both post to the server, which sets the cookie and redirects back, so the
 * whole thing works with JavaScript off — the picker is a real form and the
 * toggle is a real button inside one. What this file adds is the immediate
 * repaint, so choosing a theme doesn't feel like a page load.
 *
 * Mode cycles auto -> light -> dark -> auto. Getting back to auto matters:
 * once you've pinned it you can never again follow the browser's own
 * setting, and on a phone that setting is often on a schedule.
 */
(function () {
  "use strict";

  var html = document.documentElement;

  function resolve(pref) {
    if (pref === "light" || pref === "dark") return pref;
    var ask = window.matchMedia;
    if (ask && ask("(prefers-color-scheme: dark)").matches) return "dark";
    if (ask && ask("(prefers-color-scheme: light)").matches) return "light";
    var hour = new Date().getHours();
    return (hour < 7 || hour >= 19) ? "dark" : "light";
  }

  var NEXT = { auto: "light", light: "dark", dark: "auto" };
  var LABEL = { auto: "Lighting: auto", light: "Lighting: light", dark: "Lighting: dark" };

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    var toggle = event.target.closest("[data-mode-toggle]");
    if (toggle) {
      var form = toggle.closest("form");
      var current = html.getAttribute("data-mode-pref") || "auto";
      var next = NEXT[current] || "light";

      // Repaint now; the form submit persists it.
      html.setAttribute("data-mode-pref", next);
      html.setAttribute("data-mode", resolve(next));
      toggle.textContent = LABEL[next];
      toggle.setAttribute("aria-label", LABEL[next]);

      if (form) {
        var field = form.querySelector("[name=mode]");
        if (field) field.value = next;
      }
      return;
    }

    // Repaint on theme choice too, so the picker previews itself. The form
    // still submits; this just gets ahead of the round trip.
    var choice = event.target.closest("[data-theme-choice]");
    if (choice) {
      html.setAttribute("data-theme", choice.getAttribute("data-theme-choice"));
    }
  });

  // With JavaScript the select submits itself, so the Set button is
  // redundant — but it has to exist in the HTML for the no-JS case.
  document.querySelectorAll("[data-theme-submit]").forEach(function (button) {
    button.hidden = true;
  });

  document.addEventListener("change", function (event) {
    if (!(event.target instanceof Element)) return;
    var select = event.target.closest("[data-theme-select]");
    if (select && select.form) {
      html.setAttribute("data-theme", select.value);
      select.form.submit();
    }
  });

  // Keep up if the browser's setting changes while the page is open — the
  // only way "auto" means anything after load.
  if (window.matchMedia) {
    var query = window.matchMedia("(prefers-color-scheme: dark)");
    var listen = query.addEventListener ? "addEventListener" : "addListener";
    var handler = function () {
      if ((html.getAttribute("data-mode-pref") || "auto") === "auto") {
        html.setAttribute("data-mode", resolve("auto"));
      }
    };
    if (query.addEventListener) query.addEventListener("change", handler);
    else if (query.addListener) query.addListener(handler);
  }
})();
