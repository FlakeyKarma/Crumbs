/* Adding and removing rows on the recipe editor.
 *
 * The form works without this: Django renders three blank ingredient rows and
 * three blank step rows, and saving with some left empty is fine. This just
 * removes the "save, come back, add three more" loop.
 */
(function () {
  "use strict";

  var LISTS = {
    ingredient: { list: "ingredient-rows", blank: "ingredient-blank" },
    step: { list: "step-rows", blank: "step-blank" }
  };

  function totalFormsInput(prefix) {
    return document.getElementById("id_" + prefix + "-TOTAL_FORMS");
  }

  function addRow(kind) {
    var config = LISTS[kind];
    var list = document.getElementById(config.list);
    var blank = document.getElementById(config.blank);
    if (!list || !blank) return;

    var prefix = list.dataset.prefix;
    var total = totalFormsInput(prefix);
    if (!total) return;

    var index = parseInt(total.value, 10);
    var markup = blank.innerHTML.replace(/__prefix__/g, String(index));

    var holder = document.createElement("ul");
    holder.innerHTML = markup;
    var row = holder.querySelector(".formset-row");
    if (!row) return;

    list.appendChild(row);
    total.value = String(index + 1);

    var firstInput = row.querySelector("input:not([type=hidden]), textarea, select");
    if (firstInput) firstInput.focus();
  }

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) return;
    var button = event.target.closest("[data-add-row]");
    if (!button) return;
    event.preventDefault();
    addRow(button.dataset.addRow);
  });

  // Checking "Remove" fades the row so it is obvious what saving will do.
  document.addEventListener("change", function (event) {
    var input = event.target;
    if (!(input instanceof Element) || !input.name || input.name.indexOf("-DELETE") === -1) return;
    var row = input.closest(".formset-row");
    if (row) row.classList.toggle("is-dropped", input.checked);
  });
})();
