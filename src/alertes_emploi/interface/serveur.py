"""Interface web locale : consultation des offres stockées et mise à jour de la base par une nouvelle recherche."""

import argparse
import json
import logging
import re
import sys
import threading
from dataclasses import asdict
from datetime import date, datetime, timedelta
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from alertes_emploi.profils.chargeur import ParametresGlobaux, Profil, ProfilInvalide
from alertes_emploi import NOM_JOURNAL, raccourci
from alertes_emploi.miseajour_jobspy import ErreurMiseAJour, Jobspy, version_valide
from alertes_emploi.recherche import RechercheAnnulee, lancer_recherche
from alertes_emploi.sources.jobspy_adapter import RechercheBloquee
from alertes_emploi.stockage.base import A_POSTULER, BASE_PAR_DEFAUT, DOSSIER_PROFILS, NOUVELLE, POSTULEE, REJETEE, OffreStockee, Stockage

_journal = logging.getLogger(NOM_JOURNAL)

_PAGE = """<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Offres</title>
<style>
:root {
  color-scheme: light dark;
  --fond: #f4f5f9; --surface: #ffffff; --surface-2: #f0f1f7; --texte: #1c1e2b; --attenue: #5f6478; --bordure: #e1e3ec;
  --accent: #4f46e5; --accent-texte: #ffffff; --accent-doux: #e8e7fd;
  --danger: #c62828; --danger-doux: #fde8e8; --succes: #1a7f37; --succes-doux: #e3f4e7;
  --voile: rgba(8, 10, 25, .55);
  --rayon: 12px; --rayon-petit: 8px; --espace: 1rem;
  --ombre: 0 1px 2px rgba(20, 24, 50, .06), 0 4px 14px rgba(20, 24, 50, .08);
  --ombre-forte: 0 8px 30px rgba(20, 24, 50, .25);
  --police: system-ui, "Segoe UI", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fond: #0f1220; --surface: #181c2e; --surface-2: #20253b; --texte: #e8eaf4; --attenue: #9aa0b8; --bordure: #2c3250;
    --accent: #818cf8; --accent-texte: #0f1220; --accent-doux: #2a2f55;
    --danger: #f08a8a; --danger-doux: #3b1f27; --succes: #5fcf80; --succes-doux: #173324;
    --ombre: 0 1px 2px rgba(0, 0, 0, .3), 0 4px 14px rgba(0, 0, 0, .35);
    --ombre-forte: 0 8px 30px rgba(0, 0, 0, .6);
  }
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { height: 100%; }
body { font-family: var(--police); background: var(--fond); color: var(--texte); margin: 0; padding: 1.5rem; line-height: 1.45; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
body > .entete, body > .bandeau, body > .carte { flex: none; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
a { color: var(--accent); }
h1 { margin: 0; font-size: 1.6rem; }

button, summary.bouton, input, select { font: inherit; color: var(--texte); }
button, summary.bouton { padding: .5rem .9rem; border: 1px solid var(--bordure); border-radius: var(--rayon-petit); background: var(--surface); cursor: pointer; }
button:hover:not(:disabled), summary.bouton:hover { background: var(--surface-2); }
button:disabled { opacity: .5; cursor: not-allowed; }
button.primaire { background: var(--accent); border-color: var(--accent); color: var(--accent-texte); font-weight: 600; }
button.primaire:hover:not(:disabled) { background: var(--accent); filter: brightness(1.1); }
button.danger { color: var(--danger); border-color: var(--danger); }
button.danger:hover:not(:disabled) { background: var(--danger-doux); }
input, select { background: var(--surface); border: 1px solid var(--bordure); border-radius: var(--rayon-petit); padding: .45rem .65rem; }
input[type="checkbox"] { accent-color: var(--accent); }
input.court { width: 5em; }

.entete { display: flex; align-items: center; justify-content: space-between; gap: var(--espace); flex-wrap: wrap; margin-bottom: var(--espace); }
.outils { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; }
.menu { position: relative; }
.menu > summary { list-style: none; }
.menu > summary::-webkit-details-marker { display: none; }
.menu .entrees { position: absolute; right: 0; z-index: 10; margin-top: .4rem; min-width: 18rem; background: var(--surface); border: 1px solid var(--bordure); border-radius: var(--rayon); box-shadow: var(--ombre-forte); padding: .6rem; display: flex; flex-direction: column; align-items: stretch; gap: .5rem; }
.menu .section { font-size: .75rem; text-transform: uppercase; letter-spacing: .04em; color: var(--attenue); }
.menu hr { width: 100%; margin: .1rem 0; border: none; border-top: 1px solid var(--bordure); }

.bandeau { position: sticky; top: .75rem; z-index: 20; display: flex; align-items: center; gap: .7rem; margin-bottom: var(--espace); padding: .7rem 1rem; border-radius: var(--rayon); box-shadow: var(--ombre); background: var(--accent-doux); border: 1px solid var(--accent); }
.bandeau.ok { background: var(--succes-doux); border-color: var(--succes); }
.bandeau.erreur { background: var(--danger-doux); border-color: var(--danger); }
#b-texte { flex: 1; }
.bandeau .annuler { margin-left: auto; }
.bandeau .fermer { border: none; background: transparent; padding: 0 .4rem; font-size: 1.3rem; line-height: 1; }
.spinner { width: 1.1rem; height: 1.1rem; border: 3px solid var(--bordure); border-top-color: var(--accent); border-radius: 50%; animation: tourne .8s linear infinite; }
@keyframes tourne { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 3s; } }

.carte { background: var(--surface); border-radius: var(--rayon); box-shadow: var(--ombre); padding: .9rem 1.1rem; margin-bottom: var(--espace); }
.barre { display: flex; gap: .7rem; align-items: center; flex-wrap: wrap; }
.espace { flex: 1; }
.aide { color: var(--attenue); font-size: .9rem; }
#compte { margin-left: auto; color: var(--attenue); font-size: .9rem; }
.bascule { position: relative; }
.bascule input { position: absolute; opacity: 0; inset: 0; margin: 0; cursor: pointer; }
.bascule span { display: inline-block; padding: .4rem .85rem; border: 1px solid var(--bordure); border-radius: 999px; background: var(--surface); }
.bascule input:checked + span { background: var(--accent); border-color: var(--accent); color: var(--accent-texte); }
.bascule input:focus-visible + span { outline: 2px solid var(--accent); outline-offset: 2px; }

.tableau { background: var(--surface); border-radius: var(--rayon); box-shadow: var(--ombre); flex: 1; min-height: 0; overflow: auto; }
table { border-collapse: separate; border-spacing: 0; width: 100%; min-width: 62rem; }
th, td { padding: .75rem .9rem; text-align: left; vertical-align: middle; }
thead th { position: sticky; top: 0; z-index: 1; background: var(--surface); border-bottom: 1px solid var(--bordure); font-size: .78rem; text-transform: uppercase; letter-spacing: .04em; color: var(--attenue); white-space: nowrap; }
th[data-col] { cursor: pointer; user-select: none; }
th[data-col]::after { content: "↕"; margin-left: .35em; opacity: .35; }
th[aria-sort="ascending"]::after { content: "▲"; opacity: 1; color: var(--accent); }
th[aria-sort="descending"]::after { content: "▼"; opacity: 1; color: var(--accent); }
tbody.offre > tr:first-child td { border-top: 1px solid var(--bordure); }
tbody.offre > tr:first-child:hover td { background: var(--surface-2); }
td.titre { font-weight: 600; }
table.maj { min-width: 0; }
table.maj thead th { position: static; }
table.maj td { border-top: 1px solid var(--bordure); }
tbody.offre button { padding: .2rem .6rem; font-size: .82rem; }
/* Le titre est un bouton d'apparence de texte : il déplie et referme la description. */
tbody.offre button.titre-lien { padding: 0; border: none; background: none; font-size: inherit; font-weight: 600; text-align: left; }
tbody.offre button.titre-lien:hover:not(:disabled) { background: none; text-decoration: underline; }
tbody[data-statut="rejetee"] > tr:first-child td:not(:last-child) { opacity: .6; }
.logo { display: inline-flex; align-items: center; justify-content: center; width: 1.9rem; height: 1.9rem; margin-right: .6rem; border-radius: 50%; background: hsl(var(--t, 230) 50% 42%); color: #fff; font-size: .85rem; font-weight: 700; vertical-align: middle; }
.pastille, .statut { display: inline-block; padding: .1rem .6rem; border-radius: 999px; font-size: .78rem; font-weight: 600; }
.pastille { background: var(--succes-doux); color: var(--succes); margin-left: .4rem; }
.statut:empty { display: none; }
tbody[data-statut="rejetee"] .statut { background: var(--danger-doux); color: var(--danger); }
tbody[data-statut="postulee"] .statut { background: var(--accent-doux); color: var(--accent); }
tbody[data-statut="a_postuler"] .statut { background: var(--succes-doux); color: var(--succes); }
.vide { text-align: center; color: var(--attenue); padding: 2rem 1rem; }

td.description { padding: 0 .9rem .9rem; }
.panneau { background: var(--surface-2); border-radius: var(--rayon-petit); padding: 1rem 1.2rem; }
.description h4 { margin: .8rem 0 .2rem; }
.description p, .description ul { margin: .3rem 0; }
.description .actions { display: flex; gap: .6rem; align-items: center; flex-wrap: wrap; margin-top: 1rem; padding-top: .9rem; border-top: 1px solid var(--bordure); }
a.bouton-lien { display: inline-block; padding: .2rem .6rem; font-size: .82rem; border: 1px solid var(--bordure); border-radius: var(--rayon-petit); background: var(--surface); color: var(--texte); text-decoration: none; }
a.bouton-lien:hover { background: var(--surface-2); }
.actions label { font-size: .82rem; color: var(--attenue); }

dialog { border: none; border-radius: var(--rayon); background: var(--surface); color: var(--texte); box-shadow: var(--ombre-forte); padding: 1.4rem 1.6rem; max-width: min(44rem, 92vw); }
dialog::backdrop { background: var(--voile); }
dialog h3 { margin: 0 0 .8rem; }
dialog p { margin: .6rem 0; }
dialog label { display: inline-block; }
dialog .actions-dlg { display: flex; gap: .6rem; margin-top: 1rem; }
.erreur { color: var(--danger); }
#dlg-journal { max-width: 94vw; }
#j-texte { width: 88vw; max-width: 100%; max-height: 60vh; overflow: auto; white-space: pre-wrap; background: var(--surface-2); border-radius: var(--rayon-petit); padding: .8rem; font-size: .85em; }

@media (max-width: 700px) {
  body { padding: .8rem; }
  #compte { margin-left: 0; }
}
</style>
</head>
<body>
<header class="entete">
<h1>Offres</h1>
<div class="outils">
<details class="menu"><summary class="bouton">Réglages</summary>
<div class="entrees">
<span class="section">Recherche</span>
<button id="auto" type="button">Recherche auto</button>
<button id="globaux" type="button">Paramètres globaux</button>
<hr>
<span class="section">Suivi</span>
<button id="maj-dates" type="button">Historique des recherches</button>
<button id="journal" type="button">Journal</button>
<hr>
<span class="section">Application</span>
<button id="raccourci" type="button"__RACCOURCI__>Ajouter un raccourci au menu Démarrer</button>
<button id="jobspy" type="button"__JOBSPY__>Mettre à jour JobSpy</button>
</div></details>
</div>
</header>
<div id="bandeau" class="bandeau" role="status" aria-live="polite" hidden>
<span id="b-spinner" class="spinner" hidden></span><span id="b-texte"></span>
<button id="b-annuler" class="annuler" type="button" hidden>Annuler</button>
<button id="b-fermer" class="fermer" type="button" aria-label="Fermer le message">&times;</button>
</div>
<section class="carte barre">
<label>Profil <select id="f-profil"><option value="">Tous</option>__OPTIONS__</select></label>
<button id="maj" class="primaire" type="button" disabled>Rechercher</button>
<span id="aide-maj" class="aide">Choisissez un profil pour lancer une recherche.</span>
<span id="date-maj" class="aide"></span>
<span class="espace"></span>
<button id="nouveau" type="button">Nouveau profil</button>
<button id="modifier" type="button" disabled>Modifier</button>
<button id="supprimer" class="danger" type="button" disabled>Supprimer</button>
</section>
<section class="carte barre">
<input id="f-texte" type="search" placeholder="Recherche libre" aria-label="Recherche libre">
<input id="f-societe" type="search" placeholder="Société" aria-label="Société">
<input id="f-lieu" type="search" placeholder="Lieu" aria-label="Lieu">
<label>Statut <select id="f-statut"><option value="">Tous sauf rejetées</option><option value="nouvelle">Nouvelles</option><option value="a_postuler">Postuler</option><option value="postulee">Postulé</option><option value="rejetee">Rejeté</option></select></label>
<button id="reinit" type="button">Réinitialiser les filtres</button>
<span id="compte"></span>
</section>
<div class="tableau">
<table id="corps">
<thead><tr>
<th tabindex="0" aria-sort="none" data-col="titre">Titre</th><th tabindex="0" aria-sort="none" data-col="profil">Profil</th><th tabindex="0" aria-sort="none" data-col="site">Site</th><th tabindex="0" aria-sort="none" data-col="societe">Société</th><th tabindex="0" aria-sort="none" data-col="lieu">Lieu</th>
<th tabindex="0" aria-sort="ascending" data-col="vue">Ajoutée le</th><th tabindex="0" aria-sort="none" data-col="libelle">Statut</th><th>Annonce</th>
</tr></thead>
__LIGNES__
</table>
<p id="vide" class="vide" hidden>Aucune offre ne correspond.</p>
</div>
<dialog id="dlg-globaux">
<form id="form-globaux">
<h3>Paramètres globaux</h3>
<p>Exclusions appliquées à tous les profils, en plus des leurs. L'enregistrement réévalue les offres de tous les profils.</p>
<p><label>Mots exclus (virgules) <input id="g-exclus" size="40"></label></p>
<p><label>Villes exclues (virgules) <input id="g-villes" size="40"></label></p>
<p><label>Priorité des sites pour les doublons (virgules, le premier est préféré) <input id="g-priorite" size="40"></label></p>
<p>Quand deux offres sont des doublons, celle du site le plus à gauche est gardée.</p>
<p id="g-erreur" class="erreur"></p>
<div class="actions-dlg"><button class="primaire" type="submit">Enregistrer</button><button id="g-annuler" type="button">Annuler</button></div>
</form>
</dialog>
<dialog id="dlg-profil">
<form id="form-profil">
<h3 id="dlg-titre"></h3>
<p><label>Nom <input id="p-nom" required></label></p>
<p><label>Termes de recherche (séparés par des virgules) <input id="p-termes" required size="40"></label></p>
<p><label>Lieu <input id="p-lieu" required></label> <label>Rayon (km, 0 = seulement la ville du lieu) <input id="p-rayon" class="court" type="number" min="0"></label></p>
<p>Sites : <label><input type="checkbox" name="site" value="indeed"> indeed</label>
<label><input type="checkbox" name="site" value="linkedin"> linkedin</label>
<label><input type="checkbox" name="site" value="glassdoor"> glassdoor</label>
<label><input type="checkbox" name="site" value="google"> google</label>
<label><input type="checkbox" name="site" value="hellowork"> hellowork</label>
<label><input type="checkbox" name="site" value="francetravail"> francetravail</label>
<label><input type="checkbox" name="site" value="apec"> apec</label>
<label><input type="checkbox" name="site" value="lesjeudis"> lesjeudis</label></p>
<p><label>Type de contrat <select id="p-contrat"><option value="">indifférent</option><option>fulltime</option><option>parttime</option><option>contract</option><option>internship</option></select></label>
<label>Nombre de résultats <input id="p-nb" class="court" type="number" min="1"></label></p>
<p><label><input id="p-exiger" type="checkbox"> Exiger un terme de recherche dans l'offre</label></p>
<p><label>Mots exclus (virgules) <input id="p-exclus" size="40"></label></p>
<p><label>Villes exclues (virgules) <input id="p-villes" size="40"></label></p>
<p id="p-erreur" class="erreur"></p>
<div class="actions-dlg"><button class="primaire" type="submit">Enregistrer</button><button id="p-annuler" type="button">Annuler</button></div>
</form>
</dialog>
<dialog id="dlg-suppr">
<h3>Supprimer le profil</h3>
<p id="s-texte"></p>
<p>Les offres de ce profil seront effacées définitivement.</p>
<div class="actions-dlg"><button id="s-confirmer" class="danger" type="button">Supprimer</button><button id="s-annuler" type="button">Annuler</button></div>
</dialog>
<dialog id="dlg-journal">
<h3>Journal</h3>
<pre id="j-texte"></pre>
<div class="actions-dlg"><button id="j-fermer" type="button">Fermer</button></div>
</dialog>
<dialog id="dlg-jobspy">
<h3>Mettre à jour JobSpy</h3>
<p id="jp-texte"></p>
<p>L'installation prend quelques instants ; en cas d'échec, la version actuelle est restaurée. Il faudra ensuite relancer l'application.</p>
<div class="actions-dlg"><button id="jp-installer" class="primaire" type="button" hidden></button><button id="jp-fermer" type="button">Fermer</button></div>
</dialog>
<dialog id="dlg-auto">
<form id="form-auto">
<h3>Recherche auto</h3>
<p><label><input id="a-demarrage" type="checkbox"> Lancer une recherche au démarrage de l'application</label></p>
<p><label>Délai entre deux recherches (minutes, de 5 à 1440) <input id="a-delai" class="court" type="number" min="5" max="1440" required></label></p>
<p>Au démarrage, la recherche ne se lance que si aucun profil n'a été recherché depuis ce délai ; elle vise le profil recherché le moins récemment.</p>
<p><label>Date de la dernière recherche <input id="a-date" type="datetime-local"></label></p>
<p class="aide">Mise à jour par chaque recherche réussie. Au prochain démarrage, la recherche se lance si cette date dépasse le délai, ou si le champ est vide.</p>
<p id="a-aide" class="aide"></p>
<p id="a-erreur" class="erreur"></p>
<div class="actions-dlg"><button class="primaire" type="submit">Enregistrer</button><button id="a-lancer" type="button">Lancer maintenant</button><button id="a-annuler" type="button">Fermer</button></div>
</form>
</dialog>
<dialog id="dlg-maj">
<h3>Dernières recherches</h3>
<div id="dates">__DATES__</div>
<div class="actions-dlg"><button id="m-fermer" type="button">Fermer</button></div>
</dialog>
<script id="profils-data" type="application/json">__PROFILS__</script>
<script id="globaux-data" type="application/json">__GLOBAUX__</script>
<script id="dates-data" type="application/json">__MAJS__</script>
<script id="auto-data" type="application/json">__AUTO__</script>
<script>
const corps = document.getElementById("corps");
// Chaque offre est un <tbody class="offre"> : sa ligne de données et sa ligne de description se filtrent et se trient ensemble.
const lignes = () => Array.from(corps.querySelectorAll("tbody.offre"));
const champ = id => document.getElementById(id);
const definitions = JSON.parse(champ("profils-data").textContent);
const globaux = JSON.parse(champ("globaux-data").textContent);
const majs = JSON.parse(champ("dates-data").textContent);
const cibleAuto = JSON.parse(champ("auto-data").textContent);

// Bandeau de message unique : type « info », « ok », « erreur » ou « attente » (indicateur animé, non fermable) ; `annulable` ajoute le bouton Annuler d'une recherche en cours.
function afficher(message, type, annulable) {
  champ("bandeau").className = "bandeau " + (type || "info");
  champ("b-texte").textContent = message;
  champ("b-spinner").hidden = type !== "attente";
  champ("b-fermer").hidden = type === "attente";
  champ("b-annuler").hidden = !(type === "attente" && annulable);
  champ("b-annuler").disabled = false;
  champ("bandeau").hidden = false;
}
champ("b-fermer").addEventListener("click", () => { champ("bandeau").hidden = true; });
// L'annulation s'applique à la fin du terme en cours : le bandeau le dit en attendant que /etat annonce la fin.
champ("b-annuler").addEventListener("click", async () => {
  champ("b-annuler").disabled = true;
  champ("b-texte").textContent = "Annulation en cours…";
  await fetch("/recherche/annuler", {method: "POST"});
});

// Les filtres survivent au rechargement de la page (par onglet, jusqu'à sa fermeture).
const CHAMPS_TEXTE = ["f-texte", "f-societe", "f-lieu"];
function sauverFiltres() {
  const etat = {profil: champ("f-profil").value, statut: champ("f-statut").value};
  for (const id of CHAMPS_TEXTE) etat[id] = champ(id).value;
  try { sessionStorage.setItem("filtres", JSON.stringify(etat)); } catch (e) { /* stockage indisponible */ }
}
function restaurerFiltres() {
  try {
    const brut = sessionStorage.getItem("filtres");
    if (!brut) return;
    const etat = JSON.parse(brut);
    for (const id of CHAMPS_TEXTE) if (typeof etat[id] === "string") champ(id).value = etat[id];
    if (Array.from(champ("f-statut").options).some(o => o.value === etat.statut)) champ("f-statut").value = etat.statut;
    if (Array.from(champ("f-profil").options).some(o => o.value === etat.profil)) champ("f-profil").value = etat.profil;
  } catch (e) { /* stockage indisponible ou illisible */ }
}

function filtrer() {
  const texte = champ("f-texte").value.toLowerCase();
  const societe = champ("f-societe").value.toLowerCase();
  const lieu = champ("f-lieu").value.toLowerCase();
  const profil = champ("f-profil").value;
  const statut = champ("f-statut").value;
  let visibles = 0;
  for (const ligne of lignes()) {
    const d = ligne.dataset;
    const ok = (!texte || d.texte.includes(texte)) && (!societe || d.societe.includes(societe))
      && (!profil || d.profil === profil)
      && (!lieu || d.lieu.includes(lieu))
      && (statut ? d.statut === statut : d.statut !== "rejetee");
    ligne.hidden = !ok;
    if (ok) visibles++;
  }
  champ("compte").textContent = visibles + " offre(s) affichée(s)";
  champ("vide").hidden = visibles > 0;
  sauverFiltres();
  actualiserBouton();
}

champ("reinit").addEventListener("click", () => {
  for (const id of CHAMPS_TEXTE) champ(id).value = "";
  champ("f-statut").value = "";
  champ("f-profil").value = "";
  filtrer();
});

// La mise à jour ne vise qu'un profil : le bouton reste désactivé sur « Tous » et pendant une recherche.
let enCours = false;
function actualiserBouton() {
  const nom = champ("f-profil").value;
  champ("maj").disabled = enCours || !nom;
  champ("maj").title = nom ? "" : "Choisissez un profil pour lancer une recherche.";
  champ("aide-maj").hidden = !!nom;
  champ("date-maj").textContent = !nom ? "" : (majs[nom] && majs[nom] !== "jamais" ? "Dernière recherche : " + majs[nom] : "Jamais recherché");
  // Seuls les profils enregistrés (présents dans `definitions`) se modifient ou se suppriment : la liste peut aussi nommer un profil supprimé qui a gardé des offres.
  champ("modifier").disabled = champ("supprimer").disabled = !(nom in definitions);
}

// Gestion des profils : formulaire de création et de modification, suppression avec confirmation.
const SITES = Array.from(document.querySelectorAll('input[name="site"]')).map(c => c.value);
const PAR_DEFAUT = {nom: "", termes_recherche: [], lieu: "", rayon_km: 50, sites: ["indeed"], type_contrat: null, nombre_resultats: 15, exiger_termes: true, mots_exclus: [], villes_exclues: []};
let nomModifie = null;
const liste = texte => texte.split(",").map(s => s.trim()).filter(Boolean);

function ouvrirFormulaire(nom) {
  nomModifie = nom;
  const d = nom === null ? PAR_DEFAUT : definitions[nom];
  champ("dlg-titre").textContent = nom === null ? "Nouveau profil" : "Modifier le profil";
  champ("p-nom").value = d.nom;
  champ("p-termes").value = d.termes_recherche.join(", ");
  champ("p-lieu").value = d.lieu;
  champ("p-rayon").value = d.rayon_km;
  for (const c of document.querySelectorAll('input[name="site"]')) c.checked = d.sites.includes(c.value);
  const contrat = champ("p-contrat");
  if (d.type_contrat && !Array.from(contrat.options).some(o => o.value === d.type_contrat)) contrat.add(new Option(d.type_contrat));
  contrat.value = d.type_contrat || "";
  champ("p-nb").value = d.nombre_resultats;
  champ("p-exiger").checked = d.exiger_termes;
  champ("p-exclus").value = d.mots_exclus.join(", ");
  champ("p-villes").value = d.villes_exclues.join(", ");
  champ("p-erreur").textContent = "";
  champ("dlg-profil").showModal();
}

async function envoyer(url, corps) {
  const reponse = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(corps)});
  let donnees = {};
  try { donnees = await reponse.json(); } catch (e) { /* corps absent ou illisible */ }
  return {ok: reponse.ok, donnees: donnees, message: donnees.erreur || ("Refusé (" + reponse.status + ").")};
}

// Liste déroulante de statut de chaque offre : une offre « nouvelle » s'affiche comme « Vue » ; les statuts interdits depuis le statut courant sont désactivés.
const INTERDITS = {postulee: ["a_postuler", "rejetee"], rejetee: ["a_postuler"]};
function actualiserListe(ligne) {
  const statut = ligne.dataset.statut;
  const liste = ligne.querySelector('select[data-action="statut"]');
  liste.value = statut === "nouvelle" ? "vue" : statut;
  for (const option of liste.options) option.disabled = (INTERDITS[statut] || []).includes(option.value);
}
for (const ligne of lignes()) actualiserListe(ligne);

// Change le statut d'une offre : la ligne reste en place, seul son statut change, donc les filtres de la page sont conservés.
async function changerStatut(ligne, url, statut, libelleDe) {
  const resultat = await envoyer(url, {profil: ligne.dataset.profil, cle: ligne.dataset.cle});
  if (!resultat.ok) {
    afficher(resultat.message, "erreur");
    actualiserListe(ligne);
    return;
  }
  ligne.dataset.statut = statut;
  ligne.querySelector(".statut").textContent = ligne.dataset.libelle = libelleDe(resultat);
  const pastille = ligne.querySelector(".pastille");
  if (pastille) pastille.remove();
  actualiserListe(ligne);
  filtrer();
}
// Le serveur renvoie la date du jour (aaaa-mm-jj), affichée jj/mm/aaaa.
const CHANGEMENTS = {
  a_postuler: ligne => changerStatut(ligne, "/offres/a-postuler", "a_postuler", () => "Postuler"),
  postulee: ligne => changerStatut(ligne, "/offres/postuler", "postulee", r => "Postulé le " + r.donnees.postule_le.split("-").reverse().join("/")),
  rejetee: ligne => changerStatut(ligne, "/offres/rejeter", "rejetee", () => "Rejeté"),
  // Une offre rétablie repasse à l'état « vue ».
  vue: ligne => changerStatut(ligne, "/offres/retablir", "vue", () => ""),
};
corps.addEventListener("change", evenement => {
  const liste = evenement.target.closest('select[data-action="statut"]');
  if (liste) CHANGEMENTS[liste.value](liste.closest("tbody.offre"));
});

// Boutons de chaque offre : le titre déplie la ligne de description, « Masquer » la referme.
corps.addEventListener("click", evenement => {
  const bouton = evenement.target.closest("button[data-action]");
  if (!bouton) return;
  const detail = bouton.closest("tbody.offre").querySelector("tr.detail");
  detail.hidden = bouton.dataset.action === "masquer" ? true : !detail.hidden;
});

// Un menu se referme au clic en dehors de lui et après le choix d'une entrée (bouton ou lien).
document.addEventListener("click", evenement => {
  for (const menu of document.querySelectorAll("details.menu[open]")) {
    if (!menu.contains(evenement.target) || evenement.target.closest("button, a")) menu.removeAttribute("open");
  }
});

// Crée le raccourci du menu Démarrer ; le résultat s'affiche dans le bandeau.
champ("raccourci").addEventListener("click", async () => {
  const resultat = await envoyer("/raccourci", {});
  afficher(resultat.ok ? resultat.donnees.message : resultat.message, resultat.ok ? "ok" : "erreur");
});

// Mise à jour de JobSpy : la popup compare la version installée à la dernière publiée ; l'installation se suit dans le bandeau.
let versionCible = null;
champ("jobspy").addEventListener("click", async () => {
  versionCible = null;
  champ("jp-installer").hidden = true;
  champ("jp-texte").textContent = "Recherche de la dernière version…";
  champ("dlg-jobspy").showModal();
  try {
    const reponse = await fetch("/jobspy/version");
    const v = await reponse.json();
    if (!v.derniere) champ("jp-texte").textContent = "Version installée : " + (v.installee || "inconnue") + ". La dernière version publiée n'a pas pu être obtenue (connexion Internet ?).";
    else if (v.derniere === v.installee) champ("jp-texte").textContent = "JobSpy " + v.installee + " est à jour.";
    else {
      versionCible = v.derniere;
      champ("jp-texte").textContent = "Installée : " + (v.installee || "inconnue") + ". Disponible : " + v.derniere + ".";
      champ("jp-installer").textContent = "Installer " + v.derniere;
      champ("jp-installer").hidden = false;
    }
  } catch (erreur) {
    champ("jp-texte").textContent = "Vérification impossible : " + erreur.message;
  }
});
champ("jp-fermer").addEventListener("click", () => champ("dlg-jobspy").close());
champ("jp-installer").addEventListener("click", async () => {
  champ("dlg-jobspy").close();
  enCours = true;
  actualiserBouton();
  afficher("Installation de JobSpy " + versionCible + "…", "attente");
  const resultat = await envoyer("/jobspy/mettre-a-jour", {version: versionCible});
  if (resultat.ok) setTimeout(surveiller, 2000);
  else {
    enCours = false;
    actualiserBouton();
    afficher(resultat.message, "erreur");
  }
});

// Affiche le journal dans une popup, défilée sur ses dernières lignes.
champ("journal").addEventListener("click", async () => {
  const zone = champ("j-texte");
  try {
    const reponse = await fetch("/journal");
    zone.textContent = reponse.ok ? await reponse.text() : "Journal indisponible (HTTP " + reponse.status + ").";
  } catch (erreur) {
    zone.textContent = "Journal indisponible : " + erreur.message;
  }
  champ("dlg-journal").showModal();
  zone.scrollTop = zone.scrollHeight;
});
champ("j-fermer").addEventListener("click", () => champ("dlg-journal").close());

// Recherche auto : réglages enregistrés avec les paramètres globaux, et lancement immédiat du profil recherché le moins récemment.
champ("auto").addEventListener("click", () => {
  champ("a-demarrage").checked = globaux.recherche_auto_demarrage;
  champ("a-delai").value = globaux.delai_recherche_auto_minutes;
  champ("a-date").value = cibleAuto.derniere;
  champ("a-aide").textContent = cibleAuto.profil === null ? "Aucun profil à rechercher." : "Profil visé : « " + cibleAuto.profil + " » (dernière recherche : " + cibleAuto.date + ").";
  champ("a-lancer").disabled = enCours || cibleAuto.profil === null;
  champ("a-erreur").textContent = "";
  champ("dlg-auto").showModal();
});
champ("a-annuler").addEventListener("click", () => champ("dlg-auto").close());
champ("form-auto").addEventListener("submit", async evenement => {
  evenement.preventDefault();
  const date = await envoyer("/recherche/auto/date", {date: champ("a-date").value});
  if (!date.ok) { champ("a-erreur").textContent = date.message; return; }
  const resultat = await envoyer("/global", {...globaux, recherche_auto_demarrage: champ("a-demarrage").checked, delai_recherche_auto_minutes: parseInt(champ("a-delai").value, 10)});
  if (!resultat.ok) { champ("a-erreur").textContent = resultat.message; return; }
  try { sessionStorage.setItem("bilan", "Réglages de la recherche auto enregistrés."); } catch (e) { /* stockage indisponible */ }
  try { sessionStorage.setItem("profil", champ("f-profil").value); } catch (e) { /* stockage indisponible */ }
  location.reload();
});
champ("a-lancer").addEventListener("click", async () => {
  champ("dlg-auto").close();
  enCours = true;
  actualiserBouton();
  afficher("Recherche en cours…", "attente", true);
  const resultat = await envoyer("/recherche/auto/lancer", {});
  if (resultat.ok) setTimeout(surveiller, 2000);
  else {
    enCours = false;
    actualiserBouton();
    afficher(resultat.message, "erreur");
  }
});

// Dernières recherches de tous les profils, dans une popup.
champ("maj-dates").addEventListener("click", () => champ("dlg-maj").showModal());
champ("m-fermer").addEventListener("click", () => champ("dlg-maj").close());

champ("nouveau").addEventListener("click", () => ouvrirFormulaire(null));
champ("modifier").addEventListener("click", () => ouvrirFormulaire(champ("f-profil").value));
champ("p-annuler").addEventListener("click", () => champ("dlg-profil").close());

champ("form-profil").addEventListener("submit", async evenement => {
  evenement.preventDefault();
  const sites = Array.from(document.querySelectorAll('input[name="site"]:checked')).map(c => c.value);
  // Les sites que le formulaire ne propose pas sont conservés tels quels à la modification.
  if (nomModifie !== null) sites.push(...definitions[nomModifie].sites.filter(s => !SITES.includes(s)));
  const profil = {
    nom: champ("p-nom").value.trim(),
    termes_recherche: liste(champ("p-termes").value),
    lieu: champ("p-lieu").value.trim(),
    rayon_km: parseInt(champ("p-rayon").value, 10),
    sites: sites,
    type_contrat: champ("p-contrat").value || null,
    nombre_resultats: parseInt(champ("p-nb").value, 10),
    exiger_termes: champ("p-exiger").checked,
    mots_exclus: liste(champ("p-exclus").value),
    villes_exclues: liste(champ("p-villes").value)
  };
  const resultat = nomModifie === null
    ? await envoyer("/profils/creer", {profil: profil})
    : await envoyer("/profils/modifier", {nom: nomModifie, profil: profil});
  if (resultat.ok) {
    // Après une modification, le bilan de la réévaluation des offres déjà en base s'affiche au rechargement.
    if (resultat.donnees.reintegrees !== undefined) {
      try { sessionStorage.setItem("bilan", "Profil enregistré : " + resultat.donnees.reintegrees + " offre(s) réintégrée(s), " + resultat.donnees.ecartees + " écartée(s) d'après les nouvelles règles du profil."); } catch (e) { /* stockage indisponible */ }
    }
    // Le filtre de profil survit au rechargement : on revient sur le profil créé ou modifié.
    try { sessionStorage.setItem("profil", profil.nom); } catch (e) { /* stockage indisponible */ }
    location.reload();
  }
  else champ("p-erreur").textContent = resultat.message;
});

// Paramètres globaux : exclusions communes à tous les profils, enregistrées dans la base.
champ("globaux").addEventListener("click", () => {
  champ("g-exclus").value = globaux.mots_exclus.join(", ");
  champ("g-villes").value = globaux.villes_exclues.join(", ");
  champ("g-priorite").value = globaux.priorite_sites.join(", ");
  champ("g-erreur").textContent = "";
  champ("dlg-globaux").showModal();
});
champ("g-annuler").addEventListener("click", () => champ("dlg-globaux").close());
champ("form-globaux").addEventListener("submit", async evenement => {
  evenement.preventDefault();
  // Les réglages de la recherche auto (popup dédiée) sont renvoyés tels quels : /global remplace tous les paramètres globaux.
  const resultat = await envoyer("/global", {...globaux, mots_exclus: liste(champ("g-exclus").value), villes_exclues: liste(champ("g-villes").value), priorite_sites: liste(champ("g-priorite").value)});
  if (!resultat.ok) { champ("g-erreur").textContent = resultat.message; return; }
  try { sessionStorage.setItem("bilan", "Paramètres globaux enregistrés : " + resultat.donnees.reintegrees + " offre(s) réintégrée(s), " + resultat.donnees.ecartees + " écartée(s). La priorité des sites s'applique aux prochains nettoyages et recherches."); } catch (e) { /* stockage indisponible */ }
  // Le filtre de profil survit au rechargement.
  try { sessionStorage.setItem("profil", champ("f-profil").value); } catch (e) { /* stockage indisponible */ }
  location.reload();
});

// Suppression d'un profil : confirmation dans une popup.
champ("supprimer").addEventListener("click", () => {
  champ("s-texte").textContent = "Supprimer le profil « " + champ("f-profil").value + " » ?";
  champ("dlg-suppr").showModal();
});
champ("s-annuler").addEventListener("click", () => champ("dlg-suppr").close());
champ("s-confirmer").addEventListener("click", async () => {
  const resultat = await envoyer("/profils/supprimer", {nom: champ("f-profil").value});
  champ("dlg-suppr").close();
  if (resultat.ok) location.reload();
  else afficher(resultat.message, "erreur");
});

// Tri des colonnes : l'ordre initial est « Ajoutée le », du plus ancien au plus récent ; la flèche de l'en-tête indique la colonne et le sens.
let tri = {col: "vue", sens: 1};
function trier(col) {
  tri = {col: col, sens: tri.col === col ? -tri.sens : 1};
  // Les valeurs vides (ex. offre sans statut) restent en fin de liste dans les deux sens.
  const comparer = (a, b) => {
    const x = a.dataset[col], y = b.dataset[col];
    return !x !== !y ? (x ? -1 : 1) : x.localeCompare(y) * tri.sens;
  };
  const triees = lignes().sort(comparer);
  for (const ligne of triees) corps.appendChild(ligne);
  for (const th of document.querySelectorAll("th[data-col]")) {
    th.setAttribute("aria-sort", th.dataset.col === col ? (tri.sens === 1 ? "ascending" : "descending") : "none");
  }
}

for (const id of ["f-texte", "f-societe", "f-lieu", "f-statut", "f-profil"]) champ(id).addEventListener("input", filtrer);
for (const th of document.querySelectorAll("th[data-col]")) {
  th.addEventListener("click", () => trier(th.dataset.col));
  th.addEventListener("keydown", evenement => { if (evenement.key === "Enter" || evenement.key === " ") { evenement.preventDefault(); trier(th.dataset.col); } });
}

// La teinte de la pastille d'une société est dérivée de son nom côté serveur.
for (const logo of document.querySelectorAll(".logo")) logo.style.setProperty("--t", logo.dataset.teinte);

// Texte du bandeau d'une recherche selon sa phase (reprise des descriptions manquantes, puis recherche) ; réaffiché seulement quand il change, pour ne pas écraser « Annulation en cours… ».
let texteRecherche = "Recherche en cours…";
function majPhase(e) {
  if (e.tache === "jobspy") return;
  const texte = e.reprise ? "Reprise des descriptions en cours…" : "Recherche en cours…";
  if (texte !== texteRecherche) { texteRecherche = texte; afficher(texte, "attente", true); }
}

async function surveiller() {
  const reponse = await fetch("/etat");
  const e = await reponse.json();
  if (e.en_cours) {
    majPhase(e);
    setTimeout(surveiller, 2000);
    return;
  }
  enCours = false;
  texteRecherche = "Recherche en cours…";
  actualiserBouton();
  if (e.erreur) {
    afficher("Erreur : " + e.erreur, "erreur");
  } else if (e.annulee) {
    afficher("Recherche annulée : rien n'a été enregistré.", "info");
  } else if (e.message) {
    afficher(e.message, "ok");
  } else if (e.bilan) {
    const bilan = "Recherche terminée : " + e.bilan.nouvelles + " nouvelle(s), " + (e.bilan.retenues - e.bilan.nouvelles) + " déjà vue(s), " + e.bilan.ecartees + " écartée(s).";
    afficher(bilan + " Rechargement…", "ok");
    try { sessionStorage.setItem("bilan", bilan); } catch (e) { /* stockage indisponible */ }
    // Le filtre de profil survit au rechargement : on reste sur le profil recherché.
    try { sessionStorage.setItem("profil", champ("f-profil").value); } catch (e) { /* stockage indisponible */ }
    setTimeout(() => location.reload(), 1500);
  }
}

champ("maj").addEventListener("click", async () => {
  enCours = true;
  actualiserBouton();
  afficher("Recherche en cours…", "attente", true);
  const reponse = await fetch("/mettre-a-jour", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({profil: champ("f-profil").value})
  });
  if (reponse.status === 202 || reponse.status === 409) {
    setTimeout(surveiller, 2000);
  } else {
    enCours = false;
    actualiserBouton();
    afficher("Recherche refusée ("+ reponse.status + ").", "erreur");
  }
});

restaurerFiltres();
try {
  const profilConserve = sessionStorage.getItem("profil");
  if (profilConserve !== null) {
    sessionStorage.removeItem("profil");
    if (Array.from(champ("f-profil").options).some(o => o.value === profilConserve)) { champ("f-profil").value = profilConserve; }
  }
} catch (e) { /* stockage indisponible */ }
filtrer();
try {
  const bilan = sessionStorage.getItem("bilan");
  if (bilan) { sessionStorage.removeItem("bilan"); afficher(bilan, "ok"); }
} catch (e) { /* stockage indisponible */ }
fetch("/etat").then(r => r.json()).then(e => { if (e.en_cours) { enCours = true; actualiserBouton(); texteRecherche = e.reprise ? "Reprise des descriptions en cours…" : "Recherche en cours…"; afficher(e.tache === "jobspy" ? "Installation de JobSpy en cours…" : texteRecherche, "attente", e.tache !== "jobspy"); surveiller(); } });
</script>
</body>
</html>
"""


