#!/bin/zsh
# Alfred en WhatsApp (macOS). Primero, una vez, vincula el número de Alfred:  node puente.mjs vincular   (o: vincular +34XXXXXXXXX)
# Usa un número propio para Alfred, no el tuyo: es un dispositivo vinculado no oficial y WhatsApp puede bloquearlo.
# Después instala (o reinstala) el servicio: ./instalar.sh   ·   Quitar: ./instalar.sh --quitar
set -euo pipefail
here="${0:A:h}"; agents="$HOME/Library/LaunchAgents"; label=com.prompted.os.whatsapp
logs="$HOME/Equipo/logs"; mkdir -p "$agents" "$logs"
launchctl bootout "gui/$UID/$label" 2>/dev/null || true
rm -f "$agents/$label.plist"
if [[ "${1:-}" == "--quitar" ]]; then echo "Alfred en WhatsApp, quitado."; exit 0; fi
node_bin=$(command -v node || true)
[[ -n "$node_bin" ]] || node_bin="/Applications/Prompted OS.app/Contents/Resources/runtime/node"
[[ -x "$node_bin" ]] || { echo "No encuentro Node: instala Node 22 o posterior, o la app en /Applications."; exit 1; }
data="$HOME/Library/Application Support/com.prompted.os/Workspace/whatsapp/sesion"
if [[ ! -f "$data/creds.json" ]]; then echo "Falta vincular el número de Alfred: cd $here && \"$node_bin\" puente.mjs vincular"; exit 1; fi
[[ -d "$here/node_modules" ]] || (cd "$here" && PATH="${node_bin:h}:$PATH" npm ci --no-audit --no-fund)
cat > "$agents/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
 <key>Label</key><string>$label</string>
 <key>ProgramArguments</key><array><string>$node_bin</string><string>$here/puente.mjs</string></array>
 <key>WorkingDirectory</key><string>$here</string>
 <key>EnvironmentVariables</key><dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
 <key>RunAtLoad</key><true/>
 <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
 <key>ThrottleInterval</key><integer>30</integer>
 <key>LowPriorityIO</key><true/>
 <key>StandardOutPath</key><string>$logs/whatsapp.log</string>
 <key>StandardErrorPath</key><string>$logs/whatsapp.err.log</string>
</dict></plist>
PLIST
launchctl bootstrap "gui/$UID" "$agents/$label.plist"
echo "Alfred en WhatsApp, en marcha. Registro: $data/../registro.log"
