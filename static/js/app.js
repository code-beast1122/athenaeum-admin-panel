// Small helpers shared by every page: modals, confirmations, tabs, filters.
(function () {
  "use strict";

  function openModal(id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    modal.hidden = false;
    document.body.style.overflow = "hidden";
    var first = modal.querySelector("input:not([type=hidden]), textarea, select");
    if (first) first.focus();
  }

  function closeModal(modal) {
    modal.hidden = true;
    document.body.style.overflow = "";
  }

  document.addEventListener("click", function (event) {
    var opener = event.target.closest("[data-modal-open]");
    if (opener) {
      event.preventDefault();
      openModal(opener.getAttribute("data-modal-open"));
      return;
    }

    var closer = event.target.closest("[data-modal-close]");
    if (closer) {
      event.preventDefault();
      closeModal(closer.closest(".modal"));
      return;
    }

    var dismiss = event.target.closest("[data-dismiss]");
    if (dismiss) {
      dismiss.closest(".flash").remove();
      return;
    }

    var tab = event.target.closest("[data-tab]");
    if (tab) {
      var group = tab.closest(".tabs");
      var target = tab.getAttribute("data-tab");
      group.querySelectorAll("[data-tab]").forEach(function (t) {
        t.classList.toggle("active", t === tab);
      });
      document.querySelectorAll("[data-tab-panel]").forEach(function (panel) {
        if (panel.closest(".tab-scope") === group.closest(".tab-scope")) {
          panel.hidden = panel.getAttribute("data-tab-panel") !== target;
        }
      });
    }
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal:not([hidden])").forEach(closeModal);
    }
  });

  // Destructive actions ask before they fire.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    var message = form.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      event.preventDefault();
    }
  });

  // Filter dropdowns submit their form as soon as they change.
  document.querySelectorAll("[data-autosubmit]").forEach(function (element) {
    element.addEventListener("change", function () {
      element.closest("form").submit();
    });
  });

  // Prefill a shared edit modal from the row's `data-row` JSON payload.
  document.querySelectorAll("[data-edit-modal]").forEach(function (trigger) {
    trigger.addEventListener("click", function () {
      var modal = document.getElementById(trigger.getAttribute("data-edit-modal"));
      if (!modal) return;
      var form = modal.querySelector("form");
      if (!form) return;
      if (trigger.dataset.action) form.setAttribute("action", trigger.dataset.action);

      var row = {};
      try { row = JSON.parse(trigger.dataset.row || "{}"); } catch (e) { row = {}; }
      Object.keys(row).forEach(function (name) {
        var field = form.querySelector("[name='" + name + "']");
        if (!field) return;
        if (field.type === "checkbox") {
          field.checked = Boolean(row[name]);
        } else {
          field.value = row[name] === null || row[name] === undefined ? "" : row[name];
        }
      });
    });
  });

  var toggle = document.getElementById("sidebarToggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      document.getElementById("sidebar").classList.toggle("open");
    });
  }

  // Flash messages fade out on their own.
  setTimeout(function () {
    document.querySelectorAll(".flash-success, .flash-info").forEach(function (flash) {
      flash.style.transition = "opacity .4s";
      flash.style.opacity = "0";
      setTimeout(function () { flash.remove(); }, 400);
    });
  }, 6000);
})();
