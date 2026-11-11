from dataclasses import FrozenInstanceError
import pytest

from alertes_emploi.modele import Offre, sont_quasi_doublons


def _offre(**surcharges):
    valeurs = dict(
        titre="Développeur .NET",
        societe="Acme",
        lieu="Lyon",
        url="https://exemple.fr/offre/1",
        description="Poste en CDI.",
        source="indeed",
    )
    valeurs.update(surcharges)
    return Offre(**valeurs)


def test_offre_conserve_ses_champs():
    offre = _offre()
    assert offre.titre == "Développeur .NET"
    assert offre.societe == "Acme"
    assert offre.lieu == "Lyon"
    assert offre.url == "https://exemple.fr/offre/1"
    assert offre.description == "Poste en CDI."
    assert offre.source == "indeed"


def test_offre_est_immuable():
    with pytest.raises(FrozenInstanceError):
        _offre().titre = "Autre"


def test_deux_offres_identiques_sont_egales():
    assert _offre() == _offre()


TITRE_LECLERC = "TECHNICIEN(NE) SUPPORT INFORMATIQUE - H/F"
DESCRIPTION_LECLERC = (
    "<p>Au sein du magasin, vous assurez le support de premier niveau des utilisateurs, "
    "le suivi du parc informatique, l'installation et la maintenance des postes de travail, "
    "des caisses et des imprimantes, ainsi que la gestion des sauvegardes.</p>"
)


def _leclerc(**surcharges):
    valeurs = dict(titre=TITRE_LECLERC, societe="E. LECLERC", lieu="Grans, 13", description=DESCRIPTION_LECLERC)
    valeurs.update(surcharges)
    return _offre(**valeurs)


def test_societe_ecrite_differemment_avec_meme_description_est_un_doublon():
    assert sont_quasi_doublons(_leclerc(source="indeed"), _leclerc(source="glassdoor", societe="E.Leclerc"))


def test_societes_differentes_avec_description_identique_sont_des_doublons():
    assert sont_quasi_doublons(_leclerc(societe="Leclerc"), _leclerc(societe="Groupe E.Leclerc SA"))


def test_description_tronquee_par_l_autre_plateforme_est_un_doublon():
    tronquee = DESCRIPTION_LECLERC[: len(DESCRIPTION_LECLERC) // 2]
    assert sont_quasi_doublons(_leclerc(), _leclerc(societe="E.Leclerc", description=tronquee))


def test_societe_absente_d_un_cote_avec_description_identique_est_un_doublon():
    assert sont_quasi_doublons(_leclerc(), _leclerc(societe=""))


def test_postes_distincts_chez_un_meme_employeur_restent_fusionnes_si_titre_et_societe_identiques():
    autre_poste = _leclerc(description="Gestion des achats de la boucherie, relation fournisseurs et inventaires mensuels.")
    assert sont_quasi_doublons(_leclerc(), autre_poste)


def test_ville_differente_n_est_jamais_un_doublon():
    assert not sont_quasi_doublons(_leclerc(), _leclerc(lieu="Salon-de-Provence, 13"))


def test_ville_abregee_par_une_plateforme_est_un_doublon():
    assert sont_quasi_doublons(_leclerc(lieu="Aix, France"), _leclerc(lieu="Aix-en-Provence, B8, France"))


def test_villes_sans_mots_de_debut_communs_ne_sont_pas_des_doublons():
    assert not sont_quasi_doublons(_leclerc(lieu="Aix, France"), _leclerc(lieu="Villeneuve-d'Aix, France"))


def test_titres_differents_ne_sont_pas_des_doublons():
    assert not sont_quasi_doublons(_leclerc(), _leclerc(titre="Boucher / Bouchère - H/F"))


def test_descriptions_vides_le_score_repose_sur_titre_et_societe():
    assert sont_quasi_doublons(_leclerc(description=""), _leclerc(description="", societe="E.Leclerc"))
    assert not sont_quasi_doublons(_leclerc(description=""), _leclerc(description="", societe="Carrefour"))


def test_mirion_et_mirion_technologies_restent_des_doublons():
    assert sont_quasi_doublons(
        _offre(titre="Expert", societe="Mirion", lieu="Lamanon, 13"),
        _offre(titre="Expert", societe="Mirion Technologies", lieu="Lamanon, France"),
    )


def test_titres_eloignes_avec_description_proche_mais_pas_identique_ne_sont_pas_des_doublons():
    automaticien = _leclerc(
        titre="Automaticien - Informatique Industrielle & Cybersécurité H/F",
        description=DESCRIPTION_LECLERC + " Le poste porte sur la programmation des automates et la supervision des lignes.",
    )
    ingenieur = _leclerc(
        titre="Ingénieur Automatisme et Informatique Industrielle H/F",
        description=DESCRIPTION_LECLERC + " Le poste porte sur la conduite de projets d'automatisation et le pilotage des équipes.",
    )
    assert not sont_quasi_doublons(automaticien, ingenieur)


def test_titre_avec_faute_de_frappe_et_description_proche_reste_un_doublon():
    avec_faute = _leclerc(titre=TITRE_LECLERC.replace("TECHNICIEN", "TECHNICIENB"), description=DESCRIPTION_LECLERC + " Poste à pourvoir rapidement.")
    assert sont_quasi_doublons(_leclerc(), avec_faute)


def test_titres_eloignes_avec_description_identique_restent_des_doublons():
    assert sont_quasi_doublons(_leclerc(), _leclerc(titre="Technicien informatique de proximité - H/F"))


def test_un_seul_champ_comparable_ne_suffit_pas():
    assert not sont_quasi_doublons(_leclerc(societe="", description=""), _leclerc(societe="", description=""))
