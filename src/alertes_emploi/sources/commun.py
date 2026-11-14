"""Briques communes aux adaptateurs natifs : client GET espacé d'une pause (un 429 lève SourceBloquee), lecture d'une valeur JSON textuelle et assemblage de la description en Markdown (puces, sections)."""

import time
from collections.abc import Iterable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from alertes_emploi.sources.registre import SourceBloquee

PAUSE_SECONDES = 1.0
DELAI_SECONDES = 20
SEPARATEUR_ELEMENTS = " ; "
SEPARATEUR_LIGNES = "\n"
SEPARATEUR_PARAGRAPHES = "\n\n"
GABARIT_PUCE = "- **{libelle}** : {valeur}"
GABARIT_SECTION = "## {titre}\n{corps}"
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


def puce(libelle: str, valeur: str) -> str:
    """Ligne Markdown « - **Libellé** : valeur » (les pages des sites mettent le libellé en gras)."""
    return GABARIT_PUCE.format(libelle=libelle, valeur=valeur)


def section(titre: str, lignes: list[str]) -> str:
    """Section Markdown : titre `## …` puis ses lignes non vides ; vide si elles le sont toutes."""
    corps = SEPARATEUR_LIGNES.join(filter(None, lignes))
    return GABARIT_SECTION.format(titre=titre, corps=corps) if corps else ""


def paragraphes(parties: Iterable[str]) -> str:
    """Parties non vides séparées par une ligne vide."""
    return SEPARATEUR_PARAGRAPHES.join(filter(None, parties))


def texte_json(valeur: object) -> str:
    """Valeur JSON textuelle nettoyée ; vide si absente ou d'un autre type (la donnée vient d'un site externe)."""
    return valeur.strip() if isinstance(valeur, str) else ""
