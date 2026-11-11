"""Adaptateur JobSpy : seul module du projet autorisé à importer jobspy."""

import logging
import re
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass

import pandas as pd
from jobspy import scrape_jobs
from jobspy.linkedin import LinkedIn
from jobspy.linkedin.constant import headers as ENTETES_LINKEDIN
from jobspy.model import ScraperInput, Site
from jobspy.util import RequestsRotating, create_session

from alertes_emploi import NOM_JOURNAL, NOM_JOURNAL_JOBSPY, SOURCE_LINKEDIN
from alertes_emploi.modele import Offre

_journal = logging.getLogger(NOM_JOURNAL)

# Mapping unique : champ d'Offre -> colonne du DataFrame de JobSpy.
MAPPING_COLONNES = {
    "titre": "title",
    "societe": "company",
    "lieu": "location",
    "url": "job_url",
    "description": "description",
    "source": "site",
}

# Paramètres de scrape_jobs que l'adaptateur utilise (vérifiés par le test de contrat).
PARAMETRES_JOBSPY = (
    "site_name",
    "search_term",
    "location",
    "distance",
    "hours_old",
    "results_wanted",
    "country_indeed",
    "job_type",
    "is_remote",
    "fetch_description",
)


class RechercheBloquee(Exception):
    """Un site a bloqué la page de recherche (429) : la recherche est annulée en entier, rien n'est à enregistrer. Un 429 sur la page d'une offre ne la lève pas."""

    def __init__(self, site: str | None = None) -> None:
        nom = site or "un site"
        super().__init__(
            f"Recherche interrompue : {nom} bloque les requêtes (erreur 429, trop de requêtes). "
            "Rien n'a été enregistré : relancez la recherche plus tard."
        )


_MARQUEUR_BLOCAGE = re.compile(r"\b429\b|blocked: use a proxy")
_ID_OFFRE_JOURNAL = re.compile(r"^job (\d+):")
_ID_OFFRE_URL = re.compile(r"/jobs/view/(\d+)")


def id_offre(url: str) -> str | None:
    """Numéro d'une offre LinkedIn d'après son URL (…/jobs/view/<numéro>), ou None."""
    correspondance = _ID_OFFRE_URL.search(url)
    return correspondance.group(1) if correspondance else None


class _DetecteurBlocage(logging.Handler):
    """Mémorise le site du premier message de journal de JobSpy qui signale un blocage et compte les blocages par type d'appel ; n'affiche rien.

    JobSpy préfixe par « job <numéro> : » les blocages d'une page d'offre ; sans ce préfixe, c'est la page de recherche qui est refusée.
    """

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.bloque = False
        self.site: str | None = None
        self.sur_recherche = 0
        self.sur_offres = 0

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage()
        if not _MARQUEUR_BLOCAGE.search(message):
            return
        if _ID_OFFRE_JOURNAL.match(message):
            self.sur_offres += 1
        else:
            self.sur_recherche += 1
        if not self.bloque:
            self.bloque = True
            if record.name.startswith("jobspy."):
                self.site = record.name.split(".")[1]

def _journaliser_blocage(detecteur: _DetecteurBlocage, profil: str) -> None:
    """Écrit au journal de l'application la synthèse d'une recherche bloquée (aucune ligne quand tout s'est bien passé).

    Seule l'erreur 429 sur la page de recherche (qui annule la recherche) est journalisée ici ; celle d'une page d'offre l'est par
    recherche.lancer_recherche, seule à connaître le nombre de descriptions réellement à récupérer en base.
    """
    if not detecteur.sur_recherche:
        return
    nom = f"profil « {profil} », " if profil else ""
    site = detecteur.site or "site inconnu"
    _journal.warning(
        "Blocage %s%s : erreur 429 sur la page de recherche, recherche annulée, aucune offre enregistrée.", nom, site
    )


@contextmanager
def _surveiller_blocage():
    """Branche un détecteur de blocage sur le journal de JobSpy le temps du bloc, puis le retire."""
    detecteur = _DetecteurBlocage()
    journal = logging.getLogger(NOM_JOURNAL_JOBSPY)  # ni niveau ni propagation modifiés : la sortie de log existante reste la même
    journal.addHandler(detecteur)
    try:
        yield detecteur
    finally:
        journal.removeHandler(detecteur)


def _valeur(ligne: pd.Series, colonne: str):
    """Valeur de la colonne, ou None si absente, vide ou NaN."""
    if colonne not in ligne.index:
        return None
    valeur = ligne[colonne]
    if valeur is None or (not isinstance(valeur, (list, tuple)) and pd.isna(valeur)):
        return None
    if isinstance(valeur, str) and not valeur.strip():
        return None
    return valeur


def _en_texte(valeur) -> str:
    """Texte de la valeur ; vide si elle manque (les champs texte d'Offre et les colonnes de la base ne sont jamais NULL)."""
    return "" if valeur is None else str(valeur)


