/* Sticky note behaviour, one file for all three modes.
 *
 * Read state is the interesting part. "Pulses until tapped once for each time
 * the recipe is used" means unread is per *cook*, not per note — so it lives
 * in sessionStorage against a session token the server stamps on the page.
 * That gives the right lifetime for free (a closed tab is a finished cook)
 * and keeps one user's read state out of another's, without a write to the
 * database every time someone taps a triangle. The trade: read state doesn't
 * follow you to a second device mid-cook.
 *
 * Without this file the notes still render — every one of them is in the
 * HTML, and the <details>-free markup degrades to all notes visible. What is
 * lost is the expanding, the pulse, and the split panel.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-notes]");
  if (!root) return;

  var SESSION = root.getAttribute("data-cook-session") || "none";
  var KEY = "crumbs:notes-read:" + SESSION;

  // --- Read state --------------------------------------------------------

  function readSet() {
    try {
      return new Set(JSON.parse(window.sessionStorage.getItem(KEY) || "[]"));
    } catch (error) {
      return new Set();
    }
  }

  function markRead(id) {
    try {
      var seen = readSet();
      seen.add(String(id));
      window.sessionStorage.setItem(KEY, JSON.stringify(Array.from(seen)));
    } catch (error) {
      /* Private browsing, or storage full. The pulse just keeps going. */
    }
  }

  function paintReadState() {
    var seen = readSet();
    document.querySelectorAll("[data-note-id]").forEach(function (element) {
      var id = element.getAttribute("data-note-id");
      element.setAttribute("data-read", seen.has(String(id)) ? "true" : "false");
    });
  }

  // --- Opening and closing ----------------------------------------------

  function popoverFor(id) {
    return document.querySelector('.note-popover[data-note-panel="' + id + '"]');
  }

  function closeAll(except) {
    document.querySelectorAll(".note-popover, .note-overlay").forEach(function (panel) {
      if (panel !== except) panel.hidden = true;
    });
    document.querySelectorAll("[aria-expanded=true]").forEach(function (control) {
      if (!except || control.getAttribute("data-note-id") !== except.getAttribute("data-note-panel")) {
        control.setAttribute("aria-expanded", "false");
      }
    });
  }

  function open(control, panel) {
    closeAll(panel);
    panel.hidden = false;
    control.setAttribute("aria-expanded", "true");
    markRead(control.getAttribute("data-note-id"));
    paintReadState();

    // Grow out of the icon's own position, at the icon's own angle.
    panel.setAttribute("data-opening", "true");
    window.requestAnimationFrame(function () {
      panel.removeAttribute("data-opening");
    });
  }

  root.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    var control = event.target.closest("[data-note-id]");
    if (control && control.matches(".note-icon, .note-tab")) {
      var id = control.getAttribute("data-note-id");
      var panel = popoverFor(id) || document.querySelector('.note-overlay[data-note-panel="' + id + '"]');
      if (!panel) return;
      event.preventDefault();
      if (panel.hidden) {
        open(control, panel);
      } else {
        panel.hidden = true;
        control.setAttribute("aria-expanded", "false");
      }
      return;
    }

    // Tap anywhere on an open note to put it away. A close button on an
    // upside-down note sits where nobody looks for it.
    var inside = event.target.closest(".note-popover, .note-overlay");
    if (inside && !event.target.closest("a, button, input, textarea, select")) {
      inside.hidden = true;
      var owner = document.querySelector(
        '[data-note-id="' + inside.getAttribute("data-note-panel") + '"][aria-expanded]'
      );
      if (owner) owner.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeAll(null);
      var split = document.querySelector(".note-split");
      if (split) split.hidden = true;
    }
  });

  // --- Split mode --------------------------------------------------------

  var split = document.querySelector(".note-split");
  if (split) {
    document.addEventListener("click", function (event) {
      if (!(event.target instanceof Element)) return;

      if (event.target.closest("[data-note-split-open]")) {
        split.hidden = false;
        readSet();
        document.querySelectorAll(".note-split [data-note-id]").forEach(function (note) {
          markRead(note.getAttribute("data-note-id"));
        });
        paintReadState();
        var heading = split.querySelector("h2");
        if (heading) heading.focus();
        return;
      }

      if (event.target.closest("[data-note-split-close]")) {
        split.hidden = true;
      }
    });
  }

  paintReadState();
})();
