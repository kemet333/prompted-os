# Prompted OS · interfaz

La interfaz compartida (React 19 + Tailwind 4) de Prompted OS: Alfred, Equipo, Proyectos, Entregas y
Memoria. La app de escritorio (`../mac`) la sirve desde su servidor local; todo se guarda en tu ordenador.

## Secciones

- **Alfred** (⌘1): el asistente. Hablas o escribes; responde con voz y te propone una sola acción cada vez.
- **Equipo** (⌘2): seis departamentos de agentes (Operaciones, Inteligencia, Producto, Creativo, Negocio, Control).
- **Proyectos** (⌘3): tu cartera, leída de tus notas de proyecto.
- **Entregas** (⌘4): lo que espera tu decisión, explicado en palabras llanas, con un botón por decisión.
- **Memoria** (⌘5): tus notas (una carpeta de Markdown; Obsidian funciona), en solo lectura.

Una copia nueva arranca con una cartera de ejemplo (`lib/alfred-data.ts`). Cuando configuras tu carpeta de notas, la
cartera real sale de ahí.

## Reglas

- Una acción clara cada vez, poco texto a la vez, jerarquía fuerte.
- No se inventan porcentajes, estados ni agentes «trabajando»: fecha, evidencia y límites.
- Nada se publica, envía, gasta ni opera sin una acción explícita tuya.

## Desarrollo

```sh
npm ci
npm run dev -- --port 5317
npx tsc --noEmit
```

Licencia: AGPL-3.0.