def _lien(url: str, classe: str = "") -> str:
    # Seuls les schémas http et https deviennent cliquables ; tout le reste est affiché en texte.
    if url.lower().startswith(("http://", "https://")):
        attribut_classe = f' class="{classe}"' if classe else ""
        return f'<a{attribut_classe} href="{escape(url)}" target="_blank" rel="noopener noreferrer">Voir l\'annonce</a>'
    return escape(url)


_TITRE = re.compile(r"^#{1,6}\s+(.*)$")
_PUCE = re.compile(r"^[*-]\s+(.*)$")
_GRAS = re.compile(r"\*\*(.+?)\*\*")
_ECHAPPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!])")


def _markdown(texte: str) -> str:
    """Convertit en HTML le Markdown minimal des descriptions (titres, gras, puces, paragraphes) ; le texte est échappé avant toute balise."""
    blocs: list[str] = []
    paragraphe: list[str] = []
    puces: list[str] = []

    def vider() -> None:
        if paragraphe:
            blocs.append("<p>" + "<br>".join(paragraphe) + "</p>")
            paragraphe.clear()
        if puces:
            blocs.append("<ul>" + "".join(f"<li>{puce}</li>" for puce in puces) + "</ul>")
            puces.clear()

    for ligne in _ECHAPPE.sub(r"\1", escape(texte)).splitlines():
        ligne = _GRAS.sub(r"<strong>\1</strong>", ligne.strip())
        titre, puce = _TITRE.match(ligne), _PUCE.match(ligne)
        if not ligne:
            vider()
        elif titre:
            vider()
            blocs.append(f"<h4>{titre.group(1)}</h4>")
        elif puce:
            if paragraphe:
                vider()
            puces.append(puce.group(1))
        else:
            if puces:
                vider()
            paragraphe.append(ligne)
    vider()
    return "".join(blocs)


