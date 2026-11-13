"""Registre des sources natives : sites que JobSpy ne gère pas, chacun avec son adaptateur écrit à la main."""

from collections.abc import Callable

from alertes_emploi.modele import Offre


class SourceBloquee(Exception):
    """Un adaptateur natif a reçu un 429 sur sa page de recherche ; jobspy_adapter.rechercher la convertit en RechercheBloquee."""

    def __init__(self, site: str) -> None:
        super().__init__(site)
        self.site = site


# Signature commune d'un adaptateur natif :
# rechercher(termes, lieu, rayon_km, anciennete_heures, nombre_resultats, type_contrat, teletravail) -> list[Offre]
AdaptateurNatif = Callable[[str, str, int, int | None, int, str | None, bool | None], list[Offre]]

def _france_travail(*criteres) -> list[Offre]:
    """Import différé : france_travail importe SourceBloquee de ce module."""
    from alertes_emploi.sources import france_travail

    return france_travail.rechercher(*criteres)


SITE_FRANCE_TRAVAIL = "francetravail"

# Clé = valeur de Offre.source, aussi cochée dans le formulaire et saisie dans priorite_sites.
SOURCES_NATIVES: dict[str, AdaptateurNatif] = {SITE_FRANCE_TRAVAIL: _france_travail}
