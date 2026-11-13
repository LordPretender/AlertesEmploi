# AlertesEmploi

Application Windows de recherche d'offres d'emploi à partir de profils personnalisables, avec filtrage du bruit, historique SQLite, déduplication et consultation dans une interface locale. Le moteur de recherche est [JobSpy](https://github.com/speedyapply/JobSpy), utilisé comme dépendance externe et isolé derrière un adaptateur unique (`src/alertes_emploi/sources/jobspy_adapter.py`). L'application ne fait que chercher et trier : aucune candidature automatique.

## Installation et premier lancement

Python 3.12 est requis sur le poste.

Pour développer ou lancer les tests, préparer l'environnement du dépôt :

```
& .\scripts\preparer-venv.ps1
```

Pour utiliser l'application, générer le build portable :

```
& .\scripts\construire-build.ps1
```

Le script produit le dossier `build/AlertesEmploi/` (non versionné), que `-Sortie` change et que `-WhatIf` décrit sans écrire :

- `Alertes emploi.pyw` : lanceur, à double-cliquer
- `systeme/` : code (`src/`), `requirements.txt` et `preparer.py`, remplacé à chaque nouveau build
- `donnees/` : `alertes.db` (profils, paramètres globaux et offres), `journal.log`, jamais écrasé par un build

Au premier lancement (ou si `requirements.txt` change), `preparer.py` crée un venv propre au poste (`%LOCALAPPDATA%\AlertesEmploi\venv`) dans une console visible et y installe les dépendances (Internet requis).

- Le dossier se copie tel quel sur un NAS et se lance depuis n'importe quel poste Windows (lecteur mappé conseillé plutôt qu'un chemin UNC)
- Un seul poste à la fois doit utiliser la même base SQLite
- Linux : scripts écrits pour être portables, application non validée

Sources du lanceur : [modele-build](scripts/modele-build).

## L'application

