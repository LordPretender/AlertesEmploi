"""Commande de recherche : un profil, l'adaptateur JobSpy, une liste d'Offre."""

import argparse
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from alertes_emploi import NOM_JOURNAL, SOURCE_LINKEDIN
from alertes_emploi.export.offres_csv import exporter_csv
from alertes_emploi.filtres.regles import appliquer_regles
from alertes_emploi.modele import Offre, cle_offre, sont_quasi_doublons
from alertes_emploi.profils.chargeur import ParametresGlobaux, Profil, ProfilInvalide
from alertes_emploi.sources import jobspy_adapter
from alertes_emploi.sources.jobspy_adapter import RechercheBloquee, id_offre
from alertes_emploi.stockage.base import BASE_PAR_DEFAUT, DOSSIER_PROFILS, NOUVELLE, VUE, Stockage, rang_source

_journal = logging.getLogger(NOM_JOURNAL)


class RechercheAnnulee(Exception):
    """La recherche a été annulée à la demande de l'utilisateur ; rien n'a été enregistré."""


def rechercher_profil(
    profil: Profil,
    rechercheur: Callable[..., list[Offre]] = jobspy_adapter.rechercher,
    priorite: Sequence[str] = (),
    annulee: Callable[[], bool] | None = None,
    recuperer_descriptions: bool = True,
    ids_connus: frozenset[str] = frozenset(),
) -> list[Offre]:
    """Lance une recherche par terme du profil et renvoie les offres, sans doublon (même titre, société et ville).

    De deux doublons, celui dont la source est la mieux classée dans priorite est gardé ; à rang égal, le premier rencontré.
    ids_connus : transmis au rechercheur ; numéros des offres LinkedIn déjà en base avec leur description, dont la page n'est pas redemandée.
    recuperer_descriptions : transmis au rechercheur ; faux, les offres LinkedIn reviennent sans description (voir jobspy_adapter.rechercher).
    annulee : testée avant chaque terme ; vraie, elle lève RechercheAnnulee (un appel réseau déjà parti n'est pas interrompu).
    """
    offres: dict[str, Offre] = {}
    for terme in profil.termes_recherche:
        if annulee is not None and annulee():
            raise RechercheAnnulee()
        resultats = rechercheur(
            terme,
            profil.lieu,
            sites=profil.sites,
            rayon_km=profil.rayon_km,
            anciennete_heures=profil.anciennete_max_heures,
            nombre_resultats=profil.nombre_resultats,
            pays_indeed=profil.pays_indeed,
            type_contrat=profil.type_contrat,
            teletravail=profil.teletravail,
            recuperer_descriptions=recuperer_descriptions,
            profil=profil.nom,
            ids_connus=ids_connus,
        )
        for offre in resultats:
            doublon = cle_offre(offre) if cle_offre(offre) in offres else next(
                (cle for cle, vue in offres.items() if sont_quasi_doublons(offre, vue)), None
            )
            if doublon is None:
                offres[cle_offre(offre)] = offre
            elif rang_source(offre.source, priorite) < rang_source(offres[doublon].source, priorite):
                del offres[doublon]
                offres[cle_offre(offre)] = offre
    return list(offres.values())


@dataclass(frozen=True)
class ResultatRecherche:
    """Bilan d'une recherche enregistrée : offres nouvelles, nombre de retenues et d'écartées."""

    nouvelles: list[Offre]
    nb_retenues: int
    nb_ecartees: int