_NB_COLONNES = 8 # colonnes du tableau, largeur de la ligne de description


def _libelle_statut(stockee: OffreStockee) -> str:
    """Texte de la colonne Statut : « Rejeté », « Postuler », « Postulé le jj/mm/aaaa », vide pour les autres offres."""
    if stockee.statut == REJETEE:
        return "Rejeté"
    if stockee.statut == A_POSTULER:
        return "Postuler"
    if stockee.statut == POSTULEE:
        try:
            return "Postulé le " + date.fromisoformat(stockee.postule_le).strftime("%d/%m/%Y")
        except (TypeError, ValueError):
            return "Postulé"
    return ""


def _ligne(stockee: OffreStockee) -> str:
    offre = stockee.offre
    try:
        premiere = datetime.fromisoformat(stockee.premiere_vue).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        premiere = stockee.premiere_vue
    pastille = ' <span class="pastille">nouvelle</span>' if stockee.statut == NOUVELLE else ""
    texte = f"{offre.titre} {offre.societe} {offre.lieu} {offre.description}".lower()
    libelle = _libelle_statut(stockee)
    donnees = {
        "statut": stockee.statut,
        "libelle": libelle,
        "profil": stockee.profil,
        "site": offre.source.lower(),
        "cle": stockee.cle,
        "texte": texte,
        "titre": offre.titre.lower(),
        "societe": offre.societe.lower(),
        "lieu": offre.lieu.lower(),
        "vue": stockee.premiere_vue,
    }
    attributs = " ".join(f'data-{cle}="{escape(valeur)}"' for cle, valeur in donnees.items())
    # Pastille ronde de la société : initiale et teinte (0 à 359) dérivées du nom, sans ressource externe.
    initiale = next((c for c in offre.societe if c.isalnum()), "?").upper()
    teinte = sum(ord(c) for c in offre.societe.lower()) * 37 % 360
    logo = f'<span class="logo" data-teinte="{teinte}" aria-hidden="true">{escape(initiale)}</span>'
    return (
        f'<tbody class="offre" {attributs}><tr>'
        f'<td class="titre"><button type="button" class="titre-lien" data-action="voir">{escape(offre.titre)}</button>{pastille}</td>'
        f"<td>{escape(stockee.profil)}</td><td>{escape(offre.source)}</td><td>{logo}{escape(offre.societe)}</td><td>{escape(offre.lieu)}</td>"
        f'<td>{escape(premiere)}</td><td><span class="statut">{escape(libelle)}</span></td><td>{_lien(offre.url)}</td></tr>'
        f'<tr class="detail" hidden><td class="description" colspan="{_NB_COLONNES}"><div class="panneau">{_markdown(offre.description)}'
        f'<div class="actions">{_lien(offre.url, "bouton-lien")}<button type="button" data-action="masquer">Masquer</button>'
        '<label>Statut <select data-action="statut" aria-label="Statut de l\'offre">'
        '<option value="vue">Vue</option><option value="a_postuler">Postuler</option>'
        '<option value="postulee">Postulé</option><option value="rejetee">Rejeté</option></select></label></div></div></td></tr></tbody>'
    )


