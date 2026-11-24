#!/usr/bin/env python3
"""Un reel, TikTok o vídeo de YouTube que Owner manda al grupo de WhatsApp «Alfred» → una nota «Para rehacer» en el vault.

03/11/2026, Owner: «si te copio un reel que me interesa rehacer o que me interesa meter esa información al sistema…».
Lo que hace con cada enlace:
  1. lo descarga en una carpeta temporal (yt-dlp, sin sesión: solo lo público) y lo transcribe en este Mac (el Whisper de la app);
  2. Sonnet, por el mismo camino que el equipo (equipo.claude: modelo por rol, consumo apuntado, freno de presupuesto), saca
     por qué funciona y cómo rehacerlo; con el freno puesto, la nota sale solo con la transcripción;
  3. escribe Contenido/Para rehacer/<fecha> <autor> - <título>.md y marca su línea del inbox [x] → [[nota]];
  4. deja el vídeo como referencia del sistema de contenidos (04/11, «el sistema de contenidos no los ha recibido»):
     <referencias>/refs-<código>/ con <código>.mp4, ref.info.json y transcripcion.txt, el formato de full-visual. <referencias>
     es configuracion.json → contenido.referencias o, si no está, la carpeta input junto a contenido.fullVisual.
Lo que Owner escribe junto al enlace («para Norte») va al análisis. Nada se publica ni se comparte.

04/11/2026, Owner: «links a Reels, a TikTok… tweets, vídeos de YouTube, páginas web: transcribir y meter al sistema esa información o
dejarlo en pendiente». leer(url) decides: a short video (reel, TikTok, Short, YouTube of 3 min or less) → «Para rehacer» as above;
a tweet (through the public fxtwitter API, its video transcribed), a longer YouTube video (its subtitles, or Whisper up to 30 min)
or any web page (its readable text; a link to a PDF is read as a PDF) → filed in its project like a PDF (documentos.archivar), and
indexed in Ideas/Biblioteca.md. Whatever cannot be read stays [ ] in the inbox, pending.

Uso: ~/Equipo/venv/bin/python enlaces.py procesar <url> [wa:<id del mensaje>] [texto de Owner]   → JSON {nota, resumen, destino, icono} o {error}
"""
import datetime
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import html.parser
import urllib.request

AQUI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI)); sys.path.insert(1, str(AQUI.parent / 'equipo'))
import inbox  # noqa: E402

VIDEO = re.compile(r'https?://(?:www\.|m\.|vm\.|vt\.)?(?:instagram\.com/(?:reel|reels|p|tv)/|tiktok\.com/|youtube\.com/(?:shorts/|watch\?)|youtu\.be/)[^\s<>"]+', re.I)
MAX_SEGUNDOS = 15 * 60
CARPETA = 'Contenido/Para rehacer'
CORTO = 180                    # a YouTube video up to 3 min is a format to remake; longer, it is knowledge to file
WHISPER_MAX = 30 * 60          # longer videos without subtitles stay pending: an hour of Whisper would hold the queue
TUIT = re.compile(r'https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/([A-Za-z0-9_]+)/status/(\d+)', re.I)
YOUTUBE = re.compile(r'youtube\.com/watch\?|youtu\.be/', re.I)
NAVEGADOR = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'


def referencias():
    """Where the content system takes its reference videos from, or None (then only the note is written)."""
    propia = inbox.ruta('contenido.referencias')
    if propia: return propia
    salida = inbox.ruta('contenido.fullVisual')
    return salida.parent / 'input' if salida and (salida.parent / 'input').is_dir() else None

PROMPT = """Owner te pasa
un vídeo corto que le ha gustado para rehacerlo o aprender de él. Datos del vídeo:
Autor: {autor}
Pie: {pie}
Duración: {duracion}
Lo que dice Owner al mandarlo: {pedido}
Transcripción:
{texto}

Responde en español de España, en este formato exacto y nada más:
RESUMEN: <una línea de menos de 140 caracteres: de qué va y por qué merece la pena>
## Por qué funciona
<de 4 a 6 viñetas cortas: gancho, estructura, giro, prueba, CTA, ritmo; cita frases reales del vídeo>
## Cómo rehacerlo
<de 2 a 4 viñetas: la estructura reutilizable y una adaptación concreta, con su gancho escrito, para la marca que diga Owner o, si no dice ninguna, la que mejor encaje>
No juzgues lo legal ni lo ético del contenido: eso lo decide Owner."""


