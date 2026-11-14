"""Adaptateur LesJeudis : API JSON interne du site (https://api.lesjeudis.com/api/v1, celle qu'appelle sa page de recherche), sans authentification.

La page HTML ne porte pas la recherche (elle se fait dans le navigateur) : on interroge l'API, qui filtre par mots-clés et par lieu.
La liste renvoie une description aplatie sur une ligne : le détail de chaque offre (`/offers/<slug>`) fournit la description en Markdown et les autres informations (compétences, expérience, salaire, employeur...), assemblées en sections.
Le lieu du profil est converti en identifiant de ville par l'API de géographie du site ; avec un rayon, la recherche part de cette ville.
Seuls les mots-clés, le lieu et le rayon du profil sont gérés ; ancienneté, type de contrat et télétravail sont ignorés.
"""

import json
import logging
import re
from dataclasses import replace
from urllib.parse import quote, urlencode

from alertes_emploi import NOM_JOURNAL
from alertes_emploi.modele import Offre
from alertes_emploi.sources.communes import normaliser
from alertes_emploi.sources.commun import SEPARATEUR_ELEMENTS, SEPARATEUR_LIGNES, ClientHttp, paragraphes, puce, section, texte_json
from alertes_emploi.sources.registre import SITE_LESJEUDIS, SourceBloquee

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
_CLE_PRINCIPALE = "is_primary"
_CLE_EXIGENCES = "profile_requirements"
_CLE_AVANTAGES = "benefits"
_CLE_CONTRATS = "employment_types"
_CLE_TELETRAVAIL = "remote_options"
_CLE_TEMPS_TRAVAIL = "work_time"
_CLE_DUREE_MOIS = "contract_duration_months"
_CLE_SALAIRE = "salary_range"
_CLE_TJM = "tjm_range"
_CLE_EXPERIENCE_MIN = "years_of_experience_min"
_CLE_EXPERIENCE_MAX = "years_of_experience_max"
_CLE_ETUDES = "education_levels"
_CLE_LANGUES = "languages"
_CLE_DOMAINE = "occupational_field"
_CLE_PUBLIEE = "published_at"
_CLE_EXPIRE = "expires_at"
_CLE_SECTEUR = "industry"
_CLE_TAILLE = "size"
_CLE_SITE = "website"

# Critères de la liste, utilisés si le détail est indisponible : (libellé affiché, clé du JSON).
_CRITERES = (("Contrat", "employment_type"), ("Télétravail", "remote_option"), ("Salaire", "salary_range"), ("Domaine", "occupational_field"))
_TITRE_CRITERES = "Critères de l'offre"
_TITRE_COMPETENCES = "Compétences"
_TITRE_INFOS = "Infos clés"
_TITRE_POSTE = "Description du poste"
_TITRE_EMPLOYEUR = "Employeur"
_TEMPS_TRAVAIL = {"full_time": "Temps plein", "part_time": "Temps partiel"}
_LONGUEUR_DATE = 10  # AAAA-MM-JJ, début de l'horodatage ISO du site

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


def _objets(valeur: object) -> list[dict]:
    """Objets JSON d'une liste ; vide si `valeur` n'est pas une liste (la donnée vient d'un site externe)."""
    return [element for element in valeur if isinstance(element, dict)] if isinstance(valeur, list) else []


def _noms(valeur: object, cle: str = _CLE_NOM) -> list[str]:
    """Textes non vides de la clé `cle` d'une liste d'objets JSON."""
    return [texte for objet in _objets(valeur) if (texte := texte_json(objet.get(cle)))]


def _joindre(valeur: object) -> str:
    """Noms d'une liste d'objets JSON, séparés par « ; »."""
    return SEPARATEUR_ELEMENTS.join(_noms(valeur))


def _puces(criteres: list[tuple[str, str]]) -> list[str]:
    """Puces Markdown des couples (libellé, valeur) dont la valeur n'est pas vide."""
    return [puce(libelle, valeur) for libelle, valeur in criteres if valeur]


def _description(offre: dict) -> str:
    """Description de la liste : texte de l'offre, puis les sections « Critères de l'offre » et « Compétences » (les champs vides sont omis)."""
    criteres = _puces([(libelle, texte_json(offre.get(cle))) for libelle, cle in _CRITERES])
    return paragraphes([texte_json(offre.get(_CLE_DESCRIPTION)), section(_TITRE_CRITERES, criteres), section(_TITRE_COMPETENCES, [_joindre(offre.get(_CLE_COMPETENCES))])])


def _experience(detail: dict) -> str:
    """Expérience demandée, en années (« 3 à 8 ans », « 5 ans minimum »), ou vide."""
    mini, maxi = (valeur if isinstance(valeur := detail.get(cle), int) else None for cle in (_CLE_EXPERIENCE_MIN, _CLE_EXPERIENCE_MAX))
    if mini is not None and maxi is not None:
        return f"{mini} à {maxi} ans"
    if mini is not None:
        return f"{mini} ans minimum"
    return f"{maxi} ans maximum" if maxi is not None else ""


def _duree(detail: dict) -> str:
    """Durée du contrat en mois, ou vide."""
    mois = detail.get(_CLE_DUREE_MOIS)
    return f"{mois} mois" if isinstance(mois, int) and mois > 0 else ""


def _date(detail: dict, cle: str) -> str:
    """Date (AAAA-MM-JJ) d'un horodatage ISO du site, ou vide."""
    return texte_json(detail.get(cle))[:_LONGUEUR_DATE]


