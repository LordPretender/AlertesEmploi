"""Adaptateur France Travail : pages publiques de recherche (par lieu, sans motsCles, interdit par robots.txt) et descriptif JSON de chaque offre.

Le site ne permet pas de chercher par mots-clés : on lit les nombre_resultats premières offres du lieu (tri par date), puis le filtre par mots-clés se fait ici,
sur le titre et la description complète (https://candidat.francetravail.fr/api-descriptifoffre/offre/<id>, la source qu'utilise la page de détail).
Seuls le lieu et le rayon du profil sont gérés ; ancienneté, type de contrat et télétravail sont ignorés.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from html import unescape
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from alertes_emploi import NOM_JOURNAL
from alertes_emploi.modele import Offre
from alertes_emploi.sources.communes import code_insee, normaliser
from alertes_emploi.sources.registre import SITE_FRANCE_TRAVAIL, SourceBloquee

SITE = SITE_FRANCE_TRAVAIL
OFFRES_PAR_PAGE = 20
PAUSE_SECONDES = 1.0
DELAI_SECONDES = 20

_BASE = "https://candidat.francetravail.fr"
_URL_RECHERCHE = f"{_BASE}/offres/recherche"
_URL_DETAIL = f"{_BASE}/api-descriptifoffre/offre/"
_URL_OFFRE = f"{_BASE}/offres/recherche/detail/"
_ENTETES = {"User-Agent": "Mozilla/5.0 (compatible; AlertesEmploi)"}

# Clés du JSON du descriptif d'offre.
_CLE_DESCRIPTIF = "descriptif"
_CLE_LIEU_TRAVAIL = "lieuTravail"
_CLE_COMMUNE_TRAVAIL = "libelleCommuneLieuTravail"
_CLE_ETABLISSEMENT = "etablissement"
_CLE_EXPERIENCE = "experienceProfil"
_CLE_DUREE_EXPERIENCE = "experience"
_CLE_EXPERIENCE_INDISPENSABLE = "estUneExperienceIndispensable"
_CLE_CONDITIONS = "conditionTravailPoste"
_PREFIXE_LIBELLE = "libelle"
_PREFIXE_INDISPENSABLE = "est"
_SUFFIXE_INDISPENSABLE = "Indispensable"

# Description en sections : (libellé affiché, clé du JSON) ; une clé de liste donne ses éléments séparés par « ; ».
_CRITERES_OFFRE = (
    ("Contrat", "syntheseContrat"),
    ("Salaire", "salaire"),
    ("Horaires", "syntheseHoraire"),
    ("Déplacements", "deplacement"),
    ("Qualification", "qualification"),
    ("Public visé", "publicCible"),
    ("Secteur d'activité", "syntheseSecteurActivite"),
    ("Avantages", "listeAvantages"),
)
_PROFIL_SOUHAITE = (
    ("Formations", "listeFormation"),
    ("Compétences", "listeCompetence"),
    ("Savoir-être", "listeSavoirEtre"),
    ("Langues", "listeLangue"),
    ("Permis", "listePermis"),
)
_EMPLOYEUR = (
    ("Enseigne", "libelleEnseigne"),
    ("Secteur d'activité", "syntheseSecteurActivite"),
    ("Effectif", "effectif"),
    ("Site web", "urlEntreprise"),
)
_CLES_CONDITIONS = (("Conditions de travail", "libelleConditionTravail"), ("Précisions", "commentaireConditionTravail"), ("Type d'horaires", "listeLibelleTypeHoraire"), ("Exercice des horaires", "listeLibelleConditionExerciceHoraire"))
_PRESENTATION = "Présentation"
_CHAMP_PRESENTATION_ENTREPRISE = (_PRESENTATION, "descriptionEntreprise"),
_CHAMP_PRESENTATION_ETABLISSEMENT = (_PRESENTATION, "descriptionEtablissement"),
_NON_DISPONIBLE = "non disponible"
_INDISPENSABLE = " (indispensable)"
_SEPARATEUR_ELEMENTS = " ; "

_BLOC = re.compile(r'<li data-id-offre="(\w+)" class="result">(.*?)</li>', re.S)
_TITRE = re.compile(r'<span class="media-heading-title">(.*?)</span>', re.S)
_SOUS_TEXTE = re.compile(r'<p translate="no" class="subtext">(.*?)<span>(.*?)</span></p>', re.S)
_EXTRAIT = re.compile(r'<p class="description">(.*?)</p>', re.S)
_BALISES = re.compile(r"<[^>]+>")
_SEPARATEUR_FINAL = re.compile(r"\s*-\s*$")
_PREFIXE_DEPARTEMENT = re.compile(r"^\w{2,3} - ")

_journal = logging.getLogger(NOM_JOURNAL)


@dataclass(frozen=True)
class _Annonce:
    """Une offre telle qu'elle figure dans la page de résultats."""

    id: str
    titre: str
    societe: str
    lieu: str
    extrait: str


