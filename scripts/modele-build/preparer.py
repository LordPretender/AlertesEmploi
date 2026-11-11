"""Prépare l'environnement Python de l'application (venv propre à ce poste) : le crée et y installe requirements.txt.

Copié dans systeme/ par scripts/construire-build.ps1. Sans argument : prépare si nécessaire. Importé par le lanceur pour savoir si le venv est à jour.
"""
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

VERSION_REQUISE = (3, 12)
NOM_EMPREINTE = "requirements.installe.txt"
DELAI_INSTALLATION_S = 900


def dossier_venv() -> Path:
    """Le venv vit hors du build (un venv ne se déplace pas) : un par poste."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "AlertesEmploi"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "alertes-emploi"
    return base / "venv"


def python_du_venv(sans_console: bool = False) -> Path:
    venv_dir = dossier_venv()
    if os.name == "nt":
        return venv_dir / "Scripts" / ("pythonw.exe" if sans_console else "python.exe")
    return venv_dir / "bin" / "python"


def a_jour(systeme: Path) -> bool:
    """Vrai si le venv existe et a été installé avec le requirements.txt actuel du build."""
    empreinte = dossier_venv() / NOM_EMPREINTE
    if not python_du_venv().exists() or not empreinte.exists():
        return False
    return empreinte.read_text(encoding="utf-8").strip() == (systeme / "requirements.txt").read_text(encoding="utf-8").strip()


def preparer(systeme: Path) -> int:
    if sys.version_info[:2] != VERSION_REQUISE:
        print(f"Python {VERSION_REQUISE[0]}.{VERSION_REQUISE[1]} est requis (version détectée : {sys.version_info[0]}.{sys.version_info[1]}).")
        print("Installer Python 3.12 depuis https://www.python.org/downloads/ puis relancer.")
        return 1
    exigences = systeme / "requirements.txt"
    print(f"Préparation de l'environnement dans {dossier_venv()} (une seule fois, connexion Internet requise)...")
    try:
        venv.create(dossier_venv(), with_pip=True)
        subprocess.run([str(python_du_venv()), "-m", "pip", "install", "-r", str(exigences)], check=True, timeout=DELAI_INSTALLATION_S)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as erreur:
        print(f"Échec de la préparation : {erreur}")
        return 1
    shutil.copyfile(exigences, dossier_venv() / NOM_EMPREINTE)
    print("Environnement prêt.")
    return 0


if __name__ == "__main__":
    code = preparer(Path(__file__).resolve().parent)
    if code != 0:
        input("Appuyer sur Entrée pour fermer...")
    sys.exit(code)
