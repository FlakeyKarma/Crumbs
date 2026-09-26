/* Live preview for the theme editor.
 *
 * The colour maths here mirrors recipes/theming.py so that moving a colour
 * picker changes the page immediately instead of after a save. The server is
 * authoritative: whatever this shows, the stored theme is recomputed in
 * Python when the form is submitted. If the two ever disagree, theming.py is
 * right and this file is the bug.
 *
 * Without this script the form still works — you just don't see the change
 * until the page reloads.
 */
(function () {
  "use strict";

  var form = document.getElementById("theme-form");
  var preview = document.getElementById("theme-preview");
  if (!form || !preview) return;

  var DARK_INK = "#15202a";
  var WHITE = [255, 255, 255];
  var BLACK = [0, 0, 0];

  function parseHex(value) {
    if (typeof value !== "string") return null;
    var digits = value.trim().replace(/^#/, "");
    if (digits.length === 3) {
      digits = digits.split("").map(function (c) { return c + c; }).join("");
    }
    if (!/^[0-9a-fA-F]{6}$/.test(digits)) return null;
    return [0, 2, 4].map(function (i) { return parseInt(digits.substr(i, 2), 16); });
  }

  function toHex(rgb) {
    return "#" + rgb.map(function (c) {
      return ("0" + Math.round(c).toString(16)).slice(-2);
    }).join("");
  }

  function mix(rgb, target, amount) {
    return rgb.map(function (c, i) { return c + (target[i] - c) * amount; });
  }

  function luminance(rgb) {
    var channels = rgb.map(function (raw) {
      var c = raw / 255;
      return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
  }

  function contrast(a, b) {
    var la = luminance(a);
    var lb = luminance(b);
    return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
  }

  function readableOn(rgb) {
    return contrast(rgb, parseHex(DARK_INK)) >= contrast(rgb, WHITE) ? DARK_INK : "#ffffff";
  }

  function field(name) {
    return form.querySelector('[data-theme-field="' + name + '"]');
  }

  function override(name) {
    var input = form.querySelector('[name="' + name + '"]');
    return input && parseHex(input.value) ? input.value.trim() : null;
  }

  var note = preview.querySelector("[data-contrast-note]");

  function apply() {
    var primaryInput = field("primary");
    var secondaryInput = field("secondary");
    var roundnessInput = field("roundness");
    if (!primaryInput) return;

    var primary = parseHex(primaryInput.value);
    if (!primary) return;
    var secondary = (secondaryInput && parseHex(secondaryInput.value)) || primary;

    var root = document.documentElement.style;
    root.setProperty("--jam", toHex(primary));
    root.setProperty("--jam-deep", override("primary_hover") || toHex(mix(primary, BLACK, 0.18)));
    root.setProperty("--amber", toHex(secondary));
    root.setProperty("--on-primary", override("on_primary") || readableOn(primary));

    if (roundnessInput && /^(0|\d{1,2}(\.\d+)?(px|rem|em))$/.test(roundnessInput.value.trim())) {
      root.setProperty("--radius", roundnessInput.value.trim());
    }

    if (note) {
      // The page background in light mode. Worth saying out loud, because a
      // link that fails here is unreadable and the swatch looks fine.
      var ratio = contrast(primary, parseHex("#f1f3ef"));
      note.textContent =
        "Link contrast on a light page: " + ratio.toFixed(2) + ":1" +
        (ratio >= 4.5 ? " — comfortable." : " — below 4.5:1, try a darker colour.");
      note.dataset.ok = ratio >= 4.5 ? "true" : "false";
    }
  }

  form.addEventListener("input", apply);
  form.addEventListener("change", apply);
  apply();
})();
