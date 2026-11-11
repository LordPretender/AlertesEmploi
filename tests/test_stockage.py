import json
import sqlite3
from contextlib import closing
from datetime import date, datetime
from functools import partial

import pytest
from outils import offre_de_test

from alertes_emploi.modele import Offre
from alertes_emploi.profils.chargeur import ParametresGlobaux, Profil, ProfilInvalide
from alertes_emploi.stockage.base import ECARTEE, NOUVELLE, VUE, Stockage

_offre = partial(offre_de_test, description="d")


def _deux_doublons(stockage, source_ancienne, source_recente, statut_ancienne=NOUVELLE, statut_recente=NOUVELLE):
    # Insertion directe de deux quasi-doublons (clés différentes), comme une ancienne version en aurait laissé en base.
    stockage._ecrire("p", _offre("a/1", titre="Expert", source=source_ancienne), statut_ancienne, None, "2026-01-01T00:00:00", cle="cle-a")
    stockage._ecrire("p", _offre("b/2", titre="Expert", source=source_recente), statut_recente, None, "2026-01-03T00:00:00", cle="cle-b")
    stockage._connexion.commit()


def _urls(stockage):
    return [ligne["url"] for ligne in stockage._connexion.execute("SELECT url FROM offres ORDER BY url")]


def _statuts(stockage, profil="p"):
    lignes = stockage._connexion.execute("SELECT url, statut, motif_ecart FROM offres WHERE profil = ? ORDER BY url", (profil,))
    return [tuple(ligne) for ligne in lignes]


def test_mise_a_jour_ecrite_lue_et_remplacee_par_la_plus_recente():
    with Stockage(":memory:") as stockage:
        assert stockage.dernieres_mises_a_jour() == {}
        stockage.noter_mise_a_jour("p", datetime(2026, 11, 1, 10, 0, 0))
        stockage.noter_mise_a_jour("q", datetime(2026, 11, 1, 11, 0, 0))
        assert stockage.dernieres_mises_a_jour() == {"p": "2026-11-01T10:00:00", "q": "2026-11-01T11:00:00"}
        stockage.noter_mise_a_jour("p", datetime(2026, 11, 2, 9, 30, 0))
        assert stockage.dernieres_mises_a_jour()["p"] == "2026-11-02T09:30:00"


def test_derniere_recherche_auto_absente_ecrite_relue_ecrasee_et_effacee():
    with Stockage(":memory:") as stockage:
        assert stockage.derniere_recherche_auto() is None
        stockage.noter_recherche_auto(datetime(2026, 11, 1, 10, 0, 0))
        assert stockage.derniere_recherche_auto() == datetime(2026, 11, 1, 10, 0, 0)
        stockage.noter_recherche_auto(date=datetime(2026, 11, 2, 9, 30, 0))
        assert stockage.derniere_recherche_auto() == datetime(2026, 11, 2, 9, 30, 0)
        stockage.noter_recherche_auto()
        assert (datetime.now() - stockage.derniere_recherche_auto()).total_seconds() < 5
        stockage.effacer_recherche_auto()
        assert stockage.derniere_recherche_auto() is None


def test_derniere_recherche_auto_illisible_est_absente():
    with Stockage(":memory:") as stockage:
        stockage._connexion.execute("INSERT INTO parametres (cle, valeur) VALUES ('derniere_recherche_auto', 'n''importe quoi')")
        assert stockage.derniere_recherche_auto() is None


def test_premiere_execution_toutes_nouvelles():
    with Stockage(":memory:") as stockage:
        nouvelles = stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        assert [o.url for o in nouvelles] == ["u1", "u2"]
        assert _statuts(stockage) == [("u1", NOUVELLE, None), ("u2", NOUVELLE, None)]


def test_deduplication_entre_executions_et_passage_a_vue():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        nouvelles = stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        assert [o.url for o in nouvelles] == ["u2"]
        assert _statuts(stockage) == [("u1", VUE, None), ("u2", NOUVELLE, None)]
        assert stockage.enregistrer("p", [_offre("u1"), _offre("u2")]) == []
        assert _statuts(stockage) == [("u1", VUE, None), ("u2", VUE, None)]


def test_ecartee_garde_son_motif():
    with Stockage(":memory:") as stockage:
        assert stockage.enregistrer("p", [], [(_offre("u1"), "exclu : stage")]) == []
        assert _statuts(stockage) == [("u1", ECARTEE, "exclu : stage")]


def test_ecartee_puis_retenue_repasse_en_nouvelle():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], [(_offre("u1"), "exclu : stage")])
        nouvelles = stockage.enregistrer("p", [_offre("u1")])
        assert [o.url for o in nouvelles] == ["u1"]
        assert _statuts(stockage) == [("u1", NOUVELLE, None)]


def test_offre_absente_n_est_pas_touchee():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        stockage.enregistrer("p", [_offre("u2")])
        assert _statuts(stockage) == [("u1", NOUVELLE, None), ("u2", NOUVELLE, None)]


def test_profils_separes():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("a", [_offre("u1")])
        assert [o.url for o in stockage.enregistrer("b", [_offre("u1")])] == ["u1"]
        assert _statuts(stockage, "a") == _statuts(stockage, "b") == [("u1", NOUVELLE, None)]


def test_cle_de_repli_sans_url():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("")])
        assert stockage.enregistrer("p", [_offre("")]) == []
        assert _statuts(stockage) == [("", VUE, None)]


def test_premiere_vue_conservee_et_derniere_vue_mise_a_jour():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")], maintenant=datetime(2026, 1, 1, 8, 0))
        stockage.enregistrer("p", [_offre("u1")], maintenant=datetime(2026, 1, 2, 9, 30))
        ligne = stockage._connexion.execute("SELECT premiere_vue, derniere_vue FROM offres").fetchone()
        assert tuple(ligne) == ("2026-01-01T08:00:00", "2026-01-02T09:30:00")


