"""Règles d'un profil appliquées à des offres : mots-clés, zone (rayon 0) et villes exclues."""

from collections.abc import Iterable

from alertes_emploi.filtres.bruit import filtrer_bruit
from alertes_emploi.modele import Offre, dans_la_zone, ville_exclue
from alertes_emploi.profils.chargeur import ParametresGlobaux, Profil

MOTIF_SANS_DESCRIPTION = "sans description"


def appliquer_regles(
    offres: Iterable[Offre], profil: Profil, globaux: ParametresGlobaux = ParametresGlobaux()
) -> tuple[list[Offre], list[tuple[Offre, str]]]:
    """Sépare les offres en (retenues, écartées avec leur motif) d'après les règles actuelles du profil.

    Les mots et villes exclus des paramètres globaux s'ajoutent à ceux du profil.

    Une description vide ou blanche écarte l'offre d'emblée (« sans description »). Mots exclus, puis termes de recherche requis (si exiger_termes) d'abord ; puis, si le rayon est 0, l'offre doit être dans la ville du profil (« hors zone »),
    sinon on fait confiance au rayon de la source ; enfin une ville exclue écarte l'offre (« ville exclue : … »).
    """
    termes_requis = profil.termes_recherche if profil.exiger_termes else ()
    avec_description: list[Offre] = []
    sans_description: list[Offre] = []
    for offre in offres:
        (avec_description if offre.description.strip() else sans_description).append(offre)
    villes_exclues = profil.villes_exclues + globaux.villes_exclues
    retenues, ecartees = filtrer_bruit(avec_description, termes_requis, profil.mots_exclus + globaux.mots_exclus)
    ecartees.extend((offre, MOTIF_SANS_DESCRIPTION) for offre in sans_description)
    gardees: list[Offre] = []
    for offre in retenues:
        ville = ville_exclue(offre, villes_exclues)
        if profil.rayon_km == 0 and not dans_la_zone(offre, profil.lieu):
            ecartees.append((offre, "hors zone"))
        elif ville is not None:
            ecartees.append((offre, f"ville exclue : {ville}"))
        else:
            gardees.append(offre)
    return gardees, ecartees