@contextmanager
def _limiter_pages_offre(detecteur: _DetecteurBlocage, ids_connus: frozenset[str] | set[str] = frozenset()):
    """Le temps du bloc, enveloppe LinkedIn._fetch_details : après un 429 sur une page d'offre, les offres suivantes ne sont plus demandées.

    ids_connus : numéros des offres déjà en base avec leur description ; leur page n'est jamais demandée. LinkedIn bloque les requêtes par des
    erreurs 429 : on préfère garder la description stockée (quitte à ne plus la rafraîchir si LinkedIn la modifie) plutôt que de consommer des
    requêtes pour des offres déjà complètes. Elles comptent comme lues (pas de description_bloquee).
    Renvoie l'ensemble des numéros d'offre dont la page a été lue sans 429. Le patch est posé sur la classe (JobSpy crée lui-même ses instances) et toujours restauré.
    """
    originale = LinkedIn._fetch_details
    lues: set[str] = set()

    def enveloppe(self, job_id: str) -> dict:
        if job_id in ids_connus:
            lues.add(job_id)
            return {}
        if detecteur.sur_offres:
            return {}
        details = originale(self, job_id)
        if not detecteur.sur_offres:
            lues.add(job_id)
        return details

    LinkedIn._fetch_details = enveloppe
    try:
        yield lues
    finally:
        LinkedIn._fetch_details = originale


def convertir(jobs: pd.DataFrame, ids_offres_lues: frozenset[str] | set[str] = frozenset()) -> list[Offre]:
    """Convertit le DataFrame de JobSpy en liste d'Offre.

    ids_offres_lues : numéros des offres LinkedIn dont la page a été lue sans 429. Une offre LinkedIn sans description qui n'en fait pas partie est
    marquée description_bloquee (page en 429 ou non demandée) : sa description sera réessayée plus tard.
    """
    offres = []
    for _, ligne in jobs.iterrows():
        brut = {champ: _en_texte(_valeur(ligne, colonne)) for champ, colonne in MAPPING_COLONNES.items()}
        bloquee = brut["source"] == SOURCE_LINKEDIN and not brut["description"] and id_offre(brut["url"]) not in ids_offres_lues
        offres.append(Offre(**brut, description_bloquee=bloquee))
    return offres


@dataclass(frozen=True)
class DescriptionRecuperee:
    """Résultat de la récupération de la description d'une offre LinkedIn.

    bloque : la page de l'offre a répondu 429, à réessayer plus tard ; faux avec une description vide : page lue sans description.
    """

    description: str
    bloque: bool


def recuperer_description_linkedin(url: str) -> DescriptionRecuperee:
    """Récupère la description d'une offre LinkedIn par son URL, via la méthode privée _fetch_details du scraper de JobSpy (couverte par un test de contrat)."""
    identifiant = id_offre(url)
    if identifiant is None:
        return DescriptionRecuperee("", False)
    entree = ScraperInput(site_name=[Site.LINKEDIN], fetch_description=True)
    scraper = LinkedIn()
    scraper.scraper_input = entree
    scraper.session = create_session(RequestsRotating(entree.proxies, clear_cookies=True), entree, ENTETES_LINKEDIN)
    with _surveiller_blocage() as detecteur:
        details = scraper._fetch_details(identifiant)
    return DescriptionRecuperee(_en_texte(details.get("description")), detecteur.bloque)


def rechercher(
    termes: str,
    lieu: str,
    sites: Iterable[str] = ("indeed",),
    rayon_km: int = 50,
    anciennete_heures: int | None = None,
    nombre_resultats: int = 15,
    pays_indeed: str = "France",
    type_contrat: str | None = None,
    teletravail: bool | None = None,
    recuperer_descriptions: bool = True,
    profil: str = "",
    ids_connus: frozenset[str] | set[str] = frozenset(),
) -> list[Offre]:
    """Lance une recherche JobSpy et renvoie les résultats sous forme d'Offre.

    JobSpy ne sait que restreindre au télétravail : seul teletravail=True filtre, False et None ne filtrent pas.
    profil : nom du profil, pour la ligne de blocage du journal.
    ids_connus : numéros des offres LinkedIn déjà en base avec leur description ; leur page d'offre n'est pas demandée (elles reviennent sans description, sans description_bloquee).
    recuperer_descriptions : faux, aucune page d'offre LinkedIn n'est demandée ; les offres LinkedIn reviennent sans description, avec description_bloquee vrai.
    JobSpy n'élève pas d'exception sur un blocage (429) : il le journalise et renvoie les offres déjà récoltées.
    Un 429 sur la page de recherche lève RechercheBloquee et les résultats partiels sont jetés.
    Un 429 sur la page d'une offre ne l'annule pas : l'offre revient sans description, avec description_bloquee vrai, et les pages des offres suivantes ne sont plus demandées.
    """
    with _surveiller_blocage() as detecteur, _limiter_pages_offre(detecteur, ids_connus) as ids_offres_lues:
        jobs = scrape_jobs(
            site_name=list(sites),
            search_term=termes,
            location=lieu,
            distance=rayon_km,
            hours_old=anciennete_heures,
            results_wanted=nombre_resultats,
            country_indeed=pays_indeed,
            job_type=type_contrat,
            is_remote=teletravail is True,
            fetch_description=recuperer_descriptions,  # Seul LinkedIn le lit : une requête de plus par offre pour sa description complète.
        )
    offres = convertir(jobs, ids_offres_lues)
    if detecteur.bloque:
        _journaliser_blocage(detecteur, profil)
        if detecteur.sur_recherche:
            raise RechercheBloquee(detecteur.site)
    return offres
