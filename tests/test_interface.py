import http.client
import json
import threading
import time
from datetime import date, datetime, timedelta

import pytest

from alertes_emploi.interface import serveur
from alertes_emploi.modele import Offre
from alertes_emploi.profils.chargeur import ParametresGlobaux
from alertes_emploi.recherche import RechercheAnnulee, ResultatRecherche
from alertes_emploi.sources.jobspy_adapter import RechercheBloquee
from alertes_emploi.stockage.base import ECARTEE, NOUVELLE, VUE, OffreStockee, Stockage


def _stockee(titre="Poste", societe="Acme", url="https://exemple.fr/1", statut=NOUVELLE, profil="p"):
    offre = Offre(titre=titre, societe=societe, lieu="Lyon", url=url, description="Desc", source="indeed")
    return OffreStockee(offre, statut, "2026-10-31T10:00:00", "2026-10-31T10:00:00", profil)


def _offre_e(titre="Poste a"):
    return Offre(titre, "Acme", "Lyon", "https://e.fr/1", "d", "indeed")


def _donnees_profil(nom):
    return {"nom": nom, "termes_recherche": ["a"], "lieu": "Lyon"}


@pytest.fixture
def base(tmp_path):
    return tmp_path / "alertes.db"


def _noms_profils(base):
    with Stockage(base) as stockage:
        return list(stockage.lister_profils())


def _profil_en_base(base, nom):
    with Stockage(base) as stockage:
        return stockage.charger_profil(nom)


def _globaux_en_base(base):
    with Stockage(base) as stockage:
        return stockage.charger_globaux()


def _enregistrer_globaux(base, donnees):
    with Stockage(base) as stockage:
        stockage.enregistrer_globaux(donnees)


def test_titre_et_societe_echappes():
    page = serveur.construire_page([_stockee(titre="<script>alert(1)</script>", societe="Dupont & Fils")], ["p"])
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "Dupont &amp; Fils" in page


def test_markdown_titres_gras_puces_et_paragraphes():
    html = serveur._markdown("### **Missions**\n\nTexte un\nTexte deux\n\n* Aider\n* Veiller\nSuite")
    assert html == "<h4><strong>Missions</strong></h4><p>Texte un<br>Texte deux</p><ul><li>Aider</li><li>Veiller</li></ul><p>Suite</p>"


def test_markdown_retire_les_antislashs_des_caracteres_echappes():
    html = serveur._markdown("\\*\\*Missions\\*\\*\n\\- Aider")
    assert html == "<p><strong>Missions</strong></p><ul><li>Aider</li></ul>"


def test_markdown_echappe_le_html_avant_les_balises():
    html = serveur._markdown("<script>alert(1)</script> **gras**")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>gras</strong>" in html


def test_url_javascript_non_cliquable():
    page = serveur.construire_page([_stockee(url="javascript:alert(1)")], ["p"])
    assert 'href="javascript:' not in page


def test_url_https_cliquable_dans_un_nouvel_onglet():
    page = serveur.construire_page([_stockee(url="https://exemple.fr/1")], ["p"])
    assert '<a href="https://exemple.fr/1" target="_blank" rel="noopener noreferrer">Voir l\'annonce</a>' in page


def test_pastille_nouvelle_seulement_pour_une_offre_nouvelle():
    assert 'class="pastille"' in serveur.construire_page([_stockee(statut=NOUVELLE)], ["p"]).split("<tbody")[1]
    assert 'class="pastille"' not in serveur.construire_page([_stockee(statut=VUE)], ["p"]).split("<tbody")[1]


def test_colonne_profil_filtre_et_attribut_data_profil():
    page = serveur.construire_page([_stockee(profil="a"), _stockee(profil="b", url="https://exemple.fr/2")], ["a", "b"])
    assert '<option value="a">a</option><option value="b">b</option>' in page
    lignes = page.split("</thead>")[1]
    assert 'data-profil="a"' in lignes and 'data-profil="b"' in lignes
    assert "<td>a</td>" in lignes and "<td>b</td>" in lignes


def test_page_affiche_la_date_lisible_ou_jamais():
    page = serveur.construire_page([], ["a", "b"], dates={"a": "2026-11-01T14:05:09"})
    assert "<tr><td>a</td><td>01/11/2026 14:05</td></tr>" in page
    assert "<tr><td>b</td><td>jamais</td></tr>" in page


def test_dernieres_mises_a_jour_triees_sans_date_d_abord_puis_du_plus_ancien():
    dates = {"recent": "2026-11-03T09:00:00", "ancien": "2026-10-01T09:00:00", "milieu": "2026-10-20T09:00:00"}
    page = serveur.construire_page([], ["recent", "jamais", "ancien", "milieu"], dates=dates)
    bloc = page.split('<div id="dates">')[1].split("</div>")[0]
    assert "<th>Profil</th><th>Date</th>" in bloc
    assert [ligne.split("</td>")[0] for ligne in bloc.split("<tr><td>")[1:]] == ["jamais", "ancien", "milieu", "recent"]


def test_script_ne_masque_plus_les_mises_a_jour_selon_le_profil():
    page = serveur.construire_page([], ["a", "b"], dates={"a": "2026-11-01T14:05:09"})
    assert "#dates" not in page
    assert page.count('id="dates"') == 1


def test_dates_au_format_jj_mm_aaaa_et_attributs_data_restent_iso():
    offre = Offre(titre="Poste", societe="Acme", lieu="Lyon", url="https://exemple.fr/1", description="Desc", source="indeed")
    stockee = OffreStockee(offre, NOUVELLE, "2026-10-31T10:05:00", "2026-10-31T10:05:00", "p")
    ligne = serveur.construire_page([stockee], ["p"]).split("<tbody class=\"offre\"")[1].split("</tr>")[0]
    assert "<td>31/10/2026 10:05</td>" in ligne and "publication" not in ligne
    assert 'data-vue="2026-10-31T10:05:00"' in ligne


def test_entete_regroupe_profils_reglages_journal_et_mises_a_jour():
    page = serveur.construire_page([], ["a"], dates={"a": "2026-11-01T14:05:09"})
    entete, reste = page.split("</header>")
    assert '<summary class="bouton">Réglages</summary>' in entete
    for identifiant in ("auto", "globaux", "maj-dates", "journal", "raccourci", "jobspy"):
        assert f'<button id="{identifiant}"' in entete
    assert 'id="nouveau"' not in entete
    avant_scripts = reste.split("<script")[0]
    assert 'id="journal"' not in avant_scripts and 'id="auto"' not in avant_scripts
    barre_profil = avant_scripts.split('<section class="carte barre">')[1].split("</section>")[0]
    assert barre_profil.index('id="nouveau"') < barre_profil.index('id="modifier"')
    assert 'id="supprimer"' in barre_profil and 'id="maj"' in barre_profil
    dialogue = avant_scripts.split('<dialog id="dlg-maj">')[1].split("</dialog>")[0]
    assert "Dernières recherches" in dialogue and '<div id="dates">' in dialogue and "<tr><td>a</td><td>01/11/2026 14:05</td></tr>" in dialogue
    assert 'id="bandeau"' in avant_scripts and 'id="etat"' not in page


