# Datenmodell

Diese Datei ist die **maßgebliche Beschreibung des Datenmodells**. Wer ein Item
schreibt oder Code gegen das Modell baut, nimmt diese Liste.

Die Modellskizzen aus den Items #1214–#1219 waren **Entwürfe**. Die Umsetzung
hat teils andere Namen und andere Strukturen gewählt; wer gegen die Entwürfe
programmiert, schreibt Code, der erst zur Laufzeit scheitert. Genau das ist in
der MCP-Schicht passiert (`shut_down_on`, `biotope`, #1236). Die Gegenüberstellung
Entwurfsname → tatsächlicher Name steht unten unter
[Entwurfsnamen und was daraus wurde](#entwurfsnamen-und-was-daraus-wurde).

## Wie diese Datei aktuell bleibt

Eine Referenz, die niemand prüft, wird still falsch — der Grund, aus dem es
diese Datei überhaupt gibt. `core/test_datamodel_doc.py` liest die Feldblöcke
hier heraus und vergleicht sie mit `Model._meta` der Apps `core`, `catalog`,
`tanks`, `services` und `dashboard`. Ein neues Feld, ein umbenanntes Feld, ein
neues Modell — jedes davon macht den Test rot, bis es hier steht.

Die Blöcke sind deshalb maschinenlesbar: **erstes Wort einer Zeile ist der
Feldname**, der Rest ist Erläuterung für Menschen. Zeilen, die mit `#`
beginnen, sind reine Kommentare.

Nicht geprüft werden Auswahllisten, `related_name`, Indizes und Bedingungen —
die stehen hier als Erläuterung und im Zweifel gilt das Modell.

## Gemeinsame Bausteine

Die Auswahllisten `WaterType` (`fresh` | `brackish` | `marine`), `Difficulty`
(`easy` | `medium` | `hard`) und `Status` (`ok` | `warn` | `critical` |
`unknown`) stehen in `core/enums.py` und werden von mehreren Apps geteilt.

### `CoverImageMixin` (abstrakt)

Ein Titelbild samt abgeleiteten Varianten; die Varianten entstehen beim
Speichern und sind `editable=False`. Der Ablageort kommt aus `COVER_DIR` des
erbenden Modells. Verwendet von `tanks.Tank` und `services.Device`.

```
cover_image          Titelbild — genau eins je Datensatz
cover_thumbnail      Kachel, abgeleitet
cover_preview        Vorschau, abgeleitet
cover_width          Maße des Originals
cover_height
```

`ImageVariantsMixin` (`core/images.py`) ist kein Modell, sondern eine reine
Verhaltensklasse: Die Felder `image` / `thumbnail` / `preview` / `width` /
`height` stehen bei den erbenden Modellen selbst, weil sie je Modell in einem
anderen Verzeichnis liegen.

## core

### `core.User`

Custom User-Model von Beginn an; `AUTH_USER_MODEL` zeigt darauf. Gegenüber
`AbstractUser` ist nur `email` geändert (`unique=True`), alles andere ist
geerbt.

```
password             aus AbstractUser
last_login
is_superuser
username
first_name
last_name
is_staff
is_active
date_joined
email                abweichend von AbstractUser: unique
groups
user_permissions
```

## tanks

### `tanks.Tank` — erbt `CoverImageMixin`

Ein Aquarium eines Benutzers. **Ein** Volumenfeld, kein Brutto/Netto.
`dissolved_on` leer heißt: Becken aktiv.

```
owner                FK core.User · CASCADE · related_name="tanks"
name
slug                 mit owner zusammen eindeutig
water_type           WaterType
volume_liters        Decimal(7,1) · Pflicht · das einzige Volumenfeld
length_cm            optional
width_cm             optional — die Tiefe des Beckens, nicht „depth_cm“
height_cm            optional
location             Standort im Haus, z. B. „Wohnzimmer“
setup_date           Pflicht
dissolved_on         null = aktiv; Auflösen statt Löschen, wo Historie dranhängt
accent               Farbkennung 1–8, siehe TANK_ACCENT_COUNT
notes
created_at
updated_at
```

Abgeleitet, kein Feld: `is_dissolved`, `has_history`, `accent_class`,
`dimensions`, `age`, `age_display`, `substrate_depth_cm` (Summe der
Schichtmächtigkeiten), `setup_summary`.

### `tanks.Parameter`

Eine **gemessene** Größe (pH, NO₂, …) mit globalem Vorgabebereich. Kein
Benutzerbezug: Die Liste gilt für alle. Was gerechnet wird, steht bewusst nicht
hier — siehe [Abgeleitete Größen](#abgeleitete-grossen-kein-modell).

```
key                  unique · Schlüssel, unter dem MCP und Diagramme den Parameter ansprechen
name
unit                 kann leer sein (pH)
decimals             Nachkommastellen der Anzeige
default_min          globale Vorgabe, vom Becken überschreibbar
default_max
is_key_parameter     erscheint im Verlaufsdiagramm des Dashboards
sort_order           Reihenfolge — nicht „position“
```

### `tanks.TankParameterTarget`

Beckenspezifischer Zielbereich; überschreibt die Vorgabe am Parameter. Es gibt
**keinen Zielwert**, nur einen Bereich, und beide Grenzen sind optional
(„bis 0,2 mg/l“).

```
tank                 FK Tank · CASCADE · mit parameter zusammen eindeutig
parameter            FK Parameter · CASCADE
minimum
maximum
```

### `tanks.TankDerivedTarget`

Beckenspezifischer Zielbereich einer **abgeleiteten** Größe. Angesprochen über
den Schlüssel und nicht über einen `Parameter`, denn den bekommt eine
gerechnete Größe nicht (#1240). Die Vorgabe (CO₂: 15–25 mg/l) steht nicht in
dieser Tabelle, sondern im Code — eine Zeile gibt es nur, wo jemand
überschrieben hat.

```
tank                 FK Tank · CASCADE · mit key zusammen eindeutig
key                  Schlüssel der abgeleiteten Größe, z. B. „co2“
minimum
maximum
```

### Abgeleitete Größen — kein Modell

CO₂ hat weder eine Tabelle noch einen `Parameter`-Eintrag. Es wird bei jeder
Anzeige aus Karbonathärte und pH gerechnet:

    CO₂ [mg/l] = 3 × KH [°dH] × 10^(7 − pH)

Beschrieben ist die Größe in `tanks/derived.py` als `DerivedParameter`; die
gerechneten Werte reist ein `DerivedValue` durch die Anwendung, das dieselben
Namen trägt wie ein `Measurement` (`value`, `measured_at`, `display_value`) —
bis auf die Kennung, die es nicht gibt, und `is_derived`.

Zwei Gründe für diesen Zuschnitt: Ein gespeicherter Wert bliebe stehen, wenn
KH oder pH nachträglich korrigiert werden, und sähe dabei aus wie eine
Messung. Ein `Parameter`-Eintrag wiederum stünde im Erfassungsformular und
ließe sich von Hand eintippen.

Weil das Datenmodell **einen Wert je Zeile** speichert, gibt es keinen
Datensatz, an dem KH und pH gemeinsam hängen. Gepaart wird deshalb über
zeitliche Nähe (`CO2_PAIR_WINDOW_HOURS`, Vorgabe 6 h); ohne Partner im Fenster
entsteht kein Wert.

### `tanks.Measurement`

**Ein Wert je Zeile.** Eine „Messreihe“ mit mehreren Werten gibt es nicht;
gemeinsam erfasste Werte teilen sich lediglich `measured_at`. Der Parameter
hängt mit `PROTECT` dran: Eine Messgröße, zu der Werte existieren, lässt sich
nicht wegräumen.

```
tank                 FK Tank · CASCADE
parameter            FK Parameter · PROTECT
value                Decimal(8,3) · Pflicht — siehe Lücke 1 (kein „n. n.“)
measured_at          Zeitpunkt
note                 CharField(200)
created_by           FK User · SET_NULL
```

Index: `(tank, parameter, -measured_at)`. Abgeleitet: `display_value`,
`target_range()`, `status()`.

### `tanks.Event`

Ein Vorgang am Becken. Beobachtungen sind eine **Kategorie**, kein eigenes
Modell — dieselben Felder unter anderem Namen wären keine zweite Sache.

```
tank                 FK Tank · CASCADE
category             water_change | maintenance | treatment | stocking | equipment | incident | observation | other
title
description
occurred_at
created_by           FK User · SET_NULL
```

`equipment` schreibt auch die Anwendung selbst, wenn sie ein Gerät schaltet
(`services.devices.record_event`).

### `tanks.Stocking`

Besatz: eine Tierart in einem Becken. Eine Bewegungshistorie führt das Modell
nicht — eine Änderung ist eine neue Stückzahl, ein Abgang ein `removed_on`.

```
tank                 FK Tank · CASCADE
species              FK catalog.AnimalSpecies · PROTECT
quantity
added_on
removed_on           null = im Becken
note                 CharField(200) — siehe Lücke 5 (kein Abgangsgrund)
```

Abgeleitet: `is_active`, `group_status` (Warnung bei unterschrittener
Mindestgruppengröße).

### `tanks.Planting`

Wie `Stocking`, aber mit `planted_on` statt `added_on` — beim Schreiben von
Code der häufigste Griff daneben.

```
tank                 FK Tank · CASCADE
species              FK catalog.PlantSpecies · PROTECT
quantity
planted_on           nicht „added_on“
removed_on
note
```

### `tanks.SubstrateLayer`

Eine Schicht des Bodengrunds. `position` zählt von unten, `0` ist die unterste
Schicht; ohne sie wäre die Liste eine Menge und keine Schichtung.

```
tank                 FK Tank · CASCADE
position             0 = unterste Schicht
kind                 nutrient | soil | gravel | sand | lava | filter_mat | other
product              z. B. „Dennerle Sansibar“
grain_size           z. B. „0,5–1 mm“
depth_cm             Mächtigkeit dieser Schicht (Tank hat kein depth_cm)
added_on
depleted_on          bei Depots das rechnerische Ende der Standzeit
note
```

Abgeleitet: `is_depot`, `depth_display`, `default_depleted_on()`,
`depletion_status()`, `suggests_reminder`, `reminder_defaults()`.

### `tanks.HardscapeItem`

Wurzel, Stein, Botanik, Rückwand. Erfasst wegen der Wirkung auf die
Wasserwerte; `affects_water` ist ein eigenes Merkmal und keine Ableitung aus
`kind`, weil es vom Stück abhängt und nicht von der Kategorie.

```
tank                 FK Tank · CASCADE
kind                 wood | stone | botanicals | background | cave | other
name                 z. B. „Moorkienwurzel“
quantity             optional
added_on
removed_on           Entferntes erklärt Verläufe von früher und bleibt stehen
affects_water
water_effect         z. B. „Huminstoffe, senkt pH“
note
```

Abgeleitet: `is_active`, `expected_depletion` (Botanik nach ~6 Wochen),
`effect_label`, `suggests_reminder`.

### `tanks.CareTask`

Wiederkehrender oder einmaliger Termin. Heißt **nicht** `MaintenanceSchedule`.

```
tank                 FK Tank · CASCADE
title
category             water_change | filter | fertilizer | test | equipment | other
interval_days        leer = einmaliger Termin
due_on
last_completed_on
is_active            einmalige Termine werden beim Quittieren inaktiv
notes
```

`complete()` rechnet den nächsten Termin **ab dem Erledigungsdatum** fort, nicht
ab der alten Fälligkeit — sonst häufen sich bei einem liegen gebliebenen Termin
sofort mehrere Fälligkeiten an.

### `tanks.TaskCompletion`

```
task                 FK CareTask · CASCADE
completed_on
completed_by         FK User · SET_NULL
note                 CharField(200)
```

### `tanks.TankPhoto`

`taken_on` ist ein **Datum**, kein Zeitstempel. Das Ereignis hängt mit
`SET_NULL` dran: Wer ein Ereignis löscht, will nicht die Fotos mitlöschen.

```
tank                 FK Tank · CASCADE
event                FK Event · SET_NULL · optional
image
thumbnail            abgeleitet, editable=False
preview              abgeleitet, editable=False
width
height
caption
taken_on             DateField
created_at
```

`clean()` erzwingt, dass Foto und Ereignis zum selben Becken gehören.

## catalog

Der Katalog ist **userübergreifend**: Ein Steckbrief gehört niemandem. Deshalb
entscheidet hier — und nur hier — ein Django-Recht statt der Eigentümerschaft.

Die Basisklasse heißt `Species`, die konkreten Modelle **`PlantSpecies`** und
**`AnimalSpecies`**.

### `catalog.CatalogPermission`

Trägermodell des Rechts `can_edit_catalog`. Ohne Tabelle (`managed = False`)
und ohne Felder; es existiert allein, damit es das Recht genau einmal gibt.

### `Species` (abstrakt)

Die Identität eines Steckbriefs ist `scientific_name` **plus** `variant`, nicht
der wissenschaftliche Name allein: Wild- und Zuchtform unterscheiden sich in
Robustheit, Verhalten und Ansprüchen zu deutlich für einen gemeinsamen
Eintrag (#1244).

```
scientific_name      mit variant zusammen eindeutig (case-insensitiv), nicht allein
variant              Sortenbezeichnung, ohne Anführungszeichen gespeichert; leer = Stammform
is_cultivated_form   durch Selektion entstanden
common_name
slug                 unique · aus Name und Sorte abgeleitet (unique_slug)
summary              CharField(250)
description
water_type           WaterType
difficulty           Difficulty
temperature_min
temperature_max
ph_min
ph_max
gh_min               Gesamthärte °dH — siehe Lücke 4 (KH fehlt)
gh_max
created_at
updated_at
```

Abgeleitet: `display_name` (setzt die Anführungszeichen um die Sorte),
`primary_image`, `temperature_range`, `ph_range`, `gh_range`.

### `catalog.PlantSpecies` — erbt `Species`

```
placement            foreground | midground | background | floating | epiphyte
growth_rate          slow | medium | fast
light_demand         low | medium | high
co2_required
max_height_cm
```

### `catalog.AnimalSpecies` — erbt `Species`

```
category             fish | shrimp | crayfish | snail | mussel | other
temperament          peaceful | robust | territorial | predatory
adult_size_cm
min_group_size       Unterschreitung meldet das Dashboard als Warnung
min_tank_volume_l
```

### `SpeciesImage` (abstrakt)

```
caption
width                gilt für alle Varianten, das Seitenverhältnis bleibt erhalten
height
is_primary
sort_order
created_at
```

### `catalog.PlantImage` — erbt `SpeciesImage`

```
species              FK PlantSpecies · CASCADE · related_name="images"
image
thumbnail
preview
```

### `catalog.AnimalImage` — erbt `SpeciesImage`

```
species              FK AnimalSpecies · CASCADE · related_name="images"
image
thumbnail
preview
```

## services

Hier stehen die Geräte samt Anbindung, der Mailversand, die KI-Assistenz und
der MCP-Zugang.

### `services.MailConfig`

Singleton (`pk=1`); ohne Datensatz liefert `load()` die Werte aus
`settings.GRAPH_MAIL`. Das Client-Secret liegt verschlüsselt in der Datenbank.

```
tenant_id
client_id
client_secret        verschlüsselt · wird nirgends ausgegeben
sender_address
sender_name
reply_to
is_active
updated_at
```

### `services.MailLog`

```
created_at
recipients
subject
template
status               sent | failed | skipped
error
```

### `services.Device` — erbt `CoverImageMixin`

**Ein** Gerätemodell für alles am Becken. Ob hinter einem Gerät eine API
steckt, ist ein Merkmal der Art (`CONNECTED_KINDS`) und keine Voraussetzung
dafür, es zu erfassen. Das Becken ist Pflicht — Freitext war es einmal, mit dem
Ergebnis, dass ein Tippfehler in der Verbrauchsauswertung eine zweite Gruppe
erzeugte.

Seit #1230 liegt das Modell hier und nicht mehr in `tanks`; über
`tank.devices` ist es vom Becken aus erreichbar.

```
owner                FK core.User · CASCADE
tank                 FK tanks.Tank · CASCADE · Pflicht · related_name="devices"
kind                 eheim_classicvario | eheim_other | shelly_plug | filter | heater | light | co2 | pump | doser | sensor | socket | other
name
mac_address          Pflicht bei Eheim · je owner eindeutig, wenn gesetzt
host                 IP oder Hostname im lokalen Netz
credentials          verschlüsselt (user, password als JSON)
firmware
generation           nur Shelly: 1 oder 2+
manufacturer
model_name           Modellbezeichnung des Geräts — Tank hat kein model_name
installed_on
maintenance_interval_days
last_maintenance_on
status               Status · bei angebundenen Geräten fortgeschrieben, sonst Handeingabe
status_message
is_active            inaktive Geräte werden weder abgefragt noch geschaltet
last_seen
created_at
serial_number
supplier
purchased_on
purchase_price
warranty_until
power_watts          Grundlage der Verbrauchsschätzung ohne messende Steckdose
flow_rate_lph        nur Filter und Pumpen
daily_runtime_hours  leer heißt Dauerbetrieb (24 h)
```

### `services.DeviceSpec`

Freie technische Angabe. Feste Spalten je Geräteart wären zu vier Fünfteln
leer; typisierte Felder gibt es nur für das, womit die Anwendung selbst rechnet.

```
device               FK Device · CASCADE · related_name="specs"
label                z. B. „Beckenvolumen“
value                z. B. „60–160“
unit
position
```

### `services.DeviceDocument`

Die Datei liegt in der geschützten Ablage und hat keine öffentliche Adresse.

```
device               FK Device · CASCADE · related_name="documents"
kind                 manual | invoice | warranty | photo | other
title
file
uploaded_at
```

### `services.DeviceLink`

```
device               FK Device · CASCADE · related_name="links"
title
url
position
```

### `services.DeviceReading`

Eine Statusabfrage: ausgewertete Felder plus unveränderte Rohantwort.

```
device               FK Device · CASCADE · related_name="readings"
read_at
payload              Rohantwort, unverändert
rpm_percent          Eheim
pump_mode            Eheim
error_code           Eheim
service_due_in       Eheim, Stunden bis zur Wartung
is_on                Shelly
power_w              Shelly
energy_total_wh      Shelly, Zählerstand
temperature_c        Shelly
```

### `services.DeviceEvent`

Protokoll jeder schreibenden Aktion am Gerät. Die Felder sind wie `tanks.Event`
geschnitten: Derselbe Vorgang wird zusätzlich als Ereignis der Kategorie
*Technik* am Becken abgelegt.

```
device               FK Device · CASCADE · related_name="events"
user                 FK User · SET_NULL
occurred_at
action
title
description
succeeded
```

### `services.AIConfig`

Singleton wie `MailConfig`. Ohne Key ist die Anwendung vollständig benutzbar,
die KI-Funktionen sind dann ausgeblendet.

```
api_key              verschlüsselt · wird nirgends ausgegeben
model_name           das angesprochene Sprachmodell
is_enabled
monthly_token_budget
per_user_daily_limit
updated_at
```

### `services.AIUsageLog`

Ein Eintrag je Aufruf, auch je abgelehntem. Ausschließlich Metadaten — keine
Prompts, keine Antworten, kein Key.

```
user                 FK User · SET_NULL
action
model_name
prompt_tokens
completion_tokens
total_cost_usd
duration_ms
success
error_message
created_at
```

### `services.AISuggestion`

Ein Entwurf, kein Ergebnis. In den Katalog kommt er erst durch eine
ausdrückliche Bestätigung.

```
user                 FK User · SET_NULL
kind                 animal | plant
scientific_name
common_name
confidence
reasoning
payload              Entwurf in den Feldern des Prompts — auch das, wofür es im Katalog kein Feld gibt
status               draft | verified | rejected
catalog_ref          Verweis auf den übernommenen Katalogeintrag
decided_at
created_at
```

### `services.MCPToken`

Gespeichert wird nur der SHA-256-Hash; den Klartext gibt es genau einmal, beim
Anlegen. Der Token trägt den Benutzer und nur ihn — einen Token auf fremde
Daten gibt es nicht.

```
user                 FK User · CASCADE · related_name="mcp_tokens"
name
token_hash           unique · editable=False
hint                 die letzten Zeichen, zum Wiedererkennen
allow_write
created_at
last_used_at
expires_at
revoked_at
```

### `services.MCPAccessLog`

Protokoll der **schreibenden** MCP-Aufrufe, auch der abgewiesenen. Lesende
stehen bewusst nicht hier: Sie verändern nichts, und ein Protokoll jeder
Abfrage wäre eine Bewegungsdatenbank über den eigenen Benutzer.

```
token                FK MCPToken · SET_NULL
user                 FK User · SET_NULL
token_name           bleibt lesbar, wenn der Token gelöscht wurde
tool
arguments
object_ref
succeeded
error_message
created_at
```

## dashboard

Keine Modelle. Die App wertet aus, was in `tanks` und `services` steht.

## Entwurfsnamen und was daraus wurde

Aus #1214–#1219. Links steht, was in den Entwürfen stand und gelegentlich noch
in Prompts, Items und Code auftaucht; rechts, was es tatsächlich gibt.

| Entwurf | Tatsächlich |
|---|---|
| `Tank.shut_down_on` | `Tank.dissolved_on` |
| `Tank.biotope` | gibt es nicht |
| `Tank.volume_gross_l` / `volume_net_l` | `Tank.volume_liters` — ein Feld |
| `Tank.depth_cm` | `Tank.width_cm`; `depth_cm` gibt es nur an `SubstrateLayer` und meint dort die Mächtigkeit einer Schicht |
| `Tank.model_name` | gibt es nicht — `model_name` steht an `services.Device` |
| `Parameter.position` | `Parameter.sort_order` |
| `Parameter.supports_below_detection` | gibt es nicht → Lücke 1 |
| `TankParameterTarget.target` | gibt es nicht — nur `minimum` / `maximum` |
| `Event.breeding`, `Event.water_changed_l` | gibt es nicht |
| `TankAnimalMovement`, `Stocking.status`, `Stocking.origin` | gibt es nicht — Abgang ist `removed_on` |
| `MaintenanceSchedule` | `tanks.CareTask` (+ `tanks.TaskCompletion`) |
| `CatalogPlant` / `CatalogAnimal` | `catalog.PlantSpecies` / `catalog.AnimalSpecies` |
| `tanks.Device` | `services.Device` (#1230) |

Zwei Punkte aus den Entwürfen sind inzwischen umgesetzt und stehen deshalb
nicht mehr in der Tabelle: Die Kategorie `observation` an `tanks.Event` gibt es,
und Fotos lassen sich über `TankPhoto.event` an ein Ereignis hängen.
`Parameter.decimals` existiert ebenfalls — anders als in der ersten Fassung
dieser Referenz behauptet.

## Bekannte fachliche Lücken

Fehlende Funktion, keine Namensfragen. Der Stand der Bewertung, damit dieselbe
Frage nicht dreimal aufgemacht wird.

### 1. „n. n.“ ist nicht erfassbar — offen, eigenes Item

`Parameter` hat kein `supports_below_detection`, `Measurement.value` ist
Pflicht. Nitrit unterhalb der Nachweisgrenze — der wichtigste Wert der
Einfahrphase — lässt sich nicht als solcher festhalten. Als `0` zu speichern
ist fachlich falsch: „nicht nachweisbar“ heißt „unter der Nachweisgrenze“,
nicht „null“.

Nicht nebenbei zu erledigen: Betroffen sind Modell und Migration, die
Statuslogik (`classify_value`), die Diagramme, die MCP-Serialisierung und die
Erfassungsmaske.

### 2. Zuchtformen sind nicht unterscheidbar — erledigt (#1244)

`Species.variant` und `Species.is_cultivated_form` gibt es, die Eindeutigkeit
liegt case-insensitiv auf `(scientific_name, variant)`, der Slug wird aus
beidem gebildet, und `SpeciesQuerySet.wild_forms()` grenzt die Stammformen ab.
*Mikrogeophagus ramirezi* und die Zuchtform 'Electric Blue' sind getrennte
Steckbriefe.

### 3. Kein `verified`-Flag am Katalog — zurückgestellt, aber eine Lücke daneben

Die Trennung zwischen gepflegten und nutzergenerierten Einträgen leistet
derzeit die Berechtigung: Katalogpflege hängt an `can_edit_catalog`
(`catalog/permissions.py`), Selbstangelegtes gibt es also nicht. Ein zweites
Flag am Modell würde dieselbe Frage ein zweites Mal beantworten — deshalb
zurückgestellt. Neu zu bewerten, sobald jeder Benutzer Katalogeinträge anlegen
darf.

Beim Abgleich ist allerdings aufgefallen, dass **der KI-Weg diese Berechtigung
nicht prüft**: `services.views.ai_suggestion_decide` verlangt nur eine
Anmeldung und eine eingerichtete KI, und `services.ai.catalog.publish()`
schreibt den Eintrag anschließend in den userübergreifenden Katalog. Jeder
angemeldete Benutzer kann so am Recht vorbei einen Steckbrief veröffentlichen.
Das ist keine Modellfrage, sondern eine fehlende Prüfung, und gehört als
eigenes Item bewertet.

### 4. KH fehlt im Steckbrief — offen, eigenes Item

`Species` kennt nur `gh_min` / `gh_max`. Für Weichwasserarten ist die
Karbonathärte die aussagekräftigere Größe. Betroffen wären Modell und
Migration, Formular und Anzeige, der KI-Prompt für den Steckbrief-Entwurf und
`CATALOG_FIELDS` in `services/mcp/serialize.py`.

### 5. Kein Abgangsgrund beim Besatz — offen, eigenes Item

`Stocking.removed_on` steht ohne Grund da. Dreimal „gesprungen“ in einem Becken
wäre ein verwertbarer Hinweis, bleibt so aber unsichtbar. Das freie `note`
taugt dafür nicht: Es ist nicht auswertbar und trägt bereits andere Inhalte.