def test_lister_par_statut_et_aller_retour_des_champs():
    with Stockage(":memory:") as stockage:
        publiee = _offre("u1", titre="Dev")
        stockage.enregistrer("p", [publiee], [(_offre("u2"), "terme de recherche absent")])
        assert stockage.lister("p", NOUVELLE) == [publiee]
        assert [o.url for o in stockage.lister("p", ECARTEE)] == ["u2"]
        assert len(stockage.lister("p")) == 2
        assert stockage.lister("autre") == []


def test_lister_detail_statuts_ecartee_exclue_et_ordre_croissant():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")], maintenant=datetime(2026, 1, 1, 8, 0))
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")], [(_offre("u3"), "exclu : stage")], maintenant=datetime(2026, 1, 2, 9, 30))
        detail = stockage.lister_detail("p")
        assert [(d.offre.url, d.statut) for d in detail] == [("u1", VUE), ("u2", NOUVELLE)]
        assert (detail[0].premiere_vue, detail[0].derniere_vue) == ("2026-01-01T08:00:00", "2026-01-02T09:30:00")
        assert stockage.lister_detail("autre") == []
        assert {d.profil for d in detail} == {"p"}


def test_rejeter_passe_en_rejetee_et_survit_a_la_revue():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")], [(_offre("u3"), "exclu : stage")])
        cle = {d.offre.url: d.cle for d in stockage.lister_detail("p")}
        assert stockage.rejeter("p", cle["u1"]) is True
        assert stockage.rejeter("p", cle["u1"]) is True  # déjà rejetée : sans effet, mais pas une erreur
        assert stockage.rejeter("p", "inconnue") is False
        assert stockage.rejeter("autre", cle["u2"]) is False
        assert sorted((d.offre.url, d.statut) for d in stockage.lister_detail("p")) == [("u1", "rejetee"), ("u2", NOUVELLE)]
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        assert _statuts(stockage) == [("u1", "rejetee", None), ("u2", VUE, None), ("u3", ECARTEE, "exclu : stage")]


def test_rejeter_refuse_une_offre_ecartee():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], [(_offre("u1"), "exclu : stage")])
        cle = stockage._connexion.execute("SELECT cle FROM offres").fetchone()["cle"]
        assert stockage.rejeter("p", cle) is False
        assert _statuts(stockage) == [("u1", ECARTEE, "exclu : stage")]


def test_postuler_enregistre_la_date_et_survit_a_la_revue():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")], [(_offre("u3"), "exclu : stage")])
        cle = {d.offre.url: d.cle for d in stockage.lister_detail("p")}
        assert stockage.postuler("p", cle["u1"], date(2026, 11, 3)) is True
        assert stockage.postuler("p", "inconnue") is False
        assert stockage.postuler("autre", cle["u2"]) is False
        detail = {d.offre.url: d for d in stockage.lister_detail("p")}
        assert (detail["u1"].statut, detail["u1"].postule_le) == ("postulee", "2026-11-03")
        assert detail["u2"].postule_le is None
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        assert _statuts(stockage) == [("u1", "postulee", None), ("u2", VUE, None), ("u3", ECARTEE, "exclu : stage")]


def test_rejetee_peut_etre_postulee_mais_pas_l_inverse():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        cle = {d.offre.url: d.cle for d in stockage.lister_detail("p")}
        assert stockage.rejeter("p", cle["u1"]) is True
        assert stockage.postuler("p", cle["u1"], date(2026, 11, 3)) is True
        assert stockage.rejeter("p", cle["u1"]) is False
        assert stockage.postuler("p", cle["u2"], date(2026, 11, 3)) is True
        assert stockage.postuler("p", cle["u2"], date(2026, 11, 4)) is True  # nouvelle date pour une offre déjà postulée
        detail = {d.offre.url: d for d in stockage.lister_detail("p")}
        assert (detail["u1"].statut, detail["u1"].postule_le) == ("postulee", "2026-11-03")
        assert detail["u2"].postule_le == "2026-11-04"


def test_retablir_ramene_une_offre_rejetee_ou_postulee_a_vue_et_efface_la_date():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1"), _offre("u2"), _offre("u3")], [(_offre("u4"), "exclu : stage")])
        cle = {d.offre.url: d.cle for d in stockage.lister_detail("p")}
        stockage.rejeter("p", cle["u1"])
        stockage.postuler("p", cle["u2"], date(2026, 11, 3))
        assert stockage.retablir("p", cle["u1"]) is True
        assert stockage.retablir("p", cle["u2"]) is True
        assert stockage.retablir("p", cle["u3"]) is False  # nouvelle : rien à annuler
        assert stockage.retablir("p", "inconnue") is False
        assert stockage.retablir("autre", cle["u1"]) is False
        detail = {d.offre.url: d for d in stockage.lister_detail("p")}
        assert [(detail[u].statut, detail[u].postule_le) for u in ("u1", "u2", "u3")] == [(VUE, None), (VUE, None), (NOUVELLE, None)]


def test_retablir_refuse_une_offre_ecartee():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], [(_offre("u1"), "exclu : stage")])
        cle = stockage._connexion.execute("SELECT cle FROM offres").fetchone()["cle"]
        assert stockage.retablir("p", cle) is False
        assert _statuts(stockage) == [("u1", ECARTEE, "exclu : stage")]