def _json_pour_script(donnees: object) -> str:
    """JSON destiné à une balise <script> de la page : « < » est échappé pour qu'un profil ne puisse pas refermer la balise qui porte ses données."""
    return json.dumps(donnees, ensure_ascii=False).replace("<", "\\u003c")


def _date_lisible(iso: str | None) -> str:
    """Date ISO de la base au format jj/mm/aaaa hh:mm (heure locale du poste), « jamais » si absente ou illisible."""
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M") if iso else "jamais"
    except ValueError:
        return "jamais"


_TAILLE_MAX_CORPS = 16384  # octets, corps JSON des routes de gestion des profils
_ACTION_CREER = "creer"  # fin du chemin /profils/<action> ; « supprimer » est l'action par défaut de _gerer_profil
_ACTION_MODIFIER = "modifier"
_TACHE_RECHERCHE = "recherche"  # valeurs de `tache` renvoyées par /etat et lues par la page (e.tache === "jobspy")
_TACHE_JOBSPY = "jobspy"

_MARQUEUR = re.compile(r"__(OPTIONS|DATES|MAJS|AUTO|PROFILS|GLOBAUX|LIGNES|RACCOURCI|JOBSPY)__")

_RACCOURCI_INDISPONIBLE = ' disabled title="Disponible seulement quand l\'application est ouverte par son lanceur (Alertes emploi.pyw)."'
_JOBSPY_INDISPONIBLE = ' disabled title="Disponible seulement quand l\'application est ouverte par son lanceur (Alertes emploi.pyw). Depuis le dépôt, utiliser scripts/monter-version-jobspy.ps1."'


