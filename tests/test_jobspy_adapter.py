import inspect
import logging
from datetime import date

import pandas as pd
import pytest
from jobspy import scrape_jobs
from jobspy.linkedin import LinkedIn

from alertes_emploi import NOM_JOURNAL
from alertes_emploi.modele import Offre
from alertes_emploi.sources import jobspy_adapter
from alertes_emploi.sources.jobspy_adapter import (
    MAPPING_COLONNES,
    PARAMETRES_JOBSPY,
    DescriptionRecuperee,
    RechercheBloquee,
    convertir,
    rechercher,
    recuperer_description_linkedin,
)


def _jobs(**surcharges):
    ligne = {
        "title": "Développeur .NET",
        "company": "Acme",
        "location": "Lyon, Rhône, France",
        "job_url": "https://exemple.fr/offre/1",
        "date_posted": date(2026, 10, 30),
        "description": "Poste en CDI.",
        "site": "indeed",
    }
    ligne.update(surcharges)
    return pd.DataFrame([ligne])


def test_colonnes_jobspy_converties_en_offre():
    assert convertir(_jobs()) == [
        Offre(
            titre="Développeur .NET",
            societe="Acme",
            lieu="Lyon, Rhône, France",
            url="https://exemple.fr/offre/1",
            description="Poste en CDI.",
            source="indeed",
        )
    ]


def test_valeurs_vides_ou_nan_deviennent_texte_vide():
    offre = convertir(_jobs(company=float("nan"), description="  "))[0]
    assert offre.societe == ""
    assert offre.description == ""


def test_colonne_absente_donne_vide_sans_exception():
    jobs = _jobs().drop(columns=["company"])
    offre = convertir(jobs)[0]
    assert offre.societe == ""


def test_date_publication_de_jobspy_est_ignoree():
    assert not hasattr(convertir(_jobs())[0], "date_publication")


def test_dataframe_vide_donne_liste_vide():
    assert convertir(pd.DataFrame(columns=list(MAPPING_COLONNES.values()))) == []


def test_scrape_jobs_expose_les_parametres_utilises():
    parametres = inspect.signature(scrape_jobs).parameters
    assert set(PARAMETRES_JOBSPY) <= set(parametres)


def test_rechercher_transmet_les_parametres_a_jobspy(monkeypatch):
    recus = {}

    def faux_scrape_jobs(**kwargs):
        recus.update(kwargs)
        return _jobs()

    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", faux_scrape_jobs)
    offres = rechercher("développeur .net", "Lyon", sites=["indeed"], rayon_km=30, anciennete_heures=72, nombre_resultats=5)
    assert len(offres) == 1
    assert recus == {
        "site_name": ["indeed"],
        "search_term": "développeur .net",
        "location": "Lyon",
        "distance": 30,
        "hours_old": 72,
        "results_wanted": 5,
        "country_indeed": "France",
        "job_type": None,
        "is_remote": False,
        "fetch_description": True,
    }
    assert set(recus) == set(PARAMETRES_JOBSPY)


def test_rechercher_sans_recuperation_de_description_le_dit_a_jobspy_et_marque_les_offres_linkedin(monkeypatch):
    recus = {}

    def faux_scrape_jobs(**kwargs):
        recus.update(kwargs)
        return _jobs_linkedin()

    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", faux_scrape_jobs)
    offres = rechercher("développeur", "Lyon", sites=["linkedin"], recuperer_descriptions=False)
    assert recus["fetch_description"] is False
    assert [o.description_bloquee for o in offres] == [True, True, False]


def test_blocage_429_journalise_par_jobspy_leve_recherche_bloquee_et_retire_le_handler(monkeypatch):
    journal = logging.getLogger("jobspy")
    avant = list(journal.handlers)

    def scrape_bloque(**kwargs):
        logging.getLogger("jobspy.indeed").error("response status code 429 (blocked: use a proxy)")
        return _jobs()  # résultat partiel renvoyé malgré le blocage : il doit être jeté

    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", scrape_bloque)
    with pytest.raises(RechercheBloquee) as erreur:
        rechercher("développeur", "Lyon")
    assert "indeed" in str(erreur.value) and "429" in str(erreur.value) and "relancez" in str(erreur.value)
    assert journal.handlers == avant


def test_handler_retire_aussi_quand_jobspy_leve_une_exception(monkeypatch):
    journal = logging.getLogger("jobspy")
    avant = list(journal.handlers)

    def scrape_en_echec(**kwargs):
        raise RuntimeError("réseau coupé")

    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", scrape_en_echec)
    with pytest.raises(RuntimeError):
        rechercher("développeur", "Lyon")
    assert journal.handlers == avant


