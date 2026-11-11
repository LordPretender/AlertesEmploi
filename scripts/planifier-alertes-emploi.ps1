<#
.SYNOPSIS
Crée (ou supprime) une tâche planifiée Windows qui lance chaque jour la recherche d'un profil.
.DESCRIPTION
La tâche exécute scripts/rechercher.ps1 pour le profil donné ; la sortie est ajoutée à planification.log dans le dossier de données.
Une tâche par profil (nom : AlertesEmploi-<nom du profil>, caractères interdits remplacés par _). Relancer le script remplace la tâche existante.
Utiliser -WhatIf pour voir ce qui serait créé sans rien modifier.
.PARAMETER Profil
Nom du profil dans la base (obligatoire).
.PARAMETER Heure
Heure de lancement quotidien, au format HH:mm (par défaut : 08:00).
.PARAMETER Donnees
Dossier de données contenant alertes.db et planification.log (par défaut : build\AlertesEmploi\donnees du projet).
.PARAMETER Projet
Racine du projet contenant scripts (par défaut : le dossier parent de scripts/).
.PARAMETER Supprimer
Supprime la tâche planifiée du profil au lieu de la créer.
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [Parameter(Mandatory)][string]$Profil,
    [string]$Heure = '08:00',
    [string]$Donnees,
    [string]$Projet,
    [switch]$Supprimer
)

$ErrorActionPreference = 'Stop'

if (-not $Projet) { $Projet = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path) }
if (-not $Donnees) { $Donnees = Join-Path $Projet 'build\AlertesEmploi\donnees' }
if ($Profil.Contains('"')) { throw "Nom de profil invalide : les guillemets doubles ne sont pas pris en charge." }

$nomTache = 'AlertesEmploi-' + ($Profil -replace '[\\/:*?"<>|]', '_')

if ($Supprimer) {
    if ($PSCmdlet.ShouldProcess($nomTache, 'Supprimer la tâche planifiée')) {
        Unregister-ScheduledTask -TaskName $nomTache -Confirm:$false
        Write-Host "Tâche supprimée : $nomTache"
    }
    return
}

$heureValide = [datetime]::MinValue
if (-not [datetime]::TryParseExact($Heure, 'HH:mm', $null, 'None', [ref]$heureValide)) { throw "Heure invalide : '$Heure' (format attendu HH:mm)." }

$lanceur = Join-Path $Projet 'scripts\rechercher.ps1'
$journal = Join-Path $Donnees 'planification.log'
$profilCite = $Profil.Replace("'", "''")
$commande = "New-Item -ItemType Directory -Force -Path (Split-Path '$journal') | Out-Null; & '$lanceur' -Profil '$profilCite' -Donnees '$Donnees' *>> '$journal'"

if ($PSCmdlet.ShouldProcess($nomTache, "Créer la tâche planifiée quotidienne à $Heure")) {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -Command `"$commande`""
    $declencheur = New-ScheduledTaskTrigger -Daily -At $heureValide
    $reglages = New-ScheduledTaskSettingsSet -StartWhenAvailable
    Register-ScheduledTask -TaskName $nomTache -Action $action -Trigger $declencheur -Settings $reglages -Force | Out-Null
    Write-Host "Tâche créée : $nomTache (chaque jour à $Heure, journal : $journal)"
}
