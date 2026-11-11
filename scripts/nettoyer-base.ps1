<#
.SYNOPSIS
Fusionne les quasi-doublons de alertes.db (dossier de données du build par défaut), après vérification de la sauvegarde .bak.
.PARAMETER Base
Base SQLite à nettoyer (par défaut : alertes.db du dossier de données).
.PARAMETER Donnees
Dossier de données contenant alertes.db (par défaut : build\AlertesEmploi\donnees du projet).
.PARAMETER Simuler
Affiche le nombre de lignes touchées sans rien écrire.
.PARAMETER Projet
Racine du projet contenant .venv et src (par défaut : le dossier parent de scripts/).
#>
[CmdletBinding()]
param(
    [string]$Base,
    [string]$Donnees,
    [switch]$Simuler,
    [string]$Projet
)

$ErrorActionPreference = 'Stop'

if (-not $Projet) { $Projet = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not $Donnees) { $Donnees = Join-Path $Projet 'build\AlertesEmploi\donnees' }
if (-not $Base) { $Base = Join-Path $Donnees 'alertes.db' }

$pythonVenv = Join-Path $Projet '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonVenv)) { throw "Venv introuvable : lancer scripts/preparer-venv.ps1 d'abord." }
if (-not $Simuler -and -not (Test-Path "$Base.bak")) { throw "Sauvegarde introuvable : $Base.bak (la créer avant d'écrire)." }

$env:PYTHONPATH = Join-Path $Projet 'src'
$env:PYTHONIOENCODING = 'utf-8'
$arguments = @('--base', $Base)
if ($Simuler) { $arguments += '--simuler' }
& $pythonVenv -m alertes_emploi.nettoyage @arguments
exit $LASTEXITCODE
