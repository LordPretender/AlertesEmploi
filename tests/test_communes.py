import pytest

from alertes_emploi.sources.communes import code_insee, nom_canonique, normaliser, suggerer


def test_normaliser():
    assert normaliser("Saint-Étienne") == "saint etienne"
    assert normaliser("  L'Isle-d'Abeau ") == "l isle d abeau"


@pytest.mark.parametrize(
    ("lieu", "code"),
    [
        ("Marseille", "13055"),
        ("marseille, France", "13055"),
        ("Paris", "75056"),
        ("Lyon", "69123"),
        ("Aix-en-Provence", "13001"),
        ("Saint-Étienne", "42218"),
    ],
)
def test_lieu_connu(lieu, code):
    assert code_insee(lieu) == code


def test_homonymes_la_plus_peuplee_l_emporte():
    # Saint-Denis : la commune de La Réunion (97411) est plus peuplée que celle de Seine-Saint-Denis (93066) et que les autres homonymes.
    assert code_insee("Saint-Denis") == "97411"


def test_code_postal_departage_les_homonymes():
    assert code_insee("Saint-Denis 93200") == "93066"
    assert code_insee("Saint-Denis 97400") == "97411"


def test_code_postal_seul():
    assert code_insee("13001") == "13055"


def test_nom_canonique():
    assert nom_canonique("saint-etienne") == "Saint-Étienne"
    assert nom_canonique("Saint-Denis 93200") == "Saint-Denis"
    assert nom_canonique("Atlantide") == "Atlantide"


def test_suggerer_par_prefixe_les_plus_peuplees_d_abord():
    noms = [c["nom"] for c in suggerer("marse")]
    assert noms[0] == "Marseille"
    assert all(normaliser(n).startswith("marse") for n in noms)
    assert len(suggerer("saint", limite=5)) == 5
    assert all(c["code_postal"] for c in suggerer("lyon"))


def test_suggerer_exige_deux_caracteres():
    assert suggerer("") == [] and suggerer("m") == []


def test_lieu_introuvable_ou_vide():
    assert code_insee("Atlantide") is None
    assert code_insee("") is None
