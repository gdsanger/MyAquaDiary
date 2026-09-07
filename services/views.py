"""Geräteverwaltung: suchen, anlegen, Status ansehen, steuern.

Der Status wird nie beim Aufbau einer Seite geholt, sondern per HTMX
nachgeladen (:func:`device_status`). Ein Gerät, das nicht antwortet, kostet
damit nur einen Platzhalter im Layout und blockiert keine Seite.
"""

import mimetypes
from dataclasses import dataclass
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from tanks.models import Tank

from . import ai
from . import devices as device_service
from . import energy
from .charts import bar_chart, power_chart, rpm_chart
from .eheim import (
    FIRMWARE_HINT,
    EheimClient,
    EheimError,
    EheimService,
    is_supported_firmware,
    normalize_mac,
)
from .forms import (
    CandidateForm,
    DeviceCoverForm,
    DeviceDiscoveryForm,
    DeviceDocumentForm,
    DeviceForm,
    DeviceLinkForm,
    DevicePasswordForm,
    DeviceSpecForm,
    IdentifyForm,
    ManualDeviceForm,
    MCPTokenForm,
    ShellyDeviceForm,
    controls_for,
)
from .mcp.auth import TOKEN_PARAM
from .models import AISuggestion, Device, MCPToken
from .shelly import ShellyClient, ShellyError, ShellyService

#: So viele Messwerte gehen in das Verlaufsdiagramm.
CHART_READINGS = 200
#: So viele Protokollzeilen zeigt die Detailseite.
EVENT_ROWS = 20

#: Beschriftungen der Steckbrief-Felder. Die Schlüssel sind die Feldnamen des
#: Katalogs (siehe services.ai.schemas), damit ein bestätigter Entwurf ohne
#: Übersetzung dorthin wandert.
_SHARED_LABELS = {
    "scientific_name": "Wissenschaftlicher Name",
    "common_name": "Deutscher Name",
    "family": "Familie",
    "origin": "Herkunft",
    "difficulty": "Anspruch",
    "temp_min_c": "Temperatur ab (°C)",
    "temp_max_c": "Temperatur bis (°C)",
    "ph_min": "pH ab",
    "ph_max": "pH bis",
    "description": "Beschreibung",
    "care_notes": "Pflege",
    "warning": "Warnung",
    "uncertainties": "Unsicher",
}
PROFILE_LABELS = {
    AISuggestion.Kind.ANIMAL: {
        **_SHARED_LABELS,
        "group": "Gruppe",
        "size_max_cm": "Endgröße (cm)",
        "min_tank_liters": "Mindestvolumen (l)",
        "min_tank_length_cm": "Mindestkantenlänge (cm)",
        "min_group_size": "Mindestgruppe",
        "social_behavior": "Sozialverhalten",
        "zone": "Schwimmzone",
        "lifespan_years": "Lebenserwartung (Jahre)",
        "gh_min": "GH ab",
        "gh_max": "GH bis",
        "diet": "Ernährung",
        "compatibility_notes": "Verträglichkeit",
    },
    AISuggestion.Kind.PLANT: {
        **_SHARED_LABELS,
        "growth_form": "Wuchsform",
        "placement": "Platzierung",
        "growth_rate": "Wuchs",
        "light_demand": "Licht",
        "co2_demand": "CO2",
        "height_min_cm": "Höhe ab (cm)",
        "height_max_cm": "Höhe bis (cm)",
        "propagation": "Vermehrung",
    },
}


def _device(request, pk) -> Device:
    """Gerät des angemeldeten Benutzers — fremde Geräte gibt es nicht."""
    return get_object_or_404(Device, pk=pk, owner=request.user)


@login_required
def device_list(request):
    """Übersicht der eigenen Geräte; der Status kommt je Karte per HTMX nach.

    Angebundene und nur dokumentierte Geräte stehen in derselben Liste — es ist
    dieselbe Geräteliste, die auch der Beckenreiter „Geräte" zeigt.
    """
    return render(
        request,
        "services/device_list.html",
        {
            "devices": request.user.devices.select_related("tank"),
            "warnings": device_service.warnings_for(request.user),
        },
    )


@login_required
def device_detail(request, pk):
    """Detailseite mit Verlauf, Steuerung und Protokoll.

    Welche Diagramme es gibt, hängt am Gerät: eine Pumpe hat eine Drehzahl,
    eine Steckdose Leistung und Verbrauch.
    """
    device = _device(request, pk)
    return render(request, "services/device_detail.html", _detail_context(request, device))