def construire_page(
    offres: list[OffreStockee],
    profils: list[str],
    dates: dict[str, str] | None = None,
    definitions: dict[str, Profil] | None = None,
    globaux: ParametresGlobaux = ParametresGlobaux(),
    raccourci_disponible: bool = False,
    jobspy_disponible: bool = False,
    derniere_recherche_auto: datetime | None = None,
) -> str:
    """Page HTML complète des offres retenues de tous les profils, tout texte venant de la base étant échappé.

    profils : noms proposés dans la liste déroulante ;
    dates : date ISO de dernière mise à jour par profil (un profil absent s'affiche « jamais ») ;
    definitions : profils enregistrés en base, les seuls modifiables ou supprimables depuis la page ;
    globaux : paramètres globaux, préremplis dans le formulaire « Paramètres globaux » ;
    raccourci_disponible : faux si l'application n'a pas été ouverte par son lanceur, l'entrée de menu du raccourci est alors grisée ;
    jobspy_disponible : faux hors du build, l'entrée de menu « Mettre à jour JobSpy » est alors grisée ;
    derniere_recherche_auto : date de la dernière recherche réussie, préremplie dans la popup « Recherche auto » (champ vide si absente).
    """
    dates = dates or {}
    donnees_globaux = _json_pour_script(asdict(globaux))
    donnees_profils = _json_pour_script({nom: asdict(p) for nom, p in (definitions or {}).items()})
    # Date de mise à jour de chaque profil, affichée à côté du bouton « Rechercher ».
    donnees_majs = _json_pour_script({nom: _date_lisible(dates.get(nom)) for nom in profils})
    # Profil visé par la recherche auto (le plus ancien des profils enregistrés) et sa dernière recherche, pour l'aide de la popup « Recherche auto ».
    cible = profil_le_plus_ancien(list(definitions or {}), dates, datetime.now(), ignorer_delai=True)
    derniere = derniere_recherche_auto.isoformat(timespec="minutes") if derniere_recherche_auto else ""  # format du champ datetime-local
    donnees_auto = _json_pour_script({"profil": cible, "date": _date_lisible(dates.get(cible)) if cible else None, "derniere": derniere})
    options = "".join(f'<option value="{escape(nom)}">{escape(nom)}</option>' for nom in profils)
    lignes_dates = (
        '<table class="maj"><thead><tr><th>Profil</th><th>Date</th></tr></thead><tbody>'
        + "".join(
            f'<tr><td>{escape(nom)}</td><td>{_date_lisible(dates.get(nom))}</td></tr>'
            for nom in sorted(profils, key=lambda nom: dates.get(nom) or "")
        )
        + "</tbody></table>"
    )
    valeurs = {
        "OPTIONS": options,
        "DATES": lignes_dates,
        "MAJS": donnees_majs,
        "AUTO": donnees_auto,
        "PROFILS": donnees_profils,
        "GLOBAUX": donnees_globaux,
        "LIGNES": "\n".join(_ligne(o) for o in offres),
        "RACCOURCI": "" if raccourci_disponible else _RACCOURCI_INDISPONIBLE,
        "JOBSPY": "" if jobspy_disponible else _JOBSPY_INDISPONIBLE,
    }
    # Une seule passe : un texte inséré (offre, profil) contenant un marqueur ne doit jamais être réécrit.
    return _MARQUEUR.sub(lambda m: valeurs[m.group(1)], _PAGE)


