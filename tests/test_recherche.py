import json
import logging
from dataclasses import replace

import pandas as pd
import pytest
from outils import offre_de_test

from alertes_emploi import NOM_JOURNAL
from alertes_emploi.filtres.regles import appliquer_regles
from alertes_emploi.modele import Offre
from alertes_emploi.profils.chargeur import ParametresGlobaux, Profil
from alertes_emploi.recherche import RechercheAnnulee, lancer_recherche, main, rechercher_profil
from alertes_emploi.sources import jobspy_adapter
from alertes_emploi.sources.jobspy_adapter import MAPPING_COLONNES, RechercheBloquee
from alertes_emploi.stockage.base import ECARTEE, NOUVELLE, VUE, Stockage


_offre = offre_de_test


def _manquante(url, **champs):
    # Offre LinkedIn renvoyée sans description parce que sa page d'offre a répondu 429.
    return replace(_offre(url, description="", **champs), description_bloquee=True)


def _trois_manquantes():
    return [_manquante(f"u{n}", titre=f"Poste {n}", societe=f"Societe {n}") for n in (1, 2, 3)]


def _jobs_depuis(offres):
    return pd.DataFrame([{colonne: getattr(offre, champ) for champ, colonne in MAPPING_COLONNES.items()} for offre in offres])


def _simuler_jobspy(monkeypatch, fabrique):
    # Frontière réseau : seul scrape_jobs de JobSpy est remplacé, `main` traverse tout le code de l'application.
    monkeypatch.setattr(jobspy_adapter, "scrape_jobs", lambda **kw: fabrique())


def _creer_profil(base, nom="x"):
    with Stockage(base) as stockage:
        stockage.creer_profil({"nom": nom, "termes_recherche": ["a"], "exiger_termes": False, "lieu": "Lyon"})


def test_un_appel_par_terme_avec_les_parametres_du_profil():
    appels = []

    def faux(terme, lieu, **kwargs):
        appels.append((terme, lieu, kwargs))
        return []

    profil = Profil(nom="t", termes_recherche=("a", "b"), lieu="Lyon", rayon_km=10, type_contrat="fulltime", teletravail=True)
    rechercher_profil(profil, rechercheur=faux)
    assert [(t, l) for t, l, _ in appels] == [("a", "Lyon"), ("b", "Lyon")]
    assert appels[0][2] == {
        "sites": ("indeed",),
        "rayon_km": 10,
        "anciennete_heures": None,
        "nombre_resultats": 15,
        "pays_indeed": "France",
        "type_contrat": "fulltime",
        "teletravail": True,
        "recuperer_descriptions": True,
        "profil": "t",
        "ids_connus": frozenset(),
    }


def test_doublons_entre_termes_supprimes():
    resultats = {"a": [_offre("u1"), _offre("u2")], "b": [_offre("u2"), _offre("u3")]}
    profil = Profil(nom="t", termes_recherche=("a", "b"), lieu="Lyon")
    offres = rechercher_profil(profil, rechercheur=lambda terme, lieu, **kw: resultats[terme])
    assert [o.url for o in offres] == ["u1", "u2", "u3"]


def test_meme_titre_societe_ville_d_urls_differentes_ne_donnent_qu_une_offre():
    resultats = [
        _offre("indeed/1", titre="Développeur  C#", societe="ACME", lieu="Lamanon, 13"),
        _offre("linkedin/9", titre=" developpeur c# ", societe="Acme", lieu="lamanon, Provence-Alpes-Côte d'Azur, FR"),
        # Description différente : à description identique, une société différente ne distingue plus (même annonce republiée sous un autre nom).
        _offre("indeed/2", titre="Développeur C#", societe="Autre", lieu="Lamanon, 13", description="Mission de refonte d'un logiciel de facturation pour un autre client."),
    ]
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon")
    offres = rechercher_profil(profil, rechercheur=lambda terme, lieu, **kw: resultats)
    assert [o.url for o in offres] == ["indeed/1", "indeed/2"]


