from urllib.error import HTTPError

import pytest

from alertes_emploi.sources import commun


class _Reponse:
    def __init__(self, corps: str) -> None:
        self._corps = corps

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._corps.encode("utf-8")


@pytest.fixture
def reseau(monkeypatch):
    """Faux serveur des adaptateurs natifs : routes = {fragment d'URL: corps (str) ou code d'erreur HTTP (int)} ; renvoie aussi la liste des URL demandées."""
    routes: dict[str, str | int] = {}
    demandees: list[str] = []

    def faux_urlopen(requete, timeout):
        url = requete.full_url
        demandees.append(url)
        for fragment, retour in routes.items():
            if fragment in url:
                if isinstance(retour, int):
                    raise HTTPError(url, retour, "erreur", {}, None)
                return _Reponse(retour)
        raise HTTPError(url, 404, "inconnue", {}, None)

    monkeypatch.setattr(commun, "urlopen", faux_urlopen)
    monkeypatch.setattr(commun.time, "sleep", lambda secondes: None)
    return routes, demandees
