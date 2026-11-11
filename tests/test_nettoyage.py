import json

from alertes_emploi.modele import Offre
from alertes_emploi.nettoyage import main
from alertes_emploi.stockage.base import NOUVELLE, Stockage


def _offre(url, source, societe, lieu):
    return Offre(titre="Expert", societe=societe, lieu=lieu, url=url, description="d", source=source)


def _base_avec_doublons(chemin):
    # Deux quasi-doublons dont les clés normalisées diffèrent (sinon la migration à l'ouverture en supprimerait un) ; indeed est le plus ancien.
    with Stockage(chemin) as stockage:
        stockage._ecrire("p", _offre("indeed/1", "indeed", "Mirion", "Lamanon, 13"), NOUVELLE, None, "2026-01-01T00:00:00", cle="cle-a")
        stockage._ecrire("p", _offre("glassdoor/2", "glassdoor", "Mirion Technologies", "Lamanon, France"), NOUVELLE, None, "2026-01-03T00:00:00", cle="cle-b")
        stockage._connexion.commit()


def _urls(chemin):
    with Stockage(chemin) as stockage:
        return [ligne["url"] for ligne in stockage._connexion.execute("SELECT url FROM offres ORDER BY url")]


def test_main_garde_la_source_des_parametres_globaux_en_base(tmp_path):
    base = tmp_path / "a.db"
    _base_avec_doublons(base)
    with Stockage(base) as stockage:
        stockage.enregistrer_globaux({"priorite_sites": ["glassdoor", "indeed"]})
    assert main(["--base", str(base)]) == 0
    assert _urls(base) == ["glassdoor/2"]


def test_main_sans_parametres_globaux_utilise_l_ordre_par_defaut(tmp_path):
    base = tmp_path / "a.db"
    _base_avec_doublons(base)
    assert main(["--base", str(base)]) == 0
    assert _urls(base) == ["indeed/1"]


def test_main_importe_l_ancien_global_json_du_dossier_profils(tmp_path):
    base = tmp_path / "a.db"
    _base_avec_doublons(base)
    (tmp_path / "profils").mkdir()
    (tmp_path / "profils" / "_global.json").write_text(json.dumps({"priorite_sites": ["glassdoor", "indeed"]}), encoding="utf-8")
    assert main(["--base", str(base)]) == 0
    assert _urls(base) == ["glassdoor/2"]
    assert (tmp_path / "profils" / "_global.json.importe").exists()


def test_main_parametres_globaux_illisibles_en_base_arrete_sans_toucher_les_offres(tmp_path, capsys):
    base = tmp_path / "a.db"
    _base_avec_doublons(base)
    with Stockage(base) as stockage:
        stockage._connexion.execute("INSERT INTO parametres (cle, valeur) VALUES ('globaux', '{\"mots_exclus\": \"stage\"}')")
        stockage._connexion.commit()
    assert main(["--base", str(base)]) == 2
    assert "mots_exclus" in capsys.readouterr().err
    assert _urls(base) == ["glassdoor/2", "indeed/1"]