def test_societes_incluses_l_une_dans_l_autre_sont_fusionnees_les_sans_rapport_non():
    resultats = [
        _offre("indeed/1", titre="Expert R&D", societe="Mirion", lieu="Lamanon, 13"),
        _offre("glassdoor/2", titre="EXPERT R&D", societe="Mirion Technologies", lieu="Lamanon, France"),
        _offre("indeed/3", titre="Expert R&D", societe="L'industrie recrute", lieu="Lamanon, 13"),
    ]
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon, France")
    offres = rechercher_profil(profil, rechercheur=lambda terme, lieu, **kw: resultats)
    assert [o.url for o in offres] == ["indeed/1", "indeed/3"]


def test_rayon_zero_garde_les_lieux_contenant_la_ville_du_profil():
    dans = [
        _offre("u1", lieu="Lamanon, B8, France"),
        _offre("u2", lieu="Lamanon, Provence-Alpes-Côte d'Azur, France"),
    ]
    hors = [_offre("u3", lieu="France"), _offre("u4", lieu="Singapour"), _offre("u5", lieu="")]
    retenues, ecartees = appliquer_regles(dans + hors, Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon, France", rayon_km=0))
    assert retenues == dans
    assert ecartees == [(offre, "hors zone") for offre in hors]


def test_rayon_positif_ne_filtre_pas_par_nom_de_ville():
    offres = [_offre("u1", lieu="Marseille, B8, France"), _offre("u2", lieu="France"), _offre("u3", lieu="")]
    retenues, ecartees = appliquer_regles(offres, Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon", rayon_km=50))
    assert retenues == offres and ecartees == []


def test_villes_exclues_comparees_a_la_ville_de_l_offre_sans_casse_ni_accents_et_en_egalite_exacte():
    offres = [
        _offre("u1", lieu="Marseille, B8, France"),
        _offre("u2", lieu="MARSEILLE"),
        _offre("u3", lieu="Aix-en-Provence, France"),
        _offre("u4", lieu="Marseille 13e, France"),
        _offre("u5", lieu="Évry, France"),
    ]
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon", villes_exclues=("marseille", "Aix", " ", "evry"))
    retenues, ecartees = appliquer_regles(offres, profil)
    assert [o.url for o in retenues] == ["u3", "u4"]
    assert ecartees == [(offres[0], "ville exclue : marseille"), (offres[1], "ville exclue : marseille"), (offres[4], "ville exclue : evry")]


def test_les_mots_cles_priment_puis_la_zone_puis_les_villes_exclues():
    offre = Offre("Stage dev", "Acme", "Marseille", "u1", "Stage de développement", "indeed")
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon", rayon_km=0, mots_exclus=("stage",), villes_exclues=("Marseille",))
    assert appliquer_regles([offre], profil)[1] == [(offre, "exclu : stage")]


def test_offre_sans_description_ecartee_avec_description_conservee():
    avec = _offre("u1", description="Poste de développeur")
    vide = _offre("u2", description="")
    blanche = _offre("u3", description="  \n\t ")
    retenues, ecartees = appliquer_regles([avec, vide, blanche], Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lyon"))
    assert retenues == [avec]
    assert ecartees == [(vide, "sans description"), (blanche, "sans description")]


def _profil_simple():
    return Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lyon")


def test_lancer_recherche_n_enregistre_pas_les_offres_sans_description_et_les_journalise_avec_leur_lien(tmp_path, caplog):
    resultats = [_offre("u1"), _offre("u2", description="")]
    with caplog.at_level(logging.WARNING, logger=NOM_JOURNAL):
        bilan = lancer_recherche(_profil_simple(), tmp_path / "a.db", rechercheur=lambda terme, lieu, **kw: resultats)
    assert (bilan.nb_retenues, bilan.nb_ecartees) == (1, 0)
    with Stockage(tmp_path / "a.db") as stockage:
        assert [o.url for o in stockage.lister("t")] == ["u1"]
        assert stockage.lister_manquantes("t") == []
    assert any("u2" in message for message in caplog.messages)


def test_offre_linkedin_sans_description_a_cause_d_un_429_est_enregistree_manquante_sans_journal(tmp_path, caplog):
    resultats = [_offre("u1"), _manquante("u2")]
    with caplog.at_level(logging.WARNING, logger=NOM_JOURNAL):
        lancer_recherche(_profil_simple(), tmp_path / "a.db", rechercheur=lambda terme, lieu, **kw: resultats)
    with Stockage(tmp_path / "a.db") as stockage:
        assert [m.offre.url for m in stockage.lister_manquantes("t")] == ["u2"]
        assert [o.url for o in stockage.lister("t", NOUVELLE)] == ["u1"]
    assert not any("u2" in message for message in caplog.messages)


def _recuperateur(reponses, appels):
    def recuperer(url):
        appels.append(url)
        return reponses[url]
    return recuperer


def test_description_recuperee_a_la_recherche_suivante_rend_l_offre_visible(tmp_path):
    base = tmp_path / "a.db"
    manquante = _manquante("u2")
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [manquante])
    appels = []
    reponses = {"u2": jobspy_adapter.DescriptionRecuperee("Description enfin récupérée", False)}
    bilan = lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [], recuperateur=_recuperateur(reponses, appels))
    assert appels == ["u2"]
    assert [o.url for o in bilan.nouvelles] == ["u2"]
    with Stockage(base) as stockage:
        assert stockage.lister_manquantes("t") == []
        assert [o.url for o in stockage.lister("t", NOUVELLE)] == ["u2"]


def test_recuperation_des_descriptions_s_arrete_au_premier_429(tmp_path):
    base = tmp_path / "a.db"
    manquantes = _trois_manquantes()
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: manquantes)
    appels = []
    reponses = {
        "u1": jobspy_adapter.DescriptionRecuperee("Description un", False),
        "u2": jobspy_adapter.DescriptionRecuperee("", True),
        "u3": jobspy_adapter.DescriptionRecuperee("Description trois", False),
    }
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [], recuperateur=_recuperateur(reponses, appels))
    assert appels == ["u1", "u2"]
    with Stockage(base) as stockage:
        assert [m.offre.url for m in stockage.lister_manquantes("t")] == ["u2", "u3"]