def test_postuler_refuse_une_offre_ecartee():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], [(_offre("u1"), "exclu : stage")])
        cle = stockage._connexion.execute("SELECT cle FROM offres").fetchone()["cle"]
        assert stockage.postuler("p", cle) is False
        assert _statuts(stockage) == [("u1", ECARTEE, "exclu : stage")]


def test_a_postuler_depuis_nouvelle_ou_vue_et_survit_a_la_revue():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        stockage.enregistrer("p", [_offre("u2")])  # u2 passe à « vue »
        cle = {d.offre.url: d.cle for d in stockage.lister_detail("p")}
        assert stockage.a_postuler("p", cle["u1"]) is True
        assert stockage.a_postuler("p", cle["u2"]) is True
        assert stockage.a_postuler("p", cle["u2"]) is True  # déjà à postuler : sans effet, mais pas une erreur
        assert sorted((d.offre.url, d.statut) for d in stockage.lister_detail("p")) == [("u1", "a_postuler"), ("u2", "a_postuler")]
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")])
        assert _statuts(stockage) == [("u1", "a_postuler", None), ("u2", "a_postuler", None)]


def test_a_postuler_refuse_rejetee_postulee_ecartee_et_inconnue():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1"), _offre("u2")], [(_offre("u3"), "exclu : stage")])
        cle = {ligne["url"]: ligne["cle"] for ligne in stockage._connexion.execute("SELECT url, cle FROM offres")}
        stockage.rejeter("p", cle["u1"])
        stockage.postuler("p", cle["u2"], date(2026, 11, 3))
        assert stockage.a_postuler("p", cle["u1"]) is False
        assert stockage.a_postuler("p", cle["u2"]) is False
        assert stockage.a_postuler("p", cle["u3"]) is False
        assert stockage.a_postuler("p", "inconnue") is False
        assert stockage.a_postuler("autre", cle["u1"]) is False
        assert _statuts(stockage) == [("u1", "rejetee", None), ("u2", "postulee", None), ("u3", ECARTEE, "exclu : stage")]


def test_a_postuler_vers_postulee_enregistre_la_date():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        cle = stockage.lister_detail("p")[0].cle
        stockage.a_postuler("p", cle)
        assert stockage.postuler("p", cle, date(2026, 11, 5)) is True
        detail = stockage.lister_detail("p")[0]
        assert (detail.statut, detail.postule_le) == ("postulee", "2026-11-05")


def test_a_postuler_vers_rejetee_puis_pas_de_retour_a_postuler():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        cle = stockage.lister_detail("p")[0].cle
        stockage.a_postuler("p", cle)
        assert stockage.rejeter("p", cle) is True
        assert stockage.lister_detail("p")[0].statut == "rejetee"
        assert stockage.a_postuler("p", cle) is False


def test_a_postuler_vers_vue_via_retablir():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        cle = stockage.lister_detail("p")[0].cle
        stockage.a_postuler("p", cle)
        assert stockage.retablir("p", cle) is True
        assert stockage.lister_detail("p")[0].statut == VUE


def test_nettoyer_a_postuler_passe_devant_vue_et_derriere_rejetee():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed", statut_ancienne="a_postuler", statut_recente=VUE)
        stockage.nettoyer(priorite=("indeed", "glassdoor"))
        assert [tuple(ligne) for ligne in stockage._connexion.execute("SELECT url, statut FROM offres")] == [("b/2", "a_postuler")]
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed", statut_ancienne="a_postuler", statut_recente="rejetee")
        stockage.nettoyer(priorite=("indeed", "glassdoor"))
        assert [tuple(ligne) for ligne in stockage._connexion.execute("SELECT url, statut FROM offres")] == [("b/2", "rejetee")]


def test_base_ancienne_sans_colonne_postule_le_est_migree(tmp_path):
    chemin = tmp_path / "ancienne.db"
    with closing(sqlite3.connect(chemin)) as ancienne:
        ancienne.execute(
            "CREATE TABLE offres (profil TEXT NOT NULL, cle TEXT NOT NULL, titre TEXT NOT NULL, societe TEXT NOT NULL, lieu TEXT NOT NULL, "
            "url TEXT NOT NULL, date_publication TEXT, description TEXT NOT NULL, source TEXT NOT NULL, statut TEXT NOT NULL, "
            "motif_ecart TEXT, premiere_vue TEXT NOT NULL, derniere_vue TEXT NOT NULL, PRIMARY KEY (profil, cle))"
        )
        ancienne.commit()
    with Stockage(chemin) as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        cle = stockage.lister_detail("p")[0].cle
        assert stockage.postuler("p", cle, date(2026, 11, 3)) is True
        assert stockage.lister_detail("p")[0].postule_le == "2026-11-03"


def _sans_description(url, source="linkedin"):
    return Offre(titre=f"Poste {url}", societe="Acme", lieu="Lyon", url=url, description="  ", source=source)


def _description_manquante(stockage, profil="p"):
    lignes = stockage._connexion.execute("SELECT url, description_manquante FROM offres WHERE profil = ? ORDER BY url", (profil,))
    return [tuple(ligne) for ligne in lignes]


