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
| `services` | Geräte am Becken samt Anbindung (Eheim, Shelly), Graph-API, KI, MCP; Verbrauchsauswertung |

## Erfassen und Pflegen

Alles, was ein Becken ausmacht, wird in der Oberfläche gepflegt — das
Django-Admin ist dafür nicht nötig.

Wer was darf, entscheidet grundsätzlich die **Eigentümerschaft**, nicht eine
Django-Berechtigung: wer sein eigenes Becken pflegt, braucht kein globales
`change_tank`. Sämtliche schreibenden Ansichten der App `tanks` hängen an
`TankScopedMixin` (`tanks/views.py`), das das Becken über
`Tank.objects.for_user()` holt und jedes abhängige Objekt darüber filtert. Ein
fremder Slug oder eine fremde `pk` endet als **404**, nicht als 403 — über
fremde Daten gibt es keine Auskunft, auch keine über ihre Existenz.

Formulare und Rückfragen sind HTMX-Fragmente im Reiterbereich des Beckens:
Sie laden als Overlay über dem Reiter, und nach dem Speichern kommt der
aktualisierte Reiter zurück. Ohne JavaScript funktioniert derselbe Weg als
vollständige Seite — jede Schaltfläche ist ein Link mit `href`, jedes Formular
sendet regulär per POST.

Wo Historie dranhängt, wird nicht gelöscht:

| Datensatz | Statt Löschen |
|---|---|
| Becken mit erfassten Daten | Auflösen (`dissolved_on`), Historie bleibt lesbar |
| Besatz | Abgang buchen (`removed_on`) |
| Termin | Deaktivieren — die Quittierungen bleiben |

Löschen bleibt der Fehleingabe vorbehalten und verlangt immer einen
Zwischenschritt; es gibt keinen Link, der beim Klick löscht.

### Beobachtungen und Fotos am Ereignis

Dass die Cryptocoryne neue Blätter schiebt, ist ein **Ereignis der Kategorie
*Beobachtung*** — kein eigenes Modell. Zeitpunkt, Titel, Text und Becken sind
dieselben Felder; ein zweites Modell brächte sie nur noch einmal mit und
erweiterte die Zeitleiste in `selectors.recent_activity()` um eine Quelle, ohne
dass sich fachlich etwas unterschiede.

Was dem Ereignis wirklich fehlte, sind die Bilder. `TankPhoto.event` ist ein
optionaler Fremdschlüssel mit `SET_NULL`: ein Foto gehört dem **Becken**, die
Zuordnung zum Ereignis ist eine Angabe darüber. Wer ein Ereignis löscht,
verliert deshalb nur die Zuordnung, nicht das Foto — es steht weiter in der
Galerie.

Erfasst wird beides in einem Vorgang: unter *Ereignisse → Beobachtung erfassen*
nimmt dasselbe Formular Text **und** Bilder entgegen (Mehrfachauswahl,
`accept="image/*"`, damit das Telefon Kamera und Galerie anbietet). Ein
`capture`-Attribut steht bewusst nicht dabei — es erzwingt die Kamera und
schließt die Mehrfachauswahl aus.

Der Zeitpunkt darf leer bleiben: `taken_on` je Foto kommt aus dem EXIF-Block
(`core/images.taken_at`), und der Zeitpunkt des Ereignisses ist der früheste
davon. Eine falsch gestellte Kamerauhr fällt dabei durch — was in der Zukunft
läge, gilt als unbrauchbar und wird zu „jetzt“. Ein bereits hochgeladenes Foto
findet über *Galerie → Bearbeiten* nachträglich zu seinem Ereignis; zur Auswahl
stehen nur Ereignisse desselben Beckens.

In der Zeitleiste bleibt ein Ereignis mit Fotos **ein** Eintrag mit
Bildvorschau. Sonst schöbe eine Beobachtung mit fünf Bildern alles andere aus
der Dashboard-Kachel. Einzeln stehen dort nur Fotos ohne Ereignis.

### Katalogpflege