def _lignes_reprise(caplog):
    return [r for r in caplog.records if r.getMessage().startswith("Reprise des descriptions")]


def test_reprise_arretee_par_un_429_journalise_le_compte_exact_en_warning(tmp_path, caplog):
    base = tmp_path / "a.db"
    manquantes = _trois_manquantes()
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: manquantes)
    reponses = {
        "u1": jobspy_adapter.DescriptionRecuperee("Description un", False),
        "u2": jobspy_adapter.DescriptionRecuperee("", True),
    }
    with caplog.at_level(logging.INFO, logger=NOM_JOURNAL):
        lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [], recuperateur=_recuperateur(reponses, []))
    lignes = _lignes_reprise(caplog)
    assert [(r.levelno, r.getMessage()) for r in lignes] == [
        (logging.WARNING, "Reprise des descriptions profil « t », linkedin : 1 récupérée(s), arrêt sur une erreur 429, 2 restante(s)")
    ]


def test_reprise_complete_journalise_en_info_et_sans_offre_manquante_rien(tmp_path, caplog):
    base = tmp_path / "a.db"
    with caplog.at_level(logging.INFO, logger=NOM_JOURNAL):
        lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [])
    assert _lignes_reprise(caplog) == []
    manquante = _manquante("u1")
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [manquante])
    reponses = {"u1": jobspy_adapter.DescriptionRecuperee("Description récupérée", False)}
    with caplog.at_level(logging.INFO, logger=NOM_JOURNAL):
        lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [], recuperateur=_recuperateur(reponses, []))
    assert [(r.levelno, r.getMessage()) for r in _lignes_reprise(caplog)] == [
        (logging.INFO, "Reprise des descriptions profil « t », linkedin : 1 récupérée(s), 0 restante(s)")
    ]


