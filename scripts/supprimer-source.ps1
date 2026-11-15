<#
.SYNOPSIS
Supprime de alertes.db toutes les offres d'une source (ex. lesjeudis), après copie horodatée de la base.
.PARAMETER Source
Valeur de la colonne source à supprimer (ex. lesjeudis).
.PARAMETER Base
Base SQLite à modifier (par défaut : alertes.db du dossier de données).
.PARAMETER Donnees
Dossier de données contenant alertes.db (par défaut : build\AlertesEmploi\donnees du projet).
.PARAMETER Simuler
Affiche les comptes sans rien écrire ni sauvegarder.
.PARAMETER Projet
Racine du projet contenant .venv (par défaut : le dossier parent de scripts/).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Source,
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
if (-not (Test-Path $Base)) { throw "Base introuvable : $Base" }

if (-not $Simuler) {
    $sauvegarde = "$Base.avant-suppression-$Source-" + (Get-Date -Format 'yyyyMMddHHmmss')
    Copy-Item -LiteralPath $Base -Destination $sauvegarde
    Write-Host "Sauvegarde : $sauvegarde"
}

$code = @'
import sqlite3, sys
base, source, simuler = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
connexion = sqlite3.connect(base)
def comptes():
    return dict(connexion.execute("SELECT source, COUNT(*) FROM offres GROUP BY source").fetchall())
avant = comptes()
print("Avant :", avant)
for profil, nombre in connexion.execute("SELECT profil, COUNT(*) FROM offres WHERE source = ? GROUP BY profil", (source,)):
    print(f"  profil {profil} : {nombre}")
if simuler:
    print("Simulation, rien d'écrit.")
    sys.exit(0)
with connexion:
    connexion.execute("DELETE FROM offres WHERE source = ?", (source,))
apres = comptes()
print("Après :", apres)
autres_avant = {s: n for s, n in avant.items() if s != source}
if apres.get(source, 0) != 0 or apres != autres_avant:
    print("ERREUR : vérification échouée.", file=sys.stderr)
    sys.exit(1)
print("Vérification OK.")
'@

$env:PYTHONIOENCODING = 'utf-8'
$fichierCode = Join-Path ([System.IO.Path]::GetTempPath()) ("supprimer-source-" + [guid]::NewGuid().ToString('N') + '.py')
try {
    [System.IO.File]::WriteAllText($fichierCode, $code, (New-Object System.Text.UTF8Encoding($false)))
    & $pythonVenv $fichierCode $Base $Source ($(if ($Simuler) { '1' } else { '0' }))
    $codeSortie = $LASTEXITCODE
}
finally {
    Remove-Item -LiteralPath $fichierCode -ErrorAction SilentlyContinue
}
exit $codeSortie