def _detail_context(request, device: Device) -> dict:
    """Alles, was die Gerätedetailseite zeigt.

    Steht als eigene Funktion, weil die Abschnitte für technische Daten,
    Dokumente und Links dieselbe Seite ohne HTMX noch einmal vollständig
    rendern müssen — mit ihrem Formular darin.
    """
    readings = list(device.readings.all()[:CHART_READINGS]) if device.is_connected else []
    period = energy.normalize_period(request.GET.get("zeitraum"))
    buckets = energy.device_buckets(device, period)
    return {
        "device": device,
        "chart": power_chart(readings) if device.is_shelly else rpm_chart(readings),
        "readings": readings[:20],
        "events": device.events.all()[:EVENT_ROWS],
        "controls": [(action, label) for action, (_form, label) in controls_for(device).items()],
        "firmware_hint": FIRMWARE_HINT if _firmware_outdated(device) else "",
        "period": period,
        "periods": energy.PERIODS,
        "buckets": buckets,
        "energy_estimated": not device.is_metered,
        "energy_chart": bar_chart(
            [(bucket.label, bucket.kwh) for bucket in buckets],
            description="Stromverbrauch je Zeitraum in Kilowattstunden",
        ),
        "price_per_kwh": energy.price_per_kwh(),
        "specs": device.specs.all(),
        "documents": device.documents.all(),
        "links": device.links.all(),
    }


def _firmware_outdated(device: Device) -> bool:
    """Der Hinweis auf zu alte Gerätesoftware gilt nur für Eheim — Shelly hat
    keine Mindestversion für die lokale API."""
    return device.is_eheim and device.firmware_supported is False


