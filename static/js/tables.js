/* Resizable table columns, and the tick-boxes that go with them.
 *
 * Seven columns of food data don't suit one set of widths: hunting a brand
 * wants a wide Company column, comparing macros wants the numbers close
 * together. So each heading gets a grab handle, and the widths are remembered
 * per table in localStorage.
 *
 * Resizing needs `table-layout: fixed` — with the automatic algorithm the
 * browser recomputes every column from its content and any width you set is
 * a suggestion it feels free to ignore. Fixed layout is applied here, in
 * JavaScript, rather than in the stylesheet: without this file the table
 * should still size itself sensibly rather than sit at whatever widths the
 * fixed algorithm picks from the first row.
 *
 * Handles are keyboard-operable. A drag-only affordance is one a lot of
 * people simply cannot use.
 */
(function (window, document) {
  "use strict";

  var STORE = "crumbs:column-widths";
  var MIN_WIDTH = 56;
  var STEP = 16;

  // --- Remembered widths --------------------------------------------------

  function readAll() {
    try {
      return JSON.parse(window.localStorage.getItem(STORE) || "{}") || {};
    } catch (error) {
      return {};
    }
  }

  function saveWidths(key, widths) {
    if (!key) return;
    try {
      var all = readAll();
      all[key] = widths;
      window.localStorage.setItem(STORE, JSON.stringify(all));
    } catch (error) {
      /* Widths just won't persist. The drag still worked. */
    }
  }

  function forgetWidths(key) {
    try {
      var all = readAll();
      delete all[key];
      window.localStorage.setItem(STORE, JSON.stringify(all));
    } catch (error) {
      /* Nothing to do. */
    }
  }

  // --- One table ----------------------------------------------------------

  function setUp(table) {
    var key = table.dataset.tableKey || "";
    var headings = Array.prototype.slice.call(table.tHead.rows[0].cells);
    var saved = readAll()[key];

    // Freeze the widths the automatic algorithm just worked out, then switch
    // to fixed. Going straight to fixed would collapse the layout first.
    var current = headings.map(function (th) { return th.getBoundingClientRect().width; });
    table.style.tableLayout = "fixed";
    table.style.width = "100%";

    function apply(widths) {
      headings.forEach(function (th, index) {
        th.style.width = Math.max(MIN_WIDTH, widths[index]) + "px";
      });
    }

    apply(saved && saved.length === headings.length ? saved : current);

    function widths() {
      return headings.map(function (th) { return th.getBoundingClientRect().width; });
    }

    function remember() {
      saveWidths(key, widths());
      showReset();
    }

    function resize(index, width) {
      headings[index].style.width = Math.max(MIN_WIDTH, width) + "px";
    }

    // --- Handles ----------------------------------------------------------

    headings.forEach(function (th, index) {
      if (index === headings.length - 1) return; // nothing to its right

      var handle = document.createElement("span");
      handle.className = "col-resize";
      handle.tabIndex = 0;
      handle.setAttribute("role", "separator");
      handle.setAttribute("aria-orientation", "vertical");
      handle.setAttribute(
        "aria-label",
        "Resize the " + (th.textContent.trim() || "first") + " column"
      );

      var startX = 0;
      var startWidth = 0;

      handle.addEventListener("pointerdown", function (event) {
        event.preventDefault();
        startX = event.clientX;
        startWidth = th.getBoundingClientRect().width;
        handle.setPointerCapture(event.pointerId);
        handle.dataset.dragging = "true";
      });

      handle.addEventListener("pointermove", function (event) {
        if (handle.dataset.dragging !== "true") return;
        resize(index, startWidth + (event.clientX - startX));
      });

      function release(event) {
        if (handle.dataset.dragging !== "true") return;
        delete handle.dataset.dragging;
        if (handle.hasPointerCapture && handle.hasPointerCapture(event.pointerId)) {
          handle.releasePointerCapture(event.pointerId);
        }
        remember();
      }

      handle.addEventListener("pointerup", release);
      handle.addEventListener("pointercancel", release);

      handle.addEventListener("keydown", function (event) {
        var width = th.getBoundingClientRect().width;
        if (event.key === "ArrowRight") {
          resize(index, width + STEP);
        } else if (event.key === "ArrowLeft") {
          resize(index, width - STEP);
        } else if (event.key === "Home") {
          resize(index, current[index]);
        } else {
          return;
        }
        event.preventDefault();
        remember();
      });

      // Double-click a handle to give that column back its measured width.
      handle.addEventListener("dblclick", function () {
        resize(index, current[index]);
        remember();
      });

      th.appendChild(handle);
    });

    // --- Reset ------------------------------------------------------------

    var reset = document.createElement("button");
    reset.type = "button";
    reset.className = "btn btn--quiet table-reset";
    reset.textContent = "Reset column widths";
    reset.hidden = !(saved && saved.length === headings.length);
    reset.addEventListener("click", function () {
      apply(current);
      forgetWidths(key);
      reset.hidden = true;
    });

    function showReset() {
      reset.hidden = false;
    }

    var caption = table.caption;
    if (caption) {
      caption.appendChild(reset);
    } else {
      table.parentNode.insertBefore(reset, table);
    }
  }

  // --- Batch selection ----------------------------------------------------

  function wireSelection(form) {
    var all = form.querySelector("[data-select-all]");
    var boxes = Array.prototype.slice.call(
      form.querySelectorAll('input[name="selected"]')
    );
    var readout = form.querySelector("[data-selection-count]");
    if (!boxes.length) return;

    // The server imports at most this many per submission. With a page size
    // above it, "select all" can tick more than one go can take, so say so
    // here rather than letting the server quietly drop the tail.
    var capHolder = form.querySelector("[data-import-cap]");
    var cap = capHolder ? parseInt(capHolder.dataset.importCap, 10) : 0;

    function update() {
      var chosen = boxes.filter(function (box) { return box.checked; });
      if (all) {
        all.checked = chosen.length === boxes.length;
        // Some-but-not-all is its own state, and the box should show it.
        all.indeterminate = chosen.length > 0 && chosen.length < boxes.length;
      }
      if (readout) {
        if (!chosen.length) {
          readout.textContent = "Nothing selected yet.";
        } else if (cap && chosen.length > cap) {
          readout.textContent =
            chosen.length + " selected — only the first " + cap +
            " will import. Go again for the rest.";
        } else {
          readout.textContent = chosen.length + " selected";
        }
        readout.dataset.over = String(Boolean(cap && chosen.length > cap));
      }
    }

    if (all) {
      all.addEventListener("change", function () {
        boxes.forEach(function (box) { box.checked = all.checked; });
        update();
      });
    }
    boxes.forEach(function (box) { box.addEventListener("change", update); });
    update();
  }

  // A select that reloads on change, with its Apply button as the no-JS path.
  function wireAutoSubmit() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-submit-fallback]"),
      function (button) { button.hidden = true; }
    );
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-submit-on-change]"),
      function (select) {
        select.addEventListener("change", function () {
          if (select.form) select.form.submit();
        });
      }
    );
  }

  function begin() {
    wireAutoSubmit();
    Array.prototype.forEach.call(
      document.querySelectorAll("table[data-resizable]"),
      function (table) {
        if (table.tHead && table.tHead.rows.length) setUp(table);
      }
    );
    Array.prototype.forEach.call(
      document.querySelectorAll("form"),
      function (form) {
        if (form.querySelector('input[name="selected"]')) wireSelection(form);
      }
    );
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", begin);
  } else {
    begin();
  }
})(window, document);