def test_annulation_pendant_la_reprise_conserve_les_descriptions_recuperees_et_arrete_la_recherche(tmp_path, caplog):
    base = tmp_path / "a.db"
    manquantes = _trois_manquantes()
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: manquantes)
    appels, recherches = [], []
    reponses = {u: jobspy_adapter.DescriptionRecuperee(f"Description {u}", False) for u in ("u1", "u2", "u3")}
    recuperateur = _recuperateur(reponses, appels)
    with caplog.at_level(logging.INFO, logger=NOM_JOURNAL), pytest.raises(RechercheAnnulee):
        lancer_recherche(
            _profil_simple(), base, rechercheur=_rechercheur_qui_note(recherches, []), recuperateur=recuperateur,
            annulee=lambda: len(appels) >= 1,
        )
    assert appels == ["u1"] and recherches == []
    with Stockage(base) as stockage:
        assert [m.offre.url for m in stockage.lister_manquantes("t")] == ["u2", "u3"]
        assert [o.url for o in stockage.lister("t", NOUVELLE)] == ["u1"]
    assert [(r.levelno, r.getMessage()) for r in _lignes_reprise(caplog)] == [
        (logging.INFO, "Reprise des descriptions profil « t », linkedin : 1 récupérée(s), annulée, 2 restante(s)")
    ]


def _lignes_blocage(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records if r.getMessage().startswith("Blocage")]


def test_journal_erreur_429_donne_le_nombre_reel_de_descriptions_a_recuperer(tmp_path, caplog):
    base = tmp_path / "a.db"
    deja_en_base = _offre("u1")
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [deja_en_base])
    renvoyees = [replace(deja_en_base, description="", description_bloquee=True), _manquante("u2")]
    # u1 était déjà en base avec sa description : seule u2 reste à récupérer, bien que les deux aient été renvoyées sans description.
    with caplog.at_level(logging.INFO, logger=NOM_JOURNAL):
        lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: renvoyees)
    assert _lignes_blocage(caplog) == [
        (logging.WARNING, "Blocage profil « t », linkedin : erreur 429 sur des pages d'offre, 1 description(s) à récupérer à la prochaine recherche.")
    ]
    with Stockage(base) as stockage:
        assert [m.offre.url for m in stockage.lister_manquantes("t")] == ["u2"]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=NOM_JOURNAL):
        lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [deja_en_base])
    assert _lignes_blocage(caplog) == []


def test_reprise_signale_son_debut_et_sa_fin_seulement_avec_des_offres_manquantes(tmp_path):
    base = tmp_path / "a.db"
    signaux = []
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [], reprise_en_cours=signaux.append)
    assert signaux == []
    manquante = _manquante("u1")
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [manquante])
    reponses = {"u1": jobspy_adapter.DescriptionRecuperee("Description", False)}
    lancer_recherche(
        _profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [], recuperateur=_recuperateur(reponses, []), reprise_en_cours=signaux.append
    )
    assert signaux == [True, False]


def _rechercheur_qui_note(kwargs_recus, resultats):
    def rechercher(terme, lieu, **kw):
        kwargs_recus.append(kw)
        return resultats
    return rechercher


def test_reprise_bloquee_par_un_429_lance_la_recherche_sans_recuperation_de_description(tmp_path):
    base = tmp_path / "a.db"
    manquante = _manquante("u1")
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [manquante])
    reponses = {"u1": jobspy_adapter.DescriptionRecuperee("", True)}
    recus = []
    nouvelle_manquante = _manquante("u2")
    lancer_recherche(
        _profil_simple(), base, rechercheur=_rechercheur_qui_note(recus, [nouvelle_manquante]), recuperateur=_recuperateur(reponses, [])
    )
    assert [kw["recuperer_descriptions"] for kw in recus] == [False]
    with Stockage(base) as stockage:
        assert [m.offre.url for m in stockage.lister_manquantes("t")] == ["u1", "u2"]