def _completer_descriptions(
    profil: Profil,
    base: Path,
    globaux: ParametresGlobaux,
    recuperateur: Callable[[str], jobspy_adapter.DescriptionRecuperee],
    annulee: Callable[[], bool] | None = None,
    reprise_en_cours: Callable[[bool], None] | None = None,
) -> tuple[list[Offre], bool]:
    """Récupère la description des offres « manquantes » du profil, une à une, et s'arrête au premier 429.

    Renvoie les offres qui deviennent nouvelles, et si la reprise s'est arrêtée sur un 429.
    annulee : testée avant chaque offre ; vraie, elle lève RechercheAnnulee (les descriptions déjà récupérées restent enregistrées).
    reprise_en_cours : appelée avec vrai au début d'une reprise (offres manquantes à reprendre) et avec faux à sa fin, annulée ou non.
    Écrit une ligne au journal (récupérées, arrêt sur un 429 ou annulation, restantes) quand il y avait des offres manquantes à reprendre.
    """
    devenues_nouvelles: list[Offre] = []
    bloquee = annulation = False
    with Stockage(base) as stockage:
        manquantes = stockage.lister_manquantes(profil.nom)
        recuperees = 0
        if manquantes and reprise_en_cours is not None:
            reprise_en_cours(True)
        try:
            for stockee in manquantes:
                if annulee is not None and annulee():
                    annulation = True
                    break
                recuperee = recuperateur(stockee.offre.url)
                if recuperee.bloque:
                    bloquee = True
                    break
                recuperees += 1
                offre = stockage.completer_description(profil, stockee.cle, recuperee.description, globaux)
                if offre is not None:
                    devenues_nouvelles.append(offre)
        finally:
            if manquantes and reprise_en_cours is not None:
                reprise_en_cours(False)
    if manquantes:
        arret = ", arrêt sur une erreur 429" if bloquee else ", annulée" if annulation else ""
        _journal.log(
            logging.WARNING if bloquee else logging.INFO,
            "Reprise des descriptions profil « %s », linkedin : %d récupérée(s)%s, %d restante(s)",
            profil.nom, recuperees, arret, len(manquantes) - recuperees,
        )
    if annulation:
        raise RechercheAnnulee()
    return devenues_nouvelles, bloquee


def _rechercher_avec_descriptions_stockees(
    profil: Profil,
    base: Path,
    priorite: Sequence[str],
    annulee: Callable[[], bool] | None,
    rechercheur: Callable[..., list[Offre]] | None,
    recuperer_descriptions: bool,
) -> list[Offre]:
    """Recherche le profil ; une offre LinkedIn déjà en base avec sa description ne voit pas sa page redemandée et reprend la description stockée.

    LinkedIn bloque les requêtes par des erreurs 429 : on préfère garder la description stockée (quitte à ne plus la rafraîchir)
    plutôt que consommer des requêtes pour des offres déjà complètes.
    """
    with Stockage(base) as stockage:
        stockees = stockage.descriptions_linkedin(profil.nom)
    par_numero = {id_offre(url): description for url, description in stockees.items() if id_offre(url)}
    arguments = {"priorite": priorite, "annulee": annulee, "recuperer_descriptions": recuperer_descriptions, "ids_connus": frozenset(par_numero)}
    if rechercheur is not None:
        arguments["rechercheur"] = rechercheur
    offres = rechercher_profil(profil, **arguments)
    return [
        replace(offre, description=par_numero[id_offre(offre.url)])
        if not offre.description.strip() and offre.source == SOURCE_LINKEDIN and id_offre(offre.url) in par_numero
        else offre
        for offre in offres
    ]


