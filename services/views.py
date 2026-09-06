"""Geräteverwaltung: suchen, anlegen, Status ansehen, steuern.

Der Status wird nie beim Aufbau einer Seite geholt, sondern per HTMX
nachgeladen (:func:`device_status`). Ein Gerät, das nicht antwortet, kostet
damit nur einen Platzhalter im Layout und blockiert keine Seite.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import devices as device_service
from .charts import rpm_chart
from .eheim import (
    FIRMWARE_HINT,
    EheimClient,
    EheimError,
    EheimService,
    is_supported_firmware,
    normalize_mac,
)
from .forms import CONTROL_FORMS, DeviceDiscoveryForm, DeviceForm, DevicePasswordForm
from .models import Device

#: So viele Messwerte gehen in das Verlaufsdiagramm.
CHART_READINGS = 200
#: So viele Protokollzeilen zeigt die Detailseite.
EVENT_ROWS = 20


def _device(request, pk) -> Device:
    """Gerät des angemeldeten Benutzers — fremde Geräte gibt es nicht."""
    return get_object_or_404(Device, pk=pk, owner=request.user)


@login_required
def device_list(request):
    """Übersicht der eigenen Geräte; der Status kommt je Karte per HTMX nach."""
    return render(
        request,
        "services/device_list.html",
        {"devices": request.user.devices.all(), "warnings": device_service.warnings_for(request.user)},
    )


@login_required
def device_detail(request, pk):
    """Detailseite mit Verlauf, Steuerung und Protokoll."""
    device = _device(request, pk)
    readings = list(device.readings.all()[:CHART_READINGS])
    controls = []
    if device.kind == Device.Kind.EHEIM_CLASSICVARIO:
        controls = [(action, label) for action, (_form, label) in CONTROL_FORMS.items()]
    return render(
        request,
        "services/device_detail.html",
        {
            "device": device,
            "chart": rpm_chart(readings),
            "readings": readings[:20],
            "events": device.events.all()[:EVENT_ROWS],
            "controls": controls,
            "firmware_hint": FIRMWARE_HINT if device.firmware_supported is False else "",
        },
    )


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
        {"form": form, "found": found, "error": error, "kinds": Device.Kind.choices},
    )


def _create_selected(request, credentials) -> int:
    """Legt die angehakten Geräte an; meldet jedes Ergebnis als Message."""
    created = 0
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

        device = Device(
            owner=request.user,
            name=request.POST.get(f"name_{mac}", "").strip() or mac,
            kind=request.POST.get(f"kind_{mac}") or Device.Kind.EHEIM_OTHER,
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
    """Stammdaten und Zugangsdaten eines Geräts pflegen."""
    device = _device(request, pk)
    form = DeviceForm(request.POST or None, instance=device)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Gerät gespeichert.")
        return redirect("services:device_detail", pk=device.pk)
    return render(request, "services/device_form.html", {"form": form, "device": device})


@login_required
@require_http_methods(["GET", "POST"])
def device_control(request, pk, action):
    """Schreibender Befehl — erst nach ausdrücklicher Bestätigung.

    Der erste POST zeigt, was genau ans Gerät ginge; erst der zweite mit
    ``confirm=1`` schickt es tatsächlich los. Ohne Bestätigung passiert nichts,
    auch nicht bei einem direkt abgeschickten Formular.
    """
    device = _device(request, pk)
    if action not in CONTROL_FORMS:
        raise Http404("Unbekannte Aktion")
    if device.kind != Device.Kind.EHEIM_CLASSICVARIO:
        raise Http404("Für diesen Gerätetyp gibt es keine Steuerung")

    form_class, label = CONTROL_FORMS[action]
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
                "summary": device_service.describe(action, params),
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
    """Gerätepasswort über ``/changeauth`` ändern."""
    device = _device(request, pk)
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