def _competences(detail: dict) -> str:
    """Compétences du détail : les principales en gras d'abord, puis les autres."""
    lues = [(texte_json(c.get(_CLE_NOM)), c.get(_CLE_PRINCIPALE) is True) for c in _objets(detail.get(_CLE_COMPETENCES))]
    principales = [f"**{nom}**" for nom, principale in lues if nom and principale]
    return SEPARATEUR_ELEMENTS.join(principales +[nom for nom, principale in lues if nom and not principale])


def _infos_cles(detail: dict) -> list[str]:
    """Puces des informations clés du détail (contrat, durée, télétravail, salaire, expérience...), sans les vides."""
    temps = texte_json(detail.get(_CLE_TEMPS_TRAVAIL))
    return _puces(
        [
            ("Contrat", _joindre(detail.get(_CLE_CONTRATS))),
            ("Durée", _duree(detail)),
            ("Temps de travail", _TEMPS_TRAVAIL.get(temps, temps)),
            ("Télétravail", _joindre(detail.get(_CLE_TELETRAVAIL))),
            ("Salaire", texte_json(detail.get(_CLE_SALAIRE))),
            ("TJM", texte_json(detail.get(_CLE_TJM))),
            ("Expérience", _experience(detail)),
            ("Niveau d'études", _joindre(detail.get(_CLE_ETUDES))),
            ("Langues", _joindre(detail.get(_CLE_LANGUES))),
            ("Domaine", texte_json(detail.get(_CLE_DOMAINE))),
            ("Publiée le", _date(detail, _CLE_PUBLIEE)),
            ("Expire le", _date(detail, _CLE_EXPIRE)),
        ]
    )


def _employeur(detail: dict) -> str:
    """Corps de la section employeur : puces (société, secteur, taille, site) puis présentation de la société."""
    societe = detail.get(_CLE_SOCIETE)
    societe = societe if isinstance(societe, dict) else {}
    puces = _puces(
        [
            ("Société", texte_json(societe.get(_CLE_NOM))),
            ("Secteur", texte_json(societe.get(_CLE_SECTEUR))),
            ("Taille", texte_json(societe.get(_CLE_TAILLE))),
            ("Site", texte_json(societe.get(_CLE_SITE))),
        ]
    )
    return paragraphes([SEPARATEUR_LIGNES.join(puces), texte_json(societe.get(_CLE_DESCRIPTION))])


def _description_detail(detail: dict) -> str:
    """Description issue du détail : « Infos clés », « Compétences », « Description du poste » (texte, exigences, avantages) et « Employeur » ; les sections vides sont omises."""
    poste = paragraphes(texte_json(detail.get(cle)) for cle in (_CLE_DESCRIPTION, _CLE_EXIGENCES, _CLE_AVANTAGES))
    return paragraphes(
        [
            section(_TITRE_INFOS, _infos_cles(detail)),
            section(_TITRE_COMPETENCES, [_competences(detail)]),
            section(_TITRE_POSTE, [poste]),
            section(_TITRE_EMPLOYEUR, [_employeur(detail)]),
        ]
    )


def _enrichir(client: ClientHttp, offre: Offre, slug: str) -> Offre:
    """Offre dont la description vient du détail ; inchangée si le détail est illisible ou sans contenu. Un 429 (SourceBloquee) n'est pas intercepté ici."""
    try:
        detail = json.loads(client.lire(f"{_URL_OFFRES}/{quote(slug, safe='')}"))
    except (OSError, ValueError) as erreur:
        _journal.warning("LesJeudis : détail illisible pour %s (%s), description de la liste conservée.", slug, erreur)
        return offre
    description = _description_detail(detail) if isinstance(detail, dict) else ""
    return replace(offre, description=description) if description else offre


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


def _convertir_page(client: ClientHttp, brutes: list, restantes: int, avec_detail: bool) -> tuple[list[Offre], bool]:
    """Au plus `restantes` offres de la page, décrites par leur détail si `avec_detail` ; renvoie aussi si le détail reste utilisable (faux après un 429 : les suivantes gardent la description de la liste)."""
    offres: list[Offre] = []
    for brute in brutes:
        if len(offres) >= restantes:
            break
        if (offre := _convertir(brute)) is None:
            continue
        if avec_detail:
            try:
                offre = _enrichir(client, offre, texte_json(brute.get(_CLE_SLUG)))
            except SourceBloquee:
                _journal.warning("LesJeudis : limite atteinte sur le détail des offres, descriptions de la liste conservées pour les suivantes.")
                avec_detail = False
        offres.append(offre)
    return offres, avec_detail


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
    avec_detail = True
    try:
        parametres = _parametres_recherche(client, termes, lieu, rayon_km)
        page = 1
        while parametres is not None and len(offres) < nombre_resultats:
            reponse = _lire(client, _URL_OFFRES, {**parametres, _PARAM_PAGE: page})
            if not isinstance(reponse, dict) or not isinstance(reponse.get(_CLE_DONNEES), list):
                raise ValueError("réponse inattendue (objet avec liste `data` attendu)")
            lues, avec_detail = _convertir_page(client, reponse[_CLE_DONNEES], nombre_resultats - len(offres), avec_detail)
            offres += lues
            if len(reponse[_CLE_DONNEES]) < OFFRES_PAR_PAGE:
                break
            page += 1
    except (OSError, ValueError) as erreur:
        _journal.warning("LesJeudis : lecture arrêtée (%s).", erreur)
    return offres[:nombre_resultats]