- **Fenêtre** : l'interface s'ouvre dans une fenêtre Windows (pywebview, WebView2 requis) qui héberge un serveur local et l'arrête à sa fermeture. Si le port est déjà pris, elle se connecte au serveur existant. Détail : [fenetre.py](src/alertes_emploi/interface/fenetre.py).
- **Page** : une seule page, sans barre de défilement parasite ; le tableau défile à l'intérieur, avec ses en-têtes fixes. Les liens « Voir l'annonce » s'ouvrent dans le navigateur par défaut. L'interface suit le thème clair ou sombre de Windows. Détail du serveur : [serveur.py](src/alertes_emploi/interface/serveur.py).
- **Bandeau** : les messages (bilan d'une recherche, erreurs) s'affichent dans un bandeau fermable sous l'en-tête.
- **Raccourci** : l'entrée « Ajouter un raccourci au menu Démarrer » du menu « Réglages » crée un seul raccourci vers le lanceur du build ; elle est grisée quand la page est ouverte sans lanceur. Détail : [raccourci.py](src/alertes_emploi/raccourci.py).
- **Options de la fenêtre** : `--donnees` (dossier contenant `alertes.db`), `--base` (l'emporte sur `--donnees`), `--port` et `--lanceur`.

## Fonctions

### Offres

- La page liste les offres retenues de toute la base, avec les colonnes Titre, Profil, Site, Société, Lieu, Ajoutée le et Statut, toutes triables
- Les filtres (profil, recherche libre, société, lieu, statut) sont conservés au rechargement, et le bouton « Réinitialiser les filtres » les efface
- Un clic sur le titre déplie la description sur toute la largeur de la ligne, avec à sa fin le lien « Voir l'annonce », le bouton « Masquer » et une liste déroulante de statut

Statuts d'une offre (liste déroulante de la description, filtre « Statut » de la barre de filtres) :

- **Vue** : retour en arrière depuis n'importe quel autre statut ; l'offre ne redevient pas « nouvelle »
- **Postuler** : l'offre est à postuler, pas encore postulée
- **Postulé** : la date du jour est mémorisée ; possible depuis tous les statuts, y compris Rejeté
- **Rejeté** : l'offre disparaît de la liste tant que le filtre « Statut » ne le demande pas, et les recherches suivantes ne la remettent pas en « nouvelle »

Transitions refusées : de Postulé vers Postuler ou Rejeté, de Rejeté vers Postuler.

### Profils

- La barre de profil propose « Nouveau profil », « Modifier » et « Supprimer », chacun écrivant ou retirant le profil dans la base
- Le nom d'un profil se modifie : ses offres et sa date de recherche le suivent, et un nom déjà pris est refusé
- La suppression efface le profil, ses offres et sa date de recherche, après une confirmation qui l'annonce
- Quand un profil est modifié, ses offres déjà en base sont réévaluées tout de suite d'après les nouvelles règles, sans appel réseau : les écartées qui passent redeviennent « nouvelles », les retenues qui ne passent plus sont écartées
- Au premier lancement, les anciens fichiers `profils/*.json` du dossier de données (dont `_global.json`) sont importés dans la base puis renommés en `.json.importe` ; l'import ne se refait pas

Règles de validation : [chargeur.py](src/alertes_emploi/profils/chargeur.py).

### Recherche

- Le bouton « Rechercher » relance la recherche d'un seul profil, celui choisi dans le filtre (il est désactivé avec « Tous », pour ne pas solliciter toutes les sources d'un coup)
- Une recherche en cours s'annule par le bouton « Annuler » du bandeau : elle s'arrête à la fin du terme en cours et rien n'est enregistré
- Si un site répond 429 (blocage, trop de requêtes) sur sa page de recherche, la recherche du profil est annulée en entier : rien n'est enregistré, un message en donne la raison et il faut relancer plus tard
- Si LinkedIn répond 429 sur la page d'une offre, la recherche continue : les pages d'offre suivantes ne sont plus demandées, et les offres sans description sont enregistrées en attente (invisibles). Le journal écrit une ligne « erreur 429 sur des pages d'offre, N description(s) à récupérer à la prochaine recherche »
- Au début de la recherche suivante, la description de ces offres en attente est réessayée avant tout le reste, avec arrêt au premier 429. Un bandeau « Reprise des descriptions en cours… » avec le bouton « Annuler » le signale, et aucune recherche manuelle ne peut être lancée pendant ce temps. Une offre LinkedIn déjà en base avec sa description n'est jamais redemandée au site, pour ne pas consommer de requêtes

### Recherche automatique

- Au démarrage de l'application, la recherche du profil le plus ancien (jamais recherché en premier, puis la plus ancienne date) se lance d'elle-même, sauf si la dernière recherche auto date de moins du délai réglé ; sans date enregistrée, elle se lance
- Le menu « Réglages » ouvre la popup « Recherche auto » : case « Lancer une recherche au démarrage » (cochée par défaut), délai entre deux recherches en minutes (60 par défaut, de 5 à 1440) et date de la dernière recherche, modifiable et vidable
- Le bouton « Lancer maintenant » lance tout de suite la recherche du profil le plus ancien, sans tenir compte du délai ; la popup indique le profil visé et sa dernière recherche
- La date est stockée dans la base et mise à jour à la fin de chaque recherche réussie, automatique ou lancée à la main, jamais par une annulation ni une erreur
- La fin, l'annulation et les erreurs d'une recherche auto sont inscrites dans le journal

### Menu Réglages

Le menu « Réglages » de l'en-tête regroupe en trois sections :

- **Recherche** : « Recherche auto » et « Paramètres globaux »
- **Suivi** : « Historique des recherches » (popup « Dernières recherches », qui donne la date de dernière recherche de chaque profil, « jamais » si aucune) et « Journal » (popup affichant les 200 dernières lignes de `journal.log`, où la fenêtre écrit les logs de jobspy et les erreurs non gérées)
- **Application** : « Ajouter un raccourci au menu Démarrer » et « Mettre à jour JobSpy »

### Paramètres globaux

- Ils sont enregistrés dans la base et portent les exclusions communes à tous les profils : `mots_exclus`, `villes_exclues` et `priorite_sites` (listes de textes), tous facultatifs, qui s'ajoutent à celles du profil (union) sans le modifier
- Les réglages de la recherche auto (`recherche_auto_demarrage` et `delai_recherche_auto_minutes`) y sont aussi stockés, mais s'éditent dans la popup « Recherche auto »
- À l'enregistrement, les offres déjà en base de tous les profils sont réévaluées tout de suite et le bilan s'affiche
- Ils sont pris en compte par « Rechercher », par la recherche auto et par la ligne de commande

Chargement et validation : [chargeur.py](src/alertes_emploi/profils/chargeur.py).

### Mise à jour de JobSpy

- « Mettre à jour JobSpy » compare la version installée à la dernière publiée et l'installe dans le venv du build
- Si l'installation échoue, la version précédente est restaurée ; après un succès, l'application demande d'être relancée
- L'entrée est grisée hors du build

## Règles de filtrage

- **Sites** : le formulaire propose `indeed`, `linkedin`, `glassdoor`, `google`, `hellowork` (site français, descriptions complètes), `francetravail`, `apec` et `lesjeudis`. Google ne renvoie aucune offre pour l'instant : sa page n'est plus lisible par JobSpy. `francetravail`, `apec` et `lesjeudis` ne passent pas par JobSpy : chacun a un adaptateur écrit à la main (scraping, peut cesser de fonctionner si le site change). Leurs cases sont déjà dans le formulaire, mais les adaptateurs arrivent site par site (`francetravail` est livré) : tant que celui d'un site n'est pas livré, le cocher ne renvoie aucune offre (le site est ignoré et un avertissement est écrit au journal). Comme pour Indeed, ce scraping peut contrevenir aux conditions d'utilisation de ces sites : usage personnel, faible volume, aucune redistribution. Les autres sites gérés par JobSpy (`zip_recruiter`, `bayt`, `naukri`, `bdjobs`) sont étrangers et ne sont pas proposés, mais s'écrivent à la main dans un profil.
- **France Travail** (`francetravail`, adaptateur livré) :
  - Le site interdit la recherche par mots-clés aux robots (`robots.txt`) : l'adaptateur cherche par lieu et rayon, trié par date, lit les `nombre_resultats` premières offres, puis filtre par mots-clés sur le titre et la description complète (celle qu'affiche la page de détail). « Nombre de résultats » compte donc les offres lues avant ce filtre, et chaque offre lue coûte une requête de plus (une seconde de pause entre deux requêtes).
  - Le lieu du profil est converti en code INSEE avec la table locale [communes.json](src/alertes_emploi/sources/communes.json) (données de geo.api.gouv.fr, regénérée par `scripts/generer-communes.ps1`). Un code postal dans le lieu départage les homonymes, sinon la commune la plus peuplée l'emporte ; un lieu introuvable est ignoré avec un avertissement au journal.
  - Gérés : lieu et rayon (0 km est envoyé comme 1 km, le site ne renvoie rien pour 0). Ignorés : ancienneté, type de contrat et télétravail.
  - La société est celle de la liste, que le site tronque parfois à une quarantaine de caractères.
