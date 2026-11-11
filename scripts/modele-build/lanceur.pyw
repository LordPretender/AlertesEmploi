"""Lanceur de l'application : prépare l'environnement au premier lancement, puis ouvre la fenêtre.

Windows : double-clic (le .pyw n'ouvre pas de console). Linux : python3 "Alertes emploi.pyw".
"""
import os
import subprocess
import sys
from pathlib import Path

racine = Path(__file__).resolve().parent
systeme = racine / "systeme"
sys.path.insert(0, str(systeme))

import preparer

if not preparer.a_jour(systeme):
    # pythonw n'a pas de console : la préparation tourne avec python.exe dans sa propre console
    interprete = Path(sys.executable)
    if interprete.name.lower() == "pythonw.exe":
        interprete = interprete.with_name("python.exe")
    drapeaux = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    subprocess.call([str(interprete), str(systeme / "preparer.py")], creationflags=drapeaux)
    if not preparer.a_jour(systeme):
        sys.exit(1)

environnement = dict(os.environ, PYTHONPATH=str(systeme / "src"))
commande = [str(preparer.python_du_venv(sans_console=True)), "-m", "alertes_emploi.interface.fenetre", "--donnees", str(racine / "donnees"), "--lanceur", str(Path(__file__).resolve())]
if os.name == "nt":
    subprocess.Popen(commande, env=environnement)
else:
    subprocess.Popen(commande, env=environnement, start_new_session=True)