class _Etat:
    """État de la mise à jour en arrière-plan, partagé entre les requêtes sous verrou."""

    def __init__(self) -> None:
        self._verrou = threading.Lock()
        self._en_cours = False
        self._bilan: dict[str, int] | None = None
        self._erreur: str | None = None
        self._profil: str | None = None
        self._tache = _TACHE_RECHERCHE
        self._message: str | None = None
        self._annulation = False
        self._annulee = False
        self._reprise = False

    def demarrer(self, profil: str | None = None, tache: str = _TACHE_RECHERCHE) -> bool:
        """Prend le verrou commun aux recherches et à la mise à jour de JobSpy ; faux si l'une des deux tourne déjà."""
        with self._verrou:
            if self._en_cours:
                return False
            self._en_cours, self._bilan, self._erreur, self._profil = True, None, None, profil
            self._tache, self._message = tache, None
            self._annulation = self._annulee = self._reprise = False
            return True

    def definir_reprise(self, en_cours: bool) -> None:
        """Note si la recherche en cours est dans sa phase de reprise des descriptions manquantes (lu par la page via /etat)."""
        with self._verrou:
            self._reprise = en_cours

    def terminer(self, bilan: dict[str, int] | None, erreur: str | None, message: str | None = None, annulee: bool = False) -> None:
        with self._verrou:
            self._en_cours, self._bilan, self._erreur, self._profil, self._message = False, bilan, erreur, None, message
            self._annulee, self._reprise = annulee, False

    def demander_annulation(self) -> bool:
        """Demande l'arrêt de la recherche en cours (pris en compte avant le prochain terme) ; faux s'il n'y en a pas, l'installation de JobSpy n'étant pas annulable."""
        with self._verrou:
            if not self._en_cours or self._tache != _TACHE_RECHERCHE:
                return False
            self._annulation = True
            return True

    def annulation_demandee(self) -> bool:
        with self._verrou:
            return self._annulation

    def profil_en_cours(self) -> str | None:
        """Nom du profil dont la recherche tourne, None sinon."""
        with self._verrou:
            return self._profil if self._en_cours else None

    def instantane(self) -> dict:
        with self._verrou:
            return {"en_cours": self._en_cours, "bilan": self._bilan, "erreur": self._erreur, "tache": self._tache, "message": self._message, "annulee": self._annulee, "reprise": self._reprise}


def _mettre_a_jour_jobspy(cible: str, jobspy: Jobspy, etat: _Etat) -> None:
    """Installe la version `cible` de JobSpy ; le résultat (message ou erreur) est lu par la page via /etat."""
    try:
        message = jobspy.mettre_a_jour(cible)
    except ErreurMiseAJour as erreur:
        etat.terminer(None, str(erreur))
        return
    except Exception as exception:  # le thread ne doit jamais propager : l'erreur est affichée dans la page
        etat.terminer(None, f"{type(exception).__name__} : {exception}")
        return
    etat.terminer(None, None, message)


def _mettre_a_jour(profil: Profil, base: Path, etat: _Etat, globaux: ParametresGlobaux = ParametresGlobaux(), auto: bool = False) -> None:
    """Lance la recherche d'un profil et, si elle réussit, note la date de mise à jour du profil et la date de dernière recherche ; une annulation ou une erreur n'écrit aucune date.

    auto : recherche automatique (démarrage ou « Lancer maintenant »), seule à inscrire sa fin, son annulation ou son erreur dans le journal.
    """
    try:
        resultat = lancer_recherche(profil, base, globaux=globaux, annulee=etat.annulation_demandee, reprise_en_cours=etat.definir_reprise)
        with Stockage(base) as stockage:
            stockage.noter_mise_a_jour(profil.nom)
            stockage.noter_recherche_auto()
    except RechercheAnnulee:  # annulée par l'utilisateur : rien n'a été écrit, pas de date de mise à jour
        if auto:
            _journal.info("Recherche auto annulée : profil « %s ».", profil.nom)
        etat.terminer(None, None, annulee=True)
        return
    except RechercheBloquee as exception:  # blocage 429 : rien n'a été écrit, le message dit déjà la raison et la conséquence
        if auto:
            _journal.error("Recherche auto en erreur : profil « %s » : %s", profil.nom, exception)
        etat.terminer(None, f"{profil.nom} : {exception}")
        return
    except Exception as exception:  # le thread ne doit jamais propager : l'erreur est affichée dans la page
        if auto:
            _journal.error("Recherche auto en erreur : profil « %s » : %s : %s", profil.nom, type(exception).__name__, exception)
        etat.terminer(None, f"{profil.nom} : {type(exception).__name__} : {exception}")
        return
    if auto:
        _journal.info("Recherche auto terminée : profil « %s », %d nouvelle(s) offre(s).", profil.nom, len(resultat.nouvelles))
    etat.terminer({"nouvelles": len(resultat.nouvelles), "retenues": resultat.nb_retenues, "ecartees": resultat.nb_ecartees}, None)