def _scrape_bloque_sur(page_recherche: int, offres: int):
    def scrape(**kwargs):
        journal = logging.getLogger("jobspy.linkedin")
        for _ in range(page_recherche):
            journal.error("response status code 429 (blocked: use a proxy)")
        for numero in range(offres):
            journal.warning("job 44800%d: response status code 429 (blocked: use a proxy)", numero)
        return _jobs()

    return scrape


def test_blocage_ecrit_une_synthese_distinguant_page_de_recherche_et_pages_d_offre(monkeypatch, caplog):
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", _scrape_bloque_sur(1, 3))
    with caplog.at_level(logging.WARNING, logger=NOM_JOURNAL):
        with pytest.raises(RechercheBloquee):
            rechercher("développeur", "Lyon", sites=["linkedin"], profil="Dev")
    synthese = [r.getMessage() for r in caplog.records if r.name == NOM_JOURNAL]
    assert synthese == ["Blocage profil « Dev », linkedin : erreur 429 sur la page de recherche, recherche annulée, aucune offre enregistrée."]


def test_aucune_synthese_sans_blocage(monkeypatch, caplog):
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", lambda **kwargs: _jobs())
    with caplog.at_level(logging.DEBUG, logger=NOM_JOURNAL):
        rechercher("développeur", "Lyon")
    assert [r for r in caplog.records if r.name == NOM_JOURNAL] == []


def _jobs_linkedin():
    return pd.concat(
        [
            _jobs(job_url="https://www.linkedin.com/jobs/view/4480001", description=None, site="linkedin"),
            _jobs(job_url="https://www.linkedin.com/jobs/view/4480002", description=None, site="linkedin"),
            _jobs(job_url="https://www.linkedin.com/jobs/view/4480003", description="Poste en CDI.", site="linkedin"),
        ],
        ignore_index=True,
    )


def _scrape_linkedin_via_fetch_details(monkeypatch, bloquees: set[str]) -> list[str]:
    """Fait lire les trois pages d'offre de _jobs_linkedin par LinkedIn._fetch_details (faux : 429 pour les numéros de bloquees) ; renvoie les numéros réellement demandés."""
    demandes: list[str] = []

    def fausse_fetch_details(self, job_id):
        demandes.append(job_id)
        if job_id in bloquees:
            logging.getLogger("jobspy.linkedin").warning("job %s: response status code 429 (blocked: use a proxy)", job_id)
        return {}

    def scrape(**kwargs):
        scraper = LinkedIn()
        for numero in ("4480001", "4480002", "4480003"):
            scraper._fetch_details(numero)
        return _jobs_linkedin()

    monkeypatch.setattr(LinkedIn, "_fetch_details", fausse_fetch_details)
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", scrape)
    return demandes


def test_429_sur_page_d_offre_n_annule_pas_la_recherche_et_arrete_les_requetes_d_offre(monkeypatch, caplog):
    demandes = _scrape_linkedin_via_fetch_details(monkeypatch, {"4480001"})
    with caplog.at_level(logging.WARNING, logger=NOM_JOURNAL):
        offres = rechercher("développeur", "Lyon", sites=["linkedin"], profil="Dev")
    assert demandes == ["4480001"]
    assert [o.description_bloquee for o in offres] == [True, True, False]
    synthese = [r.getMessage() for r in caplog.records if r.name == NOM_JOURNAL]
    assert synthese == []  # l'erreur 429 d'une page d'offre est journalisée par lancer_recherche, avec le nombre réel de descriptions à récupérer


def test_offre_lue_sans_429_et_sans_description_n_est_pas_marquee(monkeypatch):
    demandes = _scrape_linkedin_via_fetch_details(monkeypatch, set())
    offres = rechercher("développeur", "Lyon", sites=["linkedin"])
    assert demandes == ["4480001", "4480002", "4480003"]
    assert not any(o.description_bloquee for o in offres)


def test_offre_deja_en_base_n_a_pas_sa_page_demandee_et_n_est_pas_marquee(monkeypatch):
    demandes = _scrape_linkedin_via_fetch_details(monkeypatch, set())
    offres = rechercher("développeur", "Lyon", sites=["linkedin"], ids_connus={"4480001"})
    assert demandes == ["4480002", "4480003"]
    assert not any(o.description_bloquee for o in offres)


def test_offre_deja_en_base_ne_declenche_pas_l_arret_apres_un_429_ni_n_en_provoque(monkeypatch):
    demandes = _scrape_linkedin_via_fetch_details(monkeypatch, {"4480002"})
    offres = rechercher("développeur", "Lyon", sites=["linkedin"], ids_connus={"4480001", "4480003"})
    assert demandes == ["4480002"]
    assert [o.description_bloquee for o in offres] == [False, True, False]


