"""Export CSV des offres retenues."""

import csv
from collections.abc import Iterable
from pathlib import Path

from alertes_emploi.modele import Offre

COLONNES = ("titre", "societe", "lieu", "url", "source", "description")


def exporter_csv(offres: Iterable[Offre], chemin: Path | str) -> int:
    """Écrit les offres dans un CSV (UTF-8 avec BOM, ouvrable directement dans Excel) et renvoie leur nombre."""
    chemin = Path(chemin)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    nombre = 0
    with chemin.open("w", encoding="utf-8-sig", newline="") as fichier:
        auteur = csv.writer(fichier, delimiter=";")
        auteur.writerow(COLONNES)
        for offre in offres:
            auteur.writerow(
                [
                    offre.titre, offre.societe, offre.lieu, offre.url,
                    offre.source, offre.description,
                ]
            )
            nombre += 1
    return nombre
