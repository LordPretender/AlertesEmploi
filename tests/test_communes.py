import pytest

from alertes_emploi.sources.communes import code_insee, normaliser


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


def test_lieu_introuvable_ou_vide():
    assert code_insee("Atlantide") is None
    assert code_insee("") is None
