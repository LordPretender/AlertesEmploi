from types import SimpleNamespace

import pytest

from alertes_emploi import raccourci


@pytest.fixture
def lanceur(tmp_path):
    fichier = tmp_path / "build" / "Alertes emploi.pyw"
    fichier.parent.mkdir()
    fichier.write_text("", encoding="utf-8")
    return fichier


def test_windows_cree_le_lnk_par_powershell_avec_les_chemins_dans_l_environnement(tmp_path, lanceur, monkeypatch):
    monkeypatch.setattr(raccourci.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    appels = []

    def faux(commande, **options):
        appels.append((commande, options))
        return SimpleNamespace(returncode=0, stderr="")

    message = raccourci.creer(lanceur, faux)
    attendu = tmp_path / "appdata" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Alertes emploi.lnk"
    commande, options = appels[0]
    assert commande[0] == "powershell" and str(lanceur) not in " ".join(commande)
    assert options["env"]["ALERTES_RACCOURCI"] == str(attendu)
    assert options["env"]["ALERTES_CIBLE"] == str(lanceur.resolve())
    assert options["env"]["ALERTES_DOSSIER"] == str(lanceur.resolve().parent)
    assert attendu.parent.is_dir() and str(attendu) in message


def test_windows_echec_de_powershell_leve_une_erreur_avec_son_message(tmp_path, lanceur, monkeypatch):
    monkeypatch.setattr(raccourci.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    with pytest.raises(raccourci.RaccourciErreur, match="boum"):
        raccourci.creer(lanceur, lambda commande, **options: SimpleNamespace(returncode=1, stderr="boum\n"))


def test_windows_powershell_introuvable_leve_une_erreur(tmp_path, lanceur, monkeypatch):
    monkeypatch.setattr(raccourci.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path))

    def absent(commande, **options):
        raise FileNotFoundError("powershell")

    with pytest.raises(raccourci.RaccourciErreur, match="impossible"):
        raccourci.creer(lanceur, absent)


def test_lanceur_introuvable_leve_une_erreur_sans_rien_creer(tmp_path, monkeypatch):
    monkeypatch.setattr(raccourci.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    with pytest.raises(raccourci.RaccourciErreur, match="introuvable"):
        raccourci.creer(tmp_path / "absent.pyw", lambda commande, **options: pytest.fail("ne doit pas être appelé"))
    assert not (tmp_path / "appdata").exists()


def test_linux_ecrit_un_fichier_desktop(tmp_path, lanceur, monkeypatch):
    # Seul cas où _est_windows est patché : sous Windows, os.name = "posix" empêche pathlib d'instancier un chemin (NotImplementedError).
    monkeypatch.setattr(raccourci, "_est_windows", lambda: False)
    monkeypatch.setattr(raccourci.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    raccourci.creer(lanceur)
    contenu = (tmp_path / "home" / ".local" / "share" / "applications" / "alertes-emploi.desktop").read_text(encoding="utf-8")
    assert "Name=Alertes emploi" in contenu and f'Exec=python3 "{lanceur.resolve()}"' in contenu


def test_supprimer_retire_le_raccourci_puis_signale_son_absence(tmp_path, monkeypatch):
    monkeypatch.setattr(raccourci.os, "name", "nt")
    monkeypatch.setenv("APPDATA", str(tmp_path))
    chemin = raccourci.chemin_raccourci()
    chemin.parent.mkdir(parents=True)
    chemin.write_text("", encoding="utf-8")
    assert "supprimé" in raccourci.supprimer() and not chemin.exists()
    assert raccourci.supprimer() == "Aucun raccourci à supprimer."
