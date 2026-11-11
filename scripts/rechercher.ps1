<#
.SYNOPSIS
Lance la recherche d'un profil enregistré dans la base avec le venv du projet et affiche les offres trouvées.
.PARAMETER Profil
Nom du profil dans la base (obligatoire).
.PARAMETER Donnees
Dossier de données contenant alertes.db (par défaut : build\AlertesEmploi\donnees du projet).
.PARAMETER Projet
Racine du projet contenant .venv et src (par défaut : le dossier parent de scripts/).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Profil,
    [string]$Donnees,
    [string]$Projet
)

$ErrorActionPreference = 'Stop'

if (-not $Projet) { $Projet = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not $Donnees) { $Donnees = Join-Path $Projet 'build\AlertesEmploi\donnees' }

$pythonVenv = Join-Path $Projet '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonVenv)) { throw "Venv introuvable : lancer scripts/preparer-venv.ps1 d'abord." }

$env:PYTHONPATH = Join-Path $Projet 'src'
$env:PYTHONIOENCODING = 'utf-8'
& $pythonVenv -m alertes_emploi.recherche $Profil --base (Join-Path $Donnees 'alertes.db')
exit $LASTEXITCODE