def corta(s, n):
    s = ' '.join(str(s or '').split())
    return s if len(s) <= n else s[:n - 1].rstrip() + '…'


def limpio(s):
    """Safe for a file name in the vault (no slashes, no Obsidian link characters)."""
    return re.sub(r'\s+', ' ', re.sub(r'[\\/:*?"<>|#^\[\]]', '', s or '')).strip()


def descargar(url, carpeta):
    """→ (vídeo, metadatos). Public videos only: no cookies, no login."""
    salida = pathlib.Path(carpeta) / 'video.%(ext)s'
    subprocess.run([inbox.tool('yt-dlp') or 'yt-dlp', '-q', '--no-warnings', '--no-playlist', '--write-info-json',
                    '--match-filter', f'duration <? {MAX_SEGUNDOS}', '-f', 'mp4/best', '-o', str(salida), url],
                   check=True, capture_output=True, timeout=300)
    info = json.loads(next(pathlib.Path(carpeta).glob('video.info.json')).read_text())
    video = next(p for p in pathlib.Path(carpeta).glob('video.*') if p.suffix != '.json')
    return video, info


def transcribir(video):
    return inbox.transcribe(video, idioma='auto')


def analizar(texto, info, pedido=''):
    """→ (resumen, cuerpo markdown) or ('', '') with the budget brake on or without an answer."""
    import equipo
    if equipo.freno(): return '', ''
    respuesta = equipo.claude(PROMPT.format(autor=info.get('uploader') or info.get('channel') or '¿?', pie=corta(info.get('description'), 600), pedido=pedido or '(nada)',
                                            duracion=f"{round(info['duration'])} s" if info.get('duration') else '¿?', texto=texto or '(sin voz)'),
                              equipo.VAULT if equipo.VAULT.exists() else AQUI, 240, role='respuestas')
    m = re.search(r'RESUMEN:\s*(.+)', respuesta or '')
    cuerpo = (respuesta or '')[m.end():].strip() if m else (respuesta or '').strip()
    return (corta(m.group(1), 140) if m else ''), cuerpo


def marcar_inbox(mid, nota):
    """The inbox line of that message → [x] with a link to its note (the marker stays)."""
    if not mid or not inbox.NOTA or not inbox.NOTA.exists(): return
    marca = inbox.marca(mid)
    lineas = inbox.NOTA.read_text().split('\n')
    for i, l in enumerate(lineas):
        if marca in l and l.startswith('- [ ] '):
            lineas[i] = '- [x] ' + l[6:].replace(' ' + marca, f' → [[{nota}]] {marca}')
            inbox.NOTA.write_text('\n'.join(lineas)); return


def guardar_referencia(video, info, texto, url):
    """The video where the content system looks for references: refs-<code>/<code>.mp4 (+ its data and transcript)."""
    base = referencias()
    if not base: return None
    codigo = re.sub(r'[^A-Za-z0-9_-]', '', str(info.get('id') or '')) or re.sub(r'\W', '', url)[-12:]
    carpeta = base / f'refs-{codigo}'
    carpeta.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(video, carpeta / f'{codigo}{pathlib.Path(video).suffix or ".mp4"}')
    datos = {k: info.get(k) for k in ('id', 'uploader', 'channel', 'description', 'title', 'duration', 'webpage_url')}
    (carpeta / 'ref.info.json').write_text(json.dumps({**datos, 'fuente': url}, ensure_ascii=False, indent=1))
    if texto: (carpeta / 'transcripcion.txt').write_text(texto + '\n')
    return carpeta


