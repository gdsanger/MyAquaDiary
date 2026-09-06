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
