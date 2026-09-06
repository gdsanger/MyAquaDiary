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
| `services` | Anbindung Graph-API, Eheim, Shelly, KI; Verbrauchsauswertung |

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

## Shelly-Steckdosen

Angebunden über die **lokale HTTP-API** der Geräte
([Doku](https://shelly-api-docs.shelly.cloud/)) — nicht über die Shelly Cloud:
ohne Cloud-Konto, ohne Ratelimit und ohne Abhängigkeit von einem Fremddienst.
Unterstützt werden beide Generationen:

| | Gen1 (Plug S) | Gen2+ (Plus Plug S) |
|---|---|---|
| Status | `GET /status` | `GET /rpc/Switch.GetStatus?id=0` |
| Schalten | `GET /relay/0?turn=on` | `GET /rpc/Switch.Set?id=0&on=true` |
| Anmeldung | Basic Auth | Digest Auth, Benutzer `admin` |

Welche Generation vorliegt, erkennt die Anwendung über `/shelly` — den einzigen
Endpunkt, den beide Generationen beantworten. Das Ergebnis steht danach am
Gerät; der Umweg fällt also nur beim ersten Kontakt an. Oberhalb von
`services/shelly/` kommt die Unterscheidung nicht mehr vor.

Einrichten unter *Geräte → Steckdose anbinden*: Adresse eingeben, optional
Benutzer und Passwort (im Auslieferungszustand ist die lokale API offen).
Angelegt wird nur, was auf `/shelly` geantwortet hat. Das Feld *Becken* ist die
Grundlage der Verbrauchsauswertung — solange das Becken-Modell nicht im Epic
liegt, ist es ein Freitext am Gerät.

Gelesen werden Schaltzustand, Leistung, Zählerstand und Gerätetemperatur; der
Statusabruf läuft wie bei Eheim per HTMX und mit kurzem Timeout
(`SHELLY_TIMEOUT`, Default 5 s). Erfasst wird periodisch mit demselben Command
wie bei Eheim:

```bash
docker compose exec web python manage.py poll_devices
```

**Schalten** ist möglich, aber mit Bedacht: jede Aktion wird vor dem Senden zur
Bestätigung angezeigt und anschließend protokolliert — wer das Licht um 3 Uhr
nachts an hatte, lässt sich später nachvollziehen. **Zeitpläne und Automatik
gibt es bewusst nicht**; das kann der Shelly selbst, und ein zweiter Zeitplan an
derselben Steckdose wäre eine Fehlerquelle ohne Gegenwert. Firmware-Update,
Neustart und Werksreset sperrt der Client (`BLOCKED_PATHS`).

### Stromverbrauch

Die Steckdose meldet einen **Zählerstand**, keinen Verbrauch je Zeitraum — der
entsteht in `services/energy.py` als Differenz zweier Messwerte. Gen1 zählt
dabei in Wattminuten (`/60` in `services/shelly/convert.py`), Gen2 in
Wattstunden; gespeichert wird einheitlich in Wattstunden. Ein fallender
Zählerstand (Gen1 nach Stromausfall) gilt als Verbrauch seit dem Reset.

Unter *Stromverbrauch* stehen Tag, Monat und Jahr je Becken im Vergleich, dazu
die Kosten mit dem Arbeitspreis aus `ENERGY_PRICE_PER_KWH` (Default 0,35 €/kWh).
Gespeichert werden Kilowattstunden, keine Beträge. Die Seite rechnet
ausschließlich aus gespeicherten Messwerten und fragt kein Gerät ab — sie ist
damit auch dann vollständig, wenn gerade keine Steckdose antwortet.

Aus dem Code:

```python
from services import devices, energy
from services.shelly import service_for

devices.probe(device)                                   # lesen + ablegen
devices.execute(device, "on", user=request.user)        # schalten + protokollieren
service_for(device).read_status()                       # nur lesen, ohne Persistenz
energy.usage_by_tank(user, energy.PERIOD_MONTH)         # Vergleich je Becken
```

## KI-Assistenz (Anthropic Claude)

Angebunden über die [Messages-API](https://platform.claude.com/docs) mit dem
offiziellen `anthropic`-SDK. Die Assistenz ist **Assistenz und kein Automat**:
jeder Aufruf geht von einer Handlung des Benutzers aus, jedes Ergebnis ist ein
Vorschlag, und nichts davon schaltet ein Gerät oder legt einen Termin an.

Konfiguriert wird im Admin unter *Services → KI-Konfiguration*; der API-Key
liegt verschlüsselt in der Datenbank. Alternativ lassen sich Key und Modell
über `ANTHROPIC_*` aus dem Environment vorgeben (siehe `.env.example`) — ein im
Admin gepflegter Datensatz hat Vorrang. **Ohne Key läuft die Anwendung normal
weiter**: Menüpunkt und KI-Seiten existieren dann schlicht nicht.

Zugang prüfen:

```bash
docker compose exec web python manage.py ai_test
```

### Anwendungsfälle

| Aktion | Wo | Ergebnis |
|---|---|---|
| Pflanze/Tier bestimmen | *KI → Art bestimmen* | Kandidaten mit Konfidenz und Katalogtreffern |
| Steckbrief entwerfen | Vorschlagsseite | vorbefüllte Felder, die geprüft werden müssen |
| Messwerte deuten | `ai.read_measurements` | Einordnung des Verlaufs als Markdown |
| Besatz prüfen | `ai.check_stocking` | Befunde zu Beckengröße, Gruppen, Verträglichkeit |
| Beckenbericht | `ai.tank_report` | Zusammenfassung eines Zeitraums als Markdown |

Die drei Auswertungen nehmen einfache Datenstrukturen entgegen
(`TankFacts`, `StockItem`, `ReportPeriod`) statt Modellinstanzen — Becken,
Messreihen und Besatz liegen in einem anderen Schritt des Epics. Ihre
Oberfläche entsteht mit den Beckenseiten; die Service-Schicht steht.

```python
from services import ai

found = ai.identify(request.FILES["photo"], "animal", user=request.user)
suggestion = ai.save_suggestion(request.user, "animal", found.candidates[0])
ai.draft_profile(suggestion)          # füllt suggestion.payload
ai.confirm_suggestion(suggestion)     # der einzige Weg in den Katalog

ai.read_measurements(ai.TankFacts(name="Becken 1", volume_liters=180), messwerte)
ai.check_stocking(becken, [ai.StockItem("Neonsalmler", count=4)])
```

Alle Aufrufe werfen keine Ausnahme: Fehler landen im Log und unter *Services →
KI-Verbrauch*, ein Aufrufer bricht daran nicht ab.

### Jeder Vorschlag ist ein Vorschlag

Eine Bestimmung und ein Steckbrief entstehen als `AISuggestion` mit
`verified=False`. Erst eine ausdrückliche Bestätigung durch den Benutzer legt
daraus einen Katalogeintrag an — ein unbestätigt übernommener Steckbrief würde
Fehler über alle Nutzer verbreiten. Verworfene Vorschläge bleiben als Protokoll
stehen.

Der Katalog wird über `apps.get_model` aufgelöst statt importiert (siehe
`services/ai/catalog.py`): solange `catalog.CatalogAnimal` und
`catalog.CatalogPlant` im Epic fehlen, bleibt der Abgleich leer und eine
Bestätigung merkt sich den Entwurf, statt zu scheitern.

Bei Messwerten ist Zurückhaltung eingebaut: der System-Prompt verbietet die
Diagnose ausdrücklich, es wird eingeordnet und auf Auffälligkeiten hingewiesen.
Die Verantwortung für die Tiere bleibt beim Halter. Jede Antwort ist in der
Oberfläche als KI-Vorschlag gekennzeichnet.

### Kostenkontrolle

Bilderkennung ist der teure Teil — ein Foto verbraucht ein Vielfaches der Token
eines Textprompts. Drei Stellen begrenzen das:

- **Bilder** werden vor dem Versand auf `AI_IMAGE_MAX_EDGE` (Default 1024 px)
  heruntergerechnet und als JPEG neu kodiert. Nebeneffekt: die EXIF-Daten
  fallen weg, ein Aufnahmeort verlässt das Haus also nicht. Gespeichert wird
  das Foto nicht.
- **Budgets** greifen vor dem ersten Byte an die API: `monthly_token_budget`
  über alle Benutzer und `per_user_daily_limit` je Benutzer und Tag (jeweils
  `0` = ohne Grenze). Ist eine Grenze erreicht, wird der Aufruf abgelehnt und
  dem Benutzer erklärt, warum und ab wann es weitergeht.
- **Verbrauch** steht je Aufruf in `AIUsageLog`: Token, Kosten in USD, Dauer,
  Erfolg. Auch ein am Budget gescheiterter Versuch wird protokolliert.

Der API-Key erscheint in keinem Log und in keiner Meldung — `AIService` filtert
ihn aus jedem Fehlertext, bevor er irgendwohin geht.

Das Modell steht in der Konfiguration (Default `claude-opus-5`) und lässt sich
im Admin umstellen, ohne dass eine Migration nötig wird. Modelle ohne
Structured Outputs werden erkannt; deren Antworten werden nachsichtig geparst,
statt die Anfrage mit einem Fehler zu quittieren.
