"""Adaptateur LesJeudis : API JSON interne du site (https://api.lesjeudis.com/api/v1, celle qu'appelle sa page de recherche), sans authentification.

La page HTML ne porte pas la recherche (elle se fait dans le navigateur) : on interroge l'API, qui filtre par mots-clés et par lieu et renvoie la description complète.
Le lieu du profil est converti en identifiant de ville par l'API de géographie du site ; avec un rayon, la recherche part de cette ville.
Seuls les mots-clés, le lieu et le rayon du profil sont gérés ; ancienneté, type de contrat et télétravail sont ignorés.
"""

import json
import logging
import re
from urllib.parse import urlencode

from alertes_emploi import NOM_JOURNAL
from alertes_emploi.modele import Offre
from alertes_emploi.sources.communes import normaliser
from alertes_emploi.sources.commun import ClientHttp, texte_json
from alertes_emploi.sources.registre import SITE_LESJEUDIS

SITE = SITE_LESJEUDIS
OFFRES_PAR_PAGE = 20

_URL_API = "https://api.lesjeudis.com/api/v1"
_URL_OFFRES = f"{_URL_API}/offers"
_URL_VILLES = f"{_URL_API}/taxonomy/geography/search"
_URL_OFFRE = "https://lesjeudis.com/offers/"
_ENTETES = {"User-Agent": "Mozilla/5.0 (compatible; AlertesEmploi)", "Accept": "application/json"}
_CODE_POSTAL = re.compile(r"\b\d{5}\b")
_NOMBRE_VILLES_LUES = 10
_TYPE_VILLE = "city"

# Paramètres de requête de l'API.
_PARAM_MOTS_CLES = "keywords"
_PARAM_PAR_PAGE = "per_page"
_PARAM_PAGE = "page"
_PARAM_LIEU = "location"
_PARAM_CENTRE_RAYON = "radius_center_id"
_PARAM_RAYON = "radius_km"
_PARAM_RECHERCHE_VILLE = "q"
_PARAM_TYPES = "types"
_PARAM_LIMITE = "limit"

# Clés du JSON de l'API.
_CLE_DONNEES = "data"
_CLE_ID = "id"
_CLE_NOM = "name"
_CLE_CODE_POSTAL = "code"
_CLE_TITRE = "title"
_CLE_SLUG = "slug"
_CLE_DESCRIPTION = "description"
_CLE_SOCIETE = "organization"
_CLE_LIEU = "primary_geography"
_CLE_LIEUX = "geographies"
_CLE_COMPETENCES = "skills"

# Critères de l'offre ajoutés à la description : (libellé affiché, clé du JSON).
_CRITERES = (("Contrat", "employment_type"), ("Télétravail", "remote_option"), ("Salaire", "salary_range"), ("Domaine", "occupational_field"))
_SEPARATEUR_ELEMENTS = " ; "
_GABARIT_PUCE = "- **{libelle}** : {valeur}"
_TITRE_CRITERES = "## Critères de l'offre\n"
_TITRE_COMPETENCES = "## Compétences\n"

_journal = logging.getLogger(NOM_JOURNAL)


def _lire(client: ClientHttp, url: str, parametres: dict[str, str | int]) -> object:
    """Réponse JSON de l'API ; ValueError si le corps n'est pas du JSON."""
    return json.loads(client.lire(f"{url}?{urlencode(parametres)}"))


def _id_ville(client: ClientHttp, lieu: str) -> str | None:
    """Identifiant LesJeudis de la ville du lieu (texte avant la première virgule), ou None si introuvable.

    Parmi les villes de même nom, celle dont le code postal est dans le lieu l'emporte, sinon la première renvoyée par le site.
    """
    postal = _CODE_POSTAL.search(lieu)
    nom = normaliser(_CODE_POSTAL.sub(" ", lieu.split(",", 1)[0]))
    if not nom:
        return None
    villes = _lire(client, _URL_VILLES, {_PARAM_RECHERCHE_VILLE: nom, _PARAM_TYPES: _TYPE_VILLE, _PARAM_LIMITE: _NOMBRE_VILLES_LUES})
    if not isinstance(villes, list):
        raise ValueError("réponse inattendue (liste de villes attendue)")
    candidates = [v for v in villes if isinstance(v, dict) and normaliser(texte_json(v.get(_CLE_NOM))) == nom]
    if postal:
        candidates = [v for v in candidates if v.get(_CLE_CODE_POSTAL) == postal.group(0)] or candidates
    return candidates[0].get(_CLE_ID) if candidates else None