def _texte(brut: str) -> str:
    """Texte d'un fragment HTML : balises retirées, entités décodées, espaces (insécables compris) réduits."""
    return " ".join(unescape(_BALISES.sub(" ", brut)).split())


def _extraire_annonces(html: str) -> list[_Annonce]:
    """Annonces de la page de résultats ; une annonce sans titre est ignorée."""
    annonces = []
    for identifiant, bloc in _BLOC.findall(html):
        titre = _TITRE.search(bloc)
        if not titre:
            continue
        sous_texte = _SOUS_TEXTE.search(bloc)
        societe = _SEPARATEUR_FINAL.sub("", _texte(sous_texte.group(1))) if sous_texte else ""
        lieu = _PREFIXE_DEPARTEMENT.sub("", _texte(sous_texte.group(2))) if sous_texte else ""
        extrait = _EXTRAIT.search(bloc)
        annonces.append(_Annonce(identifiant, _texte(titre.group(1)), societe, lieu, _texte(extrait.group(1)) if extrait else ""))
    return annonces


def _contient(terme: str, texte: str) -> bool:
    """Vrai si le terme figure en mots entiers dans le texte (sans casse ni accents) ; un terme vide est toujours présent."""
    voulu = normaliser(terme)
    return not voulu or re.search(rf"\b{re.escape(voulu)}\b", normaliser(texte)) is not None


class _Client:
    """Requêtes GET espacées d'une pause (aucune avant la première) ; un 429 lève SourceBloquee."""

    def __init__(self) -> None:
        self._premiere = True
        self.descriptifs_bloques = False

    def lire(self, url: str) -> str:
        if not self._premiere:
            time.sleep(PAUSE_SECONDES)
        self._premiere = False
        try:
            with urlopen(Request(url, headers=_ENTETES), timeout=DELAI_SECONDES) as reponse:
                return reponse.read().decode("utf-8")
        except HTTPError as erreur:
            if erreur.code == 429:
                raise SourceBloquee(SITE) from erreur
            raise


def _texte_json(valeur: object) -> str:
    """Valeur JSON textuelle nettoyée ; vide si absente, d'un autre type ou « Non disponible »."""
    if isinstance(valeur, str) and valeur.strip().lower() != _NON_DISPONIBLE:
        return valeur.strip()
    return ""


def _element(element: object) -> str:
    """Élément d'une liste du descriptif : texte simple, ou objet dont le premier champ `libelle…` est le texte (suffixe si un champ `est…Indispensable` est vrai)."""
    if isinstance(element, dict):
        libelle = next((_texte_json(valeur) for cle, valeur in element.items() if cle.startswith(_PREFIXE_LIBELLE)), "")
        indispensable = any(valeur is True for cle, valeur in element.items() if cle.startswith(_PREFIXE_INDISPENSABLE) and cle.endswith(_SUFFIXE_INDISPENSABLE))
        return libelle + _INDISPENSABLE if libelle and indispensable else libelle
    return _texte_json(element)


def _valeur(valeur: object) -> str:
    """Texte d'une valeur du descriptif : texte, ou liste d'éléments séparés par « ; »."""
    if isinstance(valeur, list):
        return _SEPARATEUR_ELEMENTS.join(filter(None, map(_element, valeur)))
    return _texte_json(valeur)


def _lignes(source: object, champs: tuple[tuple[str, str], ...]) -> list[str]:
    """Lignes « Libellé : valeur » des champs non vides de `source` (un objet JSON) ; aucune si `source` n'en est pas un."""
    if not isinstance(source, dict):
        return []
    return [f"{libelle} : {valeur}" for libelle, cle in champs if (valeur := _valeur(source.get(cle)))]


def _section(titre: str, lignes: list[str]) -> str:
    return f"{titre}\n" + "\n".join(lignes) if lignes else ""


