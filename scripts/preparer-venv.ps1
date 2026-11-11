<#
.SYNOPSIS
Crée le venv du projet (.venv) s'il manque et y installe requirements.txt.
.PARAMETER Projet
Racine du projet contenant requirements.txt (par défaut : le dossier parent de scripts/).
.PARAMETER Version
Version de Python demandée au lanceur py (3.12 par défaut).
#>
[CmdletBinding()]
param(
    [string]$Projet = (Split-Path -Parent $PSScriptRoot),
    [string]$Version = '3.12'
)

$ErrorActionPreference = 'Stop'

$venv = Join-Path $Projet '.venv'
$pythonVenv = Join-Path $venv 'Scripts\python.exe'

if (-not (Test-Path $pythonVenv)) {
    & py "-$Version" -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Création du venv échouée (py -$Version)." }
    Write-Host "Venv créé : $venv"
}
else {
    Write-Host "Venv déjà présent : $venv"
}

& $pythonVenv -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'Mise à jour de pip échouée.' }

$requirements = Join-Path $Projet 'requirements.txt'
if (Test-Path $requirements) {
    & $pythonVenv -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw 'Installation de requirements.txt échouée.' }
}
else {
    Write-Host 'Pas de requirements.txt : rien à installer.'
}
