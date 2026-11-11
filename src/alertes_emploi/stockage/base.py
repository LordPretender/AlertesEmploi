"""Stockage SQLite des offres avec historique (nouvelle, vue, écartée) et déduplication par profil."""

import json
import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from alertes_emploi import SOURCE_LINKEDIN
from alertes_emploi.filtres.regles import MOTIF_SANS_DESCRIPTION, appliquer_regles
from alertes_emploi.modele import Offre, cle_offre, sont_quasi_doublons
from alertes_emploi.profils.chargeur import (
    ParametresGlobaux,
    Profil,
    ProfilInvalide,
    globaux_depuis_donnees,
    profil_depuis_donnees,
)

NOUVELLE = "nouvelle"
VUE = "vue"
A_POSTULER = "a_postuler"
ECARTEE = "ecartee"
REJETEE = "rejetee"
POSTULEE = "postulee"

BASE_PAR_DEFAUT = Path("donnees/alertes.db")  # défaut de --base des commandes en ligne
DOSSIER_PROFILS = "profils"  # sous-dossier de la base : anciens fichiers de profils importés une fois

_SCHEMA = """
CREATE TABLE IF NOT EXISTS offres (
    profil TEXT NOT NULL,
    cle TEXT NOT NULL,
    titre TEXT NOT NULL,
    societe TEXT NOT NULL,
    lieu TEXT NOT NULL,
    url TEXT NOT NULL,
    date_publication TEXT,
    description TEXT NOT NULL,
    source TEXT NOT NULL,
    statut TEXT NOT NULL,
    motif_ecart TEXT,
    premiere_vue TEXT NOT NULL,
    derniere_vue TEXT NOT NULL,
    postule_le TEXT,
    description_manquante INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profil, cle)
)
"""

_SCHEMA_MISES_A_JOUR = """
CREATE TABLE IF NOT EXISTS mises_a_jour (
    profil TEXT PRIMARY KEY,
    date TEXT NOT NULL
)
"""

_SCHEMA_PROFILS = """
CREATE TABLE IF NOT EXISTS profils (
    nom TEXT PRIMARY KEY,
    donnees TEXT NOT NULL
)
"""

_SCHEMA_PARAMETRES = """
CREATE TABLE IF NOT EXISTS parametres (
    cle TEXT PRIMARY KEY,
    valeur TEXT NOT NULL
)
"""

_VERSION_MIGRATIONS = 1  # à incrémenter quand une migration est ajoutée : les bases déjà migrées la rejouent une fois
_CLE_GLOBAUX = "globaux"
_CLE_IMPORT = "import_json"
_CLE_RECHERCHE_AUTO = "derniere_recherche_auto"
_FICHIER_GLOBAL = "_global.json"
_PREFIXE_GABARIT = "exemple"  # les fichiers exemple*.json sont des gabarits, jamais de vrais profils
_SUFFIXE_IMPORTE = ".importe"
_DESCRIPTION_SANS_BLANCS = "TRIM(description, ' ' || char(9, 10, 13))"  # expression SQL : la description sans ses blancs de bord (vide = description blanche)


# Du moins avancé au plus avancé : lors d'une fusion, l'offre gardée reprend le statut le plus avancé.
_ORDRE_STATUTS = (NOUVELLE, VUE, A_POSTULER, REJETEE, POSTULEE)


def rang_source(source: str, priorite: Sequence[str]) -> int:
    """Position de la source dans la liste de priorité (sans casse) ; une source absente vient après toutes celles de la liste."""
    minuscules = [site.lower() for site in priorite]
    return minuscules.index(source.lower()) if source.lower() in minuscules else len(minuscules)


def _cle(offre: Offre) -> str:
    # Même clé que rechercher_profil : titre|société|ville normalisés (une offre publiée sur plusieurs plateformes compte une fois).
    return cle_offre(offre)


def _offre(ligne: sqlite3.Row) -> Offre:
    return Offre(
        titre=ligne["titre"],
        societe=ligne["societe"],
        lieu=ligne["lieu"],
        url=ligne["url"],
        description=ligne["description"],
        source=ligne["source"],
    )