def test_migration_supprime_les_offres_sans_description_et_garde_les_autres(tmp_path):
    chemin = tmp_path / "ancienne.db"
    with closing(sqlite3.connect(chemin)) as ancienne:
        ancienne.execute(
            "CREATE TABLE offres (profil TEXT NOT NULL, cle TEXT NOT NULL, titre TEXT NOT NULL, societe TEXT NOT NULL, lieu TEXT NOT NULL, "
            "url TEXT NOT NULL, date_publication TEXT, description TEXT NOT NULL, source TEXT NOT NULL, statut TEXT NOT NULL, "
            "motif_ecart TEXT, premiere_vue TEXT NOT NULL, derniere_vue TEXT NOT NULL, postule_le TEXT, PRIMARY KEY (profil, cle))"
        )
        for cle, description, statut in (("vide", "", ECARTEE), ("blanche", "  \n", VUE), ("pleine", "texte", NOUVELLE)):
            ancienne.execute(
                "INSERT INTO offres VALUES ('p', ?, ?, 'Acme', 'Lyon', ?, NULL, ?, 'indeed', ?, NULL, '2026-01-01', '2026-01-01', NULL)",
                (cle, f"Poste {cle}", f"u-{cle}", description, statut),
            )
        ancienne.commit()
    with Stockage(chemin) as stockage:
        assert _description_manquante(stockage) == [("u-pleine", 0)]
    with Stockage(chemin) as stockage:
        assert _description_manquante(stockage) == [("u-pleine", 0)]


def test_offre_sans_description_n_est_pas_enregistree_quelle_que_soit_la_source():
    with Stockage(":memory:") as stockage:
        assert stockage.enregistrer("p", [_sans_description("u1", "indeed")], [(_sans_description("u2"), "sans description")]) == []
        assert _urls(stockage) == []


def test_offre_manquante_est_enregistree_ecartee_marquee_et_invisible():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], manquantes=[_sans_description("u1")])
        assert _statuts(stockage) == [("u1", ECARTEE, "sans description")]
        assert _description_manquante(stockage) == [("u1", 1)]
        assert stockage.lister_detail("p") == []
        assert [o.offre.url for o in stockage.lister_manquantes("p")] == ["u1"]
        assert stockage.lister_manquantes("autre") == []


def test_offre_manquante_ne_touche_pas_une_offre_deja_stockee_avec_description():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1", titre="Expert", source="linkedin")])
        stockage.enregistrer("p", [], manquantes=[Offre(titre="Expert", societe="Acme", lieu="Lyon", url="u1", description="", source="linkedin")])
        assert _statuts(stockage) == [("u1", NOUVELLE, None)]
        assert stockage.lister_manquantes() == []


def test_descriptions_linkedin_ne_renvoie_que_les_offres_linkedin_du_profil_avec_description():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1", titre="Expert", source="linkedin"), _offre("u2", titre="Autre", source="indeed")])
        stockage.enregistrer("p", [], manquantes=[_sans_description("u3")])
        stockage.enregistrer("autre", [_offre("u4", titre="Ailleurs", source="linkedin")])
        assert list(stockage.descriptions_linkedin("p")) == ["u1"]


def test_completer_description_retenue_repasse_en_nouvelle():
    profil = Profil(nom="p", termes_recherche=("poste",), lieu="Lyon")
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], manquantes=[_sans_description("u1")])
        cle = stockage.lister_manquantes()[0].cle
        retenue = stockage.completer_description(profil, cle, "Un poste à Lyon", maintenant=datetime(2026, 1, 2))
        assert retenue is not None and retenue.description == "Un poste à Lyon"
        assert _statuts(stockage) == [("u1", NOUVELLE, None)]
        assert _description_manquante(stockage) == [("u1", 0)]
        assert [o.offre.description for o in stockage.lister_detail("p")] == ["Un poste à Lyon"]
        assert stockage.lister_manquantes() == []


def test_completer_description_ecartee_par_un_filtre_porte_son_motif():
    profil = Profil(nom="p", termes_recherche=("poste",), lieu="Lyon", mots_exclus=("stage",))
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [], manquantes=[_sans_description("u1")])
        assert stockage.completer_description(profil, stockage.lister_manquantes()[0].cle, "Un stage à Lyon") is None
        assert [ligne[1] for ligne in _statuts(stockage)] == [ECARTEE]
        assert _statuts(stockage)[0][2] != "sans description"
        assert _description_manquante(stockage) == [("u1", 0)]


def test_completer_description_vide_ou_offre_complete_ne_change_rien():
    profil = Profil(nom="p", termes_recherche=("poste",), lieu="Lyon")
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u2", titre="Poste")], manquantes=[_sans_description("u1")])
        manquante, complete = stockage.lister_manquantes()[0].cle, stockage.lister_detail("p")[0].cle
        assert stockage.completer_description(profil, manquante, "  ") is None
        assert stockage.completer_description(profil, complete, "autre texte") is None
        assert _description_manquante(stockage) == [("u1", 1), ("u2", 0)]
        assert stockage.lister_detail("p")[0].offre.description == "d"


def test_lister_detail_sans_profil_renvoie_tous_les_profils():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("a", [_offre("u1")], maintenant=datetime(2026, 1, 1, 8, 0))
        stockage.enregistrer("b", [_offre("u2")], [(_offre("u3"), "exclu : stage")], maintenant=datetime(2026, 1, 2, 8, 0))
        detail = stockage.lister_detail()
        assert [(d.profil, d.offre.url) for d in detail] == [("a", "u1"), ("b", "u2")]


def test_offres_equivalentes_d_urls_differentes_ne_font_qu_une_ligne():
    with Stockage(":memory:") as stockage:
        premiere = _offre("indeed/1", titre="Développeur  C#", societe="ACME", lieu="Lamanon, 13")
        seconde = _offre("linkedin/9", titre="developpeur c#", societe="Acme", lieu="Lamanon, Provence-Alpes-Côte d'Azur, FR")
        assert stockage.enregistrer("p", [premiere]) == [premiere]
        assert stockage.enregistrer("p", [seconde]) == []
        assert _statuts(stockage) == [("indeed/1", VUE, None)]