def test_page_propose_la_popup_recherche_auto_et_vise_le_profil_le_plus_ancien():
    definitions = {nom: serveur.Profil(nom, ("a",), "Lyon") for nom in ("a", "b")}
    page = serveur.construire_page([], ["a", "b"], dates={"a": "2026-11-05T10:00:00", "b": "2026-11-01T14:05:00"}, definitions=definitions,
                                   globaux=ParametresGlobaux(recherche_auto_demarrage=False, delai_recherche_auto_minutes=120))
    entete, reste = page.split("</header>")
    assert '<button id="auto" type="button">Recherche auto</button>' in entete
    dialogue = reste.split('<dialog id="dlg-auto">')[1].split("</dialog>")[0]
    assert 'id="a-demarrage"' in dialogue and 'id="a-delai"' in dialogue and 'min="5" max="1440"' in dialogue and 'id="a-lancer"' in dialogue
    assert '<script id="auto-data" type="application/json">{"profil": "b", "date": "01/11/2026 14:05", "derniere": ""}</script>' in page
    assert '"recherche_auto_demarrage": false' in page and '"delai_recherche_auto_minutes": 120' in page


def test_page_sans_profil_enregistre_n_a_pas_de_cible_de_recherche_auto():
    page = serveur.construire_page([], [])
    assert '<script id="auto-data" type="application/json">{"profil": null, "date": null, "derniere": ""}</script>' in page


def test_formulaires_globaux_et_recherche_auto_conservent_les_reglages_de_l_autre():
    page = serveur.construire_page([], ["a"])
    assert 'envoyer("/global", {...globaux, mots_exclus:' in page
    assert 'envoyer("/global", {...globaux, recherche_auto_demarrage:' in page


def test_page_conserve_le_profil_cree_ou_modifie_apres_rechargement():
    page = serveur.construire_page([], ["a"])
    assert 'sessionStorage.setItem("profil", profil.nom)' in page
    assert 'sessionStorage.getItem("profil")' in page
    # Restauration avant le premier filtrage, sinon la page s'afficherait d'abord sur « Tous ».
    assert page.index('getItem("profil")') < page.index("filtrer();\ntry {\n  const bilan")


def test_page_conserve_le_profil_apres_une_recherche():
    page = serveur.construire_page([], ["a"])
    assert 'sessionStorage.setItem("profil", champ("f-profil").value)' in page
    assert ">Rechercher</button>" in page
    assert "Mettre à jour</button>" not in page


def test_nom_de_profil_echappe_dans_la_liste_les_lignes_et_les_mises_a_jour():
    page = serveur.construire_page([_stockee(profil="<b>x</b>")], ["<b>x</b>"], {"<b>x</b>": "2026-11-01T14:05:09"})
    assert "<b>x</b>" not in page
    assert "&lt;b&gt;x&lt;/b&gt;" in page and "<tr><td>&lt;b&gt;x&lt;/b&gt;</td><td>01/11/2026 14:05</td></tr>" in page


@pytest.fixture
def lancer_serveur(base, monkeypatch):
    serveurs = []
    with Stockage(base) as stockage:
        stockage.creer_profil(_donnees_profil("t"))
        stockage.creer_profil(_donnees_profil("u"))

    def demarrer(faux_lancer_recherche, lanceur=None, demarrage=False, jobspy=None):
        # Les faux rechercheurs ignorent les paramètres globaux ; ils sont consignés dans demarrer.globaux.
        def adaptateur(profil, base, globaux=None, annulee=None, reprise_en_cours=None):
            demarrer.globaux.append(globaux)
            demarrer.annulees.append(annulee)
            demarrer.reprises.append(reprise_en_cours)
            return faux_lancer_recherche(profil, base)

        monkeypatch.setattr(serveur, "lancer_recherche", adaptateur)
        instance = serveur.creer_serveur(base, 0, lanceur, mise_a_jour_au_demarrage=demarrage, jobspy=jobspy)
        threading.Thread(target=instance.serve_forever, daemon=True).start()
        serveurs.append(instance)
        return instance.server_address[1]

    demarrer.globaux = []
    demarrer.annulees = []  # test d'annulation de chaque recherche lancée, pour que les faux rechercheurs l'interrogent
    demarrer.reprises = []  # rappel de chaque recherche lancée signalant le début et la fin de la reprise des descriptions
    yield demarrer
    for instance in serveurs:
        instance.shutdown()
        instance.server_close()


def _requete(port, methode, chemin, origine=None, corps=None):
    connexion = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connexion.request(methode, chemin, body=json.dumps(corps) if corps is not None else None, headers={"Origin": origine} if origine else {})
    reponse = connexion.getresponse()
    corps = reponse.read()
    connexion.close()
    return reponse.status, corps


def _attendre_fin(port):
    for _ in range(100):
        etat = json.loads(_requete(port, "GET", "/etat")[1])
        if not etat["en_cours"]:
            return etat
        time.sleep(0.05)
    raise AssertionError("la mise à jour ne se termine pas")


def test_journal_absent_renvoie_un_texte_vide(lancer_serveur):
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    assert _requete(port, "GET", "/journal") == (200, b"")


def test_journal_ne_renvoie_que_les_200_dernieres_lignes(lancer_serveur, tmp_path):
    (tmp_path / "journal.log").write_text("\n".join(f"ligne {i}" for i in range(250)), encoding="utf-8")
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    statut, corps = _requete(port, "GET", "/journal")
    lignes = corps.decode("utf-8").splitlines()
    assert statut == 200
    assert lignes[0] == "ligne 50" and lignes[-1] == "ligne 249" and len(lignes) == 200


def test_page_contient_le_bouton_et_la_popup_du_journal(lancer_serveur):
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    page = _requete(port, "GET", "/")[1]
    assert b'<button id="journal"' in page and b'<dialog id="dlg-journal">' in page
    assert b'href="/journal"' not in page


def test_post_avec_origine_etrangere_refuse(lancer_serveur):
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", "http://autre.example")[0] == 403


def test_post_avec_origine_du_serveur_accepte(lancer_serveur):
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 2, 1))
    assert _requete(port, "POST", "/mettre-a-jour", f"http://127.0.0.1:{port}", {"profil": "t"})[0] == 202
    etat = _attendre_fin(port)
    assert etat["bilan"] == {"nouvelles": 0, "retenues": 2, "ecartees": 1}
    assert etat["erreur"] is None


def test_second_post_pendant_une_recherche_renvoie_409(lancer_serveur):
    liberation = threading.Event()

    def lente(profil, base):
        liberation.wait(5)
        return ResultatRecherche([], 0, 0)

    port = lancer_serveur(lente)
    try:
        assert _requete(port, "POST", "/mettre-a-jour", f"http://127.0.0.1:{port}", {"profil": "t"})[0] == 202
        assert _requete(port, "POST", "/mettre-a-jour", f"http://127.0.0.1:{port}", {"profil": "t"})[0] == 409
    finally:
        liberation.set()
    _attendre_fin(port)


def test_annuler_arrete_la_recherche_sans_erreur_ni_date_de_mise_a_jour(lancer_serveur, base):
    demarree, liberation = threading.Event(), threading.Event()

    def interruptible(profil, base):
        demarree.set()
        liberation.wait(5)
        if lancer_serveur.annulees[-1]():
            raise RechercheAnnulee()
        return ResultatRecherche([], 0, 0)

    port = lancer_serveur(interruptible)
    assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
    assert demarree.wait(5)
    assert _requete(port, "POST", "/recherche/annuler", _local(port))[0] == 202
    liberation.set()
    etat = _attendre_fin(port)
    assert etat["annulee"] is True and etat["erreur"] is None and etat["bilan"] is None
    with Stockage(base) as stockage:
        assert stockage.dernieres_mises_a_jour() == {}
    # Une recherche suivante repart sans demande d'annulation.
    assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
    assert _attendre_fin(port)["annulee"] is False