- **Zone géographique** : avec un `rayon_km` supérieur à 0, on fait confiance au rayon appliqué par chaque source ; avec 0, seules les offres dont le lieu contient la ville du profil sont retenues (motif « hors zone »). Le champ `villes_exclues` écarte en plus les offres dont la ville (lieu avant la première virgule, sans casse ni accents) est exactement l'une de celles listées. Règles : [regles.py](src/alertes_emploi/filtres/regles.py).
- **Terme de recherche** : par défaut (`exiger_termes` vrai), une offre dont le titre et la description ne contiennent aucun terme de recherche du profil (mot entier, sans casse ni accents) est écartée avec le motif « terme de recherche absent ». Décocher « Exiger un terme de recherche dans l'offre » désactive ce contrôle. Règles : [bruit.py](src/alertes_emploi/filtres/bruit.py).
- **Description** : une offre dont la description est vide ou ne contient que des espaces n'est plus enregistrée en base (pas de réglage dans le profil) ; au démarrage, une migration supprime celles qui y figuraient déjà. Seule exception, une offre LinkedIn sans description à cause d'un 429 sur sa page est gardée en attente, invisible, jusqu'à ce que sa description soit récupérée ; elle est alors filtrée comme les autres offres.

## Maintenance

### Nettoyage de la base

Deux offres sont des doublons quand elles sont dans la même ville et que leur score de ressemblance (titre, société et description, calculé avec RapidFuzz) est assez élevé. Un titre proche est exigé, sauf si les descriptions sont quasi identiques. Les seuils et les poids sont dans [modele.py](src/alertes_emploi/modele.py). Une recherche fusionne ces doublons ; pour appliquer la même règle aux offres déjà stockées :

```
& .\scripts\nettoyer-base.ps1 -Simuler
& .\scripts\nettoyer-base.ps1
```

`-Simuler` n'écrit rien et affiche le nombre de lignes touchées. Sans lui, le script exige la sauvegarde `alertes.db.bak` à côté de la base (dossier de données). Détail : [nettoyage.py](src/alertes_emploi/nettoyage.py).

### Priorité des sites

Quand deux offres sont des doublons, celle du site le mieux classé est gardée ; à rang égal, la plus ancienne. L'ordre se règle dans le paramètre global `priorite_sites` (bouton « Paramètres globaux » du menu « Réglages »). Par défaut : `francetravail`, `apec`, `lesjeudis`, `hellowork`, `indeed`, `linkedin`, `glassdoor` ; un site absent de la liste passe après ceux de la liste, et une liste vide revient à « la plus ancienne gagne ».

La priorité s'applique aux recherches et à `nettoyer-base.ps1`, qui lit les paramètres globaux dans la base. Elle n'agit qu'à partir de maintenant : des doublons déjà fusionnés ne reviennent pas d'eux-mêmes, il faut restaurer une sauvegarde de la base puis relancer le nettoyage. L'offre gardée reprend la plus ancienne première apparition et le statut le plus avancé des deux (un « Postulé » n'est jamais perdu).

### Planification (facultative)

`scripts/planifier-alertes-emploi.ps1` crée une tâche planifiée Windows quotidienne par profil, désigné par son nom dans la base (`-Profil`, `-Heure`, `-Supprimer`, `-WhatIf`) ; la tâche porte le nom du profil, donc une tâche créée avant le passage à la base sous l'ancien nom est à recréer. Elle n'est pas activée : la recherche se lance pour l'instant à la main.

### Mise à jour de JobSpy côté développeur

`scripts/monter-version-jobspy.ps1` monte la version épinglée de JobSpy dans le dépôt, lance les tests et revient à la version précédente s'ils échouent.

## Tests

```
.\.venv\Scripts\python.exe -m pytest
```

## Avertissement

Le scraping d'Indeed contrevient à ses conditions d'utilisation : usage personnel, faible volume, aucune redistribution.
