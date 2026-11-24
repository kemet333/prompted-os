#!/bin/zsh
# Solo si usas Hermes (opcional): entorno de Python propio del motor del equipo (~/Equipo/venv), separado de Hermes.
# Motivo: el venv antiguo de Hermes trae SQLite 3.50.4, con el fallo de corrupción del WAL, y el motor abre la misma
# base de tableros que Hermes. Copia el Python que gestiona Hermes (SQLite al día) a ~/Equipo/python, crea el venv e
# instala las dependencias que fija hermes-agent/pyproject.toml. Volver a lanzarlo tras un `hermes update` si el motor
# deja de arrancar por un import.
set -euo pipefail
src=$(ls -d $HOME/.hermes/tools/python-3.* 2>/dev/null | sort -V | tail -1)
[[ -n "$src" ]] || { echo "No encuentro el Python de Hermes en ~/.hermes/tools"; exit 1; }
dest=$HOME/Equipo/python; venv=$HOME/Equipo/venv
rm -rf "$dest.nuevo" && cp -R "$src" "$dest.nuevo"
"$dest.nuevo/bin/python3" -c 'import sqlite3, sys; v = sqlite3.sqlite_version_info; sys.exit(0 if v >= (3, 51, 3) else 1)' \
  || { echo "El Python de Hermes también trae un SQLite antiguo"; rm -rf "$dest.nuevo"; exit 1; }
rm -rf "$dest" && mv "$dest.nuevo" "$dest"
# El venv nuevo se prepara aparte y se cambia de golpe al final: el motor puede estar trabajando mientras tanto.
rm -rf "$venv.nuevo" && "$dest/bin/python3" -m venv "$venv.nuevo"
$HOME/.local/bin/uv pip install --quiet --python "$venv.nuevo/bin/python" -r $HOME/.hermes/hermes-agent/pyproject.toml
"$venv.nuevo/bin/python" -c "import sys; sys.path.insert(0, '$HOME/.hermes/hermes-agent'); from hermes_cli import kanban_db, kanban_db_connect"
rm -rf "$venv.viejo"; [[ -d "$venv" ]] && mv "$venv" "$venv.viejo"; mv "$venv.nuevo" "$venv"; rm -rf "$venv.viejo"
"$venv/bin/python" - <<'EOF'
import sqlite3, sys
import os; sys.path.insert(0, os.path.expanduser('~/.hermes/hermes-agent'))
from hermes_cli import kanban_db, kanban_db_connect   # lo que usa equipo.py
print('Entorno listo: Python', sys.version.split()[0], '· SQLite', sqlite3.sqlite_version)
EOF