def procesar(url, mid='', ahora=None, pedido='', descargar=descargar, transcribir=transcribir, analizar=analizar):
    if not inbox.NOTA: return {'error': 'sin vault'}
    ahora = ahora or datetime.datetime.now(inbox.MADRID)
    referencia = None
    with tempfile.TemporaryDirectory(prefix='alfred-enlace-') as tmp:
        try: video, info = descargar(url, tmp)
        except Exception as e: return {'error': 'no he podido descargarlo (¿es privado?)', 'detalle': type(e).__name__}
        try: texto = transcribir(video)
        except Exception: texto = ''
        try: referencia = guardar_referencia(video, info, texto, url)
        except OSError: referencia = None
    try: resumen, cuerpo = analizar(texto, info, pedido)
    except Exception: resumen, cuerpo = '', ''
    autor = info.get('uploader') or info.get('channel') or 'autor desconocido'
    titulo = corta(limpio((info.get('description') or info.get('title') or '').split('\n')[0]) or 'vídeo', 60)
    nombre = limpio(f"{ahora:%Y-%m-%d} {autor} - {titulo}")[:120]
    carpeta = inbox.NOTA.parent.parent / CARPETA
    carpeta.mkdir(parents=True, exist_ok=True)
    nota = carpeta / f'{nombre}.md'
    n = 2
    while nota.exists(): nota = carpeta / f'{nombre} ({n}).md'; n += 1
    pie = (info.get('description') or '').strip()
    nota.write_text('\n'.join([
        '---', 'tags: [contenido, para-rehacer, referencia]', f'fuente: {url}', f'autor: {autor}',
        *( [f"duracion: {round(info['duration'])} s"] if info.get('duration') else [] ),
        *( [f'referencia: "{referencia}"'] if referencia else [] ),
        *( [f'pedido: "{corta(pedido, 200)}"'] if pedido else [] ),
        f'recibido: {ahora:%Y-%m-%d %H:%M} (WhatsApp, grupo Alfred)', '---',
        f'# {titulo} — {autor}', '',
        *( [f'> {resumen}', ''] if resumen else [] ),
        '## Transcripción (Whisper en este Mac)', '> ' + (texto or '(sin voz o no se ha podido transcribir)'), '',
        *( ['Pie: ' + corta(pie, 800), ''] if pie else [] ),
        cuerpo or '_Sin análisis: freno de presupuesto o Claude no respondió. Pídele a Alfred «analiza esta nota»._', '']))
    marcar_inbox(mid, nota.stem)
    import documentos
    documentos.biblioteca(nota.stem, 'Para rehacer', resumen or corta(f'{autor}: {titulo}', 140), ahora, url)
    return {'nota': nota.stem, 'resumen': resumen or corta(f'{autor}: {titulo}', 140), 'analizado': bool(cuerpo), **({'referencia': referencia.name} if referencia else {})}


# ---------- everything else: tweets, long videos and web pages, filed as knowledge ----------
def bajar(url, limite=8_000_000):
    """→ (bytes, content type). Public pages only: no cookies."""
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': NAVEGADOR, 'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8'}), timeout=30) as r:
        return r.read(limite), (r.headers.get('Content-Type') or '').lower()


