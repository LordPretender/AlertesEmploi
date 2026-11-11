<#
.SYNOPSIS
Lance le serveur local de consultation des offres de toute la base (filtre par profil) et ouvre la page dans le navigateur.
.PARAMETER Donnees
Dossier de données contenant alertes.db (par défaut : build\AlertesEmploi\donnees du projet).
.PARAMETER Projet
Racine du projet contenant .venv et src (par défaut : le dossier parent de scripts/).
.PARAMETER Port
Port d'écoute sur 127.0.0.1 (par défaut : 8765).
#>
[CmdletBinding()]
param(
    [string]$Donnees,
    [string]$Projet,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'

if (-not $Projet) { $Projet = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not $Donnees) { $Donnees = Join-Path $Projet 'build\AlertesEmploi\donnees' }

$pythonVenv = Join-Path $Projet '.venv\Scripts\python.exe'
if (-not (Test-Path $pythonVenv)) { throw "Venv introuvable : lancer scripts/preparer-venv.ps1 d'abord." }

$env:PYTHONPATH = Join-Path $Projet 'src'
$env:PYTHONIOENCODING = 'utf-8'

$adresse = "http://127.0.0.1:$Port/"
$null = Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 1; Start-Process $u } -ArgumentList $adresse

Write-Host "Consultation sur $adresse (Ctrl+C pour arrêter)"
& $pythonVenv -m alertes_emploi.interface.serveur --base (Join-Path $Donnees 'alertes.db') --port $Port
exit $LASTEXITCODE
