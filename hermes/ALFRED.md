# Bloque para el cerebro de Alfred (voz, WhatsApp, Telegram)

Se pega en `~/prompted-os/CLAUDE.md` **al instalar Hermes** (es la configuración viva de Alfred: no se toca desde la rama).
Con él, Owner puede hablar con Hermes a través de Alfred sin abrir el chat.

---

## Hermes supremo (objetivos de Owner)

Hermes lleva un objetivo de Owner hasta el final con el equipo (código), anuncios en ensayo, investigación y contenido.
Chat: http://127.0.0.1:4410. Órdenes (con el Python del motor, `~/Equipo/venv/bin/python ~/prompted-os/hermes/hermes.py …`):

- «Alfred, objetivo: …» → `nuevo "<objetivo con sus palabras>" [--proyecto <id>]`. Dile que primero le propondrá cómo medirlo.
- «¿Cómo va Hermes?» → `estado` y resume en una frase por objetivo: en qué está y qué espera de él.
- «Fija el contrato» → `fijar <id>`.
- «Aprueba la ronda» → `aprobar <id> --proyecto voz` (si la ronda tiene tareas importantes, pregúntale antes y añade `--importantes`).
- «Para Hermes» / «para ese objetivo» → `parar <id>`.
- «Sube la confianza de TF a 1» → `confianza beta 1` (0 = aprueba él todo, 1 = solas las fáciles, 2 = fáciles y medias).

Reglas: su permiso hablado es la aprobación (como con `equipo.py encargar`). Nunca apruebes una ronda que Owner no haya pedido aprobar.
Si hay varios objetivos y no está claro cuál, pregunta cuál en una línea.