def test_etat_expose_la_phase_de_reprise_et_refuse_une_recherche_pendant_la_reprise(lancer_serveur):
    en_reprise, liberation = threading.Event(), threading.Event()

    def avec_reprise(profil, base):
        lancer_serveur.reprises[-1](True)
        en_reprise.set()
        liberation.wait(5)
        lancer_serveur.reprises[-1](False)
        return ResultatRecherche([], 0, 0)

    port = lancer_serveur(avec_reprise)
    assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
    assert en_reprise.wait(5)
    try:
        etat = json.loads(_requete(port, "GET", "/etat")[1])
        assert etat["en_cours"] is True and etat["reprise"] is True
        assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "u"})[0] == 409
        assert _requete(port, "POST", "/recherche/auto/lancer", _local(port), {})[0] == 409
    finally:
        liberation.set()
    assert _attendre_fin(port)["reprise"] is False


def test_page_affiche_la_phase_de_reprise_avec_le_bouton_annuler():
    page = serveur.construire_page([], ["a"])
    assert "Reprise des descriptions en cours…" in page and "e.reprise" in page


def test_annuler_sans_recherche_ou_pendant_l_installation_de_jobspy_renvoie_409_et_exige_l_origine(lancer_serveur):
    attente = threading.Event()
    port = lancer_serveur(_inutile, jobspy=_FauxJobspy(attente=attente))
    assert _requete(port, "POST", "/recherche/annuler", _local(port))[0] == 409
    assert _requete(port, "POST", "/recherche/annuler", "http://evil.example")[0] == 403
    assert _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": "1.5.0"})[0] == 202
    statut = _requete(port, "POST", "/recherche/annuler", _local(port))[0]
    attente.set()
    _attendre_fin(port)
    assert statut == 409


def test_etat_renvoie_l_erreur_du_rechercheur(lancer_serveur):
    def echec(profil, base):
        raise RuntimeError("Indeed injoignable")

    port = lancer_serveur(echec)
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "t"})[0] == 202
    etat = _attendre_fin(port)
    assert etat["bilan"] is None
    assert "Indeed injoignable" in etat["erreur"]


def test_etat_d_un_blocage_429_donne_le_message_complet_sans_nom_de_classe(lancer_serveur):
    def bloquee(profil, base):
        raise RechercheBloquee("indeed")

    port = lancer_serveur(bloquee)
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "t"})[0] == 202
    etat = _attendre_fin(port)
    assert etat["bilan"] is None
    assert "429" in etat["erreur"] and "relancez" in etat["erreur"] and "indeed" in etat["erreur"]
    assert "RechercheBloquee" not in etat["erreur"]


def test_date_notee_apres_un_succes_et_pas_apres_une_erreur(lancer_serveur, base):
    def selon_profil(profil, base):
        if profil.nom == "u":
            raise RuntimeError("bloqué")
        return ResultatRecherche([], 0, 0)

    port = lancer_serveur(selon_profil)
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "t"})[0] == 202
    _attendre_fin(port)
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "u"})[0] == 202
    _attendre_fin(port)
    with Stockage(base) as stockage:
        assert list(stockage.dernieres_mises_a_jour()) == ["t"]


def test_post_profil_inconnu_refuse_sans_lancer_de_recherche(lancer_serveur):
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "../secret"})[0] == 400
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": 3})[0] == 400
    assert appels == []


def test_post_sans_nom_ou_nom_vide_refuse_sans_lancer_de_recherche(lancer_serveur):
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": ""})[0] == 400
    assert _requete(port, "POST", "/mettre-a-jour", corps={})[0] == 400
    assert _requete(port, "POST", "/mettre-a-jour")[0] == 400
    assert appels == []


def test_post_content_length_negatif_refuse_sans_bloquer(lancer_serveur):
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    connexion = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    connexion.putrequest("POST", "/mettre-a-jour")
    connexion.putheader("Content-Length", "-1")
    connexion.endheaders()
    reponse = connexion.getresponse()
    connexion.close()
    assert reponse.status == 400
    assert appels == []


_MAINTENANT = datetime(2026, 11, 5, 12, 0, 0)
_PROFILS = ["b", "a"]


def test_profil_le_plus_ancien_sans_profil_ne_donne_rien():
    assert serveur.profil_le_plus_ancien([], {}, _MAINTENANT) is None


def test_profil_le_plus_ancien_jamais_mis_a_jour_passe_en_premier_puis_ordre_alphabetique():
    assert serveur.profil_le_plus_ancien(_PROFILS, {}, _MAINTENANT) == "a"
    assert serveur.profil_le_plus_ancien(_PROFILS, {"a": "2026-11-05T10:00:00"}, _MAINTENANT) == "b"


def test_profil_le_plus_ancien_choisit_la_date_la_plus_ancienne():
    dates = {"a": "2026-11-05T09:00:00", "b": "2026-11-04T09:00:00"}
    assert serveur.profil_le_plus_ancien(_PROFILS, dates, _MAINTENANT) == "b"


def test_profil_le_plus_ancien_respecte_le_seuil_d_une_heure():
    maintenant = _MAINTENANT
    # Un seul profil récent suffit à ne rien lancer, même si l'autre est périmé.
    assert serveur.profil_le_plus_ancien(_PROFILS, {"a": "2026-11-04T09:00:00", "b": "2026-11-05T11:30:00"}, maintenant) is None
    assert serveur.profil_le_plus_ancien(_PROFILS, {"a": "2026-11-05T11:00:01", "b": "2026-11-05T09:00:00"}, maintenant) is None
    # Tous périmés : le plus ancien est lancé.
    assert serveur.profil_le_plus_ancien(_PROFILS, {"a": "2026-11-05T10:59:59", "b": "2026-11-05T10:00:00"}, maintenant) == "b"
    assert serveur.profil_le_plus_ancien(_PROFILS, {"a": "2026-11-05T10:00:00", "b": "2026-11-05T10:59:59"}, maintenant) == "a"


def test_profil_le_plus_ancien_tient_compte_de_la_date_d_un_profil_disparu():
    dates = {"a": "2026-11-05T08:00:00", "b": "2026-11-05T08:00:00", "disparu": "2026-11-05T11:30:00"}
    assert serveur.profil_le_plus_ancien(_PROFILS, dates, datetime(2026, 11, 5, 12, 0)) is None


def test_profil_le_plus_ancien_date_illisible_est_perimee():
    assert serveur.profil_le_plus_ancien(_PROFILS, {"a": "n'importe quoi", "b": "2026-11-05T09:00:00"}, _MAINTENANT) == "a"


