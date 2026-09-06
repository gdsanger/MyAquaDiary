"""Preise und Modellfähigkeiten — alles, was am Modellnamen hängt.

Bewusst ohne Zugriff auf Datenbank oder API: die Kosten je Aufruf werden aus
den zurückgemeldeten Token gerechnet, nicht abgefragt.
"""

from decimal import ROUND_HALF_UP, Decimal

#: Listenpreise in USD je einer Million Token (Stand 06/2026).
#: Unbekannte Modelle werden mit 0 bewertet — dann stimmt die Tokenzählung
#: weiter, nur die Kostenspalte bleibt leer. Das ist ehrlicher als ein
#: geratener Preis.
PRICES_PER_MILLION = {
    "claude-fable-5": (Decimal("10.00"), Decimal("50.00")),
    "claude-mythos-5": (Decimal("10.00"), Decimal("50.00")),
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-8": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-7": (Decimal("5.00"), Decimal("25.00")),
    "claude-opus-4-6": (Decimal("5.00"), Decimal("25.00")),
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
    "claude-sonnet-4-6": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
}

#: Modelle, die ein erzwungenes JSON-Schema (``output_config.format``)
#: beherrschen. Bei allen anderen wird das Schema nur im Prompt beschrieben
#: und die Antwort nachsichtig geparst — ein im Admin eingetragenes älteres
#: Modell soll die Bestimmung nicht mit einem 400 quittieren.
STRUCTURED_OUTPUT_MODELS = frozenset(
    {
        "claude-fable-5",
        "claude-mythos-5",
        "claude-opus-5",
        "claude-opus-4-8",
        "claude-sonnet-5",
        "claude-haiku-4-5",
    }
)

#: Aufschlag beim Schreiben des Prompt-Caches bzw. Nachlass beim Lesen.
CACHE_WRITE_FACTOR = Decimal("1.25")
CACHE_READ_FACTOR = Decimal("0.10")

MILLION = Decimal(1_000_000)
CENT_PRECISION = Decimal("0.0001")


def supports_structured_output(model_name: str) -> bool:
    return (model_name or "").strip() in STRUCTURED_OUTPUT_MODELS


def cost_usd(model_name, prompt_tokens, completion_tokens, cache_write=0, cache_read=0) -> Decimal:
    """Kosten eines Aufrufs in USD, gerundet auf vier Nachkommastellen.

    Cache-Token werden getrennt bewertet: geschriebene Cache-Token kosten mehr
    als normale Eingabe, gelesene deutlich weniger.
    """
    input_price, output_price = PRICES_PER_MILLION.get(
        (model_name or "").strip(), (Decimal(0), Decimal(0))
    )
    billable_input = (
        Decimal(prompt_tokens or 0)
        + Decimal(cache_write or 0) * CACHE_WRITE_FACTOR
        + Decimal(cache_read or 0) * CACHE_READ_FACTOR
    )
    total = (billable_input * input_price + Decimal(completion_tokens or 0) * output_price) / MILLION
    return total.quantize(CENT_PRECISION, rounding=ROUND_HALF_UP)