class _Texto(html.parser.HTMLParser):
    """The readable text of a page: title, description and the words outside scripts, menus and footers."""
    FUERA = {'script', 'style', 'noscript', 'svg', 'nav', 'footer', 'header', 'form', 'aside', 'template'}
    BLOQUE = {'p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'section', 'article', 'tr', 'blockquote'}

    def __init__(self):
        super().__init__(); self.partes, self.dentro, self.titulo, self.desc, self._t = [], 0, '', '', False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.FUERA: self.dentro += 1
        elif tag == 'title': self._t = True
        elif tag == 'meta' and (a.get('name') or a.get('property') or '').lower() in ('description', 'og:description') and not self.desc: self.desc = a.get('content') or ''
        elif tag == 'meta' and (a.get('property') or '').lower() == 'og:title' and a.get('content'): self.titulo = a['content']
        if tag in self.BLOQUE: self.partes.append('\n')

    def handle_endtag(self, tag):
        if tag in self.FUERA and self.dentro: self.dentro -= 1
        elif tag == 'title': self._t = False

    def handle_data(self, data):
        if self._t: self.titulo = self.titulo or data.strip()
        elif not self.dentro: self.partes.append(data)


def texto_web(crudo):
    """→ (title, readable text)."""
    p = _Texto()
    try: p.feed(crudo); p.close()
    except Exception: pass
    lineas = [' '.join(l.split()) for l in ''.join(p.partes).split('\n')]
    cuerpo = '\n'.join(l for l in lineas if len(l) > 1)
    return ' '.join(p.titulo.split()), ((p.desc.strip() + '\n\n') if p.desc.strip() else '') + cuerpo


def subtitulos(url, carpeta):
    """YouTube's own subtitles (Spanish or English, made by hand or automatic) as plain text, or ''."""
    try:
        subprocess.run([inbox.tool('yt-dlp') or 'yt-dlp', '-q', '--no-warnings', '--no-playlist', '--skip-download', '--write-subs', '--write-auto-subs',
                        '--sub-langs', 'es-ES,es,en-orig,en,en-US', '--sub-format', 'vtt', '-o', str(pathlib.Path(carpeta) / 'subs.%(ext)s'), url],
                       capture_output=True, timeout=180)   # no check: YouTube may refuse one track (429) after writing another
    except (OSError, subprocess.SubprocessError): return ''
    vtts = sorted(pathlib.Path(carpeta).glob('subs*.vtt'), key=lambda f: (not re.search(r'\.es', f.name), len(f.name)))
    if not vtts: return ''
    vistas, salida = set(), []
    for l in vtts[0].read_text(errors='ignore').splitlines():
        l = re.sub(r'<[^>]+>', '', l).strip()
        if not l or '-->' in l or l.startswith(('WEBVTT', 'Kind:', 'Language:')) or l in vistas: continue
        vistas.add(l); salida.append(l)
    return ' '.join(salida)


def info_video(url):
    """yt-dlp metadata without downloading."""
    out = subprocess.run([inbox.tool('yt-dlp') or 'yt-dlp', '-q', '--no-warnings', '--no-playlist', '-J', url], check=True, capture_output=True, text=True, timeout=120).stdout
    return json.loads(out)


def leer_youtube(url, info, mid, pedido, ahora):
    import documentos
    with tempfile.TemporaryDirectory(prefix='alfred-yt-') as tmp:
        texto = subtitulos(url, tmp)
        if not texto:
            if (info.get('duration') or 0) > WHISPER_MAX: return {'error': 'es largo y no tiene subtítulos; queda pendiente'}
            subprocess.run([inbox.tool('yt-dlp') or 'yt-dlp', '-q', '--no-warnings', '--no-playlist', '-f', 'bestaudio/best', '-o', str(pathlib.Path(tmp) / 'audio.%(ext)s'), url],
                           check=True, capture_output=True, timeout=900)
            texto = transcribir(next(pathlib.Path(tmp).glob('audio.*')))
    if not texto: return {'error': 'no he podido transcribirlo'}
    cabeza = f"Vídeo de YouTube de {info.get('uploader') or info.get('channel') or '¿?'}: «{info.get('title') or ''}» ({round((info.get('duration') or 0) / 60)} min)\n"
    cabeza += f"Descripción: {corta(info.get('description'), 800)}\nTranscripción:\n"
    return documentos.archivar(cabeza + texto, ahora, mid, pedido, info.get('title') or 'vídeo', 'un vídeo de YouTube (su transcripción)', 'video', url)


def leer_tuit(url, usuario, tid, mid, pedido, ahora):
    import documentos
    datos = json.loads(bajar(f'https://api.fxtwitter.com/{usuario}/status/{tid}')[0]).get('tweet') or {}
    if not datos: return {'error': 'no he podido leer el tuit (¿borrado o privado?)'}
    autor = datos.get('author') or {}
    partes = [f"Tuit de {autor.get('name') or usuario} (@{autor.get('screen_name') or usuario}), {datos.get('created_at') or ''}:", datos.get('text') or '']
    articulo = datos.get('article') or {}
    if isinstance(articulo, dict) and (articulo.get('title') or articulo.get('preview_text')):
        partes.append(f"Artículo: {articulo.get('title') or ''}\n{articulo.get('preview_text') or ''}")
    citado = datos.get('quote') or {}
    if citado.get('text'): partes.append(f"Cita a @{(citado.get('author') or {}).get('screen_name') or '¿?'}: {citado['text']}")
    videos = [m for m in ((datos.get('media') or {}).get('videos') or []) if isinstance(m, dict)]
    if videos:
        try:
            with tempfile.TemporaryDirectory(prefix='alfred-tuit-') as tmp:
                video, _ = descargar(url, tmp)
                oido = transcribir(video)
            if oido: partes.append('Transcripción del vídeo: ' + oido)
        except Exception: partes.append('(tiene un vídeo que no he podido transcribir)')
    titulo = f"@{autor.get('screen_name') or usuario} - {corta(limpio((datos.get('text') or '').split(chr(10))[0]), 50)}"
    return documentos.archivar('\n\n'.join(p for p in partes if p), ahora, mid, pedido, titulo, 'un tuit', 'tuit', url)


def leer_web(url, mid, pedido, ahora):
    import documentos
    crudo, tipo = bajar(url)
    if 'pdf' in tipo or crudo[:5] == b'%PDF-':
        with tempfile.TemporaryDirectory(prefix='alfred-web-') as tmp:
            pdf = pathlib.Path(tmp) / 'documento.pdf'; pdf.write_bytes(crudo)
            return {**documentos.procesar(pdf, mid, ahora, pedido, pathlib.Path(url.split('?')[0]).name), 'icono': '📄'}
    if 'html' not in tipo and 'text' not in tipo: return {'error': f'no sé leer este tipo de enlace ({tipo or "desconocido"})'}
    titulo, texto = texto_web(crudo.decode('utf-8', errors='ignore'))
    if len(texto) < 200: return {'error': 'la página no deja leer su texto (pide sesión o va con JavaScript); queda pendiente'}
    return documentos.archivar(f'Página web: {titulo}\n{url}\n\n{texto}', ahora, mid, pedido, titulo or url, 'una página web', 'web', url)


def pie_instagram(url):
    """→ (título, texto del pie) from the post's public page (og:title / og:description), or None."""
    import html as _html
    crudo, _ = bajar(url)
    h = crudo.decode('utf-8', errors='ignore')
    meta = lambda k: _html.unescape(m.group(1)) if (m := re.search(rf'property="og:{k}" content="([^"]*)"', h)) else ''
    texto = meta('description') or meta('title')
    if len(texto) < 40: return None
    autor = meta('title').split(' en Instagram')[0].split(' on Instagram')[0].strip()
    frase = texto.split(':', 1)[-1].strip(' "“”')
    return corta(f'{autor} - {frase}', 70), texto


def documentos_archivar(texto, ahora, mid, pedido, titulo, url):
    import documentos
    return documentos.archivar(texto, ahora, mid, pedido, titulo, 'un post de Instagram con fotos', 'post', url)


def leer(url, mid='', ahora=None, pedido=''):
    """Any link Owner sends → the right path (see the header). → {nota, resumen, destino, icono} or {error}."""
    if not inbox.NOTA: return {'error': 'sin vault'}
    ahora = ahora or datetime.datetime.now(inbox.MADRID)
    try:
        if (m := TUIT.search(url)): r, icono = leer_tuit(url, m.group(1), m.group(2), mid, pedido, ahora), '🐦'
        elif YOUTUBE.search(url) and not re.search(r'/shorts/', url):
            info = info_video(url)
            if (info.get('duration') or 0) <= CORTO: return {**procesar(url, mid, ahora, pedido), 'destino': 'Para rehacer', 'icono': '🎬'}
            r, icono = leer_youtube(url, info, mid, pedido, ahora), '▶️'
        elif VIDEO.search(url):
            r = procesar(url, mid, ahora, pedido)
            # 05/11: a photo carousel (instagram.com/p/…) has no video to download: its caption still says what it is
            if r.get('error') and '/p/' in url and (pie := pie_instagram(url)):
                r, icono = documentos_archivar(f'Post de Instagram (fotos): {pie[0]}\n{url}\n\n{pie[1]}', ahora, mid, pedido, pie[0], url), '🖼️'
            else: return {**r, 'destino': 'Para rehacer', 'icono': '🎬'}
        else: r, icono = leer_web(url, mid, pedido, ahora), '🔗'
    except Exception as e:
        return {'error': 'no he podido abrirlo (¿es privado o pide sesión?); queda pendiente', 'detalle': type(e).__name__}
    if r.get('nota'): r = {'icono': icono, **r, 'destino': r.get('proyecto') or ''}
    return r


if __name__ == '__main__':
    if sys.argv[1:2] == ['procesar'] and len(sys.argv) >= 3:
        print(json.dumps(leer(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else '', pedido=' '.join(sys.argv[4:])), ensure_ascii=False))
    else:
        print(__doc__); sys.exit(2)
