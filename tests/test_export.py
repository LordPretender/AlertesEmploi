import csv
from datetime import date

from alertes_emploi.export.offres_csv import COLONNES, exporter_csv
from alertes_emploi.modele import Offre


def _lire(chemin):
    with chemin.open(encoding="utf-8-sig", newline="") as fichier:
        return list(csv.reader(fichier, delimiter=";"))


def test_export_entete_lignes_et_caracteres_speciaux(tmp_path):
    chemin = tmp_path / "sortie" / "offres.csv"
    offres = [
        Offre("Dév; \"senior\"", "Société", "Lyon", "u1", "ligne1\nligne2", "indeed"),
        Offre("Autre", "Acme", "Paris", "", "", "linkedin"),
    ]
    assert exporter_csv(offres, chemin) == 2
    lignes = _lire(chemin)
    assert lignes[0] == list(COLONNES)
    assert lignes[1] == ["Dév; \"senior\"", "Société", "Lyon", "u1", "indeed", "ligne1\nligne2"]
    assert lignes[2] == ["Autre", "Acme", "Paris", "", "linkedin", ""]


def test_export_vide_ecrit_l_entete_seule(tmp_path):
    chemin = tmp_path / "offres.csv"
    assert exporter_csv([], chemin) == 0
    assert _lire(chemin) == [list(COLONNES)]