def test_demarrage_lance_le_profil_le_plus_ancien(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.noter_mise_a_jour("t", datetime.now() - timedelta(days=2))
        stockage.noter_mise_a_jour("u", datetime.now() - timedelta(days=1))
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    _attendre_fin(port)
    assert appels == ["t"]


def test_demarrage_ne_lance_rien_si_la_derniere_recherche_date_de_moins_d_une_heure(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.noter_mise_a_jour("t", datetime.now() - timedelta(days=2))
        stockage.noter_recherche_auto()
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    assert json.loads(_requete(port, "GET", "/etat")[1])["en_cours"] is False
    assert appels == []


def test_profil_le_plus_ancien_delai_personnalise_et_delai_ignore():
    dates = {"a": "2026-11-05T09:00:00", "b": "2026-11-05T11:00:00"}
    maintenant = datetime(2026, 11, 5, 12, 0)
    assert serveur.profil_le_plus_ancien(_PROFILS, dates, maintenant, timedelta(minutes=30)) == "a"
    assert serveur.profil_le_plus_ancien(_PROFILS, dates, maintenant, timedelta(minutes=90)) is None
    assert serveur.profil_le_plus_ancien(_PROFILS, dates, maintenant, timedelta(minutes=90), ignorer_delai=True) == "a"


def test_demarrage_desactive_dans_les_reglages_ne_lance_rien(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer_globaux({"recherche_auto_demarrage": False})
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    assert json.loads(_requete(port, "GET", "/etat")[1])["en_cours"] is False
    assert appels == []


def test_demarrage_utilise_le_delai_des_reglages(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer_globaux({"delai_recherche_auto_minutes": 5})
        stockage.noter_mise_a_jour("t", datetime.now() - timedelta(minutes=30))
        stockage.noter_mise_a_jour("u", datetime.now() - timedelta(minutes=10))
        stockage.noter_recherche_auto(datetime.now() - timedelta(minutes=10))
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    _attendre_fin(port)
    assert appels == ["t"]


def test_demarrage_delai_long_des_reglages_empeche_la_recherche(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer_globaux({"delai_recherche_auto_minutes": 1440})
        stockage.noter_mise_a_jour("t", datetime.now() - timedelta(hours=5))
        stockage.noter_mise_a_jour("u", datetime.now() - timedelta(hours=3))
        stockage.noter_recherche_auto(datetime.now() - timedelta(hours=3))
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    assert json.loads(_requete(port, "GET", "/etat")[1])["en_cours"] is False
    assert appels == []


def test_demarrage_lance_si_la_derniere_recherche_est_absente_ou_plus_vieille_que_le_delai(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.noter_mise_a_jour("t")  # une mise à jour récente d'un profil ne suffit plus à empêcher la recherche
        stockage.noter_mise_a_jour("u")
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    _attendre_fin(port)
    assert len(appels) == 1
    with Stockage(base) as stockage:
        assert (datetime.now() - stockage.derniere_recherche_auto()).total_seconds() < 10


def test_demarrage_lance_si_la_derniere_recherche_depasse_le_delai(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.noter_recherche_auto(datetime.now() - timedelta(hours=2))
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0), demarrage=True)
    _attendre_fin(port)
    assert len(appels) == 1


def test_recherche_annulee_ou_en_erreur_ne_note_pas_la_date(lancer_serveur, base):
    def selon_profil(profil, base):
        if profil.nom == "t":
            raise RechercheAnnulee()
        raise RuntimeError("bloqué")

    port = lancer_serveur(selon_profil)
    for nom in ("t", "u"):
        assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": nom})[0] == 202
        _attendre_fin(port)
    with Stockage(base) as stockage:
        assert stockage.derniere_recherche_auto() is None


def test_recherche_manuelle_reussie_note_la_date(lancer_serveur, base):
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "t"})[0] == 202
    _attendre_fin(port)
    with Stockage(base) as stockage:
        assert stockage.derniere_recherche_auto() is not None


def test_recherche_auto_inscrit_sa_fin_dans_le_journal(lancer_serveur, caplog):
    caplog.set_level("INFO", logger="alertes_emploi")
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/recherche/auto/lancer", _local(port))[0] == 202
    _attendre_fin(port)
    assert any(r.levelname == "INFO" and "terminée" in r.getMessage() and "0 nouvelle" in r.getMessage() for r in caplog.records)


def test_recherche_auto_inscrit_son_annulation_et_son_erreur_dans_le_journal(lancer_serveur, caplog):
    caplog.set_level("INFO", logger="alertes_emploi")
    appels = []

    def selon_appel(profil, base):
        appels.append(profil.nom)
        if len(appels) == 1:
            raise RechercheAnnulee()
        raise RechercheBloquee("429 : relancez plus tard")

    port = lancer_serveur(selon_appel)
    for _ in range(2):
        assert _requete(port, "POST", "/recherche/auto/lancer", _local(port))[0] == 202
        _attendre_fin(port)
    messages = [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "alertes_emploi"]
    assert any(niveau == "INFO" and "annulée" in texte for niveau, texte in messages)
    assert any(niveau == "ERROR" and "429" in texte for niveau, texte in messages)


def test_recherche_manuelle_reste_muette_dans_le_journal(lancer_serveur, caplog):
    caplog.set_level("INFO", logger="alertes_emploi")
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "t"})[0] == 202
    _attendre_fin(port)
    assert [r for r in caplog.records if r.name == "alertes_emploi"] == []


def test_route_date_recherche_auto_ecrit_efface_et_refuse(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/recherche/auto/date", _local(port), {"date": "2026-11-06T10:15"})[0] == 200
    with Stockage(base) as stockage:
        assert stockage.derniere_recherche_auto() == datetime(2026, 11, 6, 10, 15)
    assert _requete(port, "POST", "/recherche/auto/date", _local(port), {"date": "pas une date"})[0] == 400
    assert _requete(port, "POST", "/recherche/auto/date", _local(port), {"date": 3})[0] == 400
    assert _requete(port, "POST", "/recherche/auto/date", "http://autre.exemple", {"date": ""})[0] == 403
    with Stockage(base) as stockage:
        assert stockage.derniere_recherche_auto() == datetime(2026, 11, 6, 10, 15)
    assert _requete(port, "POST", "/recherche/auto/date", _local(port), {"date": ""})[0] == 200
    with Stockage(base) as stockage:
        assert stockage.derniere_recherche_auto() is None


def test_page_prerempli_la_date_de_derniere_recherche_auto(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    assert b'id="a-date" type="datetime-local"' in _requete(port, "GET", "/")[1]
    with Stockage(base) as stockage:
        stockage.noter_recherche_auto(datetime(2026, 11, 6, 10, 15))
    assert b'"derniere": "2026-11-06T10:15"' in _requete(port, "GET", "/")[1]


def test_lancer_recherche_auto_vise_le_plus_ancien_en_ignorant_le_delai(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.noter_mise_a_jour("t", datetime.now())
        stockage.noter_mise_a_jour("u", datetime.now() - timedelta(minutes=10))
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    statut, corps = _requete(port, "POST", "/recherche/auto/lancer", _local(port))
    assert statut == 202 and json.loads(corps)["profil"] == "u"
    _attendre_fin(port)
    assert appels == ["u"]


def test_lancer_recherche_auto_409_si_une_recherche_tourne(lancer_serveur):
    liberer = threading.Event()
    port = lancer_serveur(lambda profil, base: liberer.wait(5) and ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "u"})[0] == 202
    try:
        assert _requete(port, "POST", "/recherche/auto/lancer", _local(port))[0] == 409
    finally:
        liberer.set()
        _attendre_fin(port)


def test_lancer_recherche_auto_400_sans_profil(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.supprimer_profil("t")
        stockage.supprimer_profil("u")
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/recherche/auto/lancer", _local(port))[0] == 400


def test_lancer_recherche_auto_403_si_origine_invalide(lancer_serveur):
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/recherche/auto/lancer", "http://autre.exemple")[0] == 403
    assert appels == []


def test_sans_demarrage_aucune_recherche_n_est_lancee(lancer_serveur):
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    assert json.loads(_requete(port, "GET", "/etat")[1])["en_cours"] is False
    assert appels == []


def test_demarrage_en_echec_est_signale_sans_empecher_le_serveur(lancer_serveur):
    def echec(profil, base):
        raise RuntimeError("boum")

    port = lancer_serveur(echec, demarrage=True)
    etat = _attendre_fin(port)
    assert "boum" in etat["erreur"]


def test_post_avec_un_nom_relance_ce_seul_profil(lancer_serveur):
    appels = []
    port = lancer_serveur(lambda profil, base: appels.append(profil.nom) or ResultatRecherche([], 0, 0))
    assert _requete(port, "POST", "/mettre-a-jour", corps={"profil": "u"})[0] == 202
    _attendre_fin(port)
    assert appels == ["u"]


def test_page_d_accueil_charge_toute_la_base_dont_les_profils_sans_definition(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e("Poste t")])
        stockage.enregistrer("ancien", [Offre("Poste ancien", "Beta", "Lyon", "https://e.fr/2", "d", "indeed")])
    port = lancer_serveur(lambda profil, base: ResultatRecherche([], 0, 0))
    page = _requete(port, "GET", "/")[1].decode("utf-8")
    assert "Poste t" in page and "Poste ancien" in page
    assert '<option value="ancien">' in page and '<option value="u">' in page


def _nouveau(nom="v", **champs):
    return {"nom": nom, "termes_recherche": ["a"], "lieu": "Lyon", **champs}


def _local(port):
    return f"http://127.0.0.1:{port}"


def _inutile(profil, base):
    return ResultatRecherche([], 0, 0)


def test_creer_profil_201_ecrit_en_base_et_apparait_dans_la_page(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/profils/creer", _local(port), {"profil": _nouveau("Mon Profil")})
    assert statut == 201 and json.loads(corps) == {"ok": True}
    assert "Mon Profil" in _noms_profils(base)
    assert '<option value="Mon Profil">' in _requete(port, "GET", "/")[1].decode("utf-8")


def test_creer_profil_invalide_400_avec_message_et_sans_effet_en_base(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/profils/creer", _local(port), {"profil": _nouveau(rayon_km="loin")})
    assert statut == 400 and "rayon_km" in json.loads(corps)["erreur"]
    statut, corps = _requete(port, "POST", "/profils/creer", _local(port), {"profil": _nouveau("t")})
    assert statut == 400 and "déjà utilisé" in json.loads(corps)["erreur"]
    assert _noms_profils(base) == ["t", "u"]


def test_modifier_profil_200_reecrit_le_profil_en_base(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"lieu": "Paris"}})[0] == 200
    assert _profil_en_base(base, "t").lieu == "Paris"


def test_modifier_profil_avec_nouveau_nom_renomme_le_profil_et_ses_offres(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e("Poste t")])
        stockage.noter_mise_a_jour("t")
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"nom": "neuf"}})[0] == 200
    assert _noms_profils(base) == ["neuf", "u"]
    with Stockage(base) as stockage:
        # lister ne filtre pas par statut : la réévaluation a pu écarter l'offre sans qu'elle quitte le profil.
        assert [o.titre for o in stockage.lister("neuf")] == ["Poste t"] and stockage.lister("t") == []
        assert list(stockage.dernieres_mises_a_jour()) == ["neuf"]


