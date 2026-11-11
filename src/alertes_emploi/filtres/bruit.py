"""Filtrage du bruit : écarte les offres selon les mots-clés d'exclusion d'un profil et ses termes de recherche requis."""

import re
import unicodedata
from collections.abc import Iterable

from alertes_emploi.modele import Offre


def _normaliser_pour_motif(texte: str) -> str:
    """Minuscules et sans accents, pour comparer sans tenir compte de la casse ni des accents.

    Volontairement distincte de `modele._normaliser` (NFD + lower, espaces réduits), dont la sortie fait la clé de déduplication stockée en base : ici NFKD + casefold (ß, ligatures) et les retours à la ligne sont conservés.
    """
    decompose = unicodedata.normalize("NFKD", texte.casefold())
    return "".join(caractere for caractere in decompose if not unicodedata.combining(caractere))


def _motif(mot: str) -> re.Pattern[str]:
    # Mot entier : ni lettre ni chiffre accolé de part et d'autre.
    return re.compile(rf"(?<!\w){re.escape(_normaliser_pour_motif(mot.strip()))}(?!\w)")


def filtrer_bruit(
    offres: Iterable[Offre],
    termes_requis: Iterable[str] = (),
    mots_exclus: Iterable[str] = (),
) -> tuple[list[Offre], list[tuple[Offre, str]]]:
    """Sépare les offres en (retenues, écartées avec leur motif).

    Une offre est écartée si son titre ou sa description contient un mot exclu, ou si des termes
    requis sont définis et qu'aucun n'apparaît en mot entier. L'exclusion prime sur les termes requis.
    """
    exclus = [(mot, _motif(mot)) for mot in mots_exclus if mot.strip()]
    requis = [_motif(mot) for mot in termes_requis if mot.strip()]
    retenues: list[Offre] = []
    ecartees: list[tuple[Offre, str]] = []
    for offre in offres:
        texte = _normaliser_pour_motif(f"{offre.titre}\n{offre.description}")
        motif_exclusion = next((mot for mot, motif in exclus if motif.search(texte)), None)
        if motif_exclusion is not None:
            ecartees.append((offre, f"exclu : {motif_exclusion}"))
        elif requis and not any(motif.search(texte) for motif in requis):
            ecartees.append((offre, "terme de recherche absent"))
        else:
            retenues.append(offre)
    return retenues, ecartees
