import json
from urllib.error import HTTPError

import pytest

from alertes_emploi.sources import france_travail
from alertes_emploi.sources.registre import SOURCES_NATIVES, SourceBloquee


class _Reponse:
    def __init__(self, corps: str) -> None:
        self._corps = corps

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._corps.encode("utf-8")


def _annonce(identifiant, titre, societe="ACME -", lieu="13 - MARSEILLE 12", extrait="extrait"):
    return (
        f'<li data-id-offre="{identifiant}" class="result"><a><div class="media-body">'
        f'<h2 data-intitule-offre="{identifiant}"><span class="media-heading-title">{titre}</span></h2>'
        f'<p translate="no" class="subtext">\n{societe}\n <span>{lieu}</span></p>'
        f'<p class="description">{extrait}</p></div></a></li>'
    )


def _page(*annonces: str) -> str:
    return f'<ul class="result-list">{"".join(annonces)}</ul>'


def _detail(descriptif: str, commune: str = "Marseille") -> str:
    return json.dumps({"descriptif": descriptif, "lieuTravail": {"libelleCommuneLieuTravail": commune}})


@pytest.fixture
def reseau(monkeypatch):
    """Faux serveur : routes = {fragment d'URL: corps (str) ou code d'erreur HTTP (int)} ; renvoie la liste des URL demandées."""
    routes: dict[str, str | int] = {}
    demandees: list[str] = []

    def faux_urlopen(requete, timeout):
        url = requete.full_url
        demandees.append(url)
        for fragment, retour in routes.items():
            if fragment in url:
                if isinstance(retour, int):
                    raise HTTPError(url, retour, "erreur", {}, None)
                return _Reponse(retour)
        raise HTTPError(url, 404, "inconnue", {}, None)

    monkeypatch.setattr(france_travail, "urlopen", faux_urlopen)
    monkeypatch.setattr(france_travail.time, "sleep", lambda secondes: None)
    monkeypatch.setattr(france_travail, "code_insee", lambda lieu: "13055" if "marseille" in lieu.lower() else None)
    return routes, demandees


def _rechercher(termes="python", lieu="Marseille", rayon_km=20, nombre_resultats=20):
    return france_travail.rechercher(termes, lieu, rayon_km, None, nombre_resultats, None, None)


def test_extraction_nominale(reseau):
    routes, demandees = reseau
    routes["range=0-19"] = _page(_annonce("AAA1", "Développeur Python (H/F)"))
    routes["offre/AAA1"] = _detail("Description complète avec Python.", commune="Marseille")
    offres = _rechercher()
    assert len(offres) == 1
    offre = offres[0]
    assert (offre.titre, offre.societe, offre.lieu, offre.source) == ("Développeur Python (H/F)", "ACME", "Marseille", "francetravail")
    assert offre.url == "https://candidat.francetravail.fr/offres/recherche/detail/AAA1"
    assert offre.description == "Description complète avec Python."
    assert "lieux=13055" in demandees[0] and "rayon=20" in demandees[0] and "tri=1" in demandees[0] and "motsCles" not in demandees[0]


def test_filtre_sur_titre_et_description_complete(reseau):
    routes, _ = reseau
    routes["range=0-19"] = _page(_annonce("A1", "Chauffeur"), _annonce("A2", "Secrétaire"), _annonce("A3", "Boulanger"))
    routes["offre/A1"] = _detail("Conduite de véhicules.")
    routes["offre/A2"] = _detail("Utilisation de Python pour les tableaux.")
    routes["offre/A3"] = _detail("Pain.")
    assert [o.titre for o in _rechercher()] == ["Secrétaire"]


def test_terme_en_mot_entier_sans_casse_ni_accents(reseau):
    routes, _ = reseau
    routes["range=0-19"] = _page(_annonce("A1", "Responsable Qualité"), _annonce("A2", "Pythonesque"))
    routes["offre/A1"] = _detail("x")
    routes["offre/A2"] = _detail("y")
    assert [o.titre for o in _rechercher(termes="qualite")] == ["Responsable Qualité"]


def test_resultat_vide(reseau):
    routes, demandees = reseau
    routes["range=0-19"] = _page()
    assert _rechercher() == []
    assert len(demandees) == 1


def test_429_sur_la_page_de_resultats_leve_source_bloquee(reseau):
    routes, _ = reseau
    routes["range=0-19"] = 429
    with pytest.raises(SourceBloquee) as erreur:
        _rechercher()
    assert erreur.value.site == "francetravail"


def test_429_sur_un_descriptif_garde_l_extrait_et_n_en_demande_plus(reseau):
    routes, demandees = reseau
    routes["range=0-19"] = _page(_annonce("A1", "Dev", extrait="python un"), _annonce("A2", "Dev", extrait="python deux"))
    routes["offre/A1"] = 429
    offres = _rechercher()
    assert [(o.description, o.lieu) for o in offres] == [("python un", "MARSEILLE 12"), ("python deux", "MARSEILLE 12")]
    assert sum("api-descriptifoffre" in url for url in demandees) == 1


def test_descriptif_illisible_garde_l_extrait(reseau):
    routes, _ = reseau
    routes["range=0-19"] = _page(_annonce("A1", "Dev", extrait="python"))
    routes["offre/A1"] = "pas du json"
    assert [o.description for o in _rechercher()] == ["python"]


@pytest.mark.parametrize("corps", ["[]", "null", '"texte"'])
def test_descriptif_json_non_objet_garde_l_extrait(reseau, corps):
    routes, _ = reseau
    routes["range=0-19"] = _page(_annonce("A1", "Dev", extrait="python"))
    routes["offre/A1"] = corps
    assert [o.description for o in _rechercher()] == ["python"]


def test_pagination_bornee_par_nombre_resultats(reseau):
    routes, demandees = reseau
    routes["range=0-19"] = _page(*(_annonce(f"P{i:02d}", "Dev python") for i in range(20)))
    routes["range=20-39"] = _page(*(_annonce(f"Q{i:02d}", "Dev python") for i in range(20)))
    routes["api-descriptifoffre"] = _detail("python")
    offres = _rechercher(nombre_resultats=25)
    assert len(offres) == 25
    assert sum("recherche?" in url for url in demandees) == 2
    assert not any("Q05" in url for url in demandees)


def test_derniere_page_incomplete_arrete_la_lecture(reseau):
    routes, demandees = reseau
    routes["range=0-19"] = _page(_annonce("A1", "Dev python"))
    routes["offre/A1"] = _detail("python")
    _rechercher(nombre_resultats=100)
    assert sum("recherche?" in url for url in demandees) == 1


def test_rayon_zero_demande_un_kilometre(reseau):
    routes, demandees = reseau
    routes["range=0-19"] = _page()
    _rechercher(rayon_km=0)
    assert "rayon=1&" in demandees[0]


def test_lieu_introuvable_ne_fait_aucune_requete(reseau):
    _, demandees = reseau
    assert _rechercher(lieu="Atlantide") == []
    assert demandees == []


def test_erreur_reseau_sur_la_page_garde_les_offres_deja_lues(reseau):
    routes, _ = reseau
    routes["range=0-19"] = _page(*(_annonce(f"P{i:02d}", "Dev python") for i in range(20)))
    routes["api-descriptifoffre"] = _detail("python")
    routes["range=20-39"] = 500
    assert len(_rechercher(nombre_resultats=40)) == 20


def test_enregistre_au_registre():
    assert "francetravail" in SOURCES_NATIVES
