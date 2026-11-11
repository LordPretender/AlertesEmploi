import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from rapidfuzz import fuzz

# Détection des doublons (hypothèses de départ, à calibrer sur la vraie base)
POIDS_TITRE = 40
POIDS_SOCIETE = 20
POIDS_DESCRIPTION = 40
SEUIL_DOUBLON = 85
SEUIL_TITRE_IDENTIQUE = 95
SEUIL_SOCIETE_COMPATIBLE = 90
SEUIL_TITRE_PROCHE = 90
SEUIL_DESCRIPTION_SANS_TITRE = 97
_LONGUEUR_DESCRIPTION = 1500

_BALISES_HTML = re.compile(r"<[^>]+>")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Offre:
    titre: str
    societe: str
    lieu: str
    url: str
    description: str
    source: str
    description_bloquee: bool = field(default=False, compare=False)  # LinkedIn : description absente à cause d'un 429 sur la page de l'offre (à réessayer)


def _normaliser(texte: str) -> str:
    """Minuscules, sans accents, espaces réduits à un seul et bords retirés."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte) if not unicodedata.combining(c))
    return " ".join(sans_accents.lower().split())


def _ville(lieu: str) -> str:
    """Ville normalisée : le lieu avant la première virgule."""
    return _normaliser(lieu.split(",", 1)[0])


def _villes_compatibles(a: str, b: str) -> bool:
    """Vrai si les deux villes sont égales ou si les mots de l'une débutent celle de l'autre (« aix » et « aix-en-provence »)."""
    court, long = sorted((_comparable(a).split(), _comparable(b).split()), key=len)
    return bool(court) and long[: len(court)] == court


def cle_offre(offre: Offre) -> str:
    """Clé de déduplication titre|société|ville, identique d'une plateforme à l'autre (ville = lieu avant la première virgule)."""
    return f"{_normaliser(offre.titre)}|{_normaliser(offre.societe)}|{_ville(offre.lieu)}"


@lru_cache(maxsize=8192)
def _comparable(texte: str) -> str:
    """Texte prêt à comparer : balises HTML retirées, normalisé, ponctuation remplacée par des espaces."""
    sans_balises = _BALISES_HTML.sub(" ", texte)
    return " ".join(_NON_ALNUM.sub(" ", _normaliser(sans_balises)).split())


def sont_quasi_doublons(a: Offre, b: Offre) -> bool:
    """Même ville, puis score pondéré titre + société + description (RapidFuzz) >= SEUIL_DOUBLON, ou titre et société quasi identiques quelle que soit la description.

    Un champ vide d'un côté ou de l'autre est exclu du calcul et les poids restants sont renormalisés ; il faut au moins deux champs comparés.
    Hors règle titre et société quasi identiques, un titre éloigné (< SEUIL_TITRE_PROCHE) n'est fusionné que si la description est quasi identique (>= SEUIL_DESCRIPTION_SANS_TITRE)."""
    if not _villes_compatibles(_ville(a.lieu), _ville(b.lieu)):
        return False
    titre_a, titre_b = _comparable(a.titre), _comparable(b.titre)
    societe_a, societe_b = _comparable(a.societe), _comparable(b.societe)
    similarites: list[tuple[float, int]] = []
    sim_titre = sim_societe = None
    if titre_a and titre_b:
        sim_titre = fuzz.token_sort_ratio(titre_a, titre_b)
        similarites.append((sim_titre, POIDS_TITRE))
    if societe_a and societe_b:
        sim_societe = fuzz.token_set_ratio(societe_a, societe_b)
        similarites.append((sim_societe, POIDS_SOCIETE))
    if sim_titre is not None and sim_societe is not None and sim_titre >= SEUIL_TITRE_IDENTIQUE and sim_societe >= SEUIL_SOCIETE_COMPATIBLE:
        return True

    description_a = _comparable(a.description)[:_LONGUEUR_DESCRIPTION]
    description_b = _comparable(b.description)[:_LONGUEUR_DESCRIPTION]
    sim_description = None
    comparer_description = bool(description_a and description_b)
    if len(similarites) + comparer_description < 2:
        return False
    # La description (comparaison coûteuse) n'est calculée que si, même identique, elle pourrait faire atteindre le seuil.
    if comparer_description:
        optimiste = similarites + [(100, POIDS_DESCRIPTION)]
        if _score(optimiste) < SEUIL_DOUBLON:
            return False
        sim_description = fuzz.partial_ratio(description_a, description_b)
        similarites.append((sim_description, POIDS_DESCRIPTION))
    if _score(similarites) < SEUIL_DOUBLON:
        return False
    return sim_titre is None or sim_titre >= SEUIL_TITRE_PROCHE or (sim_description is not None and sim_description >= SEUIL_DESCRIPTION_SANS_TITRE)


def _score(similarites: list[tuple[float, int]]) -> float:
    """Moyenne pondérée des similarités (0 à 100) ; les champs absents ne figurent pas dans la liste, les poids sont renormalisés."""
    return sum(sim * poids for sim, poids in similarites) / sum(poids for _, poids in similarites)


def ville_exclue(offre: Offre, villes_exclues: tuple[str, ...]) -> str | None:
    """La ville exclue (telle que saisie) correspondant à la ville de l'offre (lieu avant la première virgule, comparaison exacte sans casse ni accents), None sinon."""
    ville = _ville(offre.lieu)
    return next((v for v in villes_exclues if _normaliser(v) and _normaliser(v) == ville), None)


def dans_la_zone(offre: Offre, lieu_profil: str) -> bool:
    """Vrai si la ville du profil (lieu avant la première virgule) figure dans le lieu de l'offre ; sans ville de profil, tout passe."""
    ville = _ville(lieu_profil)
    return not ville or ville in _normaliser(offre.lieu)