def test_modifier_profil_avec_un_nom_deja_pris_400_sans_effet(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"nom": "u"}})
    assert statut == 400 and "déjà utilisé" in json.loads(corps)["erreur"]
    assert _noms_profils(base) == ["t", "u"]


def test_modifier_profil_donnee_invalide_400(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    avant = _profil_en_base(base, "t")
    assert _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"sites": []}})[0] == 400
    assert _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": "texte"})[0] == 400
    assert _profil_en_base(base, "t") == avant


def test_supprimer_profil_200_efface_le_profil_ses_offres_et_sa_date(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e("Poste t")])
        stockage.noter_mise_a_jour("t")
        stockage.noter_mise_a_jour("u")
    port = lancer_serveur(_inutile)
    code, corps = _requete(port, "POST", "/profils/supprimer", _local(port), {"nom": "t"})[:2]
    assert code == 200
    assert json.loads(corps)["offres_effacees"] == 1
    assert _noms_profils(base) == ["u"]
    assert "Poste t" not in _requete(port, "GET", "/")[1].decode("utf-8")
    with Stockage(base) as stockage:
        assert list(stockage.dernieres_mises_a_jour()) == ["u"]


def test_supprimer_profil_sans_offre_renvoie_zero_offre_effacee(lancer_serveur, tmp_path):
    port = lancer_serveur(_inutile)
    code, corps = _requete(port, "POST", "/profils/supprimer", _local(port), {"nom": "u"})[:2]
    assert code == 200
    assert json.loads(corps)["offres_effacees"] == 0


def test_page_annonce_l_effacement_definitif_des_offres_a_la_suppression(lancer_serveur):
    port = lancer_serveur(_inutile)
    page = _requete(port, "GET", "/")[1].decode("utf-8")
    assert "effacées définitivement" in page
    assert "sont conservées" not in page


@pytest.mark.parametrize("nom", ["../t", "inconnu", 3, None, "exemple"])
def test_modifier_ou_supprimer_un_profil_inconnu_400(lancer_serveur, base, nom):
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/profils/modifier", _local(port), {"nom": nom, "profil": {"lieu": "Paris"}})[0] == 400
    assert _requete(port, "POST", "/profils/supprimer", _local(port), {"nom": nom})[0] == 400
    assert _noms_profils(base) == ["t", "u"]


@pytest.mark.parametrize(
    "chemin, corps",
    [("/profils/creer", {"profil": _nouveau("w")}), ("/profils/modifier", {"nom": "t", "profil": {"lieu": "Paris"}}), ("/profils/supprimer", {"nom": "t"})],
)
def test_routes_profils_origine_etrangere_403_sans_effet(lancer_serveur, base, chemin, corps):
    port = lancer_serveur(_inutile)
    avant = _profil_en_base(base, "t")
    assert _requete(port, "POST", chemin, "http://autre.example", corps)[0] == 403
    assert _noms_profils(base) == ["t", "u"]
    assert _profil_en_base(base, "t") == avant


def test_routes_profils_corps_absent_ou_illisible_400(lancer_serveur):
    port = lancer_serveur(_inutile)
    for chemin in ("/profils/creer", "/profils/modifier", "/profils/supprimer"):
        assert _requete(port, "POST", chemin, _local(port))[0] == 400
    assert _requete(port, "POST", "/profils/creer", _local(port), {"profil": _nouveau(description="x" * 20000)})[0] == 400