def test_societes_incluses_l_une_dans_l_autre_ne_font_qu_une_ligne_les_sans_rapport_non():
    with Stockage(":memory:") as stockage:
        premiere = _offre("indeed/1", titre="Expert", societe="Mirion", lieu="Lamanon, 13")
        proche = _offre("glassdoor/2", titre="Expert", societe="Mirion Technologies", lieu="Lamanon, France")
        sans_rapport = _offre("indeed/3", titre="Expert", societe="L'industrie recrute", lieu="Lamanon, 13")
        assert stockage.enregistrer("p", [premiere]) == [premiere]
        assert stockage.enregistrer("p", [proche]) == []
        assert stockage.enregistrer("p", [sans_rapport]) == [sans_rapport]
        assert _statuts(stockage) == [("indeed/1", VUE, None), ("indeed/3", NOUVELLE, None)]


def test_nettoyer_fusionne_les_quasi_doublons_en_gardant_le_plus_ancien():
    with Stockage(":memory:") as stockage:
        ancienne = _offre("indeed/1", titre="Expert", societe="Mirion", lieu="Lamanon, 13")
        proche = _offre("glassdoor/2", titre="Expert", societe="Mirion Technologies", lieu="Lamanon, France")
        ailleurs = _offre("linkedin/3", titre="Autre", lieu="France")
        stockage.enregistrer("p", [ancienne], maintenant=datetime(2026, 1, 1))
        # Insertion directe de ce qu'une ancienne version aurait laissé en base (clés différentes).
        stockage.enregistrer("p", [ailleurs], maintenant=datetime(2026, 1, 2))
        stockage._ecrire("p", proche, NOUVELLE, None, "2026-01-03T00:00:00", cle="cle-proche")
        stockage._connexion.commit()
        assert stockage.nettoyer(simuler=True) == 1
        assert len(_statuts(stockage)) == 3
        assert stockage.nettoyer() == 1
        assert _statuts(stockage) == [("indeed/1", NOUVELLE, None), ("linkedin/3", NOUVELLE, None)]


def test_nettoyer_garde_la_source_la_mieux_classee_meme_si_plus_recente():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed")
        assert stockage.nettoyer(priorite=("indeed", "glassdoor")) == 1
        assert _urls(stockage) == ["b/2"]


def test_nettoyer_garde_la_source_la_mieux_classee_meme_si_plus_ancienne():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "indeed", "glassdoor")
        assert stockage.nettoyer(priorite=("indeed", "glassdoor")) == 1
        assert _urls(stockage) == ["a/1"]


def test_nettoyer_sans_priorite_garde_la_plus_ancienne():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed")
        assert stockage.nettoyer() == 1
        assert _urls(stockage) == ["a/1"]


def test_nettoyer_source_hors_liste_passe_apres_celles_de_la_liste():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "hellowork", "glassdoor")
        assert stockage.nettoyer(priorite=("glassdoor",)) == 1
        assert _urls(stockage) == ["b/2"]


def test_nettoyer_heritage_du_statut_postule_de_la_date_et_de_la_premiere_vue():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed", statut_ancienne="postulee")
        stockage._connexion.execute("UPDATE offres SET postule_le = '2026-02-02' WHERE url = 'a/1'")
        stockage._connexion.commit()
        assert stockage.nettoyer(priorite=("indeed", "glassdoor")) == 1
        ligne = stockage._connexion.execute("SELECT url, statut, postule_le, premiere_vue FROM offres").fetchone()
        assert tuple(ligne) == ("b/2", "postulee", "2026-02-02", "2026-01-01T00:00:00")


def test_nettoyer_ne_degrade_pas_le_statut_de_l_offre_gardee():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed", statut_ancienne=VUE, statut_recente="rejetee")
        stockage.nettoyer(priorite=("indeed", "glassdoor"))
        assert [tuple(ligne) for ligne in stockage._connexion.execute("SELECT url, statut FROM offres")] == [("b/2", "rejetee")]


def test_nettoyer_ignore_les_offres_ecartees():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed", statut_recente=ECARTEE)
        assert stockage.nettoyer(priorite=("indeed", "glassdoor")) == 0
        assert _urls(stockage) == ["a/1", "b/2"]


def test_nettoyer_simuler_n_ecrit_rien_avec_priorite():
    with Stockage(":memory:") as stockage:
        _deux_doublons(stockage, "glassdoor", "indeed", statut_ancienne="postulee")
        assert stockage.nettoyer(simuler=True, priorite=("indeed", "glassdoor")) == 1
        assert [tuple(ligne) for ligne in stockage._connexion.execute("SELECT url, statut, premiere_vue FROM offres ORDER BY url")] == [
            ("a/1", "postulee", "2026-01-01T00:00:00"),
            ("b/2", NOUVELLE, "2026-01-03T00:00:00"),
        ]


def _source_et_url(stockage):
    return [tuple(ligne) for ligne in stockage._connexion.execute("SELECT source, url, statut, derniere_vue FROM offres")]


def test_enregistrer_n_ecrase_pas_une_source_mieux_classee():
    with Stockage(":memory:") as stockage:
        priorite = ("indeed", "glassdoor")
        stockage.enregistrer("p", [_offre("indeed/1", titre="Expert", source="indeed")], maintenant=datetime(2026, 1, 1), priorite=priorite)
        assert stockage.enregistrer("p", [_offre("glassdoor/1", titre="Expert", source="glassdoor")], maintenant=datetime(2026, 1, 2), priorite=priorite) == []
        assert _source_et_url(stockage) == [("indeed", "indeed/1", VUE, "2026-01-02T00:00:00")]


