# MyAquaDiary

Aquarien-Tagebuch für mehrere Benutzer mit je mehreren Becken (Messreihen,
Ereignisse, Besatz, Bepflanzung, Fotos, wiederkehrende Termine).

## Stack

Django 5 + django-htmx, Bootstrap 5.3 (Light Mode only), PostgreSQL, Docker.

## Lokale Entwicklung

```bash
cp .env.example .env   # anpassen (Secret Key, DB-Zugangsdaten)
docker compose up
```

App unter http://localhost:8000. Migrationen laufen beim Start automatisch.

Superuser anlegen:

```bash
docker compose exec web python manage.py createsuperuser
```

## Projektstruktur

| App | Zweck |
|---|---|
| `config` | Settings (`base`/`development`/`production`), URL-Routing |
| `core` | Custom User-Model, Mixins, Utils |
| `catalog` | Pflanzen- und Tier-Katalog (userübergreifend) |
| `tanks` | Becken, Messreihen, Ereignisse, Termine, Besatz, Bepflanzung, Fotos |
| `dashboard` | KPIs, fällige Termine |
| `services` | Anbindung Graph-API, Eheim, Shelly, KI |

## Mailversand (Microsoft Graph)

Ausgehende Mail läuft über die Microsoft Graph API, authentifiziert per
Client-Credentials-Flow. Nötig ist eine App-Registrierung mit der
**Application**-Permission `Mail.Send` (Admin-Consent erteilt) und ein
Postfach, aus dem gesendet wird. Eingehende Mail ist nicht vorgesehen.

Konfiguriert wird im Admin unter *Services → Mail-Konfiguration*; das
Client-Secret liegt verschlüsselt in der Datenbank. Alternativ lassen sich die
Werte über `GRAPH_*` aus dem Environment vorgeben (siehe `.env.example`) — ein
im Admin gepflegter Datensatz hat Vorrang. Ohne Konfiguration läuft die
Anwendung normal weiter, es werden lediglich keine Mails verschickt.

Versenden aus dem Code:

```python
from services.graph import GraphMailService, send_template_mail

GraphMailService().send("max@example.com", "Betreff", "<p>HTML</p>", "Plaintext")
send_template_mail(user.email, "appointment_reminder", {"appointments": [...]})
```

Beide Aufrufe werfen keine Ausnahme: Fehler landen im Log und unter *Services →
Mail-Protokoll*, ein Cron-Command bricht daran nicht ab.

Testmail — im Admin über den Button *Testmail senden* oder per Command:

```bash
docker compose exec web python manage.py send_test_mail max@example.com
docker compose exec web python manage.py send_test_mail max@example.com --template measurement_alert
```

Die Templates liegen unter `templates/mail/` — je Mail eine `.subject.txt`,
eine `.html` und eine `.txt`; beide Body-Varianten gehen immer gemeinsam raus.
Das HTML nutzt bewusst Table-Layout mit Inline-Styles und einen fest hellen
Hintergrund, weil Outlook weder Flexbox noch Grid rendert und die Clients Dark
Mode sehr unterschiedlich behandeln.

## Eheim-Digital-Geräte

Angebunden über die offizielle REST-API der Geräte
([Doku](https://api.eheimdigital.com/docs/eheim_digital_api/eheim-digital-api),
AGPLv3). Voraussetzung ist **Gerätesoftware 2.0.1 oder neuer** — ältere Geräte
haben die API nicht; die Anwendung erkennt das und sagt es beim Anlegen.

Einrichten unter *Geräte → Geräte suchen*: Adresse eines erreichbaren Geräts
plus Zugangsdaten eingeben (werksseitig `api` / `admin`). Das Gerät beantwortet
`/mesh-liste` für das ganze Mesh; aus der Trefferliste werden die gewünschten
Geräte übernommen. **Danach über *Zugangsdaten ändern* ein eigenes Passwort
setzen** — ein unverändertes Werkspasswort im LAN ist kein guter Zustand. Die
Zugangsdaten liegen verschlüsselt in der Datenbank und werden nie ausgegeben.

Der Gerätestatus wird nie beim Seitenaufbau geholt, sondern per HTMX
nachgeladen; ein nicht erreichbares Gerät kostet damit nur einen Platzhalter.
Das Timeout steht über `EHEIM_TIMEOUT` (Default 5 s).

Periodisch erfassen — je Lauf ein `DeviceReading` pro Gerät, inklusive
unveränderter Rohantwort:

```bash
docker compose exec web python manage.py poll_devices
docker compose exec web python manage.py poll_devices --device 3
```

Meldet die letzte Messung Fehlercode 1 (Rotor blockiert) oder 2 (Luft im
Filter), erscheint das im Klartext als Warnung auf dem Dashboard.

Schreibende Befehle (ein/aus, Bio, Pulse, Manuell) zeigen vor dem Senden, was
genau ans Gerät geht, und werden anschließend protokolliert.
**`/doupdate` wird nicht angeboten** — ein Firmware-Update aus einem Tagebuch
heraus anzustoßen ist Risiko ohne Nutzen; der Client sperrt den Pfad zusätzlich.

Aus dem Code:

```python
from services import devices
from services.eheim import service_for

devices.probe(device)                      # lesen + als DeviceReading ablegen
devices.execute(device, "manual", {"speed_percent": 70}, user=request.user)
service_for(device).read_status()          # nur lesen, ohne Persistenz
```

Die drei Eigenheiten der API — Minuten seit Mitternacht (15:00 Uhr = `900`),
Zehntelgrade mit separater Einheit `mUnit` (23,5 °C = `235`) und `1`/`0` statt
`true`/`false` — stecken ausschließlich in `services/eheim/convert.py`. In
Views und Templates kommen sie nicht vor.

`services/eheim/` ist reine HTTP-Kommunikation: eine Basisklasse mit den
allgemeinen Endpunkten, je Gerätetyp eine Ableitung (v1: classicVARIO+e).
Weitere Typen wie pHcontrol+e oder thermocontrol+e brauchen nur eine weitere
Ableitung und einen Eintrag in `Device.Kind`.
