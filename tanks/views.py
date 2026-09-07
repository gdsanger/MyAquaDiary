"""Beckenübersicht, Beckendetail mit Registerkarten — und die Schreibpfade.

Zum Aufbau der schreibenden Ansichten: sie antworten nicht mit einer eigenen
Seite, sondern immer mit dem Reiterbereich des Beckens. Per HTMX ist das das
Fragment, ohne HTMX dieselbe Seite als Ganzes. Damit bleibt der Reiterkontext
erhalten, und jede Ansicht ist auch ohne JavaScript bedienbar.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.generic import TemplateView, View

from core.views import NavSectionMixin
from services.ai import ai_enabled

from . import analysis, selectors
from .charts import key_parameter_charts
from .forms import (
    CareTaskForm,
    EventForm,
    MeasurementForm,
    MeasurementSeriesForm,
    PhotoForm,
    PhotoUploadForm,
    PlantingForm,
    StockingForm,
    StockingRemovalForm,
    TankDissolveForm,
    TankForm,
    TankParameterTargetForm,
)
from .models import (
    CareTask,
    Event,
    Measurement,
    Planting,
    Stocking,
    Tank,
    TankParameterTarget,
    TankPhoto,
)

#: Registerkarten des Beckendetails in Anzeigereihenfolge.
TABS = [
    ("uebersicht", "Übersicht"),
    ("messwerte", "Messwerte"),
    ("ereignisse", "Ereignisse"),
    ("besatz", "Besatz"),
    ("pflanzen", "Pflanzen"),
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
        tanks = Tank.objects.for_user(self.request.user).with_overview()
        context["active_tanks"] = list(tanks.active())
        context["dissolved_tanks"] = list(tanks.dissolved().order_by("-dissolved_on"))
        return context


def _get_tank(request, slug):
    return get_object_or_404(Tank.objects.for_user(request.user), slug=slug)


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
            "target_form": TankParameterTargetForm(tank=tank),
        }
    if tab == "messwerte":
        measurements = selectors.tank_measurements(tank, limit=MEASUREMENT_PAGE_SIZE)
        return {
            "measurements": measurements,
            "charts": key_parameter_charts(tank),
            "page_size": MEASUREMENT_PAGE_SIZE,
            # Serverseitig mitgeliefert und nicht erst nachgeladen: ohne
            # JavaScript ist die Auswertung sonst gar nicht zu sehen.
            "analysis": analysis.state(tank),
        }
    if tab == "ereignisse":
        return {"events": tank.events.all()[:100]}
    if tab == "besatz":
        return {"stockings": tank.stockings.select_related("species").prefetch_related("species__images")}
    if tab == "pflanzen":
        return {"plantings": tank.plantings.select_related("species").prefetch_related("species__images")}
    if tab == "termine":
        return {"tasks": tank.tasks.order_by("-is_active", "due_on")}
    if tab == "geraete":
        # ``devices`` sind die Geräte aus services.Device — dieselben, die der
        # Bereich /geraete/ zeigt. Ein zweites Gerätemodell gibt es nicht.
        return {"devices": tank.devices.order_by("kind", "name")}
    if tab == "galerie":
        return {"photos": tank.photos.all()}
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
        # Die Auswertung läuft neben der Anfrage her; gespeichert ist die
        # Messreihe auch dann, wenn Anthropic nicht antwortet.
        if created and self.tank.ai_analysis_automatic:
            analysis.start(analysis.anchor_of(created[0]), user=request.user)
        return self.done(f"{len(created)} Messwert(e) erfasst.")

    def form_context(self, form):
        return self.modal(
            title="Messreihe erfassen",
            action=reverse("tanks:measurement-create", args=[self.tank.slug]),
            body_template="tanks/partials/form_modal.html",
            form=form,
            modal_submit="Erfassen",
            modal_hint="Nicht nachweisbare Werte als „n.n.“ eintragen. "
            "Leere Felder werden nicht gespeichert.",
        )


class MeasurementAnalysisView(TankScopedMixin, View):
    """Die KI-Auswertung der jüngsten Messreihe — als eigenes Fragment.

    Bewusst nicht der ganze Reiterbereich: solange die Auswertung läuft, holt
    sich dieser Block sein Ergebnis im Sekundentakt nach, und dabei soll nicht
    jedes Mal die halbe Seite neu entstehen.

    Ohne HTMX gibt es kein Nachladen; dann führt der Weg über den Reiter, der
    denselben Block serverseitig rendert. ``POST`` landet deshalb ohne HTMX auf
    der Beckenseite und nicht auf einem nackten Fragment.
    """

    model = Measurement
    template_name = "tanks/partials/measurement_analysis.html"

    def dispatch(self, request, *args, **kwargs):
        # Kein API-Key heißt: die Adresse gibt es nicht — wie bei den übrigen
        # KI-Ansichten (siehe ``services.views._ai_view``).
        if not ai_enabled():
            raise Http404("Die KI-Assistenz ist nicht eingerichtet")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request, **kwargs):
        return self.render_state(analysis.state(self.tank))

    def post(self, request, **kwargs):
        anchor = analysis.latest_anchor(self.tank)
        state = analysis.start(anchor, user=request.user) if anchor else analysis.state(self.tank)
        return self.render_state(state)

    def render_state(self, state):
        if not getattr(self.request, "htmx", False):
            return HttpResponseRedirect(f"{self.tank.get_absolute_url()}?reiter=messwerte")
        return render(self.request, self.template_name, {"tank": self.tank, "analysis": state})


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


class EventCreateView(TankObjectFormView):
    model = Event
    tab = "ereignisse"
    form_class = EventForm
    create_title = "Ereignis erfassen"
    create_url_name = "tanks:event-create"


class EventUpdateView(TankObjectFormView):
    model = Event
    tab = "ereignisse"
    form_class = EventForm
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


class PhotoUpdateView(TankObjectFormView):
    model = TankPhoto
    tab = "galerie"
    form_class = PhotoForm
    update_title = "Bildunterschrift bearbeiten"
    update_url_name = "tanks:photo-update"


class PhotoDeleteView(TankObjectConfirmView):
    model = TankPhoto
    tab = "galerie"
    title = "Foto löschen"
    question = "Das Foto wird gelöscht. Fortfahren?"
    url_name = "tanks:photo-delete"

    def describe(self, obj):
        return obj.caption or f"Foto vom {obj.taken_on:%d.%m.%Y}"
