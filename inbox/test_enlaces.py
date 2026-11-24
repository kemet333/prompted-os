"""inbox/enlaces.py against a temporary vault with a fake download, transcription and analysis: never Owner's vault, the
network or Claude."""
import datetime
import json
import pathlib
import sys
import tempfile
import unittest

AQUI = pathlib.Path(__file__).resolve().parent
_TMP = tempfile.TemporaryDirectory()
VAULT = pathlib.Path(_TMP.name) / 'vault'
(VAULT / 'Ideas').mkdir(parents=True)
sys.path.insert(0, str(AQUI))
from unittest import mock  # noqa: E402
enlaces = None


def setUpModule():
    # Imported here, not at the top: test_inbox.py must import the shared inbox module first, pointed at its own fake folder.
    # Run alone, it points at an empty one of its own; never at Owner's real Workspace.
    global enlaces
    if 'inbox' not in sys.modules:
        import os
        os.environ['PROMPTED_OS_DATA_DIR'] = str(pathlib.Path(_TMP.name) / 'ws')
    import enlaces as e
    enlaces = e

AHORA = datetime.datetime(2026, 11, 3, 19, 50)
INFO = {'uploader': 'Miquel Gironès', 'description': 'Why America Is A Tax Haven 🇺🇸\nComment "US"', 'duration': 37.4}


def descargar(url, carpeta):
    video = pathlib.Path(carpeta) / 'video.mp4'; video.write_bytes(b'x')
    return video, INFO


