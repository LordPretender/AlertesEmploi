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


def _derniere_recherche(demandees: list[str]) -> str:
    return [url for url in demandees if "/offers?" in url][-1]


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
    parametres = _parametres(_derniere_recherche(demandees))
    assert (parametres["keywords"], parametres["location"], parametres["page"]) == ("python", ID_MARSEILLE, "1")
    assert "radius_km" not in parametres


def test_rayon_utilise_la_ville_comme_centre(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("a"))
    _rechercher(rayon_km=30)
    parametres = _parametres(_derniere_recherche(demandees))
    assert (parametres["radius_center_id"], parametres["radius_km"]) == (ID_MARSEILLE, "30")
    assert "location" not in parametres


def test_code_postal_depart_les_homonymes(reseau):
    routes, demandees = reseau
    routes["geography/search"] = json.dumps([_ville("a", "Aix", "11111"), _ville("b", "Aix", "22222")])
    routes["/offers?"] = _page(_offre("a"))
    _rechercher(lieu="Aix 22222")
    assert _parametres(_derniere_recherche(demandees))["location"] == "b"


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


def _detail(**champs):
    detail = {
        "slug": "dev-python-1",
        "description": "### Le poste\n- Missions\n- Équipe",
        "profile_requirements": "### Compétences requises\n- 4 ans de Python",
        "benefits": "### Avantages\n- Télétravail",
        "employment_types": [{"name": "CDI"}],
        "remote_options": [{"name": "Hybride"}],
        "work_time": "full_time",
        "contract_duration_months": None,
        "salary_range": "45 k€ - 55 k€",
        "tjm_range": None,
        "years_of_experience_min": 3,
        "years_of_experience_max": 8,
        "education_levels": [{"name": "Master"}],
        "languages": [{"name": "Français"}, {"name": "Anglais"}],
        "occupational_field": "Développeur Backend",
        "published_at": "2026-10-21T09:36:29.351075+00:00",
        "expires_at": "2026-11-20T09:36:29.351075+00:00",
        "skills": [{"name": "Docker", "is_primary": False}, {"name": "Python", "is_primary": True}],
        "organization": {"name": "ACME", "industry": "Conseil IT", "size": "500+", "website": "https://acme.example", "description": "Cabinet de conseil."},
    }
    detail.update(champs)
    return json.dumps(detail)


def _route_liste(routes):
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("dev-python-1"))


def test_description_issue_du_detail(reseau):
    routes, demandees = reseau
    _route_liste(routes)
    routes["/offers/dev-python-1"] = _detail()
    description = _rechercher()[0].description
    assert demandees[-1].endswith("/offers/dev-python-1")
    assert description.startswith("## Infos clés\n- **Contrat** : CDI\n- **Temps de travail** : Temps plein\n- **Télétravail** : Hybride\n- **Salaire** : 45 k€ - 55 k€\n- **Expérience** : 3 à 8 ans")
    assert "- **Niveau d'études** : Master\n- **Langues** : Français ; Anglais\n- **Domaine** : Développeur Backend" in description
    assert "- **Publiée le** : 2026-10-21\n- **Expire le** : 2026-11-20" in description
    assert "## Compétences\n**Python** ; Docker" in description
    assert "## Description du poste\n### Le poste\n- Missions\n- Équipe\n\n### Compétences requises\n- 4 ans de Python\n\n### Avantages\n- Télétravail" in description
    assert "## Employeur\n- **Société** : ACME\n- **Secteur** : Conseil IT\n- **Taille** : 500+\n- **Site** : https://acme.example\n\nCabinet de conseil." in description
    assert "Description complète." not in description


def test_detail_en_echec_garde_la_description_de_la_liste(reseau):
    routes, _ = reseau
    _route_liste(routes)
    routes["/offers/dev-python-1"] = 500
    description = _rechercher()[0].description
    assert description.startswith("Description complète.")
    assert "## Compétences\nPython ; Docker" in description


def test_detail_non_json_garde_la_description_de_la_liste(reseau):
    routes, _ = reseau
    _route_liste(routes)
    routes["/offers/dev-python-1"] = "pas du json"
    assert _rechercher()[0].description.startswith("Description complète.")


def test_detail_sans_contenu_garde_la_description_de_la_liste(reseau):
    routes, _ = reseau
    _route_liste(routes)
    routes["/offers/dev-python-1"] = json.dumps({"slug": "dev-python-1"})
    assert _rechercher()[0].description.startswith("Description complète.")


def test_sections_vides_omises(reseau):
    routes, _ = reseau
    _route_liste(routes)
    routes["/offers/dev-python-1"] = json.dumps({"description": "Texte seul."})
    assert _rechercher()[0].description == "## Description du poste\nTexte seul."


def test_experience_et_duree_partielles(reseau):
    routes, _ = reseau
    _route_liste(routes)
    routes["/offers/dev-python-1"] = _detail(years_of_experience_max=None, contract_duration_months=6)
    description = _rechercher()[0].description
    assert "- **Durée** : 6 mois" in description
    assert "- **Expérience** : 3 ans minimum" in description


def test_blocage_429_sur_le_detail_arrete_les_details_suivants(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("a"), _offre("b"), _offre("c"))
    routes["/offers/a"] = _detail()
    routes["/offers/b"] = 429
    offres = _rechercher()
    assert [o.url[-1] for o in offres] == ["a", "b", "c"]
    assert offres[0].description.startswith("## Infos clés")
    assert offres[1].description.startswith("Description complète.")
    assert offres[2].description.startswith("Description complète.")
    assert not any(url.endswith("/offers/c") for url in demandees)


def test_detail_limite_au_nombre_de_resultats(reseau):
    routes, demandees = reseau
    routes["geography/search"] = _villes_marseille()
    routes["/offers?"] = _page(_offre("a"), _offre("b"), _offre("c"))
    routes["/offers/"] = _detail()
    assert len(_rechercher(nombre_resultats=2)) == 2
    assert sum("/offers/" in url for url in demandees) == 2
