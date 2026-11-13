"""Constructions de données partagées par plusieurs fichiers de test (importé par `from outils import ...` : pytest place le dossier tests dans sys.path)."""

import hashlib

from alertes_emploi.modele import Offre

# Valeur par défaut de priorite_sites, écrite en dur à dessein : la déduire de ParametresGlobaux() rendrait les tests tautologiques.
PRIORITE_SITES_PAR_DEFAUT = ("francetravail", "apec", "lesjeudis", "hellowork", "indeed", "linkedin", "glassdoor")


def offre_de_test(url, titre=None, societe="Acme", lieu="Lyon", description="Description de l'offre", source="indeed"):
    # Le titre par défaut dépend de l'URL et en diffère nettement (empreinte) : deux URL distinctes sont deux offres distinctes, même pour la comparaison floue des quasi-doublons.
    return Offre(titre=titre or f"Poste {hashlib.md5(url.encode()).hexdigest()}", societe=societe, lieu=lieu, url=url, description=description, source=source)
