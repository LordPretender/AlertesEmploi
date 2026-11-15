# Scripts

Index des scripts du projet. Chaque script PowerShell porte son aide détaillée (`Get-Help .\scripts\<nom>.ps1 -Full`).

| Script | Objet |
|---|---|
| [construire-build.ps1](construire-build.ps1) | Construit le dossier portable `build/AlertesEmploi` (lanceur Python, src, requirements) ; ne remplace que `systeme/` et le lanceur, jamais `donnees/` |
| [consulter.ps1](consulter.ps1) | Lance le serveur local de consultation des offres de toute la base (filtre par profil) et ouvre la page dans le navigateur |
| [generer-communes.ps1](generer-communes.ps1) | Télécharge la table des communes françaises depuis geo.api.gouv.fr vers `src\alertes_emploi\sources\communes.json` |
| [monter-version-jobspy.ps1](monter-version-jobspy.ps1) | Monte la version épinglée de python-jobspy, lance les tests et revient à la version précédente si ça échoue |
| [nettoyer-base.ps1](nettoyer-base.ps1) | Fusionne les quasi-doublons de `alertes.db`, après vérification de la sauvegarde `.bak` |
| [planifier-alertes-emploi.ps1](planifier-alertes-emploi.ps1) | Crée (ou supprime) une tâche planifiée Windows qui lance chaque jour la recherche d'un profil |
| [preparer-venv.ps1](preparer-venv.ps1) | Crée le venv du projet (`.venv`) s'il manque et y installe `requirements.txt` |
| [rechercher.ps1](rechercher.ps1) | Lance la recherche d'un profil enregistré dans la base avec le venv du projet et affiche les offres trouvées |
| [supprimer-source.ps1](supprimer-source.ps1) | Supprime de `alertes.db` toutes les offres d'une source (ex. `lesjeudis`), après copie horodatée de la base |

Le dossier [modele-build](modele-build) contient les sources du lanceur copiées dans le build par `construire-build.ps1`.
