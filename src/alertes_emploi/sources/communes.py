"""Table locale des communes françaises (communes.json, générée par scripts/generer-communes.ps1) : code INSEE d'un lieu saisi dans un profil."""

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path

_FICHIER = Path(__file__).with_name("communes.json")
_CODE_POSTAL = re.compile(r"\b\d{5}\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Clés du JSON de geo.api.gouv.fr.
_CLE_NOM = "nom"
_CLE_CODE = "code"
_CLE_CODES_POSTAUX = "codesPostaux"
_CLE_POPULATION = "population"


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, ponctuation remplacée par des espaces (« Saint-Étienne » donne « saint etienne »)."""
    sans_accents = "".join(c for c in unicodedata.normalize("NFD", texte) if not unicodedata.combining(c))
    return " ".join(_NON_ALNUM.sub(" ", sans_accents.lower()).split())


@lru_cache(maxsize=1)
def _communes() -> tuple[dict[str, list[dict]], list[dict]]:
    """Communes indexées par nom normalisé, et liste complète."""
    with _FICHIER.open(encoding="utf-8") as fichier:
        liste = json.load(fichier)
    index: dict[str, list[dict]] = {}
    for commune in liste:
        index.setdefault(normaliser(commune[_CLE_NOM]), []).append(commune)
    return index, liste


def code_insee(lieu: str) -> str | None:
    """Code INSEE de la commune du lieu (texte avant la première virgule), ou None si introuvable.

    Un code postal dans le lieu départage les homonymes ; sans nom exploitable, il désigne à lui seul la commune la plus peuplée qui le porte.
    Parmi des homonymes sans code postal (ou sans correspondance), la commune la plus peuplée l'emporte.
    """
    postal = _CODE_POSTAL.search(lieu)
    nom = normaliser(_CODE_POSTAL.sub(" ", lieu.split(",", 1)[0]))
    index, liste = _communes()
    if nom:
        candidates = index.get(nom, [])
        if postal:
            candidates = [c for c in candidates if postal.group(0) in c.get(_CLE_CODES_POSTAUX, [])] or candidates
    elif postal:
        candidates = [c for c in liste if postal.group(0) in c.get(_CLE_CODES_POSTAUX, [])]
    else:
        candidates = []
    if not candidates:
        return None
    return max(candidates, key=lambda commune: (commune.get(_CLE_POPULATION, 0), commune[_CLE_CODE]))[_CLE_CODE]
