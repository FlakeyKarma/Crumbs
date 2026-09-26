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
  // Driven by a deadline rather than by counting ticks, so a 45 minute roast
  // is still accurate after the phone has spent half of it asleep.

  var running = new WeakMap();

  function clockFace(totalSeconds) {
    var minutes = Math.floor(totalSeconds / 60);
    var seconds = totalSeconds % 60;
    return minutes + ":" + (seconds < 10 ? "0" : "") + seconds;
  }

  function attachTimer(button) {
    var minutes = parseInt(button.dataset.timerMinutes, 10);
    var idleLabel = button.textContent.trim();

    function stop() {
      var state = running.get(button);
      if (state) window.clearInterval(state.interval);
      running.delete(button);
      button.dataset.running = "false";
    }

    function tick(deadline) {
      var remaining = Math.round((deadline - Date.now()) / 1000);
      if (remaining <= 0) {
        stop();
        button.textContent = "Time's up — tap to restart";
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
        return;
      }
      button.textContent = clockFace(remaining) + " left";
    }

    button.addEventListener("click", function () {
      if (running.has(button)) {
        stop();
        button.textContent = idleLabel;
        return;
      }
      var deadline = Date.now() + minutes * 60 * 1000;
      button.dataset.running = "true";
      tick(deadline);
      running.set(button, {
        interval: window.setInterval(function () {
          tick(deadline);
        }, 1000)
      });
    });
  }

  var timerButtons = document.querySelectorAll("[data-timer-minutes]");
  for (var i = 0; i < timerButtons.length; i++) {
    attachTimer(timerButtons[i]);
  }
})();
