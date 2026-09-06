"""Fehlerklassen des Graph-Mailservice."""


class GraphMailError(Exception):
    """Der Versand über die Graph-API ist fehlgeschlagen."""


class MailNotConfigured(GraphMailError):
    """Es liegen keine (vollständigen) Zugangsdaten vor."""
