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

/* Writing a note in a dialog, then placing it by hand.
 *
 * Kept apart from the reading behaviour above: that runs for everyone, this
 * only for someone who can edit. Both are no-ops without their markup.
 */
(function (window, document) {
  "use strict";

  // --- The dialog ---------------------------------------------------------

  var dialog = document.getElementById("note-dialog");
  if (dialog && typeof dialog.showModal === "function") {
    document.addEventListener("click", function (event) {
      if (!(event.target instanceof Element)) return;

      var opener = event.target.closest("[data-note-dialog-open]");
      if (opener) {
        // Only now: without this the link goes to the full page form, which
        // is the fallback when <dialog> is unsupported.
        event.preventDefault();
        dialog.showModal();
        var first = dialog.querySelector("textarea, input:not([type=hidden]), select");
        if (first) first.focus();
        return;
      }

      if (event.target.closest("[data-note-dialog-close]")) {
        dialog.close();
      }
    });

    // Clicking the backdrop closes it. The dialog element itself is the
    // backdrop's hit target, so a click landing on it and not on the form
    // came from outside.
    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
    });
  }

  // --- Placing ------------------------------------------------------------

  var toolbar = document.querySelector("[data-placing-note]");
  if (!toolbar) return;

  var id = toolbar.dataset.placingNote;
  var icon = document.querySelector('.note-icon[data-note-id="' + id + '"]');
  var popover = document.querySelector('.note-popover[data-note-panel="' + id + '"]');
  if (!icon) return;

  var anchor = icon.closest(".note-anchor") || icon.parentElement;
  var rotation = parseInt(toolbar.dataset.rotation, 10) || 0;
  var readout = toolbar.querySelector("[data-angle-readout]");
  var token = toolbar.querySelector("[name=csrfmiddlewaretoken]");

  // Out of the way while placing: an expanding note under the cursor makes
  // the icon impossible to aim at.
  if (popover) popover.hidden = true;
  icon.dataset.read = "true";

  function percentOf(rect, clientX, clientY) {
    return {
      x: Math.max(0, Math.min(100, ((clientX - rect.left) / rect.width) * 100)),
      y: Math.max(0, Math.min(100, ((clientY - rect.top) / rect.height) * 100))
    };
  }

  var position = {
    x: parseFloat(icon.style.left) || 50,
    y: parseFloat(icon.style.top) || 50
  };

  function paint() {
    icon.style.left = position.x.toFixed(2) + "%";
    icon.style.top = position.y.toFixed(2) + "%";
    icon.style.setProperty("--note-rotation", rotation + "deg");
    if (readout) readout.textContent = rotation + "\u00b0";
  }

  // --- Drag ---------------------------------------------------------------

  icon.addEventListener("pointerdown", function (event) {
    event.preventDefault();
    icon.setPointerCapture(event.pointerId);
    icon.dataset.dragging = "true";
  });

  icon.addEventListener("pointermove", function (event) {
    if (icon.dataset.dragging !== "true") return;
    position = percentOf(anchor.getBoundingClientRect(), event.clientX, event.clientY);
    paint();
  });

  function drop(event) {
    if (icon.dataset.dragging !== "true") return;
    delete icon.dataset.dragging;
    if (icon.hasPointerCapture && icon.hasPointerCapture(event.pointerId)) {
      icon.releasePointerCapture(event.pointerId);
    }
  }

  icon.addEventListener("pointerup", drop);
  icon.addEventListener("pointercancel", drop);

  // --- Keyboard -----------------------------------------------------------

  var NUDGE = { ArrowLeft: [-2, 0], ArrowRight: [2, 0], ArrowUp: [0, -2], ArrowDown: [0, 2] };

  icon.addEventListener("keydown", function (event) {
    var move = NUDGE[event.key];
    if (move) {
      position.x = Math.max(0, Math.min(100, position.x + move[0]));
      position.y = Math.max(0, Math.min(100, position.y + move[1]));
      event.preventDefault();
      paint();
    } else if (event.key === "," || event.key === "<") {
      turn(-15);
      event.preventDefault();
    } else if (event.key === "." || event.key === ">") {
      turn(15);
      event.preventDefault();
    }
  });

  // --- Rotate -------------------------------------------------------------

  function turn(degrees) {
    // Wraps rather than stopping: past 359 comes back to 0, which is what
    // an upside-down note being a feature actually requires.
    rotation = (((rotation + degrees) % 360) + 360) % 360;
    paint();
  }

  toolbar.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    var rotate = event.target.closest("[data-rotate]");
    if (rotate) {
      var amount = rotate.dataset.rotate;
      if (amount === "reset") {
        rotation = 0;
        paint();
      } else {
        turn(parseInt(amount, 10));
      }
      return;
    }

    if (event.target.closest("[data-placing-done]")) save();
  });

  // --- Saving -------------------------------------------------------------

  function save() {
    var body = new FormData();
    body.append("offset_x", position.x.toFixed(2));
    body.append("offset_y", position.y.toFixed(2));
    body.append("rotation", String(rotation));
    if (token) body.append("csrfmiddlewaretoken", token.value);

    window
      .fetch(toolbar.dataset.placeUrl, {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        // Drop ?place= so a reload doesn't reopen placement.
        window.location.replace(window.location.pathname);
      })
      .catch(function () {
        toolbar.dataset.failed = "true";
        var hint = toolbar.querySelector(".placing__hint");
        if (hint) hint.textContent = "Couldn't save that — check your connection and press Done again.";
      });
  }

  paint();
})(window, document);