def _demarrer_recherche(profil: Profil, base: Path, etat: _Etat, globaux: ParametresGlobaux, auto: bool = False) -> bool:
    """Marque l'état « en cours » puis lance la recherche du profil dans un thread ; faux, sans rien lancer, si une recherche ou une installation est déjà en cours."""
    if not etat.demarrer(profil.nom):
        return False
    threading.Thread(target=_mettre_a_jour, args=(profil, base, etat, globaux, auto), daemon=True).start()
    return True


_SEUIL_MISE_A_JOUR = timedelta(hours=1)


def profil_le_plus_ancien(noms: list[str], dates: dict[str, str], maintenant: datetime, delai: timedelta = _SEUIL_MISE_A_JOUR, ignorer_delai: bool = False) -> str | None:
    """Nom du profil visé par la recherche auto : le plus ancien (jamais recherché ou date illisible en premier, puis ordre alphabétique), seulement si aucune recherche (automatique ou à la main, y compris d'un profil disparu) n'a eu lieu depuis moins de `delai` ; `ignorer_delai` désigne le profil sans tenir compte de ce délai."""

    def date_de(nom: str) -> datetime:
        try:
            return datetime.fromisoformat(dates[nom])
        except (KeyError, ValueError, TypeError):
            return datetime.min

    if not noms:
        return None
    if not ignorer_delai and max(date_de(n) for n in {*noms, *dates}) >= maintenant - delai:
        return None
    _, nom = min((date_de(n), n) for n in noms)
    return nom


_LIGNES_JOURNAL = 200


def _dernieres_lignes(fichier: Path, nombre: int) -> str:
    """Les `nombre` dernières lignes du fichier, texte vide s'il n'existe pas encore."""
    if not fichier.exists():
        return ""
    lignes = fichier.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lignes[-nombre:])


