<#
.SYNOPSIS
Construit le dossier portable build/AlertesEmploi (lanceur Python, src, requirements) ; ne remplace que systeme/ et le lanceur, jamais donnees/.
.PARAMETER Projet
Racine du projet contenant src, requirements.txt et scripts\modele-build (par défaut : le dossier parent de scripts/).
.PARAMETER Sortie
Dossier du build à produire (par défaut : build\AlertesEmploi sous le projet).
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Projet = (Split-Path -Parent $PSScriptRoot),
    [string]$Sortie
)

$ErrorActionPreference = 'Stop'

if (-not $Sortie) { $Sortie = Join-Path $Projet 'build\AlertesEmploi' }

$modeles = Join-Path $PSScriptRoot 'modele-build'
$systeme = Join-Path $Sortie 'systeme'
$donnees = Join-Path $Sortie 'donnees'
$lanceur = Join-Path $Sortie 'Alertes emploi.pyw'

if (-not $PSCmdlet.ShouldProcess($Sortie, 'Reconstruire systeme\ et le lanceur (donnees\ intact)')) { return }

New-Item -ItemType Directory -Force -Path $Sortie | Out-Null
if (Test-Path $systeme) { Remove-Item -Recurse -Force $systeme }
New-Item -ItemType Directory -Force -Path $systeme | Out-Null

# Anciens lanceurs (version avec Python embarqué)
$ancienLanceur = Join-Path $Sortie 'Alertes emploi.vbs'
if (Test-Path $ancienLanceur) { Remove-Item -Force $ancienLanceur }

# Dépendances (hors pytest) : installées au premier lancement par preparer.py, dans un venv propre à chaque poste
Get-Content (Join-Path $Projet 'requirements.txt') | Where-Object { $_ -notmatch '^\s*pytest' } | Set-Content -Path (Join-Path $systeme 'requirements.txt') -Encoding ASCII

# Code du projet
$copieSrc = Join-Path $systeme 'src'
Copy-Item -Recurse -Path (Join-Path $Projet 'src') -Destination $copieSrc
Get-ChildItem -Path $copieSrc -Recurse -Directory -Filter '__pycache__' | Remove-Item -Recurse -Force

# Scripts Python du build (indépendants du système d'exploitation)
Copy-Item -Path (Join-Path $modeles 'preparer.py') -Destination $systeme
Copy-Item -Path (Join-Path $modeles 'lanceur.pyw') -Destination $lanceur

# donnees\ n'est créé que s'il manque, jamais vidé
New-Item -ItemType Directory -Force -Path $donnees | Out-Null

$taille = [math]::Round((Get-ChildItem -Recurse -File $Sortie -Exclude '*.db' | Where-Object { $_.FullName -notlike "$donnees*" } | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "Build produit : $Sortie"
Write-Host "Hors donnees\ : $taille Mo (donnees\ conservé tel quel)"
