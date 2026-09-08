"""Beckenübersicht, Beckendetail mit Registerkarten — und die Schreibpfade.

Zum Aufbau der schreibenden Ansichten: sie antworten nicht mit einer eigenen
Seite, sondern immer mit dem Reiterbereich des Beckens. Per HTMX ist das das
Fragment, ohne HTMX dieselbe Seite als Ganzes. Damit bleibt der Reiterkontext
erhalten, und jede Ansicht ist auch ohne JavaScript bedienbar.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import prefetch_related_objects
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from core.views import NavSectionMixin

from . import derived, selectors, transfers
from .charts import derived_charts, key_parameter_charts
from .forms import (
    CareTaskForm,
    EventForm,
    HardscapeItemForm,
    HardscapeRemovalForm,
    MeasurementForm,
    MeasurementSeriesForm,
    PhotoForm,
    PhotoUploadForm,
    PlantingForm,
    StockingForm,
    StockingRemovalForm,
    SubstrateLayerForm,
    TankDerivedTargetForm,
    TankDissolveForm,
    TankForm,
    TankParameterTargetForm,
    TransferForm,
)
from .models import (
    CareTask,
    Event,
    HardscapeItem,
    Measurement,
    Planting,
    Stocking,
    SubstrateLayer,
    Tank,
    TankDerivedTarget,
    TankParameterTarget,
    TankPhoto,
    format_cm,
)

#: Registerkarten des Beckendetails in Anzeigereihenfolge.
TABS = [
    ("uebersicht", "Übersicht"),
    ("messwerte", "Messwerte"),
    ("ereignisse", "Ereignisse"),
    ("besatz", "Besatz"),
    ("pflanzen", "Pflanzen"),
    ("einrichtung", "Einrichtung"),
    ("termine", "Termine"),
    ("geraete", "Geräte"),
    ("galerie", "Galerie"),
]
TAB_KEYS = [key for key, _ in TABS]
DEFAULT_TAB = TAB_KEYS[0]

#: Sessionschlüssel für „zuletzt genutztes Becken" auf dem Dashboard.
LAST_TANK_SESSION_KEY = "last_tank_id"

MEASUREMENT_PAGE_SIZE = 50


class TankListView(LoginRequiredMixin, NavSectionMixin, TemplateView):
    """Kartenraster aller Becken, aufgelöste getrennt darunter."""

    template_name = "tanks/tank_list.html"
    nav_section = "tanks"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Die Zusammenfassung der Einrichtung zählt Schichten und Hardscape je
        # Karte; ohne Vorladung wären das zwei Abfragen pro Becken.
        tanks = (
            Tank.objects.for_user(self.request.user)
            .with_overview()
            .prefetch_related("substrate_layers", "hardscape")
        )
        context["active_tanks"] = list(tanks.active())
        context["dissolved_tanks"] = list(tanks.dissolved().order_by("-dissolved_on"))
        return context


def _get_tank(request, slug):
    return get_object_or_404(Tank.objects.for_user(request.user), slug=slug)


def derived_target_rows(tank):
    """Die abgeleiteten Größen mit ihrem geltenden Zielbereich.

    Anders als bei den gemessenen Größen steht hier **immer** eine Zeile: die
    Vorgabe liegt im Code und nicht in einer Parameter-Tabelle, in der man sie
    nachschlagen könnte. ``target`` ist die beckeneigene Überschreibung, sofern
    es eine gibt — nur dann lässt sich zurücksetzen.
    """
    overrides = {row.key: row for row in tank.derived_targets.all()}
    rows = []
    for key, parameter in derived.DERIVED_PARAMETERS.items():
        target = overrides.get(key)
        minimum, maximum = derived.target_range(parameter, target)
        rows.append(
            {
                "parameter": parameter,
                "target": target,
                "range_label": parameter.format_range(minimum, maximum),
            }
        )
    return rows


def tab_context(tank, tab):
    """Daten der gewählten Registerkarte — nur die, die sie wirklich braucht."""
    if tab == "uebersicht":
        return {
            "measurements": selectors.tank_measurement_overview(tank),
            "open_tasks": selectors.open_tasks_for_tank(tank),
            "photo": tank.photos.first(),
            "targets": tank.parameter_targets.select_related("parameter").order_by(
                "parameter__sort_order", "parameter__name"
            ),
            "derived_targets": derived_target_rows(tank),
            "target_form": TankParameterTargetForm(tank=tank),
        }
    if tab == "messwerte":
        return {
            # Gemessene und gerechnete Zeilen in einer Liste — getrennt
            # untereinander stünde CO₂ ohne den pH daneben, aus dem es kommt.
            "measurements": selectors.tank_measurement_rows(tank, limit=MEASUREMENT_PAGE_SIZE),
            "charts": [*key_parameter_charts(tank), *derived_charts(tank)],
            "page_size": MEASUREMENT_PAGE_SIZE,
        }
    if tab == "ereignisse":
        # ``prefetch_related`` statt einer Abfrage je Zeile: die Vorschaubilder
        # stehen an jedem Eintrag.
        return {"events": tank.events.prefetch_related("photos")[:100]}
    if tab == "besatz":
        # ``annotate_origins`` hängt die Herkunft an — mit einer Abfrage für
        # die ganze Liste, nicht mit einer je Zeile.
        return {
            "stockings": transfers.annotate_origins(
                tank.stockings.select_related("species").prefetch_related("species__images"), tank
            )
        }
    if tab == "pflanzen":
        return {
            "plantings": transfers.annotate_origins(
                tank.plantings.select_related("species").prefetch_related("species__images"), tank
            )
        }
    if tab == "einrichtung":
        # Einmal vorladen: Schichtstapel, Gesamthöhe und Zusammenfassung
        # arbeiten danach auf denselben Objekten statt auf drei Abfragen.
        prefetch_related_objects([tank], "substrate_layers", "hardscape")
        return {
            # Von oben nach unten — so schaut man in ein Becken hinein.
            "layers": list(tank.substrate_layers.all())[::-1],
            "substrate_depth": format_cm(tank.substrate_depth_cm),
            "hardscape": tank.hardscape.all(),
        }
    if tab == "termine":
        return {"tasks": tank.tasks.order_by("-is_active", "due_on")}
    if tab == "geraete":
        # ``devices`` sind die Geräte aus services.Device — dieselben, die der
        # Bereich /geraete/ zeigt. Ein zweites Gerätemodell gibt es nicht.
        return {"devices": tank.devices.order_by("kind", "name")}
    if tab == "galerie":
        return {"photos": tank.photos.select_related("event")}
    return {}


def detail_context(request, tank, tab):
    context = {
        "tank": tank,
        "tabs": TABS,
        "active_tab": tab,
        "tab_template": f"tanks/tabs/{tab}.html",
        "nav_section": "tanks",
    }
    context.update(tab_context(tank, tab))
    return context


class TankDetailView(LoginRequiredMixin, TemplateView):
    """Beckendetail. Die Registerkarte kommt aus ``?reiter=`` — so ist jeder
    Reiter auch ohne JavaScript direkt verlinkbar."""

    template_name = "tanks/tank_detail.html"

    def get(self, request, slug, **kwargs):
        tank = _get_tank(request, slug)
        request.session[LAST_TANK_SESSION_KEY] = tank.pk
        tab = request.GET.get("reiter", DEFAULT_TAB)
        if tab not in TAB_KEYS:
            tab = DEFAULT_TAB
        return render(request, self.template_name, detail_context(request, tank, tab))


class TankTabView(LoginRequiredMixin, TemplateView):
    """HTMX-Fragment einer Registerkarte: Reiterleiste plus Inhalt."""

    template_name = "tanks/partials/tab_area.html"

    def get(self, request, slug, tab, **kwargs):
        if tab not in TAB_KEYS:
            raise Http404("Unbekannte Registerkarte")
        tank = _get_tank(request, slug)
        request.session[LAST_TANK_SESSION_KEY] = tank.pk
        return render(request, self.template_name, detail_context(request, tank, tab))


# --------------------------------------------------------------------------
# Schreibpfade am Becken
# --------------------------------------------------------------------------


class TankScopedMixin(LoginRequiredMixin):
    """Bindet jede Ansicht an ein Becken des angemeldeten Benutzers.

    Die Eigentümerprüfung steht genau hier und nirgends sonst: ``self.tank``
    kommt immer aus ``Tank.objects.for_user()``, und ``get_queryset()`` filtert
    jedes abhängige Objekt über dieses Becken. Ein fremder Slug oder eine
    fremde ``pk`` endet damit als 404 — nicht als 403, denn über fremde Daten
    gibt es hier keine Auskunft, auch keine über ihre Existenz.

    Django-Berechtigungen kommen bewusst nicht zum Einsatz: wer sein eigenes
    Becken pflegt, braucht kein globales ``change_tank``.
    """

    #: Modell der abhängigen Daten; ``None`` bei Ansichten auf dem Becken selbst.
    model = None

    def dispatch(self, request, *args, **kwargs):
        # Erst anmelden, dann suchen: für einen anonymen Benutzer gibt es
        # keine Beckenmenge, in der sich nachschlagen ließe.
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.tank = get_object_or_404(Tank.objects.for_user(request.user), slug=kwargs["slug"])
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.model.objects.filter(tank=self.tank)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])


class TankFragmentView(TankScopedMixin, View):
    """Basis aller schreibenden Ansichten am Becken.

    Antwort ist stets der Reiterbereich: mit HTMX das Fragment, ohne HTMX die
    vollständige Beckenseite. Meldungen werden nur im zweiten Fall gesetzt —
    im Fragment gibt es keinen Meldungsbereich, dort spricht der aktualisierte
    Reiter für sich.
    """

    #: Registerkarte, die nach der Aktion frisch geliefert wird.
    tab = DEFAULT_TAB

    def render_tab(self, extra=None):
        context = detail_context(self.request, self.tank, self.tab)
        context.update(extra or {})
        template = (
            "tanks/partials/tab_area.html"
            if getattr(self.request, "htmx", False)
            else "tanks/tank_detail.html"
        )
        return render(self.request, template, context)

    def done(self, message=""):
        if message and not getattr(self.request, "htmx", False):
            messages.success(self.request, message)
        return self.render_tab()

    def tab_url(self):
        return f"{self.tank.get_absolute_url()}?reiter={self.tab}"

    def modal(self, *, title, action, body_template, **extra):
        """Kontext für das Overlay über dem Reiter."""
        return {
            "modal_template": body_template,
            "modal_title": title,
            "modal_action": action,
            "cancel_url": reverse("tanks:tab", args=[self.tank.slug, self.tab]),
            "cancel_href": self.tab_url(),
            **extra,
        }


class TankObjectFormView(TankFragmentView):
    """Anlegen und Bearbeiten eines Datensatzes am Becken.

    Beides ist derselbe Ablauf und unterscheidet sich nur darin, ob es schon
    ein Objekt gibt; das hält die Zahl der Ansichten halbiert und sorgt dafür,
    dass Anlegen und Bearbeiten nie auseinanderlaufen.
    """

    form_class = None
    create_title = ""
    update_title = ""
    submit_label = "Speichern"
    multipart = False
    hint = ""
    #: URL-Namen für die Formularadresse.
    create_url_name = ""
    update_url_name = ""

    @property
    def is_update(self):
        return "pk" in self.kwargs

    def get_instance(self):
        return self.get_object() if self.is_update else None

    def get_form(self, data=None, files=None):
        instance = self.get_instance()
        return self.form_class(data, files, instance=instance, **self.form_kwargs())

    def form_kwargs(self):
        return {}

    def get_action(self):
        if self.is_update:
            return reverse(self.update_url_name, args=[self.tank.slug, self.kwargs["pk"]])
        return reverse(self.create_url_name, args=[self.tank.slug])

    def save(self, form):
        obj = form.save(commit=False)
        obj.tank = self.tank
        obj.save()
        form.save_m2m()
        return obj

    def success_message(self, obj):
        return "Gespeichert." if self.is_update else "Angelegt."

    def form_context(self, form):
        return self.modal(
            title=self.update_title if self.is_update else self.create_title,
            action=self.get_action(),
            body_template="tanks/partials/form_modal.html",
            form=form,
            modal_submit=self.submit_label,
            modal_multipart=self.multipart,
            modal_hint=self.hint,
        )

    def get(self, request, **kwargs):
        return self.render_tab(self.form_context(self.get_form()))

    def post(self, request, **kwargs):
        form = self.get_form(request.POST, request.FILES or None)
        if not form.is_valid():
            return self.render_tab(self.form_context(form))
        return self.done(self.success_message(self.save(form)))


class TankObjectConfirmView(TankFragmentView):
    """Ein Schritt vor der folgenschweren Aktion.

    Es gibt keinen Link, der beim Klick löscht: ``GET`` zeigt, was passieren
    würde, erst ``POST`` führt es aus.
    """

    title = ""
    question = ""
    submit_label = "Löschen"
    url_name = ""

    def get_action(self):
        return reverse(self.url_name, args=[self.tank.slug, self.kwargs["pk"]])

    def describe(self, obj):
        return str(obj)

    def perform(self, obj):
        obj.delete()
        return "Gelöscht."

    def get(self, request, **kwargs):
        obj = self.get_object()
        return self.render_tab(
            self.modal(
                title=self.title,
                action=self.get_action(),
                body_template="tanks/partials/confirm_modal.html",
                modal_question=self.question,
                modal_subject=self.describe(obj),
                modal_submit=self.submit_label,
            )
        )

    def post(self, request, **kwargs):
        return self.done(self.perform(self.get_object()))


# --- Becken ---------------------------------------------------------------


class TankCreateView(LoginRequiredMixin, NavSectionMixin, View):
    """Neues Becken.

    Bewusst eine eigene Seite und kein Overlay: es gibt noch kein Becken und
    damit auch keinen Reiterkontext, in den das Formular gehören könnte.
    """

    nav_section = "tanks"
    template_name = "tanks/tank_form.html"

    def get(self, request):
        return self.render_form(TankForm(owner=request.user))

    def post(self, request):
        form = TankForm(request.POST, request.FILES or None, owner=request.user)
        if not form.is_valid():
            return self.render_form(form)
        tank = form.save()
        messages.success(request, f"„{tank.name}“ angelegt.")
        return HttpResponseRedirect(tank.get_absolute_url())

    def render_form(self, form):
        return render(
            self.request,
            self.template_name,
            {"form": form, "nav_section": self.nav_section, "title": "Becken anlegen"},
        )


class TankUpdateView(TankScopedMixin, View):
    """Stammdaten eines Beckens — ebenfalls als Seite.

    Der Beckenkopf mit Name, Volumen und Wassertyp steht außerhalb des
    Reiterbereichs; ein Overlay im Reiter könnte ihn nach dem Speichern nicht
    mitaktualisieren.
    """

    template_name = "tanks/tank_form.html"

    def get(self, request, **kwargs):
        return self.render_form(TankForm(instance=self.tank))

    def post(self, request, **kwargs):
        form = TankForm(request.POST, request.FILES or None, instance=self.tank)
        if not form.is_valid():
            return self.render_form(form)
        tank = form.save()
        messages.success(request, f"„{tank.name}“ gespeichert.")
        return HttpResponseRedirect(tank.get_absolute_url())

    def render_form(self, form):
        return render(
            self.request,
            self.template_name,
            {
                "form": form,
                "tank": self.tank,
                "nav_section": "tanks",
                "title": "Becken bearbeiten",
            },
        )


class TankDissolveView(TankScopedMixin, View):
    """Becken auflösen statt löschen.

    Ein Becken mit Messreihen verschwindet nicht: es bekommt ein Enddatum und
    steht danach in der Übersicht bei den aufgelösten. Die Historie bleibt
    lesbar — genau dafür wurde sie geführt.
    """

    template_name = "tanks/tank_dissolve.html"

    def get(self, request, **kwargs):
        return self.render_form(TankDissolveForm(instance=self.tank))

    def post(self, request, **kwargs):
        if "reaktivieren" in request.POST:
            self.tank.dissolved_on = None
            self.tank.save(update_fields=["dissolved_on"])
            messages.success(request, f"„{self.tank.name}“ ist wieder in Betrieb.")
            return HttpResponseRedirect(self.tank.get_absolute_url())

        form = TankDissolveForm(request.POST, instance=self.tank)
        if not form.is_valid():
            return self.render_form(form)
        form.save()
        messages.success(request, f"„{self.tank.name}“ aufgelöst.")
        return HttpResponseRedirect(self.tank.get_absolute_url())

    def render_form(self, form):
        return render(
            self.request,
            self.template_name,
            {"form": form, "tank": self.tank, "nav_section": "tanks"},
        )


class TankDeleteView(TankScopedMixin, View):
    """Becken löschen — nur, solange nichts daran hängt.

    Sobald Messwerte, Ereignisse, Besatz, Termine, Geräte oder Fotos erfasst
    sind, gibt es kein Löschen mehr, sondern nur noch die Auflösung. Löschen
    bleibt der Fehleingabe vorbehalten.
    """

    template_name = "tanks/tank_delete.html"

    def get(self, request, **kwargs):
        return self.render_page()

    def post(self, request, **kwargs):
        if self.tank.has_history:
            messages.warning(
                request,
                f"„{self.tank.name}“ hat erfasste Daten und wird deshalb aufgelöst "
                "statt gelöscht.",
            )
            return HttpResponseRedirect(reverse("tanks:dissolve", args=[self.tank.slug]))
        name = self.tank.name
        self.tank.delete()
        messages.success(request, f"„{name}“ gelöscht.")
        return HttpResponseRedirect(reverse("tanks:list"))

    def render_page(self):
        return render(
            self.request,
            self.template_name,
            {
                "tank": self.tank,
                "has_history": self.tank.has_history,
                "nav_section": "tanks",
            },
        )


# --- Messwerte ------------------------------------------------------------


class MeasurementCreateView(TankFragmentView):
    """Messreihe erfassen: alle Parameter zu einem Zeitpunkt.

    Der Zeitpunkt ist frei wählbar, auch rückwirkend — Testergebnisse werden
    oft erst am Abend nachgetragen.
    """

    model = Measurement
    tab = "messwerte"

    def get(self, request, **kwargs):
        return self.render_tab(self.form_context(MeasurementSeriesForm()))

    def post(self, request, **kwargs):
        form = MeasurementSeriesForm(request.POST)
        if not form.is_valid():
            return self.render_tab(self.form_context(form))
        created = form.save(self.tank, user=request.user)
        return self.done(f"{len(created)} Messwert(e) erfasst.")

    def form_context(self, form):
        return self.modal(
            title="Messreihe erfassen",
            action=reverse("tanks:measurement-create", args=[self.tank.slug]),
            body_template="tanks/partials/form_modal.html",
            form=form,
            modal_submit="Erfassen",
            modal_hint="Nicht nachweisbare Werte als „n.n.“ eintragen oder den "
            "Schalter „nicht nachweisbar“ setzen. Leere Felder werden nicht "
            "gespeichert.",
        )


class MeasurementUpdateView(TankObjectFormView):
    model = Measurement
    tab = "messwerte"
    form_class = MeasurementForm
    update_title = "Messwert bearbeiten"
    update_url_name = "tanks:measurement-update"


class MeasurementDeleteView(TankObjectConfirmView):
    model = Measurement
    tab = "messwerte"
    title = "Messwert löschen"
    question = "Dieser Messwert wird gelöscht. Fortfahren?"
    url_name = "tanks:measurement-delete"

    def describe(self, obj):
        return f"{obj.parameter.name}: {obj.display_value} vom {obj.measured_at:%d.%m.%Y %H:%M}"


# --- Ereignisse -----------------------------------------------------------


class EventFormView(TankObjectFormView):
    """Ereignis anlegen oder bearbeiten — samt Bildern in einem Durchgang.

    Das Formular nimmt Bilder entgegen, die Fotos entstehen aber erst, wenn das
    Ereignis eine Kennung hat. Deshalb der zweite Schritt nach ``save()``.
    """

    model = Event
    tab = "ereignisse"
    form_class = EventForm
    multipart = True

    #: Fotos dieses Durchgangs — ``save()`` füllt sie, die Meldung liest sie.
    photos = ()

    def save(self, form):
        event = super().save(form)
        self.photos = form.save_photos(event)
        return event

    def success_message(self, obj):
        message = super().success_message(obj)
        if not self.photos:
            return message
        return f"{message} {len(self.photos)} Foto(s) hinzugefügt."


class EventCreateView(EventFormView):
    create_title = "Ereignis erfassen"
    create_url_name = "tanks:event-create"

    #: Kategorie, die das leere Formular vorschlägt. ``EventObservationCreateView``
    #: setzt sie um — dieselbe Ansicht, nur ein anderer Einstieg.
    initial_category = None

    def get_form(self, data=None, files=None):
        form = super().get_form(data, files)
        if data is None and self.initial_category:
            form.initial["category"] = self.initial_category
        return form


class EventObservationCreateView(EventCreateView):
    """„Beobachtung erfassen“ — der Hauptfall am Becken, mit dem Telefon.

    Kein eigener Ablauf und kein eigenes Modell: dasselbe Formular mit
    vorgewählter Kategorie. Wer am Becken steht, soll die Auswahlliste nicht
    erst durchsuchen müssen.
    """

    create_title = "Beobachtung erfassen"
    create_url_name = "tanks:observation-create"
    initial_category = Event.Category.OBSERVATION
    hint = (
        "Fotografieren, zwei Sätze dazu, fertig. Bleibt der Zeitpunkt leer, "
        "kommt er aus den Bildern."
    )


class EventUpdateView(EventFormView):
    update_title = "Ereignis bearbeiten"
    update_url_name = "tanks:event-update"


class EventDeleteView(TankObjectConfirmView):
    model = Event
    tab = "ereignisse"
    title = "Ereignis löschen"
    question = "Dieses Ereignis wird gelöscht. Fortfahren?"
    url_name = "tanks:event-delete"


# --- Besatz ---------------------------------------------------------------


class StockingCreateView(TankObjectFormView):
    model = Stocking
    tab = "besatz"
    form_class = StockingForm
    create_title = "Besatz einsetzen"
    create_url_name = "tanks:stocking-create"


class StockingUpdateView(TankObjectFormView):
    model = Stocking
    tab = "besatz"
    form_class = StockingForm
    update_title = "Besatz bearbeiten"
    update_url_name = "tanks:stocking-update"


class StockingRemoveView(TankObjectFormView):
    """Abgang buchen statt löschen."""

    model = Stocking
    tab = "besatz"
    form_class = StockingRemovalForm
    update_title = "Abgang buchen"
    update_url_name = "tanks:stocking-remove"
    submit_label = "Abgang buchen"
    hint = (
        "Der Besatz bleibt mit Abgangsdatum in der Beckengeschichte stehen "
        "und zählt ab dann nicht mehr mit."
    )

    def success_message(self, obj):
        return f"Abgang für {obj.species.display_name} gebucht."


# --- Bepflanzung ----------------------------------------------------------


class PlantingCreateView(TankObjectFormView):
    model = Planting
    tab = "pflanzen"
    form_class = PlantingForm
    create_title = "Pflanze einsetzen"
    create_url_name = "tanks:planting-create"


class PlantingUpdateView(TankObjectFormView):
    model = Planting
    tab = "pflanzen"
    form_class = PlantingForm
    update_title = "Bepflanzung bearbeiten"
    update_url_name = "tanks:planting-update"


class PlantingDeleteView(TankObjectConfirmView):
    model = Planting
    tab = "pflanzen"
    title = "Bepflanzung löschen"
    question = "Dieser Eintrag wird gelöscht. Fortfahren?"
    url_name = "tanks:planting-delete"


# --- Umsetzen zwischen Becken ---------------------------------------------


class TransferView(TankFragmentView):
    """Tiere oder Pflanzen in ein anderes eigenes Becken umsetzen.

    Gibt es etwas zu bedenken — abweichende Wasserwerte, Artansprüche, eine zu
    klein werdende Gruppe —, zeigt der erste ``POST`` die Hinweise und lässt
    das Formular stehen; erst der zweite bucht. Das ist keine Sperre: derselbe
    Knopf führt weiter, er heißt nur anders. Der Zwischenschritt ist der einzige
    Weg, der ohne JavaScript auskommt und den Hinweis trotzdem *vor* den Umzug
    stellt — hinterher wäre er ein Vorwurf statt einer Entscheidungshilfe.
    """

    #: Name der URL, an die das Formular sendet.
    url_name = ""
    #: Überschrift des Overlays.
    title = ""

    def get_queryset(self):
        # Was abgegangen ist, zieht nicht mehr um.
        return self.model.objects.filter(tank=self.tank, removed_on__isnull=True)

    def get_form(self, data=None):
        return TransferForm(data, entry=self.get_object())

    def form_context(self, form, hints=()):
        hints = list(hints)
        return self.modal(
            title=self.title,
            action=reverse(self.url_name, args=[self.tank.slug, self.kwargs["pk"]]),
            body_template="tanks/partials/form_modal.html",
            modal_before_fields="tanks/partials/transfer_hints.html",
            form=form,
            modal_submit="Trotzdem umsetzen" if hints else "Umsetzen",
            modal_hint=(
                "Der Bestand wird im Quellbecken verringert und im Zielbecken "
                "zusammengeführt. Beide Becken bekommen ein Ereignis."
            ),
            transfer_hints=hints,
        )

    def get(self, request, **kwargs):
        return self.render_tab(self.form_context(self.get_form()))

    def post(self, request, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_tab(self.form_context(form))
        move = form.move()
        found = transfers.hints(move)
        if found and not request.POST.get("bestaetigt"):
            return self.render_tab(self.form_context(form, found))
        transfer = transfers.perform(move, user=request.user)
        return self.done(
            f"{transfer.quantity}× {transfer.species.display_name} nach "
            f"„{transfer.target_tank.name}“ umgesetzt."
        )


class StockingTransferView(TransferView):
    model = Stocking
    tab = "besatz"
    title = "Besatz umsetzen"
    url_name = "tanks:stocking-transfer"


class PlantingTransferView(TransferView):
    model = Planting
    tab = "pflanzen"
    title = "Pflanzen umsetzen"
    url_name = "tanks:planting-transfer"


class TransferListView(LoginRequiredMixin, NavSectionMixin, TemplateView):
    """Alle Umzüge des Benutzers, becken- und artübergreifend.

    In der Beckengeschichte steht ein Umzug zweimal — einmal je Becken. Wer
    nachvollziehen will, wohin eine Art gewandert ist, sucht sonst in zwei
    Zeitleisten nach zwei Hälften desselben Vorgangs.
    """

    template_name = "tanks/transfer_list.html"
    nav_section = "tanks"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["transfers"] = transfers.for_user(self.request.user)
        return context


# --- Termine --------------------------------------------------------------


class CareTaskCreateView(TankObjectFormView):
    model = CareTask
    tab = "termine"
    form_class = CareTaskForm
    create_title = "Termin anlegen"
    create_url_name = "tanks:task-create"


class CareTaskUpdateView(TankObjectFormView):
    model = CareTask
    tab = "termine"
    form_class = CareTaskForm
    update_title = "Termin bearbeiten"
    update_url_name = "tanks:task-update"


class CareTaskToggleView(TankObjectConfirmView):
    """Termin deaktivieren statt löschen — die Quittierungen bleiben.

    Das Wiedereinschalten braucht keinen Zwischenschritt: es macht nichts
    kaputt und ist mit demselben Klick wieder rückgängig.
    """

    model = CareTask
    tab = "termine"
    title = "Termin deaktivieren"
    question = "Der Termin wird deaktiviert und taucht nicht mehr als fällig auf."
    submit_label = "Deaktivieren"
    url_name = "tanks:task-toggle"

    def get(self, request, **kwargs):
        task = self.get_object()
        if not task.is_active:
            # Nichts zu bestätigen — das Formular schaltet direkt wieder ein.
            return self.render_tab()
        return super().get(request, **kwargs)

    def perform(self, task):
        task.is_active = not task.is_active
        task.save(update_fields=["is_active"])
        return f"„{task.title}“ {'aktiviert' if task.is_active else 'deaktiviert'}."


# --- Einrichtung ----------------------------------------------------------


def reminder_modal(view, source, url_name):
    """Overlay, das einen Termin vorschlägt — vorbelegt aus der Einrichtung.

    Angeboten, nicht angelegt: ein selbsttätig erscheinender Termin, den
    niemand wollte, untergräbt das Vertrauen in die Terminliste. Abgelehnt wird
    das Angebot durch „Abbrechen“, und dann bleibt es dabei.
    """
    return view.modal(
        title="Termin anlegen?",
        action=reverse(url_name, args=[view.tank.slug, source.pk]),
        body_template="tanks/partials/form_modal.html",
        form=CareTaskForm(initial=source.reminder_defaults()),
        modal_submit="Termin anlegen",
        modal_hint=(
            "Diese Angabe hat eine Standzeit. Der Termin ist vorbelegt und "
            "änderbar — angelegt wird er nur, wenn du das hier bestätigst."
        ),
    )


class ReminderOfferMixin:
    """Bietet nach dem Anlegen den passenden Termin an.

    Nur beim Anlegen: beim Bearbeiten stünde das Angebot erneut im Weg,
    obwohl es beim ersten Mal vielleicht abgelehnt wurde. Nachträglich führt
    die Zeilenaktion *Erinnerung* zum selben Formular.
    """

    reminder_url_name = ""

    def post(self, request, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_tab(self.form_context(form))
        obj = self.save(form)
        if obj.suggests_reminder:
            return self.render_tab(reminder_modal(self, obj, self.reminder_url_name))
        return self.done(self.success_message(obj))


class ReminderCreateView(TankFragmentView):
    """Der Termin zu einer Einrichtungsposition, vorbelegt aus deren Standzeit.

    Ein eigener Weg statt ``task-create``, weil die Vorbelegung aus der
    Bodengrundschicht bzw. dem Hardscape kommt und die Antwort im Reiter
    *Einrichtung* bleiben soll.
    """

    tab = "einrichtung"
    #: URL-Name dieser Ansicht — für die Formularadresse des Overlays.
    url_name = ""

    def get(self, request, **kwargs):
        return self.render_tab(reminder_modal(self, self.get_object(), self.url_name))

    def post(self, request, **kwargs):
        source = self.get_object()
        form = CareTaskForm(request.POST)
        if not form.is_valid():
            context = reminder_modal(self, source, self.url_name)
            context["form"] = form
            return self.render_tab(context)
        task = form.save(commit=False)
        task.tank = self.tank
        task.save()
        return self.done(f"Termin „{task.title}“ angelegt.")


class SubstrateLayerCreateView(ReminderOfferMixin, TankObjectFormView):
    model = SubstrateLayer
    tab = "einrichtung"
    form_class = SubstrateLayerForm
    create_title = "Bodengrundschicht anlegen"
    create_url_name = "tanks:substrate-create"
    reminder_url_name = "tanks:substrate-reminder"
    hint = "Die Schicht kommt oben auf den Stapel; verschieben lässt sie sich danach."

    def save(self, form):
        # Aufgefüllt wird von oben — und die Positionen sind lückenlos, dafür
        # sorgt :meth:`SubstrateLayer.move`.
        form.instance.position = self.tank.substrate_layers.count()
        return super().save(form)


class SubstrateLayerUpdateView(TankObjectFormView):
    model = SubstrateLayer
    tab = "einrichtung"
    form_class = SubstrateLayerForm
    update_title = "Bodengrundschicht bearbeiten"
    update_url_name = "tanks:substrate-update"


class SubstrateLayerDeleteView(TankObjectConfirmView):
    model = SubstrateLayer
    tab = "einrichtung"
    title = "Schicht löschen"
    question = "Diese Schicht wird gelöscht. Fortfahren?"
    url_name = "tanks:substrate-delete"


class SubstrateLayerMoveView(TankFragmentView):
    """Schicht im Stapel verschieben.

    Nur ``POST`` — die Reihenfolge ist eine Angabe und ändert Daten. Eine
    Rückfrage gibt es trotzdem nicht: der Gegenpfeil nimmt den Schritt zurück.
    """

    model = SubstrateLayer
    tab = "einrichtung"

    def post(self, request, **kwargs):
        self.get_object().move(1 if request.POST.get("richtung") == "hoch" else -1)
        return self.done()


class HardscapeCreateView(ReminderOfferMixin, TankObjectFormView):
    model = HardscapeItem
    tab = "einrichtung"
    form_class = HardscapeItemForm
    create_title = "Hardscape erfassen"
    create_url_name = "tanks:hardscape-create"
    reminder_url_name = "tanks:hardscape-reminder"
    hint = (
        "Was die Wasserwerte beeinflusst, gehört angehakt: bei einer "
        "unerklärten Veränderung ist die Einrichtung der erste Verdächtige."
    )


class HardscapeUpdateView(TankObjectFormView):
    model = HardscapeItem
    tab = "einrichtung"
    form_class = HardscapeItemForm
    update_title = "Hardscape bearbeiten"
    update_url_name = "tanks:hardscape-update"


class HardscapeRemoveView(TankObjectFormView):
    """Als entfernt markieren statt löschen."""

    model = HardscapeItem
    tab = "einrichtung"
    form_class = HardscapeRemovalForm
    update_title = "Als entfernt markieren"
    update_url_name = "tanks:hardscape-remove"
    submit_label = "Entfernt buchen"
    hint = (
        "Die Position bleibt mit Datum in der Beckengeschichte stehen — sie "
        "erklärt womöglich einen Verlauf, der später auffällt."
    )

    def success_message(self, obj):
        return f"{obj.name} als entfernt gebucht."


class SubstrateReminderView(ReminderCreateView):
    model = SubstrateLayer
    url_name = "tanks:substrate-reminder"


class HardscapeReminderView(ReminderCreateView):
    model = HardscapeItem
    url_name = "tanks:hardscape-reminder"


# --- Zielbereiche ---------------------------------------------------------


class TargetCreateView(TankObjectFormView):
    """Zielbereich anlegen — inline auf der Übersicht des Beckens."""

    model = TankParameterTarget
    tab = "uebersicht"
    form_class = TankParameterTargetForm
    create_title = "Zielbereich festlegen"
    create_url_name = "tanks:target-create"

    def form_kwargs(self):
        return {"tank": self.tank}

    def get(self, request, **kwargs):
        # Das Anlegeformular steht dauerhaft in der Übersicht; ein eigener
        # Aufruf braucht deshalb kein Overlay.
        return self.render_tab()

    def post(self, request, **kwargs):
        form = self.get_form(request.POST)
        if not form.is_valid():
            return self.render_tab({"target_form": form})
        return self.done(self.success_message(self.save(form)))


class TargetUpdateView(TankObjectFormView):
    model = TankParameterTarget
    tab = "uebersicht"
    form_class = TankParameterTargetForm
    update_title = "Zielbereich bearbeiten"
    update_url_name = "tanks:target-update"

    def form_kwargs(self):
        return {"tank": self.tank}


class TargetDeleteView(TankObjectConfirmView):
    model = TankParameterTarget
    tab = "uebersicht"
    title = "Zielbereich löschen"
    question = "Danach gilt wieder der Standardbereich des Parameters."
    url_name = "tanks:target-delete"

    def describe(self, obj):
        return f"{obj.parameter.name}: {obj.parameter.format_range(obj.minimum, obj.maximum)}"


class DerivedTargetMixin:
    """Gemeinsames für die Zielbereiche der gerechneten Größen.

    Angesprochen werden sie über den Schlüssel aus der Adresse und nicht über
    eine Kennung: die Zeile in ``TankDerivedTarget`` entsteht erst, wenn jemand
    die Vorgabe überschreibt, und bis dahin gibt es keine ``pk``, die man
    verlinken könnte.
    """

    model = TankDerivedTarget
    tab = "uebersicht"

    @property
    def parameter(self):
        found = derived.DERIVED_PARAMETERS.get(self.kwargs["key"])
        if found is None:
            raise Http404("Diese berechnete Größe gibt es nicht.")
        return found

    def get_target(self):
        return self.get_queryset().filter(key=self.parameter.key).first()


class DerivedTargetUpdateView(DerivedTargetMixin, TankFragmentView):
    """Zielbereich einer gerechneten Größe festlegen oder ändern."""

    def form(self, data=None):
        instance = self.get_target()
        if instance is None:
            # Vorbelegt mit der geltenden Vorgabe: wer 15–25 auf 12–20 ändern
            # will, soll nicht zwei leere Felder vorfinden.
            instance = TankDerivedTarget(
                tank=self.tank,
                key=self.parameter.key,
                minimum=self.parameter.default_min,
                maximum=self.parameter.default_max,
            )
        return TankDerivedTargetForm(data, instance=instance)

    def form_context(self, form):
        return self.modal(
            title=f"Zielbereich {self.parameter.name}",
            action=reverse(
                "tanks:derived-target-update", args=[self.tank.slug, self.parameter.key]
            ),
            body_template="tanks/partials/form_modal.html",
            form=form,
            modal_submit="Speichern",
            modal_hint=f"{self.parameter.name} wird aus "
            f"{self.parameter.formula} gerechnet und nicht gemessen. "
            f"Ohne eigenen Zielbereich gilt "
            f"{self.parameter.format_range(self.parameter.default_min, self.parameter.default_max)}.",
        )

    def get(self, request, **kwargs):
        return self.render_tab(self.form_context(self.form()))

    def post(self, request, **kwargs):
        form = self.form(request.POST)
        if not form.is_valid():
            return self.render_tab(self.form_context(form))
        form.save()
        return self.done("Gespeichert.")


class DerivedTargetResetView(DerivedTargetMixin, TankFragmentView):
    """Eigenen Zielbereich verwerfen — danach gilt wieder die Vorgabe."""

    def get_action(self):
        return reverse("tanks:derived-target-reset", args=[self.tank.slug, self.parameter.key])

    def get(self, request, **kwargs):
        target = self.get_target()
        if target is None:
            raise Http404("Für diese Größe ist kein eigener Zielbereich hinterlegt.")
        default = self.parameter.format_range(
            self.parameter.default_min, self.parameter.default_max
        )
        return self.render_tab(
            self.modal(
                title=f"Zielbereich {self.parameter.name} zurücksetzen",
                action=self.get_action(),
                body_template="tanks/partials/confirm_modal.html",
                modal_question=f"Danach gilt wieder die Vorgabe {default}.",
                modal_subject=f"{self.parameter.name}: {target.range_label}",
                modal_submit="Zurücksetzen",
            )
        )

    def post(self, request, **kwargs):
        target = self.get_target()
        if target is not None:
            target.delete()
        return self.done("Zurückgesetzt.")


# --- Fotos ----------------------------------------------------------------


class PhotoCreateView(TankFragmentView):
    """Mehrere Fotos in einem Durchgang hochladen."""

    model = TankPhoto
    tab = "galerie"

    def get(self, request, **kwargs):
        return self.render_tab(self.form_context(PhotoUploadForm()))

    def post(self, request, **kwargs):
        form = PhotoUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return self.render_tab(self.form_context(form))
        created = form.save(self.tank)
        return self.done(f"{len(created)} Foto(s) hochgeladen.")

    def form_context(self, form):
        return self.modal(
            title="Fotos hochladen",
            action=reverse("tanks:photo-create", args=[self.tank.slug]),
            body_template="tanks/partials/form_modal.html",
            form=form,
            modal_submit="Hochladen",
            modal_multipart=True,
        )


class PhotoDetailView(TankScopedMixin, View):
    """Großansicht eines Fotos — mit HTMX als Overlay, sonst als eigene Seite.

    Der Inhalt wird erst beim Klick geholt. Die Vorschauvarianten aller Bilder
    gleich mit der Galerie auszuliefern hätte die Kacheln überflüssig gemacht,
    für die sie da sind.

    Ohne HTMX antwortet dieselbe Adresse mit einer vollständigen Seite: das
    Foto bleibt erreichbar, auch wenn kein Skript läuft.
    """

    model = TankPhoto
    tab = "galerie"

    def get(self, request, **kwargs):
        photo = get_object_or_404(
            self.get_queryset().select_related("event"), pk=self.kwargs["pk"]
        )
        previous_pk, next_pk = selectors.photo_neighbours(self.tank, photo)
        context = {
            "tank": self.tank,
            "photo": photo,
            "previous_pk": previous_pk,
            "next_pk": next_pk,
            "gallery_href": f"{self.tank.get_absolute_url()}?reiter={self.tab}",
            "nav_section": "tanks",
        }
        template = (
            "tanks/partials/photo_lightbox.html"
            if getattr(request, "htmx", False)
            else "tanks/photo_detail.html"
        )
        return render(request, template, context)


class PhotoUpdateView(TankObjectFormView):
    """Bildunterschrift, Datum — und die Zuordnung zu einem Ereignis.

    Das Becken kommt aus dem Mixin; das Formular begrenzt die Ereignisauswahl
    darauf, damit ein Foto nicht an einem fremden Ereignis landet.
    """

    model = TankPhoto
    tab = "galerie"
    form_class = PhotoForm
    update_title = "Foto bearbeiten"
    update_url_name = "tanks:photo-update"

    def form_kwargs(self):
        return {"tank": self.tank}


class PhotoDeleteView(TankObjectConfirmView):
    model = TankPhoto
    tab = "galerie"
    title = "Foto löschen"
    question = "Das Foto wird gelöscht. Fortfahren?"
    url_name = "tanks:photo-delete"

    def describe(self, obj):
        return obj.alt_text
