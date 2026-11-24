"""inbox/documentos.py against a temporary vault with fake text extraction and analysis: never Owner's vault or Claude."""
import datetime
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

AQUI = pathlib.Path(__file__).resolve().parent
_TMP = tempfile.TemporaryDirectory()
VAULT = pathlib.Path(_TMP.name) / 'vault'
sys.path.insert(0, str(AQUI))
documentos = None


def setUpModule():
    # Imported here, like test_enlaces.py: the shared inbox module must never point at Owner's real Workspace.
    global documentos
    if 'inbox' not in sys.modules: os.environ['PROMPTED_OS_DATA_DIR'] = str(pathlib.Path(_TMP.name) / 'ws')
    import documentos as d
    documentos = d

AHORA = datetime.datetime(2026, 11, 4, 9, 30)
ANALISIS = {'titulo': 'Trading Info-Business Blueprint', 'proyecto': 'norte-capital', 'resumen': 'Cómo monetizar una audiencia de traders',
            'cuerpo': '## Qué es\nUn sistema en 4 fases.\n## Ideas clave\n- High-ticket con llamada'}


class Documentos(unittest.TestCase):
    def setUp(self):
        for carpeta in ('Ideas', 'Contenido', 'Cartera', 'norte-capital', 'prompted'): (VAULT / carpeta).mkdir(parents=True, exist_ok=True)
        patcher = mock.patch.object(documentos.inbox, 'NOTA', VAULT / 'Ideas' / 'inbox.md'); patcher.start(); self.addCleanup(patcher.stop)
        (VAULT / 'Ideas' / 'inbox.md').write_text('# Inbox\n\n- [ ] 2026-11-04 09:29 · para Norte <!-- wa:M1 -->\n')
        self.pdf = pathlib.Path(_TMP.name) / '2026-11-04-0929-wa-M1.pdf'; self.pdf.write_bytes(b'%PDF-1.4 falso')
        for f in VAULT.rglob('research/*'): f.unlink()

    def test_the_pdf_is_filed_in_its_project_with_its_note_and_the_inbox_line_is_ticked(self):
        vistos = []
        r = documentos.procesar(self.pdf, 'wa:M1', AHORA, pedido='para Norte', texto_pdf=lambda p: 'texto',
                                analizar=lambda texto, carpetas, pedido, que, pdf: vistos.append((carpetas, pedido)) or ANALISIS)
        self.assertEqual(r, {'nota': '2026-11-04 Trading Info-Business Blueprint', 'proyecto': 'norte-capital', 'resumen': 'Cómo monetizar una audiencia de traders',
                             'analizado': True, 'ruta': 'norte-capital/research/2026-11-04 Trading Info-Business Blueprint.md'})
        self.assertEqual(vistos, [(['norte-capital', 'prompted'], 'para Norte')])      # Ideas, Contenido and Cartera are not projects
        carpeta = VAULT / 'norte-capital' / 'research'
        self.assertEqual((carpeta / f"{r['nota']}.pdf").read_bytes(), b'%PDF-1.4 falso')
        nota = (carpeta / f"{r['nota']}.md").read_text()
        for parte in ('proyecto: norte-capital', 'pedido: "para Norte"', '> Cómo monetizar una audiencia de traders', f"![[{r['nota']}.pdf]]", '- High-ticket con llamada'):
            self.assertIn(parte, nota)
        self.assertIn(f"- [x] 2026-11-04 09:29 · para Norte → [[{r['nota']}]] <!-- wa:M1 -->", (VAULT / 'Ideas' / 'inbox.md').read_text())

    def test_an_unknown_project_or_no_analysis_goes_to_ideas_with_its_first_lines_and_the_whatsapp_name(self):
        r = documentos.procesar(self.pdf, '', AHORA, original='Plan secreto.pdf', texto_pdf=lambda p: 'Capítulo 1. El método', analizar=lambda *a: {})
        self.assertEqual((r['proyecto'], r['nota'], r['analizado']), ('Ideas', '2026-11-04 Plan secreto', False))
        nota = (VAULT / 'Ideas' / 'research' / '2026-11-04 Plan secreto.md').read_text()
        self.assertIn('Sin análisis', nota); self.assertIn('> Capítulo 1. El método', nota)
        r = documentos.procesar(self.pdf, '', AHORA, texto_pdf=lambda p: '', analizar=lambda *a: {**ANALISIS, 'proyecto': '../fuera'})
        self.assertEqual(r['ruta'], 'Ideas/research/2026-11-04 Trading Info-Business Blueprint.md')   # never outside the vault's projects

    def test_the_same_document_twice_does_not_overwrite_the_first(self):
        hecho = lambda: documentos.procesar(self.pdf, '', AHORA, texto_pdf=lambda p: '', analizar=lambda *a: ANALISIS)
        a, b = hecho(), hecho()
        self.assertTrue(b['nota'].endswith('(2)')); self.assertNotEqual(a['nota'], b['nota'])
        self.assertTrue((VAULT / 'norte-capital' / 'research' / f"{b['nota']}.pdf").exists())

    def test_a_missing_file_is_an_error(self):
        self.assertEqual(documentos.procesar(pathlib.Path(_TMP.name) / 'no.pdf', '', AHORA)['error'], 'no encuentro el archivo')


if __name__ == '__main__':
    unittest.main()
