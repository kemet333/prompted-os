#!/usr/bin/env python3
"""Inbox de ideas: lo que Owner manda al chat privado de un bot de Telegram cae en una nota del vault, con fecha y hora.

Sustituye a su chat de WhatsApp consigo mismo (30/10/2026: «haz de este chat una vía directa contigo… una especie de inbox»).
Un bot propio y no el de Alfred: el de Alfred lo lee la app y, con la app cerrada, Telegram le guarda los toques de botones;
si este servicio leyera ese bot se los comería. Este lo lee solo él, cada minuto (launchd com.prompted.os.inbox), con la app
abierta o cerrada.

- Texto, enlaces y reenvíos tal cual; notas de voz transcritas en el Mac con whisper.cpp (el mismo modelo que la app); fotos,
  vídeos y archivos se guardan junto a la nota y se enlazan.
- Cada entrada es «- [ ] AAAA-MM-DD HH:MM · texto <!-- tg:N -->»: Claude la lee al empezar una sesión, la mete donde toca y
  la marca [x]. La marca tg:N evita duplicados aunque se pierda el estado.
- Solo acepta a Owner: el mismo usuario de Telegram que vinculó a Alfred (Workspace/telegram.json); el resto se ignora.
- Responde con un acuse de una línea por pasada.

configuracion.json (Workspace): "inbox": {"nota": "<ruta dentro del vault>", "tokenArchivo": "~/…/.env"}. El token es la
línea TELEGRAM_BOT_TOKEN de ese archivo, o el llavero (servicio owner-inbox-telegram). Sin nota o sin token, no hace nada.

Uso: python3 inbox.py                     una pasada
     python3 inbox.py --estado            qué lee, a quién acepta y dónde escribe (sin el token)
     python3 inbox.py apuntar < datos.json   apunta un mensaje de otro canal (WhatsApp); ver apuntar()
     python3 inbox.py transcribir audio      nota de voz → texto
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request

try:
    from zoneinfo import ZoneInfo
    MADRID = ZoneInfo('Europe/Madrid')
except Exception:
    MADRID = None

HOME = pathlib.Path.home()
# Tests point everything at a temporary folder and a fake Telegram (test_inbox.py); never Owner's real data.
DATA = pathlib.Path(os.environ.get('PROMPTED_OS_DATA_DIR') or HOME / 'Library/Application Support/com.prompted.os/Workspace')
API = os.environ.get('PROMPTED_OS_INBOX_TEST_API') or 'https://api.telegram.org'
STATE = DATA / 'inbox' / 'estado.json'
LOG = DATA / 'inbox' / 'registro.log'


def load(path, default):
    try: return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError): return default


CONFIG = load(DATA / 'configuracion.json', {})


def ajuste(key, default=''):
    v = CONFIG
    for part in key.split('.'): v = v.get(part) if isinstance(v, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() else default


def ruta(key, default=None):
    v = ajuste(key)
    return pathlib.Path(os.path.expanduser(v)) if v else default


VAULT = ruta('vault')
NOTA = (VAULT / ajuste('inbox.nota')) if VAULT and ajuste('inbox.nota') else None
ADJUNTOS = NOTA.parent / 'inbox-adjuntos' if NOTA else None
WHISPER = ruta('whisper.modelo', DATA.parent / 'modelos' / 'ggml-large-v3-turbo-q5_0.bin')
VOCABULARIO = ajuste('whisper.vocabulario', 'Alfred, Claude, Norte, Beta.')
CABECERA = ('# Inbox de ideas\n\n'
            'Lo que Owner manda al grupo de WhatsApp «Alfred» (texto, notas de voz transcritas, fotos, enlaces). '
            'Claude lo lee al empezar cada sesión: mete cada idea donde toca y la marca [x] con un enlace a dónde fue.\n\n')


def log(msg):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as f: f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def token():
    if os.environ.get('PROMPTED_OS_INBOX_TEST_TOKEN'): return os.environ['PROMPTED_OS_INBOX_TEST_TOKEN']
    archivo = ruta('inbox.tokenArchivo')
    if archivo and archivo.exists():
        for line in archivo.read_text().splitlines():
            if line.startswith('TELEGRAM_BOT_TOKEN='): return line.split('=', 1)[1].strip().strip('"\'')
    try:
        p = subprocess.run(['/usr/bin/security', 'find-generic-password', '-s', 'owner-inbox-telegram', '-w'], capture_output=True, text=True, timeout=10)
        return p.stdout.strip() if p.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError): return ''


def dueno():
    """Owner's Telegram user: the one who linked Alfred in the app."""
    uid = (load(DATA / 'telegram.json', {}) or {}).get('userId')
    return uid if isinstance(uid, int) else None


