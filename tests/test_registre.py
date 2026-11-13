import pandas as pd
import pytest

from alertes_emploi.modele import Offre
from alertes_emploi.sources import jobspy_adapter, registre
from alertes_emploi.sources.jobspy_adapter import RechercheBloquee, rechercher


def _offre_native(site: str = "faux") -> Offre:
    return Offre(titre="Dev", societe="ACME", lieu="Lyon", url="https://exemple.fr/1", description="", source=site)


@pytest.fixture
def scrape_espion(monkeypatch):
    appels = []

    def faux_scrape_jobs(**kwargs):
        appels.append(kwargs)
        return pd.DataFrame(
            [{"title": "Dev", "company": "X", "location": "Paris", "job_url": "https://indeed.fr/1", "description": "d", "site": "indeed"}]
        )

    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", faux_scrape_jobs)
    return appels


def test_site_natif_seul_est_appele_sans_scrape_jobs(monkeypatch, scrape_espion):
    recus = []

    def faux(*args):
        recus.append(args)
        return [_offre_native()]

    monkeypatch.setitem(registre.SOURCES_NATIVES, "faux", faux)
    offres = rechercher("python", "Lyon", sites=["faux"], rayon_km=20, anciennete_heures=48, nombre_resultats=30, type_contrat="fulltime", teletravail=True)
    assert [o.source for o in offres] == ["faux"]
    assert recus == [("python", "Lyon", 20, 48, 30, "fulltime", True)]
    assert scrape_espion == []


def test_site_jobspy_seul_appelle_scrape_jobs(scrape_espion):
    offres = rechercher("python", "Lyon", sites=["indeed"])
    assert [o.source for o in offres] == ["indeed"]
    assert scrape_espion[0]["site_name"] == ["indeed"]


def test_melange_appelle_les_deux_et_ne_donne_a_jobspy_que_ses_sites(monkeypatch, scrape_espion):
    monkeypatch.setitem(registre.SOURCES_NATIVES, "faux", lambda *args: [_offre_native()])
    offres = rechercher("python", "Lyon", sites=["indeed", "faux"])
    assert sorted(o.source for o in offres) == ["faux", "indeed"]
    assert scrape_espion[0]["site_name"] == ["indeed"]


def test_site_sans_source_est_ignore_sans_appeler_jobspy(scrape_espion):
    assert rechercher("python", "Lyon", sites=["apec"]) == []
    assert scrape_espion == []


def test_site_sans_source_ne_retire_pas_les_autres(scrape_espion):
    offres = rechercher("python", "Lyon", sites=["indeed", "apec"])
    assert [o.source for o in offres] == ["indeed"]
    assert scrape_espion[0]["site_name"] == ["indeed"]


def test_site_natif_bloque_leve_recherche_bloquee(monkeypatch, scrape_espion):
    def bloque(*args):
        raise registre.SourceBloquee("faux")

    monkeypatch.setitem(registre.SOURCES_NATIVES, "faux", bloque)
    with pytest.raises(RechercheBloquee, match="faux"):
        rechercher("python", "Lyon", sites=["indeed", "faux"])