def test_reprise_non_bloquee_ou_sans_offre_manquante_garde_la_recuperation_de_description(tmp_path):
    base = tmp_path / "a.db"
    recus = []
    lancer_recherche(_profil_simple(), base, rechercheur=_rechercheur_qui_note(recus, []))
    manquante = _manquante("u1")
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [manquante])
    reponses = {"u1": jobspy_adapter.DescriptionRecuperee("Description récupérée", False)}
    lancer_recherche(_profil_simple(), base, rechercheur=_rechercheur_qui_note(recus, []), recuperateur=_recuperateur(reponses, []))
    assert [kw["recuperer_descriptions"] for kw in recus] == [True, True]


def _offre_linkedin(numero, description="Description de l'offre", bloquee=False):
    return replace(
        _offre(f"https://www.linkedin.com/jobs/view/{numero}", description=description), source="linkedin", description_bloquee=bloquee
    )


def test_offre_linkedin_deja_en_base_est_transmise_comme_connue_et_garde_sa_description_stockee(tmp_path):
    base = tmp_path / "a.db"
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [_offre_linkedin("4480001", "Description stockée")])
    recus = []
    revenue_sans_description = _offre_linkedin("4480001", description="")
    resultat = lancer_recherche(_profil_simple(), base, rechercheur=_rechercheur_qui_note(recus, [revenue_sans_description]))
    assert recus[0]["ids_connus"] == frozenset({"4480001"})
    assert resultat.nb_retenues == 1
    with Stockage(base) as stockage:
        assert [o.description for o in stockage.lister("t")] == ["Description stockée"]
        assert stockage.lister_manquantes("t") == []


def test_offre_linkedin_manquante_n_est_pas_transmise_comme_connue(tmp_path):
    base = tmp_path / "a.db"
    lancer_recherche(_profil_simple(), base, rechercheur=lambda terme, lieu, **kw: [_offre_linkedin("4480001", "", bloquee=True)])
    recus = []
    lancer_recherche(
        _profil_simple(), base, rechercheur=_rechercheur_qui_note(recus, []),
        recuperateur=_recuperateur({"https://www.linkedin.com/jobs/view/4480001": jobspy_adapter.DescriptionRecuperee("", True)}, []),
    )
    assert recus[0]["ids_connus"] == frozenset()


def test_lancer_recherche_enregistre_les_offres_hors_zone_comme_ecartees(tmp_path):
    resultats = [_offre("u1", lieu="Lamanon, 13"), _offre("u2", lieu="France")]
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lamanon, France", rayon_km=0)
    bilan = lancer_recherche(profil, tmp_path / "a.db", rechercheur=lambda terme, lieu, **kw: resultats)
    assert (bilan.nb_retenues, bilan.nb_ecartees) == (1, 1)
    with Stockage(tmp_path / "a.db") as stockage:
        assert [o.url for o in stockage.lister("t", ECARTEE)] == ["u2"]


def test_main_exporte_csv_nouvelles_et_deja_vues(tmp_path, monkeypatch):
    base, sortie = tmp_path / "alertes.db", tmp_path / "offres.csv"
    _creer_profil(base)
    resultats = [[_offre("u1")], [_offre("u1"), _offre("u2")]]
    _simuler_jobspy(monkeypatch, lambda: _jobs_depuis(resultats.pop(0)))
    assert main(["x", "--base", str(base)]) == 0
    assert not sortie.exists()
    assert main(["x", "--base", str(base), "--csv", str(sortie)]) == 0
    assert [l.split(";")[3] for l in sortie.read_text(encoding="utf-8-sig").splitlines()[1:]] == ["u1", "u2"]


