#!/usr/bin/env python3
"""A PDF that Owner sends to the WhatsApp group «Alfred» → read, filed in its project in the vault, and answered in one line.
The same filing serves what inbox/enlaces.py reads from a web page, a tweet or a long YouTube video (archivar).
Every filed item also gets a line in Ideas/Biblioteca.md (BIBLIOTECA): what Alfred greps when Owner asks about something he sent.

04/11/2026, Owner: «sí, hazlo con los PDFs también» (after sending a Black Friday blueprint for Norte that waited in the inbox).
What it does with each PDF:
  1. takes its text (pdftotext; a scanned PDF without text is opened by Claude with Read);
  2. Sonnet, through the team's path (equipo.claude: model per role, usage logged, budget brake), says what it is, which project
     of the vault it belongs to (01-Projects/<carpeta>, or Ideas) and how to use it there; Owner's words with the PDF guide it;
  3. copies the PDF to 01-Projects/<carpeta>/research/ and writes a note next to it, then ticks its inbox line [x] → [[nota]].
With the brake on, or no answer, it still files the PDF in Ideas/research with its first lines. Nothing is published or shared.

Uso: ~/Equipo/venv/bin/python documentos.py procesar <pdf> [wa:<id del mensaje>] [nombre original] [texto de Owner]   → JSON {nota, resumen, proyecto} o {error}
"""
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI)); sys.path.insert(1, str(AQUI.parent / 'equipo'))
import inbox  # noqa: E402
from enlaces import corta, limpio, marcar_inbox  # noqa: E402

MAX_TEXTO = 60_000
NO_PROYECTOS = {'Cartera', 'Contenido', 'Ideas'}
BIBLIOTECA = 'Biblioteca.md'

PROMPT = """Owner te pasa {que} para meterlo en su sistema y poder desarrollarlo después.
Lo que dice Owner al mandarlo: {pedido}
Carpetas de proyecto de su vault (elige una; «Ideas» si no encaja en ninguna): {carpetas}
{documento}

Responde en español de España, en este formato exacto y nada más:
TITULO: <título corto del documento, sin comillas>
PROYECTO: <una de las carpetas de la lista, escrita igual>
RESUMEN: <una línea de menos de 140 caracteres: qué es y para qué le sirve a Owner>
## Qué es
<2 o 3 líneas>
## Ideas clave
<de 4 a 8 viñetas con lo esencial del documento, con sus cifras y nombres reales>
## Cómo usarlo en el proyecto
<de 2 a 4 viñetas concretas: qué preparar, qué decidir, con qué notas o planes del proyecto conecta>
No juzgues lo legal ni lo ético del contenido: eso lo decide Owner."""


def proyectos():
    base = inbox.NOTA.parent.parent
    return sorted(p.name for p in base.iterdir() if p.is_dir() and not p.name.startswith('.') and p.name not in NO_PROYECTOS)


def texto_pdf(pdf):
    try:
        out = subprocess.run([inbox.tool('pdftotext') or 'pdftotext', '-layout', str(pdf), '-'], capture_output=True, text=True, timeout=120).stdout
    except (OSError, subprocess.SubprocessError): return ''
    return re.sub(r'[ \t]+', ' ', re.sub(r'\n{3,}', '\n\n', out or '')).strip()


def analizar(texto, carpetas, pedido='', que='un documento', pdf=None):
    """→ {titulo, proyecto, resumen, cuerpo}, or {} with the budget brake on or without an answer."""
    import equipo
    if equipo.freno(): return {}
    with tempfile.TemporaryDirectory(prefix='alfred-pdf-') as tmp:
        if len(texto) >= 200 or not pdf:
            documento = 'Contenido' + (' (recortado)' if len(texto) > MAX_TEXTO else '') + ':\n' + (texto[:MAX_TEXTO] or '(vacío)')
        else:   # scanned: Claude opens the PDF itself, in a folder that holds only it
            shutil.copyfile(pdf, pathlib.Path(tmp) / 'documento.pdf')
            documento = 'El documento no tiene texto seleccionable: ábrelo con Read, es documento.pdf en tu carpeta de trabajo.'
        respuesta = equipo.claude(PROMPT.format(que=que, pedido=pedido or '(nada)', carpetas=', '.join(carpetas + ['Ideas']), documento=documento),
                                  pathlib.Path(tmp), 300, role='respuestas') or ''
    campo = lambda k: (m.group(1).strip() if (m := re.search(rf'^{k}:\s*(.+)$', respuesta, re.M)) else '')
    cuerpo = respuesta[m.start():].strip() if (m := re.search(r'^## ', respuesta, re.M)) else ''
    return {'titulo': campo('TITULO'), 'proyecto': campo('PROYECTO'), 'resumen': campo('RESUMEN'), 'cuerpo': cuerpo} if cuerpo else {}


