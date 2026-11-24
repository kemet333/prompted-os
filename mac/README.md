# Prompted OS para Mac

Apple Silicon · macOS 15 o posterior.

## Qué es

Un sistema operativo personal con un asistente, **Alfred**, al que hablas por voz, por texto o por Telegram. Planifica
tu día, recuerda lo que te importa y coordina un equipo de agentes que trabaja en tus proyectos mientras tú decides.
Todo se ejecuta y se guarda en tu ordenador.

Tus datos viven fuera de la app, en `~/Library/Application Support/com.prompted.os/Workspace/`, así que
actualizarla no los toca. Las rutas de tu ordenador (carpeta de notas, proyectos, voz…) van en
`Workspace/configuracion.json`; lo que no configures queda apagado.

## Requisitos

- [Claude Code](https://claude.com/claude-code) con tu sesión iniciada: es el cerebro de Alfred.
- Opcional: una clave de ElevenLabs para la voz (si no, usa la voz del Mac), un bot de Telegram propio y
  `whisper-cpp` para las notas de voz.

## Compilar y comprobar

```sh
cd ../alfred-os && npm ci && cd ../mac/local-workspace && npm ci && cd ..
cd ../mac && ./build.sh && ./test.sh
```

`build.sh` usa el Node de tu PATH (o `PROMPTED_OS_NODE_BINARY`) y lo incluye dentro de la app. La firma es ad hoc, para uso
local. Las pruebas usan datos aislados de ejemplo; nunca tocan tu espacio real.

## Privacidad

El servidor escucha solo en loopback, con una sesión privada nueva en cada arranque. El micrófono se activa al pulsar
para hablar; el audio se procesa en memoria y no se guarda.

Licencia: AGPL-3.0.