def test_main_affiche_le_meme_bilan_apres_extraction_de_lancer_recherche(tmp_path, monkeypatch, capsys):
    base = tmp_path / "alertes.db"
    _creer_profil(base)
    _simuler_jobspy(monkeypatch, lambda: _jobs_depuis([_offre("u1", titre="Poste u1")]))
    assert main(["x", "--base", str(base)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        "1 offre(s) nouvelle(s), 0 déjà vue(s), 0 écartée(s) pour le profil « x ».",
        "- Poste u1 | Acme | Lyon | u1",
    ]
    assert main(["x", "--base", str(base)]) == 0
    assert capsys.readouterr().out.splitlines() == ["0 offre(s) nouvelle(s), 1 déjà vue(s), 0 écartée(s) pour le profil « x »."]


def test_main_profil_introuvable_renvoie_2(tmp_path, capsys):
    base = tmp_path / "alertes.db"
    _creer_profil(base)
    assert main(["absent", "--base", str(base)]) == 2
    assert "introuvable" in capsys.readouterr().err


def test_blocage_au_second_terme_annule_toute_la_recherche_du_profil(tmp_path):
    appels = []

    def bloque_au_second(terme, lieu, **kw):
        appels.append(terme)
        if len(appels) == 2:
            raise RechercheBloquee("indeed")
        return [_offre("u1")]

    profil = Profil(nom="t", termes_recherche=("a", "b"), exiger_termes=False, lieu="Lyon")
    base = tmp_path / "a.db"
    with pytest.raises(RechercheBloquee):
        lancer_recherche(profil, base, rechercheur=bloque_au_second)
    with Stockage(base) as stockage:
        assert stockage.lister("t", NOUVELLE) == [] and stockage.lister("t", VUE) == [] and stockage.lister("t", ECARTEE) == []
        assert stockage.dernieres_mises_a_jour() == {}


def test_annulation_entre_deux_termes_n_enregistre_rien(tmp_path):
    appels = []
    demande = []

    def rechercheur(terme, lieu, **kw):
        appels.append(terme)
        demande.append(True)  # l'utilisateur annule pendant le premier terme
        return [_offre("u1")]

    profil = Profil(nom="t", termes_recherche=("a", "b"), exiger_termes=False, lieu="Lyon")
    base = tmp_path / "a.db"
    with pytest.raises(RechercheAnnulee):
        lancer_recherche(profil, base, rechercheur=rechercheur, annulee=lambda: bool(demande))
    assert appels == ["a"]
    with Stockage(base) as stockage:
        assert stockage.lister("t", NOUVELLE) == [] and stockage.lister("t", VUE) == [] and stockage.lister("t", ECARTEE) == []
        assert stockage.dernieres_mises_a_jour() == {}


def test_sans_annulation_demandee_tous_les_termes_sont_cherches():
    appels = []
    profil = Profil(nom="t", termes_recherche=("a", "b"), exiger_termes=False, lieu="Lyon")
    rechercher_profil(profil, lambda terme, lieu, **kw: appels.append(terme) or [], annulee=lambda: False)
    assert appels == ["a", "b"]


def test_main_blocage_429_renvoie_3_et_explique_sur_stderr(tmp_path, monkeypatch, capsys):
    _creer_profil(tmp_path / "alertes.db")

    def bloquee():
        # JobSpy ne lève rien sur un blocage : il le journalise, et l'adaptateur le détecte via ce journal.
        logging.getLogger("jobspy.indeed").error("429 Too Many Requests")
        return _jobs_depuis([])

    _simuler_jobspy(monkeypatch, bloquee)
    assert main(["x", "--base", str(tmp_path / "alertes.db")]) == 3
    sortie = capsys.readouterr()
    assert "429" in sortie.err and "relancez" in sortie.err
    assert sortie.out == ""


def test_exclusions_globales_s_ajoutent_a_celles_du_profil():
    stage, paris, lyon = _offre("u1", titre="Stage dev"), _offre("u2", lieu="Paris"), _offre("u3", titre="Alternance", lieu="Lyon")
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lyon", mots_exclus=("alternance",))
    globaux = ParametresGlobaux(mots_exclus=("stage",), villes_exclues=("Paris",))
    retenues, ecartees = appliquer_regles([stage, paris, lyon, _offre("u4")], profil, globaux)
    assert [o.url for o in retenues] == ["u4"]
    assert ecartees == [(stage, "exclu : stage"), (lyon, "exclu : alternance"), (paris, "ville exclue : Paris")]


def test_lancer_recherche_applique_les_exclusions_globales(tmp_path):
    resultats = [_offre("u1", titre="Stage dev"), _offre("u2")]
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lyon")
    bilan = lancer_recherche(
        profil, tmp_path / "a.db", rechercheur=lambda terme, lieu, **kw: resultats, globaux=ParametresGlobaux(mots_exclus=("stage",))
    )
    assert (bilan.nb_retenues, bilan.nb_ecartees) == (1, 1)


def _profil_a_un_terme():
    return Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lyon")


def test_rechercher_profil_garde_la_source_la_mieux_classee_quelle_que_soit_l_ordre():
    glassdoor = replace(_offre("glassdoor/1", titre="Expert"), source="glassdoor")
    indeed = replace(_offre("indeed/1", titre="Expert"), source="indeed")
    for resultats in ([glassdoor, indeed], [indeed, glassdoor]):
        offres = rechercher_profil(_profil_a_un_terme(), rechercheur=lambda terme, lieu, **kw: resultats, priorite=("indeed", "glassdoor"))
        assert [o.url for o in offres] == ["indeed/1"]


def test_rechercher_profil_sans_priorite_garde_le_premier_rencontre():
    glassdoor = replace(_offre("glassdoor/1", titre="Expert"), source="glassdoor")
    indeed = replace(_offre("indeed/1", titre="Expert"), source="indeed")
    offres = rechercher_profil(_profil_a_un_terme(), rechercheur=lambda terme, lieu, **kw: [glassdoor, indeed])
    assert [o.url for o in offres] == ["glassdoor/1"]


def test_lancer_recherche_transmet_la_priorite_des_sites(tmp_path):
    profil = Profil(nom="t", termes_recherche=("a",), exiger_termes=False, lieu="Lyon")
    base = tmp_path / "a.db"
    for source in ("glassdoor", "indeed"):
        resultats = [replace(_offre(f"{source}/1", titre="Expert"), source=source)]
        lancer_recherche(
            profil, base, rechercheur=lambda terme, lieu, **kw: resultats, globaux=ParametresGlobaux(priorite_sites=("indeed", "glassdoor"))
        )
    with Stockage(base) as stockage:
        assert [(o.source, o.url) for o in stockage.lister("t")] == [("indeed", "indeed/1")]


def test_main_charge_les_exclusions_globales_de_la_base(tmp_path, monkeypatch):
    base = tmp_path / "a.db"
    _creer_profil(base, nom="t")
    with Stockage(base) as stockage:
        stockage.enregistrer_globaux({"mots_exclus": ["stage"]})
    _simuler_jobspy(monkeypatch, lambda: _jobs_depuis([_offre("u1", titre="Stage dev"), _offre("u2")]))
    assert main(["t", "--base", str(base)]) == 0
    with Stockage(base) as stockage:
        assert [o.url for o in stockage.lister("t", ECARTEE)] == ["u1"]


def test_main_importe_les_anciens_fichiers_json_du_dossier_profils(tmp_path, monkeypatch):
    base = tmp_path / "a.db"
    (tmp_path / "profils").mkdir()
    (tmp_path / "profils" / "p.json").write_text(json.dumps({"nom": "t", "termes_recherche": ["a"], "lieu": "Lyon", "exiger_termes": False}), encoding="utf-8")
    (tmp_path / "profils" / "_global.json").write_text(json.dumps({"mots_exclus": ["stage"]}), encoding="utf-8")
    _simuler_jobspy(monkeypatch, lambda: _jobs_depuis([_offre("u1", titre="Stage dev"), _offre("u2")]))
    assert main(["t", "--base", str(base)]) == 0
    with Stockage(base) as stockage:
        assert [o.url for o in stockage.lister("t", ECARTEE)] == ["u1"]
    assert (tmp_path / "profils" / "p.json.importe").exists()