def test_modifier_ou_supprimer_refuse_pendant_la_mise_a_jour_de_ce_profil(lancer_serveur, base):
    liberation = threading.Event()

    def lente(profil, base):
        liberation.wait(5)
        return ResultatRecherche([], 0, 0)

    port = lancer_serveur(lente)
    avant = _profil_en_base(base, "t")
    try:
        assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
        assert _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"lieu": "Paris"}})[0] == 409
        assert _requete(port, "POST", "/profils/supprimer", _local(port), {"nom": "t"})[0] == 409
        assert _profil_en_base(base, "t") == avant
        assert _requete(port, "POST", "/profils/supprimer", _local(port), {"nom": "u"})[0] == 200
    finally:
        liberation.set()
    _attendre_fin(port)


def test_modifier_profil_reevalue_les_offres_deja_en_base(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer(
            "t",
            [Offre("Poste a", "Acme", "Marseille, B8, France", "https://e.fr/1", "d", "indeed")],
            [(Offre("Poste b", "Acme", "Venelles, France", "https://e.fr/2", "d", "indeed"), "hors zone")],
        )
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"villes_exclues": ["Marseille"], "exiger_termes": False}})
    assert statut == 200 and json.loads(corps) == {"ok": True, "reintegrees": 1, "ecartees": 1}
    with Stockage(base) as stockage:
        assert [o.titre for o in stockage.lister("t", ECARTEE)] == ["Poste a"]
        assert [o.titre for o in stockage.lister("t", NOUVELLE)] == ["Poste b"]


def test_page_propose_le_filtre_de_statut_et_la_cle_de_chaque_ligne():
    stockee = OffreStockee(_stockee().offre, "rejetee", "2026-10-31T10:00:00", "2026-10-31T10:00:00", "p", "cle-1")
    page = serveur.construire_page([stockee], ["p"])
    assert 'id="f-statut"' in page and "/offres/rejeter" in page
    assert all(f'<option value="{valeur}"' in page.split('id="f-statut"')[1] for valeur in ("", NOUVELLE, "a_postuler", "postulee", "rejetee"))
    assert 'id="f-nouvelles"' not in page and 'id="f-postulees"' not in page and 'id="f-rejetees"' not in page
    assert 'data-cle="cle-1"' in page.split("<tbody")[1] and 'data-statut="rejetee"' in page.split("<tbody")[1]


def test_rejeter_une_offre_200_puis_elle_est_rejetee_en_base(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e()])
        cle = stockage.lister_detail("t")[0].cle
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/offres/rejeter", _local(port), {"profil": "t", "cle": cle})
    assert statut == 200 and json.loads(corps) == {"ok": True}
    with Stockage(base) as stockage:
        assert [o.titre for o in stockage.lister("t", "rejetee")] == ["Poste a"]
    assert f'data-cle="{cle}"' in _requete(port, "GET", "/")[1].decode("utf-8")


def test_rejeter_offre_inconnue_404_corps_invalide_400_origine_etrangere_403(lancer_serveur):
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/offres/rejeter", _local(port), {"profil": "t", "cle": "inconnue"})[0] == 404
    assert _requete(port, "POST", "/offres/rejeter", _local(port), {"profil": "t"})[0] == 400
    assert _requete(port, "POST", "/offres/rejeter", _local(port), {"profil": 3, "cle": None})[0] == 400
    assert _requete(port, "POST", "/offres/rejeter", _local(port))[0] == 400
    assert _requete(port, "POST", "/offres/rejeter", "http://autre.example", {"profil": "t", "cle": "x"})[0] == 403


def test_page_affiche_la_colonne_statut_et_la_liste_deroulante_de_statut():
    brute = _stockee()
    rejetee = OffreStockee(brute.offre, "rejetee", "2026-10-31T10:00:00", "2026-10-31T10:00:00", "p", "c1")
    postulee = OffreStockee(brute.offre, "postulee", "2026-10-31T10:00:00", "2026-10-31T10:00:00", "p", "c2", "2026-11-03")
    a_postuler = OffreStockee(brute.offre, "a_postuler", "2026-10-31T10:00:00", "2026-10-31T10:00:00", "p", "c3")
    page = serveur.construire_page([brute, rejetee, postulee, a_postuler], ["p"])
    assert 'data-col="libelle">Statut</th>' in page and 'data-libelle="Rejeté"' in page and 'data-libelle="Postulé le 03/11/2026"' in page and 'data-libelle="Postuler"' in page
    assert page.count('<select data-action="statut"') == 4 and "/offres/postuler" in page and "/offres/a-postuler" in page and "/offres/retablir" in page
    for ancien in ('data-action="rejeter"', 'data-action="postuler"', 'data-action="retablir"'):
        assert ancien not in page
    cellules = [bloc.split("</span>")[0] for bloc in page.split('<span class="statut">')[1:]]
    assert cellules == ["", "Rejeté", "Postulé le 03/11/2026", "Postuler"]


def test_a_postuler_une_offre_200_puis_elle_est_a_postuler_en_base_et_dans_la_page(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e()])
        cle = stockage.lister_detail("t")[0].cle
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/offres/a-postuler", _local(port), {"profil": "t", "cle": cle})
    assert statut == 200 and json.loads(corps) == {"ok": True}
    with Stockage(base) as stockage:
        assert [o.titre for o in stockage.lister("t", "a_postuler")] == ["Poste a"]
    page = _requete(port, "GET", "/")[1].decode("utf-8")
    assert 'data-statut="a_postuler"' in page and '<span class="statut">Postuler</span>' in page


def test_a_postuler_offre_rejetee_ou_inconnue_404_corps_invalide_400_origine_etrangere_403(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e()])
        cle = stockage.lister_detail("t")[0].cle
        stockage.rejeter("t", cle)
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/offres/a-postuler", _local(port), {"profil": "t", "cle": cle})[0] == 404  # Rejeté vers Postuler interdit
    assert _requete(port, "POST", "/offres/a-postuler", _local(port), {"profil": "t", "cle": "inconnue"})[0] == 404
    assert _requete(port, "POST", "/offres/a-postuler", _local(port), {"profil": "t"})[0] == 400
    assert _requete(port, "POST", "/offres/a-postuler", _local(port), {"profil": 3, "cle": None})[0] == 400
    assert _requete(port, "POST", "/offres/a-postuler", _local(port))[0] == 400
    assert _requete(port, "POST", "/offres/a-postuler", "http://autre.example", {"profil": "t", "cle": cle})[0] == 403


def test_postuler_une_offre_200_date_du_jour_puis_visible_en_base_et_dans_la_page(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e()])
        cle = stockage.lister_detail("t")[0].cle
        stockage.rejeter("t", cle)  # une offre rejetée peut ensuite être postulée
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/offres/postuler", _local(port), {"profil": "t", "cle": cle})
    donnees = json.loads(corps)
    assert statut == 200 and donnees["ok"] is True and donnees["postule_le"] == date.today().isoformat()
    with Stockage(base) as stockage:
        assert [o.titre for o in stockage.lister("t", "postulee")] == ["Poste a"]
    assert "Postulé le " + date.today().strftime("%d/%m/%Y") in _requete(port, "GET", "/")[1].decode("utf-8")
    assert _requete(port, "POST", "/offres/rejeter", _local(port), {"profil": "t", "cle": cle})[0] == 404  # l'inverse est refusé


def test_page_propose_le_retablissement_d_une_offre_par_l_option_vue():
    page = serveur.construire_page([_stockee()], ["p"])
    assert "/offres/retablir" in page and f'<option value="{VUE}">Vue</option>' in page