@login_required
def device_status(request, pk):
    """Live-Status als Fragment — Einstiegspunkt für ``hx-get``.

    Antwortet immer mit ``200``: der Fehlerfall ist eine Karte mit Hinweis,
    keine Fehlerseite im halben Layout.
    """
    device = _device(request, pk)
    result = device_service.probe(device)
    return render(
        request,
        "services/_device_status.html",
        {"device": device, "status": result.status, "reading": result.reading, "error": result.error},
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_add(request):
    """Ein Gerät ohne Anbindung erfassen.

    Für alles, was am Becken hängt und nicht am Netz: Heizer, CO₂-Anlage,
    Beleuchtung. Der Weg über die Mesh-Suche oder die Shelly-Adresse steht
    daneben und ändert sich nicht — es ist dasselbe Modell, nur ohne Anbindung.
    """
    form = ManualDeviceForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        device = form.save(commit=False)
        device.owner = request.user
        device.save()
        messages.success(request, f"{device.name} erfasst.")
        return redirect("services:device_detail", pk=device.pk)
    return render(request, "services/device_add.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def device_discover(request):
    """Mesh über ein erreichbares Gerät durchsuchen und Geräte anlegen.

    Ein Durchgang, zwei Schaltflächen: *Suchen* holt die Liste über
    ``/mesh-liste``, *Anlegen* übernimmt die angehakten Geräte. Die
    Zugangsdaten bleiben dabei im Formular und werden nicht zwischengelagert.
    """
    form = DeviceDiscoveryForm(request.POST or None)
    found, error = [], ""

    if request.method == "POST" and form.is_valid():
        client = EheimClient(
            form.cleaned_data["host"],
            form.cleaned_data["username"],
            form.cleaned_data["password"],
        )
        if request.POST.get("action") == "create":
            created = _create_selected(request, form.cleaned_data)
            if created:
                return redirect("services:device_list")
        try:
            found = EheimService(client=client).discover()
        except EheimError as exc:
            error = str(exc)
        else:
            if not found:
                error = "Unter dieser Adresse hat sich kein Gerät gemeldet."

    return render(
        request,
        "services/device_discover.html",
        {
            "form": form,
            "found": found,
            "error": error,
            "kinds": [
                (value, label)
                for value, label in Device.Kind.choices
                if value in Device.EHEIM_KINDS
            ],
            "tanks": Tank.objects.for_user(request.user),
        },
    )


def _selected_tank(tanks, value):
    """Das gewählte Becken aus der Auswahl des Benutzers, sonst ``None``."""
    try:
        return tanks.filter(pk=int(value)).first()
    except (TypeError, ValueError):
        return None


def _create_selected(request, credentials) -> int:
    """Legt die angehakten Geräte an; meldet jedes Ergebnis als Message."""
    created = 0
    tanks = Tank.objects.for_user(request.user)
    for mac in request.POST.getlist("macs"):
        mac = normalize_mac(mac)
        firmware = request.POST.get(f"firmware_{mac}", "").strip()
        if firmware and not is_supported_firmware(firmware):
            messages.warning(
                request, f"{mac} läuft mit Gerätesoftware {firmware}. {FIRMWARE_HINT}"
            )
            continue
        if Device.objects.filter(owner=request.user, mac_address=mac).exists():
            messages.info(request, f"{mac} ist bereits angelegt.")
            continue
        # Das Becken ist Pflicht — und es ist eines des Benutzers. Eine fremde
        # Kennung findet sich hier schlicht nicht wieder.
        tank = _selected_tank(tanks, request.POST.get(f"tank_{mac}"))
        if tank is None:
            messages.error(request, f"{mac}: Bitte ein Becken auswählen.")
            continue

        kind = request.POST.get(f"kind_{mac}", "")
        device = Device(
            owner=request.user,
            tank=tank,
            name=request.POST.get(f"name_{mac}", "").strip() or mac,
            kind=kind if kind in Device.EHEIM_KINDS else Device.Kind.EHEIM_OTHER,
            mac_address=mac,
            host=credentials["host"],
            firmware=firmware[:40],
        )
        device.set_credentials(credentials["username"], credentials["password"])
        try:
            device.full_clean(exclude=["owner"])
        except ValidationError as exc:
            messages.error(request, f"{mac} konnte nicht angelegt werden: {'; '.join(exc.messages)}")
            continue
        device.save()
        created += 1
        messages.success(request, f"{device.name} angelegt.")
        if device.uses_default_password:
            messages.warning(
                request,
                f"{device.name} nutzt noch das Werkspasswort — bitte über "
                "„Zugangsdaten ändern“ ein eigenes Passwort setzen.",
            )
    return created


@login_required
@require_http_methods(["GET", "POST"])
def device_edit(request, pk):
    """Stammdaten und — bei angebundenen Geräten — Zugangsdaten pflegen."""
    device = _device(request, pk)
    form_class = _form_class(device)
    form = form_class(request.POST or None, instance=device, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Gerät gespeichert.")
        return redirect("services:device_detail", pk=device.pk)
    return render(request, "services/device_form.html", {"form": form, "device": device})


def _form_class(device: Device):
    """Formular passend zur Geräteart — ohne Anbindung ohne Zugangsdaten."""
    if device.is_shelly:
        return ShellyDeviceForm
    if device.is_eheim:
        return DeviceForm
    return ManualDeviceForm


@login_required
@require_http_methods(["GET", "POST"])
def device_control(request, pk, action):
    """Schreibender Befehl — erst nach ausdrücklicher Bestätigung.

    Der erste POST zeigt, was genau ans Gerät ginge; erst der zweite mit
    ``confirm=1`` schickt es tatsächlich los. Ohne Bestätigung passiert nichts,
    auch nicht bei einem direkt abgeschickten Formular.
    """
    device = _device(request, pk)
    controls = controls_for(device)
    if action not in controls:
        raise Http404("Unbekannte Aktion")

    form_class, label = controls[action]
    form = form_class(request.POST or None) if form_class else None

    if request.method == "POST" and (form is None or form.is_valid()):
        params = dict(form.cleaned_data) if form else {}
        if request.POST.get("confirm") == "1":
            result = device_service.execute(device, action, params, user=request.user)
            if result:
                messages.success(request, f"{device.name}: {result.message}")
            else:
                messages.error(request, f"{device.name}: {result.message}")
            return redirect("services:device_detail", pk=device.pk)

        return render(
            request,
            "services/device_control_confirm.html",
            {
                "device": device,
                "action": action,
                "label": label,
                "summary": device_service.describe(device, action, params),
                "posted": [
                    (key, value)
                    for key, value in request.POST.items()
                    if key not in ("csrfmiddlewaretoken", "confirm")
                ],
            },
        )

    return render(
        request,
        "services/device_control.html",
        {"device": device, "action": action, "label": label, "form": form},
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_credentials(request, pk):
    """Gerätepasswort über ``/changeauth`` ändern — nur bei Eheim.

    Eine Shelly-Steckdose bekommt ihr Passwort in ihrer eigenen Oberfläche;
    hier wird es nur hinterlegt (siehe :func:`device_edit`).
    """
    device = _device(request, pk)
    if not device.is_eheim:
        raise Http404("Für diesen Gerätetyp gibt es hier keine Zugangsdaten")
    form = DevicePasswordForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = device_service.change_password(
            device, form.cleaned_data["password"], user=request.user
        )
        if result:
            messages.success(request, f"{device.name}: {result.message}")
            return redirect("services:device_detail", pk=device.pk)
        messages.error(request, f"{device.name}: {result.message}")
    return render(request, "services/device_credentials.html", {"device": device, "form": form})


# --------------------------------------------------------------------------
# Technische Daten, Dokumente und Links
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Section:
    """Ein nachladbarer Abschnitt der Gerätedetailseite.

    Die drei Abschnitte unterscheiden sich in Modell, Formular und Beschriftung
    — im Ablauf nicht. Was sie gemeinsam haben, steht deshalb einmal in
    :func:`_section_form` und :func:`_section_delete`; was sie unterscheidet,
    steht hier.
    """

    #: Zugleich der ``related_name`` am Gerät und der Schlüssel im Kontext.
    key: str
    template: str
    form_class: type
    create_title: str
    update_title: str
    delete_title: str
    delete_question: str
    create_url_name: str
    update_url_name: str
    delete_url_name: str
    multipart: bool = False


SECTIONS = {
    "specs": Section(
        key="specs",
        template="services/_device_specs.html",
        form_class=DeviceSpecForm,
        create_title="Technische Angabe",
        update_title="Angabe bearbeiten",
        delete_title="Angabe löschen",
        delete_question="Soll diese Angabe entfernt werden?",
        create_url_name="services:device_spec_create",
        update_url_name="services:device_spec_update",
        delete_url_name="services:device_spec_delete",
    ),
    "documents": Section(
        key="documents",
        template="services/_device_documents.html",
        form_class=DeviceDocumentForm,
        create_title="Dokument hinzufügen",
        update_title="Dokument bearbeiten",
        delete_title="Dokument löschen",
        delete_question="Soll dieses Dokument samt Datei gelöscht werden?",
        create_url_name="services:device_document_create",
        update_url_name="services:device_document_update",
        delete_url_name="services:device_document_delete",
        multipart=True,
    ),
    "links": Section(
        key="links",
        template="services/_device_links.html",
        form_class=DeviceLinkForm,
        create_title="Link hinzufügen",
        update_title="Link bearbeiten",
        delete_title="Link löschen",
        delete_question="Soll dieser Link entfernt werden?",
        create_url_name="services:device_link_create",
        update_url_name="services:device_link_update",
        delete_url_name="services:device_link_delete",
    ),
}


def _render_section(request, device: Device, section: Section, **extra):
    """Abschnitt zurückgeben — mit HTMX das Fragment, ohne HTMX die ganze Seite.

    Ohne JavaScript ist jede Schaltfläche ein Link und jedes Formular ein
    regulärer POST; dann kommt die vollständige Detailseite zurück, mit dem
    Formular an seinem Platz im Abschnitt. Das Markup dafür gibt es nur einmal.
    """
    context = {"open_section": section.key, **extra}
    if getattr(request, "htmx", False):
        entries = getattr(device, section.key).all()
        return render(
            request, section.template, {"device": device, section.key: entries, **context}
        )
    return render(
        request, "services/device_detail.html", {**_detail_context(request, device), **context}
    )


def _section_object(device: Device, section: Section, object_pk):
    """Datensatz eines Abschnitts — nur am eigenen Gerät, sonst 404."""
    return get_object_or_404(getattr(device, section.key), pk=object_pk)


def _section_form(request, pk, section: Section, object_pk=None):
    """Anlegen und Bearbeiten sind derselbe Ablauf; es gibt nur einmal ein Objekt."""
    device = _device(request, pk)
    instance = _section_object(device, section, object_pk) if object_pk else None
    form = section.form_class(request.POST or None, request.FILES or None, instance=instance)

    if request.method == "POST" and form.is_valid():
        entry = form.save(commit=False)
        entry.device = device
        entry.save()
        if not getattr(request, "htmx", False):
            messages.success(request, "Gespeichert." if instance else "Angelegt.")
        return _render_section(request, device, section)

    action = (
        reverse(section.update_url_name, args=[device.pk, object_pk])
        if object_pk
        else reverse(section.create_url_name, args=[device.pk])
    )
    return _render_section(
        request,
        device,
        section,
        section_form=form,
        section_action=action,
        section_title=section.update_title if object_pk else section.create_title,
        section_submit="Speichern",
        section_multipart=section.multipart,
    )


def _section_delete(request, pk, section: Section, object_pk):
    """Ein Schritt vor dem Löschen: ``GET`` fragt, ``POST`` führt aus."""
    device = _device(request, pk)
    entry = _section_object(device, section, object_pk)

    if request.method == "POST":
        entry.delete()
        if not getattr(request, "htmx", False):
            messages.success(request, "Gelöscht.")
        return _render_section(request, device, section)

    return _render_section(
        request,
        device,
        section,
        section_action=reverse(section.delete_url_name, args=[device.pk, object_pk]),
        section_title=section.delete_title,
        section_question=section.delete_question,
        section_subject=str(entry),
        section_submit="Löschen",
    )


@login_required
def device_section(request, pk, section):
    """Abschnitt frisch ausliefern — das Ziel jedes „Abbrechen"."""
    return _render_section(request, _device(request, pk), SECTIONS[section])


# --------------------------------------------------------------------------
# Titelbild
# --------------------------------------------------------------------------
#
# Ein eigener Abschnitt, obwohl er aussieht wie die anderen: hinter Technik,
# Dokumenten und Links steht je eine Sammlung am Gerät, hinter dem Titelbild
# ein Feld des Geräts selbst. Die Formulare der Abschnitte legen einen
# Datensatz an — hier gibt es keinen.


def _render_cover(request, device: Device, **extra):
    """Wie :func:`_render_section`, nur für das Titelbild.

    Mit HTMX kommt der Abschnitt zurück, ohne HTMX die ganze Detailseite mit
    dem Formular an seinem Platz — ohne JavaScript soll das Titelbild ebenso
    zu wechseln sein.
    """
    context = {"open_section": "cover", **extra}
    if getattr(request, "htmx", False):
        return render(request, "services/_device_cover.html", {"device": device, **context})
    return render(
        request, "services/device_detail.html", {**_detail_context(request, device), **context}
    )


@login_required
def device_cover(request, pk):
    """Titelbild-Abschnitt frisch ausliefern — das Ziel jedes „Abbrechen"."""
    return _render_cover(request, _device(request, pk))


@login_required
@require_http_methods(["GET", "POST"])
def device_cover_edit(request, pk):
    """Titelbild hochladen oder ersetzen."""
    device = _device(request, pk)
    form = DeviceCoverForm(request.POST or None, request.FILES or None)

    if request.method == "POST" and form.is_valid():
        device.set_cover(form.cleaned_data["cover_image"])
        if not getattr(request, "htmx", False):
            messages.success(request, "Titelbild gespeichert.")
        return _render_cover(request, device)

    return _render_cover(
        request,
        device,
        section_form=form,
        section_action=reverse("services:device_cover_edit", args=[device.pk]),
        section_title="Titelbild ersetzen" if device.has_cover else "Titelbild hochladen",
        section_submit="Speichern",
        section_multipart=True,
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_cover_delete(request, pk):
    """Ein Schritt vor dem Entfernen: ``GET`` fragt, ``POST`` führt aus."""
    device = _device(request, pk)

    if request.method == "POST":
        device.clear_cover()
        if not getattr(request, "htmx", False):
            messages.success(request, "Titelbild entfernt.")
        return _render_cover(request, device)

    return _render_cover(
        request,
        device,
        section_action=reverse("services:device_cover_delete", args=[device.pk]),
        section_title="Titelbild entfernen",
        section_question="Soll das Titelbild samt seiner Varianten gelöscht werden?",
        section_subject=device.name,
        section_submit="Entfernen",
    )


@login_required
@require_http_methods(["GET", "POST"])
def device_spec_create(request, pk):
    return _section_form(request, pk, SECTIONS["specs"])


@login_required
@require_http_methods(["GET", "POST"])
def device_spec_update(request, pk, spec_pk):
    return _section_form(request, pk, SECTIONS["specs"], spec_pk)


@login_required
@require_http_methods(["GET", "POST"])
def device_spec_delete(request, pk, spec_pk):
    return _section_delete(request, pk, SECTIONS["specs"], spec_pk)


@login_required
@require_http_methods(["GET", "POST"])
def device_document_create(request, pk):
    return _section_form(request, pk, SECTIONS["documents"])


@login_required
@require_http_methods(["GET", "POST"])
def device_document_update(request, pk, document_pk):
    return _section_form(request, pk, SECTIONS["documents"], document_pk)


@login_required
@require_http_methods(["GET", "POST"])
def device_document_delete(request, pk, document_pk):
    return _section_delete(request, pk, SECTIONS["documents"], document_pk)


@login_required
@require_http_methods(["GET", "POST"])
def device_link_create(request, pk):
    return _section_form(request, pk, SECTIONS["links"])


@login_required
@require_http_methods(["GET", "POST"])
def device_link_update(request, pk, link_pk):
    return _section_form(request, pk, SECTIONS["links"], link_pk)


@login_required
@require_http_methods(["GET", "POST"])
def device_link_delete(request, pk, link_pk):
    return _section_delete(request, pk, SECTIONS["links"], link_pk)


@login_required
def device_document(request, pk, document_pk):
    """Ein Gerätedokument ausliefern — und vorher prüfen, wem es gehört.

    Der einzige Weg zu diesen Dateien. Sie liegen außerhalb von ``MEDIA_ROOT``
    und werden von keinem Webserver direkt ausgeliefert; auf einer Rechnung
    stehen Name, Anschrift und Zahlungsdaten, und eine schwer zu erratende
    Adresse ist dafür kein Schutz, sondern nur eine Hoffnung.

    Steht nginx davor, übernimmt der das Ausliefern über ``X-Accel-Redirect``:
    die Prüfung bleibt hier, die Bytes gehen an dem Python-Prozess vorbei.
    """
    device = _device(request, pk)
    document = get_object_or_404(device.documents, pk=document_pk)
    content_type = mimetypes.guess_type(document.filename)[0] or "application/octet-stream"
    # Bilder und PDF im Browser zeigen, alles andere herunterladen. Ausführbares
    # kommt hier nicht an — die Endungsprüfung lässt es gar nicht erst hinein.
    inline = content_type.startswith("image/") or content_type == "application/pdf"
    disposition = "inline" if inline else "attachment"

    accel = getattr(settings, "PRIVATE_MEDIA_ACCEL_LOCATION", "")
    if accel:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = f"{accel.rstrip('/')}/{document.file.name}"
    else:
        try:
            response = FileResponse(document.file.open("rb"), content_type=content_type)
        except (FileNotFoundError, OSError) as exc:
            # Datensatz ohne Datei: eine Fehlerseite ist die ehrlichere Antwort
            # als ein Serverfehler — abrufbar ist hier gerade nichts.
            raise Http404("Die Datei ist nicht mehr vorhanden") from exc
    response["Content-Disposition"] = f'{disposition}; filename="{document.filename}"'
    # Ein falsch geratener Typ soll nicht dazu führen, dass der Browser eine
    # hochgeladene Datei als etwas anderes behandelt, als sie ist.
    response["X-Content-Type-Options"] = "nosniff"
    return response


# --------------------------------------------------------------------------
# Shelly
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def shelly_add(request):
    """Steckdose über ihre Adresse anbinden.

    Vor dem Speichern wird ``/shelly`` gelesen: das bestätigt, dass unter der
    Adresse tatsächlich ein Shelly antwortet, und liefert die Generation, ohne
    die der Client das falsche Anmeldeverfahren wählen würde. Antwortet das
    Gerät nicht, wird nichts angelegt — ein Karteileichen-Gerät mit falscher
    Adresse hilft niemandem.
    """
    form = ShellyDeviceForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        device = form.save(commit=False)
        device.owner = request.user
        client = ShellyClient(
            device.host, form.cleaned_data.get("username", ""), device.api_password
        )
        try:
            info = ShellyService(client=client).identify()
        except ShellyError as exc:
            form.add_error("host", str(exc))
        else:
            device.generation = info.generation
            device.firmware = info.firmware[:40]
            device.name = device.name or info.label
            device.save()
            messages.success(
                request,
                f"{device.name} angebunden ({info.generation_label}"
                f"{', ' + info.model if info.model else ''}).",
            )
            return redirect("services:device_detail", pk=device.pk)

    return render(request, "services/shelly_form.html", {"form": form})


@login_required
def energy_overview(request):
    """Stromverbrauch je Becken und je Gerät im Vergleich.

    Der eigentliche Nutzen der Anbindung: was kostet welches Becken im Monat.
    Gerechnet wird ausschließlich aus gespeicherten Messwerten und aus den am
    Gerät hinterlegten Nennleistungen — die Seite fasst kein Gerät an und ist
    damit auch dann vollständig, wenn gerade keins antwortet. Was gemessen und
    was hochgerechnet ist, steht an jeder Zeile.
    """
    period = energy.normalize_period(request.GET.get("zeitraum"))
    tanks = energy.usage_by_tank(request.user, period)
    devices = energy.usage_by_device(request.user, period)
    return render(
        request,
        "services/energy_overview.html",
        {
            "period": period,
            "period_label": energy.period_label(period),
            "periods": energy.PERIODS,
            "tanks": tanks,
            "devices": devices,
            "chart": bar_chart(
                [(usage.label, usage.kwh) for usage in tanks],
                description="Stromverbrauch je Becken in Kilowattstunden",
            ),
            "total_kwh": energy.total_kwh(tanks),
            "total_estimated_kwh": energy.total_estimated_kwh(tanks),
            "total_cost": energy.total_cost(tanks),
            "price_per_kwh": energy.price_per_kwh(),
            "has_devices": energy.accounted_devices(request.user).exists(),
        },
    )


# --------------------------------------------------------------------------
# KI-Assistenz
# --------------------------------------------------------------------------


def _ai_view(view):
    """Blendet eine KI-Seite aus, solange kein API-Key hinterlegt ist.

    Kein Key heißt: die Seite existiert nicht. Das ist ehrlicher als ein
    Formular, das erst nach dem Absenden mitteilt, dass es nicht geht — und
    zusammen mit dem ausgeblendeten Menüpunkt sieht ein Benutzer ohne
    KI-Konfiguration nichts davon.
    """

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not ai.ai_enabled():
            raise Http404("Die KI-Assistenz ist nicht eingerichtet")
        return view(request, *args, **kwargs)

    return login_required(wrapper)


def _suggestion(request, pk) -> AISuggestion:
    """Vorschlag des angemeldeten Benutzers — fremde gibt es nicht."""
    return get_object_or_404(AISuggestion, pk=pk, user=request.user)


@_ai_view
@require_http_methods(["GET", "POST"])
def ai_identify(request):
    """Art aus einem Foto bestimmen.

    Das Ergebnis ist bewusst nichts als eine Liste von Vorschlägen: erst der
    Klick auf „Als Entwurf übernehmen“ legt etwas an, und auch das ist noch
    kein Katalogeintrag.
    """
    form = IdentifyForm(request.POST or None, request.FILES or None)
    found = None

    if request.method == "POST" and form.is_valid():
        # Auch der Fehlerfall gehört in die Ergebnisspalte und nicht in eine
        # Meldung über der Seite: dort steht er neben dem Formular, mit dem
        # man es gleich noch einmal versuchen kann.
        found = ai.identify(
            form.cleaned_data["photo"],
            form.cleaned_data["kind"],
            user=request.user,
            notes=form.cleaned_data.get("notes", ""),
        )

    return render(
        request,
        "services/ai_identify.html",
        {
            "form": form,
            "found": found,
            "kind": form.data.get("kind", ""),
            "budget": ai.budget_status(request.user),
            "open_drafts": AISuggestion.objects.filter(
                user=request.user, status=AISuggestion.Status.DRAFT
            ).count(),
        },
    )


@_ai_view
@require_http_methods(["POST"])
def ai_suggestion_create(request):
    """Übernimmt einen Kandidaten der Bestimmung als Entwurf."""
    form = CandidateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Der Vorschlag konnte nicht übernommen werden.")
        return redirect("services:ai_identify")

    suggestion = ai.save_suggestion(
        request.user,
        form.cleaned_data["kind"],
        ai.Candidate(
            scientific_name=form.cleaned_data.get("scientific_name", ""),
            common_name=form.cleaned_data.get("common_name", ""),
            confidence=form.cleaned_data.get("confidence"),
            reasoning=form.cleaned_data.get("reasoning", ""),
        ),
    )
    messages.success(request, f"{suggestion.label} als Entwurf übernommen.")
    return redirect("services:ai_suggestion_detail", pk=suggestion.pk)


@_ai_view
def ai_suggestion_list(request):
    """Alle eigenen Vorschläge — offene zuerst."""
    suggestions = AISuggestion.objects.filter(user=request.user)
    return render(
        request,
        "services/ai_suggestion_list.html",
        {
            "drafts": suggestions.filter(status=AISuggestion.Status.DRAFT),
            "decided": suggestions.exclude(status=AISuggestion.Status.DRAFT)[:50],
            "budget": ai.budget_status(request.user),
        },
    )


@_ai_view
def ai_suggestion_detail(request, pk):
    """Ein Vorschlag mit Steckbrief-Entwurf und den beiden Entscheidungen."""
    suggestion = _suggestion(request, pk)
    return render(
        request,
        "services/ai_suggestion_detail.html",
        {
            "suggestion": suggestion,
            "fields": _profile_rows(suggestion),
            "matches": ai.find_matches(
                suggestion.kind, suggestion.scientific_name, suggestion.common_name
            ),
        },
    )


@_ai_view
@require_http_methods(["POST"])
def ai_suggestion_profile(request, pk):
    """Steckbrief zu einem Vorschlag entwerfen lassen."""
    suggestion = _suggestion(request, pk)
    if not suggestion.is_draft:
        raise Http404("Der Vorschlag ist bereits entschieden")

    answer = ai.draft_profile(suggestion, user=request.user)
    if answer:
        messages.success(
            request,
            "Steckbrief-Entwurf erstellt. Bitte Feld für Feld prüfen — die Angaben "
            "stammen von Claude und sind noch nicht bestätigt.",
        )
    else:
        messages.error(request, answer.error)
    return redirect("services:ai_suggestion_detail", pk=suggestion.pk)


@_ai_view
@require_http_methods(["POST"])
def ai_suggestion_decide(request, pk, decision):
    """Bestätigen oder verwerfen — der einzige Weg in den Katalog."""
    suggestion = _suggestion(request, pk)
    if not suggestion.is_draft:
        raise Http404("Der Vorschlag ist bereits entschieden")
    if decision not in ("bestaetigen", "verwerfen"):
        raise Http404("Unbekannte Entscheidung")

    if decision == "verwerfen":
        ai.reject_suggestion(suggestion)
        messages.info(request, f"{suggestion.label} verworfen.")
        return redirect("services:ai_suggestion_list")

    ai.confirm_suggestion(suggestion, user=request.user)
    if suggestion.catalog_ref:
        messages.success(request, f"{suggestion.label} ist im Katalog.")
    else:
        messages.success(
            request,
            f"{suggestion.label} bestätigt. Der Katalog ist noch nicht angebunden — "
            "der Entwurf steht bereit und wandert dorthin, sobald es ihn gibt.",
        )
    return redirect("services:ai_suggestion_detail", pk=suggestion.pk)


def _profile_rows(suggestion: AISuggestion):
    """Steckbrief-Entwurf als beschriftete Zeilen für die Anzeige.

    Ein Schlüssel ohne Beschriftung wird trotzdem angezeigt — lieber ein
    technischer Feldname als eine verschluckte Angabe.
    """
    labels = PROFILE_LABELS.get(suggestion.kind, {})
    return [
        (labels.get(key, key), value)
        for key, value in (suggestion.payload or {}).items()
        if value not in (None, "", [])
    ]


# --------------------------------------------------------------------------
# MCP-Zugänge
# --------------------------------------------------------------------------


@login_required
@require_http_methods(["GET", "POST"])
def mcp_token_list(request):
    """Eigene MCP-Zugänge ansehen und anlegen.

    Nach dem Anlegen wird die Seite unmittelbar gerendert statt umgeleitet:
    der Klartext des Tokens existiert genau in dieser einen Antwort. Er landet
    weder in der Session noch in einer Meldung, die ein zweiter Aufruf wieder
    hervorholen könnte — auch nicht in der Datenbank.
    """
    form = MCPTokenForm(request.POST or None)
    issued_key = ""
    issued_token = None
    if request.method == "POST" and form.is_valid():
        issued_token, issued_key = form.issue(request.user)
        messages.success(request, f"Zugang „{issued_token.name}“ angelegt.")
        form = MCPTokenForm()

    # Nicht SITE_URL: der MCP-Server ist ein eigener Dienst hinter einer
    # eigenen Adresse.
    endpoint_url = f"{settings.MCP_PUBLIC_URL.rstrip('/')}/mcp/"
    return render(
        request,
        "services/mcp_token_list.html",
        {
            "form": form,
            "tokens": request.user.mcp_tokens.all(),
            "issued_key": issued_key,
            "issued_token": issued_token,
            "endpoint_url": endpoint_url,
            # Die fertige Adresse: mehr braucht ein Client nicht, und genau
            # deshalb ist sie so schutzbedürftig wie ein Passwort.
            "client_url": f"{endpoint_url}?{TOKEN_PARAM}={issued_key}" if issued_key else "",
        },
    )


@login_required
@require_http_methods(["POST"])
def mcp_token_revoke(request, pk):
    """Entzieht einen eigenen Zugang.

    Nur widerrufen, nicht löschen: das Protokoll der Schreibzugriffe soll auch
    danach noch sagen können, welcher Zugang einen Datensatz angelegt hat.
    """
    token = get_object_or_404(MCPToken, pk=pk, user=request.user)
    token.revoke()
    messages.info(request, f"Zugang „{token.name}“ widerrufen.")
    return redirect("services:mcp_token_list")