def test_offre_linkedin_sans_description_dont_la_page_n_a_pas_ete_demandee_est_marquee(monkeypatch):
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", lambda **kwargs: _jobs_linkedin())
    assert [o.description_bloquee for o in rechercher("développeur", "Lyon", sites=["linkedin"])] == [True, True, False]


def test_offre_d_un_autre_site_sans_description_n_est_jamais_marquee(monkeypatch):
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", lambda **kwargs: _jobs(description=None))
    assert not rechercher("développeur", "Lyon")[0].description_bloquee


def test_fetch_details_d_origine_restaure_apres_la_recherche_meme_en_echec(monkeypatch):
    def fausse_fetch_details(self, job_id):
        return {}

    monkeypatch.setattr(LinkedIn, "_fetch_details", fausse_fetch_details)
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", lambda **kwargs: _jobs())
    rechercher("développeur", "Lyon")
    assert LinkedIn._fetch_details is fausse_fetch_details

    def scrape_en_echec(**kwargs):
        raise RuntimeError("réseau coupé")

    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", scrape_en_echec)
    with pytest.raises(RuntimeError):
        rechercher("développeur", "Lyon")
    assert LinkedIn._fetch_details is fausse_fetch_details


def test_description_bloquee_ne_compte_pas_dans_l_egalite_des_offres():
    assert Offre("a", "b", "c", "d", "e", "f", description_bloquee=True) == Offre("a", "b", "c", "d", "e", "f")


def test_linkedin_expose_la_methode_privee_utilisee_pour_les_descriptions():
    """Contrat avec JobSpy : recuperer_description_linkedin appelle LinkedIn._fetch_details(job_id) ; ce test échoue si JobSpy la renomme ou change sa signature."""
    assert list(inspect.signature(LinkedIn._fetch_details).parameters) == ["self", "job_id"]


class _FausseReponse:
    def __init__(self, statut: int, texte: str = "", url: str = "https://www.linkedin.com/jobs/view/4480001") -> None:
        self.status_code = statut
        self.text = texte
        self.url = url


def _fausse_session(monkeypatch, reponse: _FausseReponse):
    class FausseSession:
        def __init__(self, *args, **kwargs) -> None:
            self.headers = {}

        def get(self, url, **kwargs):
            return reponse

    monkeypatch.setattr(jobspy_adapter, "RequestsRotating", FausseSession)


def test_description_recuperee_par_fetch_details(monkeypatch):
    page = '<html><div class="show-more-less-html__markup"><p>Poste en CDI</p></div></html>'
    _fausse_session(monkeypatch, _FausseReponse(200, page))
    resultat = recuperer_description_linkedin("https://www.linkedin.com/jobs/view/4480001")
    assert resultat == DescriptionRecuperee("Poste en CDI", False)


def test_429_sur_la_recuperation_est_distingue_d_une_description_recuperee(monkeypatch):
    _fausse_session(monkeypatch, _FausseReponse(429))
    journal = logging.getLogger("jobspy")
    avant = list(journal.handlers)
    assert recuperer_description_linkedin("https://www.linkedin.com/jobs/view/4480001") == DescriptionRecuperee("", True)
    assert journal.handlers == avant


def test_page_lue_sans_description_n_est_pas_un_blocage(monkeypatch):
    _fausse_session(monkeypatch, _FausseReponse(200, "<html></html>"))
    assert recuperer_description_linkedin("https://www.linkedin.com/jobs/view/4480001") == DescriptionRecuperee("", False)


def test_url_sans_numero_d_offre_ne_lance_aucune_requete(monkeypatch):
    _fausse_session(monkeypatch, _FausseReponse(429))
    assert recuperer_description_linkedin("https://exemple.fr/offre/1") == DescriptionRecuperee("", False)


@pytest.mark.reseau
def test_recherche_reelle_indeed():
    offres = rechercher("développeur", "Lyon", nombre_resultats=3)
    assert offres
    assert all(isinstance(o, Offre) and o.url for o in offres)


def test_marqueur_de_blocage_ne_confond_pas_un_numero_contenant_429():
    from alertes_emploi.sources.jobspy_adapter import _MARQUEUR_BLOCAGE

    assert _MARQUEUR_BLOCAGE.search("response status code 429")
    assert _MARQUEUR_BLOCAGE.search("blocked: use a proxy")
    assert not _MARQUEUR_BLOCAGE.search("job 4429871 : réponse reçue")
