"""Inbox de ideas contra un Telegram falso. Run: python3 -m unittest discover -s inbox
Never Owner's real data folder, vault or bot: everything lives in a temporary folder."""
import json, os, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

TMP = tempfile.TemporaryDirectory()
ROOT = Path(TMP.name); DATA = ROOT / 'Workspace'; VAULT = ROOT / 'vault'
DATA.mkdir(); VAULT.mkdir()
DUENO, CHAT, OTRO = 111, 111, 999
(DATA / 'configuracion.json').write_text(json.dumps({'vault': str(VAULT), 'inbox': {'nota': 'Ideas/inbox.md'}}))
(DATA / 'telegram.json').write_text(json.dumps({'chatId': CHAT, 'userId': DUENO, 'name': 'Owner', 'since': '2026-10-30'}))


class Fake(BaseHTTPRequestHandler):
    """The few Telegram methods the inbox uses. getUpdates returns everything from the offset on, like the real one."""
    updates, sent = [], []

    def log_message(self, *a): pass

    def reply(self, body, kind='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200); self.send_header('Content-Type', kind); self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith('/file/botTEST/'): return self.reply(b'contenido ' + self.path.encode(), 'application/octet-stream')
        self.send_response(404); self.end_headers()

    def do_POST(self):
        assert self.path.startswith('/botTEST/'), self.path
        method, body = self.path.split('/')[-1], json.loads(self.rfile.read(int(self.headers['Content-Length'])) or b'{}')
        if method == 'getUpdates':
            return self.reply({'ok': True, 'result': [u for u in Fake.updates if u['update_id'] >= body.get('offset', 0)]})
        if method == 'getFile':
            ext = {'voz': 'oga', 'foto': 'jpg'}.get(body['file_id'].split('-')[0], 'bin')
            return self.reply({'ok': True, 'result': {'file_id': body['file_id'], 'file_path': f"cosas/{body['file_id']}.{ext}", 'file_size': 10}})
        if method == 'sendMessage':
            Fake.sent.append(body); return self.reply({'ok': True, 'result': {'message_id': 5000 + len(Fake.sent)}})
        self.reply({'ok': False, 'description': 'método desconocido ' + method})


server = HTTPServer(('127.0.0.1', 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ.update({'PROMPTED_OS_DATA_DIR': str(DATA), 'PROMPTED_OS_INBOX_TEST_API': f'http://127.0.0.1:{server.server_port}', 'PROMPTED_OS_INBOX_TEST_TOKEN': 'TEST'})
import inbox  # noqa: E402  (after the environment points at the fake)

NOTA = VAULT / 'Ideas' / 'inbox.md'
# 30/10/2026 18:11 in Madrid (UTC+1 after the clock change)
T = 1793380260


def tearDownModule():
    server.shutdown(); TMP.cleanup()


def msg(mid, uid=DUENO, **campos):
    return {'message_id': mid, 'date': T, 'chat': {'id': uid, 'type': 'private'}, 'from': {'id': uid, 'first_name': 'X'}, **campos}


class Inbox(unittest.TestCase):
    def setUp(self):
        Fake.updates, Fake.sent = [], []
        for f in (NOTA, inbox.STATE): f.unlink(missing_ok=True)

    def entra(self, *mensajes):
        base = len(Fake.updates)
        Fake.updates += [{'update_id': 100 + base + i, 'message': m} for i, m in enumerate(mensajes)]
        return inbox.pasada()

    def test_texto_con_fecha_hora_y_acuse_de_una_linea(self):
        self.assertEqual(self.entra(msg(1, text='Sauron: fondo de IA que se mejora solo')), 1)
        texto = NOTA.read_text()
        self.assertTrue(texto.startswith('# Inbox de ideas'))
        self.assertIn('- [ ] 2026-10-30 18:11 · Sauron: fondo de IA que se mejora solo <!-- tg:1 -->\n', texto)
        self.assertEqual([s['text'] for s in Fake.sent], ['📥 Apuntado: «Sauron: fondo de IA que se mejora solo»'])
        self.assertEqual(Fake.sent[0]['chat_id'], CHAT)
        self.assertNotIn('\n', Fake.sent[0]['text'])

    def test_sin_duplicados_aunque_se_pierda_el_estado(self):
        self.entra(msg(1, text='idea uno'))
        inbox.STATE.unlink()                       # Telegram vuelve a dar el mismo mensaje
        self.assertEqual(inbox.pasada(), 0)
        self.assertEqual(NOTA.read_text().count('tg:1 -->'), 1)
        self.assertEqual(len(Fake.sent), 1)        # y no se vuelve a contestar
        self.assertEqual(inbox.pasada(), 0)         # con estado, ni siquiera lo vuelve a pedir

    def test_solo_owner(self):
        self.assertEqual(self.entra(msg(1, uid=OTRO, text='hola bot'), {**msg(2, text='en un grupo'), 'chat': {'id': -5, 'type': 'group'}}), 0)
        self.assertFalse(NOTA.exists()); self.assertEqual(Fake.sent, [])
        self.assertEqual(json.loads(inbox.STATE.read_text())['offset'], 102)   # leídos y descartados

    def test_varias_ideas_un_solo_acuse(self):
        self.assertEqual(self.entra(msg(1, text='uno'), msg(2, text='dos\ncon segunda línea'), msg(3, text='https://example.com/reel')), 3)
        texto = NOTA.read_text()
        self.assertIn('· dos\n  con segunda línea <!-- tg:2 -->', texto)
        self.assertIn('· https://example.com/reel <!-- tg:3 -->', texto)
        self.assertEqual([s['text'] for s in Fake.sent], ['📥 Apuntadas 3 ideas en tu inbox.'])

    def test_nota_de_voz_transcrita(self):
        with mock.patch.object(inbox, 'transcribe', return_value='la entrada no es lo importante') as t:
            self.entra(msg(4, voice={'file_id': 'voz-1', 'duration': 12}))
        self.assertTrue(t.call_args[0][0].name.endswith('-4.oga'))
        self.assertIn('- [ ] 2026-10-30 18:11 · 🎙️ la entrada no es lo importante\n  audio: [[inbox-adjuntos/2026-10-30-1811-4.oga]] <!-- tg:4 -->\n', NOTA.read_text())
        self.assertEqual(Fake.sent[0]['text'], '📥 Apuntado: «la entrada no es lo importante»')

    def test_voz_sin_transcribir_se_guarda_igual(self):
        with mock.patch.object(inbox, 'transcribe', return_value=''):
            self.entra(msg(5, voice={'file_id': 'voz-2'}))
        self.assertIn('(sin transcribir) <!-- tg:5 -->', NOTA.read_text())
        self.assertEqual(Fake.sent[0]['text'], '📥 Apuntado: «nota de voz (sin transcribir)»')

    def test_foto_con_pie_y_reenvio(self):
        self.entra(msg(6, photo=[{'file_id': 'foto-peq'}, {'file_id': 'foto-grande'}], caption='setup brutal estilo Idiocracy',
                       forward_origin={'type': 'channel', 'chat': {'title': 'Canal X'}}))
        adj = VAULT / 'Ideas' / 'inbox-adjuntos' / '2026-10-30-1811-6.jpg'
        self.assertIn(b'foto-grande', adj.read_bytes())
        self.assertIn('(reenviado de Canal X) · setup brutal estilo Idiocracy\n  ![[inbox-adjuntos/2026-10-30-1811-6.jpg]] <!-- tg:6 -->', NOTA.read_text())

    def test_start_saluda_y_no_se_apunta(self):
        self.assertEqual(self.entra(msg(7, text='/start')), 0)
        self.assertFalse(NOTA.exists())
        self.assertEqual(len(Fake.sent), 1); self.assertIn('Inbox listo', Fake.sent[0]['text'])

    def test_apuntar_desde_whatsapp_sin_duplicados(self):
        d = {'id': 'wa:3EB0ABC', 'fecha': T, 'texto': '', 'oido': 'reel de la entrada', 'adjuntos': [{'tipo': 'audio', 'archivo': '/x/2026-10-30-1811-wa.ogg'}]}
        self.assertEqual(inbox.apuntar(d), {'escrita': True, 'resumen': 'reel de la entrada', 'contexto': '🎙️ reel de la entrada audio: [[inbox-adjuntos/2026-10-30-1811-wa.ogg]]'})
        self.assertEqual(inbox.apuntar(d)['escrita'], False)
        self.assertIn('· 🎙️ reel de la entrada\n  audio: [[inbox-adjuntos/2026-10-30-1811-wa.ogg]] <!-- wa:3EB0ABC -->\n', NOTA.read_text())
        self.assertEqual(inbox.apuntar({'id': 'wa:F1', 'fecha': T, 'texto': 'mira esto', 'origen': 'Ana', 'adjuntos': [{'tipo': 'foto', 'archivo': 'f.jpg'}]})['resumen'], 'mira esto')
        self.assertIn('(reenviado de Ana) · mira esto\n  ![[inbox-adjuntos/f.jpg]] <!-- wa:F1 -->', NOTA.read_text())

    def test_rafaga_de_whatsapp_es_una_sola_idea(self):
        """12/11: «Tengo que implementar esto en lumen» + «Esto es brutal» + «Y combinado con el dock» = una línea."""
        a = lambda i, t, texto, origen='': inbox.apuntar({'id': 'wa:' + i, 'fecha': t, 'texto': texto, 'origen': origen})
        a('R1', T, 'Tengo que implementar esto en lumen'); a('R2', T + 40, 'Esto es brutal'); r = a('R3', T + 150, 'Y combinado con el dock')
        lineas = [l for l in NOTA.read_text().split('\n') if l.startswith('- [')]
        self.assertEqual(len(lineas), 1)
        self.assertTrue(lineas[0].endswith('· Tengo que implementar esto en lumen <!-- wa:R1 --> · Esto es brutal <!-- wa:R2 --> · Y combinado con el dock <!-- wa:R3 -->'))
        self.assertEqual(r['contexto'], 'Tengo que implementar esto en lumen · Esto es brutal · Y combinado con el dock')
        self.assertFalse(a('R2', T + 160, 'Esto es brutal')['escrita'], 'still no duplicates')
        a('R4', T + 150 + 121, 'Otra idea, más de dos minutos después')
        a('R5', T + 150 + 130, 'mira esto', 'Ana')
        lineas = [l for l in NOTA.read_text().split('\n') if l.startswith('- [')]
        self.assertEqual(len(lineas), 3, 'a pause over two minutes and a forwarded message start their own lines')

    def test_rafaga_no_se_une_a_lo_ya_archivado(self):
        a = lambda i, t, texto: inbox.apuntar({'id': 'wa:' + i, 'fecha': t, 'texto': texto})
        a('S1', T, 'primera')
        NOTA.write_text(NOTA.read_text().replace('- [ ] ', '- [x] '))
        a('S2', T + 30, 'segunda')
        self.assertEqual(len([l for l in NOTA.read_text().split('\n') if l.startswith('- [ ] ')]), 1, 'a ticked line never takes new text')

    def test_sin_vincular_no_hace_nada(self):
        with mock.patch.object(inbox, 'dueno', return_value=None):
            self.assertEqual(self.entra(msg(8, text='x')), 0)
        self.assertEqual(Fake.sent, []); self.assertFalse(inbox.STATE.exists())   # no consume nada


if __name__ == '__main__':
    unittest.main()
