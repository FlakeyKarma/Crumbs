/* Two search popups: ingredients on the editor, foods on a reference.
 *
 * Both are the same interaction — type, wait, pick — so the searching and
 * rendering is shared and only what happens on a pick differs. Each picker
 * carries its own endpoint on the input, so this file knows nothing about
 * either URL.
 *
 * Neither is load-bearing. Without this file the ingredient rows are still
 * typed by hand with the existing datalist, and a reference renders as a
 * button that does nothing — which is why the button is only drawn for
 * references the server knows about.
 */
(function (window, document) {
  "use strict";

  var DEBOUNCE = 220;
  var MIN_LETTERS = 2;

  function token() {
    var field = document.querySelector("[name=csrfmiddlewaretoken]");
    return field ? field.value : "";
  }

  // --- Searching ----------------------------------------------------------

  function wire(dialog, onPick) {
    var input = dialog.querySelector("[data-picker-input]");
    var results = dialog.querySelector("[data-picker-results]");
    var status = dialog.querySelector("[data-picker-status]");
    if (!input || !results) return null;

    var timer = null;
    var sequence = 0;

    function show(items) {
      results.textContent = "";
      items.forEach(function (item) {
        var row = document.createElement("li");
        var button = document.createElement("button");
        button.type = "button";
        button.className = "picker__result";
        // textContent throughout: these are names off an API and out of a
        // database, not markup.
        button.textContent = item.name;

        if (item.description) {
          var note = document.createElement("span");
          note.className = "picker__note";
          note.textContent = item.description;
          button.appendChild(note);
        }
        if (item.known === false) {
          var tag = document.createElement("span");
          tag.className = "picker__tag";
          tag.textContent = "from the catalogue";
          button.appendChild(tag);
        }

        button.addEventListener("click", function () {
          onPick(item);
          dialog.close();
        });
        row.appendChild(button);
        results.appendChild(row);
      });
    }

    function search() {
      var term = input.value.trim();
      if (term.length < MIN_LETTERS) {
        show([]);
        status.textContent = "Type at least two letters.";
        return;
      }

      var mine = ++sequence;
      status.textContent = "Searching\u2026";

      window
        .fetch(input.dataset.pickerUrl + "?q=" + encodeURIComponent(term), {
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" }
        })
        .then(function (response) {
          if (!response.ok) throw new Error(response.status);
          return response.json();
        })
        .then(function (data) {
          // A slow earlier request must not overwrite a fast later one.
          if (mine !== sequence) return;
          show(data.results || []);
          status.textContent = (data.results || []).length
            ? ""
            : "Nothing matched that.";
        })
        .catch(function () {
          if (mine !== sequence) return;
          status.textContent = "Couldn't search just now.";
        });
    }

    input.addEventListener("input", function () {
      window.clearTimeout(timer);
      timer = window.setTimeout(search, DEBOUNCE);
    });

    dialog.addEventListener("click", function (event) {
      if (event.target === dialog) dialog.close();
      if (event.target instanceof Element && event.target.closest("[data-picker-close]")) {
        dialog.close();
      }
    });

    return {
      open: function () {
        input.value = "";
        show([]);
        status.textContent = "Type at least two letters.";
        if (typeof dialog.showModal === "function") dialog.showModal();
        input.focus();
      }
    };
  }

  // --- The "+" box on the recipe editor -----------------------------------

  var ingredientDialog = document.getElementById("ingredient-picker");
  if (ingredientDialog && typeof ingredientDialog.showModal === "function") {
    var picker = wire(ingredientDialog, function (item) {
      // Reuse the formset's own add-a-row path rather than building a row
      // here: one place knows how a row is made.
      var adder = document.querySelector('[data-add-row="ingredient"]');
      if (adder) adder.click();

      var rows = document.querySelectorAll("#ingredient-rows .formset-row");
      var last = rows[rows.length - 1];
      if (!last) return;
      var name = last.querySelector('input[name$="-name"]');
      if (name) {
        name.value = item.name;
        var quantity = last.querySelector('input[name$="-quantity"]');
        if (quantity) quantity.focus();
      }
    });

    document.addEventListener("click", function (event) {
      if (!(event.target instanceof Element)) return;
      if (event.target.closest("[data-ingredient-picker-open]")) {
        event.preventDefault();
        picker.open();
      }
    });
  }

  // --- Adding a phrasing ---------------------------------------------------
  //
  // The panel is a <details>, so it opens without this. All the script adds
  // is Cancel actually closing it — type="reset" alone just empties the box
  // and leaves you looking at it.

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    var cancel = event.target.closest("[data-cancel]");
    if (!cancel) return;
    var panel = cancel.closest("details");
    if (panel) {
      window.setTimeout(function () { panel.open = false; }, 0);
    }
  });

  // --- Attaching a portion to a reference ----------------------------------

  var portionDialog = document.getElementById("portion-picker");
  if (portionDialog && typeof portionDialog.showModal === "function") {
    var attachTo = null;

    var portionPicker = wire(portionDialog, function (item) {
      if (!attachTo) return;
      // Fill the form's own select and submit it, rather than posting from
      // here: one path to the server, and it still works without this file.
      var select = attachTo.querySelector("select[name=meal_food]");
      if (select) {
        var option = select.querySelector('option[value="' + item.id + '"]');
        if (!option) {
          option = document.createElement("option");
          option.value = item.id;
          select.appendChild(option);
        }
        select.value = String(item.id);
      }
      attachTo.submit();
    });

    // The dropdown is the no-script fallback. With the script here, the
    // search is better, so the select steps aside.
    document.querySelectorAll("[data-attach-fallback]").forEach(function (span) {
      span.hidden = true;
    });

    document.addEventListener("click", function (event) {
      if (!(event.target instanceof Element)) return;
      var opener = event.target.closest("[data-attach-open]");
      if (!opener) return;
      attachTo = opener.closest("[data-attach-form]");
      portionPicker.open();
    });
  }

  // --- References ---------------------------------------------------------

  var foodDialog = document.getElementById("food-picker");
  if (!foodDialog || typeof foodDialog.showModal !== "function") return;

  var active = null;

  function paint(button, label, bound) {
    button.textContent = label;
    button.dataset.bound = bound ? "true" : "false";
    button.setAttribute("aria-label", label + " \u2014 choose a food");
  }

  function send(button, body) {
    body.append("csrfmiddlewaretoken", token());
    body.append("recipe", recipeSlug());
    return window
      .fetch("/references/" + button.dataset.reference + "/bind/", {
        method: "POST",
        body: body,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      })
      .then(function (response) {
        if (!response.ok) throw new Error(response.status);
        return response.json();
      })
      .then(function (data) {
        // Every button for the same filler, not just the one clicked: a
        // reference usually appears in more than one step.
        document
          .querySelectorAll('.reference[data-reference="' + button.dataset.reference + '"]')
          .forEach(function (twin) {
            paint(twin, data.label, data.bound);
          });
      })
      .catch(function () {
        button.dataset.failed = "true";
      });
  }

  var foodPicker = wire(foodDialog, function (item) {
    if (!active) return;
    var body = new window.FormData();
    body.append("meal_food", item.id);
    send(active, body);
  });

  function recipeSlug() {
    var article = document.querySelector("[data-recipe]");
    return article ? article.dataset.recipe : "";
  }

  /* The reference's own shortlist, shown before anyone types. The search is
     still there underneath — the list is a shortcut, not a fence. */
  function preload(button) {
    var id = button.dataset.reference;
    var results = foodDialog.querySelector("[data-picker-results]");
    var status = foodDialog.querySelector("[data-picker-status]");
    var offer = foodDialog.querySelector("[data-picker-create]");

    if (!id) {
      // Nothing recognises this phrase yet. Offer to make one rather than
      // sending someone off to the pantry to write a pattern for a word
      // already on the screen.
      if (offer) {
        offer.hidden = false;
        offer.querySelector("[data-picker-phrase]").textContent = button.dataset.phrase;
      }
      status.textContent = "No reference matches this phrase yet.";
      return;
    }
    if (offer) offer.hidden = true;

    window
      .fetch("/pantry/references/" + id + "/options/", {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" }
      })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        if (!(data.results || []).length) {
          status.textContent = "Nothing on this reference's list yet — search below.";
          return;
        }
        status.textContent = "On " + data.reference + "'s list:";
        data.results.forEach(function (item) {
          var row = document.createElement("li");
          var choice = document.createElement("button");
          choice.type = "button";
          choice.className = "picker__result";
          choice.textContent = item.name;
          var note = document.createElement("span");
          note.className = "picker__note";
          note.textContent = item.description;
          choice.appendChild(note);
          choice.addEventListener("click", function () {
            var body = new window.FormData();
            body.append("meal_food", item.id);
            send(active, body);
            foodDialog.close();
          });
          row.appendChild(choice);
          results.appendChild(row);
        });
      })
      .catch(function () {
        status.textContent = "Couldn't load that reference's list.";
      });
  }

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;

    var reference = event.target.closest(".reference");
    if (reference) {
      active = reference;
      foodPicker.open();
      preload(reference);
      return;
    }

    if (event.target.closest("[data-picker-create]") && active) {
      var make = new window.FormData();
      make.append("phrase", active.dataset.phrase);
      make.append("csrfmiddlewaretoken", token());
      window
        .fetch("/references/new/", {
          method: "POST", body: make, credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" }
        })
        .then(function (response) { return response.json(); })
        .then(function (data) {
          if (!data.ok) return;
          // Every button for the phrase, not just this one.
          document
            .querySelectorAll('.reference[data-phrase="' + active.dataset.phrase + '"]')
            .forEach(function (twin) {
              twin.dataset.reference = data.id;
              twin.dataset.known = "true";
            });
          preload(active);
        });
      return;
    }

    if (event.target.closest("[data-picker-clear]") && active) {
      var body = new window.FormData();
      body.append("clear", "1");
      send(active, body);
      foodDialog.close();
    }
  });
})(window, document);
