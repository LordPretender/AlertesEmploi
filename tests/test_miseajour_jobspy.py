import pytest

from alertes_emploi import miseajour_jobspy as mj


def test_version_valide_n_accepte_que_chiffres_et_points():
    assert mj.version_valide("1.4.2") and mj.version_valide("2")
    for refusee in ("", "1.", ".1", "1.4.2; calc", "1.4.2 ", "../x", "1.4.2rc1", "-r evil.txt", None, 12):
        assert not mj.version_valide(refusee)


def test_lire_et_remplacer_l_epingle_en_gardant_le_reste():
    texte = "pandas==2.0\r\npython-jobspy==1.4.1\r\npytest==9\r\n"
    assert mj.lire_epingle(texte) == "1.4.1"
    assert mj.remplacer_epingle(texte, "1.5.0") == "pandas==2.0\r\npython-jobspy==1.5.0\r\npytest==9\r\n"
    assert mj.lire_epingle("pandas==2.0\n") is None
    assert mj.remplacer_epingle("pandas==2.0\n", "1.5.0") == "pandas==2.0\n"


def test_derniere_version_extraite_de_la_sortie_de_pip():
    assert mj.extraire_derniere_version("python-jobspy (1.5.0)\nAvailable versions: 1.5.0, 1.4.1\n") == "1.5.0"
    assert mj.extraire_derniere_version("ERROR: No matching distribution") is None
    assert mj.extraire_derniere_version("python-jobspy (evil; calc)") is None


class FauxPip:
    def __init__(self, codes):
        self.codes = list(codes)
        self.appels = []

    def __call__(self, arguments):
        self.appels.append(arguments)
        return self.codes.pop(0), "sortie de pip"


@pytest.fixture
def emplacements(tmp_path):
    requirements = tmp_path / "requirements.txt"
    empreinte = tmp_path / "requirements.installe.txt"
    for fichier in (requirements, empreinte):
        fichier.write_text("pandas==2.0\npython-jobspy==1.4.1\n", encoding="utf-8")
    return mj.Emplacements(tmp_path / "python.exe", requirements, empreinte)


def _jobspy(emplacements, pip, installee="1.4.1"):
    return mj.Jobspy(emplacements, pip, lambda: installee)


def test_succes_installe_la_cible_et_resynchronise_les_deux_fichiers(emplacements):
    pip = FauxPip([0])
    message = _jobspy(emplacements, pip).mettre_a_jour("1.5.0")
    assert "1.5.0" in message and "relancez" in message
    assert [a[-1] for a in pip.appels] == ["python-jobspy==1.5.0"]
    texte = emplacements.requirements.read_text(encoding="utf-8")
    assert texte == "pandas==2.0\npython-jobspy==1.5.0\n"
    assert emplacements.empreinte.read_text(encoding="utf-8") == texte


def test_echec_de_pip_revient_a_la_version_precedente_sans_toucher_aux_fichiers(emplacements):
    pip = FauxPip([1, 0])
    with pytest.raises(mj.ErreurMiseAJour, match="Version 1.4.1 conservée"):
        _jobspy(emplacements, pip).mettre_a_jour("1.5.0")
    assert [a[-1] for a in pip.appels] == ["python-jobspy==1.5.0", "python-jobspy==1.4.1"]
    assert "1.4.1" in emplacements.requirements.read_text(encoding="utf-8")
    assert "1.4.1" in emplacements.empreinte.read_text(encoding="utf-8")


def test_echec_du_retour_arriere_est_signale(emplacements):
    with pytest.raises(mj.ErreurMiseAJour, match="retour à la version 1.4.1 a échoué"):
        _jobspy(emplacements, FauxPip([1, 1])).mettre_a_jour("1.5.0")


def test_version_invalide_refusee_sans_appeler_pip(emplacements):
    pip = FauxPip([])
    with pytest.raises(mj.ErreurMiseAJour, match="invalide"):
        _jobspy(emplacements, pip).mettre_a_jour("1.5.0; calc")
    assert pip.appels == []


def test_version_deja_installee_ne_fait_rien(emplacements):
    pip = FauxPip([])
    assert "déjà installé" in _jobspy(emplacements, pip).mettre_a_jour("1.4.1")
    assert pip.appels == []


def test_hors_build_la_mise_a_jour_est_refusee():
    jobspy = mj.Jobspy(None, FauxPip([]), lambda: "1.4.1")
    assert not jobspy.disponible
    with pytest.raises(mj.ErreurMiseAJour, match="indisponible"):
        jobspy.mettre_a_jour("1.5.0")
    assert jobspy.versions() == ("1.4.1", None)


def test_versions_lit_la_derniere_version_dans_pip_index(emplacements):
    pip = lambda arguments: (0, "python-jobspy (1.5.0)\n")
    assert _jobspy(emplacements, pip).versions() == ("1.4.1", "1.5.0")


def test_fichier_sans_ligne_jobspy_signale_une_erreur_apres_l_installation(emplacements):
    emplacements.empreinte.write_text("pandas==2.0\n", encoding="utf-8")
    with pytest.raises(mj.ErreurMiseAJour, match="requirements"):
        _jobspy(emplacements, FauxPip([0])).mettre_a_jour("1.5.0")


def test_depot_de_developpement_n_est_pas_detecte_comme_un_build():
    # Le code testé vit dans src/ du dépôt, sans preparer.py à côté de requirements.txt.
    assert mj.detecter_emplacements() is None