def test_enregistrer_remplace_par_une_source_mieux_classee():
    with Stockage(":memory:") as stockage:
        priorite = ("indeed", "glassdoor")
        stockage.enregistrer("p", [_offre("glassdoor/1", titre="Expert", source="glassdoor")], maintenant=datetime(2026, 1, 1), priorite=priorite)
        assert stockage.enregistrer("p", [_offre("indeed/1", titre="Expert", source="indeed")], maintenant=datetime(2026, 1, 2), priorite=priorite) == []
        assert _source_et_url(stockage) == [("indeed", "indeed/1", VUE, "2026-01-02T00:00:00")]
        assert stockage._connexion.execute("SELECT premiere_vue FROM offres").fetchone()[0] == "2026-01-01T00:00:00"


def test_enregistrer_sans_priorite_ne_change_pas_la_ligne_existante():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("glassdoor/1", titre="Expert", source="glassdoor")], maintenant=datetime(2026, 1, 1))
        stockage.enregistrer("p", [_offre("indeed/1", titre="Expert", source="indeed")], maintenant=datetime(2026, 1, 2))
        assert _source_et_url(stockage) == [("glassdoor", "glassdoor/1", VUE, "2026-01-02T00:00:00")]


def test_enregistrer_offre_ecartee_reintegree_par_une_source_moins_bien_classee_garde_la_meilleure():
    with Stockage(":memory:") as stockage:
        priorite = ("indeed", "glassdoor")
        stockage.enregistrer("p", [], [(_offre("indeed/1", titre="Expert", source="indeed"), "motif")], maintenant=datetime(2026, 1, 1), priorite=priorite)
        assert len(stockage.enregistrer("p", [_offre("glassdoor/1", titre="Expert", source="glassdoor")], maintenant=datetime(2026, 1, 2), priorite=priorite)) == 1
        assert _source_et_url(stockage) == [("indeed", "indeed/1", NOUVELLE, "2026-01-02T00:00:00")]


