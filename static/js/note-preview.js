/* Live preview on the note editor.
 *
 * Only the angle and the text — enough to answer "is that actually upside
 * down?" before saving. Without it the form still works; you just find out
 * on the recipe page.
 */
(function () {
  "use strict";

  var form = document.getElementById("note-form");
  var preview = document.getElementById("note-preview");
  if (!form || !preview) return;

  var body = form.querySelector('[data-note-field="body"]');
  var rotation = form.querySelector('[data-note-field="rotation"]');
  var target = preview.querySelector("[data-note-preview]");
  var placeholder = target ? target.textContent : "";

  function apply() {
    if (target) {
      target.textContent = (body && body.value.trim()) || placeholder;
    }
    var angle = rotation ? parseInt(rotation.value, 10) : 0;
    if (isNaN(angle)) angle = 0;
    preview.style.setProperty("--note-rotation", (angle % 360) + "deg");
  }

  form.addEventListener("input", apply);
  apply();
})();
