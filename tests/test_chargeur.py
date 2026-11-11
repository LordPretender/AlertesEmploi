import pytest

from alertes_emploi.profils.chargeur import (
    ParametresGlobaux,
    Profil,
    ProfilInvalide,
    globaux_depuis_donnees,
    profil_depuis_donnees,
)

MINIMAL = {"nom": "test", "termes_recherche": ["dev"], "lieu": "Lyon"}


def test_profil_valide_avec_valeurs_par_defaut():
    profil = profil_depuis_donnees(MINIMAL)
    assert profil == Profil(nom="test", termes_recherche=("dev",), lieu="Lyon")
    assert profil.sites == ("indeed",)
    assert profil.pays_indeed == "France"
    assert profil.teletravail is None
    assert profil.exiger_termes is True


def test_exiger_termes_faux_accepte():
    assert profil_depuis_donnees({**MINIMAL, "exiger_termes": False}).exiger_termes is False


def test_mots_inclus_est_un_champ_inconnu():
    with pytest.raises(ProfilInvalide, match="inconnu.*mots_inclus"):
        profil_depuis_donnees({**MINIMAL, "mots_inclus": []})


def test_profil_complet():
    donnees = {**MINIMAL, "rayon_km": 10, "teletravail": True, "mots_exclus": ["stage"], "anciennete_max_heures": 24, "villes_exclues": ["Marseille"]}
    profil = profil_depuis_donnees(donnees)
    assert profil.villes_exclues == ("Marseille",)
    assert profil.rayon_km == 10
    assert profil.teletravail is True
    assert profil.mots_exclus == ("stage",)
    assert profil.anciennete_max_heures == 24


def test_champ_manquant():
    with pytest.raises(ProfilInvalide, match="manquant.*lieu"):
        profil_depuis_donnees({"nom": "test", "termes_recherche": ["dev"]})


def test_champ_inconnu():
    with pytest.raises(ProfilInvalide, match="inconnu.*salaire"):
        profil_depuis_donnees({**MINIMAL, "salaire": 1})


@pytest.mark.parametrize(
    "champ, valeur",
    [("rayon_km", "30"), ("rayon_km", True), ("termes_recherche", "dev"), ("mots_exclus", [1]), ("teletravail", "oui"), ("exiger_termes", "oui"), ("exiger_termes", 1)],
)
def test_champ_mal_type(champ, valeur):
    with pytest.raises(ProfilInvalide, match=champ):
        profil_depuis_donnees({**MINIMAL, champ: valeur})


@pytest.mark.parametrize("donnees", [["pas", "un", "objet"], "texte", None])
def test_profil_qui_n_est_pas_un_objet_refuse(donnees):
    with pytest.raises(ProfilInvalide, match="objet"):
        profil_depuis_donnees(donnees)


def test_nom_vide_refuse():
    with pytest.raises(ProfilInvalide, match="nom"):
        profil_depuis_donnees({**MINIMAL, "nom": "  "})


def test_termes_recherche_vide_refuse():
    with pytest.raises(ProfilInvalide, match="termes_recherche"):
        profil_depuis_donnees({**MINIMAL, "termes_recherche": []})


def test_globaux_valides_et_champs_facultatifs():
    assert globaux_depuis_donnees({"mots_exclus": ["stage"]}) == ParametresGlobaux(mots_exclus=("stage",))


@pytest.mark.parametrize("donnees", [{"sites": ["indeed"]}, {"mots_exclus": "stage"}, {"villes_exclues": [1]}, ["stage"]])
def test_globaux_invalides_refuses(donnees):
    with pytest.raises(ProfilInvalide):
        globaux_depuis_donnees(donnees)


def test_globaux_priorite_sites_par_defaut():
    assert globaux_depuis_donnees({"mots_exclus": ["stage"]}).priorite_sites == ("linkedin", "indeed", "hellowork", "glassdoor")


def test_globaux_priorite_sites_personnalisee_lue():
    assert globaux_depuis_donnees({"priorite_sites": ["glassdoor", "indeed"]}).priorite_sites == ("glassdoor", "indeed")


def test_globaux_priorite_sites_vide_conservee():
    assert globaux_depuis_donnees({"priorite_sites": []}).priorite_sites == ()


def test_globaux_priorite_sites_non_liste_refusee():
    with pytest.raises(ProfilInvalide):
        globaux_depuis_donnees({"priorite_sites": "indeed"})


def test_globaux_recherche_auto_valeurs_par_defaut():
    globaux = globaux_depuis_donnees({})
    assert globaux.recherche_auto_demarrage is True and globaux.delai_recherche_auto_minutes == 60


def test_globaux_recherche_auto_valeurs_lues_bornes_comprises():
    assert globaux_depuis_donnees({"recherche_auto_demarrage": False, "delai_recherche_auto_minutes": 5}) == ParametresGlobaux(
        recherche_auto_demarrage=False, delai_recherche_auto_minutes=5
    )
    assert globaux_depuis_donnees({"delai_recherche_auto_minutes": 1440}).delai_recherche_auto_minutes == 1440


@pytest.mark.parametrize(
    "donnees",
    [
        {"delai_recherche_auto_minutes": 4},
        {"delai_recherche_auto_minutes": 1441},
        {"delai_recherche_auto_minutes": "60"},
        {"delai_recherche_auto_minutes": 60.5},
        {"delai_recherche_auto_minutes": True},
        {"recherche_auto_demarrage": 1},
        {"recherche_auto_demarrage": "oui"},
    ],
)
def test_globaux_recherche_auto_invalides_refuses(donnees):
    with pytest.raises(ProfilInvalide):
        globaux_depuis_donnees(donnees)
