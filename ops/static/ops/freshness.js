/* Takt live-notice behaviour (eng re-review D5, design D18). Plain JS on top of htmx; no dependencies.

   Contract for page authors:
   - Every dispute form (and any form whose input must survive a Refresh) carries data-takt-form.
   - Refresh controls carry data-takt-refresh (the notice fragment adds it).

   1. Guard: a Refresh is cancelled when the region it would replace contains any open data-takt-form,
      with or without input. It is checked before the request is sent (htmx:beforeRequest: an HX-Refresh
      response would reload the page before htmx:beforeSwap ever runs) and again before the swap. The notice then says "Finish or cancel your dispute, then Refresh".
      A plain-link Refresh (full reload) is guarded the same way against any such form on the page.
   2. After a Refresh swap, focus moves to the swapped region's heading and #live-region announces
      "New data loaded" once. A Refresh link may override the text with data-takt-loaded="…".
*/
(function () {
  "use strict";
  var GUARD_MESSAGE = "Finish or cancel your dispute, then Refresh";

  // Doc (re-review D5): open dispute forms are never touched, so any open form[data-takt-form]
  // inside the swap target blocks Refresh, whether or not it has input yet.
  function busyForm(scope) {
    if (!scope || !scope.querySelector) return null;
    if (scope.matches && scope.matches("form[data-takt-form]")) return scope;
    return scope.querySelector("form[data-takt-form]");
  }

  function warn(trigger) {
    var notice = trigger.closest(".freshness-notice");
    if (!notice) return;
    var msg = notice.querySelector(".freshness-notice-guard");
    if (!msg) {
      msg = document.createElement("span");
      msg.className = "freshness-notice-guard";
      notice.appendChild(msg);
    }
    msg.textContent = " " + GUARD_MESSAGE;
  }

  function refreshTrigger(evt) {
    var cfg = evt.detail && evt.detail.requestConfig;
    var elt = cfg && cfg.elt;
    return elt && elt.hasAttribute && elt.hasAttribute("data-takt-refresh") ? elt : null;
  }

  document.addEventListener("htmx:beforeRequest", function (evt) {
    var elt = evt.detail && evt.detail.elt;
    if (!elt || !elt.hasAttribute || !elt.hasAttribute("data-takt-refresh")) return;
    var selector = elt.getAttribute("hx-target");
    var target = evt.detail.target || (selector && document.querySelector(selector));
    if (busyForm(target)) {
      evt.preventDefault();
      warn(elt);
    }
  });

  document.addEventListener("htmx:beforeSwap", function (evt) {
    var trigger = refreshTrigger(evt);
    if (!trigger) return;
    if (busyForm(evt.detail.target)) {
      evt.detail.shouldSwap = false;
      warn(trigger);
    }
  });

  document.addEventListener("click", function (evt) {
    var link = evt.target.closest && evt.target.closest("[data-takt-refresh]");
    if (!link || link.hasAttribute("hx-get")) return;
    if (busyForm(document)) {
      evt.preventDefault();
      warn(link);
    }
  });

  document.addEventListener("htmx:afterSwap", function (evt) {
    var trigger = refreshTrigger(evt);
    var cfg = evt.detail.requestConfig;
    if (!trigger || cfg.taktAnnounced) return;
    cfg.taktAnnounced = true;  // afterSwap fires per swapped element; act once per Refresh
    var selector = trigger.getAttribute("hx-target");
    var region = (selector && document.querySelector(selector)) || evt.target;
    var heading = region.querySelector("h1, h2, h3, h4") || region;
    if (!heading.hasAttribute("tabindex")) heading.setAttribute("tabindex", "-1");
    heading.focus();
    var live = document.getElementById("live-region");
    if (live) {
      live.textContent = "";
      var text = trigger.getAttribute("data-takt-loaded") || "New data loaded";
      window.setTimeout(function () { live.textContent = text; }, 50);
    }
  });
})();