Der Katalog ist die Ausnahme: er ist userübergreifend, ein Steckbrief gehört
niemandem. Hier entscheidet deshalb ein echtes Recht —
`catalog.can_edit_catalog` (Trägermodell `catalog.CatalogPermission`, ohne
eigene Tabelle). Staff darf immer. Ohne das Recht erscheinen die
Pflege-Schaltflächen nicht, und die zugehörigen Adressen antworten mit 403.

Vergeben wird es im Admin unter *Benutzer → Berechtigungen*
(„Darf den Katalog pflegen“) oder über eine Gruppe.

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

## Geräte

Ein Gerät ist ein Datensatz: `services.Device`, mit **Pflicht-Fremdschlüssel
auf das Becken**. Ob eine Anbindung dahintersteckt, entscheidet die Art:

| Art | Anbindung | Status |
|---|---|---|
| Eheim classicVARIO+e, Eheim (sonstiges), Shelly Plug | REST- bzw. lokale HTTP-API | aus dem letzten `DeviceReading` fortgeschrieben |
| Filter, Heizer, Beleuchtung, CO₂, Pumpe, Dosierpumpe, Sensor, Steckdose, Sonstiges | keine | Handeingabe |

**Nicht jedes Gerät muss anbindbar sein.** Ein CO₂-Nachtabschalter ohne
Netzanschluss wird unter *Geräte → Gerät erfassen* angelegt und dokumentiert:
Hersteller, Modell, Inbetriebnahme, Wartungsintervall. Status, Verlauf,
Steuerung und Zugangsdaten erscheinen nur bei anbindbaren Arten — es gibt dort
schlicht nichts abzufragen.

Der Status ist **ein** Feld mit zwei Quellen. Bei angebundenen Geräten schreibt
ihn jede Abfrage fort (Eheim-Fehlercode 1 oder 2 → *kritisch*, mit dem Fehler
im Klartext als Meldung), sonst bleibt er eine Handeingabe. Auswertende Stellen
— `tanks/selectors.warnings()`, Beckenreiter, Geräteliste — lesen deshalb nur
diesen einen Weg. Gerätefehler und fällige Wartung erscheinen darüber als
**Beckenwarnung** auf dem Dashboard.

Jede Schaltaktion wird zweimal protokolliert: als `DeviceEvent` am Gerät und
als `tanks.Event` der Kategorie *Technik* am Becken — geschrieben in
`services.devices.record_event`, der einzigen Stelle, an der das Protokoll
entsteht. Wer eine Woche später eine Trübung sucht, sieht in der
Beckenhistorie, dass am Vorabend der Filter umgestellt wurde. Fehlgeschlagene
Versuche stehen mit dabei.

Der Beckenreiter *Geräte* und der Bereich `/geraete/` zeigen dieselben Geräte;
gepflegt werden sie im Gerätebereich, weil dort auch Anbindung und Steuerung
liegen.

## Eheim-Digital-Geräte