@dataclass(frozen=True)
class OffreStockee:
    """Une offre retenue avec son statut et ses dates de première et dernière apparition."""

    offre: Offre
    statut: str
    premiere_vue: str
    derniere_vue: str
    profil: str = ""
    cle: str = ""
    postule_le: str | None = None


def _offre_stockee(ligne: sqlite3.Row) -> OffreStockee:
    return OffreStockee(_offre(ligne), ligne["statut"], ligne["premiere_vue"], ligne["derniere_vue"], ligne["profil"], ligne["cle"], ligne["postule_le"])


class Stockage:
    def __init__(self, chemin: Path | str):
        chemin = Path(chemin)
        if str(chemin) != ":memory:":
            chemin.parent.mkdir(parents=True, exist_ok=True)
        self._connexion = sqlite3.connect(chemin)
        self._connexion.row_factory = sqlite3.Row
        self._connexion.execute(_SCHEMA)
        self._connexion.execute(_SCHEMA_MISES_A_JOUR)
        self._connexion.execute(_SCHEMA_PROFILS)
        self._connexion.execute(_SCHEMA_PARAMETRES)
        # Les migrations relisent toute la table : une seule fois par base, mémorisée dans le PRAGMA user_version.
        if self._connexion.execute("PRAGMA user_version").fetchone()[0] < _VERSION_MIGRATIONS:
            self._ajouter_colonne_postule()
            self._migrer_descriptions()
            self._migrer_cles()
            self._connexion.execute(f"PRAGMA user_version = {_VERSION_MIGRATIONS}")

    def _donnees_profil(self, nom: str) -> dict:
        ligne = self._connexion.execute("SELECT donnees FROM profils WHERE nom = ?", (nom,)).fetchone()
        if ligne is None:
            raise ProfilInvalide(f"Profil introuvable : « {nom} ».")
        return json.loads(ligne["donnees"])

    def lister_profils(self) -> dict[str, Profil]:
        """Profils enregistrés, par nom, triés sans tenir compte de la casse."""
        lignes = self._connexion.execute("SELECT nom, donnees FROM profils").fetchall()
        profils = {ligne["nom"]: profil_depuis_donnees(json.loads(ligne["donnees"]), ligne["nom"]) for ligne in lignes}
        return dict(sorted(profils.items(), key=lambda element: element[0].casefold()))

    def charger_profil(self, nom: str) -> Profil:
        """Profil de ce nom ; lève ProfilInvalide s'il n'existe pas."""
        return profil_depuis_donnees(self._donnees_profil(nom), nom)

    def creer_profil(self, donnees: dict) -> Profil:
        """Valide puis enregistre un nouveau profil ; refuse de réutiliser un nom déjà pris."""
        profil = profil_depuis_donnees(donnees)
        with self._connexion:
            try:
                self._connexion.execute(
                    "INSERT INTO profils (nom, donnees) VALUES (?, ?)", (profil.nom, json.dumps(donnees, ensure_ascii=False))
                )
            except sqlite3.IntegrityError as erreur:
                raise ProfilInvalide(f"Profil : le nom « {profil.nom} » est déjà utilisé.") from erreur
        return profil

    def modifier_profil(self, nom: str, donnees: dict) -> Profil:
        """Réécrit un profil existant : les champs fournis remplacent ceux enregistrés, les autres sont conservés.

        Un champ « nom » différent renomme le profil dans la même transaction : ses offres et sa date de mise à jour le suivent.
        Le nouveau nom est refusé s'il est déjà pris, y compris par les offres ou la date d'un profil supprimé (collision de clé).
        """
        if not isinstance(donnees, dict):
            raise ProfilInvalide("Profil : le profil doit être un objet JSON.")
        actuelles = self._donnees_profil(nom)
        fusion = {**actuelles, **donnees}
        profil = profil_depuis_donnees(fusion, fusion["nom"] if isinstance(fusion.get("nom"), str) else nom)
        with self._connexion:
            if profil.nom != nom:
                for table, colonne in (("profils", "nom"), ("offres", "profil"), ("mises_a_jour", "profil")):
                    pris = self._connexion.execute(f"SELECT 1 FROM {table} WHERE {colonne} = ? LIMIT 1", (profil.nom,)).fetchone()
                    if pris is not None:
                        raise ProfilInvalide(f"Profil : le nom « {profil.nom} » est déjà utilisé.")
                self._connexion.execute("UPDATE offres SET profil = ? WHERE profil = ?", (profil.nom, nom))
                self._connexion.execute("UPDATE mises_a_jour SET profil = ? WHERE profil = ?", (profil.nom, nom))
            self._connexion.execute(
                "UPDATE profils SET nom = ?, donnees = ? WHERE nom = ?", (profil.nom, json.dumps(fusion, ensure_ascii=False), nom)
            )
        return profil

    def supprimer_profil(self, nom: str) -> int:
        """Supprime le profil, ses offres et sa date de mise à jour dans une transaction ; renvoie le nombre d'offres effacées."""
        with self._connexion:
            curseur = self._connexion.execute("DELETE FROM profils WHERE nom = ?", (nom,))
            if curseur.rowcount == 0:
                raise ProfilInvalide(f"Profil introuvable : « {nom} ».")
            offres = self._connexion.execute("DELETE FROM offres WHERE profil = ?", (nom,)).rowcount
            self._connexion.execute("DELETE FROM mises_a_jour WHERE profil = ?", (nom,))
        return offres

    def charger_globaux(self) -> ParametresGlobaux:
        """Paramètres globaux enregistrés ; valeurs par défaut s'il n'y en a pas."""
        ligne = self._connexion.execute("SELECT valeur FROM parametres WHERE cle = ?", (_CLE_GLOBAUX,)).fetchone()
        return ParametresGlobaux() if ligne is None else globaux_depuis_donnees(json.loads(ligne["valeur"]))

    def enregistrer_globaux(self, donnees: dict) -> None:
        """Valide puis remplace les paramètres globaux."""
        globaux_depuis_donnees(donnees)
        with self._connexion:
            self._connexion.execute(
                "INSERT INTO parametres (cle, valeur) VALUES (?, ?) ON CONFLICT (cle) DO UPDATE SET valeur = excluded.valeur",
                (_CLE_GLOBAUX, json.dumps(donnees, ensure_ascii=False)),
            )

    def importer_profils_json(self, dossier: Path | str) -> list[str]:
        """Importe une seule fois les anciens fichiers JSON (profils et _global.json) du dossier ; renvoie les messages sur les fichiers non importés.

        Chaque fichier traité est renommé en <fichier>.json.importe ; un fichier invalide est signalé et laissé en place.
        Un nom de profil déjà en base n'est pas écrasé. Un marqueur en base empêche tout nouvel import.
        """
        if self._connexion.execute("SELECT 1 FROM parametres WHERE cle = ?", (_CLE_IMPORT,)).fetchone():
            return []
        dossier = Path(dossier)
        messages: list[str] = []
        if dossier.is_dir():
            for fichier in sorted(dossier.glob("*.json")):
                if fichier.name.startswith(_PREFIXE_GABARIT):
                    continue
                try:
                    self._importer_fichier(fichier)
                except ProfilInvalide as erreur:
                    messages.append(str(erreur))
                    continue
                try:
                    fichier.rename(fichier.with_name(fichier.name + _SUFFIXE_IMPORTE))
                except OSError as erreur:
                    messages.append(f"{fichier.name} : renommage impossible ({erreur.strerror}).")
        with self._connexion:
            self._connexion.execute("INSERT INTO parametres (cle, valeur) VALUES (?, ?)", (_CLE_IMPORT, "1"))
        return messages

    def _importer_fichier(self, fichier: Path) -> None:
        try:
            donnees = json.loads(fichier.read_text(encoding="utf-8-sig"))
        except OSError as erreur:
            raise ProfilInvalide(f"{fichier.name} : fichier illisible ({erreur.strerror}).") from erreur
        except json.JSONDecodeError as erreur:
            raise ProfilInvalide(f"{fichier.name} : JSON invalide (ligne {erreur.lineno}, colonne {erreur.colno}).") from erreur
        if fichier.name == _FICHIER_GLOBAL:
            globaux_depuis_donnees(donnees, fichier.name)
            with self._connexion:
                self._connexion.execute(
                    "INSERT OR IGNORE INTO parametres (cle, valeur) VALUES (?, ?)",
                    (_CLE_GLOBAUX, json.dumps(donnees, ensure_ascii=False)),
                )
            return
        profil = profil_depuis_donnees(donnees, fichier.name)
        with self._connexion:
            self._connexion.execute(
                "INSERT OR IGNORE INTO profils (nom, donnees) VALUES (?, ?)", (profil.nom, json.dumps(donnees, ensure_ascii=False))
            )

    def _ajouter_colonne_postule(self) -> None:
        """Ajoute la colonne postule_le aux bases créées avant l'apparition du statut « postulée »."""
        colonnes = {ligne["name"] for ligne in self._connexion.execute("PRAGMA table_info(offres)")}
        if "postule_le" not in colonnes:
            with self._connexion:
                self._connexion.execute("ALTER TABLE offres ADD COLUMN postule_le TEXT")

    def _migrer_descriptions(self) -> None:
        """Ajoute la colonne description_manquante aux bases plus anciennes, puis supprime les offres sans description (hors « manquantes »).

        Une offre déjà en base avec une description est complète ; une offre sans description n'est plus conservée.
        """
        colonnes = {ligne["name"] for ligne in self._connexion.execute("PRAGMA table_info(offres)")}
        with self._connexion:
            if "description_manquante" not in colonnes:
                self._connexion.execute("ALTER TABLE offres ADD COLUMN description_manquante INTEGER NOT NULL DEFAULT 0")
            self._connexion.execute(f"DELETE FROM offres WHERE {_DESCRIPTION_SANS_BLANCS} = '' AND description_manquante = 0")

    def noter_mise_a_jour(self, profil: str, maintenant: datetime | None = None) -> None:
        """Enregistre la date de dernière mise à jour d'un profil, en remplaçant la précédente."""
        horodatage = (maintenant or datetime.now()).isoformat(timespec="seconds")
        with self._connexion:
            self._connexion.execute(
                "INSERT INTO mises_a_jour (profil, date) VALUES (?, ?) ON CONFLICT (profil) DO UPDATE SET date = excluded.date",
                (profil, horodatage),
            )

    def dernieres_mises_a_jour(self) -> dict[str, str]:
        """Date ISO de dernière mise à jour de chaque profil déjà mis à jour."""
        return {ligne["profil"]: ligne["date"] for ligne in self._connexion.execute("SELECT profil, date FROM mises_a_jour")}

    def derniere_recherche_auto(self) -> datetime | None:
        """Date et heure de la dernière recherche réussie ; None si elle est absente ou illisible."""
        ligne = self._connexion.execute("SELECT valeur FROM parametres WHERE cle = ?", (_CLE_RECHERCHE_AUTO,)).fetchone()
        if ligne is None:
            return None
        try:
            return datetime.fromisoformat(ligne["valeur"])
        except (ValueError, TypeError):
            return None

    def noter_recherche_auto(self, maintenant: datetime | None = None, date: datetime | None = None) -> None:
        """Enregistre la date de dernière recherche : `date` si elle est donnée, sinon `maintenant`, sinon l'instant présent."""
        horodatage = (date or maintenant or datetime.now()).isoformat(timespec="seconds")
        with self._connexion:
            self._connexion.execute(
                "INSERT INTO parametres (cle, valeur) VALUES (?, ?) ON CONFLICT (cle) DO UPDATE SET valeur = excluded.valeur",
                (_CLE_RECHERCHE_AUTO, horodatage),
            )

    def effacer_recherche_auto(self) -> None:
        """Efface la date de dernière recherche : la prochaine recherche au démarrage se lancera."""
        with self._connexion:
            self._connexion.execute("DELETE FROM parametres WHERE cle = ?", (_CLE_RECHERCHE_AUTO,))

    def _migrer_cles(self) -> None:
        """Remplace les anciennes clés (URL) par la clé normalisée ; un doublon est supprimé, la plus ancienne offre gagne."""
        with self._connexion:
            lignes = self._connexion.execute("SELECT rowid, * FROM offres ORDER BY premiere_vue, rowid").fetchall()
            for ligne in lignes:
                nouvelle = _cle(_offre(ligne))
                if nouvelle == ligne["cle"]:
                    continue
                occupant = self._connexion.execute(
                    "SELECT 1 FROM offres WHERE profil = ? AND cle = ?", (ligne["profil"], nouvelle)
                ).fetchone()
                if occupant:
                    self._connexion.execute("DELETE FROM offres WHERE rowid = ?", (ligne["rowid"],))
                else:
                    self._connexion.execute("UPDATE offres SET cle = ? WHERE rowid = ?", (nouvelle, ligne["rowid"]))

    def nettoyer(self, simuler: bool = False, priorite: Sequence[str] = ()) -> int:
        """Fusionne les quasi-doublons des offres retenues ; renvoie le nombre de fusionnées.

        De deux quasi-doublons, celui dont la source est la mieux classée dans priorite est conservé (à rang égal, le plus ancien),
        l'autre est supprimé ; le conservé reprend la plus ancienne première apparition et le statut le plus avancé des deux.
        Avec simuler, rien n'est écrit.
        """
        fusionnees = 0
        lignes = self._connexion.execute("SELECT rowid, * FROM offres WHERE statut != ?", (ECARTEE,)).fetchall()
        lignes.sort(key=lambda ligne: (rang_source(ligne["source"], priorite), ligne["premiere_vue"], ligne["rowid"]))
        gardees: dict[str, list[dict]] = {}
        for ligne in lignes:
            offre = _offre(ligne)
            gardee = next((g for g in gardees.get(ligne["profil"], []) if sont_quasi_doublons(offre, g["offre"])), None)
            if gardee is None:
                gardees.setdefault(ligne["profil"], []).append(
                    {"offre": offre, "rowid": ligne["rowid"], "premiere_vue": ligne["premiere_vue"], "statut": ligne["statut"], "postule_le": ligne["postule_le"]}
                )
                continue
            if ligne["premiere_vue"] < gardee["premiere_vue"]:
                gardee["premiere_vue"] = ligne["premiere_vue"]
            if _ORDRE_STATUTS.index(ligne["statut"]) > _ORDRE_STATUTS.index(gardee["statut"]):
                gardee["statut"], gardee["postule_le"] = ligne["statut"], ligne["postule_le"]
            self._connexion.execute(
                "UPDATE offres SET premiere_vue = ?, statut = ?, postule_le = ? WHERE rowid = ?",
                (gardee["premiere_vue"], gardee["statut"], gardee["postule_le"], gardee["rowid"]),
            )
            self._connexion.execute("DELETE FROM offres WHERE rowid = ?", (ligne["rowid"],))
            fusionnees += 1
        if simuler:
            self._connexion.rollback()
        else:
            self._connexion.commit()
        return fusionnees

    def reevaluer(self, profil: Profil, globaux: ParametresGlobaux = ParametresGlobaux()) -> tuple[int, int]:
        """Rejoue les règles actuelles du profil (et les exclusions globales) sur ses offres stockées ; renvoie (réintégrées, écartées).

        Une offre écartée qui passe désormais les règles redevient « nouvelle » ; une offre retenue qui ne les passe
        plus est écartée avec son motif. Rien n'est lu sur le réseau.
        """
        lignes = self._connexion.execute("SELECT rowid, * FROM offres WHERE profil = ?", (profil.nom,)).fetchall()
        motifs: dict[int, str] = {}
        for ligne in lignes:
            _, ecartees = appliquer_regles([_offre(ligne)], profil, globaux)
            motifs[ligne["rowid"]] = ecartees[0][1] if ecartees else ""
        reintegrees = ecartees_ajoutees = 0
        with self._connexion:
            for ligne in lignes:
                motif = motifs[ligne["rowid"]]
                if motif and ligne["statut"] != ECARTEE:
                    ecartees_ajoutees += 1
                elif not motif and ligne["statut"] == ECARTEE:
                    reintegrees += 1
                else:
                    continue
                self._connexion.execute(
                    "UPDATE offres SET statut = ?, motif_ecart = ? WHERE rowid = ?",
                    (ECARTEE if motif else NOUVELLE, motif or None, ligne["rowid"]),
                )
        return reintegrees, ecartees_ajoutees

    def fermer(self) -> None:
        self._connexion.close()

    def __enter__(self) -> "Stockage":
        return self

    def __exit__(self, *_) -> None:
        self.fermer()

    def enregistrer(
        self,
        profil: str,
        retenues: Iterable[Offre],
        ecartees: Iterable[tuple[Offre, str]] = (),
        maintenant: datetime | None = None,
        priorite: Sequence[str] = (),
        manquantes: Iterable[Offre] = (),
    ) -> list[Offre]:
        """Enregistre le résultat d'une exécution et renvoie les offres retenues jamais vues pour ce profil.

        Une offre sans description n'est jamais écrite, sauf celles de `manquantes` (description à récupérer plus tard) :
        elles sont écrites écartées (motif « sans description »), marquées « description manquante », si l'offre est inconnue du profil.

        Une offre déjà stockée garde sa première apparition ; « nouvelle » devient « vue » quand
        elle est revue, et une offre écartée qui est retenue plus tard repasse en « nouvelle ».
        Quand l'offre entrante retombe sur une ligne existante, la source la mieux classée dans priorite
        fournit titre, société, lieu, URL, date et description : une source moins bien classée ne les écrase pas.
        """
        horodatage = (maintenant or datetime.now()).isoformat(timespec="seconds")
        nouvelles: list[Offre] = []
        with self._connexion:
            for offre in retenues:
                if offre.description.strip() and self._enregistrer_retenue(profil, offre, horodatage, priorite):
                    nouvelles.append(offre)
            for offre, motif in ecartees:
                if offre.description.strip():
                    self._ecrire(profil, offre, ECARTEE, motif, horodatage)
            for offre in manquantes:
                cle = self._cle_existante(profil, offre)
                if self._connexion.execute("SELECT 1 FROM offres WHERE profil = ? AND cle = ?", (profil, cle)).fetchone() is None:
                    self._ecrire(profil, offre, ECARTEE, MOTIF_SANS_DESCRIPTION, horodatage, cle, manquante=True)
        return nouvelles

    def _enregistrer_retenue(self, profil: str, offre: Offre, horodatage: str, priorite: Sequence[str]) -> bool:
        """Écrit une offre retenue (avec description) ; renvoie True si elle est nouvelle pour le profil (jamais vue, ou écartée jusque-là)."""
        cle = self._cle_existante(profil, offre)
        existante = self._connexion.execute(
            "SELECT statut, source, description_manquante FROM offres WHERE profil = ? AND cle = ?", (profil, cle)
        ).fetchone()
        if existante is None:
            self._ecrire(profil, offre, NOUVELLE, None, horodatage, cle)
            return True
        rang_entrant, rang_existant = rang_source(offre.source, priorite), rang_source(existante["source"], priorite)
        if existante["statut"] != ECARTEE:
            if rang_entrant < rang_existant:
                self._remplacer_description(profil, cle, offre)
            self._revoir(profil, cle, horodatage)
            return False
        if rang_entrant > rang_existant and not existante["description_manquante"]:
            self._connexion.execute(
                "UPDATE offres SET statut = ?, motif_ecart = NULL, derniere_vue = ? WHERE profil = ? AND cle = ?",
                (NOUVELLE, horodatage, profil, cle),
            )
        else:
            self._ecrire(profil, offre, NOUVELLE, None, horodatage, cle)
        return True

    def lister_manquantes(self, profil: str | None = None) -> list[OffreStockee]:
        """Offres dont la description reste à récupérer (d'un profil, ou de tous), les plus anciennes d'abord ; l'URL sert à la récupérer."""
        requete = "SELECT * FROM offres WHERE description_manquante = 1"
        parametres: list[str] = []
        if profil is not None:
            requete += " AND profil = ?"
            parametres.append(profil)
        lignes = self._connexion.execute(requete + " ORDER BY premiere_vue, rowid", parametres)
        return [_offre_stockee(ligne) for ligne in lignes]

    def descriptions_linkedin(self, profil: str) -> dict[str, str]:
        """Description stockée de chaque offre LinkedIn du profil dont la description est présente, par URL d'offre."""
        lignes = self._connexion.execute(
            f"SELECT url, description FROM offres WHERE profil = ? AND source = ? AND description_manquante = 0 AND {_DESCRIPTION_SANS_BLANCS} != ''",
            (profil, SOURCE_LINKEDIN),
        )
        return {ligne["url"]: ligne["description"] for ligne in lignes}

    def completer_description(
        self, profil: Profil, cle: str, description: str, globaux: ParametresGlobaux = ParametresGlobaux(), maintenant: datetime | None = None
    ) -> Offre | None:
        """Enregistre la description récupérée d'une offre « manquante » et rejoue les règles du profil sur l'offre complète.

        La description remplace celle de la ligne, quel que soit le rang de la source. Retenue, l'offre devient « nouvelle » et est renvoyée ;
        écartée, elle porte le motif du filtre et None est renvoyé. Sans description exploitable ou si l'offre n'est plus « manquante », rien ne change.
        """
        if not description.strip():
            return None
        ligne = self._connexion.execute(
            "SELECT * FROM offres WHERE profil = ? AND cle = ? AND description_manquante = 1", (profil.nom, cle)
        ).fetchone()
        if ligne is None:
            return None
        offre = replace(_offre(ligne), description=description)
        retenues, ecartees = appliquer_regles([offre], profil, globaux)
        horodatage = (maintenant or datetime.now()).isoformat(timespec="seconds")
        with self._connexion:
            self._connexion.execute(
                "UPDATE offres SET description = ?, description_manquante = 0, statut = ?, motif_ecart = ?, derniere_vue = ? WHERE profil = ? AND cle = ?",
                (description, NOUVELLE if retenues else ECARTEE, None if retenues else ecartees[0][1], horodatage, profil.nom, cle),
            )
        return offre if retenues else None

    def lister(self, profil: str, statut: str | None = None) -> list[Offre]:
        """Offres stockées pour un profil, éventuellement d'un seul statut, de la plus ancienne à la plus récente."""
        requete = "SELECT * FROM offres WHERE profil = ?"
        parametres: list[str] = [profil]
        if statut is not None:
            requete += " AND statut = ?"
            parametres.append(statut)
        lignes = self._connexion.execute(requete + " ORDER BY premiere_vue, rowid", parametres)
        return [_offre(ligne) for ligne in lignes]

    def lister_detail(self, profil: str | None = None) -> list[OffreStockee]:
        """Offres retenues (nouvelles, vues, à postuler, rejetées et postulées) d'un profil, ou de tous si profil est None, avec statut et dates, les plus anciennes d'abord."""
        requete = "SELECT * FROM offres WHERE statut IN (?, ?, ?, ?, ?)"
        parametres: list[str] = [NOUVELLE, VUE, A_POSTULER, REJETEE, POSTULEE]
        if profil is not None:
            requete += " AND profil = ?"
            parametres.append(profil)
        lignes = self._connexion.execute(requete + " ORDER BY premiere_vue, rowid", parametres)
        return [_offre_stockee(ligne) for ligne in lignes]

    def postuler(self, profil: str, cle: str, maintenant: date | None = None) -> bool:
        """Passe une offre retenue (nouvelle, vue, à postuler, rejetée ou déjà postulée) en « postulée » avec la date du jour ; renvoie False si elle est inconnue ou écartée."""
        jour = (maintenant or date.today()).isoformat()
        with self._connexion:
            curseur = self._connexion.execute(
                "UPDATE offres SET statut = ?, postule_le = ? WHERE profil = ? AND cle = ? AND statut IN (?, ?, ?, ?, ?)",
                (POSTULEE, jour, profil, cle, NOUVELLE, VUE, A_POSTULER, REJETEE, POSTULEE),
            )
        return curseur.rowcount > 0

    def a_postuler(self, profil: str, cle: str) -> bool:
        """Passe une offre nouvelle ou vue (ou déjà à postuler) en « à postuler » ; renvoie False si elle est inconnue, écartée, rejetée ou postulée."""
        with self._connexion:
            curseur = self._connexion.execute(
                "UPDATE offres SET statut = ? WHERE profil = ? AND cle = ? AND statut IN (?, ?, ?)",
                (A_POSTULER, profil, cle, NOUVELLE, VUE, A_POSTULER),
            )
        return curseur.rowcount > 0

    def rejeter(self, profil: str, cle: str) -> bool:
        """Passe une offre retenue (nouvelle, vue, à postuler ou déjà rejetée) en « rejetée » ; renvoie False si elle est inconnue, écartée ou déjà postulée."""
        with self._connexion:
            curseur = self._connexion.execute(
                "UPDATE offres SET statut = ? WHERE profil = ? AND cle = ? AND statut IN (?, ?, ?, ?)",
                (REJETEE, profil, cle, NOUVELLE, VUE, A_POSTULER, REJETEE),
            )
        return curseur.rowcount > 0

    def retablir(self, profil: str, cle: str) -> bool:
        """Ramène une offre à postuler, rejetée ou postulée à l'état « vue » (date de candidature effacée) ; renvoie False si elle est inconnue ou dans un autre état."""
        with self._connexion:
            curseur = self._connexion.execute(
                "UPDATE offres SET statut = ?, postule_le = NULL WHERE profil = ? AND cle = ? AND statut IN (?, ?, ?)",
                (VUE, profil, cle, A_POSTULER, REJETEE, POSTULEE),
            )
        return curseur.rowcount > 0

    def _remplacer_description(self, profil: str, cle: str, offre: Offre) -> None:
        """Remplace le contenu d'une ligne existante par celui d'une offre mieux classée ; statut et dates ne changent pas."""
        self._connexion.execute(
            "UPDATE offres SET titre = ?, societe = ?, lieu = ?, url = ?, description = ?, source = ? WHERE profil = ? AND cle = ?",
            (
                offre.titre, offre.societe, offre.lieu, offre.url,
                offre.description, offre.source, profil, cle,
            ),
        )

    def _cle_existante(self, profil: str, offre: Offre) -> str:
        """Clé sous laquelle l'offre est déjà stockée : la sienne, sinon celle du plus ancien quasi-doublon du profil, sinon la sienne."""
        propre = _cle(offre)
        if self._connexion.execute("SELECT 1 FROM offres WHERE profil = ? AND cle = ?", (profil, propre)).fetchone():
            return propre
        lignes = self._connexion.execute("SELECT * FROM offres WHERE profil = ? ORDER BY premiere_vue, rowid", (profil,))
        return next((ligne["cle"] for ligne in lignes if sont_quasi_doublons(offre, _offre(ligne))), propre)

    def _revoir(self, profil: str, cle: str, horodatage: str) -> None:
        self._connexion.execute(
            "UPDATE offres SET statut = CASE WHEN statut IN (?, ?, ?) THEN statut ELSE ? END, derniere_vue = ? WHERE profil = ? AND cle = ?",
            (A_POSTULER, REJETEE, POSTULEE, VUE, horodatage, profil, cle),
        )

    def _ecrire(
        self, profil: str, offre: Offre, statut: str, motif: str | None, horodatage: str, cle: str | None = None, manquante: bool = False
    ) -> None:
        # Une offre déjà stockée garde sa première apparition.
        self._connexion.execute(
            """
            INSERT INTO offres (profil, cle, titre, societe, lieu, url, description,
                                source, statut, motif_ecart, premiere_vue, derniere_vue, description_manquante)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (profil, cle) DO UPDATE SET
                titre = excluded.titre, societe = excluded.societe, lieu = excluded.lieu, url = excluded.url,
                description = excluded.description,
                source = excluded.source, statut = excluded.statut, motif_ecart = excluded.motif_ecart,
                derniere_vue = excluded.derniere_vue, description_manquante = excluded.description_manquante
            """,
            (
                profil, cle or _cle(offre), offre.titre, offre.societe, offre.lieu, offre.url,
                offre.description, offre.source, statut, motif, horodatage, horodatage, int(manquante),
            ),
        )
