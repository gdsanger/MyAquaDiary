"""Wer den Katalog pflegen darf.

Eine Stelle, an der die Frage beantwortet wird — Ansichten und Templates
fragen dieselbe Funktion, damit keine Schaltfläche erscheint, die hinterher
in einer 403 endet.
"""

#: Recht aus :class:`catalog.models.CatalogPermission`.
CATALOG_EDIT_PERM = "catalog.can_edit_catalog"


def may_edit_catalog(user) -> bool:
    """Staff darf immer, alle anderen brauchen ``can_edit_catalog``."""
    if not getattr(user, "is_authenticated", False):
        return False
    return bool(user.is_staff or user.has_perm(CATALOG_EDIT_PERM))
