"""Commande de nettoyage : fusionne les quasi-doublons de la base existante."""

import argparse
import sys
from pathlib import Path

from alertes_emploi.profils.chargeur import ProfilInvalide
from alertes_emploi.stockage.base import BASE_PAR_DEFAUT, DOSSIER_PROFILS, Stockage


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Fusionne les quasi-doublons de la base.")
    analyseur.add_argument("--base", type=Path, default=BASE_PAR_DEFAUT, help="Base SQLite de l'historique des offres.")
    analyseur.add_argument("--simuler", action="store_true", help="Affiche le nombre de lignes touchées sans rien écrire.")
    args = analyseur.parse_args(argv)
    with Stockage(args.base) as stockage:
        stockage.importer_profils_json(args.base.parent / DOSSIER_PROFILS)
        try:
            globaux = stockage.charger_globaux()
        except ProfilInvalide as erreur:
            print(f"Erreur : {erreur}", file=sys.stderr)
            return 2
        fusionnees = stockage.nettoyer(simuler=args.simuler, priorite=globaux.priorite_sites)
    mode = "simulation, rien d'écrit" if args.simuler else "base mise à jour"
    print(f"{fusionnees} quasi-doublon(s) fusionné(s) ({mode}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
