/* Cooking aids for the recipe page.
 *
 * Nothing here is required to read a recipe: the servings dial is plain links
 * handled on the server, and printing works without JavaScript. This file only
 * adds the things that need a live page — crossing off steps, keeping the
 * screen awake, and counting down.
 */
(function () {
  "use strict";

  var body = document.body;
  var toggle = document.getElementById("cook-mode-toggle");
  var steps = document.getElementById("steps");
  var wakeLock = null;

  function requestWakeLock() {
    if (!("wakeLock" in navigator)) return;
    navigator.wakeLock
      .request("screen")
      .then(function (lock) {
        wakeLock = lock;
        lock.addEventListener("release", function () {
          wakeLock = null;
        });
      })
      .catch(function () {
        /* Denied or unsupported — cook mode still works, the screen just dims. */
      });
  }

  function releaseWakeLock() {
    if (wakeLock) {
      wakeLock.release();
      wakeLock = null;
    }
  }

  if (toggle) {
    toggle.addEventListener("click", function () {
      var on = !body.classList.contains("cook-mode");
      body.classList.toggle("cook-mode", on);
      toggle.setAttribute("aria-pressed", String(on));
      toggle.textContent = on ? "Leave cook mode" : "Cook mode";
      if (on) {
        requestWakeLock();
      } else {
        releaseWakeLock();
        var done = document.querySelectorAll(".step.is-done");
        for (var i = 0; i < done.length; i++) done[i].classList.remove("is-done");
      }
    });
  }

  // Re-acquire the lock when the tab comes back to the front.
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible" && body.classList.contains("cook-mode")) {
      requestWakeLock();
    }
  });

  if (steps) {
    steps.addEventListener("click", function (event) {
      if (!body.classList.contains("cook-mode")) return;
      if (event.target.closest("button")) return; // the timer has its own job
      var step = event.target.closest(".step");
      if (step) step.classList.toggle("is-done");
    });
  }

  // --- Timers -------------------------------------------------------------
  //
  // The countdown itself belongs to timers.js, which keeps it in
  // localStorage so it survives leaving this page. All this file does is
  // start and cancel one, and keep the step's own button showing the time.

  var steps = document.getElementById("steps");
  var recipeTitle = steps ? steps.dataset.recipeTitle || "" : "";
  var recipeUrl = steps ? steps.dataset.recipeUrl || "" : "";
  var timerButtons = document.querySelectorAll("[data-timer-minutes]");

  if (window.CrumbsTimers && timerButtons.length) {
    var Timers = window.CrumbsTimers;

    Array.prototype.forEach.call(timerButtons, function (button) {
      var idle = button.textContent.trim();
      button.dataset.idleLabel = idle;

      button.addEventListener("click", function () {
        var id = button.dataset.timerId;
        if (Timers.find(id)) {
          Timers.cancel(id);
          return;
        }
        Timers.start({
          id: id,
          minutes: parseInt(button.dataset.timerMinutes, 10),
          recipeTitle: recipeTitle,
          recipeUrl: recipeUrl,
          step: button.dataset.timerStep
        });
      });
    });

    function repaint() {
      Array.prototype.forEach.call(timerButtons, function (button) {
        var timer = Timers.find(button.dataset.timerId);
        if (!timer) {
          button.dataset.running = "false";
          button.textContent = button.dataset.idleLabel;
          return;
        }
        var left = Timers.remaining(timer);
        button.dataset.running = left > 0 ? "true" : "false";
        button.textContent = left > 0
          ? Timers.clockFace(left) + " left"
          : "Time's up — tap to restart";
      });
    }

    // Subscribing covers changes — started here, cancelled from the dock, or
    // cancelled in another tab. The interval covers the clock simply running
    // down, which changes nothing in the store and so publishes nothing.
    Timers.subscribe(repaint);
    window.setInterval(repaint, 1000);
  }

  // --- Coming back from the dock -------------------------------------------
  //
  // "Back to the recipe" links to ?cook=1#step-N, so arriving that way should
  // land in cook mode at that step rather than at the top of an ordinary page.

  if (toggle && /[?&]cook=1(&|$)/.test(window.location.search)) {
    toggle.click();
    var target = window.location.hash && document.querySelector(window.location.hash);
    if (target && target.scrollIntoView) {
      target.scrollIntoView({ block: "center" });
    }
  }
})();
