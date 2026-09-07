/*
 * Grossansicht der Beckengalerie: Tastatur, Wischgeste und Fokus.
 *
 * Alles Fachliche steckt in den Vorlagen und in HTMX — Oeffnen und Blaettern
 * sind gewoehnliche Links mit `hx-get` auf `#mad-lightbox`. Ohne dieses Skript
 * bleibt die Grossansicht bedienbar; sie oeffnet dann als eigene Seite bzw.
 * schliesst per Link zurueck zur Galerie. Hier steht deshalb nur, was ein Link
 * nicht kann: Esc, Klick auf den Hintergrund, Pfeiltasten, Wischen und die
 * Fokusfuehrung.
 *
 * Bewusst kein Lightbox-Paket: gebraucht werden ein Bild, zwei Pfeile und eine
 * Beschriftung. Ein Fremdpaket brachte dafuer eine eigene Bildverwaltung mit,
 * die neben HTMX ein zweites Mal entscheiden wollte, wann was geladen wird.
 */
(function () {
  "use strict";

  var HOST_ID = "mad-lightbox";

  /* Ab dieser Strecke gilt eine Beruehrung als Wischgeste. Darunter ist es
   * eher ein ungenauer Tipp auf die Bildflaeche. */
  var SWIPE_DISTANCE = 48;

  var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  /* Das Element, von dem aus geoeffnet wurde. Beim Schliessen kehrt der Fokus
   * dorthin zurueck — sonst faengt die Tastaturnavigation wieder am
   * Seitenanfang an, und der Weg zum naechsten Bild ist ein Dutzend Tabs weit. */
  var opener = null;

  function host() {
    return document.getElementById(HOST_ID);
  }

  function dialog() {
    var container = host();
    return container ? container.querySelector("[data-lightbox-dialog]") : null;
  }

  function isOpen() {
    return dialog() !== null;
  }

  function close() {
    var container = host();
    if (container) {
      container.innerHTML = "";
    }
    document.body.classList.remove("mad-no-scroll");
    if (opener && opener.isConnected) {
      opener.focus();
    }
    opener = null;
  }

  /* Blaettern heisst: den vorhandenen Link ausloesen. HTMX haengt an ihm und
   * unterbindet dabei die Navigation — die Logik steht damit an genau einer
   * Stelle, naemlich in der Vorlage. */
  function step(direction) {
    var box = dialog();
    var link = box ? box.querySelector("[data-lightbox-" + direction + "]") : null;
    if (link) {
      link.click();
    }
  }

  /* Tab bleibt im Dialog. Ohne das wandert der Fokus hinter das Overlay, wo
   * nichts sichtbar ist und trotzdem alles bedienbar waere. */
  function keepFocusInside(event) {
    var box = dialog();
    if (!box) {
      return;
    }
    var items = Array.prototype.filter.call(
      box.querySelectorAll(FOCUSABLE),
      function (element) {
        return element.offsetParent !== null;
      }
    );
    if (items.length === 0) {
      event.preventDefault();
      box.focus();
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    var active = document.activeElement;
    if (event.shiftKey && (active === first || active === box)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  document.addEventListener("click", function (event) {
    if (!(event.target instanceof Element)) {
      return;
    }
    var trigger = event.target.closest("[data-lightbox-open]");
    if (trigger) {
      opener = trigger;
      return;
    }
    if (!isOpen()) {
      return;
    }
    if (event.target.closest("[data-lightbox-close]")) {
      event.preventDefault();
      close();
      return;
    }
    /* Hintergrund ist alles innerhalb des Overlays, aber ausserhalb des
     * Dialogs. */
    if (event.target.closest("[data-lightbox]") && !event.target.closest("[data-lightbox-dialog]")) {
      event.preventDefault();
      close();
    }
  });

  document.addEventListener("keydown", function (event) {
    if (!isOpen() || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      step("prev");
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      step("next");
    } else if (event.key === "Tab") {
      keepFocusInside(event);
    }
  });

  /* Wischen. Die Galerie wird ueberwiegend am Telefon angesehen, und dort ist
   * die Geste der naheliegende Weg zum naechsten Bild. Senkrechte Bewegungen
   * bleiben dem Scrollen vorbehalten, deshalb der Vergleich beider Strecken. */
  var touchOrigin = null;

  document.addEventListener("touchstart", function (event) {
    touchOrigin = isOpen() && event.touches.length === 1 ? event.touches[0] : null;
  }, { passive: true });

  document.addEventListener("touchend", function (event) {
    var origin = touchOrigin;
    touchOrigin = null;
    if (!origin || !isOpen()) {
      return;
    }
    var touch = event.changedTouches[0];
    var sideways = touch.clientX - origin.clientX;
    var upwards = touch.clientY - origin.clientY;
    if (Math.abs(sideways) < SWIPE_DISTANCE || Math.abs(sideways) <= Math.abs(upwards)) {
      return;
    }
    step(sideways < 0 ? "next" : "prev");
  }, { passive: true });

  document.body.addEventListener("htmx:afterSwap", function (event) {
    if (isOpen()) {
      document.body.classList.add("mad-no-scroll");
      /* Nach dem Oeffnen und nach jedem Blaettern: der Dialog selbst nimmt den
       * Fokus, nicht die erste Schaltflaeche — vorgelesen wird damit zuerst,
       * worum es geht. */
      if (event.target.id === HOST_ID) {
        dialog().focus();
      }
      return;
    }
    /* Der Reiterbereich wurde getauscht (Bearbeiten, Loeschen, Reiterwechsel)
     * und hat das Overlay mitgenommen. */
    document.body.classList.remove("mad-no-scroll");
    opener = null;
  });
})();
