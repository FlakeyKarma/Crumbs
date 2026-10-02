/* Timers that outlive the page they were started on.
 *
 * The old version kept each countdown in a closure on the recipe page, so
 * navigating away destroyed it — which is exactly backwards for a kitchen,
 * where the whole point of starting a 45 minute timer is to go and do
 * something else.
 *
 * So the state lives in localStorage and the UI is a dock pinned to every
 * page. Two consequences worth knowing:
 *
 *   - it is stored as a *deadline*, not a remaining count, so a timer is
 *     still right after the phone slept, the tab was discarded, or the
 *     browser was closed and reopened;
 *   - localStorage is shared between tabs, so a timer started on the laptop's
 *     recipe tab also ticks on its shopping-list tab. The `storage` event
 *     keeps them in step.
 *
 * It does not survive a different device, and it cannot ring while the
 * browser is shut. That would need a service worker and notification
 * permission, which is a much bigger ask than a countdown deserves.
 */
(function (window, document) {
  "use strict";

  var KEY = "crumbs:timers";
  var subscribers = [];

  // --- The store ----------------------------------------------------------

  function read() {
    try {
      var raw = JSON.parse(window.localStorage.getItem(KEY) || "[]");
      return Array.isArray(raw) ? raw.filter(function (t) { return t && t.id; }) : [];
    } catch (error) {
      return [];
    }
  }

  function write(timers) {
    try {
      window.localStorage.setItem(KEY, JSON.stringify(timers));
    } catch (error) {
      /* Private browsing, or full. The timer still runs for this page. */
    }
    publish();
  }

  function publish() {
    var timers = read();
    for (var i = 0; i < subscribers.length; i++) subscribers[i](timers);
  }

  var Timers = {
    all: read,

    find: function (id) {
      return read().filter(function (t) { return t.id === id; })[0] || null;
    },

    start: function (spec) {
      var timers = read().filter(function (t) { return t.id !== spec.id; });
      timers.push({
        id: spec.id,
        minutes: spec.minutes,
        deadline: Date.now() + spec.minutes * 60 * 1000,
        recipeTitle: spec.recipeTitle || "",
        recipeUrl: spec.recipeUrl || "",
        step: spec.step || null,
        announced: false
      });
      write(timers);
    },

    cancel: function (id) {
      write(read().filter(function (t) { return t.id !== id; }));
    },

    remaining: function (timer) {
      return Math.max(0, Math.round((timer.deadline - Date.now()) / 1000));
    },

    subscribe: function (callback) {
      subscribers.push(callback);
      callback(read());
    }
  };

  window.CrumbsTimers = Timers;

  function clockFace(seconds) {
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    var rest = seconds % 60;
    var pad = function (n) { return (n < 10 ? "0" : "") + n; };
    if (hours) return hours + ":" + pad(minutes) + ":" + pad(rest);
    return minutes + ":" + pad(rest);
  }

  Timers.clockFace = clockFace;

  // --- The dock -----------------------------------------------------------

  var dock, pill, panel, announcer;

  function build() {
    dock = document.createElement("div");
    dock.className = "timer-dock";
    dock.hidden = true;

    pill = document.createElement("button");
    pill.type = "button";
    pill.className = "timer-dock__pill";
    pill.setAttribute("aria-expanded", "false");

    panel = document.createElement("div");
    panel.className = "timer-dock__panel";
    panel.hidden = true;

    // Announced on completion only. A live region updating every second
    // would read the clock aloud continuously.
    announcer = document.createElement("p");
    announcer.className = "skip-link";
    announcer.setAttribute("aria-live", "polite");

    pill.addEventListener("click", function () {
      var open = panel.hidden;
      panel.hidden = !open;
      pill.setAttribute("aria-expanded", String(open));
    });

    dock.appendChild(pill);
    dock.appendChild(panel);
    dock.appendChild(announcer);
    document.body.appendChild(dock);
  }

  function rowFor(timer) {
    var left = Timers.remaining(timer);
    var done = left <= 0;

    var row = document.createElement("div");
    row.className = "timer-row";
    row.dataset.done = String(done);

    var title = document.createElement("p");
    title.className = "timer-row__title";
    // textContent, not innerHTML: a recipe title is whatever someone typed.
    title.textContent = timer.recipeTitle || "Timer";
    row.appendChild(title);

    var detail = document.createElement("p");
    detail.className = "timer-row__detail";
    detail.textContent = (timer.step ? "Step " + timer.step + " — " : "") +
      (done ? "time's up" : clockFace(left) + " left");
    row.appendChild(detail);

    var actions = document.createElement("div");
    actions.className = "timer-row__actions";

    var cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "btn btn--quiet";
    cancel.textContent = done ? "Dismiss" : "Cancel";
    cancel.addEventListener("click", function () {
      Timers.cancel(timer.id);
    });
    actions.appendChild(cancel);

    if (timer.recipeUrl) {
      var back = document.createElement("a");
      back.className = "btn";
      // cook=1 puts the page back into cook mode, and the fragment lands on
      // the step — the two halves of "where I left off".
      back.href = timer.recipeUrl + "?cook=1" + (timer.step ? "#step-" + timer.step : "");
      back.textContent = "Back to the recipe";
      actions.appendChild(back);
    }

    row.appendChild(actions);
    return row;
  }

  function render(timers) {
    if (!dock) return;

    if (!timers.length) {
      dock.hidden = true;
      panel.hidden = true;
      pill.setAttribute("aria-expanded", "false");
      return;
    }

    dock.hidden = false;

    var soonest = null;
    var finished = 0;
    for (var i = 0; i < timers.length; i++) {
      var left = Timers.remaining(timers[i]);
      if (left <= 0) {
        finished += 1;
      } else if (!soonest || left < Timers.remaining(soonest)) {
        soonest = timers[i];
      }
    }

    if (finished) {
      pill.textContent = finished === timers.length
        ? "Time's up"
        : "Time's up, and " + (timers.length - finished) + " running";
      pill.dataset.done = "true";
    } else {
      pill.textContent = clockFace(Timers.remaining(soonest)) +
        (timers.length > 1 ? " and " + (timers.length - 1) + " more" : "");
      pill.dataset.done = "false";
    }

    panel.textContent = "";
    timers
      .slice()
      .sort(function (a, b) { return a.deadline - b.deadline; })
      .forEach(function (timer) { panel.appendChild(rowFor(timer)); });
  }

  function announceFinished() {
    var timers = read();
    var changed = false;
    for (var i = 0; i < timers.length; i++) {
      if (!timers[i].announced && Timers.remaining(timers[i]) <= 0) {
        timers[i].announced = true;
        changed = true;
        announcer.textContent =
          "Timer finished: " + (timers[i].recipeTitle || "timer") +
          (timers[i].step ? ", step " + timers[i].step : "");
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
      }
    }
    // Written back so a second tab doesn't buzz for the same timer.
    if (changed) write(timers);
  }

  function tick() {
    var timers = read();
    if (timers.length) {
      announceFinished();
      render(read());
    }
  }

  function begin() {
    build();
    Timers.subscribe(render);
    window.setInterval(tick, 1000);
    // Another tab started or cancelled one.
    window.addEventListener("storage", function (event) {
      if (event.key === KEY) publish();
    });
    // Coming back to a backgrounded tab, where the interval may have been
    // throttled to a crawl.
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") tick();
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", begin);
  } else {
    begin();
  }
})(window, document);
