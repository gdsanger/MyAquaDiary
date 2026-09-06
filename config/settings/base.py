"""
Base Django settings for MyAquaDiary, shared by all environments.

Environment-specific settings live in development.py / production.py.
Secrets and environment-dependent values are read from the process
environment (see .env.example) via django-environ.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY")

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# Schlüsselmaterial für core.fields.EncryptedTextField. Der erste Schlüssel
# verschlüsselt, alle weiteren stehen für die Entschlüsselung bereit (Rotation).
# Ohne Angabe wird ein Schlüssel aus SECRET_KEY abgeleitet.
FIELD_ENCRYPTION_KEYS = env.list("DJANGO_FIELD_ENCRYPTION_KEYS", default=[])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django_htmx",
    "core",
    "catalog",
    "tanks",
    "dashboard",
    "services",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "services.context_processors.ai_status",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env("POSTGRES_USER"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", default="db"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

AUTH_USER_MODEL = "core.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard:index"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "de-de"
TIME_ZONE = "Europe/Berlin"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "mediafiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Startwerte für die MailConfig-Singleton. Solange kein Datensatz existiert,
# arbeitet der Mailversand direkt mit diesen Werten; das Admin-Formular nimmt
# sie als Vorbelegung. Bleiben sie leer, ist der Mailversand schlicht inaktiv.
GRAPH_MAIL = {
    "TENANT_ID": env("GRAPH_TENANT_ID", default=""),
    "CLIENT_ID": env("GRAPH_CLIENT_ID", default=""),
    "CLIENT_SECRET": env("GRAPH_CLIENT_SECRET", default=""),
    "SENDER_ADDRESS": env("GRAPH_SENDER_ADDRESS", default=""),
    "SENDER_NAME": env("GRAPH_SENDER_NAME", default="MyAquaDiary"),
    "REPLY_TO": env("GRAPH_REPLY_TO", default=""),
}

# Timeout für Requests an EHEIM-Digital-Geräte im LAN. Bewusst kurz: der
# Gerätestatus wird per HTMX nachgeladen, und ein stummes Gerät darf die
# Anzeige nicht aufhalten.
EHEIM_TIMEOUT = env.int("EHEIM_TIMEOUT", default=5)

# Timeout für Requests an Shelly-Geräte im LAN — aus demselben Grund kurz.
SHELLY_TIMEOUT = env.int("SHELLY_TIMEOUT", default=5)

# Arbeitspreis je Kilowattstunde für die Verbrauchsauswertung. Reine
# Anzeigehilfe: gespeichert werden Kilowattstunden, keine Beträge.
ENERGY_PRICE_PER_KWH = env("ENERGY_PRICE_PER_KWH", default="0.35")

# Startwerte für die AIConfig-Singleton (Anthropic Claude). Ohne API-Key sind
# sämtliche KI-Funktionen ausgeblendet, die Anwendung läuft normal weiter.
# Ein im Admin gepflegter Datensatz hat Vorrang.
ANTHROPIC = {
    "API_KEY": env("ANTHROPIC_API_KEY", default=""),
    "MODEL": env("ANTHROPIC_MODEL", default="claude-opus-5"),
}

# Längste Bildkante, auf die ein Foto vor dem Versand an Claude gerechnet wird.
# Bilder sind der teure Teil der Bilderkennung; 1024 px reichen für eine
# Artbestimmung und kosten einen Bruchteil der Token eines Originalfotos.
AI_IMAGE_MAX_EDGE = env.int("AI_IMAGE_MAX_EDGE", default=1024)

# Timeout (Sekunden) für einen Aufruf der Claude-API. Großzügiger als bei den
# Geräten im LAN: hier denkt ein Modell nach, und der Aufruf passiert bewusst
# nur auf ausdrückliche Anforderung des Benutzers.
AI_TIMEOUT = env.int("AI_TIMEOUT", default=120)

# Basis-URL für absolute Links in Mails (Mails haben keinen Request-Kontext).
SITE_URL = env("SITE_URL", default="http://localhost:8000")

# Öffentliche Adresse des MCP-Endpunkts. Steht getrennt neben SITE_URL, weil
# der MCP-Server ein eigener Dienst auf einem eigenen Port ist — und die
# Beispielkonfiguration auf der Token-Seite die Adresse nennen muss, die der
# Client wirklich erreicht.
MCP_PUBLIC_URL = env("MCP_PUBLIC_URL", default="http://localhost:8001")

# Aufrufe je MCP-Token und Minute. Schützt vor einem fehlkonfigurierten Client,
# der in einer Schleife schreibt. 0 schaltet die Prüfung ab.
MCP_RATE_LIMIT_PER_MINUTE = env.int("MCP_RATE_LIMIT_PER_MINUTE", default=60)

# Abstand der Keep-alive-Zeilen im SSE-Strom. Kurz genug, dass kein Proxy die
# ruhige Verbindung für tot hält.
MCP_KEEPALIVE_SECONDS = env.int("MCP_KEEPALIVE_SECONDS", default=15)

# So lange darf eine SSE-Sitzung ohne Lebenszeichen im Speicher liegen, bevor
# sie beim nächsten Verbindungsaufbau aufgeräumt wird.
MCP_SESSION_IDLE_TIMEOUT = env.int("MCP_SESSION_IDLE_TIMEOUT", default=3600)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {"format": "{levelname} {asctime} {name} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "simple"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "services": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "core": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
