"""Briques communes aux adaptateurs natifs : client GET espacé d'une pause (un 429 lève SourceBloquee) et lecture d'une valeur JSON textuelle."""

import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from alertes_emploi.sources.registre import SourceBloquee

PAUSE_SECONDES = 1.0
DELAI_SECONDES = 20
_CODE_TROP_DE_REQUETES = 429


class ClientHttp:
    """Requêtes GET d'un site, espacées d'une pause (aucune avant la première) ; un 429 lève SourceBloquee(site)."""

    def __init__(self, site: str, entetes: dict[str, str]) -> None:
        self._site = site
        self._entetes = entetes
        self._premiere = True

    def lire(self, url: str) -> str:
        if not self._premiere:
            time.sleep(PAUSE_SECONDES)
        self._premiere = False
        try:
            with urlopen(Request(url, headers=self._entetes), timeout=DELAI_SECONDES) as reponse:
                return reponse.read().decode("utf-8")
        except HTTPError as erreur:
            if erreur.code == _CODE_TROP_DE_REQUETES:
                raise SourceBloquee(self._site) from erreur
            raise


def texte_json(valeur: object) -> str:
    """Valeur JSON textuelle nettoyée ; vide si absente ou d'un autre type (la donnée vient d'un site externe)."""
    return valeur.strip() if isinstance(valeur, str) else ""