class Telegram:
    def __init__(self, tok): self.tok = tok

    def call(self, method, **params):
        req = urllib.request.Request(f'{API}/bot{self.tok}/{method}', json.dumps(params).encode(), {'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=40) as r: out = json.load(r)
        except urllib.error.HTTPError as e: out = json.load(e)
        if not out.get('ok'): raise RuntimeError(f"{method}: {out.get('description', 'error')}")
        return out['result']

    def download(self, file_id, stem):
        info = self.call('getFile', file_id=file_id)
        if (info.get('file_size') or 0) > 20_000_000: raise RuntimeError('pesa más de 20 MB')
        ext = (pathlib.Path(info['file_path']).suffix or '.bin').lower()
        ADJUNTOS.mkdir(parents=True, exist_ok=True)
        dest = ADJUNTOS / f'{stem}{ext}'
        with urllib.request.urlopen(f"{API}/file/bot{self.tok}/{info['file_path']}", timeout=120) as r: dest.write_bytes(r.read())
        return dest


def tool(name):
    return next((p for p in (f'/opt/homebrew/bin/{name}', f'/usr/local/bin/{name}') if os.path.exists(p)), None) or shutil.which(name)


def transcribe(audio, idioma='es'):
    """Voice note → Spanish text, on this Mac (the app's Whisper model). '' if it cannot. A reel may be in any language: 'auto'."""
    whisper, ffmpeg = tool('whisper-cli'), tool('ffmpeg')
    if not WHISPER.exists() or not whisper or not ffmpeg: return ''
    with tempfile.TemporaryDirectory() as tmp:
        wav = pathlib.Path(tmp) / 'nota.wav'
        subprocess.run([ffmpeg, '-loglevel', 'error', '-y', '-i', str(audio), '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', str(wav)], check=True, timeout=120)
        out = subprocess.run([whisper, '-m', str(WHISPER), '-l', idioma, '-nt', '-np', *(['--prompt', VOCABULARIO] if idioma == 'es' else []), '-f', str(wav)],
                             capture_output=True, text=True, timeout=600, check=True)
    return ' '.join(out.stdout.split())


def marca(mid): return f'<!-- tg:{mid} -->' if not str(mid).startswith('wa:') else f'<!-- {mid} -->'


# 12/11/2026, Owner: «Pics», «POCS», «NAKED POCS» o «Esto es brutal» + «Y combinado con el dock» llegaban como ideas sueltas.
# What he sends in a burst (each message at most JUNTAR_S after the previous one) is one idea: the next message joins the
# last line while it is still the last thing in the inbox and still pending. Every message keeps its own marker.
JUNTAR_S = 120
ULTIMA = DATA / 'inbox' / 'ultima.json'


def _juntable(ya, stamp):
    u = load(ULTIMA, {})
    m, hora = u.get('marca'), u.get('hora')
    if not m or not isinstance(hora, (int, float)) or not 0 <= stamp.timestamp() - hora <= JUNTAR_S: return False
    lineas = ya.rstrip('\n').split('\n')
    if not lineas[-1].endswith(m): return False
    inicio = next((l for l in reversed(lineas) if l.startswith('- [')), '')
    return inicio.startswith('- [ ] ') and '(reenviado de' not in inicio


def contexto(mid):
    """The whole pending entry that holds this message, without markers (what the burst said around a link)."""
    ya = NOTA.read_text() if NOTA and NOTA.exists() else ''
    for entrada in re.split(r'\n(?=- \[)', ya):
        if marca(mid) in entrada:
            texto = re.sub(r'^- \[[ x]\] \S+ \S+ · ', '', entrada.strip())
            return re.sub(r'\s*<!--.*?-->', '', ' '.join(l.strip() for l in texto.split('\n'))).strip()
    return ''


def escribir(mid, stamp, partes, origen='', juntar=False):
    """Appends one entry unless its marker is already there. True if it wrote it. With juntar (WhatsApp), a message that
    follows the previous one within JUNTAR_S joins its line instead of starting a new idea."""
    ya = NOTA.read_text() if NOTA.exists() else ''
    if marca(mid) in ya: return False
    cuerpo = '\n'.join(partes).replace('\n', '\n  ')
    if not ya: NOTA.parent.mkdir(parents=True, exist_ok=True); NOTA.write_text(CABECERA); ya = CABECERA
    if juntar and not origen and _juntable(ya, stamp):
        tmp = NOTA.with_name('.' + NOTA.name + '.tmp')
        tmp.write_text(ya.rstrip('\n') + f" · {cuerpo} {marca(mid)}\n"); tmp.replace(NOTA)
    else:
        with NOTA.open('a') as f: f.write(f"- [ ] {stamp:%Y-%m-%d %H:%M}{f' (reenviado de {origen})' if origen else ''} · {cuerpo} {marca(mid)}\n")
    if juntar:
        ULTIMA.parent.mkdir(parents=True, exist_ok=True)
        ULTIMA.write_text(json.dumps({'marca': marca(mid), 'hora': stamp.timestamp()}))
    return True


def entrada(tg, msg, stamp):
    """One message → (its parts, origin, short text for the acknowledgement), or None if there is nothing to keep."""
    texto = (msg.get('text') or msg.get('caption') or '').strip()
    partes, resumen, stem = [], texto, f"{stamp:%Y-%m-%d-%H%M}-{msg['message_id']}"
    fo = msg.get('forward_origin') or {}
    origen = fo.get('sender_user_name') or (fo.get('sender_user') or {}).get('first_name') or (fo.get('chat') or {}).get('title') or ''
    if texto: partes.append(texto)
    try:
        if msg.get('voice') or msg.get('audio'):
            nota = tg.download((msg.get('voice') or msg.get('audio'))['file_id'], stem)
            try: oido = transcribe(nota)
            except Exception as e:
                oido = ''; log(f'whisper: {type(e).__name__}')
            if oido:
                partes.insert(0, f'🎙️ {oido}'); resumen = resumen or oido
            partes.append(f'audio: [[inbox-adjuntos/{nota.name}]]' + ('' if oido else ' (sin transcribir)'))
            resumen = resumen or 'nota de voz (sin transcribir)'
        for campo, que in (('photo', 'foto'), ('video', 'vídeo'), ('video_note', 'vídeo'), ('document', 'archivo')):
            f = msg.get(campo)
            if not f: continue
            dest = tg.download((f[-1] if isinstance(f, list) else f)['file_id'], stem)
            partes.append(f'![[inbox-adjuntos/{dest.name}]]' if campo == 'photo' else f'{que}: [[inbox-adjuntos/{dest.name}]]')
            resumen = resumen or que
    except Exception as e:   # the words are kept even if the attachment is not
        partes.append(f'(adjunto no descargado: {str(e)[:80]})'); resumen = resumen or 'adjunto'
    if not partes: return None
    return partes, origen, resumen


def acuse(resumenes):
    """One line, read in a second."""
    if len(resumenes) == 1:
        r = ' '.join(resumenes[0].split())
        return f"📥 Apuntado: «{r if len(r) <= 60 else r[:59].rstrip() + '…'}»"
    return f'📥 Apuntadas {len(resumenes)} ideas en tu inbox.'


def pasada():
    """Reads what is new, writes it down and acknowledges it. Returns how many entries it wrote."""
    tok, uid = token(), dueno()
    if not NOTA or not tok or uid is None: return 0
    st = load(STATE, {}) or {}
    tg = Telegram(tok)
    try: updates = tg.call('getUpdates', offset=int(st.get('offset') or 0), timeout=0, allowed_updates=['message'])
    except (urllib.error.URLError, OSError, RuntimeError) as e:
        log(f'sin respuesta de Telegram: {type(e).__name__}'); return 0
    escritas, por_chat = 0, {}
    for u in updates:
        msg = u.get('message') or {}
        if msg and msg.get('chat', {}).get('type') == 'private' and msg.get('from', {}).get('id') == uid:
            chat = msg['chat']['id']
            if re.fullmatch(r'/start(@\w+)?', (msg.get('text') or '').strip()):
                por_chat.setdefault(chat, []).append(None)
            elif not NOTA.exists() or marca(msg['message_id']) not in NOTA.read_text():
                stamp = datetime.datetime.fromtimestamp(msg['date'], MADRID)
                hecho = entrada(tg, msg, stamp)
                if hecho and escribir(msg['message_id'], stamp, hecho[0], hecho[1]):
                    escritas += 1
                    por_chat.setdefault(chat, []).append(hecho[2])
        st['offset'] = u['update_id'] + 1
        STATE.parent.mkdir(parents=True, exist_ok=True); STATE.write_text(json.dumps(st))
    for chat, resumenes in por_chat.items():
        ideas = [r for r in resumenes if r]
        texto = acuse(ideas) if ideas else 'Inbox listo: todo lo que me mandes aquí entra en tu sistema.'
        try: tg.call('sendMessage', chat_id=chat, text=texto, disable_web_page_preview=True)
        except Exception as e: log(f'acuse no enviado: {type(e).__name__}')
    if escritas: log(f'apuntadas {escritas}')
    return escritas


def apuntar(d):
    """For other channels (the WhatsApp bridge, whatsapp/puente.mjs): one message already downloaded into ADJUNTOS.
    d = {id: 'wa:…', fecha: unix seconds, texto, origen, oido (already transcribed), adjuntos: [{tipo, archivo}]}."""
    if not NOTA: return {'escrita': False, 'motivo': 'sin nota'}
    stamp = datetime.datetime.fromtimestamp(int(d['fecha']), MADRID)
    partes, resumen = [], (d.get('texto') or '').strip()
    if resumen: partes.append(resumen)
    oido = (d.get('oido') or '').strip()
    for a in d.get('adjuntos') or []:
        nombre = pathlib.Path(a['archivo']).name
        if a['tipo'] == 'audio':
            if oido: partes.insert(0, f'🎙️ {oido}'); resumen = resumen or oido
            partes.append(f'audio: [[inbox-adjuntos/{nombre}]]' + ('' if oido else ' (sin transcribir)'))
            resumen = resumen or 'nota de voz (sin transcribir)'
        else:
            partes.append(f'![[inbox-adjuntos/{nombre}]]' if a['tipo'] == 'foto' else f"{a['tipo']}: [[inbox-adjuntos/{nombre}]]")
            resumen = resumen or a['tipo']
    if not partes: return {'escrita': False, 'motivo': 'vacío'}
    escrita = escribir(str(d['id']), stamp, partes, d.get('origen') or '', juntar=True)
    return {'escrita': escrita, 'resumen': resumen, **({'contexto': contexto(str(d['id']))} if escrita else {})}


def estado():
    return {'nota': str(NOTA) if NOTA else None, 'adjuntos': str(ADJUNTOS) if ADJUNTOS else None, 'token': bool(token()), 'dueno': dueno() is not None,
            'whisper': WHISPER.exists(), 'offset': (load(STATE, {}) or {}).get('offset', 0)}


if __name__ == '__main__':
    import fcntl
    if '--estado' in sys.argv:
        print(json.dumps(estado(), ensure_ascii=False)); sys.exit(0)
    if sys.argv[1:2] == ['transcribir']:
        try: print(json.dumps({'texto': transcribe(sys.argv[2])}, ensure_ascii=False))
        except Exception as e: print(json.dumps({'texto': '', 'error': type(e).__name__}))
        sys.exit(0)
    if sys.argv[1:2] == ['apuntar']:
        print(json.dumps(apuntar(json.load(sys.stdin)), ensure_ascii=False)); sys.exit(0)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.parent / 'inbox.lock', 'w') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: sys.exit(0)   # the previous pass is still transcribing
        pasada()