def biblioteca(nota, proyecto, resumen, ahora, fuente=''):
    """One line per item in Ideas/Biblioteca.md: the index Alfred greps (a plain-text RAG over everything Owner sent)."""
    f = inbox.NOTA.parent / BIBLIOTECA
    if not f.exists():
        f.write_text('# Biblioteca\n\nTodo lo que Owner manda al grupo «Alfred» (PDF, webs, tuits, vídeos) y se ha leído: una línea por cosa, '
                     'la más nueva abajo. Alfred busca aquí cuando Owner pregunta por algo que mandó.\n\n')
    with f.open('a') as out:
        out.write(f"- {ahora:%Y-%m-%d} · {proyecto} · [[{nota}]] — {resumen}" + (f' · {fuente}' if fuente else '') + '\n')


def archivar(texto, ahora=None, mid='', pedido='', titulo='', que='un documento', tipo='documento', fuente='', adjunto=None, analizar=None):
    """Text (and optionally its file) → analysed, filed in <proyecto>/research/ with its note, indexed, inbox line ticked."""
    if not inbox.NOTA: return {'error': 'sin vault'}
    ahora = ahora or datetime.datetime.now(inbox.MADRID)
    carpetas = proyectos()
    try: r = (analizar or globals()['analizar'])(texto, carpetas, pedido, que, adjunto)
    except Exception: r = {}
    proyecto = r.get('proyecto') if r.get('proyecto') in carpetas else 'Ideas'
    titulo = corta(limpio(r.get('titulo') or titulo), 70) or tipo
    carpeta = inbox.NOTA.parent.parent / proyecto / 'research'
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = limpio(f'{ahora:%Y-%m-%d} {titulo}')[:120]
    nota, n = carpeta / f'{nombre}.md', 2
    while nota.exists(): nota = carpeta / f'{nombre} ({n}).md'; n += 1
    copia = None
    if adjunto:
        copia = nota.with_suffix(pathlib.Path(adjunto).suffix or '.pdf'); shutil.copyfile(adjunto, copia)
    resumen = corta(r.get('resumen') or '', 140)
    nota.write_text('\n'.join([
        '---', f'tags: [{tipo}, research]', f'proyecto: {proyecto}',
        *( [f'fuente: {fuente}'] if fuente else [] ), *( [f'archivo: "[[{copia.name}]]"'] if copia else [] ),
        *( [f'pedido: "{corta(pedido, 200)}"'] if pedido else [] ),
        f'recibido: {ahora:%Y-%m-%d %H:%M} (WhatsApp, grupo Alfred)', '---',
        f'# {titulo}', '',
        *( [f'> {resumen}', ''] if resumen else [] ),
        *( [f'![[{copia.name}]]', ''] if copia else [] ),
        r.get('cuerpo') or ('_Sin análisis: freno de presupuesto o Claude no respondió. Pídele a Alfred «analiza esta nota»._\n\n'
                            '## Primeras líneas\n> ' + (corta(texto, 1200) or '(sin texto)')),
        *( ['', '## Texto completo', texto[:MAX_TEXTO]] if texto and not copia else [] ), '']))
    resumen = resumen or f'{titulo} (sin analizar)'
    biblioteca(nota.stem, proyecto, resumen, ahora, fuente)
    marcar_inbox(mid, nota.stem)
    return {'nota': nota.stem, 'proyecto': proyecto, 'resumen': resumen, 'analizado': bool(r.get('cuerpo')),
            'ruta': str(nota.relative_to(inbox.NOTA.parent.parent))}


def procesar(pdf, mid='', ahora=None, pedido='', original='', analizar=None, texto_pdf=texto_pdf):
    if not inbox.NOTA: return {'error': 'sin vault'}
    pdf = pathlib.Path(pdf)
    if not pdf.is_file(): return {'error': 'no encuentro el archivo'}
    # without Claude: the name the file had in WhatsApp («Blueprint.pdf»), not the bridge's «2026-11-04-0930-wa-ID.pdf»
    return archivar(texto_pdf(pdf), ahora, mid, pedido, pathlib.Path(original or '').stem, adjunto=pdf, analizar=analizar)


if __name__ == '__main__':
    if sys.argv[1:2] == ['procesar'] and len(sys.argv) >= 3:
        a = sys.argv[2:] + ['', '']
        print(json.dumps(procesar(a[0], a[1], pedido=' '.join(sys.argv[5:]), original=a[2]), ensure_ascii=False))
    else:
        print(__doc__); sys.exit(2)