@pytest.mark.parametrize("etat", ["rejetee", "postulee"])
def test_retablir_une_offre_200_puis_elle_est_vue_sans_date_de_candidature(lancer_serveur, base, etat):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e()])
        cle = stockage.lister_detail("t")[0].cle
        stockage.rejeter("t", cle) if etat == "rejetee" else stockage.postuler("t", cle)
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/offres/retablir", _local(port), {"profil": "t", "cle": cle})
    assert statut == 200 and json.loads(corps) == {"ok": True}
    with Stockage(base) as stockage:
        detail = stockage.lister_detail("t")[0]
    assert (detail.statut, detail.postule_le) == (VUE, None)


def test_retablir_offre_non_rejetee_ni_postulee_ou_inconnue_404_corps_invalide_400_origine_etrangere_403(lancer_serveur, base):
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [_offre_e()])
        cle = stockage.lister_detail("t")[0].cle
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/offres/retablir", _local(port), {"profil": "t", "cle": cle})[0] == 404  # encore « nouvelle »
    assert _requete(port, "POST", "/offres/retablir", _local(port), {"profil": "t", "cle": "inconnue"})[0] == 404
    assert _requete(port, "POST", "/offres/retablir", _local(port), {"profil": "t"})[0] == 400
    assert _requete(port, "POST", "/offres/retablir", _local(port), {"profil": 3, "cle": None})[0] == 400
    assert _requete(port, "POST", "/offres/retablir", _local(port))[0] == 400
    assert _requete(port, "POST", "/offres/retablir", "http://autre.example", {"profil": "t", "cle": cle})[0] == 403


def test_postuler_offre_inconnue_404_corps_invalide_400_origine_etrangere_403(lancer_serveur):
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/offres/postuler", _local(port), {"profil": "t", "cle": "inconnue"})[0] == 404
    assert _requete(port, "POST", "/offres/postuler", _local(port), {"profil": "t"})[0] == 400
    assert _requete(port, "POST", "/offres/postuler", _local(port), {"profil": 3, "cle": None})[0] == 400
    assert _requete(port, "POST", "/offres/postuler", "http://autre.example", {"profil": "t", "cle": "x"})[0] == 403


def test_page_propose_le_champ_villes_exclues():
    page = serveur.construire_page([], ["a"])
    assert 'id="p-villes"' in page and "villes_exclues" in page


def test_page_propose_la_case_exiger_termes_et_plus_mots_inclus():
    page = serveur.construire_page([], ["a"])
    assert 'id="p-exiger" type="checkbox"' in page and "exiger_termes" in page
    assert "mots_inclus" not in page and "p-inclus" not in page


def test_post_global_ecrit_en_base_et_reevalue_tous_les_profils(lancer_serveur, base):
    with Stockage(base) as stockage:
        for profil in ("t", "u"):
            stockage.enregistrer(profil, [Offre("Poste stagiaire", "Acme", "Lyon", f"https://e.fr/{profil}", "d", "indeed")], [])
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/global", _local(port), {"mots_exclus": ["stagiaire"], "villes_exclues": []})
    assert statut == 200 and json.loads(corps) == {"ok": True, "reintegrees": 0, "ecartees": 2}
    assert _globaux_en_base(base) == ParametresGlobaux(("stagiaire",), ())
    with Stockage(base) as stockage:
        assert len(stockage.lister("t", ECARTEE)) == 1 and len(stockage.lister("u", ECARTEE)) == 1


def test_post_global_origine_etrangere_403_corps_invalide_400_sans_effet(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/global", "http://autre.fr", {"mots_exclus": ["x"]})[0] == 403
    assert _requete(port, "POST", "/global", _local(port))[0] == 400
    statut, corps = _requete(port, "POST", "/global", _local(port), {"mots_exclus": "x"})
    assert statut == 400 and "mots_exclus" in json.loads(corps)["erreur"]
    assert _requete(port, "POST", "/global", _local(port), {"inconnu": []})[0] == 400
    assert _globaux_en_base(base) == ParametresGlobaux()


def test_post_global_refuse_pendant_une_mise_a_jour(lancer_serveur, base):
    liberation = threading.Event()

    def lente(profil, base):
        liberation.wait(5)
        return ResultatRecherche([], 0, 0)

    port = lancer_serveur(lente)
    try:
        assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
        assert _requete(port, "POST", "/global", _local(port), {"mots_exclus": ["x"]})[0] == 409
        assert _globaux_en_base(base) == ParametresGlobaux()
    finally:
        liberation.set()
    _attendre_fin(port)


def test_mise_a_jour_passe_les_globaux_a_la_recherche(lancer_serveur, base):
    _enregistrer_globaux(base, {"mots_exclus": ["stagiaire"]})
    port = lancer_serveur(_inutile)
    assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
    _attendre_fin(port)
    assert [g.mots_exclus for g in lancer_serveur.globaux] == [("stagiaire",)]


def test_modifier_profil_applique_les_globaux_a_la_reevaluation(lancer_serveur, base):
    _enregistrer_globaux(base, {"mots_exclus": ["stagiaire"]})
    with Stockage(base) as stockage:
        stockage.enregistrer("t", [Offre("Poste stagiaire", "Acme", "Lyon", "https://e.fr/1", "d", "indeed")], [])
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/profils/modifier", _local(port), {"nom": "t", "profil": {"lieu": "Lyon"}})
    assert statut == 200 and json.loads(corps)["ecartees"] == 1


def test_page_propose_le_formulaire_global_prerempli():
    page = serveur.construire_page([], ["a"], globaux=ParametresGlobaux(("stagiaire",), ("Paris <b>",)))
    assert 'id="form-globaux"' in page and 'id="g-exclus"' in page and 'id="g-villes"' in page
    assert '"mots_exclus": ["stagiaire"]' in page
    assert "<b>" not in page.split('id="globaux-data"')[1].split("</script>")[0]


def test_get_page_prerempli_depuis_les_parametres_globaux_de_la_base(lancer_serveur, base):
    _enregistrer_globaux(base, {"mots_exclus": ["stagiaire"]})
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "GET", "/")
    assert statut == 200 and '"mots_exclus": ["stagiaire"]' in corps.decode("utf-8")


def test_page_propose_le_champ_priorite_des_sites_prerempli():
    par_defaut = serveur.construire_page([], ["a"])
    assert 'id="g-priorite"' in par_defaut
    assert '"priorite_sites": ["linkedin", "indeed", "hellowork", "glassdoor"]' in par_defaut
    perso = serveur.construire_page([], ["a"], globaux=ParametresGlobaux(priorite_sites=("glassdoor", "<b>")))
    assert '"priorite_sites": ["glassdoor", "\\u003cb>"]' in perso
    assert "<b>" not in perso.split('id="globaux-data"')[1].split("</script>")[0]


