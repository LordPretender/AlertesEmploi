import json
from urllib.parse import parse_qs, urlparse

import pytest

from alertes_emploi.sources import lesjeudis
from alertes_emploi.sources.registre import SOURCES_NATIVES, SourceBloquee

ID_MARSEILLE = "331ddb0f-4a4e-5d5e-9407-7985e78c9883"


def _ville(identifiant, nom, code):
    return {"id": identifiant, "type": "city", "name": nom, "code": code}


def _villes_marseille():
    return json.dumps([_ville("autre", "Marseille-en-Beauvaisis", "60690"), _ville(ID_MARSEILLE, "Marseille", "13001")])


def _offre(slug, titre="Développeur Python", societe="ACME", ville="Marseille", description="Description complète."):
    return {
        "title": titre,
        "slug": slug,
        "description": description,
        "organization": {"name": societe},
        "geographies": [{"name": ville}],
        "primary_geography": {"name": "Provence-Alpes-Côte d'Azur"},
        "employment_type": "CDI",
        "remote_option": "Hybride",
        "salary_range": None,
        "occupational_field": "Développeur Backend",
        "skills": [{"name": "Python"}, {"name": "Docker"}],
    }


def _page(*offres: dict) -> str:
    return json.dumps({"data": list(offres), "pagination": {"total": len(offres), "page": 1, "per_page": 20, "total_pages": 1}})


def _rechercher(termes="python", lieu="Marseille", rayon_km=0, nombre_resultats=20):
    return lesjeudis.rechercher(termes, lieu, rayon_km, None, nombre_resultats, None, None)


def _parametres(url: str) -> dict[str, str]:
    return {cle: valeurs[0] for cle, valeurs in parse_qs(urlparse(url).query).items()}


def test_enregistre_au_registre():
    assert SOURCES_NATIVES["lesjeudis"] is not None


def test_extraction_nominale(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("dev-python-1"))
    offres = _rechercher()
    assert len(offres) == 1
    offre = offres[0]
    assert (offre.titre, offre.societe, offre.lieu, offre.source) == ("Développeur Python", "ACME", "Marseille", "lesjeudis")
    assert offre.url == "https://lesjeudis.com/offers/dev-python-1"
    assert offre.description.startswith("Description complète.")
    assert "- **Contrat** : CDI" in offre.description
    assert "- **Télétravail** : Hybride" in offre.description
    assert "Salaire" not in offre.description
    assert "## Compétences\nPython ; Docker" in offre.description
    parametres = _parametres(demandees[-1])
    assert (parametres["keywords"], parametres["location"], parametres["page"]) == ("python", ID_MARSEILLE, "1")
    assert "radius_km" not in parametres


def test_rayon_utilise_la_ville_comme_centre(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("a"))
    _rechercher(rayon_km=30)
    parametres = _parametres(demandees[-1])
    assert (parametres["radius_center_id"], parametres["radius_km"]) == (ID_MARSEILLE, "30")
    assert "location" not in parametres


def test_code_postal_depart_les_homonymes(reseau):
    routes, demandees = reseau
    routes["geography/search"] = json.dumps([_ville("a", "Aix", "11111"), _ville("b", "Aix", "22222")])
    routes["/offers?"] = _page(_offre("a"))
    _rechercher(lieu="Aix 22222")
    assert _parametres(demandees[-1])["location"] == "b"


def test_resultat_vide(reseau):
    routes, _ = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page()
    assert _rechercher() == []


def test_ville_introuvable_ignore_le_site(reseau, caplog):
    routes, demandees = reseau
    routes["geography/search"] = "[]"
    assert _rechercher(lieu="Nulle part") == []
    assert len(demandees) == 1
    assert "ville introuvable" in caplog.text


def test_lieu_vide_cherche_partout(reseau):
    routes, demandees = reseau
    routes["/offers?"] = _page(_offre("a"))
    assert len(_rechercher(lieu="")) == 1
    assert "geography" not in demandees[0]
    assert "location" not in _parametres(demandees[0])


def test_blocage_429_sur_la_recherche(reseau):
    routes, _ = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = 429
    with pytest.raises(SourceBloquee) as erreur:
        _rechercher()
    assert erreur.value.site == "lesjeudis"


def test_blocage_429_sur_la_ville(reseau):
    routes, _ = reseau
    routes["geography/search"] = 429
    with pytest.raises(SourceBloquee):
        _rechercher()


def test_pagination_bornee_par_nombre_resultats(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["&page=1"] = _page(*[_offre(f"p1-{i}") for i in range(lesjeudis.OFFRES_PAR_PAGE)])
    routes["&page=2"] = _page(*[_offre(f"p2-{i}") for i in range(lesjeudis.OFFRES_PAR_PAGE)])
    offres = _rechercher(nombre_resultats=lesjeudis.OFFRES_PAR_PAGE + 5)
    assert len(offres) == lesjeudis.OFFRES_PAR_PAGE + 5
    assert sum("/offers?" in url for url in demandees) == 2


def test_arret_si_page_incomplete(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("a"), _offre("b"))
    assert len(_rechercher(nombre_resultats=100)) == 2
    assert sum("/offers?" in url for url in demandees) == 1


def test_erreur_reseau_garde_les_offres_deja_lues(reseau):
    routes, _ = reseau
    routes["geography/search"] = _villes_marseille()
    routes["&page=1"] = _page(*[_offre(f"p1-{i}") for i in range(lesjeudis.OFFRES_PAR_PAGE)])
    routes["&page=2"] = 500
    assert len(_rechercher(nombre_resultats=100)) == lesjeudis.OFFRES_PAR_PAGE


def test_reponse_inattendue_ne_leve_pas(reseau):
    routes, _ = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = json.dumps({"detail": "autre chose"})
    assert _rechercher() == []


def test_offre_sans_titre_ou_lien_ignoree(reseau):
    routes, _ = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("a", titre=""), _offre(""), _offre("ok"))
    assert [o.url for o in _rechercher()] == ["https://lesjeudis.com/offers/ok"]
