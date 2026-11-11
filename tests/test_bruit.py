from alertes_emploi.filtres.bruit import filtrer_bruit
from alertes_emploi.modele import Offre


def _offre(titre, description=""):
    return Offre(titre=titre, societe="Acme", lieu="Lyon", url=f"u/{titre}", description=description, source="indeed")


def test_sans_mots_tout_est_retenu():
    offres = [_offre("a"), _offre("b")]
    assert filtrer_bruit(offres) == (offres, [])


def test_mot_exclu_dans_titre_ou_description():
    titre, description, propre = _offre("Stage développeur"), _offre("Développeur", "poste en alternance"), _offre("Développeur")
    retenues, ecartees = filtrer_bruit([titre, description, propre], mots_exclus=["stage", "alternance"])
    assert retenues == [propre]
    assert ecartees == [(titre, "exclu : stage"), (description, "exclu : alternance")]


def test_terme_requis_present_dans_titre_ou_description():
    titre, description = _offre("Dev Python"), _offre("Développeur", "stack python et sql")
    assert filtrer_bruit([titre, description], termes_requis=["python"]) == ([titre, description], [])


def test_terme_requis_absent_ecarte_l_offre():
    python, java = _offre("Dev Python"), _offre("Dev Java")
    retenues, ecartees = filtrer_bruit([python, java], termes_requis=["python"])
    assert retenues == [python]
    assert ecartees == [(java, "terme de recherche absent")]


def test_un_seul_terme_requis_parmi_plusieurs_suffit():
    python, java = _offre("Dev Python"), _offre("Dev Java")
    retenues, ecartees = filtrer_bruit([python, java], termes_requis=["python", "django"])
    assert retenues == [python]
    assert ecartees == [(java, "terme de recherche absent")]


def test_exclusion_prime_sur_termes_requis():
    offre = _offre("Stage Python")
    retenues, ecartees = filtrer_bruit([offre], termes_requis=["python"], mots_exclus=["stage"])
    assert retenues == []
    assert ecartees == [(offre, "exclu : stage")]


def test_casse_et_accents_ignores():
    offre = _offre("Responsable Développement")
    assert filtrer_bruit([offre], termes_requis=["DEVELOPPEMENT"])[0] == [offre]
    assert filtrer_bruit([offre], mots_exclus=["développement"])[0] == []


def test_mot_entier_uniquement():
    offre = _offre("Stagiaire développeur")
    assert filtrer_bruit([offre], mots_exclus=["stage"])[0] == [offre]
    assert filtrer_bruit([offre], termes_requis=["stage"])[0] == []
    assert filtrer_bruit([_offre("Admin MySQL")], termes_requis=["sql"])[0] == []


def test_expression_de_plusieurs_mots():
    offre = _offre("Chef de projet IT")
    assert filtrer_bruit([offre], termes_requis=["chef de projet"])[0] == [offre]
    assert filtrer_bruit([offre], mots_exclus=["chef de projet"])[0] == []


def test_mots_vides_ignores():
    offre = _offre("Dev")
    assert filtrer_bruit([offre], termes_requis=[" "], mots_exclus=[""]) == ([offre], [])