class Enlaces(unittest.TestCase):
    def setUp(self):
        # test_inbox.py configures the shared inbox module for its own folder: only the note path is swapped here
        patcher = mock.patch.object(enlaces.inbox, 'NOTA', VAULT / 'Ideas' / 'inbox.md'); patcher.start(); self.addCleanup(patcher.stop)
        (VAULT / 'Ideas' / 'inbox.md').write_text('# Inbox\n\n- [ ] 2026-11-03 19:49 · https://www.instagram.com/reel/A1/ <!-- wa:M1 -->\n')
        for f in (VAULT / 'Contenido' / 'Para rehacer').glob('*.md') if (VAULT / 'Contenido').exists() else []: f.unlink()

    def test_the_note_has_the_transcript_and_the_analysis_and_the_inbox_line_is_ticked(self):
        r = enlaces.procesar('https://www.instagram.com/reel/A1/', 'wa:M1', AHORA, descargar=descargar, transcribir=lambda v: 'Do you know what the biggest tax haven is?',
                             analizar=lambda texto, info, pedido: ('Gancho contrario sobre impuestos', '## Por qué funciona\n- Pregunta con giro'))
        self.assertEqual(r, {'nota': '2026-11-03 Miquel Gironès - Why America Is A Tax Haven 🇺🇸', 'resumen': 'Gancho contrario sobre impuestos', 'analizado': True})
        nota = (VAULT / 'Contenido' / 'Para rehacer' / (r['nota'] + '.md')).read_text()
        for parte in ('fuente: https://www.instagram.com/reel/A1/', 'duracion: 37 s', '> Gancho contrario sobre impuestos', '> Do you know what the biggest tax haven is?', '- Pregunta con giro'):
            self.assertIn(parte, nota)
        self.assertIn('- [x] 2026-11-03 19:49 · https://www.instagram.com/reel/A1/ → [[2026-11-03 Miquel Gironès - Why America Is A Tax Haven 🇺🇸]] <!-- wa:M1 -->',
                      (VAULT / 'Ideas' / 'inbox.md').read_text())

    def test_without_analysis_the_note_still_keeps_the_transcript_and_says_why(self):
        r = enlaces.procesar('https://youtu.be/x', '', AHORA, descargar=descargar, transcribir=lambda v: 'hola', analizar=lambda t, i, p: ('', ''))
        nota = (VAULT / 'Contenido' / 'Para rehacer' / (r['nota'] + '.md')).read_text()
        self.assertFalse(r['analizado'])
        self.assertIn('> hola', nota); self.assertIn('Sin análisis', nota)
        self.assertIn('- [ ] ', (VAULT / 'Ideas' / 'inbox.md').read_text())        # no message id: the inbox is not touched

    def test_a_private_video_is_an_error_and_writes_nothing(self):
        def falla(url, carpeta): raise RuntimeError('login required')
        self.assertEqual(enlaces.procesar('https://www.instagram.com/reel/B2/', 'wa:M1', AHORA, descargar=falla)['error'], 'no he podido descargarlo (¿es privado?)')
        self.assertFalse((VAULT / 'Contenido' / 'Para rehacer').exists() and any((VAULT / 'Contenido' / 'Para rehacer').glob('*.md')))

    def test_the_same_video_twice_does_not_overwrite_the_first_note(self):
        hecho = lambda: enlaces.procesar('https://youtu.be/x', '', AHORA, descargar=descargar, transcribir=lambda v: '', analizar=lambda t, i, p: ('', ''))
        a, b = hecho(), hecho()
        self.assertNotEqual(a['nota'], b['nota'])
        self.assertTrue(b['nota'].endswith('(2)'))

    def test_the_video_is_left_where_the_content_system_looks_and_owner_s_words_reach_the_analysis(self):
        refs, vistos = pathlib.Path(_TMP.name) / 'full-visual-input', []
        with mock.patch.object(enlaces, 'referencias', lambda: refs):
            r = enlaces.procesar('https://www.instagram.com/reel/A1/', 'wa:M1', AHORA, pedido='para Norte', descargar=lambda u, c: descargar(u, c)[:1] + ({**INFO, 'id': 'DfQWdNKMRRe'},),
                                 transcribir=lambda v: 'hola', analizar=lambda t, i, p: vistos.append(p) or ('', ''))
        self.assertEqual(r['referencia'], 'refs-DfQWdNKMRRe')
        self.assertEqual(sorted(f.name for f in (refs / 'refs-DfQWdNKMRRe').iterdir()), ['DfQWdNKMRRe.mp4', 'ref.info.json', 'transcripcion.txt'])
        self.assertEqual(vistos, ['para Norte'])
        self.assertIn('pedido: "para Norte"', (VAULT / 'Contenido' / 'Para rehacer' / (r['nota'] + '.md')).read_text())

    def test_only_video_links_match(self):
        for url in ('https://www.instagram.com/reel/DfSijxqFOyt/?igsh=1', 'https://vm.tiktok.com/ZMabc/', 'https://www.youtube.com/shorts/abc', 'https://youtu.be/abc'):
            self.assertTrue(enlaces.VIDEO.search(url), url)
        for url in ('https://elpais.com/x', 'https://www.instagram.com/miquel_girones/'):
            self.assertFalse(enlaces.VIDEO.search(url), url)


