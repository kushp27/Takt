/* Takt screen behaviour (design review D18 accessibility baseline). Plain JS on htmx 2; no dependencies.

   Drill-downs and the dispute flow are real <button>s:
   - A toggle (.drill-toggle) names its container with aria-controls and carries aria-expanded. Clicking an
     expanded toggle collapses it (no request). After its content loads, aria-expanded becomes "true", focus
     moves to the new content's heading, and #live-region says the toggle's data-takt-loaded text once
     ("Stations loaded", "Operators loaded", "Jobs loaded").
   - [data-takt-cancel] (Cancel / Close) empties its container, collapses the toggle and returns focus to it.
   - A server response can ask for an announcement or a nav badge update with HX-Trigger events:
     {"takt:announce": "Dispute submitted", "takt:navcount": 3}. An element marked data-takt-focus-here
     in the swapped content takes focus ("Dispute submitted" heading, the decided queue row).
   The live notice's Refresh (data-takt-refresh) is handled by ops/freshness.js, not here.
*/
(function () {
  "use strict";

  function announce(text) {
    var live = document.getElementById("live-region");
    if (!live || !text) return;
    live.textContent = "";
    window.setTimeout(function () { live.textContent = text; }, 50);
  }

  function toggleFor(container) {
    return container && container.id ? document.querySelector('[aria-controls="' + container.id + '"]') : null;
  }

  function focusHeading(region) {
    if (!region) return;
    var target = region.querySelector("[data-takt-focus-here]") || region.querySelector("h1, h2, h3, h4") || region;
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus();
  }

  function collapse(toggle, container) {
    if (container) container.replaceChildren();
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }

  function triggerOf(evt) {
    var cfg = evt.detail && evt.detail.requestConfig;
    return (cfg && cfg.elt) || evt.detail.elt;
  }

  // An expanded toggle collapses instead of fetching again.
  document.addEventListener("htmx:beforeRequest", function (evt) {
    var elt = evt.detail.elt;
    if (!elt || !elt.classList || !elt.classList.contains("drill-toggle")) return;
    if (elt.getAttribute("aria-expanded") === "true") {
      evt.preventDefault();
      collapse(elt, document.getElementById(elt.getAttribute("aria-controls")));
      // Toggles sharing a container (e.g. "This looks wrong" and "N jobs excluded") collapse together.
      document.querySelectorAll('[aria-controls="' + elt.getAttribute("aria-controls") + '"]').forEach(function (t) {
        t.setAttribute("aria-expanded", "false");
      });
    }
  });

  // After a drill-down loads: expanded state, focus to the new heading, one polite announcement.
  document.addEventListener("htmx:afterSwap", function (evt) {
    var trigger = triggerOf(evt);
    if (!trigger || !trigger.classList || !trigger.classList.contains("drill-toggle")) return;
    if (trigger.hasAttribute("data-takt-refresh")) return;
    var cfg = evt.detail.requestConfig || {};
    if (cfg.taktDone) return;  // afterSwap can fire per swapped element; act once per request
    cfg.taktDone = true;
    var container = evt.detail.target;
    document.querySelectorAll('[aria-controls="' + trigger.getAttribute("aria-controls") + '"]').forEach(function (t) {
      t.setAttribute("aria-expanded", t === trigger ? "true" : "false");
    });
    if (!container.querySelector(".inline-error")) {
      focusHeading(container);
      announce(trigger.getAttribute("data-takt-loaded"));
    }
  });

  // Content that names its own focus target (a decided queue row, "Dispute submitted").
  document.addEventListener("htmx:afterSettle", function () {
    var target = document.querySelector("[data-takt-focus-here]");
    if (!target) return;
    if (!target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    target.focus();
    target.removeAttribute("data-takt-focus-here");
  });

  // Server-sent events (HX-Trigger-After-Settle).
  document.body.addEventListener("takt:announce", function (evt) {
    announce(evt.detail && (evt.detail.value || evt.detail));
  });
  document.body.addEventListener("takt:navcount", function (evt) {
    var badge = document.getElementById("nav-dispute-count");
    var n = evt.detail && (evt.detail.value !== undefined ? evt.detail.value : evt.detail);
    if (badge && n !== undefined && n !== null) {
      badge.replaceChildren(document.createTextNode(String(n)));
      var hidden = document.createElement("span");
      hidden.className = "visually-hidden";
      hidden.textContent = " open";
      badge.appendChild(hidden);
    }
  });

  // Cancel / Close: empty the container, collapse the toggle, return focus to it.
  document.addEventListener("click", function (evt) {
    var btn = evt.target.closest && evt.target.closest("[data-takt-cancel]");
    if (!btn) return;
    evt.preventDefault();
    var container = btn.closest(".drill");
    var toggle = toggleFor(container);
    collapse(toggle, container);
    if (toggle) toggle.focus();
  });
})();
