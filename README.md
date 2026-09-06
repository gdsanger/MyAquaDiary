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