def test_post_global_ecrit_la_priorite_des_sites(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    statut, _ = _requete(port, "POST", "/global", _local(port), {"mots_exclus": [], "villes_exclues": [], "priorite_sites": ["glassdoor", "indeed"]})
    assert statut == 200
    assert _globaux_en_base(base).priorite_sites == ("glassdoor", "indeed")


def test_post_global_priorite_des_sites_invalide_400_sans_effet(lancer_serveur, base):
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/global", _local(port), {"priorite_sites": "indeed"})
    assert statut == 400 and "priorite_sites" in json.loads(corps)["erreur"]
    assert _globaux_en_base(base) == ParametresGlobaux()


def test_page_propose_une_case_par_site_dont_hellowork():
    page = serveur.construire_page([], ["a"])
    for site in ("indeed", "linkedin", "glassdoor", "google", "hellowork"):
        assert f'name="site" value="{site}"' in page


def test_page_grise_l_entree_raccourci_sans_lanceur():
    sans = serveur.construire_page([], ["a"])
    avec = serveur.construire_page([], ["a"], raccourci_disponible=True)
    assert '<button id="raccourci" type="button" disabled title="' in sans
    assert '<button id="raccourci" type="button">Ajouter un raccourci au menu Démarrer</button>' in avec


def test_post_raccourci_sans_lanceur_409(lancer_serveur):
    port = lancer_serveur(_inutile)
    statut, corps = _requete(port, "POST", "/raccourci", _local(port), {})
    assert statut == 409 and "lanceur" in json.loads(corps)["erreur"]


def test_post_raccourci_appelle_le_module_avec_le_lanceur_du_serveur(lancer_serveur, tmp_path, monkeypatch):
    appels = []
    monkeypatch.setattr(serveur.raccourci, "creer", lambda lanceur: appels.append(lanceur) or "Raccourci créé : x")
    lanceur = tmp_path / "Alertes emploi.pyw"
    port = lancer_serveur(_inutile, lanceur)
    statut, corps = _requete(port, "POST", "/raccourci", _local(port), {"lanceur": "ignoré.exe"})
    assert statut == 200 and json.loads(corps) == {"ok": True, "message": "Raccourci créé : x"}
    assert appels == [lanceur]


def test_post_raccourci_erreur_du_module_500_avec_message(lancer_serveur, tmp_path, monkeypatch):
    def echouer(lanceur):
        raise serveur.raccourci.RaccourciErreur("Lanceur introuvable : z")

    monkeypatch.setattr(serveur.raccourci, "creer", echouer)
    port = lancer_serveur(_inutile, tmp_path / "Alertes emploi.pyw")
    statut, corps = _requete(port, "POST", "/raccourci", _local(port), {})
    assert statut == 500 and json.loads(corps)["erreur"] == "Lanceur introuvable : z"


def test_post_raccourci_origine_etrangere_403_sans_appel(lancer_serveur, tmp_path, monkeypatch):
    appels = []
    monkeypatch.setattr(serveur.raccourci, "creer", lambda lanceur: appels.append(lanceur) or "x")
    port = lancer_serveur(_inutile, tmp_path / "Alertes emploi.pyw")
    statut, _ = _requete(port, "POST", "/raccourci", "http://evil.example", {})
    assert statut == 403 and appels == []


class _FauxJobspy:
    """Faux Jobspy : disponible ou non, versions fixes, mise à jour consignée ou en échec."""

    def __init__(self, disponible=True, echec=None, attente=None):
        self.disponible = disponible
        self.appels = []
        self.nb_versions = 0
        self._echec = echec
        self._attente = attente

    def versions(self):
        self.nb_versions += 1
        return "1.4.1", "1.5.0"

    def mettre_a_jour(self, cible):
        self.appels.append(cible)
        if self._attente is not None:
            self._attente.wait(5)
        if self._echec:
            raise serveur.ErreurMiseAJour(self._echec)
        return f"JobSpy {cible} installé : relancez l'application pour l'utiliser."


def test_menu_jobspy_grise_hors_du_build_et_actif_dans_le_build():
    hors_build = serveur.construire_page([], ["a"])
    dans_le_build = serveur.construire_page([], ["a"], jobspy_disponible=True)
    assert '<button id="jobspy" type="button" disabled' in hors_build
    assert '<button id="jobspy" type="button">Mettre à jour JobSpy</button>' in dans_le_build
    assert 'id="dlg-jobspy"' in dans_le_build and 'id="jp-installer"' in dans_le_build


def test_get_jobspy_version_renvoie_installee_et_derniere(lancer_serveur):
    port = lancer_serveur(_inutile, jobspy=_FauxJobspy())
    statut, corps = _requete(port, "GET", "/jobspy/version")
    assert statut == 200 and json.loads(corps) == {"disponible": True, "installee": "1.4.1", "derniere": "1.5.0"}


def test_get_jobspy_version_origine_etrangere_403_sans_appel_de_pip(lancer_serveur):
    faux = _FauxJobspy()
    port = lancer_serveur(_inutile, jobspy=faux)
    assert _requete(port, "GET", "/jobspy/version", "http://evil.example")[0] == 403
    assert faux.nb_versions == 0
    assert _requete(port, "GET", "/jobspy/version", _local(port))[0] == 200
    assert faux.nb_versions == 1


def test_post_jobspy_installe_la_version_et_l_etat_porte_le_message(lancer_serveur):
    faux = _FauxJobspy()
    port = lancer_serveur(_inutile, jobspy=faux)
    statut, _ = _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": "1.5.0"})
    etat = _attendre_fin(port)
    assert statut == 202 and faux.appels == ["1.5.0"]
    assert etat["tache"] == "jobspy" and etat["erreur"] is None and "relancez" in etat["message"]


def test_post_jobspy_echec_renvoie_l_erreur_dans_l_etat(lancer_serveur):
    port = lancer_serveur(_inutile, jobspy=_FauxJobspy(echec="Installation échouée. Version 1.4.1 conservée."))
    _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": "1.5.0"})
    etat = _attendre_fin(port)
    assert etat["erreur"] == "Installation échouée. Version 1.4.1 conservée." and etat["message"] is None


def test_post_jobspy_version_invalide_400_sans_appel(lancer_serveur):
    faux = _FauxJobspy()
    port = lancer_serveur(_inutile, jobspy=faux)
    for version in ("1.5.0; calc", "", None, 15):
        statut, _ = _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": version})
        assert statut == 400
    assert faux.appels == []


def test_post_jobspy_indisponible_409_et_origine_etrangere_403(lancer_serveur):
    faux = _FauxJobspy(disponible=False)
    port = lancer_serveur(_inutile, jobspy=faux)
    statut, corps = _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": "1.5.0"})
    assert statut == 409 and "lanceur" in json.loads(corps)["erreur"]
    port = lancer_serveur(_inutile, jobspy=_FauxJobspy())
    statut, _ = _requete(port, "POST", "/jobspy/mettre-a-jour", "http://evil.example", {"version": "1.5.0"})
    assert statut == 403


def test_jobspy_refuse_pendant_une_recherche_et_la_recherche_pendant_l_installation(lancer_serveur):
    liberer = threading.Event()
    port = lancer_serveur(lambda profil, base: liberer.wait(5) and ResultatRecherche([], 0, 0), jobspy=_FauxJobspy())
    assert _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})[0] == 202
    statut, corps = _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": "1.5.0"})
    liberer.set()
    _attendre_fin(port)
    assert statut == 409 and "en cours" in json.loads(corps)["erreur"]

    attente = threading.Event()
    port = lancer_serveur(_inutile, jobspy=_FauxJobspy(attente=attente))
    assert _requete(port, "POST", "/jobspy/mettre-a-jour", _local(port), {"version": "1.5.0"})[0] == 202
    statut, _ = _requete(port, "POST", "/mettre-a-jour", _local(port), {"profil": "t"})
    attente.set()
    _attendre_fin(port)
    assert statut == 409