def lancer_recherche(
    profil: Profil,
    base: Path,
    rechercheur: Callable[..., list[Offre]] | None = None,
    globaux: ParametresGlobaux = ParametresGlobaux(),
    annulee: Callable[[], bool] | None = None,
    recuperateur: Callable[[str], jobspy_adapter.DescriptionRecuperee] = jobspy_adapter.recuperer_description_linkedin,
    reprise_en_cours: Callable[[bool], None] | None = None,
) -> ResultatRecherche:
    """Recherche les offres du profil, applique ses règles et les exclusions globales (mots-clés, zone, villes exclues) et les enregistre dans la base.

    Avant la recherche, la description des offres « manquantes » du profil est récupérée (recuperateur), jusqu'au premier 429 ; dans ce cas, la
    recherche est lancée sans récupération de description (les offres LinkedIn reviennent sans description et sont enregistrées « manquantes »).
    Après : une offre sans description n'est pas enregistrée et est signalée au journal avec son lien ; seule exception, une offre dont la page a
    répondu 429 (description_bloquee) est enregistrée « manquante » pour être réessayée à la recherche suivante.
    annulee : voir rechercher_profil, et testée avant chaque offre de la reprise des descriptions ; une recherche annulée n'enregistre pas ses offres
    (les descriptions déjà récupérées par la reprise restent enregistrées). Une annulation pendant la reprise arrête aussi la recherche.
    reprise_en_cours : voir _completer_descriptions.
    """
    priorite = globaux.priorite_sites
    nouvelles, bloquee = _completer_descriptions(profil, base, globaux, recuperateur, annulee, reprise_en_cours)
    offres = _rechercher_avec_descriptions_stockees(profil, base, priorite, annulee, rechercheur, recuperer_descriptions=not bloquee)
    avec_description = [offre for offre in offres if offre.description.strip()]
    sans_description = [offre for offre in offres if not offre.description.strip()]
    manquantes = [offre for offre in sans_description if offre.description_bloquee]
    for offre in sans_description:
        if not offre.description_bloquee:
            _journal.warning("Offre sans description, non enregistrée : %s", offre.url)
    retenues, ecartees = appliquer_regles(avec_description, profil, globaux)
    with Stockage(base) as stockage:
        nouvelles += stockage.enregistrer(profil.nom, retenues, ecartees, priorite=priorite, manquantes=manquantes)
        a_recuperer = len(stockage.lister_manquantes(profil.nom))
    if manquantes:
        _journal.warning(
            "Blocage profil « %s », linkedin : erreur 429 sur des pages d'offre, %d description(s) à récupérer à la prochaine recherche.",
            profil.nom, a_recuperer,
        )
    return ResultatRecherche(nouvelles, len(retenues), len(ecartees))


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Lance la recherche d'un profil et affiche les offres.")
    analyseur.add_argument("profil", help="Nom du profil enregistré dans la base.")
    analyseur.add_argument("--base", type=Path, default=BASE_PAR_DEFAUT, help="Base SQLite des profils et de l'historique des offres.")
    analyseur.add_argument("--csv", type=Path, help="Exporte en CSV toutes les offres retenues du profil (nouvelles et déjà vues).")
    args = analyseur.parse_args(argv)
    try:
        with Stockage(args.base) as stockage:
            stockage.importer_profils_json(args.base.parent / DOSSIER_PROFILS)
            profils = stockage.lister_profils()
            globaux = stockage.charger_globaux()
    except ProfilInvalide as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 2
    if args.profil not in profils:
        print(f"Erreur : profil introuvable : « {args.profil} ».", file=sys.stderr)
        return 2
    profil = profils[args.profil]
    try:
        resultat = lancer_recherche(profil, args.base, globaux=globaux)
    except RechercheBloquee as erreur:
        print(f"Erreur : {erreur}", file=sys.stderr)
        return 3
    if args.csv:
        with Stockage(args.base) as stockage:
            a_exporter = stockage.lister(profil.nom, VUE) + stockage.lister(profil.nom, NOUVELLE)
        print(f"{exporter_csv(a_exporter, args.csv)} offre(s) retenue(s) exportée(s) dans {args.csv}.")
    print(
        f"{len(resultat.nouvelles)} offre(s) nouvelle(s), {resultat.nb_retenues - len(resultat.nouvelles)} déjà vue(s), "
        f"{resultat.nb_ecartees} écartée(s) pour le profil « {profil.nom} »."
    )
    for offre in resultat.nouvelles:
        print(f"- {offre.titre} | {offre.societe} | {offre.lieu} | {offre.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
