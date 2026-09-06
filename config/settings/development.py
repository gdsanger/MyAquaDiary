from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True

ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
MIDDLEWARE.insert(  # noqa: F405
    MIDDLEWARE.index("django.middleware.common.CommonMiddleware") + 1,  # noqa: F405
    "debug_toolbar.middleware.DebugToolbarMiddleware",
)

INTERNAL_IPS = ["127.0.0.1"]
