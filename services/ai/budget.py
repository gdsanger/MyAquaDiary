"""Budgetprüfung vor jedem Aufruf.

Zwei Grenzen, beide aus :class:`~services.models.AIConfig`:

* ``monthly_token_budget`` — über alle Benutzer, je Kalendermonat
* ``per_user_daily_limit`` — je Benutzer und Kalendertag

Gerechnet wird über :class:`~services.models.AIUsageLog`, also über tatsächlich
verbrauchte Token. Ein Aufruf, der die Grenze überschreiten würde, wird vorher
abgelehnt; die letzte Anfrage vor dem Limit darf noch durchlaufen, weil der
Verbrauch erst nach der Antwort feststeht.
"""

from dataclasses import dataclass

from django.db.models import F, Sum
from django.utils import timezone

from ..models import AIConfig, AIUsageLog
from .exceptions import AIBudgetExceeded

#: 0 heißt „keine Begrenzung" — dann wird gar nicht erst gezählt.
UNLIMITED = 0


def thousands(value: int) -> str:
    """Tausenderpunkte, ohne von der Locale des Prozesses abzuhängen."""
    return f"{int(value):,}".replace(",", ".")


@dataclass(frozen=True)
class BudgetStatus:
    """Verbrauch und Grenzen für Anzeige und Prüfung."""

    monthly_used: int
    monthly_limit: int
    daily_used: int
    daily_limit: int

    @property
    def monthly_exceeded(self) -> bool:
        return self.monthly_limit > UNLIMITED and self.monthly_used >= self.monthly_limit

    @property
    def daily_exceeded(self) -> bool:
        return self.daily_limit > UNLIMITED and self.daily_used >= self.daily_limit

    @property
    def exceeded(self) -> bool:
        return self.monthly_exceeded or self.daily_exceeded

    @property
    def monthly_percent(self) -> int:
        return _percent(self.monthly_used, self.monthly_limit)

    @property
    def daily_percent(self) -> int:
        return _percent(self.daily_used, self.daily_limit)

    @property
    def message(self) -> str:
        """Verständliche Begründung für den Benutzer — leer, wenn frei."""
        if self.monthly_exceeded:
            return (
                "Das Token-Budget für diesen Monat ist aufgebraucht "
                f"({thousands(self.monthly_used)} von {thousands(self.monthly_limit)} Token). "
                "Die KI-Funktionen stehen ab dem Ersten des nächsten Monats wieder "
                "zur Verfügung."
            )
        if self.daily_exceeded:
            return (
                "Dein Tageslimit für die KI-Assistenz ist erreicht "
                f"({thousands(self.daily_used)} von {thousands(self.daily_limit)} Token). "
                "Morgen geht es weiter."
            )
        return ""


def _percent(used: int, limit: int) -> int:
    if limit <= UNLIMITED:
        return 0
    return min(round(used / limit * 100), 100)


def _tokens(queryset) -> int:
    total = queryset.aggregate(
        total=Sum(F("prompt_tokens") + F("completion_tokens"))
    )["total"]
    return int(total or 0)


def used_this_month(now=None) -> int:
    """Verbrauchte Token aller Benutzer im laufenden Kalendermonat."""
    now = now or timezone.localtime()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return _tokens(AIUsageLog.objects.filter(created_at__gte=month_start))


def used_today(user, now=None) -> int:
    """Verbrauchte Token eines Benutzers am laufenden Kalendertag."""
    if user is None or not getattr(user, "pk", None):
        return 0
    now = now or timezone.localtime()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return _tokens(AIUsageLog.objects.filter(user=user, created_at__gte=day_start))


def status(user=None, config: AIConfig | None = None) -> BudgetStatus:
    """Aktueller Stand beider Grenzen."""
    config = config if config is not None else AIConfig.load()
    monthly_limit = max(int(config.monthly_token_budget or UNLIMITED), UNLIMITED)
    daily_limit = max(int(config.per_user_daily_limit or UNLIMITED), UNLIMITED)
    return BudgetStatus(
        monthly_used=used_this_month() if monthly_limit > UNLIMITED else 0,
        monthly_limit=monthly_limit,
        daily_used=used_today(user) if daily_limit > UNLIMITED else 0,
        daily_limit=daily_limit,
    )


def check(user=None, config: AIConfig | None = None) -> BudgetStatus:
    """Wirft :class:`AIBudgetExceeded`, wenn eine Grenze erreicht ist."""
    current = status(user, config)
    if current.exceeded:
        raise AIBudgetExceeded(current.message)
    return current
