#!/bin/zsh
set -euo pipefail
project_dir="${0:A:h}"
test_dir="${PROMPTED_OS_TEST_DIR:-${TMPDIR:-/private/tmp}/prompted-os-tests-$UID}"
mkdir -p "$test_dir/modules"
export PROMPTED_OS_DISABLE_RITUAL_PREP=1   # never a real Claude call for the ritual greeting in tests
xcrun swiftc -swift-version 6 -strict-concurrency=complete -module-cache-path "$test_dir/modules" \
  "$project_dir/Sources/DoubleClapDetector.swift" "$project_dir/Tests/DetectorChecks.swift" \
  -o "$test_dir/detector-checks"
"$test_dir/detector-checks"
xcrun swiftc -swift-version 6 -strict-concurrency=complete -module-cache-path "$test_dir/modules" \
  "$project_dir/Sources/DoubleClapDetector.swift" "$project_dir/Sources/AudioWindowFeatures.swift" \
  "$project_dir/Tests/FeatureChecks.swift" -framework AVFoundation -o "$test_dir/feature-checks"
"$test_dir/feature-checks"

xcrun swiftc -swift-version 6 -strict-concurrency=complete -module-cache-path "$test_dir/modules" \
  "$project_dir/Sources/NativeSpeechRequest.swift" "$project_dir/Tests/NativeSpeechChecks.swift" \
  -o "$test_dir/native-speech-checks"
"$test_dir/native-speech-checks"

node_binary="${PROMPTED_OS_NODE_BINARY:-$(command -v node || echo "$HOME/.local/bin/node")}"; node_binary=${node_binary:A}
"$node_binary" "$project_dir/local-workspace/build-server.mjs"
xcrun swiftc -swift-version 5 -O -module-cache-path "$test_dir/modules" \
  "$project_dir/Sources/HealthImport.swift" -o "$test_dir/health-import"
PROMPTED_OS_HEALTH_HELPER="$test_dir/health-import" "$node_binary" "$project_dir/local-workspace/life-checks.mjs"
"$node_binary" "$project_dir/local-workspace/life-activity-checks.mjs"
"$node_binary" "$project_dir/local-workspace/ritual-checks.mjs"
"$node_binary" "$project_dir/local-workspace/runtime-checks.mjs"
"$node_binary" "$project_dir/local-workspace/telegram-checks.mjs"
"$node_binary" "$project_dir/local-workspace/cartera-checks.mjs"
"$node_binary" "$project_dir/local-workspace/contenido-checks.mjs"
"$node_binary" "$project_dir/local-workspace/voice-checks.mjs"
"$node_binary" "$project_dir/local-workspace/entreno-checks.mjs"
# Inbox de ideas contra un Telegram falso (nunca el bot ni el vault reales)
/usr/bin/python3 -m unittest discover -s "$project_dir/../inbox" -p "test_*.py"
# Alfred en WhatsApp: la lógica del puente contra un WhatsApp falso y el inbox real en una carpeta temporal
"$node_binary" --test "$project_dir/../whatsapp/puente-checks.mjs"
