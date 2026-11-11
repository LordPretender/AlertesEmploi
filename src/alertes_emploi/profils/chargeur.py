"""Définition et validation des profils de recherche et des paramètres globaux (stockés dans la base, voir stockage.base)."""

from dataclasses import dataclass


class ProfilInvalide(ValueError):
    """Profil de recherche illisible, incomplet ou mal typé."""


@dataclass(frozen=True)
class Profil:
    nom: str
    termes_recherche: tuple[str, ...]
    lieu: str
    rayon_km: int = 50
    sites: tuple[str, ...] = ("indeed",)
    pays_indeed: str = "France"
    type_contrat: str | None = None
    teletravail: bool | None = None
    anciennete_max_heures: int | None = None
    nombre_resultats: int = 15
    exiger_termes: bool = True
    mots_exclus: tuple[str, ...] = ()
    villes_exclues: tuple[str, ...] = ()


# champ -> (type attendu, obligatoire, accepte null)
_CHAMPS = {
    "nom": (str, True, False),
    "termes_recherche": (list, True, False),
    "lieu": (str, True, False),
    "rayon_km": (int, False, False),
    "sites": (list, False, False),
    "pays_indeed": (str, False, False),
    "type_contrat": (str, False, True),
    "teletravail": (bool, False, True),
    "anciennete_max_heures": (int, False, True),
    "nombre_resultats": (int, False, False),
    "exiger_termes": (bool, False, False),
    "mots_exclus": (list, False, False),
    "villes_exclues": (list, False, False),
}

_NOMS_TYPES = {str: "du texte", list: "une liste de textes", int: "un nombre entier", bool: "vrai ou faux"}


def _valider(donnees: dict, source: str) -> dict:
    inconnus = sorted(set(donnees) - set(_CHAMPS))
    if inconnus:
        raise ProfilInvalide(f"{source} : champ(s) inconnu(s) : {', '.join(inconnus)}.")
    manquants = [champ for champ, (_, obligatoire, _) in _CHAMPS.items() if obligatoire and champ not in donnees]
    if manquants:
        raise ProfilInvalide(f"{source} : champ(s) obligatoire(s) manquant(s) : {', '.join(manquants)}.")

    valeurs = {}
    for champ, valeur in donnees.items():
        type_attendu, _, nullable = _CHAMPS[champ]
        if valeur is None and nullable:
            valeurs[champ] = None
            continue
        # bool est un sous-type de int : on refuse true/false pour un champ entier.
        if type_attendu is int and isinstance(valeur, bool):
            valide = False
        else:
            valide = isinstance(valeur, type_attendu)
        if valide and type_attendu is list:
            valide = all(isinstance(element, str) for element in valeur)
        if not valide:
            raise ProfilInvalide(f"{source} : le champ « {champ} » doit être {_NOMS_TYPES[type_attendu]}.")
        valeurs[champ] = tuple(valeur) if type_attendu is list else valeur

    if not valeurs["termes_recherche"]:
        raise ProfilInvalide(f"{source} : « termes_recherche » ne doit pas être vide.")
    if "sites" in valeurs and not valeurs["sites"]:
        raise ProfilInvalide(f"{source} : « sites » ne doit pas être vide.")
    return valeurs


def profil_depuis_donnees(donnees: object, source: str = "Profil") -> Profil:
    """Valide les données d'un profil (objet JSON déjà décodé) et en construit le Profil ; lève ProfilInvalide en cas d'erreur."""
    if not isinstance(donnees, dict):
        raise ProfilInvalide(f"{source} : le profil doit être un objet JSON.")
    valeurs = _valider(donnees, source)
    if not valeurs["nom"].strip():
        raise ProfilInvalide(f"{source} : le nom ne doit pas être vide.")
    return Profil(**valeurs)


@dataclass(frozen=True)
class ParametresGlobaux:
    """Exclusions appliquées à tous les profils, en plus des leurs, priorité des sites pour départager les doublons (le premier est préféré) et réglages de la recherche automatique."""

    mots_exclus: tuple[str, ...] = ()
    villes_exclues: tuple[str, ...] = ()
    priorite_sites: tuple[str, ...] = ("linkedin", "indeed", "hellowork", "glassdoor")
    recherche_auto_demarrage: bool = True
    delai_recherche_auto_minutes: int = 60


_CHAMPS_LISTES_GLOBAUX = ("mots_exclus", "villes_exclues", "priorite_sites")
_CHAMP_AUTO_DEMARRAGE = "recherche_auto_demarrage"
_CHAMP_DELAI_AUTO = "delai_recherche_auto_minutes"
_DELAI_AUTO_MIN, _DELAI_AUTO_MAX = 5, 1440


def _valider_globaux(donnees: dict, source: str) -> ParametresGlobaux:
    inconnus = sorted(set(donnees) - set(_CHAMPS_LISTES_GLOBAUX) - {_CHAMP_AUTO_DEMARRAGE, _CHAMP_DELAI_AUTO})
    if inconnus:
        raise ProfilInvalide(f"{source} : champ(s) inconnu(s) : {', '.join(inconnus)}.")
    valeurs = {}
    for champ, valeur in donnees.items():
        if champ in _CHAMPS_LISTES_GLOBAUX:
            if not isinstance(valeur, list) or not all(isinstance(element, str) for element in valeur):
                raise ProfilInvalide(f"{source} : le champ « {champ} » doit être {_NOMS_TYPES[list]}.")
            valeurs[champ] = tuple(valeur)
        elif champ == _CHAMP_AUTO_DEMARRAGE:
            if not isinstance(valeur, bool):
                raise ProfilInvalide(f"{source} : le champ « {champ} » doit être {_NOMS_TYPES[bool]}.")
            valeurs[champ] = valeur
        else:
            # bool est un sous-type de int : true/false refusés pour le délai.
            if not isinstance(valeur, int) or isinstance(valeur, bool):
                raise ProfilInvalide(f"{source} : le champ « {champ} » doit être {_NOMS_TYPES[int]}.")
            if not _DELAI_AUTO_MIN <= valeur <= _DELAI_AUTO_MAX:
                raise ProfilInvalide(f"{source} : « {champ} » doit être compris entre {_DELAI_AUTO_MIN} et {_DELAI_AUTO_MAX} minutes.")
            valeurs[champ] = valeur
    return ParametresGlobaux(**valeurs)


def globaux_depuis_donnees(donnees: object, source: str = "Paramètres globaux") -> ParametresGlobaux:
    """Valide les paramètres globaux (objet JSON déjà décodé) ; lève ProfilInvalide en cas d'erreur."""
    if not isinstance(donnees, dict):
        raise ProfilInvalide(f"{source} : les paramètres globaux doivent être un objet JSON.")
    return _valider_globaux(donnees, source)
