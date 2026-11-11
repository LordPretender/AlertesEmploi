"""Fenêtre native (pywebview) qui héberge le serveur de consultation : la fermer arrête le serveur."""

import argparse
import logging
import sys
import threading
from pathlib import Path

import webview

from alertes_emploi import NOM_JOURNAL, NOM_JOURNAL_JOBSPY
from alertes_emploi.interface.serveur import creer_serveur
from alertes_emploi.stockage.base import DOSSIER_PROFILS, Stockage

# Racine du projet : fenetre.py est dans src/alertes_emploi/interface/
RACINE_PROJET = Path(__file__).resolve().parents[3]
DONNEES_PAR_DEFAUT = RACINE_PROJET / "build" / "AlertesEmploi" / "donnees"


def configurer_journal(fichier: Path) -> None:
    """Écrit dans `fichier` les logs de jobspy, les erreurs non gérées et stderr (pythonw n'a pas de console)."""
    gestionnaire = logging.FileHandler(fichier, encoding="utf-8")
    gestionnaire.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s"))
    journal = logging.getLogger(NOM_JOURNAL)
    journal.setLevel(logging.INFO)
    journal.addHandler(gestionnaire)
    logging.getLogger(NOM_JOURNAL_JOBSPY).addHandler(gestionnaire)

    sys.excepthook = lambda type_, valeur, trace: journal.error("Erreur non gérée", exc_info=(type_, valeur, trace))
    threading.excepthook = lambda a: journal.error(
        "Erreur non gérée dans le thread %s", a.thread.name if a.thread else "?", exc_info=(a.exc_type, a.exc_value, a.exc_traceback)
    )
    if sys.stderr is None:
        # Sous pythonw, stderr est None : le fichier doit rester ouvert tant que le processus vit (il remplace stderr), un `with` le fermerait aussitôt ; l'OS le libère à la sortie.
        sys.stderr = open(fichier, "a", encoding="utf-8", buffering=1)


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Ouvre la consultation des offres dans une fenêtre native.")
    analyseur.add_argument("--donnees", type=Path, default=DONNEES_PAR_DEFAUT, help="Dossier de données (contient alertes.db et journal.log).")
    analyseur.add_argument("--base", type=Path, default=None, help="Base SQLite (l'emporte sur --donnees).")
    analyseur.add_argument("--port", type=int, default=8765, help="Port d'écoute sur 127.0.0.1.")
    analyseur.add_argument("--lanceur", type=Path, default=None, help="« Alertes emploi.pyw », cible du raccourci du menu Démarrer (entrée grisée sans lui).")
    args = analyseur.parse_args(argv)

    base = args.base or args.donnees / "alertes.db"
    base.parent.mkdir(parents=True, exist_ok=True)
    configurer_journal(base.parent / "journal.log")

    # Import unique des anciens fichiers de profils (<dossier de la base>/profils) dans la base.
    with Stockage(base) as stockage:
        for message in stockage.importer_profils_json(base.parent / DOSSIER_PROFILS):
            logging.getLogger(NOM_JOURNAL).warning("Import de profil ignoré : %s", message)

    try:
        serveur = creer_serveur(base, args.port, args.lanceur, mise_a_jour_au_demarrage=True)
    except OSError:
        # Port déjà pris (ex. consulter.ps1 tourne) : la fenêtre se connecte au serveur existant
        serveur = None
    else:
        threading.Thread(target=serveur.serve_forever, daemon=True).start()

    webview.create_window("Alertes emploi", f"http://127.0.0.1:{args.port}/", width=1280, height=860, text_select=True)
    try:
        webview.start()
    finally:
        if serveur is not None:
            serveur.shutdown()
            serveur.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
