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