def _description(offre: dict) -> str:
    """Description complète : texte de l'offre, puis les sections « Critères de l'offre » et « Compétences » (les champs vides sont omis)."""
    criteres = [_GABARIT_PUCE.format(libelle=libelle, valeur=valeur) for libelle, cle in _CRITERES if (valeur := texte_json(offre.get(cle)))]
    competences = offre.get(_CLE_COMPETENCES)
    noms = [texte_json(c.get(_CLE_NOM)) for c in competences if isinstance(c, dict)] if isinstance(competences, list) else []
    parties = [texte_json(offre.get(_CLE_DESCRIPTION))]
    if criteres:
        parties.append(_TITRE_CRITERES + "\n".join(criteres))
    if noms := list(filter(None, noms)):
        parties.append(_TITRE_COMPETENCES + _SEPARATEUR_ELEMENTS.join(noms))
    return "\n\n".join(filter(None, parties))


def _lieu(offre: dict) -> str:
    """Ville de l'offre : première ville de `geographies`, à défaut le lieu principal."""
    for cle in (_CLE_LIEUX, _CLE_LIEU):
        valeur = offre.get(cle)
        lieux = valeur if isinstance(valeur, list) else [valeur]
        nom = next((texte_json(lieu.get(_CLE_NOM)) for lieu in lieux if isinstance(lieu, dict) and texte_json(lieu.get(_CLE_NOM))), "")
        if nom:
            return nom
    return ""


def _convertir(offre: object) -> Offre | None:
    """Offre du site au format interne ; None si le titre ou le lien manque."""
    if not isinstance(offre, dict):
        return None
    titre, slug = texte_json(offre.get(_CLE_TITRE)), texte_json(offre.get(_CLE_SLUG))
    if not titre or not slug:
        return None
    societe = offre.get(_CLE_SOCIETE)
    nom_societe = texte_json(societe.get(_CLE_NOM)) if isinstance(societe, dict) else ""
    return Offre(titre, nom_societe, _lieu(offre), _URL_OFFRE + slug, _description(offre), SITE)


def _parametres_recherche(client: ClientHttp, termes: str, lieu: str, rayon_km: int) -> dict[str, str | int] | None:
    """Paramètres de la recherche d'offres ; None si le lieu est introuvable (sans lui, on chercherait dans toute la France)."""
    parametres: dict[str, str | int] = {_PARAM_PAR_PAGE: OFFRES_PAR_PAGE}
    if termes.strip():
        parametres[_PARAM_MOTS_CLES] = termes.strip()
    if not lieu.strip():
        return parametres
    ville = _id_ville(client, lieu)
    if ville is None:
        _journal.warning("LesJeudis : ville introuvable pour le lieu « %s », site ignoré.", lieu)
        return None
    if rayon_km > 0:
        parametres.update({_PARAM_CENTRE_RAYON: ville, _PARAM_RAYON: rayon_km})
    else:
        parametres[_PARAM_LIEU] = ville
    return parametres


def rechercher(
    termes: str,
    lieu: str,
    rayon_km: int,
    anciennete_heures: int | None,
    nombre_resultats: int,
    type_contrat: str | None,
    teletravail: bool | None,
) -> list[Offre]:
    """Offres LesJeudis correspondant aux termes, au lieu et au rayon, au plus nombre_resultats (signature commune du registre).

    Un 429 lève SourceBloquee ; une autre erreur réseau ou une réponse inattendue arrête la lecture avec les offres déjà obtenues.
    """
    client = ClientHttp(SITE, _ENTETES)
    offres: list[Offre] = []
    try:
        parametres = _parametres_recherche(client, termes, lieu, rayon_km)
        page = 1
        while parametres is not None and len(offres) < nombre_resultats:
            reponse = _lire(client, _URL_OFFRES, {**parametres, _PARAM_PAGE: page})
            if not isinstance(reponse, dict) or not isinstance(reponse.get(_CLE_DONNEES), list):
                raise ValueError("réponse inattendue (objet avec liste `data` attendu)")
            offres += filter(None, map(_convertir, reponse[_CLE_DONNEES]))
            if len(reponse[_CLE_DONNEES]) < OFFRES_PAR_PAGE:
                break
            page += 1
    except (OSError, ValueError) as erreur:
        _journal.warning("LesJeudis : lecture arrêtée (%s).", erreur)
    return offres[:nombre_resultats]