def test_reevaluer_reintegre_les_ecartees_et_ecarte_les_retenues_selon_les_regles_actuelles():
    with Stockage(":memory:") as stockage:
        marseille, lamanon, venelles = _offre("u1", lieu="Marseille, B8, France"), _offre("u2", lieu="Lamanon, 13"), _offre("u3", lieu="Venelles, France")
        stockage.enregistrer("p", [lamanon, venelles], [(marseille, "hors zone")])
        profil = Profil(nom="p", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon", rayon_km=50, villes_exclues=("Venelles",))
        assert stockage.reevaluer(profil) == (1, 1)
        assert _statuts(stockage) == [("u1", NOUVELLE, None), ("u2", NOUVELLE, None), ("u3", ECARTEE, "ville exclue : Venelles")]
        assert stockage.reevaluer(profil) == (0, 0)


def test_reevaluer_ecarte_les_offres_sans_terme_de_recherche_sauf_si_exiger_termes_est_faux():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1", titre="Dev Python"), _offre("u2", titre="Dev Java")])
        assert stockage.reevaluer(Profil(nom="p", termes_recherche=("python",), lieu="Lyon")) == (0, 1)
        assert _statuts(stockage) == [("u1", NOUVELLE, None), ("u2", ECARTEE, "terme de recherche absent")]
        assert stockage.reevaluer(Profil(nom="p", termes_recherche=("python",), lieu="Lyon", exiger_termes=False)) == (1, 0)
        assert _statuts(stockage) == [("u1", NOUVELLE, None), ("u2", NOUVELLE, None)]


def test_reevaluer_ne_touche_pas_aux_autres_profils_et_garde_le_statut_vue():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1")])
        stockage.enregistrer("p", [_offre("u1")])
        stockage.enregistrer("q", [], [(_offre("u2"), "hors zone")])
        assert stockage.reevaluer(Profil(nom="p", termes_recherche=("a",), exiger_termes=False, lieu="Lyon")) == (0, 0)
        assert _statuts(stockage) == [("u1", VUE, None)]
        assert _statuts(stockage, "q") == [("u2", ECARTEE, "hors zone")]


def _cles(stockage):
    return [ligne["cle"] for ligne in stockage._connexion.execute("SELECT cle FROM offres ORDER BY cle")]


def _inserer_ancienne_cle(stockage, offre, premiere_vue):
    # Ligne telle que l'ancienne version la stockait : la clé est l'URL.
    stockage._connexion.execute(
        "INSERT INTO offres (profil, cle, titre, societe, lieu, url, date_publication, description, source, statut, motif_ecart, premiere_vue, derniere_vue) "
        "VALUES ('p', ?, ?, ?, ?, ?, NULL, 'd', 'indeed', 'vue', NULL, ?, ?)",
        (offre.url, offre.titre, offre.societe, offre.lieu, offre.url, premiere_vue, premiere_vue),
    )
    stockage._connexion.execute("PRAGMA user_version = 0")  # base d'avant les migrations : elles se rejouent à la prochaine ouverture
    stockage._connexion.commit()


def test_migrations_jouees_une_seule_fois_par_base(tmp_path):
    chemin = tmp_path / "alertes.db"
    with Stockage(chemin) as stockage:
        _inserer_ancienne_cle(stockage, _offre("indeed/1"), "2026-01-01T00:00:00")
    with Stockage(chemin) as stockage:
        assert _cles(stockage) != ["indeed/1"]  # clé migrée (plus l'URL)
        stockage._connexion.execute("UPDATE offres SET cle = 'a-la-main'")
        stockage._connexion.commit()
    with Stockage(chemin) as stockage:
        assert _cles(stockage) == ["a-la-main"]  # base déjà migrée : pas de nouvelle migration


def test_migration_fusionne_les_doublons_en_gardant_la_plus_ancienne(tmp_path):
    chemin = tmp_path / "alertes.db"
    ancienne = _offre("indeed/1", titre="Dev", lieu="Lamanon, 13")
    doublon = _offre("linkedin/9", titre="DEV", lieu="Lamanon, France")
    autre = _offre("indeed/2", titre="Chef de projet")
    with Stockage(chemin) as stockage:
        _inserer_ancienne_cle(stockage, doublon, "2026-01-02T00:00:00")
        _inserer_ancienne_cle(stockage, ancienne, "2026-01-01T00:00:00")
        _inserer_ancienne_cle(stockage, autre, "2026-01-03T00:00:00")
    with Stockage(chemin) as stockage:
        assert _statuts(stockage) == [("indeed/1", VUE, None), ("indeed/2", VUE, None)]
        assert stockage.enregistrer("p", [doublon]) == []
    with Stockage(chemin) as stockage:
        assert len(_statuts(stockage)) == 2
        cles = [ligne[0] for ligne in stockage._connexion.execute("SELECT cle FROM offres ORDER BY cle")]
        assert cles == ["chef de projet|acme|lyon", "dev|acme|lamanon"]


def test_fichier_cree_avec_son_repertoire(tmp_path):
    chemin = tmp_path / "donnees" / "alertes.db"
    with Stockage(chemin) as stockage:
        stockage.enregistrer("p", [_offre("u1")])
    with Stockage(chemin) as stockage:
        assert [o.url for o in stockage.lister("p")] == ["u1"]


def test_reevaluer_applique_les_exclusions_globales():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer("p", [_offre("u1", titre="Stage dev"), _offre("u2")])
        profil = Profil(nom="p", termes_recherche=("a",), exiger_termes=False, lieu="Lyon")
        assert stockage.reevaluer(profil, ParametresGlobaux(mots_exclus=("stage",))) == (0, 1)
        assert _statuts(stockage) == [("u1", ECARTEE, "exclu : stage"), ("u2", NOUVELLE, None)]
        assert stockage.reevaluer(profil) == (1, 0)


PROFIL = {"nom": "test", "termes_recherche": ["dev"], "lieu": "Lyon"}


def test_creer_puis_lister_et_charger_un_profil_avec_accents():
    with Stockage(":memory:") as stockage:
        assert stockage.lister_profils() == {}
        stockage.creer_profil({**PROFIL, "nom": "Été"})
        stockage.creer_profil({**PROFIL, "nom": "alpha"})
        assert list(stockage.lister_profils()) == ["alpha", "Été"]
        assert stockage.charger_profil("Été").lieu == "Lyon"


def test_creer_profil_refuse_un_nom_deja_pris():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        with pytest.raises(ProfilInvalide, match="déjà utilisé"):
            stockage.creer_profil({**PROFIL, "lieu": "Paris"})
        assert stockage.charger_profil("test").lieu == "Lyon"


def test_creer_profil_invalide_n_enregistre_rien():
    with Stockage(":memory:") as stockage:
        with pytest.raises(ProfilInvalide, match="rayon_km"):
            stockage.creer_profil({**PROFIL, "rayon_km": "30"})
        assert stockage.lister_profils() == {}


def test_charger_profil_inconnu_refuse():
    with Stockage(":memory:") as stockage:
        with pytest.raises(ProfilInvalide, match="introuvable"):
            stockage.charger_profil("absent")


def test_modifier_profil_fusionne_les_champs_et_garde_le_nom():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil({**PROFIL, "mots_exclus": ["stage"]})
        profil = stockage.modifier_profil("test", {"nom": "test", "lieu": "Paris"})
        assert (profil.nom, profil.lieu, profil.mots_exclus) == ("test", "Paris", ("stage",))
        assert stockage.charger_profil("test") == profil


def test_modifier_profil_renomme_et_les_offres_et_la_date_suivent():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        stockage.enregistrer("test", [_offre("u1")])
        stockage.noter_mise_a_jour("test")
        profil = stockage.modifier_profil("test", {"nom": "autre", "lieu": "Paris"})
        assert (profil.nom, profil.lieu) == ("autre", "Paris")
        assert list(stockage.lister_profils()) == ["autre"]
        assert stockage.charger_profil("autre").nom == "autre"
        assert [o.profil for o in stockage.lister_detail()] == ["autre"]
        assert list(stockage.dernieres_mises_a_jour()) == ["autre"]


def test_modifier_profil_refuse_un_nom_deja_pris_ou_vide_sans_rien_changer():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        stockage.creer_profil({**PROFIL, "nom": "autre"})
        stockage.enregistrer("test", [_offre("u1")])
        with pytest.raises(ProfilInvalide, match="déjà utilisé"):
            stockage.modifier_profil("test", {"nom": "autre"})
        with pytest.raises(ProfilInvalide, match="nom"):
            stockage.modifier_profil("test", {"nom": " "})
        assert list(stockage.lister_profils()) == ["autre", "test"]
        assert [o.profil for o in stockage.lister_detail()] == ["test"]


def test_modifier_profil_refuse_le_nom_d_offres_orphelines_laissees_par_une_ancienne_suppression():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        stockage.enregistrer("ancien", [_offre("u2")])
        with pytest.raises(ProfilInvalide, match="déjà utilisé"):
            stockage.modifier_profil("test", {"nom": "ancien"})
        assert list(stockage.lister_profils()) == ["test"]


def test_modifier_profil_invalide_ou_inconnu_laisse_la_base_intacte():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        with pytest.raises(ProfilInvalide, match="termes_recherche"):
            stockage.modifier_profil("test", {"termes_recherche": []})
        with pytest.raises(ProfilInvalide, match="introuvable"):
            stockage.modifier_profil("absent", {"lieu": "Paris"})
        assert stockage.charger_profil("test").termes_recherche == ("dev",)


def test_supprimer_profil_efface_ses_offres_et_sa_date_sans_toucher_aux_autres():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        stockage.creer_profil({**PROFIL, "nom": "autre"})
        stockage.enregistrer("test", [_offre("u1"), _offre("u2")])
        stockage.enregistrer("autre", [_offre("u3")])
        stockage.noter_mise_a_jour("test")
        stockage.noter_mise_a_jour("autre")
        assert stockage.supprimer_profil("test") == 2
        assert list(stockage.lister_profils()) == ["autre"]
        assert _statuts(stockage, "test") == []
        assert _statuts(stockage, "autre") == [("u3", NOUVELLE, None)]
        assert list(stockage.dernieres_mises_a_jour()) == ["autre"]


def test_supprimer_profil_sans_offre_renvoie_zero():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        assert stockage.supprimer_profil("test") == 0


def test_supprimer_profil_introuvable_leve_et_laisse_la_base_intacte():
    with Stockage(":memory:") as stockage:
        stockage.creer_profil(PROFIL)
        stockage.enregistrer("orphelin", [_offre("u1")])
        with pytest.raises(ProfilInvalide, match="introuvable"):
            stockage.supprimer_profil("orphelin")
        assert _statuts(stockage, "orphelin") == [("u1", NOUVELLE, None)]


def test_globaux_par_defaut_puis_enregistres_et_remplaces():
    with Stockage(":memory:") as stockage:
        assert stockage.charger_globaux() == ParametresGlobaux()
        stockage.enregistrer_globaux({"mots_exclus": ["stage"], "villes_exclues": ["Paris"]})
        assert stockage.charger_globaux() == ParametresGlobaux(("stage",), ("Paris",))
        stockage.enregistrer_globaux({"priorite_sites": ["indeed", "glassdoor"]})
        assert stockage.charger_globaux() == ParametresGlobaux(priorite_sites=("indeed", "glassdoor"))


def test_globaux_recherche_auto_enregistres_et_relus():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer_globaux({"recherche_auto_demarrage": False, "delai_recherche_auto_minutes": 120})
        globaux = stockage.charger_globaux()
        assert globaux.recherche_auto_demarrage is False and globaux.delai_recherche_auto_minutes == 120
        with pytest.raises(ProfilInvalide):
            stockage.enregistrer_globaux({"delai_recherche_auto_minutes": 2})
        assert stockage.charger_globaux().delai_recherche_auto_minutes == 120


def test_enregistrer_globaux_invalide_ne_change_rien():
    with Stockage(":memory:") as stockage:
        stockage.enregistrer_globaux({"mots_exclus": ["stage"]})
        with pytest.raises(ProfilInvalide):
            stockage.enregistrer_globaux({"mots_exclus": "stage"})
        assert stockage.charger_globaux().mots_exclus == ("stage",)


def _dossier_json(tmp_path):
    dossier = tmp_path / "profils"
    dossier.mkdir()
    (dossier / "a.json").write_text(json.dumps({**PROFIL, "nom": "Alpha"}), encoding="utf-8-sig")
    (dossier / "b.json").write_text(json.dumps({**PROFIL, "nom": "Beta"}), encoding="utf-8")
    (dossier / "casse.json").write_text("{pas du json", encoding="utf-8")
    (dossier / "exemple.json").write_text(json.dumps({**PROFIL, "nom": "EXEMPLE"}), encoding="utf-8")
    (dossier / "_global.json").write_text(json.dumps({"mots_exclus": ["stage"]}), encoding="utf-8")
    return dossier


def test_import_json_une_seule_fois(tmp_path):
    dossier = _dossier_json(tmp_path)
    with Stockage(tmp_path / "alertes.db") as stockage:
        messages = stockage.importer_profils_json(dossier)
        assert list(stockage.lister_profils()) == ["Alpha", "Beta"]
        assert stockage.charger_globaux().mots_exclus == ("stage",)
        assert len(messages) == 1 and "casse.json" in messages[0] and "JSON invalide" in messages[0]
        assert sorted(f.name for f in dossier.iterdir()) == [
            "_global.json.importe", "a.json.importe", "b.json.importe", "casse.json", "exemple.json",
        ]
        (dossier / "nouveau.json").write_text(json.dumps({**PROFIL, "nom": "Gamma"}), encoding="utf-8")
        assert stockage.importer_profils_json(dossier) == []
        assert list(stockage.lister_profils()) == ["Alpha", "Beta"]
        assert (dossier / "nouveau.json").exists()


def test_import_json_n_ecrase_pas_un_profil_existant(tmp_path):
    dossier = _dossier_json(tmp_path)
    with Stockage(tmp_path / "alertes.db") as stockage:
        stockage.creer_profil({**PROFIL, "nom": "Alpha", "lieu": "Paris"})
        stockage.importer_profils_json(dossier)
        assert stockage.charger_profil("Alpha").lieu == "Paris"


def test_import_json_dossier_absent_pose_quand_meme_le_marqueur(tmp_path):
    with Stockage(tmp_path / "alertes.db") as stockage:
        assert stockage.importer_profils_json(tmp_path / "absent") == []
        (tmp_path / "absent").mkdir()
        (tmp_path / "absent" / "a.json").write_text(json.dumps(PROFIL), encoding="utf-8")
        assert stockage.importer_profils_json(tmp_path / "absent") == []
        assert stockage.lister_profils() == {}


def test_valeurs_de_statut_stockees_en_base_figees():
    # Les constantes de statut sont écrites telles quelles en base : changer une valeur casserait les bases existantes.
    assert (NOUVELLE, VUE, ECARTEE) == ("nouvelle", "vue", "ecartee")
