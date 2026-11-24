# Hermes supremo · nota de diseño

Encargo de Owner (nota de voz, 10/11/2026): «En el Mac, un chat Hermes supremo que coordine Codex y Claude a la perfección,
en bucle hasta completar mi objetivo». Ampliado el 13/11 con su «top todo»: objetivo con métrica de dinero y todos los motores,
demostrar con capturas, aprobar desde el móvil y por voz con niveles de confianza, debate Codex/Claude con el constructor medido,
turno de noche y detección de estancamiento.

## Qué es
Un chat local (http://127.0.0.1:4410) donde Owner escribe **qué** quiere conseguir, idealmente con una cifra. Hermes fija con él cómo
medirlo, reparte el trabajo entre los motores que ya existen, deja que Claude y Codex debatan cada ronda, comprueba lo construido
y vuelve a planificar hasta que la cifra se cumpla o necesite a Owner.

## El bucle
```
objetivo de Owner
 └─ contrato ─ Claude propone: qué es «hecho», la métrica (si hay fuente que la lea sola), plazo, presupuesto y qué no se toca
      └─ Owner lo fija (o le dice qué cambiar)
 └─ planificando ─ se mide la métrica: si ya se cumple → hecho (sin gastar en planificar)
      ├─ Claude planifica ≤3 tareas, cada una con su motor y sus criterios de aceptación
      ├─ Codex lee el código y contrapropone SU ronda
      └─ un árbitro (Claude Opus) elige o mezcla y explica la diferencia en dos frases
 └─ esperando_aprobacion ─ Owner aprueba en el chat, con un botón en WhatsApp/Telegram o por voz
      └─ o se aprueba sola si Owner subió la confianza del proyecto y la ronda es solo de tareas fáciles
 └─ en_marcha ─ cada tarea a su motor:
      equipo        → `equipo.py encargar` (constructor y revisor de distinto proveedor, pruebas, integra en equipo/<pid>, nunca main)
      anuncios      → plan JSON a sem-meta-ads y `crear` en ENSAYO (nunca --ejecutar: nada llega a Meta)
      investigacion → Claude con web en solo lectura → nota en el vault (01-Projects/Hermes/<objetivo>/)
      contenido     → borradores (guiones, emails, textos) → nota en el vault; nadie publica
 └─ ronda terminada ─ verificación: un tercer agente comprueba cada criterio contra el diff integrado,
      y se arranca la rama del equipo en una copia aparte para sacar capturas (escritorio y móvil) si el proyecto está en demos.json
 └─ vuelta a planificar con resultados, criterios fallidos y la nueva medida … → hecho (+ cierre y playbook)
```
Un paso por objetivo en cada `tick` (cada 5 min con el servicio). Idempotente, con bloqueo de archivo: chat, botones y tick no se pisan.

## Las cinco mejoras de Owner, cómo están hechas
1. **Métrica de dinero y todos los motores.** El contrato lleva la métrica; Hermes la lee sola de `sem-meta-ads` (leads, coste por lead),
   de las solicitudes de la landing de TbT o de los recibos del equipo. Un «hecho» del planificador no vale si la cifra no se cumple:
   busca otra palanca y, si no la ve, pregunta. Cuatro motores: equipo, anuncios (ensayo), investigación y contenido.
2. **Demostrar, no decir.** Criterios de aceptación escritos antes de construir y comprobados después por un agente que no construyó ni
   revisó. Capturas reales de la rama del equipo (probado con Beta: 4 capturas en 8,6 s, sin tocar la carpeta de Owner).
3. **Móvil, voz y confianza.** Cada ronda lista llega a WhatsApp/Telegram con «Aprobar / Descartar / Parar» (callback `hm:<id>:a|i|d|p`,
   gestionado en `telegram.ts`, comprobado en `telegram-checks.mjs`). Voz: bloque listo en `ALFRED.md`. Confianza por proyecto:
   0 = apruebas tú todo (por defecto), 1 = solas las fáciles, 2 = fáciles y medias. **El máximo lo fija Owner**; el nivel real se gana
   con 3 rondas seguidas bien hechas y vuelve a 0 con un fallo, un criterio sin cumplir o un descarte. Anuncios e importantes, nunca solas.
4. **Debate y constructor por resultados.** Contrapropuesta completa de Codex y árbitro. «Quién construye mejor»: tasa de integración a la
   primera de Codex y Claude por tipo de trabajo, con los recibos reales (30 días). A día 13/11: Codex gana en motor (97 % frente a 90 %),
   Claude en pruebas (100 % frente a 76 %) y lógica (83 % frente a 78 %). Hoy se enseña en el chat; **para que el motor elija constructor
   con esa tabla hace falta un cambio de 3 líneas en `equipo.py`** (un campo `preferir` en la tarea), que se hace después de la PR #10.
5. **Turno de noche y estancamiento.** De 00:00 a 07:30 no despierta a Owner: guarda los avisos y a las 08:25 manda un solo resumen
   («Mientras dormías… / Te espera…») con los botones de la primera ronda pendiente. Dos rondas sin integrar nada, o la métrica quieta tres
   medidas → pregunta en lugar de insistir («sigue» para insistir). Presupuesto de llamadas por objetivo, con contador.

Además: estimación de minutos y uso por ronda (con el consumo real de 14 días), cierre de cada objetivo (rondas, tareas, motores,
llamadas, días) guardado como **playbook**: un objetivo parecido arranca con lo que funcionó.

## Paradas y topes
| Qué | Efecto |
|---|---|
| Botón **Parar** (chat, móvil o voz) | No planifica ni encarga más; dice qué tareas siguen en el equipo y cómo retirarlas. |
| Freno del motor (85 % del plan) o equipo en pausa | No planifica ni deja aprobar; avisa una vez y sigue al levantarse. |
| Presupuesto del objetivo (contrato) | Pregunta; «sigue» da un tercio más. |
| 8 rondas por objetivo · 6 rondas y 40 llamadas al día | Pregunta o para hasta mañana; «sigue» da margen. |
| 3 tareas por ronda | Rondas pequeñas: se ve el resultado antes de seguir. |
| Pantallas protegidas (`focus-console.tsx`, `orb.tsx`, `orb.css`) y lo que el contrato dice que no se toca | Fuera antes de enseñar la ronda. |
| «necesito a Owner» | Precio, clave, publicar, activar anuncios, gastar o decisión de producto. |

Nunca: aprobar por Owner sin que él haya subido la confianza, publicar, activar anuncios, gastar, push, main ni despliegues.

## Archivos
- `nucleo.py`: el bucle (lógica pura). `adaptador.py`: real (equipo, sem-meta-ads, TbT, vault, capturas) y falso (demo y pruebas).
- `hermes.py`: `serve`, `tick`, `nuevo`, `estado`, `fijar`, `aprobar`, `parar`, `boton`, `confianza`, `demo`. `chat.html`: el chat.
- `demos.json`: proyectos que sabe arrancar para las capturas (hoy Beta).
- `ALFRED.md`: bloque para el cerebro de Alfred (voz) que se pega al instalar.
- `test_hermes.py`: 30 pruebas. App: botón `hm:` en `telegram.ts`, `hermes()` en `server.ts`, 4 comprobaciones en `telegram-checks.mjs`.
- `com.prompted.os.hermes.plist.example`: servicio **sin instalar**.

## Probar
```sh
~/Equipo/venv/bin/python -m unittest hermes/test_hermes.py
~/Equipo/venv/bin/python hermes/hermes.py demo                 # en seco, en la terminal
~/Equipo/venv/bin/python hermes/hermes.py serve --seco          # en seco, en el navegador: http://127.0.0.1:4410
```

## Para instalar (lo decide Owner)
1. Juntar la rama a `main` y compilar la app (los botones del móvil viven en la app).
2. Copiar `com.prompted.os.hermes.plist.example` a `~/Library/LaunchAgents/` y cargarlo.
3. Pegar `ALFRED.md` en `~/prompted-os/CLAUDE.md`.
4. Opcional: subir la confianza de algún proyecto y añadir más proyectos a `demos.json`.

## Después (no hecho)
- El campo `preferir` en `equipo.py` para que el ranking elija constructor (tras la PR #10).
- Duelo: Codex y Claude construyen la misma tarea dudosa y gana la que pase pruebas y revisión con menos cambios (también en el motor).
- Mandar las capturas al móvil como imagen (el puente de WhatsApp hoy solo manda texto y encuestas).
- Vista «Hermes» dentro de Prompted OS leyendo la misma API (nunca en la pantalla Alfred). Deshacer una ronda con un botón.