Angebunden über die offizielle REST-API der Geräte
([Doku](https://api.eheimdigital.com/docs/eheim_digital_api/eheim-digital-api),
AGPLv3). Voraussetzung ist **Gerätesoftware 2.0.1 oder neuer** — ältere Geräte
haben die API nicht; die Anwendung erkennt das und sagt es beim Anlegen.

Einrichten unter *Geräte → Geräte suchen*: Adresse eines erreichbaren Geräts
plus Zugangsdaten eingeben (werksseitig `api` / `admin`). Das Gerät beantwortet
`/mesh-liste` für das ganze Mesh; aus der Trefferliste werden die gewünschten
Geräte übernommen — je Gerät mit dem Becken, an dem es hängt. **Danach über *Zugangsdaten ändern* ein eigenes Passwort
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
Grundlage der Verbrauchsauswertung; zur Auswahl stehen ausschließlich die
eigenen Becken.

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
Gruppiert wird über die Beckenkennung, nicht über einen Namen: eine Umbenennung
erzeugt damit keine zweite Gruppe.
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

## MCP-Server (Zugang für KI-Clients)

Eigener Endpunkt, über den externe MCP-Clients — Claude Desktop, Claude Code,
andere MCP-fähige Anwendungen — das Tagebuch **lesen und beschreiben** können.
Er teilt sich Modelle und Service-Schicht mit der Web-App: kein paralleler
Datenzugriff, keine zweite Geschäftslogik, nur eine weitere Oberfläche auf
dieselbe Anwendung.

Der Server läuft als **eigener Dienst** neben der Web-App — in
`docker compose` als Dienst `mcp` auf Port 8001, sonst:

```bash
# Entwicklung
python manage.py run_mcp_server 0.0.0.0:8001

# Betrieb
gunicorn config.wsgi_mcp:application \
    --bind 0.0.0.0:8001 --worker-class gthread --threads 16 --timeout 0
```

Ein SSE-Strom belegt seinen Worker, solange der Client verbunden ist — daher
Threads statt zusätzlicher Prozesse und kein Worker-Timeout. **Ein Prozess ist
Absicht:** die offenen Sitzungen liegen im Arbeitsspeicher.

Der Entrypoint kennt nur `config/mcp_urls.py`. Admin, Login und
Beckenverwaltung sind über diesen Port nicht erreichbar, auch nicht
versehentlich.

### Zugänge

Tokens legt jeder Benutzer selbst unter *MCP* an: Name, wahlweise Schreibrecht
und ein Ablaufdatum. Der Klartext wird **genau einmal** angezeigt — gespeichert
ist nur sein SHA-256-Hash. Widerrufen wirkt sofort, auch mitten in einer
laufenden Sitzung; der Token bleibt danach als Eintrag stehen, damit das
Protokoll ihn weiter benennen kann.

Beispielkonfiguration für Claude Desktop
(`claude_desktop_config.json`) — der Token steht im `Authorization`-Kopf, nicht
in der Adresse:

```json
{
  "mcpServers": {
    "myaquadiary": {
      "command": "npx",
      "args": [
        "-y", "mcp-remote",
        "https://tagebuch.example.com/mcp/sse/",
        "--header", "Authorization: Bearer mad_dein-token"
      ]
    }
  }
}
```

Dieselbe Angabe steht mit der richtigen Adresse auf der Seite *MCP*, sobald ein
Token angelegt ist; welche Adresse dort erscheint, steht in `MCP_PUBLIC_URL`
(Default `http://localhost:8001`) — der MCP-Dienst hört auf einem eigenen Port
und damit nicht unter `SITE_URL`.

### Werkzeuge

| Lesend | Zweck |
|---|---|
| `list_tanks` | Becken des Nutzers mit Stammdaten |
| `get_tank` | Detail inkl. Technik, Zielbereichen, Besatz, Bepflanzung |
| `list_measurements` | Messreihen, Zeitraum- und Parameterfilter |
| `get_measurement` | Einzelne Messreihe inkl. berechnetem CO2 und Zielabgleich |
| `list_events` | Ereignisse, Kategorie- und Zeitraumfilter |
| `list_due_schedules` | Fällige und anstehende Termine |
| `search_catalog` | Pflanzen- und Tierkatalog durchsuchen |
| `get_catalog_entry` | Steckbrief |

| Schreibend | Zweck |
|---|---|
| `create_measurement` | Messreihe anlegen |
| `create_event` | Ereignis anlegen |
| `complete_schedule` | Termin quittieren (erzeugt Ereignis, rechnet fort) |
| `add_tank_animal` / `add_tank_plant` | Besatz und Bepflanzung ergänzen |
| `record_animal_movement` | Zu- oder Abgang buchen |

Ein Token ohne Schreibrecht bekommt die schreibenden Werkzeuge gar nicht erst
zu sehen — geprüft wird trotzdem beim Aufruf.

### Was es bewusst nicht gibt

- **Geräte.** Weder lesend noch schaltend, auch nicht als Beiwerk von
  `get_tank`. Eheim-Filter und Shelly-Steckdosen regeln sich autark; ihre Werte
  gehören in die Oberfläche und in die serverseitige Auswertung, nicht in ein
  externes Modell. Dass ein Gerät seit #1230 am Becken hängt, ändert daran
  nichts.
- **Löschen.** Kein `delete_*`. Was falsch angelegt wurde, wird in der
  Oberfläche korrigiert — ein irrtümlich ausgelöster Löschbefehl aus einem
  Chatfenster ist nicht zurückzuholen.
- **Katalogpflege.** Einträge, die alle Benutzer sehen, entstehen nicht über
  eine Einzelnutzer-Schnittstelle.
- **Benutzer-, Token- und Konfigurationsverwaltung.** Nichts, was Rechte oder
  Zugänge verändert.

### Eingrenzung, Kennzeichnung, Protokoll

Der Token bestimmt den Benutzer, der Benutzer bestimmt die Daten. Jede Abfrage
beginnt bei `Tank.objects.for_user(token.user)`, alles Weitere hängt daran
(`services/mcp/data.py`). Eine fremde `tank_id` ist für den Client nicht von
einer erfundenen zu unterscheiden — die Fehlermeldung ist dieselbe.

Was über MCP entsteht, trägt `source="mcp"` am Datensatz, sofern das Modell
eine Herkunft führt. Zusätzlich hält `MCPAccessLog` jeden **schreibenden**
Aufruf fest — Zeitpunkt, Token, Werkzeug, Parameter, angelegter Datensatz —
auch den abgewiesenen. Lesende Aufrufe stehen dort nicht: sie verändern nichts,
und ein Protokoll jeder Abfrage wäre eine Bewegungsdatenbank über den eigenen
Benutzer.

Je Token gilt ein Ratelimit von `MCP_RATE_LIMIT_PER_MINUTE` Aufrufen je Minute
(Default 60, `0` schaltet es ab). Gezählt wird im Cache; der Standard-Cache ist
prozesslokal, was zum Ein-Prozess-Betrieb passt. Wer den Dienst auf mehrere
Worker verteilt, hinterlegt einen gemeinsamen Cache.

Becken und Katalog werden — wie in `services/ai/catalog.py` — über
`apps.get_model` aufgelöst statt importiert. Solange die Modelle im Epic
fehlen, antwortet jedes Werkzeug mit einer verständlichen Meldung, statt den
Server beim Start scheitern zu lassen.
| `services` | Anbindung Graph-API, Eheim, Shelly, KI |

## Oberfläche

Ausschließlich Light Mode — es gibt keinen Umschalter und keinen
`prefers-color-scheme`-Zweig.

Die komplette Farbpalette steht als CSS-Variablen in
[`static/css/main.css`](static/css/main.css) und ist die einzige Stelle mit
Farbwerten. Templates verwenden ausschließlich Klassen; `core/tests.py` prüft
das maschinell mit — zusammen mit den WCAG-AA-Kontrasten.

Farbe trägt Bedeutung und wird nicht doppelt belegt:

| Farbe | Bedeutung | Klassen |
|---|---|---|
| grün | Wert im Zielbereich, Termin erledigt | `mad-status--ok`, `mad-value--ok` |
| orange | Abweichung, heute fällig | `mad-status--warn`, `mad-value--warn` |
| rot | kritisch, überfällig, Gerätefehler | `mad-status--critical`, `mad-value--critical` |
| kühle Kennfarben | Becken- und Wassertyp-Zuordnung | `mad-tank-accent-1…8`, `mad-watertype--*` |

Das Dashboard lädt jede Kachel als eigenes HTMX-Fragment
(`/kacheln/…`); das Seitengerüst selbst fragt keine Daten ab.

Erfassungsformulare liegen als Overlay (`.mad-modal`) innerhalb des
Reiterbereichs `#tab-area`: Jeder Reiterwechsel und jedes Speichern ersetzt
diesen Bereich — und räumt das Formular damit ohne eine Zeile JavaScript weg.
Die Oberfläche ist bis 375 px Breite bedienbar; Kartenköpfe, Zeilenaktionen
und das Overlay brechen dort um, statt zu scrollen.