class _Gestionnaire(BaseHTTPRequestHandler):
    """Routes HTTP de l'application ; base, etat, jobspy et lanceur sont posés par _fabrique_gestionnaire."""

    base: Path
    etat: _Etat
    jobspy: Jobspy
    lanceur: Path | None

    def _repondre(self, code: int, contenu: bytes = b"", type_contenu: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", type_contenu)
        self.send_header("Content-Length", str(len(contenu)))
        self.end_headers()
        self.wfile.write(contenu)

    def _profil_demande(self, profils: dict[str, Profil]) -> Profil | None:
        """Profil à relancer d'après le corps JSON {"profil": nom}. None si le nom est vide, absent ou n'est pas un profil chargé.

        Le nom n'est jamais utilisé comme chemin : il sert seulement de clé dans les profils déjà chargés.
        """
        try:
            longueur = int(self.headers.get("Content-Length") or 0)
            if longueur <= 0:
                # Un Content-Length négatif ferait lire rfile jusqu'à la fermeture de la connexion, sans borne.
                return None
            corps = json.loads(self.rfile.read(min(longueur, 4096)))
            nom = corps.get("profil") if isinstance(corps, dict) else None
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(nom, str) and nom in profils:
            return profils[nom]
        return None

    def do_GET(self) -> None:
        if self.path == "/":
            with Stockage(self.base) as stockage:
                profils = stockage.lister_profils()
                globaux = stockage.charger_globaux()
                offres = stockage.lister_detail()
                dates = stockage.dernieres_mises_a_jour()
                derniere = stockage.derniere_recherche_auto()
            noms = sorted(set(profils) | {o.profil for o in offres})
            page = construire_page(offres, noms, dates, profils, globaux, self.lanceur is not None, self.jobspy.disponible, derniere)
            self._repondre(200, page.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/jobspy/version":
            # Ce GET lance pip en sous-processus : même contrôle d'origine que les POST.
            if not self._origine_valide():
                self._repondre(403)
                return
            installee, derniere = self.jobspy.versions()
            self._repondre_json(200, {"disponible": self.jobspy.disponible, "installee": installee, "derniere": derniere})
        elif self.path == "/etat":
            self._repondre(200, json.dumps(self.etat.instantane()).encode("utf-8"), "application/json; charset=utf-8")
        elif self.path == "/journal":
            self._repondre(200, _dernieres_lignes(self.base.parent / "journal.log", _LIGNES_JOURNAL).encode("utf-8"))
        else:
            self._repondre(404)

    def _origine_valide(self) -> bool:
        port_serveur = self.server.server_address[1]
        origine = self.headers.get("Origin")
        return origine is None or origine in (f"http://127.0.0.1:{port_serveur}", f"http://localhost:{port_serveur}")

    def _repondre_json(self, code: int, contenu: dict) -> None:
        self._repondre(code, json.dumps(contenu, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _corps_json(self) -> dict | None:
        """Corps JSON de la requête (objet, 16 Ko au plus), None s'il est trop gros, illisible ou n'est pas un objet."""
        try:
            longueur = int(self.headers.get("Content-Length") or 0)
            if not 0 < longueur <= _TAILLE_MAX_CORPS:
                return None
            corps = json.loads(self.rfile.read(longueur))
        except (ValueError, UnicodeDecodeError):
            return None
        return corps if isinstance(corps, dict) else None

    def _gerer_profil(self) -> None:
        """Routes /profils/creer, /profils/modifier et /profils/supprimer ; le profil visé est retrouvé par son nom parmi les profils de la base, jamais utilisé comme chemin."""
        corps = self._corps_json()
        if corps is None:
            self._repondre_json(400, {"erreur": "Requête illisible ou trop volumineuse."})
            return
        action = self.path.rsplit("/", 1)[1]
        try:
            with Stockage(self.base) as stockage:
                if action == _ACTION_CREER:
                    stockage.creer_profil(corps.get("profil"))
                    self._repondre_json(201, {"ok": True})
                    return
                nom = corps.get("nom")
                if not isinstance(nom, str) or nom not in stockage.lister_profils():
                    self._repondre_json(400, {"erreur": "Profil inconnu."})
                    return
                if self.etat.profil_en_cours() == nom:
                    self._repondre_json(409, {"erreur": "Une recherche de ce profil est en cours : réessayez à sa fin."})
                    return
                if action == _ACTION_MODIFIER:
                    profil = stockage.modifier_profil(nom, corps.get("profil"))
                    # Les règles ont pu changer : les offres déjà en base sont réévaluées tout de suite, sans appel réseau.
                    reintegrees, ecartees = stockage.reevaluer(profil, stockage.charger_globaux())
                    self._repondre_json(200, {"ok": True, "reintegrees": reintegrees, "ecartees": ecartees})
                    return
                offres = stockage.supprimer_profil(nom)
                self._repondre_json(200, {"ok": True, "offres_effacees": offres})
        except ProfilInvalide as erreur:
            self._repondre_json(400, {"erreur": str(erreur)})

    def _gerer_globaux(self) -> None:
        """Route /global : enregistre les paramètres globaux puis réévalue les offres de tous les profils, sans appel réseau."""
        corps = self._corps_json()
        if corps is None:
            self._repondre_json(400, {"erreur": "Requête illisible ou trop volumineuse."})
            return
        if self.etat.instantane()["en_cours"]:
            self._repondre_json(409, {"erreur": "Une recherche est en cours : réessayez à sa fin."})
            return
        try:
            reintegrees = ecartees = 0
            with Stockage(self.base) as stockage:
                stockage.enregistrer_globaux(corps)
                globaux = stockage.charger_globaux()
                for profil in stockage.lister_profils().values():
                    r, e = stockage.reevaluer(profil, globaux)
                    reintegrees += r
                    ecartees += e
        except ProfilInvalide as erreur:
            self._repondre_json(400, {"erreur": str(erreur)})
            return
        self._repondre_json(200, {"ok": True, "reintegrees": reintegrees, "ecartees": ecartees})

    def _changer_statut_offre(self, appliquer, complement: dict | None = None) -> None:
        """Corps commun des routes /offres/* : l'offre visée est retrouvée par son profil et sa clé, jamais utilisés comme chemin.

        `appliquer(stockage, profil, cle)` change le statut et renvoie vrai si l'offre existe ; `complement` s'ajoute à la réponse 200.
        """
        corps = self._corps_json() or {}
        profil, cle = corps.get("profil"), corps.get("cle")
        if not isinstance(profil, str) or not isinstance(cle, str):
            self._repondre_json(400, {"erreur": "Requête illisible ou incomplète."})
            return
        with Stockage(self.base) as stockage:
            trouvee = appliquer(stockage, profil, cle)
        if trouvee:
            self._repondre_json(200, {"ok": True, **(complement or {})})
        else:
            self._repondre_json(404, {"erreur": "Offre inconnue."})

    def _rejeter_offre(self) -> None:
        """Route /offres/rejeter."""
        self._changer_statut_offre(Stockage.rejeter)

    def _postuler_offre(self) -> None:
        """Route /offres/postuler : renvoie la date enregistrée (aaaa-mm-jj)."""
        jour = date.today()
        self._changer_statut_offre(lambda stockage, profil, cle: stockage.postuler(profil, cle, jour), {"postule_le": jour.isoformat()})

    def _a_postuler_offre(self) -> None:
        """Route /offres/a-postuler : marque l'offre « à postuler »."""
        self._changer_statut_offre(Stockage.a_postuler)

    def _retablir_offre(self) -> None:
        """Route /offres/retablir : annule un rejet ou un « postulé »."""
        self._changer_statut_offre(Stockage.retablir)

    def _creer_raccourci(self) -> None:
        """Route /raccourci : crée le raccourci du menu Démarrer vers le lanceur donné au serveur (aucun chemin ne vient de la requête)."""
        if self.lanceur is None:
            self._repondre_json(409, {"erreur": "Raccourci indisponible : ouvrez l'application par son lanceur (Alertes emploi.pyw)."})
            return
        try:
            message = raccourci.creer(self.lanceur)
        except raccourci.RaccourciErreur as erreur:
            self._repondre_json(500, {"erreur": str(erreur)})
            return
        self._repondre_json(200, {"ok": True, "message": message})

    def _installer_jobspy(self) -> None:
        """Route /jobspy/mettre-a-jour : installe en arrière-plan la version reçue ({"version": "x.y.z"}, chiffres et points seulement) ; aucun chemin ni nom de paquet ne vient de la requête."""
        if not self.jobspy.disponible:
            self._repondre_json(409, {"erreur": "Mise à jour indisponible : ouvrez l'application par son lanceur (Alertes emploi.pyw)."})
            return
        version = (self._corps_json() or {}).get("version")
        if not version_valide(version):
            self._repondre_json(400, {"erreur": "Version invalide."})
            return
        if not self.etat.demarrer(tache=_TACHE_JOBSPY):
            self._repondre_json(409, {"erreur": "Une recherche ou une installation est en cours : réessayez à sa fin."})
            return
        threading.Thread(target=_mettre_a_jour_jobspy, args=(version, self.jobspy, self.etat), daemon=True).start()
        self._repondre_json(202, {"ok": True})

    def _lancer_recherche_auto(self) -> None:
        """Route /recherche/auto/lancer : lance maintenant la recherche du profil le plus ancien, sans tenir compte du délai ; aucun nom ne vient de la requête."""
        with Stockage(self.base) as stockage:
            profils = stockage.lister_profils()
            dates = stockage.dernieres_mises_a_jour()
            globaux = stockage.charger_globaux()
        nom = profil_le_plus_ancien(list(profils), dates, datetime.now(), ignorer_delai=True)
        if nom is None:
            self._repondre_json(400, {"erreur": "Aucun profil à rechercher."})
            return
        if not _demarrer_recherche(profils[nom], self.base, self.etat, globaux, auto=True):
            self._repondre_json(409, {"erreur": "Une recherche ou une installation est en cours : réessayez à sa fin."})
            return
        self._repondre_json(202, {"ok": True, "profil": nom})

    def _noter_date_recherche_auto(self) -> None:
        """Route /recherche/auto/date : enregistre la date de dernière recherche reçue ({"date": "aaaa-mm-jjThh:mm"}), ou l'efface si elle est vide."""
        corps = self._corps_json()
        texte = corps.get("date") if corps is not None else None
        if not isinstance(texte, str):
            self._repondre_json(400, {"erreur": "Requête illisible."})
            return
        try:
            nouvelle = datetime.fromisoformat(texte) if texte else None
        except ValueError:
            self._repondre_json(400, {"erreur": "Date illisible."})
            return
        with Stockage(self.base) as stockage:
            if nouvelle is None:
                stockage.effacer_recherche_auto()
            else:
                stockage.noter_recherche_auto(date=nouvelle)
        self._repondre_json(200, {"ok": True})

    def _annuler_recherche(self) -> None:
        """Route /recherche/annuler : 202 si une recherche en cours a reçu la demande d'annulation, 409 sinon."""
        self._repondre(202 if self.etat.demander_annulation() else 409)

    # Liste blanche des routes POST et table de dispatch à la fois : un chemin absent répond 404.
    _ROUTES_POST = {
        "/recherche/auto/date": "_noter_date_recherche_auto",
        "/recherche/auto/lancer": "_lancer_recherche_auto",
        "/mettre-a-jour": "_lancer_mise_a_jour",
        "/offres/rejeter": "_rejeter_offre",
        "/offres/postuler": "_postuler_offre",
        "/offres/a-postuler": "_a_postuler_offre",
        "/offres/retablir": "_retablir_offre",
        f"/profils/{_ACTION_CREER}": "_gerer_profil",
        f"/profils/{_ACTION_MODIFIER}": "_gerer_profil",
        "/profils/supprimer": "_gerer_profil",
        "/global": "_gerer_globaux",
        "/raccourci": "_creer_raccourci",
        "/jobspy/mettre-a-jour": "_installer_jobspy",
        "/recherche/annuler": "_annuler_recherche",
    }

    def do_POST(self) -> None:
        methode = self._ROUTES_POST.get(self.path)
        if methode is None:
            self._repondre(404)
            return
        if not self._origine_valide():
            self._repondre(403)
            return
        getattr(self, methode)()

    def _lancer_mise_a_jour(self) -> None:
        """Route /mettre-a-jour : relance la recherche du profil nommé dans le corps JSON."""
        with Stockage(self.base) as stockage:
            profils = stockage.lister_profils()
            globaux = stockage.charger_globaux()
        choisi = self._profil_demande(profils)
        if choisi is None:
            self._repondre(400)
            return
        if not _demarrer_recherche(choisi, self.base, self.etat, globaux):
            self._repondre(409)
            return
        self._repondre(202)

    def log_message(self, format: str, *args) -> None:
        pass


def _fabrique_gestionnaire(base: Path, etat: _Etat, jobspy: Jobspy, lanceur: Path | None) -> type[_Gestionnaire]:
    """Sous-classe de _Gestionnaire liée à la base, à l'état partagé, à JobSpy et au lanceur : ThreadingHTTPServer instancie la classe sans argument."""
    return type("Gestionnaire", (_Gestionnaire,), {"base": base, "etat": etat, "jobspy": jobspy, "lanceur": lanceur})


def _lancer_recherche_au_demarrage(base: Path, etat: _Etat) -> None:
    """Lance en arrière-plan la recherche du profil le plus ancien si les réglages l'autorisent et si la dernière recherche réussie est échue."""
    with Stockage(base) as stockage:
        profils = stockage.lister_profils()
        dates = stockage.dernieres_mises_a_jour()
        globaux = stockage.charger_globaux()
        derniere = stockage.derniere_recherche_auto()
    delai = timedelta(minutes=globaux.delai_recherche_auto_minutes)
    # La recherche se lance si la dernière recherche réussie est absente ou plus ancienne que le délai ; les dates des profils ne servent qu'à choisir le profil visé.
    echue = derniere is None or derniere < datetime.now() - delai
    nom = profil_le_plus_ancien(list(profils), dates, datetime.now(), ignorer_delai=True) if globaux.recherche_auto_demarrage and echue else None
    if nom is not None:
        _demarrer_recherche(profils[nom], base, etat, globaux, auto=True)


def creer_serveur(base: Path, port: int = 8765, lanceur: Path | None = None, mise_a_jour_au_demarrage: bool = False, jobspy: Jobspy | None = None) -> ThreadingHTTPServer:
    """Serveur lié à 127.0.0.1 sur les profils et les offres de la base (relus à chaque requête) ; le port 0 laisse le système en choisir un libre.

    lanceur : « Alertes emploi.pyw », cible du raccourci du menu Démarrer ; None quand l'application n'a pas été ouverte par lui.
    mise_a_jour_au_demarrage : lance en arrière-plan la recherche du profil le plus ancien, sauf si les réglages globaux la désactivent ou si la dernière recherche réussie date de moins du délai réglé (une heure par défaut) ; sans date enregistrée, elle se lance.
    jobspy : mise à jour de python-jobspy ; détectée d'après l'emplacement du code (disponible seulement depuis le build) quand elle n'est pas fournie.
    """
    jobspy = jobspy or Jobspy.detecter()
    etat = _Etat()
    if mise_a_jour_au_demarrage:
        _lancer_recherche_au_demarrage(base, etat)
    return ThreadingHTTPServer(("127.0.0.1", port), _fabrique_gestionnaire(base, etat, jobspy, lanceur))


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Sert en local la page de consultation des offres de tous les profils.")
    analyseur.add_argument("--base", type=Path, default=BASE_PAR_DEFAUT, help="Base SQLite des profils et de l'historique des offres.")
    analyseur.add_argument("--port", type=int, default=8765, help="Port d'écoute sur 127.0.0.1.")
    args = analyseur.parse_args(argv)
    with Stockage(args.base) as stockage:
        for message in stockage.importer_profils_json(args.base.parent / DOSSIER_PROFILS):
            print(f"Import ignoré : {message}", file=sys.stderr)
    serveur = creer_serveur(args.base, args.port)
    print(f"Consultation des profils de {args.base} sur http://127.0.0.1:{serveur.server_address[1]}/ (Ctrl+C pour arrêter).")
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
