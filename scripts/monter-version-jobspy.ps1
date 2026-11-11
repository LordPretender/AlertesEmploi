<#
.SYNOPSIS
Monte la version épinglée de python-jobspy, lance les tests et revient à la version précédente si ça échoue.
.PARAMETER Projet
Racine du projet contenant requirements.txt et le venv (par défaut : le dossier parent de scripts/).
.PARAMETER Version
Version cible de python-jobspy (par défaut : la dernière version publiée sur PyPI).
#>
[CmdletBinding()]
param(
    [string]$Projet,
    [string]$Version
)

$ErrorActionPreference = 'Stop'

if (-not $Projet) { $Projet = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }

$paquet = 'python-jobspy'
$pythonVenv = Join-Path $Projet '.venv\Scripts\python.exe'
$requirements = Join-Path $Projet 'requirements.txt'

if (-not (Test-Path $pythonVenv)) { throw "Venv introuvable : lancer scripts/preparer-venv.ps1 d'abord." }
if (-not (Test-Path $requirements)) { throw "requirements.txt introuvable : $requirements" }

$encodage = New-Object System.Text.UTF8Encoding($false)
$contenuInitial = [System.IO.File]::ReadAllText($requirements, $encodage)
$motif = "(?m)^$([regex]::Escape($paquet))==(\S+)\s*$"
$m = [regex]::Match($contenuInitial, $motif)
if (-not $m.Success) { throw "Ligne $paquet==<version> absente de requirements.txt." }
$precedente = $m.Groups[1].Value

if (-not $Version) {
    $sortie = & $pythonVenv -m pip index versions $paquet 2>&1 | Out-String
    $v = [regex]::Match($sortie, "$([regex]::Escape($paquet)) \(([^)]+)\)")
    if (-not $v.Success) { throw "Dernière version introuvable via pip index :`n$sortie" }
    $Version = $v.Groups[1].Value
}

if ($Version -eq $precedente) {
    Write-Host "$paquet est déjà en $Version : rien à faire."
    return
}

function Restaurer {
    [System.IO.File]::WriteAllText($requirements, $contenuInitial, $encodage)
    & $pythonVenv -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Rollback incomplet : réinstallation de $paquet==$precedente échouée." }
    Write-Host "Retour à $paquet==$precedente."
}

Write-Host "Montée de $paquet : $precedente -> $Version"
$contenuNouveau = [regex]::Replace($contenuInitial, $motif, "$paquet==$Version")
[System.IO.File]::WriteAllText($requirements, $contenuNouveau, $encodage)

& $pythonVenv -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installation de $paquet==$Version échouée."
    Restaurer
    exit 1
}

Push-Location $Projet
try {
    & $pythonVenv -m pytest
    $codeTests = $LASTEXITCODE
}
finally {
    Pop-Location
}

if ($codeTests -ne 0) {
    Write-Host "Tests en échec avec $paquet==$Version (voir test de contrat de l'adaptateur)."
    Restaurer
    exit 1
}

Write-Host "$paquet==$Version validé : requirements.txt mis à jour."
