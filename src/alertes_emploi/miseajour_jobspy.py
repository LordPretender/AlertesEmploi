"""Mise à jour de python-jobspy depuis l'application : installation dans le venv du build, retour arrière si pip échoue, requirements du build resynchronisés."""

import logging
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from alertes_emploi import NOM_JOURNAL

PAQUET = "python-jobspy"
NOM_EMPREINTE = "requirements.installe.txt"  # même nom que scripts/modele-build/preparer.py
_VERSION = re.compile(r"\d+(\.\d+){0,3}")
_EPINGLE = re.compile(rf"(?m)^{re.escape(PAQUET)}==(\S+)[ \t]*(?=\r?$)")
_DERNIERE = re.compile(rf"{re.escape(PAQUET)} \(([^)]+)\)")
_DELAI_PIP = 900  # secondes

journal = logging.getLogger(NOM_JOURNAL)

# Exécuteur de commande : (arguments) -> (code de retour, sortie). Injecté dans les tests pour ne jamais appeler pip pour de vrai.
Executeur = Callable[[list[str]], tuple[int, str]]


class ErreurMiseAJour(Exception):
    """Mise à jour impossible ; le message est affiché tel quel à l'utilisateur."""


@dataclass(frozen=True)
class Emplacements:
    python: Path  # interpréteur du venv, utilisé pour lancer pip
    requirements: Path  # systeme/requirements.txt du build
    empreinte: Path  # requirements.installe.txt du venv


def version_valide(version: object) -> bool:
    """Chiffres séparés par des points, rien d'autre : la version est la seule donnée venant de la requête."""
    return isinstance(version, str) and _VERSION.fullmatch(version) is not None


def lire_epingle(texte: str) -> str | None:
    """Version de la ligne `python-jobspy==X` du texte, None si absente."""
    trouve = _EPINGLE.search(texte)
    return trouve.group(1) if trouve else None


def remplacer_epingle(texte: str, version: str) -> str:
    """Texte avec la ligne `python-jobspy==` portée à `version`, le reste intact."""
    return _EPINGLE.sub(f"{PAQUET}=={version}", texte, count=1)


def extraire_derniere_version(sortie: str) -> str | None:
    """Dernière version publiée d'après la sortie de `pip index versions python-jobspy`."""
    trouve = _DERNIERE.search(sortie)
    return trouve.group(1) if trouve and version_valide(trouve.group(1)) else None


def executer(arguments: list[str]) -> tuple[int, str]:
    """Exécuteur réel : lance la commande sans console et renvoie code et sortie."""
    drapeaux = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        fini = subprocess.run(arguments, capture_output=True, text=True, errors="replace", timeout=_DELAI_PIP, creationflags=drapeaux)
    except (OSError, subprocess.TimeoutExpired) as erreur:
        return 1, f"{type(erreur).__name__} : {erreur}"
    return fini.returncode, (fini.stdout or "") + (fini.stderr or "")


def version_installee() -> str | None:
    try:
        return metadata.version(PAQUET)
    except metadata.PackageNotFoundError:
        return None


def detecter_emplacements() -> Emplacements | None:
    """Emplacements du build quand le code tourne depuis lui (systeme/src, avec preparer.py à côté et un venv), None sinon (dépôt de développement)."""
    systeme = Path(__file__).resolve().parents[2]
    requirements = systeme / "requirements.txt"
    if not (systeme / "preparer.py").exists() or not requirements.exists() or sys.prefix == sys.base_prefix:
        return None
    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe":
        python = python.with_name("python.exe")  # pythonw n'a pas de sortie : pip tourne avec python.exe
    return Emplacements(python, requirements, Path(sys.prefix) / NOM_EMPREINTE)


class Jobspy:
    """Mise à jour de python-jobspy ; emplacements, exécuteur et lecture de la version installée sont injectables."""

    def __init__(
        self,
        emplacements: Emplacements | None = None,
        executeur: Executeur = executer,
        lire_installee: Callable[[], str | None] = version_installee,
    ) -> None:
        self.emplacements = emplacements
        self._executeur = executeur
        self._lire_installee = lire_installee

    @classmethod
    def detecter(cls) -> "Jobspy":
        return cls(detecter_emplacements())

    @property
    def disponible(self) -> bool:
        return self.emplacements is not None

    def versions(self) -> tuple[str | None, str | None]:
        """(version installée, dernière version publiée) ; la seconde est None si pip ne la donne pas."""
        derniere = None
        if self.emplacements is not None:
            _, sortie = self._executeur([str(self.emplacements.python), "-m", "pip", "index", "versions", PAQUET])
            derniere = extraire_derniere_version(sortie)
        return self._lire_installee(), derniere

    def mettre_a_jour(self, cible: str) -> str:
        """Installe `cible`, revient à la version précédente si pip échoue ; renvoie le message de réussite, lève ErreurMiseAJour sinon."""
        if self.emplacements is None:
            raise ErreurMiseAJour("Mise à jour indisponible : ouvrez l'application par son lanceur (Alertes emploi.pyw).")
        if not version_valide(cible):
            raise ErreurMiseAJour("Version invalide.")
        precedente = self._lire_installee()
        if precedente == cible:
            return f"JobSpy {cible} est déjà installé."
        python = str(self.emplacements.python)
        journal.info("Mise à jour de %s : %s -> %s", PAQUET, precedente, cible)
        code, sortie = self._executeur([python, "-m", "pip", "install", f"{PAQUET}=={cible}"])
        if code != 0:
            journal.error("Installation de %s==%s échouée :\n%s", PAQUET, cible, sortie)
            raise ErreurMiseAJour(f"Installation de JobSpy {cible} échouée. {self._revenir(python, precedente)}")
        try:
            self._resynchroniser(cible)
        except OSError as erreur:
            journal.error("Requirements non mis à jour après l'installation de %s==%s : %s", PAQUET, cible, erreur)
            raise ErreurMiseAJour(f"JobSpy {cible} est installé mais les fichiers requirements n'ont pas pu être mis à jour ({erreur}) : le prochain lancement recréera l'environnement.") from erreur
        journal.info("%s==%s installé", PAQUET, cible)
        return f"JobSpy {cible} installé : relancez l'application pour l'utiliser."

    def _revenir(self, python: str, precedente: str | None) -> str:
        if precedente is None:
            return "Aucune version précédente connue à restaurer."
        code, sortie = self._executeur([python, "-m", "pip", "install", f"{PAQUET}=={precedente}"])
        if code != 0:
            journal.error("Retour à %s==%s échoué :\n%s", PAQUET, precedente, sortie)
            return f"Le retour à la version {precedente} a échoué : voir le journal."
        journal.info("Retour à %s==%s", PAQUET, precedente)
        return f"Version {precedente} conservée."

    def _resynchroniser(self, version: str) -> None:
        """Porte la ligne python-jobspy== de requirements.txt et de requirements.installe.txt à `version`, pour que le lanceur ne recrée pas le venv."""
        assert self.emplacements is not None
        for fichier in (self.emplacements.requirements, self.emplacements.empreinte):
            if fichier.exists():
                texte = fichier.read_text(encoding="utf-8")
                if lire_epingle(texte) is None:
                    raise OSError(f"ligne {PAQUET}== absente de {fichier.name}")
                with fichier.open("w", encoding="utf-8", newline="") as sortie:
                    sortie.write(remplacer_epingle(texte, version))