class Conocimiento(unittest.TestCase):
    """04/11: tweets, long videos and web pages are filed in their project like a PDF and indexed in the Biblioteca."""
    def setUp(self):
        patcher = mock.patch.object(enlaces.inbox, 'NOTA', VAULT / 'Ideas' / 'inbox.md'); patcher.start(); self.addCleanup(patcher.stop)
        (VAULT / 'prompted').mkdir(exist_ok=True)
        for f in (VAULT / 'prompted').rglob('*.md'): f.unlink()
        (VAULT / 'Ideas' / 'inbox.md').write_text('# Inbox\n\n- [ ] 2026-11-04 10:00 · https://ejemplo.com/a <!-- wa:W1 -->\n')
        import documentos
        self.analisis = mock.patch.object(documentos, 'analizar', lambda texto, carpetas, pedido, que, pdf: self.vistos.append((que, texto)) or
                                          {'titulo': 'Cómo validar', 'proyecto': 'prompted', 'resumen': 'Validar antes de construir', 'cuerpo': '## Qué es\nUn ensayo.'})
        self.vistos = []; self.analisis.start(); self.addCleanup(self.analisis.stop)

    def test_a_web_page_keeps_its_readable_text_and_goes_to_its_project_and_the_biblioteca(self):
        pagina = ('<html><head><title>Validar</title><meta name="description" content="Un ensayo corto"></head><body><nav>Menú</nav>'
                  '<script>var x=1</script><article><p>' + 'Construye solo lo que alguien paga. ' * 10 + '</p></article><footer>©</footer></body></html>')
        with mock.patch.object(enlaces, 'bajar', lambda url, limite=0: (pagina.encode(), 'text/html; charset=utf-8')):
            r = enlaces.leer('https://ejemplo.com/a', 'wa:W1', AHORA)
        self.assertEqual((r['destino'], r['icono'], r['nota']), ('prompted', '🔗', '2026-11-03 Cómo validar'))
        que, texto = self.vistos[0]
        self.assertEqual(que, 'una página web'); self.assertIn('Un ensayo corto', texto); self.assertIn('Construye solo', texto)
        for fuera in ('Menú', 'var x', '©'): self.assertNotIn(fuera, texto)
        self.assertIn('[[2026-11-03 Cómo validar]] — Validar antes de construir · https://ejemplo.com/a', (VAULT / 'Ideas' / 'Biblioteca.md').read_text())
        self.assertIn('- [x] ', (VAULT / 'Ideas' / 'inbox.md').read_text())

    def test_a_tweet_is_read_through_the_public_api(self):
        tuit = {'tweet': {'text': 'Lanza en una semana', 'author': {'name': 'Ana', 'screen_name': 'ana'}, 'quote': {'text': 'cita', 'author': {'screen_name': 'bob'}}}}
        with mock.patch.object(enlaces, 'bajar', lambda url, limite=0: (json.dumps(tuit).encode(), 'application/json')) as _:
            r = enlaces.leer('https://x.com/ana/status/123?s=20', '', AHORA)
        self.assertEqual(r['icono'], '🐦')
        self.assertIn('Lanza en una semana', self.vistos[0][1]); self.assertIn('Cita a @bob: cita', self.vistos[0][1])

    def test_an_instagram_photo_post_without_video_is_filed_from_its_caption(self):
        pagina = ('<meta property="og:title" content="QuantumDigest en Instagram: &quot;Cómo saltar entre realidades&quot;" />'
                  '<meta property="og:description" content="quantumdigest el 10 de octubre: &quot;Cómo saltar entre realidades. En 1993 Vadim Zeland…&quot;" />')
        with mock.patch.object(enlaces, 'procesar', lambda *a, **k: {'error': 'no he podido descargarlo (¿es privado?)'}), \
             mock.patch.object(enlaces, 'bajar', lambda url, limite=0: (pagina.encode(), 'text/html')):
            r = enlaces.leer('https://www.instagram.com/p/ABC/?img_index=3', 'wa:W1', AHORA)
        self.assertEqual((r['icono'], r['destino']), ('🖼️', 'prompted'))
        self.assertEqual(self.vistos[0][0], 'un post de Instagram con fotos'); self.assertIn('Vadim Zeland', self.vistos[0][1])
        with mock.patch.object(enlaces, 'procesar', lambda *a, **k: {'error': 'no he podido descargarlo (¿es privado?)'}):   # a reel stays as it was
            self.assertEqual(enlaces.leer('https://www.instagram.com/reel/ABC/', '', AHORA)['destino'], 'Para rehacer')

    def test_what_cannot_be_read_stays_pending(self):
        with mock.patch.object(enlaces, 'bajar', lambda url, limite=0: (b'<html><body>Inicia sesion</body></html>', 'text/html')):
            r = enlaces.leer('https://ejemplo.com/a', 'wa:W1', AHORA)
        self.assertIn('queda pendiente', r['error'])
        self.assertIn('- [ ] ', (VAULT / 'Ideas' / 'inbox.md').read_text())


if __name__ == '__main__':
    unittest.main()
