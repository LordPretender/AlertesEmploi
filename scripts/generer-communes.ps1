<#
.SYNOPSIS
Télécharge la table des communes françaises (nom, code INSEE, codes postaux, population) depuis geo.api.gouv.fr vers src\alertes_emploi\sources\communes.json.
.PARAMETER Projet
Racine du projet contenant src (par défaut : le dossier parent de scripts/).
.PARAMETER Sortie
Fichier à produire (par défaut : src\alertes_emploi\sources\communes.json sous le projet).
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    [string]$Projet = (Split-Path -Parent $PSScriptRoot),
    [string]$Sortie
)

$ErrorActionPreference = 'Stop'

if (-not $Sortie) { $Sortie = Join-Path $Projet 'src\alertes_emploi\sources\communes.json' }
$url = 'https://geo.api.gouv.fr/communes?fields=nom,code,codesPostaux,population&format=json'

if (-not $PSCmdlet.ShouldProcess($Sortie, "Télécharger la table des communes depuis $url")) { return }

$reponse = Invoke-WebRequest -Uri $url -UseBasicParsing
$octets = $reponse.RawContentStream.ToArray()
$texte = [System.Text.Encoding]::UTF8.GetString($octets)
if ($texte.Length -lt 1000000 -or -not $texte.TrimStart().StartsWith('[')) { throw "Réponse inattendue de l'API (taille : $($texte.Length) caractères) : fichier non écrit." }

[System.IO.File]::WriteAllText($Sortie, $texte, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "Table écrite : $Sortie ($([math]::Round($octets.Length / 1MB, 1)) Mo)"