def _description(detail: dict) -> str:
    """Description complète d'une offre : descriptif, puis sections « Critères de l'offre », « Profil souhaité » et « Employeur » (les champs vides sont omis)."""
    criteres = _lignes(detail, _CRITERES_OFFRE) + _lignes(detail.get(_CLE_CONDITIONS), _CLES_CONDITIONS)
    experience = detail.get(_CLE_EXPERIENCE)
    profil = []
    if isinstance(experience, dict) and (duree := _texte_json(experience.get(_CLE_DUREE_EXPERIENCE))):
        profil.append(f"Expérience : {duree}{_INDISPENSABLE if experience.get(_CLE_EXPERIENCE_INDISPENSABLE) is True else ''}")
    profil += _lignes(detail, _PROFIL_SOUHAITE)
    etablissement = detail.get(_CLE_ETABLISSEMENT)
    employeur = _lignes(etablissement, _EMPLOYEUR)
    # Les deux présentations sont souvent identiques : on ne garde qu'un exemplaire.
    presentations = _lignes(detail, _CHAMP_PRESENTATION_ENTREPRISE) + _lignes(etablissement, _CHAMP_PRESENTATION_ETABLISSEMENT)
    employeur += dict.fromkeys(presentations)
    parties = [_texte_json(detail.get(_CLE_DESCRIPTIF)), _section("Critères de l'offre", criteres), _section("Profil souhaité", profil), _section("Employeur", employeur)]
    return "\n\n".join(filter(None, parties))


def _descriptif(client: _Client, annonce: _Annonce) -> tuple[str, str]:
    """Description et commune de l'offre d'après son descriptif JSON ; à défaut (429, réseau, JSON inattendu), l'extrait de la liste et aucune commune.

    Un 429 coupe les descriptifs suivants du client : les offres restantes se contentent de l'extrait.
    """
    if client.descriptifs_bloques:
        return annonce.extrait, ""
    try:
        detail = json.loads(client.lire(_URL_DETAIL + annonce.id))
        if not isinstance(detail, dict):
            raise ValueError("réponse inattendue (objet JSON attendu)")
    except SourceBloquee:
        client.descriptifs_bloques = True
        _journal.warning("France Travail : erreur 429 sur un descriptif, les offres suivantes gardent l'extrait de la liste.")
        return annonce.extrait, ""
    except (OSError, ValueError) as erreur:
        _journal.warning("France Travail : descriptif de l'offre %s illisible (%s), extrait conservé.", annonce.id, erreur)
        return annonce.extrait, ""
    lieu_travail = detail.get(_CLE_LIEU_TRAVAIL)
    commune = lieu_travail.get(_CLE_COMMUNE_TRAVAIL) if isinstance(lieu_travail, dict) else ""
    return _description(detail) or annonce.extrait, commune or ""


def _url_page(code: str, rayon_km: int, debut: int) -> str:
    """URL d'une page de résultats ; le site ne renvoie aucune offre pour rayon=0, d'où le minimum de 1 km."""
    parametres = {"lieux": code, "rayon": max(rayon_km, 1), "tri": 1, "range": f"{debut}-{debut + OFFRES_PAR_PAGE - 1}"}
    return f"{_URL_RECHERCHE}?{urlencode(parametres)}"


def rechercher(
    termes: str,
    lieu: str,
    rayon_km: int,
    anciennete_heures: int | None,
    nombre_resultats: int,
    type_contrat: str | None,
    teletravail: bool | None,
) -> list[Offre]:
    """Offres France Travail du lieu et du rayon dont le titre ou la description contient les termes (signature commune du registre).

    nombre_resultats : offres lues avant le filtre par mots-clés (pas offres renvoyées). Un 429 sur une page de résultats lève SourceBloquee ;
    un 429 sur un descriptif n'annule rien : les offres suivantes se contentent de l'extrait de la liste. Autre erreur réseau : on s'arrête avec les offres déjà lues.
    """
    code = code_insee(lieu)
    if code is None:
        _journal.warning("France Travail : commune introuvable pour le lieu « %s », site ignoré.", lieu)
        return []
    client = _Client()
    offres: list[Offre] = []
    lues = 0
    while lues < nombre_resultats:
        try:
            annonces = _extraire_annonces(client.lire(_url_page(code, rayon_km, lues)))
        except (OSError, ValueError) as erreur:
            _journal.warning("France Travail : page de résultats illisible (%s), lecture arrêtée.", erreur)
            break
        for annonce in annonces[: nombre_resultats - lues]:
            description, commune = _descriptif(client, annonce)
            if _contient(termes, f"{annonce.titre} {description}"):
                offres.append(Offre(annonce.titre, annonce.societe, commune or annonce.lieu, _URL_OFFRE + annonce.id, description, SITE))
        lues += min(len(annonces), nombre_resultats - lues)
        if len(annonces) < OFFRES_PAR_PAGE:
            break
    return offres
