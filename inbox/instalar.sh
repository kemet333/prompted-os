#!/bin/zsh
# Instala (o reinstala) el inbox de ideas en macOS: cada minuto, con la app abierta o cerrada. Quitar: ./instalar.sh --quitar
set -euo pipefail
here="${0:A:h}"; agents="$HOME/Library/LaunchAgents"; label=com.prompted.os.inbox
logs="$HOME/Equipo/logs"; mkdir -p "$agents" "$logs"
launchctl bootout "gui/$UID/$label" 2>/dev/null || true
rm -f "$agents/$label.plist"
if [[ "${1:-}" == "--quitar" ]]; then echo "Inbox quitado."; exit 0; fi
cat > "$agents/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>$label</string>
 <key>ProgramArguments</key><array><string>/usr/bin/python3</string><string>$here/inbox.py</string></array>
 <key>StartInterval</key><integer>60</integer>
 <key>RunAtLoad</key><true/>
 <key>LowPriorityIO</key><true/>
 <key>StandardOutPath</key><string>$logs/inbox.log</string>
 <key>StandardErrorPath</key><string>$logs/inbox.err.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$UID" "$agents/$label.plist"
echo "Inbox instalado (cada minuto). Estado: python3 $here/inbox.py --estado"
