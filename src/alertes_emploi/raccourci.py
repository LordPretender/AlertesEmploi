"""Raccourci de l'application dans le menu Démarrer (Windows) ou le menu des applications (Linux)."""

import os
import subprocess
from pathlib import Path

NOM = "Alertes emploi"


class RaccourciErreur(Exception):
    """Le raccourci n'a pas pu être créé ou supprimé ; le message est affiché tel quel à l'utilisateur."""


# Les chemins passent par l'environnement et non par le texte de la commande : aucun caractère d'un chemin ne peut y être interprété.
_CREER_WINDOWS = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ALERTES_RACCOURCI);"
    "$s.TargetPath = $env:ALERTES_CIBLE;"
    "$s.WorkingDirectory = $env:ALERTES_DOSSIER;"
    "$s.Description = $env:ALERTES_NOM;"
    "$s.Save()"
)


def _est_windows() -> bool:
    return os.name == "nt"


def chemin_raccourci() -> Path:
    """Emplacement du raccourci : dossier Programmes du menu Démarrer de l'utilisateur, ou ~/.local/share/applications."""
    if _est_windows():
        appdata = os.environ.get("APPDATA")
        if not appdata:
            raise RaccourciErreur("Dossier du menu Démarrer introuvable (variable APPDATA absente).")
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / f"{NOM}.lnk"
    return Path.home() / ".local" / "share" / "applications" / "alertes-emploi.desktop"


def creer(lanceur: Path, executer=subprocess.run) -> str:
    """Crée le raccourci vers `lanceur` (« Alertes emploi.pyw ») et renvoie le message de résultat ; `executer` remplace l'appel système dans les tests."""
    lanceur = Path(lanceur).resolve()
    if not lanceur.is_file():
        raise RaccourciErreur(f"Lanceur introuvable : {lanceur}")
    chemin = chemin_raccourci()
    chemin.parent.mkdir(parents=True, exist_ok=True)
    if _est_windows():
        environnement = dict(
            os.environ,
            ALERTES_RACCOURCI=str(chemin),
            ALERTES_CIBLE=str(lanceur),
            ALERTES_DOSSIER=str(lanceur.parent),
            ALERTES_NOM=NOM,
        )
        try:
            resultat = executer(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _CREER_WINDOWS],
                env=environnement,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as erreur:
            raise RaccourciErreur(f"Création du raccourci impossible : {erreur}") from erreur
        if resultat.returncode != 0:
            raise RaccourciErreur(f"Création du raccourci impossible : {(resultat.stderr or '').strip() or 'erreur PowerShell'}")
    else:
        # Non validé sous Linux : pywebview y exige GTK ou Qt.
        contenu = (
            "[Desktop Entry]\nType=Application\n"
            f"Name={NOM}\n"
            f'Exec=python3 "{lanceur}"\n'
            f'Path={lanceur.parent}\n'
            "Terminal=false\n"
        )
        try:
            chemin.write_text(contenu, encoding="utf-8")
        except OSError as erreur:
            raise RaccourciErreur(f"Création du raccourci impossible : {erreur}") from erreur
    return f"Raccourci créé : {chemin}"


def supprimer() -> str:
    """Supprime le raccourci s'il existe et renvoie le message de résultat (pas encore exposé dans l'interface)."""
    chemin = chemin_raccourci()
    try:
        chemin.unlink()
    except FileNotFoundError:
        return "Aucun raccourci à supprimer."
    except OSError as erreur:
        raise RaccourciErreur(f"Suppression du raccourci impossible : {erreur}") from erreur
    return f"Raccourci supprimé : {chemin}"
