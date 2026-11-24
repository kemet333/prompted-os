#!/usr/bin/env python3
"""Equipo de agentes de Owner: planificar → aprobar → construir → probar → revisar → integrar.

Una tarea a la vez por proyecto, en worktrees aislados bajo ~/Equipo. El estado de las tareas vive en
los tableros Kanban de Hermes (uno por proyecto); este programa solo ejecuta tareas que Owner aprobó
dentro de un lote (registro con hash en state/approved.json). Nunca toca la carpeta de trabajo de
Owner, ni main, ni hace push, despliegues, publicaciones o gastos.

Ejecutar con el Python propio del motor (entorno.sh: dependencias de Hermes, SQLite al día):
  ~/Equipo/venv/bin/python equipo.py <setup|plan|proposals|approve|run|status|pause|resume>
"""
import argparse, concurrent.futures, hashlib, json, os, re, shutil, signal, socket, subprocess, sys, threading, time, unicodedata, uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path, PurePosixPath
from statistics import median
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plataforma as pf

def _config_path():
    """EQUIPO_CONFIG, the repository's projects.json (this computer's owner), or the one the app's setup wrote."""
    if os.environ.get('EQUIPO_CONFIG'): return Path(os.environ['EQUIPO_CONFIG'])
    here = HERE / 'projects.json'
    return here if here.exists() else pf.app_data() / 'equipo' / 'projects.json'

CONFIG = json.loads(_config_path().read_text())
ROOT = Path(os.path.expanduser(CONFIG.get('root') or '~/Equipo'))
STATE = ROOT / 'state'
LANE = 'equipo-constructor'          # not a Hermes profile: the native dispatcher leaves these cards alone
def _zona():
    """The owner's time zone (projects.json 'zona'), else this computer's. Windows without tzdata has no zone database."""
    try: return ZoneInfo(CONFIG['zona']) if CONFIG.get('zona') else datetime.now().astimezone().tzinfo
    except Exception: return datetime.now().astimezone().tzinfo
MADRID = _zona()

# Task boards: Hermes' Kanban where it is installed (and not turned off with "tablero": "propio"), else the team's own.
HERMES_AGENT = Path(os.path.expanduser(CONFIG.get('hermes') or Path.home() / '.hermes' / 'hermes-agent'))
try:
    if CONFIG.get('tablero') == 'propio' or not HERMES_AGENT.is_dir(): raise ImportError('sin Hermes')
    sys.path.insert(0, str(HERMES_AGENT))
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc
    HERMES = True
except ImportError:
    os.environ.setdefault('EQUIPO_TABLEROS', str(STATE / 'tableros'))
    import tablero as kb
    kbc = kb
    HERMES = False

# The owner's name, from the app's profile. Prompts and notices are written with NOMBRE_EN_CODIGO and say the real name.
NOMBRE_EN_CODIGO = 'Owner'
try: OWNER = (json.loads((pf.app_data() / 'perfil' / 'perfil.json').read_text()).get('nombre') or '').strip()[:60] or NOMBRE_EN_CODIGO
except (OSError, ValueError, AttributeError): OWNER = NOMBRE_EN_CODIGO

def con_nombre(text):
    """The text with the owner's real name (a no-op when it is the one written in the code)."""
    if OWNER == NOMBRE_EN_CODIGO or not isinstance(text, str): return text
    return re.sub(rf'\b{NOMBRE_EN_CODIGO}\b', OWNER, text)

def con_nombre_todo(value):
    if isinstance(value, str): return con_nombre(value)
    if isinstance(value, list): return [con_nombre_todo(v) for v in value]
    if isinstance(value, dict): return {k: con_nombre_todo(v) for k, v in value.items()}
    return value
LIMITS = CONFIG['limits']
CHILDREN = set()

class Stop(RuntimeError):
    pass

# ---------- the Mac asleep ----------
# Owner (15/10/2026): on battery the Mac sleeps with dark-wake slivers of a few seconds every quarter of an hour. Work
# started in a sliver froze mid-test and woke up "15 minutes later": six tasks were blocked for failures that
# were only sleep. time.monotonic() is mach_absolute_time on macOS and stops while asleep; time.time() does not.
SLEEP_GAP = 90
ARRANQUE_FALLIDO = re.compile(r'did not become healthy|EADDRINUSE')

class Asleep(Stop):
    """The Mac slept mid-task. Nothing failed: the task goes back to the queue without spending an attempt."""

def sleep_clock():
    return time.time() - time.monotonic()      # grows only while the Mac sleeps

def lid_will_sleep(): return pf.lid_will_sleep()

# 01/11: a Remotion render from another session held the load at 40-57 on 8 cores; Norte's demo server missed its 15 s
# health check twice and two healthy tasks spent all their attempts. Above this load per core nothing new starts, and
# a test that runs out of time while the Mac is saturated goes back to the queue (at most SATURADO_VECES times).
CARGA_MAX = 3.0
SATURADO_VECES = 3
TIEMPO_AGOTADO = re.compile(r'did not become healthy|EADDRINUSE|timed out after|ETIMEDOUT')

class Saturado(Stop):
    """Another process held the Mac at full load while testing: the work may be fine, so it does not spend an attempt."""

def carga():
    try: return os.getloadavg()[0] / (os.cpu_count() or 1)
    except (OSError, AttributeError): return 0.0

def saturado(): return carga() > CARGA_MAX

# sleep_clock(), checklist() and repaso_semanal() keep time.time(): they compare with real timestamps of the board and the files
def now(): return datetime.now(MADRID)
def today(): return now().strftime('%Y-%m-%d')
def digest(text): return hashlib.sha256(text.encode()).hexdigest()
def canonical(value): return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
def log(*parts): print(now().strftime('%H:%M:%S'), *parts, flush=True)

def load(path, default):
    try: return json.loads(Path(path).read_text())
    except (OSError, ValueError): return default

def save(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp'); tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n'); tmp.replace(path)

# ---------- processes ----------

def clean_env():
    keep = {'HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG', 'LC_ALL'}
    if pf.WINDOWS: keep |= {'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'SYSTEMROOT', 'SYSTEMDRIVE', 'COMSPEC', 'PATHEXT', 'TEMP', 'TMP', 'PROGRAMFILES', 'WINDIR'}
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env['PATH'] = pf.search_path()
    env.update(PYTHONDONTWRITEBYTECODE='1', NO_COLOR='1', CI='1')
    return env

def run(argv, cwd, timeout, out=None, stdin=None, heartbeat=None):
    """No shell. Kills the whole process group on timeout or when the claim is lost."""
    proc = subprocess.Popen(argv, cwd=cwd, env=clean_env(), stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **pf.session_kwargs())
    CHILDREN.add(proc)
    result = {}
    def reader():
        result['out'] = proc.communicate(stdin)[0]
    t = threading.Thread(target=reader, daemon=True); t.start()
    deadline = time.monotonic() + timeout
    try:
        while t.is_alive():
            t.join(1)
            if heartbeat and not heartbeat(): raise Stop('claim_lost')
            if time.monotonic() > deadline:
                pf.kill_tree(proc)
                t.join(5)
                if out: Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(result.get('out') or '')
                raise Stop('timeout')
    finally:
        pf.kill_tree(proc)
        CHILDREN.discard(proc)
    text = result.get('out') or ''
    if out: Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(text)
    return proc.returncode, text

def git(repo, *args, check=True):
    p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True, env=clean_env())
    if check and p.returncode: raise Stop('git_' + args[0] + ': ' + (p.stderr or p.stdout).strip()[:300])
    return p.stdout.strip()

CLAUDE_CANDIDATES = [
    # Bundled with Alfred's voice (Agent SDK), usually newer than the desktop app's copy.
    *HERE.parent.glob('mac/local-workspace/node_modules/@anthropic-ai/claude-agent-sdk-*/claude*'),
    *Path.home().joinpath('.local/share/claude/versions').glob('*'),
    *([Path(os.path.expanduser(CONFIG['claude']))] if CONFIG.get('claude') else []),
    *([Path(x)] if (x := shutil.which('claude', path=pf.search_path())) else []),
]
_claude = None
def claude_bin():
    """The newest installed Claude Code: new models need recent CLI versions."""
    global _claude
    if _claude: return _claude
    found = []
    for path in CLAUDE_CANDIDATES:
        try:
            out = subprocess.run([str(path), '--version'], capture_output=True, text=True, timeout=20).stdout
            found.append(([int(x) for x in re.findall(r'\d+', out.split()[0])], str(path)))
        except (OSError, IndexError, subprocess.SubprocessError): continue
    if not found: raise Stop('no hay Claude Code instalado')
    _claude = max(found)[1]
    return _claude

MODEL_POLICY = load(Path(__file__).with_name('modelos.json'), {})
LEVELS = ('baja', 'media', 'alta')

CODEX_BIN = Path(shutil.which('codex', path=pf.search_path()) or Path.home() / '.local/bin/codex')
CODEX_LOCK = threading.Lock()
def is_codex(model): return str(model or '').startswith('gpt-')

CODEX_RETRY_MINUTES = 5
CODEX_NETWORK = re.compile(r'request timed out|network error|reconnecting|ENOTFOUND|ECONNRESET|ECONNREFUSED|EAI_AGAIN|ETIMEDOUT', re.I)
CODEX_QUOTA = re.compile(r'usage limit|rate limit|quota|credits|insufficient_quota', re.I)
CODEX_RESET = re.compile(r'try again at ([A-Za-z]{3})[a-z]* (\d{1,2})[a-z]*,? (\d{4}),? (\d{1,2}):(\d{2}) ?([AP]M)', re.I)

def codex_transient(reason):
    """A transport failure is not evidence that the subscription has run out."""
    text = str(reason or '')
    return bool(CODEX_NETWORK.search(text)) and not (CODEX_QUOTA.search(text) or CODEX_RESET.search(text))

def codex_outage():
    """Read the outage without rewriting it; old network failures also get the bounded cooldown."""
    down = load(STATE / 'codex.json', {})
    if not isinstance(down, dict): return {}
    # New records classify the complete error before truncating it. A legacy 300-character reason may have lost
    # a quota/reset suffix: only reinterpret an untruncated legacy transport error.
    legacy_network = ('tipo' not in down and len(str(down.get('motivo', ''))) < 300
                      and codex_transient(down.get('motivo')))
    if legacy_network and down.get('hasta') and down.get('hora'):
        try:
            since = datetime.fromisoformat(str(down['hora']).replace('Z', '+00:00'))
            since = since.replace(tzinfo=MADRID) if since.tzinfo is None else since.astimezone(MADRID)
            retry = since.timestamp() + CODEX_RETRY_MINUTES * 60
            down = {**down, 'tipo': 'red', 'hasta_epoch': retry,
                    'hasta': datetime.fromtimestamp(retry, MADRID).strftime('%Y-%m-%d %H:%M')}
        except (ValueError, TypeError): pass   # never shorten an unreadable limit record
    return down

def codex_waiting(down):
    """Network cooldowns are elapsed instants, even when the local clock goes backwards for DST."""
    epoch = down.get('hasta_epoch')
    if down.get('tipo') == 'red' and isinstance(epoch, (int, float)) and isfinite(epoch):
        return now().timestamp() < epoch
    return clock() < down.get('hasta', '')

def clock(): return now().strftime('%Y-%m-%d %H:%M')

def codex_ready():
    """Codex works from its start in modelos.json ('2026-10-10 14:30' or a bare date), except while it is out."""
    rule = MODEL_POLICY.get('codex') or {}
    if not rule.get('desde') or clock() < rule['desde'] or not CODEX_BIN.exists(): return False
    return not codex_waiting(codex_outage())

def codex_back(reason):
    """Honor explicit resets and quota limits; retry a temporary network failure after five minutes."""
    m = CODEX_RESET.search(str(reason or ''))
    if m:
        try: return datetime.strptime(f'{m[1]} {m[2]} {m[3]} {m[4]}:{m[5]} {m[6]}', '%b %d %Y %I:%M %p').strftime('%Y-%m-%d %H:%M')
        except ValueError: pass
    if codex_transient(reason): return datetime.fromtimestamp(now().timestamp() + CODEX_RETRY_MINUTES * 60, MADRID).strftime('%Y-%m-%d %H:%M')
    return today() + ' 23:59'

def codex_down(reason):
    """Respect the cooldown and keep repeated network notices to once per day."""
    with CODEX_LOCK:
        previous = codex_outage()
        temporary = codex_transient(reason)
        until = codex_back(reason)
        if codex_waiting(previous):
            # Another request can report a real quota while the first request is already in a network cooldown.
            # Promote that limit (or extend an existing one), never downgrade it to a short transport retry.
            if temporary or (previous.get('tipo') != 'red' and until <= previous.get('hasta', '')): return
        already_warned = (str(previous.get('hora', '')).startswith(today())
                          and previous.get('tipo') == 'red' and temporary)
        marker = {'hasta': until, 'motivo': reason[:300], 'hora': now().isoformat(timespec='seconds'),
                  'tipo': 'red' if temporary else 'limite'}
        if temporary: marker['hasta_epoch'] = now().timestamp() + CODEX_RETRY_MINUTES * 60
        save(STATE / 'codex.json', marker)
    when = until[11:] if until[:10] == today() else f'{until[8:10]}/{until[5:7]} a las {until[11:]}'
    log('Codex fuera hasta', until, reason[:120])
    if not already_warned:
        post('alfred', ['owner'], f'Codex no responde ({reason[:140]}). El trabajo se conserva; se podrá reintentar a las {when}.', 'aviso', 'general')

def pick_model(role, spec=None, attempt=1, diff_lines=0, codex=True, builder=None):
    """The cheapest model that fits the job (equipo/modelos.json): fixed per role, or by the task's difficulty for
    builder and reviewer. Important or sensitive tasks, a second attempt and big reviews always go to the strong model.
    From Codex's start date its roles (the builder) take first attempts from its own difficulty table; a second
    attempt switches to Opus for a fresh pair of eyes. The reviewer is always the other vendor: Claude reviews what Codex
    built, and (codex.revisa_a_claude) Codex reviews what Claude built — second attempts and Claude covering an outage."""
    names, roles = MODEL_POLICY.get('modelos', {}), MODEL_POLICY.get('roles', {})
    rule = roles.get(role) if role else None
    if not rule: return CONFIG['model'], None
    if rule.get('modelo') != 'auto': return names.get(rule['modelo'], CONFIG['model']), rule.get('esfuerzo')
    spec = spec or {}
    level = spec.get('dificultad') if spec.get('dificultad') in LEVELS else 'media'
    if spec.get('importante') or attempt > 1 or diff_lines > 400: level = 'alta'
    table = 'dificultad'
    rule_codex = MODEL_POLICY.get('codex') or {}
    if codex and attempt == 1 and role in rule_codex.get('roles', []) and codex_ready(): table = 'dificultad_codex'
    if codex and role == 'revisor' and builder and not is_codex(builder) and rule_codex.get('revisa_a_claude') and codex_ready():
        table = 'dificultad_codex'
    chosen = MODEL_POLICY.get(table, {}).get(level, {})
    return names.get(chosen.get('modelo'), CONFIG['model']), chosen.get('esfuerzo')

def record_usage(role, model, effort, text):
    """One line per call in state/consumo/<date>.jsonl: which model, how much it cost — the evidence for the savings."""
    if isinstance(text, dict): data = text
    else:
        try: data = json.loads(text) if text else {}
        except ValueError: data = {}
    line = {'hora': now().isoformat(timespec='seconds'), 'rol': role or 'otro', 'modelo': model, 'esfuerzo': effort,
            'coste_usd': data.get('total_cost_usd'), 'tokens': {k: v for k, v in (data.get('usage') or {}).items() if isinstance(v, int)}}
    folder = STATE / 'consumo'; folder.mkdir(parents=True, exist_ok=True)
    with open(folder / f'{today()}.jsonl', 'a') as fh: fh.write(json.dumps(line, ensure_ascii=False) + '\n')

CODEX_NOTE = """
Cómo trabajas aquí (Codex): para leer usa solo comandos de lectura (rg, sed, cat, ls) y edita con apply_patch.
Nada de pruebas, git, instalaciones, red ni escrituras fuera de los archivos permitidos: el equipo prueba después."""

def codex_run(prompt, cwd, timeout, model, effort, write=False, out=None, heartbeat=None, role=None):
    """One isolated Codex run: without Owner's user config (no priority tier, no hooks), nothing kept, sandboxed
    to the worktree with no network; read-only for every role but the builder. Usage comes from the JSON events."""
    final = Path(out).with_name(Path(out).stem + '-codex.txt') if out else STATE / 'tmp' / f'codex-{uuid.uuid4().hex}.txt'
    final.parent.mkdir(parents=True, exist_ok=True); final.unlink(missing_ok=True)
    argv = [str(CODEX_BIN), 'exec', '--ignore-user-config', '--ephemeral', '--skip-git-repo-check', '--color', 'never', '--json',
            '-s', 'workspace-write' if write else 'read-only', '-m', model, *(['-c', f'model_reasoning_effort="{effort}"'] if effort else []),
            '-C', str(cwd), '-o', str(final), '-']
    code, text = run(argv, cwd, timeout, out=out, stdin=prompt + (CODEX_NOTE if write else ''), heartbeat=heartbeat)
    events, usage = [], {}
    for line in text.splitlines():
        try: events.append(json.loads(line))
        except ValueError: continue
    for e in events:
        if isinstance(e, dict) and e.get('type') == 'turn.completed':
            for k, v in (e.get('usage') or {}).items():
                if isinstance(v, int): usage[k] = usage.get(k, 0) + v
    try: record_usage(role, model, effort, json.dumps({'usage': usage}))
    except OSError: pass
    answer = final.read_text() if final.exists() else ''
    if not out: final.unlink(missing_ok=True)
    failed = [e for e in events if isinstance(e, dict) and e.get('type') in ('error', 'turn.failed')]
    if code or failed or not answer.strip():
        why = failed[-1].get('message') or (failed[-1].get('error') or {}).get('message') if failed else f'exit {code}'
        raise Stop('codex_error: ' + str(why)[:300])
    return answer

NET_WAITS = (30, 120)    # seconds to wait before each retry of a Claude Code call that failed on the network
RED_AGOTADA = 'API inaccesible'
RED_VENTANA_HORAS = 6
NET_ERROR = re.compile(r"ENOTFOUND|ECONNRESET|ETIMEDOUT|ECONNREFUSED|EAI_AGAIN|Can't reach the API server|API Error: 5\d\d")

def claude(prompt, cwd, timeout, write=False, out=None, heartbeat=None, tools=None, role=None, spec=None, attempt=1, diff_lines=0,
           info=None, on_fallback=None, builder=None):
    """One isolated agent run, on the model the policy picks. Codex runs through codex_run; if it fails for any reason
    but a timeout or a lost claim, it is out for the day and the same call goes to Claude (on_fallback first undoes
    whatever it left in the worktree). Claude Code runs have no MCP, no user/project settings or hooks and no session;
    builders may only use file tools inside their worktree; planners and reviewers only read. info['modelo'] = who answered."""
    model, effort = pick_model(role, spec, attempt, diff_lines, builder=builder)
    prompt = con_nombre(prompt)
    if is_codex(model):
        try:
            answer = codex_run(prompt, cwd, timeout, model, effort, write, out, heartbeat, role)
            if info is not None: info['modelo'] = model
            return answer
        except Stop as e:
            if str(e) in ('timeout', 'claim_lost'): raise
            codex_down(str(e))
            if on_fallback: on_fallback()
            model, effort = pick_model(role, spec, attempt, diff_lines, codex=False, builder=builder)
    if info is not None: info['modelo'] = model
    other = [m for m in MODEL_POLICY.get('modelos', {}).values() if m != model and not is_codex(m)][:1]
    argv = [claude_bin(), '-p', '--output-format', 'stream-json', '--verbose', '--model', model, *(['--effort', effort] if effort else []),
            *(['--fallback-model', other[0]] if other else []),
            '--restricted', '--safe-mode', '--permission-mode', 'acceptEdits' if write else 'dontAsk',
            '--permission-prompts', 'none', '--tools', tools or ('Read,Edit,Write,Glob,Grep' if write else 'Read,Glob,Grep'),
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}', '--setting-sources', '',
            '--no-session-persistence', '--disable-slash-commands']
    if tools and 'Web' in tools: argv += ['--allowedTools', 'WebSearch,WebFetch']   # read-only research, pre-approved
    for tries in range(len(NET_WAITS) + 1):
        code, text = run(argv, cwd, timeout, stdin=prompt, heartbeat=heartbeat)
        data, rate = parse_stream(text)
        # The receipt keeps the same one-object format as before; the stream itself is not kept.
        if out: Path(out).parent.mkdir(parents=True, exist_ok=True); Path(out).write_text(json.dumps(data or {'salida': text[-4000:]}, ensure_ascii=False))
        try:
            record_usage(role, model, effort, data or {})
            if rate: record_plan_usage(rate)
        except OSError: pass
        if data is None: raise Stop(f'claude_salida_invalida (exit {code})')
        if not (code or data.get('is_error')): return data.get('result') or ''
        reason = str(data.get('result') or data.get('subtype'))
        if not NET_ERROR.search(reason): raise Stop('claude_error: ' + reason[:300])
        if tries == len(NET_WAITS): raise Stop(f'{RED_AGOTADA} tras {tries + 1} intentos: ' + reason[:200])
        log(f'Claude: error de red ({reason[:120]}); reintento en {NET_WAITS[tries]} s')
        for _ in range(NET_WAITS[tries]):
            time.sleep(1)
            if heartbeat and not heartbeat(): raise Stop('claim_lost')

def parse_stream(text):
    """(result event, last rate-limit info) from Claude Code's stream-json output."""
    result, rate = None, None
    for line in text.splitlines():
        try: e = json.loads(line)
        except ValueError: continue
        if not isinstance(e, dict): continue
        if e.get('type') == 'result': result = e
        elif e.get('type') == 'rate_limit_event' and isinstance(e.get('rate_limit_info'), dict): rate = e['rate_limit_info']
    return result, rate

def record_plan_usage(info):
    """Plan usage as the team's own calls see it (same shape the app writes to Workspace/uso.json)."""
    w = info.get('unifiedWindows') or {}
    own = lambda k: info.get('utilization') if info.get('rateLimitType') == k else None
    five, week = w.get('five_hour') or {}, w.get('seven_day') or {}
    save(STATE / 'uso.json', {'fiveHour': five.get('utilization', own('five_hour')), 'sevenDay': week.get('utilization', own('seven_day')),
                              'resetsAt': five.get('resetsAt'), 'resetsWeek': week.get('resetsAt'), 'status': info.get('status'),
                              'hora': now().isoformat(timespec='seconds')})

def _when(iso):
    try: return datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
    except ValueError: return None

def uso_actual():
    """The freshest plan usage seen by Alfred (the app) or by the team."""
    best = None
    for f in (STATE / 'uso.json', WORKSPACE / 'uso.json'):
        u = load(f, None); t = _when(u.get('hora')) if isinstance(u, dict) else None
        if t and (best is None or t > best[0]): best = (t, u)
    return best

def freno():
    """Why the team should not spend now (None = go): the plan is near its limit, so Alfred keeps room to answer.
    Thresholds in modelos.json 'freno'. A window that has already reset no longer counts."""
    seen = uso_actual()
    if not seen: return None
    t, u = seen; ts = now().timestamp(); age = ts - t.timestamp()
    lim = MODEL_POLICY.get('freno') or {}
    five = u.get('fiveHour') or 0
    if (u.get('resetsAt') and ts >= float(u['resetsAt'])) or age > 5 * 3600: five = 0
    week = u.get('sevenDay') or 0
    if (u.get('resetsWeek') and ts >= float(u['resetsWeek'])) or age > 24 * 3600: week = 0
    hhmm = lambda e: datetime.fromtimestamp(float(e), MADRID).strftime('%H:%M') if e else 'cuando se renueve'
    if five >= lim.get('cinco_horas', 0.85): return f"el plan va al {round(five * 100)} % de las 5 h; se renueva a las {hhmm(u.get('resetsAt'))}"
    if week >= lim.get('semana', 0.85): return f"el plan va al {round(week * 100)} % de la semana"
    return None

def json_block(text):
    """The last fenced or bare JSON value in a model answer."""
    blocks = re.findall(r'```(?:json)?\s*([\[{][\s\S]*?[\]}])\s*```', text) or re.findall(r'([\[{][\s\S]*[\]}])', text)
    for block in reversed(blocks):
        try: return json.loads(block)
        except ValueError: continue
    raise Stop('respuesta_sin_json')

# ---------- boards ----------

def project(pid):
    if pid not in CONFIG['projects']: raise Stop('proyecto_desconocido: ' + pid)
    return CONFIG['projects'][pid]

@contextmanager
def board(pid):
    slug = project(pid)['board']
    with kb.scoped_current_board(slug):
        with kbc.connect_closing(board=slug) as conn:
            yield conn

def tasks(pid, status=None):
    with board(pid) as conn:
        return [asdict(t) for t in kb.list_tasks(conn, status=status)]

def claimer(): return f'{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}'

# ---------- setup ----------

def setup():
    for pid, p in CONFIG['projects'].items():
        kb.create_board(p['board'], name=f"Equipo · {p['name']}", description='Lote diario aprobado por Owner; una tarea a la vez.', default_workdir=p['repo'])
        team = f'equipo/{pid}'
        if not git(p['repo'], 'branch', '--list', team):
            git(p['repo'], 'branch', team, p['base'])
            log(pid, f'rama {team} creada desde {p["base"]} ({git(p["repo"], "rev-parse", "--short", p["base"])})')
        else:
            log(pid, f'rama {team} ya existe')
    for d in ('proposals', 'runs', 'followups', 'lecciones', 'senales', 'retros', 'mesa'): (STATE / d).mkdir(parents=True, exist_ok=True)
    log('listo:', ROOT)

def worktree(pid, name, branch=None, new_branch=None, start=None):
    """Isolated checkout under ~/Equipo/worktrees, with dependency folders linked from the main repo."""
    p = project(pid); path = ROOT / 'worktrees' / pid / name
    if path.exists():
        git(p['repo'], 'worktree', 'remove', '--force', str(path), check=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    git(p['repo'], 'worktree', 'prune')
    if new_branch: git(p['repo'], 'worktree', 'add', '-B', new_branch, str(path), start)
    elif branch: git(p['repo'], 'worktree', 'add', '--force', str(path), branch)
    else: git(p['repo'], 'worktree', 'add', '--detach', str(path), start)
    for link in p.get('links', []):
        src, dst = Path(p['repo']) / link, path / link
        if src.exists() and not dst.exists(): dst.parent.mkdir(parents=True, exist_ok=True); dst.symlink_to(src)
    # A symlink is not a directory for a 'node_modules/' ignore pattern, so keep the links out of git status.
    linked = [link for link in p.get('links', []) if (path / link).is_symlink()]
    if linked:
        exclude = Path(git(path, 'rev-parse', '--git-path', 'info/exclude'))
        if not exclude.is_absolute(): exclude = path / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        text = exclude.read_text() if exclude.exists() else ''
        have = set(text.splitlines())
        new = [f'/{link}' for link in linked if f'/{link}' not in have]
        if new: exclude.write_text(text + ('' if not text or text.endswith('\n') else '\n') + '\n'.join(new) + '\n')
    return path

APP_CONFIG = load(pf.app_data() / 'configuracion.json', {})
def _ruta(key, env=None):
    v = APP_CONFIG
    for part in key.split('.'):
        v = v.get(part) if isinstance(v, dict) else None
    v = (os.environ.get(env) if env else None) or v
    return Path(os.path.expanduser(v)) if isinstance(v, str) and v.strip() else None
VAULT = _ruta('vault', 'PROMPTED_OS_VAULT_DIR') or STATE / 'sin-notas'      # the owner's notes (none set up: an empty folder)
CARTERA = _ruta('cartera', 'PROMPTED_OS_CARTERA_DIR') or VAULT / 'Cartera'

def cartera():
    """Owner's live portfolio: one Obsidian note per project (properties + context). Read-only for the team."""
    out = []
    for f in sorted(CARTERA.glob('*.md')) if CARTERA.is_dir() else []:
        try: text = f.read_text()
        except OSError: continue
        m = re.match(r'^---\r?\n(.*?)\r?\n---\r?\n?(.*)$', text, re.S)
        if not m: continue
        props = {}
        for line in m.group(1).splitlines():
            kv = re.match(r'^([A-Za-z_]+):\s*(.*)$', line)
            if kv: props[kv.group(1).lower()] = kv.group(2).strip().strip('"').strip("'")
        if props.get('tipo') != 'proyecto': continue
        title = re.search(r'^#\s+(.+)$', m.group(2), re.M)
        out.append({**props, 'nombre': title.group(1).strip() if title else f.stem, 'nota': f.name, 'texto': text})
    return out

def estrategia(corta=False):
    """Owner's latest commercial plan (vault 01-Projects/plan-monetizacion-*.md): priorities, offers and experiments.
    The planner gets it whole; the mesa only the decisions, the next moves, the sprint and the pending points."""
    notes = sorted((VAULT / '01-Projects').glob('plan-monetizacion-*.md'))
    if not notes: return ''
    try: text = notes[-1].read_text()
    except OSError: return ''
    text = re.sub(r'^---\n.*?\n---\n', '', text, flags=re.S)
    if corta:
        parts = re.split(r'\n(?=## )', text)
        text = '\n'.join(x for x in parts if re.match(r'## (1|2|8|9|10)\.', x))
    return (f'PLAN COMERCIAL VIGENTE ({notes[-1].name}; las decisiones de Owner mandan, el resto son propuestas):\n'
            + text.strip()[:30000])

RF_DB = _ruta('contenido.maquina', 'PROMPTED_OS_RF_DB')
REELS_DIA = _ruta('contenido.reelsDia', 'PROMPTED_OS_DAY_REELS')

def tendencias(pid, limit=2500):
    """What works now on each platform for this brand: the «Qué replicar» blocks of the latest content-machine report
    (contenido/tendencias.sh writes it to the vault every morning). Empty for projects without a content brand."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    reports = sorted((VAULT / '01-Projects/Contenido/Tendencias').glob(f'{brand}-*.md')) if brand else []
    if not reports: return ''
    try: text = reports[-1].read_text()
    except OSError: return ''
    out = []
    for block in re.split(r'\n(?=## )', text):
        name = block.split('\n', 1)[0].lstrip('# ').strip()
        hit = re.search(r'### Qué replicar\n(.*?)(?=\n### |\Z)', block, re.S)
        if hit and hit.group(1).strip(): out.append(f'{name}: ' + hit.group(1).strip())
    return (f'Tendencias ({reports[-1].name}, lo que funciona ahora en redes; ideas, no datos nuestros): ' + '\n'.join(out))[:limit] if out else ''

def analisis_contenido(pid, limit=1500, hoy=None):
    """What our own reels say, from the daily content analysis (scheduled task `analizar-contenido`, Workspace/contenido/
    analisis/ultimo.json): what wins and loses on our accounts, the concept to squeeze with Trial Reel variants (Norte) and
    the conclusions with their numbers. Empty without a content brand or when the analysis is older than three days."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    data = load(TELEGRAM_LINK.parent / 'contenido' / 'analisis' / 'ultimo.json', {}) if brand else {}
    m = (data.get('marcas') or {}).get(brand) if isinstance(data, dict) else None
    try: viejo = (datetime.fromisoformat(hoy or today()) - datetime.fromisoformat(str(data.get("fecha")))).days > 3
    except (TypeError, ValueError): viejo = True
    if not isinstance(m, dict) or viejo: return ''
    lista = lambda k: [str(x) for x in (m.get(k) or []) if isinstance(x, (str, int, float))]
    partes = [m.get('resumen') or '']
    for k, nombre in (('ganadores', 'Ganan frente a la mediana de su cuenta'), ('perdedores', 'Pierden frente a la mediana')):
        reels = []
        for reel in m.get(k) or []:
            if not isinstance(reel, dict): continue
            if any(type(reel.get(campo)) not in (int, float) or reel[campo] < 0
                   or (isinstance(reel[campo], float) and not isfinite(reel[campo])) for campo in ('vistas', 'x_mediana')): continue
            titulo = str(reel.get('titulo') or '')
            if len(titulo) > 80: titulo = titulo[:79] + '…'
            x = reel['x_mediana']
            factor = (str(x) if isinstance(x, int) else f'{x:.1f}'.removesuffix('.0')).replace('.', ',')
            reels.append(f'«{titulo}» ({reel.get("red") or "sin red"}): {reel["vistas"]} vistas, ×{factor}')
            if len(reels) == 3: break
        if reels: partes.append(f'{nombre}: ' + '; '.join(reels) + '.')
    for k, nombre in (('conclusiones', 'Conclusiones'), ('ganchos_que_ganan', 'Ganchos que ganan'), ('conceptos_que_ganan', 'Conceptos que ganan'),
                      ('repetir', 'Repetir'), ('evitar', 'Evitar'), ('validados', 'Conceptos ya validados (Owner decide subirlos como normales)')):
        if lista(k): partes.append(f'{nombre}: ' + '; '.join(lista(k)))
    ex = m.get('exprimir') if isinstance(m.get('exprimir'), dict) else {}
    if ex.get('concepto'):
        variantes = '; '.join(f"«{v.get('gancho', '')}» ({v.get('angulo', '')})" for v in ex.get('variantes') or [] if isinstance(v, dict))
        partes.append(f"Exprimir con variantes en Trial Reels: {ex['concepto']}" + (f" ({ex['por_que']})" if ex.get('por_que') else '') + (f'. Variantes: {variantes}' if variantes else ''))
    texto = '\n'.join(p for p in partes if p)
    return (f"Análisis de nuestro contenido ({data['fecha']}, datos reales de nuestras cuentas): " + texto)[:limit] if texto else ''

def _notas_para_rehacer(pid):
    """Recent reels sent by Owner, shared across brands unless his request names a specific one."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    carpeta = VAULT / '01-Projects/Contenido/Para rehacer'
    if not brand or not carpeta.is_dir(): return []
    ahora = now().astimezone(MADRID)
    prefijos = _prefijos()
    notas = []
    for path in carpeta.glob('*.md'):
        try: texto = path.read_text()
        except OSError: continue
        front = re.match(r'\A---\n(.*?)\n---(?:\n|\Z)', texto, re.S)
        if not front: continue
        recibido = re.search(r'^recibido: (\d{4}-\d{2}-\d{2} \d{2}:\d{2})(?:[ \t]+\([^\n]*\))?[ \t]*$', front.group(1), re.M)
        if not recibido: continue
        try: fecha = datetime.strptime(recibido.group(1), '%Y-%m-%d %H:%M').replace(tzinfo=MADRID)
        except ValueError: continue
        if not ahora - timedelta(days=14) <= fecha <= ahora: continue
        pedido_match = re.search(r'^pedido:[ \t]*(.*)$', front.group(1), re.M)
        pedido = pedido_match.group(1).strip() if pedido_match else ''
        if len(pedido) >= 2 and pedido[0] == pedido[-1] and pedido[0] in ('"', "'"):
            pedido = pedido[1:-1]
        minusculas = pedido.lower()
        compacto = re.sub(r'[\W_]+', '', minusculas)
        marcas = {marca for prefijo, marca in prefijos.items()
                  if re.search(r'\b' + re.escape(prefijo) + r'\b', minusculas)
                  or marca.replace('-', '').lower() in compacto
                  or re.search(r'\b' + re.escape(marca.split('-')[0].lower()) + r'\b', minusculas)}
        if marcas and brand not in marcas: continue
        cabecera = re.split(r'^## ', texto[front.end():], maxsplit=1, flags=re.M)[0]
        h1 = re.search(r'^# (.+)$', cabecera, re.M)
        titulo = h1.group(1).strip() if h1 else path.stem
        if len(titulo) > 80: titulo = titulo[:79] + '…'
        resumen = re.search(r'^> (.+)$', cabecera, re.M)
        linea = f'«{titulo}» ({fecha:%d/%m})'
        linea += ': ' + resumen.group(1).strip().rstrip('.') + '.' if resumen else '.'
        if pedido: linea += f' Pide: «{pedido}».'
        notas.append((fecha, titulo, linea))
    notas.sort(key=lambda n: n[0], reverse=True)
    return notas

def _formato_para_rehacer(notas, limit=900):
    if not notas: return ''
    partes = [f'Reels que Owner mandó para rehacer (últimos 14 días; referencias de otros, no datos nuestros): {len(notas)}.']
    partes.extend(linea for _, _, linea in notas[:3])
    if len(notas) > 3: partes.append(f'y {len(notas) - 3} más')
    return '\n'.join(partes)[:limit]

def para_rehacer(pid, limit=900):
    return _formato_para_rehacer(_notas_para_rehacer(pid), limit)

def citas_para_rehacer(pid, days=7):
    total = {'lotes': 0, 'propuestas': 0, 'citan': 0}
    entradas = load(STATE / 'para_rehacer_citas' / f'{pid}.json', [])
    if not isinstance(entradas, list): return total
    hoy = now().date()
    for entrada in entradas:
        if not isinstance(entrada, dict): continue
        fecha = entrada.get('fecha')
        if not isinstance(fecha, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', fecha): continue
        try: fecha = datetime.strptime(fecha, '%Y-%m-%d').date()
        except ValueError: continue
        if not hoy - timedelta(days=days) <= fecha <= hoy: continue
        if any(type(entrada.get(k)) is not int or entrada[k] < 0 for k in ('propuestas', 'citan')): continue
        if entrada['citan'] > entrada['propuestas']: continue
        total['lotes'] += 1
        for k in ('propuestas', 'citan'): total[k] += entrada[k]
    return total

def _prefijos():
    """{prefix: brand} of the content brands in the app configuration (Contenido's `marcas`), prefixes like `tf` or `kc`."""
    marcas = APP_CONFIG.get('contenido', {}).get('marcas') if isinstance(APP_CONFIG.get('contenido'), dict) else None
    if not isinstance(marcas, list): return {}
    return {m['prefijo']: m['id'] for m in marcas if isinstance(m, dict) and isinstance(m.get('id'), str)
            and isinstance(m.get('prefijo'), str) and re.fullmatch(r'[a-z]{1,6}', m['prefijo'])}

# Same rule as RITMO_BUENO/esReelBueno in local-workspace/contenido.ts: the 20:00 «reel bueno» of a day, used with fullmatch.
RITMO_BUENO = re.compile(r'ritmo:([a-z]{1,6})-2000:(\d{4}-\d{2}-\d{2})')
CLICS_MINIMOS_COMPRAS = 10

def _cifras_dia(posible):
    return (isinstance(posible, list) and len(posible) == 2
            and all(type(n) is int and n >= 0 for n in posible) and posible[1] > 0)

def _atribucion_dia(valor):
    return (isinstance(valor, list) and len(valor) == 3
            and all(type(n) is int and n >= 0 for n in valor) and valor[2] > 0
            and valor[0] <= valor[2] and valor[1] <= valor[2])

def _historico_cercano(historico, fecha, orden, valido):
    """The first valid historical entry at one of the requested day offsets."""
    for dias in orden:
        posible = historico.get((fecha - timedelta(days=dias)).strftime('%Y-%m-%d'))
        if valido(posible): return dias, posible
    return None

def cifras_calendario(pid):
    """The newest usable calendar figures for a project, without changing their history."""
    historico = load(STATE / 'cifras_calendario' / f'{pid}.json', {})
    if not isinstance(historico, dict): return None
    ahora = now()
    validas = []
    for texto, cifras in historico.items():
        if not isinstance(texto, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', texto) or not _cifras_dia(cifras): continue
        try: fecha = datetime.strptime(texto, '%Y-%m-%d').date()
        except ValueError: continue
        if fecha <= ahora.date(): validas.append((fecha, texto, cifras))
    if not validas: return None
    fecha, texto, cifras = max(validas)
    if fecha < (ahora - timedelta(days=1)).date(): return None
    hace_7 = _historico_cercano(historico, fecha, (7, 8, 6), _cifras_dia)
    hace_14 = _historico_cercano(historico, fecha, (14, 15, 13), _cifras_dia)
    return {'fecha': texto, 'apuntadas': cifras[0], 'publicadas': cifras[1],
            'hace_7': None if hace_7 is None else {'dias': hace_7[0], 'apuntadas': hace_7[1][0], 'publicadas': hace_7[1][1]},
            'hace_14': None if hace_14 is None else {'dias': hace_14[0], 'apuntadas': hace_14[1][0], 'publicadas': hace_14[1][1]}}

def sin_enlace_calendario(pid):
    """Upcoming publications without measured links, compared with the nearest usable week."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    if not brand or brand == 'beta': return ''
    historico = load(WORKSPACE / 'contenido' / 'sin-enlace.json', {})
    if not isinstance(historico, dict): return ''
    def valido(entrada):
        return (isinstance(entrada, dict) and type(entrada.get('total')) is int and entrada['total'] >= 0
                and isinstance(entrada.get('marcas'), dict)
                and type(entrada['marcas'].get(brand, 0)) is int and entrada['marcas'].get(brand, 0) >= 0)
    hoy = now().date()
    validas = []
    for texto, entrada in historico.items():
        if not isinstance(texto, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', texto) or not valido(entrada): continue
        try: fecha = datetime.strptime(texto, '%Y-%m-%d').date()
        except ValueError: continue
        if fecha <= hoy: validas.append((fecha, entrada))
    if not validas: return ''
    fecha, entrada = max(validas, key=lambda item: item[0])
    if fecha < hoy - timedelta(days=1): return ''
    cantidad = entrada['marcas'].get(brand, 0)
    anterior = _historico_cercano(historico, fecha, (7, 8, 6), valido)
    if cantidad == 0 and (anterior is None or anterior[1]['marcas'].get(brand, 0) == 0): return ''
    linea = f'Publicaciones de los próximos 7 días que saldrían sin enlace medido (sus clics y compras no se podrán atribuir): {cantidad}.'
    if anterior is not None: linea += f' Hace {anterior[0]} días: {anterior[1]["marcas"].get(brand, 0)}.'
    return linea

def atribucion_calendario(pid):
    """The newest usable calendar attribution for a project, without changing its history."""
    historico = load(STATE / 'atribucion_calendario' / f'{pid}.json', {})
    if not isinstance(historico, dict): return None
    ahora = now()
    validas = []
    for texto, cifras in historico.items():
        if not isinstance(texto, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', texto) or not _atribucion_dia(cifras): continue
        try: fecha = datetime.strptime(texto, '%Y-%m-%d').date()
        except ValueError: continue
        if fecha <= ahora.date(): validas.append((fecha, texto, cifras))
    if not validas: return None
    fecha, texto, cifras = max(validas)
    if fecha < (ahora - timedelta(days=1)).date(): return None
    hace_7 = _historico_cercano(historico, fecha, (7, 8, 6), _atribucion_dia)
    hace_14 = _historico_cercano(historico, fecha, (14, 15, 13), _atribucion_dia)
    return {'fecha': texto, 'con_clics': cifras[0], 'con_compras': cifras[1], 'con_cifras': cifras[2],
            'hace_7': None if hace_7 is None else {'dias': hace_7[0], 'con_clics': hace_7[1][0], 'con_compras': hace_7[1][1], 'con_cifras': hace_7[1][2]},
            'hace_14': None if hace_14 is None else {'dias': hace_14[0], 'con_clics': hace_14[1][0], 'con_compras': hace_14[1][1], 'con_cifras': hace_14[1][2]}}

def calendario_por_medir(pid, limit=900, solo_cifras=False):
    """Recent calendar publications awaiting figures, and failed publications, for this brand. With solo_cifras only figures and attribution."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    if not brand: return ''
    calendario = load(WORKSPACE / 'contenido' / 'calendario.json', {})
    medidas = load(WORKSPACE / 'contenido' / 'medidas.json', {})
    if not isinstance(calendario, dict) or not isinstance(calendario.get('entradas'), list) or not isinstance(medidas, dict): return ''
    ahora = now()
    hasta = ahora.timestamp(); desde = hasta - 7 * 86400
    pendientes = []; fallidas = []; publicadas = 0
    medidas_publicadas = []
    def corto(value):
        text = ' '.join(value.split()) if isinstance(value, str) else ''
        return text[:79] + '…' if len(text) > 80 else text
    for e in calendario['entradas']:
        if not isinstance(e, dict) or e.get('marca') != brand or not isinstance(e.get('id'), str): continue
        fecha, hora = e.get('fecha'), e.get('hora')
        if not isinstance(fecha, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', fecha): continue
        if not isinstance(hora, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', hora): continue
        try: momento = datetime.strptime(f'{fecha} {hora}', '%Y-%m-%d %H:%M').replace(tzinfo=MADRID)
        except ValueError: continue
        at = momento.timestamp()
        if not desde <= at <= hasta: continue
        titulo = corto(e.get('titulo')) or f'publicación del calendario del {momento.strftime("%d/%m")}'
        item = f'«{titulo}» ({corto(e.get("red")) or "sin red"}, {momento.strftime("%d/%m")})'
        if e.get('estado') == 'publicado' and at <= hasta - 86400:
            publicadas += 1
            medidas_publicadas.append(medidas.get('cal:' + e['id']))
            if 'cal:' + e['id'] not in medidas:
                pendientes.append((at, item))
        elif e.get('estado') == 'fallo':
            nota = corto(e.get('nota'))
            fallidas.append((at, item + (': ' + nota if nota else '')))
    def lista(rows):
        items = [item for _, item in sorted(rows, key=lambda row: row[0], reverse=True)[:3]]
        if len(rows) > 3: items.append(f'y {len(rows) - 3} más')
        return '; '.join(items)
    lines = []
    if publicadas:
        apuntadas = publicadas - len(pendientes)
        archivo = STATE / 'cifras_calendario' / f'{pid}.json'
        historico = load(archivo, {})
        if not isinstance(historico, dict): historico = {}
        linea = f'Cifras apuntadas del calendario (publicadas hace más de 24 h, últimos 7 días): {apuntadas} de {publicadas}.'
        for orden in ((7, 8, 6), (14, 15, 13)):
            encontrado = _historico_cercano(historico, ahora, orden, _cifras_dia)
            if encontrado is not None:
                linea += f' Hace {encontrado[0]} días: {encontrado[1][0]} de {encontrado[1][1]}.'
        lines.append(linea)
        historico[ahora.strftime('%Y-%m-%d')] = [apuntadas, publicadas]
        try: save(archivo, {fecha: historico[fecha] for fecha in sorted(f for f in historico if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', f))[-60:]})
        except OSError: pass     # An unavailable history must not interrupt the calendar notice.
        con_cifras = con_clics = con_compras = 0
        for medida in medidas_publicadas:
            if not isinstance(medida, dict) or any(type(medida.get(k)) is not int or medida[k] < 0
                                                   for k in ('vistas', 'visitas', 'clics', 'compras')): continue
            con_cifras += 1
            if medida['clics'] > 0: con_clics += 1
            if medida['compras'] > 0: con_compras += 1
        if con_cifras:
            linea = f'Atribución del calendario (mismas publicaciones): de {con_cifras} con cifras, {con_clics} con clics y {con_compras} con compras.'
            archivo = STATE / 'atribucion_calendario' / f'{pid}.json'
            historico = load(archivo, {})
            if not isinstance(historico, dict): historico = {}
            for orden in ((7, 8, 6), (14, 15, 13)):
                encontrado = _historico_cercano(historico, ahora, orden, _atribucion_dia)
                if encontrado is not None:
                    linea += f' Hace {encontrado[0]} días: {encontrado[1][0]} con clics y {encontrado[1][1]} con compras de {encontrado[1][2]}.'
            lines.append(linea)
            historico[ahora.strftime('%Y-%m-%d')] = [con_clics, con_compras, con_cifras]
            try: save(archivo, {fecha: historico[fecha] for fecha in sorted(f for f in historico if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', f))[-60:]})
            except OSError: pass     # An unavailable history must not interrupt the calendar notice.
    if solo_cifras: return '\n'.join(lines)[:limit]
    if pendientes:
        lines.append(f'Publicaciones del calendario sin cifras (publicadas hace más de 24 h, últimos 7 días): {len(pendientes)}: '
                     + lista(pendientes) + '. Pide a Owner que apunte vistas, visitas, clics y compras en Contenido o con /medir antes de sacar conclusiones de esa línea.')
    if fallidas:
        lines.append(f'Publicaciones del calendario que fallaron al publicar (7 días): {len(fallidas)}: ' + lista(fallidas))
    return '\n'.join(lines)[:limit]

def contenido_medido(pid, limit=1500):
    """Our publications measured by Owner in Contenido, matched to their brand and ranked by clicks per view."""
    import sqlite3
    from fractions import Fraction
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    if not brand: return ''
    medidas = load(WORKSPACE / 'contenido' / 'medidas.json', {})
    if not isinstance(medidas, dict): return ''
    fields = ('vistas', 'visitas', 'clics', 'compras')
    rows = []; conn = None; attempted = False; calendario = None
    try:
        for ident, medida in medidas.items():
            if not isinstance(medida, dict) or any(type(medida.get(k)) is not int or medida[k] < 0 for k in fields): continue
            if re.fullmatch(r'rf:\d+', ident):
                if not attempted:
                    attempted = True
                    if RF_DB is None: continue
                    try: conn = sqlite3.connect(f'file:{RF_DB}?mode=ro', uri=True)
                    except sqlite3.Error: continue
                if conn is None: continue
                try:
                    publication = conn.execute('select p.brand, u.channel, coalesce(p.title, p.hook) from publications u join pieces p on p.id=u.piece_id where u.id=?', (ident[3:],)).fetchone()
                except sqlite3.Error: continue
                if not publication: continue
                measured_brand, channel, title = publication
            elif (ritmo := RITMO_BUENO.fullmatch(ident)):
                measured_brand = _prefijos().get(ritmo.group(1))
                try: dia = datetime.strptime(ritmo.group(2), '%Y-%m-%d')
                except ValueError: continue
                if not measured_brand: continue
                channel = 'instagram'; title = f'reel bueno del {dia.strftime("%d/%m")}'; trial = None
                if REELS_DIA is not None:
                    ganadores = load(REELS_DIA / (dia - timedelta(days=1)).strftime('%Y-%m-%d') / 'ganadores.json', {})
                    ganador = ganadores.get(measured_brand) if isinstance(ganadores, dict) else None
                    if isinstance(ganador, dict):
                        if isinstance(ganador.get('titulo'), str) and ganador['titulo'].strip(): title = ganador['titulo'].strip()
                        if type(ganador.get('vistas')) is int and ganador['vistas'] >= 0: trial = ganador['vistas']
                if measured_brand == brand: rows.append({**medida, 'red': channel, 'titulo': title, 'fecha': dia, 'trial': trial})
                continue
            elif (cal := re.fullmatch(r'cal:(.+)', ident)):
                if calendario is None:
                    data = load(WORKSPACE / 'contenido' / 'calendario.json', {})
                    entradas = data.get('entradas') if isinstance(data, dict) else None
                    calendario = {e['id']: e for e in entradas if isinstance(e, dict)
                                  and isinstance(e.get('id'), str) and isinstance(e.get('marca'), str)} if isinstance(entradas, list) else {}
                e = calendario.get(cal.group(1))
                if e is None: continue
                measured_brand = e['marca']; channel = e['red'] if isinstance(e.get('red'), str) else ''
                title = e['titulo'].strip() if isinstance(e.get('titulo'), str) else ''
                if not title and isinstance(e.get('texto'), str):
                    try: title = next((line.strip() for line in Path(e['texto']).read_text().splitlines() if line.strip()), '')
                    except (OSError, ValueError): title = ''
                if not title:
                    title = 'publicación del calendario'
                    fecha = e.get('fecha')
                    if isinstance(fecha, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', fecha):
                        try: dia = datetime.strptime(fecha, '%Y-%m-%d')
                        except ValueError: dia = None
                        if dia is not None: title += f' del {dia.strftime("%d/%m")}'
            elif ident.startswith('plan:'):
                measured_brand, channel, title = 'norte-capital', '', 'publicación del plan de vuelta'
            elif ident.startswith('fv:'):
                folder = ident[3:]
                if re.search(r'-tf-|beta', folder): measured_brand = 'beta'
                elif re.search(r'-kc-|norte|stop-liquidez', folder): measured_brand = 'norte-capital'
                else: continue
                channel = ''; title = re.sub(r'^\d{4}-\d{2}-\d{2}-full-visual-', '', folder).replace('-', ' ')
            else: continue
            if measured_brand == brand: rows.append({**medida, 'red': channel or '', 'titulo': title or ''})
    finally:
        if conn is not None: conn.close()
    if not rows: return ''
    vistas, visitas, clics, compras = (sum(row[k] for row in rows) for k in fields)
    def porcentaje_clics(numerador, denominador):
        x = numerador * 100 / denominador
        s = f'{x:.1f}'
        return s.removesuffix('.0').replace('.', ',') + '%'
    text = (f'Lo medido de nuestras publicaciones (cifras reales que apuntó Owner en Contenido; {len(rows)} publicaciones): '
            f'{vistas} vistas, {visitas} visitas al perfil, {clics} clics, {compras} compras.')
    if vistas > 0: text += f' Clics por vista en total: {porcentaje_clics(clics, vistas)}.'
    if clics > 0: text += f' Compras por clic en total: {porcentaje_clics(compras, clics)}.'
    desde = now() - timedelta(days=7); recientes = 0
    for row in rows:
        hora = row.get('hora')
        if not isinstance(hora, str): continue
        try: at = datetime.fromisoformat(hora[:-1] + '+00:00' if hora.endswith('Z') else hora)
        except ValueError: continue
        if at.tzinfo is not None and at >= desde: recientes += 1
    text += f' Apuntadas en los últimos 7 días: {recientes}.'
    top = sorted((row for row in rows if row['vistas'] > 0),
                 key=lambda row: (Fraction(row['clics'], row['vistas']), row['clics']), reverse=True)[:3]
    if top:
        text += '\nMás clics por vista: ' + '; '.join(
            f'«{row["titulo"][:79] + "…" if len(row["titulo"]) > 80 else row["titulo"]}» ({row["red"] or "sin red"}): {row["vistas"]} vistas, {row["clics"]} clics, {row["compras"]} compras, '
            f'{porcentaje_clics(row["clics"], row["vistas"])} de clics por vista' for row in top) + '.'
    mejores_compras = sorted((row for row in rows if row['clics'] >= CLICS_MINIMOS_COMPRAS),
                             key=lambda row: (Fraction(row['compras'], row['clics']), row['compras'], row['clics']), reverse=True)[:3]
    if mejores_compras:
        text += f'\nMás compras por clic (publicaciones con {CLICS_MINIMOS_COMPRAS} clics o más): ' + '; '.join(
            f'«{row["titulo"][:79] + "…" if len(row["titulo"]) > 80 else row["titulo"]}» ({row["red"] or "sin red"}): {row["clics"]} clics, {row["compras"]} compras, '
            f'{porcentaje_clics(row["compras"], row["clics"])} de compras por clic' for row in mejores_compras) + '.'
    redes = {}
    for row in rows:
        red = row['red']
        if red not in redes: redes[red] = {k: 0 for k in ('publicaciones', 'vistas', 'clics', 'compras')}
        redes[red]['publicaciones'] += 1
        for field in ('vistas', 'clics', 'compras'): redes[red][field] += row[field]
    if len(redes) >= 2:
        por_red = []
        for red, datos in sorted(redes.items(), key=lambda item: (-item[1]['clics'], item[0] or 'sin red')):
            nombre = red or 'sin red'
            tramo = (f'{nombre}: {datos["publicaciones"]} publicaciones, {datos["vistas"]} vistas, '
                     f'{datos["clics"]} clics, {datos["compras"]} compras')
            if datos['vistas'] > 0: tramo += f', {porcentaje_clics(datos["clics"], datos["vistas"])} de clics por vista'
            if datos['clics'] > 0: tramo += f', {porcentaje_clics(datos["compras"], datos["clics"])} de compras por clic'
            por_red.append(tramo)
        text += '\nPor red: ' + '; '.join(por_red) + '.'
    buenos = sorted((row for row in rows if 'fecha' in row), key=lambda row: row['fecha'], reverse=True)[:3]
    ganadores = ''
    if buenos:
        ganadores = '\nGanadores de los Trial Reels en la cuenta principal: ' + '; '.join(
            f'«{row["titulo"][:79] + "…" if len(row["titulo"]) > 80 else row["titulo"]}» ({row["fecha"].strftime("%d/%m")}): '
            + (f'{row["trial"]} vistas en Trial Reels' if row['trial'] is not None else 'sin vistas de Trial apuntadas')
            + f' → {row["vistas"]} vistas, {row["clics"]} clics, {row["compras"]} compras en la principal' for row in buenos) + '.'
    if not ganadores: return text[:limit]
    if len(text + ganadores) <= limit: return text + ganadores
    if len(ganadores) < limit: return text[:limit - len(ganadores)] + ganadores
    return (text + ganadores)[:limit]

def calendario_pendiente(pid, limit=1100):
    """Where this brand's publishing calendar stands: published, still without figures after 24 h, failed and about to go out."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    if not brand: return ''
    calendario = load(WORKSPACE / 'contenido' / 'calendario.json', {})
    medidas = load(WORKSPACE / 'contenido' / 'medidas.json', {})
    entradas = calendario.get('entradas') if isinstance(calendario, dict) else None
    if not isinstance(entradas, list): return ''
    if not isinstance(medidas, dict): medidas = {}
    ahora = now(); medibles = ahora - timedelta(hours=24); hasta = ahora + timedelta(days=7)
    publicadas = proximas = validas = 0; sin_medir = []; fallos = []
    for e in entradas:
        if not isinstance(e, dict) or not isinstance(e.get('id'), str) or e.get('marca') != brand: continue
        fecha, hora, estado = e.get('fecha'), e.get('hora'), e.get('estado')
        if not isinstance(fecha, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', fecha): continue
        if not isinstance(hora, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', hora): continue
        if estado not in {'pendiente', 'programado', 'publicado', 'fallo'}: continue
        try: momento = datetime.strptime(f'{fecha} {hora}', '%Y-%m-%d %H:%M').replace(tzinfo=MADRID)
        except ValueError: continue
        validas += 1
        if estado == 'publicado':
            publicadas += 1
            if momento <= medibles and not isinstance(medidas.get('cal:' + e['id']), dict): sin_medir.append((momento, e))
        elif estado == 'fallo': fallos.append((momento, e))
        elif ahora <= momento <= hasta: proximas += 1
    if not validas: return ''
    def corto(value):
        text = ' '.join(value.split()) if isinstance(value, str) else ''
        return text[:79] + '…' if len(text) > 80 else text
    def titulo(e):
        title = corto(e.get('titulo'))
        if not title and isinstance(e.get('texto'), str):
            try: title = corto(next((line for line in Path(e['texto']).read_text().splitlines() if line.strip()), ''))
            except (OSError, ValueError): title = ''
        return title or 'publicación del calendario'
    def lista(rows, detalle):
        rows = sorted(rows, key=lambda row: row[0], reverse=True)
        text = '; '.join(detalle(momento, e) for momento, e in rows[:3])
        return text + (f' y {len(rows) - 3} más' if len(rows) > 3 else '')
    text = (f'Calendario de publicación (lo publica solo la tarea programada; estado real de PostOnce): {publicadas} publicadas, '
            f'{len(sin_medir)} sin cifras pasadas 24 h, {len(fallos)} con fallo, {proximas} por salir en 7 días.')
    if sin_medir:
        text += '\nSin cifras: ' + lista(sin_medir, lambda momento, e: f'«{titulo(e)}» ({corto(e.get("red")) or "sin red"}, {momento.strftime("%d/%m")})')
        text += '. Pide a Owner que apunte vistas, visitas, clics y compras en Contenido o con /medir antes de sacar conclusiones de esa línea.'
    if fallos:
        text += '\nFallos al publicar: ' + lista(fallos, lambda momento, e: f'«{titulo(e)}» ({corto(e.get("red")) or "sin red"}, '
                                                f'{momento.strftime("%d/%m")}): {corto(e.get("nota")) or "sin motivo"}')
    return text[:limit]

def tipo_gancho(texto):
    """Kind of hook: a question, one with a figure, or other. Empty text has no kind."""
    if not texto: return ''
    if '?' in texto or '¿' in texto: return 'pregunta'
    return 'cifra' if re.search(r'\d', texto) else 'otro'

def aperturas_medidas(pid, limit=1500):
    """Views and winning hook types from this brand's Trial Reels over the last 7 days."""
    brand = CONFIG['projects'].get(pid, {}).get('marca')
    if not brand: return ''
    prefijo = next((p for p, b in _prefijos().items() if b == brand), '')
    if not prefijo: return ''
    dia = load(WORKSPACE / 'contenido' / 'dia.json', {})
    if not isinstance(dia, dict): return ''
    hoy = now().date(); lines = []; con_ganador = []; total = 0
    for back in range(7):
        fecha = (hoy - timedelta(days=back)).strftime('%Y-%m-%d')
        notas = dia.get(fecha)
        vistas = notas.get('vistas') if isinstance(notas, dict) else None
        if not isinstance(vistas, dict): continue
        for hora in ('1730', '1030'):
            franja = f'{prefijo}-{hora}'
            medidas = [(h, vistas[f'{franja}-h{h}']) for h in (1, 2, 3)
                       if type(vistas.get(f'{franja}-h{h}')) is int and vistas[f'{franja}-h{h}'] >= 0]
            if not medidas: continue
            total += 1
            medidas.sort(key=lambda m: -m[1])
            partes = []; completos = []
            for h, v in medidas:
                gancho = ''
                if REELS_DIA is not None:
                    try: texto = (REELS_DIA / fecha / f'{franja}-h{h}.txt').read_text()
                    except (OSError, ValueError): texto = ''
                    gancho = next((l.strip() for l in texto.splitlines() if l.strip() and not re.match(r'comenta\b', l.strip(), re.I)), '')
                completos.append(gancho)
                if len(gancho) > 80: gancho = gancho[:79] + '…'
                partes.append((h, f'«{gancho}» ' if gancho else '', v))
            gana = len(partes) >= 2 and partes[0][2] > partes[1][2]
            detalle = [f'apertura {h} {g}{"con " if gana and i == 0 else ""}{v} vistas' for i, (h, g, v) in enumerate(partes)]
            if gana: detalle[0] = 'gana ' + detalle[0]
            if len(lines) < 6:
                lines.append(f'{fecha[8:]}/{fecha[5:7]} {hora[:2]}:{hora[2:]}: ' + '; '.join(detalle))
            if gana: con_ganador.append(({tipo_gancho(c) for c in completos if c}, tipo_gancho(completos[0])))
    if not lines: return ''
    resumen = (f'{total} franjas.' if total == len(lines)
               else f'{total} franjas; se listan las {len(lines)} más recientes.')
    cabecera = ('Trial Reels con vistas apuntadas (últimos 7 días; cifras reales que apuntó Owner en Contenido → Hoy; cada franja son 3 aperturas '
                f'del mismo vídeo con distinto gancho): {resumen}\n')
    con_tipo = sum(1 for _, ganadora in con_ganador if ganadora)
    if con_tipo >= 2:
        tipos = [(t, sum(t in competen for competen, _ in con_ganador), sum(ganadora == t for _, ganadora in con_ganador))
                 for t in ('pregunta', 'cifra', 'otro')]
        cabecera += (f'Tipo de gancho que gana ({con_tipo} franjas con ganador): '
                     + '; '.join(f'{t} ganó {gana} de {compite}' for t, compite, gana in tipos if compite) + '.\n')
    return (cabecera + '\n'.join(lines))[:limit]

def oportunidades(limit=2500):
    """The latest blue-ocean note (contenido/oportunidades.py): what works abroad and could be brought to Spanish."""
    notes = sorted((VAULT / '01-Projects/Contenido/Oportunidades').glob('Oportunidades *.md'))
    if not notes: return ''
    try: return f'{notes[-1].stem} (ideas de fuera para el mercado hispano; hipótesis, no datos nuestros):\n' + notes[-1].read_text()[:limit]
    except OSError: return ''

def ficha(pid):
    """The Obsidian note whose `equipo` property names this team project, if Owner keeps one."""
    return next((n for n in cartera() if n.get('equipo') == pid), None)

def cartera_resumen():
    return [{k: n.get(k, '') for k in ('nombre', 'area', 'linea', 'etapa', 'siguiente', 'equipo', 'actualizado')} for n in cartera()]

def context_text(pid, root):
    parts = []
    note = ficha(pid)
    if note: parts.append(f"--- Nota de Owner en Obsidian ({note['nota']}): manda sobre documentos más antiguos ---\n" + note['texto'][:6000])
    for name in project(pid).get('context', []):
        f = Path(root) / name
        if f.is_file(): parts.append(f'--- {name} ---\n' + f.read_text()[:12000])
    return '\n\n'.join(parts)

# ---------- planning ----------

# Owner (08/10/2026): business projects exist to be validated against the market and make money as soon as possible,
# not to inch the product forward every day. Injected into the planner, the mesa and the synthesis.
VALIDAR = """FORMA DE TRABAJAR (Owner, prioritaria en los proyectos de negocio): el objetivo es VALIDAR el proyecto en el mercado y GANAR DINERO cuanto antes, no avanzar el producto un poco cada día. Método Lean Startup: construir → medir → aprender, con el MVP más pequeño que pruebe la hipótesis más arriesgada (¿alguien lo quiere?, ¿alguien paga?). Valida por este orden lo que falte: idea, contenido, oferta, producto.
Antes de proponer algo, responde: ¿qué hipótesis prueba?, ¿con qué métrica?, ¿qué resultado nos haría seguir, cambiar o parar?
Prioridad: 1) experimentos que pongan una oferta delante de compradores reales y la forma de medirlos (landing con oferta y botón de reserva, compra o lista de espera; piezas de contenido que miden interés; guiones de mensajes a posibles clientes; seguimiento de visitas, clics y altas); 2) lo mínimo para cobrar o entregar al primer cliente; 3) lo que bloquee un experimento. Aparca el pulido, las refactorizaciones, la deuda técnica y las funciones «por si acaso» que no sirvan a un experimento.
Salir al mercado (publicar, enviar, gastar, abrir cuentas, fijar precios) lo hace Owner: el equipo deja el experimento listo para lanzar (qué se lanza, a quién, cómo se mide y el umbral de éxito) y lo marca como importante. No inventes datos de mercado ni resultados: lo que no se ha medido es una hipótesis.
En proyectos internos (Owner OS) esto se traduce en priorizar lo que ayude a Owner a lanzar y medir experimentos de sus negocios."""

PLAN_PROMPT = """Eres el PLANIFICADOR del equipo de agentes de Owner para el proyecto {name}.
REGLA DE OWNER (prioritaria): no propongas, construyas ni apruebes cambios hechos por motivos legales, éticos o de cumplimiento (quitar o suavizar cifras de premios, la palabra cashback, promesas, avisos, textos legales, marcar cifras como hipótesis). De eso se encarga Owner. Si ves un riesgo legal o ético, menciónaselo una vez a Owner en la mesa y no crees tareas para cambiarlo.
{validar}
{estrategia}
Objetivo del proyecto (confirmado por Owner): {goal}

Tu trabajo: proponer el lote de HOY, como máximo {n} tareas pequeñas, concretas y verificables que acerquen ese objetivo
por el camino más corto a validar y cobrar. En "porque" di qué hipótesis o experimento sirve y cómo se medirá.
Cada tarea la hará un agente constructor que SOLO puede editar los archivos que listes (máximo {max_files}), sin ejecutar
comandos ni instalar dependencias, y un revisor independiente la aprobará si las pruebas pasan. Diseña tareas de menos de
~200 líneas de cambio, que dejen el producto funcionando. Lee el código antes de proponer: rutas reales que existen
(o archivos nuevos con ruta clara). Nada de despliegues, credenciales, cuentas, pagos, publicaciones ni trading real.

Pruebas disponibles (elige por nombre, al menos una): {tests}
Tareas abiertas o recientes del tablero: {board}
Bloqueos y sugerencias del equipo de días anteriores: {followups}
{related}
{learning}
Contexto del proyecto:
{context}

Marca "importante": true SOLO si la tarea necesita una decisión de Owner: cambia lo que ve un cliente en la landing o la
marca, precios, cifras o promesas a usuarios, textos legales, autenticación o datos de usuarios, el esquema de la base de
datos, dependencias, o cualquier preparación de despliegue, broker, pagos o cuentas. Si no, false: el equipo la hará sin
esperar. En "motivo_importante" explica en una frase qué tendría que decidir Owner.
Si la tarea deja un experimento listo para que Owner lo lance, añade "lanzamiento": {{"que": "...", "a_quien": "...", "metrica": "...", "umbral": "..."}} (umbral = resultado que haría seguir, cambiar o parar); si no, omítelo.
Owner no programa: escribe "motivo_importante" y la ficha en español llano, como a un socio de negocio, sin nombres de archivos, comandos,
variables, códigos internos ni siglas (nada de rutas, BAD_TOKEN, KC-R3 o LOTE-1). Una sola ficha por experimento: si en el contexto ya hay
uno listo para lanzar del mismo experimento, no hagas otra ficha (mejora esa tarea o no la repitas).
Si la tarea es el siguiente paso de un experimento en el que Owner decidió seguir, añade "viene_de": "<id de esa tarea>".

Responde SOLO con un bloque ```json``` con esta forma:
[{{"titulo": "...", "porque": "una frase: qué aporta", "detalle": "criterios de aceptación concretos",
   "archivos": ["ruta/relativa"], "pruebas": ["nombre"], "depende_de": [indice_previo_o_vacio],
   "importante": false, "motivo_importante": "", "dificultad": "baja|media|alta",
   "lanzamiento": {{"que": "...", "a_quien": "...", "metrica": "...", "umbral": "..."}}}}]
En "dificultad": "baja" si es un cambio pequeño y localizado (textos, estilos, una función sencilla, pruebas de algo existente);
"alta" si toca lógica delicada, varios módulos, seguridad, datos, dinero o algo donde un error cuesta caro; si dudas, "media".
Sirve para elegir el modelo: no la infles.
"""

def safe_rel(path):
    # Repository paths are POSIX-style everywhere: on Windows '/etc/passwd' or 'C:\\x' must not pass as relative either.
    text = str(path).replace('\\', '/')
    if re.match(r'^[A-Za-z]:', text): return False
    p = PurePosixPath(text)
    return bool(p.parts) and not p.is_absolute() and '..' not in p.parts and not any(
        part in {'.git', '.env', '.claude', '.codex', 'node_modules', 'CLAUDE.md', 'AGENTS.md'} or part.startswith('.env.') for part in p.parts)

def validate_task(pid, t, index, root=None):
    p = project(pid)
    assert isinstance(t.get('titulo'), str) and len(t['titulo'].strip()) >= 4, 'titulo'
    # A title a few characters too long must not sink the whole round: trim it at a word boundary.
    if len(t['titulo'].strip()) > 120: t = {**t, 'titulo': t['titulo'].strip()[:118].rsplit(' ', 1)[0].rstrip(' ,.;:') + '…'}
    assert isinstance(t.get('detalle'), str) and len(t['detalle']) >= 20, 'detalle'
    files = t.get('archivos'); assert isinstance(files, list) and 1 <= len(files) <= LIMITS['max_files'], 'archivos'
    assert all(isinstance(f, str) and safe_rel(f) for f in files), 'ruta no permitida'
    tests = t.get('pruebas'); assert isinstance(tests, list) and tests and all(x in p['tests'] for x in tests), 'pruebas'
    deps = t.get('depende_de') or []; assert all(isinstance(d, int) and 0 <= d < index for d in deps), 'depende_de'
    nota = ''
    # npm test only runs the tests package.json lists: a new test/*.test.mjs needs package.json in the approved files.
    if root is not None and 'package.json' not in files and any(
            (x.get('cmd') or [''])[0] == 'npm' for x in p['tests'].values()) and any(
            re.fullmatch(r'test/[^/]+\.test\.mjs', f) and not (Path(root) / f).exists() for f in files):
        assert len(files) < LIMITS['max_files'], 'archivos: una prueba nueva en test/ necesita también package.json'
        files = files + ['package.json']
        nota = ' Incluye package.json para que npm test ejecute la prueba nueva.'
    out = {'titulo': t['titulo'].strip(), 'porque': str(t.get('porque', '')).strip()[:300], 'detalle': t['detalle'].strip()[:4000 - len(nota)] + nota,
           'archivos': files, 'pruebas': tests, 'depende_de': deps}
    lanzamiento = t.get('lanzamiento'); keys = ('que', 'a_quien', 'metrica', 'umbral')
    if isinstance(lanzamiento, dict) and all(isinstance(lanzamiento.get(k), str) and lanzamiento[k].strip() for k in keys):
        out['lanzamiento'] = {k: lanzamiento[k].strip()[:200] for k in keys}
    viene_de = t.get('viene_de')
    if isinstance(viene_de, str) and TAREA_ID.fullmatch(viene_de) and 'lanzamiento' in out:
        if any(isinstance(r, dict) and r.get('tarea') == viene_de and r.get('decision') == 'seguir' for r in resultados(pid)):
            out['viene_de'] = viene_de
    motivo = sensitive(pid, files) or (str(t.get('motivo_importante') or 'El planificador pide tu decisión').strip()[:200] if t.get('importante') is True else '')
    if motivo: out['importante'] = motivo
    out['dificultad'] = t.get('dificultad') if t.get('dificultad') in LEVELS else 'media'
    return out

def norm_title(title):
    """Title as compared for duplicates: case-folded, spaces collapsed, no trailing punctuation or '…'."""
    return re.sub(r'[\W_]+$', '', re.sub(r'\s+', ' ', str(title or '')).strip().casefold())

def superseded(ts):
    """Blocked or triage task ids whose title matches a done task in the same list."""
    done = {norm_title(t.get('title')) for t in ts if t['status'] == 'done'}
    return {t['id'] for t in ts if t['status'] in ('blocked', 'triage') and norm_title(t.get('title')) in done}

def drop_duplicates(items, board_tasks):
    """Split validated proposals into (kept, dropped). A proposal is dropped when its title matches a board task that is
    not archived or blocked (blocked ones may be proposed again: that is their second chance), an earlier kept proposal
    of the same batch, or when it depends on a dropped one. depende_de of the kept ones is renumbered."""
    on_board = {}
    for t in board_tasks:
        key = norm_title(t.get('title'))
        if key and t.get('status') not in ('archived', 'blocked'): on_board.setdefault(key, t.get('id'))
    kept, dropped, seen, new_index = [], [], set(), {}
    for pos, item in enumerate(items):
        key = norm_title(item['titulo'])
        if key in on_board: dropped.append({'titulo': item['titulo'], 'duplicada_de': on_board[key]}); continue
        if key in seen: dropped.append({'titulo': item['titulo'], 'duplicada_de': 'el mismo lote'}); continue
        if any(d not in new_index for d in item.get('depende_de', [])):
            dropped.append({'titulo': item['titulo'], 'duplicada_de': 'depende de una duplicada'}); continue
        seen.add(key)
        new_index[pos] = len(kept)
        kept.append({**item, 'depende_de': [new_index[d] for d in item.get('depende_de', [])]})
    return kept, dropped

# Files whose change always waits for Owner, whatever the planner says. Tests never count. Per project: "importante_si".
SENSITIVE = ['package.json', '*/package.json', '*package-lock.json', '*.lock', '*lock.yaml', '*dockerfile*', '*wrangler*',
             '*railway*', 'fly.toml', '.github/*', '*migration*', '*schema*', '*auth*', '*tradovate*', '*databento*',
             '*projectx*', '*stripe*', '*payment*', '*checkout*', '*whop*', '*legal*', '*privacy*', '*terms*']

def sensitive(pid, files):
    import fnmatch
    patterns = SENSITIVE + project(pid).get('importante_si', [])
    for f in files:
        low = f.lower()
        if low.startswith(('test/', 'tests/')) or '.test.' in low or '/test/' in low: continue
        hit = next((pat for pat in patterns if fnmatch.fnmatch(low, pat.lower())), None)
        if hit: return f'Toca {f}, que siempre pasa por ti'
    return ''

def plan(pids):
    why = freno()
    if why:
        save(STATE / 'plan_aplazado.json', {'fecha': today(), 'motivo': why}); log('plan aplazado:', why)
        return {'fecha': today(), 'aplazado': why}
    file = STATE / 'proposals' / f'{today()}.json'
    batch = load(file, {'fecha': today(), 'proyectos': {}})
    for pid in pids:                         # learn from yesterday first, per project
        try: retro(pid)
        except (Stop, AssertionError) as e: log(pid, 'la retrospectiva falló:', e)
    try: mesa()                              # then the departments meet; agreements reach each planner
    except Exception as e: log('la mesa falló:', e)
    for pid in pids:
        if batch['proyectos'].get(pid, {}).get('estado') in {'pendiente', 'aprobado'}:
            log(pid, 'ya tiene lote hoy'); continue
        if plan_project(pid, batch, file)['estado'] == 'pendiente' and auto_approve(pid):
            try: auto_approve_pending(pid, 1)
            except Exception as e: log(pid, 'no he podido autoaprobar el lote:', e)
    batch = load(file, batch)
    ready = [project(pid)['name'] for pid in pids if batch['proyectos'].get(pid, {}).get('estado') == 'pendiente'
             and not auto_approve(pid)]
    if ready: notify('Lote del día listo', 'Aprueba las tareas de ' + ', '.join(ready) + ' en Entregas.')
    return batch

def plan_project(pid, batch, file, ronda=1, waiting=()):
    """Ask the planner for one batch of this project on top of the team branch. Round 1 is the morning plan;
    later rounds start when the previous one is finished (continuous mode)."""
    p = project(pid)
    log(pid, 'planificando…' if ronda == 1 else f'planificando la ronda {ronda}…')
    tip = git(p['repo'], 'rev-parse', f'equipo/{pid}')
    path = worktree(pid, '_plan', start=tip)
    board_tasks = tasks(pid)
    open_tasks = [f"{t['status']}: {t['title']}" for t in board_tasks if t['status'] != 'archived'][-25:]
    related = ''
    for slug in p.get('related_boards', []):
        try:
            with kb.scoped_current_board(slug), kbc.connect_closing(board=slug) as conn:
                ready = [t.title for t in kb.list_tasks(conn, status='ready')][:20]
            related += f'Backlog «{slug}» (tareas listas que Owner ya había preparado): ' + json.dumps(ready, ensure_ascii=False) + '\n'
        except Exception: pass
    if tendencias(pid): related += tendencias(pid) + '\n'
    medido = contenido_medido(pid)
    if medido: related += medido + '\n'
    pendiente = calendario_pendiente(pid)
    if pendiente: related += pendiente + '\n'
    aperturas = aperturas_medidas(pid)
    if aperturas: related += aperturas + '\n'
    analisis = analisis_contenido(pid)
    if analisis: related += analisis + '\n'
    notas_rehacer = _notas_para_rehacer(pid)
    referencias = _formato_para_rehacer(notas_rehacer)
    if referencias: related += referencias + '\n'
    cal = calendario_por_medir(pid, solo_cifras=True)
    if cal: related += cal + '\n'
    sin_enlace = sin_enlace_calendario(pid)
    if sin_enlace: related += sin_enlace + '\n'
    if ronda > 1:
        related += (f'Esta es la ronda {ronda} de hoy: las tareas de rondas anteriores ya están hechas o bloqueadas (ver tablero). '
                    'Propón el siguiente paso, sin repetir nada que ya esté hecho, en cola o bloqueado.\n')
    if waiting:
        related += ('Estas tareas esperan la decisión de Owner: no las repitas ni propongas nada que dependa de ellas: '
                    + json.dumps([t['titulo'] for t in waiting], ensure_ascii=False) + '\n')
    prompt = PLAN_PROMPT.format(validar=VALIDAR, estrategia=estrategia(), name=p['name'], goal=p['goal'], n=LIMITS['tasks_per_batch'], max_files=LIMITS['max_files'],
                                tests=', '.join(p['tests']), board=json.dumps(open_tasks, ensure_ascii=False) or '[]',
                                followups=json.dumps(load(STATE / 'followups' / f'{pid}.json', [])[-15:], ensure_ascii=False),
                                related=related, learning=planner_signals(pid), context=context_text(pid, path))
    folder = STATE / 'proposals' / today() / (pid if ronda == 1 else f'{pid}-r{ronda}')
    previous = batch['proyectos'].get(pid, {})
    history = previous.get('rondas_previas', []) + ([{k: previous[k] for k in ('ronda', 'estado', 'tarjetas') if k in previous}] if ronda > 1 and previous.get('estado') != 'error' else [])
    try:
        answer = claude(prompt, path, LIMITS['plan_seconds'], out=folder / 'planner.json', role='planificador')
        raw = json_block(answer)
        items = [validate_task(pid, t, i, root=path) for i, t in enumerate(raw[:LIMITS['tasks_per_batch']])]
        items, duplicadas = drop_duplicates(items, board_tasks)
        if duplicadas: log(pid, f'{len(duplicadas)} propuestas descartadas por estar ya en el tablero o repetidas')
        if notas_rehacer and items:
            titulos = [titulo.removesuffix('…').lower() for _, titulo, _ in notas_rehacer[:3]
                       if len(titulo.removesuffix('…')) >= 8]
            citan = sum(any(titulo in str(item.get(campo) or '').lower()
                            for titulo in titulos for campo in ('titulo', 'porque', 'detalle')) for item in items)
            citas_file = STATE / 'para_rehacer_citas' / f'{pid}.json'
            citas = load(citas_file, [])
            if not isinstance(citas, list): citas = []
            citas.append({'fecha': today(), 'ronda': ronda, 'propuestas': len(items), 'citan': citan})
            try: save(citas_file, citas[-60:])
            except OSError as e: log(pid, 'no se pudieron guardar las citas de Para rehacer:', e)
        entry = {'estado': 'pendiente' if items else 'sin propuestas', 'base': tip, 'tareas': items, 'creado': now().isoformat()}
        if duplicadas: entry['duplicadas'] = duplicadas
        if not items and duplicadas:
            entry['motivo'] = f'todo lo propuesto ya estaba en el tablero o repetido ({len(duplicadas)})'
            log(pid, 'lote vacío:', entry['motivo'])
        else: log(pid, f'{len(items)} tareas propuestas')
    except (Stop, AssertionError) as e:
        entry = {'estado': 'error', 'motivo': str(e)[:300], 'creado': now().isoformat()}
        log(pid, 'el plan falló:', e)
    entry['ronda'] = ronda
    if history: entry['rondas_previas'] = history
    with batch_lock():                       # projects plan in parallel processes: re-read before writing
        fresh = load(file, {'fecha': today(), 'proyectos': {}})
        fresh['proyectos'][pid] = entry
        save(file, fresh)
    batch.clear(); batch.update(fresh)
    return entry

@contextmanager
def batch_lock():
    """The day's proposals file is shared by the per-project processes; every read-modify-write holds this lock."""
    (STATE / 'proposals').mkdir(parents=True, exist_ok=True)
    with open(STATE / 'proposals' / '.lock', 'w') as fh:
        pf.lock(fh)
        yield

# ---------- continuous mode ----------
# When a project's approved batch is finished (every card done or blocked), the team plans the next round right away
# instead of idling until tomorrow. Owner approves it (Telegram/Entregas) unless he turned on auto-approval for that
# project. Bounded by limits.rounds_per_day; a round with no proposals or a failed plan ends the day for that project.

def continuous(pid): return bool(project(pid).get('continuo', CONFIG.get('continuo', False)))
def auto_approve(pid): return bool(project(pid).get('auto_aprobar', CONFIG.get('auto_aprobar', False)))

def held_positions(tareas):
    """Important tasks, plus any task that depends on one (it cannot run before Owner decides)."""
    held = set()
    for pos, t in enumerate(tareas):
        if t.get('importante') or any(d in held for d in t.get('depende_de', [])): held.add(pos)
    return held

def auto_approve_pending(pid, ronda):
    """Approve the routine part of a fresh batch; keep the important part waiting for Owner. Returns a notice text."""
    file = STATE / 'proposals' / f'{today()}.json'
    with batch_lock():
        entry = load(file, {}).get('proyectos', {}).get(pid, {})
        tareas = entry.get('tareas', [])
        held = held_positions(tareas)
        _approve([pid], hold=held, by='equipo')
    name = project(pid)['name']; normal = [t['titulo'] for pos, t in enumerate(tareas) if pos not in held]
    lines = [f"en marcha, {len(normal)} {'tarea' if len(normal) == 1 else 'tareas'}"]
    if held: lines.append(f"{len(held)} {'espera' if len(held) == 1 else 'esperan'} tu OK → /lote")
    notify(f'{name}: ronda {ronda}', ' · '.join(lines))
    return 'aprobada' if not held else 'parcial'

def round_finished(pid, entry):
    """Every card of the round is done or blocked. Important tasks still waiting for Owner do not stop the team."""
    cards = list((entry.get('tarjetas') or {}).values())
    if entry.get('estado') not in {'aprobado', 'pendiente'} or not cards: return False
    status = {t['id']: t['status'] for t in tasks(pid)}
    return all(status.get(c) in {'done', 'blocked', 'archived'} for c in cards)

def next_round(pid):
    """Called from run_project (under the project lock) when there is nothing ready to build."""
    if not continuous(pid) or (ROOT / 'PAUSED').exists(): return None
    file = STATE / 'proposals' / f'{today()}.json'
    batch = load(file, None)
    entry = (batch or {}).get('proyectos', {}).get(pid)
    if entry and entry.get('estado') == 'error': return retry_round(pid, auto=True)
    if not entry or not round_finished(pid, entry): return None
    ronda = entry.get('ronda', 1) + 1
    if ronda > LIMITS.get('rounds_per_day', 6):
        if not entry.get('tope_avisado'):
            with batch_lock():
                fresh = load(file, batch); fresh['proyectos'][pid]['tope_avisado'] = True; save(file, fresh)
            notify(f"{project(pid)['name']}: tope de rondas", f"Hoy ya se han hecho {ronda - 1} rondas. Mañana a las 7:30, más.")
        return None
    waiting = entry.get('tareas', []) if entry.get('estado') == 'pendiente' else []   # important, not decided yet
    new = plan_project(pid, batch, file, ronda, waiting=waiting)
    name = project(pid)['name']
    if waiting:
        with batch_lock():
            fresh = load(file, batch); cur = fresh['proyectos'][pid]
            cur['tareas'] = [{**t, 'depende_de': [], 'importante': t.get('importante') or 'Depende de otra tarea que espera tu decisión'} for t in waiting] + [{**t, 'depende_de': [d + len(waiting) for d in t['depende_de']]} for t in cur.get('tareas', [])]
            if cur['estado'] == 'sin propuestas': cur.pop('motivo', None)
            if cur['estado'] in {'sin propuestas', 'error'}: cur['estado'] = 'pendiente'
            save(file, fresh); new = cur
    if new['estado'] != 'pendiente': return new['estado']
    if auto_approve(pid): return auto_approve_pending(pid, ronda)
    notify(f'{name}: ronda {ronda} lista', 'apruébala → /lote')
    return 'pendiente'

def retry_round(pid, auto=False):
    """Plan again a round whose plan failed (planner error, an invalid task), keeping its number. The team retries on
    its own once per round; `equipo.py reintentar <proyecto>` relaunches it whenever Owner asks."""
    file = STATE / 'proposals' / f'{today()}.json'
    batch = load(file, None)
    entry = (batch or {}).get('proyectos', {}).get(pid)
    if not entry or entry.get('estado') != 'error' or (auto and entry.get('reintentado')): return None
    ronda = entry.get('ronda', 1)
    log(pid, f'relanzando la ronda {ronda} (el plan anterior falló: {entry.get("motivo", "")[:80]})')
    new = plan_project(pid, batch, file, ronda)
    if new['estado'] == 'error':
        with batch_lock():                   # never loop: one automatic retry per round
            fresh = load(file, batch); fresh['proyectos'][pid]['reintentado'] = True; save(file, fresh)
        return 'error'
    if new['estado'] != 'pendiente': return new['estado']
    if auto_approve(pid): return auto_approve_pending(pid, ronda)
    notify(f"{project(pid)['name']}: ronda {ronda} lista", 'el plan que falló ya está → /lote')
    return 'pendiente'

TELEGRAM_LINK = pf.app_data() / 'telegram.json'

def first_sentence(text, limit=160):
    """Telegram notices are read in a second: the first sentence, cut short if it runs on."""
    t = ' '.join(str(text or '').split())
    m = re.match(r'(.+?[.!?])(\s|$)', t)
    t = m.group(1) if m else t
    return t if len(t) <= limit else t[:limit - 1].rstrip() + '…'

def notify(title, text):
    """A macOS notification and, once Owner has linked his Telegram chat in the app, the same notice there, in one line
    when it fits (Owner reads them at a glance)."""
    pf.notify(con_nombre(title), con_nombre(text), 'Equipo de agentes')
    telegram(f'{title}: {text}' if '\n' not in text and len(title) + len(text) < 200 else f'{title}\n{text}')

# 01/11/2026 Owner: Alfred on WhatsApp, Telegram as the fallback. While the bridge (whatsapp/puente.mjs) is connected to his group
# it says so in Workspace/whatsapp/estado.json every few seconds, and notices go to its outbox: the same text and buttons, which
# it turns into polls. If it stops for five minutes, they go to Telegram again. configuracion.json → whatsapp.avisos: false turns it off.
def whatsapp_activo():
    if (APP_CONFIG.get('whatsapp') or {}).get('avisos') is False: return False
    e = load(TELEGRAM_LINK.parent / 'whatsapp' / 'estado.json', {})
    try: return e.get('conectado') is True and (now() - datetime.fromisoformat(e['hora'].replace('Z', '+00:00'))).total_seconds() < 300
    except (KeyError, TypeError, ValueError, AttributeError): return False

def whatsapp_encolar(text, botones=None):
    """One message for the bridge; returns its number (what Telegram code keeps as the message id)."""
    folder = TELEGRAM_LINK.parent / 'whatsapp' / 'salida'; folder.mkdir(parents=True, exist_ok=True)
    ref = int(time.time() * 1000)
    while (folder / f'{ref}-equipo.json').exists(): ref += 1
    tmp = folder / f'.{ref}.tmp'
    tmp.write_text(json.dumps({'ref': ref, 'texto': con_nombre(text)[:3800], **({'botones': botones} if botones else {})}, ensure_ascii=False))
    tmp.rename(folder / f'{ref}-equipo.json')
    return ref

# 04/11/2026 Owner: «me mandan una actualización técnica de esas de ya lo he hecho, no sé qué… no sé qué tengo que hacer. No lo
# explica bien». Every team notice that reaches him (telegram(): WhatsApp first, Telegram as fallback) is rewritten by Sonnet into
# what happened and what HE has to do, with what each button does, in street words. Cached by text; with the brake on, or if
# Claude fails, the original goes out. Short one-liners («Hoy: 3/5 hecho») are already plain and are not touched.
LLANO_JERGA = re.compile(r'fusion|commit|rama|merge|\bPR\b|worktree|deploy|despleg|lote|tablero|experimento|métrica|umbral|revisor|constructor|'
                         r'build|backend|frontend|endpoint|repo|pipeline|tarea t_|bloquead|integrad', re.I)
LLANO_PROMPT = """Owner no programa y lee esto en el móvil en un segundo. Su equipo de agentes le manda este aviso y no lo entiende.
Reescríbelo en español de España, como se lo diría un mayordomo de confianza, en este formato y nada más:
<una línea: qué ha pasado, en palabras de la calle>
Tú: <lo único que tiene que hacer él, concreto (qué abrir, qué mirar, qué decidir) y cuánto tarda; o «nada, es solo para que lo sepas»>
{botones}
Prohibido: fusionar, commit, rama, PR, merge, worktree, desplegar, lote, tablero, experimento, métrica, umbral, revisor, constructor,
backend, frontend, ids de tareas, rutas de archivos y siglas. «Fusionar» se dice «juntar los cambios para que estén en la versión buena»;
«lanzar» se dice con lo que es (publicar, mandar, probar). Conserva nombres de proyectos, cifras y enlaces. No inventes nada que no esté.
Aviso:
{texto}"""

def en_cristiano(text, botones=None):
    t = str(text or '').strip()
    if not t or (len(t) <= 140 and '\n' not in t and not LLANO_JERGA.search(t)): return text
    nombres = [b.get('text') for fila in (botones or []) if isinstance(fila, list) for b in fila if isinstance(b, dict) and b.get('text')]
    file = STATE / 'llano.json'; cache = load(file, {})
    clave = digest(t + '|' + '|'.join(nombres))
    if isinstance(cache.get(clave), str): return cache[clave]
    if freno(): return text
    linea = ('Botones: ' + ', '.join(f'«{n}»' for n in nombres) + '. Termina con una línea por botón: «<nombre exacto> = qué pasa si lo pulsa».') if nombres else ''
    try: out = (claude(LLANO_PROMPT.format(botones=linea, texto=t[:4000]), ROOT, 90, role='respuestas') or '').strip()
    except Exception as e: log('en cristiano: no he podido reescribir el aviso:', e); return text
    if not out or len(out) > 900 or 'Tú:' not in out: return text
    cache = {k: v for k, v in list(cache.items())[-199:]}; cache[clave] = out; save(file, cache)
    return out

def telegram(text, botones=None):
    """Best effort: only to the one chat Owner linked; the bot token never leaves the Keychain except to Telegram."""
    text = en_cristiano(text, botones if isinstance(botones, list) else None)
    if whatsapp_activo(): return bool(whatsapp_encolar(text, botones if isinstance(botones, list) else None))
    link = load(TELEGRAM_LINK, None)
    if not link or not isinstance(link.get('chatId'), int): return False
    token = pf.secret('prompted-os-telegram')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token): return False
    import urllib.request
    body = {'chat_id': link['chatId'], 'text': con_nombre(text)[:3800], 'disable_web_page_preview': True}
    if isinstance(botones, list): body['reply_markup'] = {'inline_keyboard': botones}
    body = json.dumps(body).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', body, {'Content-Type': 'application/json'}), timeout=15).read()
        return True
    except Exception as e:
        log('telegram: aviso no enviado', type(e).__name__)
        return False

def telegram_send(text, markup=None):
    """Like telegram(), with buttons; returns the message id (None if it could not be sent)."""
    if whatsapp_activo(): return whatsapp_encolar(text, (markup or {}).get('inline_keyboard'))
    link = load(TELEGRAM_LINK, None)
    if not link or not isinstance(link.get('chatId'), int): return None
    token = pf.secret('prompted-os-telegram')
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]{20,}', token): return None
    import urllib.request
    body = {'chat_id': link['chatId'], 'text': con_nombre(text)[:3800], 'disable_web_page_preview': True, **({'reply_markup': markup} if markup else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(f'https://api.telegram.org/bot{token}/sendMessage', json.dumps(body).encode(), {'Content-Type': 'application/json'}), timeout=15) as r:
            return json.loads(r.read()).get('result', {}).get('message_id')
    except Exception as e:
        log('telegram: mensaje no enviado', type(e).__name__)
        return None

# ---------- today's to-do when the app is closed ----------
# The app writes and sends it from nine (local-workspace/hoy.ts). When the app is closed (19/10: no list came because it
# was), the launchd run does the same — every 5 min, and on waking — with the same file, message and buttons, so the
# app takes over when it opens (it handles the taps Telegram keeps for it and moves the tasks to Reminders).
WORKSPACE = TELEGRAM_LINK.parent
HOY_HORA, HOY_ULTIMA, HOY_MAX = 9, 21, 5
DIAS = ['lun', 'mar', 'mié', 'jue', 'vie', 'sáb', 'dom']

def app_abierta():
    return subprocess.run(['/usr/bin/pgrep', '-f', 'Prompted OS.app/Contents/MacOS/'], capture_output=True).returncode == 0

def hoy_items(texto):
    """Alfred's reply → at most five short, unique tasks with their plain explanation (hoy.ts parseItems):
    «- Título :: qué hacer, dónde y cuánto tardas»."""
    partes = [] if '\n' in texto else re.split(r'\s+\|\s+|\s+-\s+(?=[A-ZÁÉÍÓÚÑ¿])', texto)
    filas = ['- ' + re.sub(r'^\s*[-•*]\s+', '', x) for x in partes] if len(partes) > 1 else texto.split('\n')
    out = []
    for fila in filas:
        m = re.match(r'^\s*(?:[-•*]|\d+[.)])\s+(.+)$', fila)
        if not m: continue
        limpio = lambda s: ' '.join(re.sub(r'\*\*|__|`', '', s).split()).rstrip('.;')
        titulo, _, nota = m.group(1).partition('::')
        t, n = limpio(titulo), limpio(nota)
        if len(t) > 70: t = t[:69].rstrip() + '…'
        if len(n) > 240: n = n[:239].rstrip() + '…'
        if t and t.lower() not in (o['t'].lower() for o in out): out.append({'t': t, 'n': n})
        if len(out) >= HOY_MAX: break
    return out

def hoy_lineas(texto):
    """Only the titles (the same rules as hoy.ts parse)."""
    return [i['t'] for i in hoy_items(texto)]

def hoy_detalles():
    """What each waiting decision is and what Owner has to do, so the to-do can explain it (equipo-acciones.ts detallesParaTi)."""
    try: grupos = para_ti_vigente() if load(STATE / 'para_ti.json', {}).get('grupos') else []
    except Exception: grupos = []
    return [' — '.join(x for x in [g['titulo'], g.get('que_es') and 'qué es: ' + g['que_es'], g.get('tu_parte') and 'su parte: ' + g['tu_parte']] if x)[:400]
            for g in grupos if g.get('titulo')][:8]

def hoy_decisiones():
    """What only Owner can unblock today, in the order the app uses (equipo-acciones.ts decisionesDelEquipo): experiments
    to launch or measure, decisions waiting in a batch, work ready to merge, and the team's real questions."""
    out = []
    try: grupos = para_ti_vigente() if load(STATE / 'para_ti.json', {}).get('grupos') else []   # only once explained
    except Exception: grupos = []
    if grupos:          # the plain words, grouped (what Owner reads in /lote)
        out = [g['titulo'][0].lower() + g['titulo'][1:] + ' (/lote)' for g in grupos if g['tipo'] != 'pregunta' and g.get('titulo')]
        n = sum(len(g['claves']) for g in grupos if g['tipo'] == 'pregunta')
        for pid in CONFIG['projects']:
            try: k = int(pending_merge(pid) or 0)
            except Exception: k = 0
            if k: out.append(f"juntar {k} {'mejora' if k == 1 else 'mejoras'} de {project(pid)['name']} ya {'revisada' if k == 1 else 'revisadas'} (/lote)")
        if n: out.append(f"responder {n} {'pregunta' if n == 1 else 'preguntas'} del equipo (/mesa)")
        return out
    try:
        items = tablero(); lanzar = sum(1 for i in items if i.get('fase', '').startswith('lista para lanzar')); medir = sum(1 for i in items if i.get('fase') == 'falta medir')
        if lanzar: out.append(f"lanzar {lanzar} {'experimento' if lanzar == 1 else 'experimentos'} (/lote)")
        if medir: out.append(f"apuntar lo medido de {medir} {'experimento' if medir == 1 else 'experimentos'} (/lote)")
    except Exception: pass
    for pid, entry in (load(STATE / 'proposals' / f'{today()}.json', {}).get('proyectos') or {}).items():
        n = len(entry.get('tareas') or [])
        if pid in CONFIG['projects'] and entry.get('estado') == 'pendiente' and n: out.append(f"decidir {n} {'cosa' if n == 1 else 'cosas'} de {project(pid)['name']} (/lote)")
    for pid in CONFIG['projects']:
        try: n = int(pending_merge(pid) or 0)
        except Exception: n = 0
        if n: out.append(f"{project(pid)['name']}: {n} cambios por fusionar")
    m = mesa_load()['mensajes']
    asked = [x for x in m if 'owner' in x.get('para', []) and x.get('tipo') == 'pregunta' and x.get('de') != 'owner' and not any(r.get('de') == 'owner' and r.get('responde_a') == x['id'] for r in m)]
    if asked: out.append(f"responder {len(asked)} {'pregunta' if len(asked) == 1 else 'preguntas'} del equipo (/mesa)")
    return out

def hoy_prompt(decisiones, ayer, detalles=()):
    abiertas = [i['t'] for i in (ayer or {}).get('items', []) if not i.get('hecho')]
    hechas = [i['t'] for i in (ayer or {}).get('items', []) if i.get('hecho')]
    leer = lambda p, n: (p.read_text(errors='ignore')[:n] if p.exists() else '')
    return '\n'.join(x for x in [
        f'Eres Alfred, el asistente personal de Owner. Escribe su to-do de hoy: como mucho {HOY_MAX} tareas, una por línea, con este formato exacto: "- Título :: explicación". '
        'El título empieza por un verbo y tiene menos de 60 caracteres. La explicación es una frase llana que dice exactamente qué hacer, dónde (qué app, web o sitio) y cuánto tarda, '
        'para que la entienda a la primera sin contexto: nada de jerga, siglas ni nombres internos del equipo («lote», «experimento», «fusión»). Si no puedes explicar una tarea así de claro, no la pongas. '
        'Elige lo que más le acerca a sus objetivos hoy y lo que solo puede hacer él.',
        'Decisiones del equipo que le esperan: ' + '; '.join(decisiones) + '.' if decisiones else 'El equipo no le espera para nada.',
        'Qué es cada una y qué le toca hacer: ' + ' | '.join(detalles) + '.' if detalles else '',
        'De ayer quedó sin hacer: ' + '; '.join(abiertas) + '. Si sigue importando, mantenlo.' if abiertas else '',
        'Ayer hizo: ' + '; '.join(hechas) + '.' if hechas else '',
        'Sus objetivos:\n' + leer(VAULT / 'Objetivos.md', 4000), 'Su rutina:\n' + leer(VAULT / 'Personal' / 'Rutina.md', 3000),
        'Responde solo con la lista: sin saludo, sin explicaciones, sin nada más.'] if x)

def hoy_guardados(folder, fecha, items):
    """hoy/futuro.json (hoy.ts takeLater): the items kept for this day or an earlier one go first. Returns the list and what
    stays kept (None: nothing to change), written only once the message has gone."""
    todos = load(folder / 'futuro.json', [])
    if not isinstance(todos, list): return items, None
    toca = [x for x in todos if isinstance(x, dict) and str(x.get('fecha', '9')) <= fecha and x.get('t')]
    if not toca: return items, None
    vistos = {i['t'].lower() for i in items}
    return [{'t': x['t'], 'hecho': False, **({'n': x['n']} if x.get('n') else {})} for x in toca if x['t'].lower() not in vistos] + items, [x for x in todos if x not in toca]


def hoy_manana(at=None):
    """Writes and sends today's to-do if it is time, the app is closed and nobody has sent it yet. Returns the tasks."""
    at = at or now()
    if not (HOY_HORA <= at.hour < HOY_ULTIMA) or app_abierta(): return None
    if not isinstance((load(TELEGRAM_LINK, None) or {}).get('chatId'), int) and not whatsapp_activo(): return None
    fecha = at.date().isoformat(); folder = WORKSPACE / 'hoy'; file = folder / f'{fecha}.json'
    if file.exists(): return None
    folder.mkdir(parents=True, exist_ok=True)
    save(file, {'fecha': fecha, 'items': [], 'enviado': 'enviando'})      # the app must not send a second one meanwhile
    try:
        decisiones = hoy_decisiones()
        ayer = load(folder / f'{(at - timedelta(days=1)).date().isoformat()}.json', None)
        lineas, notas, por = [], {}, ''
        if freno(): por = 'freno de presupuesto'
        else:
            try:
                hechos = hoy_items(claude(hoy_prompt(decisiones, ayer, hoy_detalles()), VAULT if VAULT.exists() else ROOT, 180, role='respuestas'))
                lineas = [i['t'] for i in hechos]; notas = {i['t']: i['n'] for i in hechos if i['n']}
            except Exception as e: por = type(e).__name__ + ': ' + str(e)[:120]
        if not lineas:
            por = por or 'respuesta sin lista'
            lineas = [d[0].upper() + d[1:] for d in decisiones] + [i['t'] for i in (ayer or {}).get('items', []) if not i.get('hecho')]
            lineas = lineas[:HOY_MAX]
        items = [{'t': t, 'hecho': False, **({'n': notas[t]} if t in notas else {})} for t in lineas]
        items, resto = hoy_guardados(folder, fecha, items)      # what Owner asked to be reminded of on this day (<<hoy:AAAA-MM-DD+…>>)
        if not items: file.unlink(missing_ok=True); return []
        texto = '\n'.join([f'Hoy, {DIAS[at.weekday()]} {at.day}'] + [l for i in items for l in ['○ ' + i['t']] + (['   ' + i['n']] if i.get('n') else [])])
        botones = {'inline_keyboard': [[{'text': '✓ ' + (i['t'] if len(i['t']) <= 34 else i['t'][:33] + '…'), 'callback_data': f'h:{fecha}:{n}'}] for n, i in enumerate(items)]}
        mensaje = telegram_send(texto, botones)
        if mensaje is None: file.unlink(missing_ok=True); return None
        save(file, {'fecha': fecha, 'items': items, 'enviado': at.isoformat(timespec='seconds'), 'mensaje': mensaje})
        if resto is not None: save(folder / 'futuro.json', resto)
        with open(folder / 'registro.log', 'a') as fh: fh.write(f"{at.isoformat(timespec='seconds')} {fecha} equipo: {'Alfred ' + str(len(items)) + ' tareas' if not por else 'respaldo · ' + por}\n")
        tarjetas = load(WORKSPACE / 'repaso' / 'tarjetas.json', [])
        toca = min(10, sum(1 for c in tarjetas if isinstance(c, dict) and c.get('due', '9') <= fecha))
        if toca: telegram_send(f"Repaso de hoy: {toca} {'tarjeta' if toca == 1 else 'tarjetas'} (2 min).", {'inline_keyboard': [[{'text': 'Empezar', 'callback_data': 'r:s'}]]})
        return [i['t'] for i in items]
    except Exception:
        if load(file, {}).get('enviado') == 'enviando': file.unlink(missing_ok=True)
        raise

# ---------- good morning: today's workout, food and what waits for him (04/11/2026, Owner: «que sea un mayordomo… prepararme los
# entrenamientos, las comidas, ayudar en el día a día»). One message at BUENOS_DIAS (configuracion.json → manana.hora, «08:30»),
# app open or closed, before the 9:00 to-do (which carries the agenda). Built only from his own notes: the plan, the last sessions he
# logged and his alerts in Personal/Salud y deporte, and his routine; loads come from what he really did, never invented. The text is
# kept in Workspace/manana/<fecha>.md so Alfred knows what was proposed when Owner reports the session.
BUENOS_DIAS_FIN = 12
SALUD_NOTAS = ('00-Contexto/AGENTS.md', '03-Planes/Plan-vigente.md', '04-Salud/Alertas-y-recuperacion.md', '00-Contexto/Perfil-y-objetivos.md')

def buenos_dias_hora():
    v = str(((APP_CONFIG.get('manana') or {}).get('hora')) or '08:30')
    m = re.fullmatch(r'(\d{1,2}):(\d{2})', v)
    return (int(m.group(1)), int(m.group(2))) if m else (8, 30)

def buenos_dias_prompt(at, decisiones):
    base = VAULT / 'Personal' / 'Salud y deporte'
    leer = lambda p, n: p.read_text(errors='ignore')[-n:] if p.exists() else ''
    ayer = WORKSPACE / 'manana' / f'{(at - timedelta(days=1)).date().isoformat()}.md'
    return '\n\n'.join(x for x in [
        f'Eres Alfred, el mayordomo de Owner. Hoy es {DIAS[at.weekday()]} {at.day}. Escríbele el mensaje de buenos días para WhatsApp, en español de España, '
        'que se lea en 20 segundos, con este formato exacto y nada más:',
        'Buenos días, Owner. <una frase breve y natural, sin rollo gurú>\n\n🏋️ Entreno de hoy: <tipo de sesión>\n- Calentamiento: <8–10 min concretos, movilidad de cadera si hay bisagra>\n'
        '- <ejercicio>: <carga> · <series×reps>\n<…de 4 a 6 ejercicios, o el cardio con duración y ritmo, o «descanso activo» con qué hacer>\n\n'
        '🍽️ Comidas: <una línea práctica para hoy: proteína, verdura e hidrato, una idea concreta de comida o cena>\n\n'
        '<si hay algo que le espera, una línea: «📌 Te espera: …» en palabras llanas; si no, nada>\n\nCuando acabes, mándame una nota de voz con lo que hiciste y lo apunto.',
        'Reglas del entreno: elige la sesión que toca según la estructura semanal del plan y lo que hizo en los últimos días (no repitas el mismo grupo dos días seguidos). '
        'Las cargas salen de lo último que REALIZÓ en ese ejercicio (mancuernas por mano, barra con su total); si no hay dato, «peso cómodo, RIR 2». '
        'Respeta las alertas abiertas y lo que diga su perfil. No inventes datos.',
        'Lo que le propusiste ayer (si no consta en el diario, no sabes si lo hizo):\n' + leer(ayer, 1500) if ayer.exists() else '',
        *[f'{n}:\n' + leer(base / n, 3500) for n in SALUD_NOTAS if (base / n).exists()],
        'Últimas sesiones registradas (Diario-de-entrenamiento, lo más reciente al final):\n' + leer(base / '01-Registros' / 'Diario-de-entrenamiento.md', 5000),
        'Su rutina:\n' + leer(VAULT / 'Personal' / 'Rutina.md', 2500),
        'Lo que le espera del equipo (resúmelo en una línea llana o no pongas nada): ' + '; '.join(decisiones) if decisiones else ''] if x)

def buenos_dias(at=None):
    """Once a day from BUENOS_DIAS until noon. Returns the text sent, or None."""
    at = at or now()
    if (at.hour, at.minute) < buenos_dias_hora() or at.hour >= BUENOS_DIAS_FIN: return None
    if not isinstance((load(TELEGRAM_LINK, None) or {}).get('chatId'), int) and not whatsapp_activo(): return None
    if not (VAULT / 'Personal' / 'Salud y deporte').exists(): return None
    folder = WORKSPACE / 'manana'; file = folder / f'{at.date().isoformat()}.md'
    if file.exists() or freno(): return None
    folder.mkdir(parents=True, exist_ok=True)
    file.write_text('enviando\n')                     # never twice, even if Claude or the send fails below
    try: decisiones = hoy_decisiones()
    except Exception: decisiones = []
    try: texto = (claude(buenos_dias_prompt(at, decisiones), VAULT, 180, role='respuestas') or '').strip()
    except Exception as e: log('buenos días: no he podido escribirlo:', e); texto = ''
    if not texto.startswith('Buenos días') or len(texto) > 2500:
        file.write_text(f'sin mensaje ({at:%H:%M})\n'); return None
    file.write_text(texto + '\n')
    telegram_send(texto)
    return texto

# ---------- what Owner is asked, in plain words ----------
# 19/10: «Aprueba el documento Docs 28…», «¿lanzar el qué?». Everything waiting for Owner is explained in plain Spanish — what
# it is, what his part is, why — and near-duplicates are grouped (six launch cards were the same 7-day test of Automate).
# Cached by content: Claude is asked again only when the list changes. status()['para_ti'] carries it to Telegram.
PARA_TI_TIPOS = ('lanzar', 'medir', 'decidir', 'pregunta')
TU_PARTE = {'lanzar': 'Ponerlo en marcha tú y avisar con el botón.', 'medir': 'Contar qué salió con el botón.',
            'decidir': 'Decir sí o no.', 'pregunta': 'Responder sí o no.'}

def pendientes_tuyas(tb=None):
    """Everything that waits for Owner, each with a stable key: l:<task> launch, m:<task> measure, d:<project>:<index>
    decide a batch task, q:<message> answer the team."""
    items = []
    if tb is None:
        try: tb = tablero()
        except Exception: tb = []
    for t in tb:
        fase = t.get('fase') or ''
        if t.get('columna') != 'para-ti' or not t.get('lanzamiento') or not t.get('id'): continue
        if fase.startswith('lista para lanzar'): items.append({'clave': f"l:{t['id']}", 'tipo': 'lanzar', 'proyecto': t.get('proyecto'), 'titulo': t.get('titulo', ''), 'ficha': t['lanzamiento']})
        elif fase == 'falta medir': items.append({'clave': f"m:{t['id']}", 'tipo': 'medir', 'proyecto': t.get('proyecto'), 'titulo': t.get('titulo', ''), 'ficha': t['lanzamiento']})
    for pid, entry in (load(STATE / 'proposals' / f'{today()}.json', {}).get('proyectos') or {}).items():
        if pid not in CONFIG['projects'] or entry.get('estado') != 'pendiente': continue
        idx = entry.get('indices') or list(range(len(entry.get('tareas') or [])))
        for pos, t in enumerate(entry.get('tareas') or []):
            items.append({'clave': f'd:{pid}:{idx[pos]}', 'tipo': 'decidir', 'proyecto': pid, 'titulo': t.get('titulo', ''),
                          'motivo': t.get('importante') if isinstance(t.get('importante'), str) else t.get('motivo_importante', ''), 'porque': t.get('porque', '')})
    m = mesa_load()['mensajes']
    for x in m:
        if x.get('tipo') == 'pregunta' and 'owner' in x.get('para', []) and x.get('de') != 'owner' and not any(r.get('de') == 'owner' and r.get('responde_a') == x['id'] for r in m):
            items.append({'clave': f"q:{x['id']}", 'tipo': 'pregunta', 'de': x.get('de'), 'texto': x.get('texto', '')})
    return items

def primera_frase(t):
    """The first sentence of the team's «why» (the rest is detail for the builder)."""
    t = ' '.join(str(t or '').split()); m = re.match(r'(.+?[.!?])(\s|$)', t)
    return (m.group(1) if m else t)[:300]

def _grupo_id(claves): return hashlib.sha1('|'.join(sorted(claves)).encode()).hexdigest()[:8]

def grupos_validos(raw, items):
    """Claude's grouping, checked: known keys only, one type per group, every key once; anything left out gets its own
    plain fallback group, so nothing that waits for Owner can disappear."""
    por_clave = {i['clave']: i for i in items}; usadas = set(); grupos = []
    for g in raw if isinstance(raw, list) else []:
        if not isinstance(g, dict): continue
        claves = [c for c in g.get('claves', []) if c in por_clave and c not in usadas]
        if not claves: continue
        tipo = por_clave[claves[0]]['tipo']; claves = [c for c in claves if por_clave[c]['tipo'] == tipo]
        usadas.update(claves)
        txt = lambda k, n: ' '.join(str(g.get(k) or '').split())[:n]
        grupos.append({'id': _grupo_id(claves), 'claves': claves, 'tipo': tipo, 'proyecto': por_clave[claves[0]].get('proyecto'),
                       'titulo': txt('titulo', 60) or por_clave[claves[0]].get('titulo', '')[:60], 'que_es': txt('que_es', 400),
                       'tu_parte': txt('tu_parte', 300) or TU_PARTE[tipo], 'por_que': txt('por_que', 300), 'si_dices_que_si': txt('si_dices_que_si', 300)})
    for c, i in por_clave.items():
        if c in usadas: continue
        base = i.get('titulo') or i.get('texto') or ''
        grupos.append({'id': _grupo_id([c]), 'claves': [c], 'tipo': i['tipo'], 'proyecto': i.get('proyecto'), 'titulo': base[:60],
                       'que_es': (primera_frase(i.get('porque')) or i.get('motivo') or (i.get('ficha') or {}).get('que') or base)[:400], 'tu_parte': TU_PARTE[i['tipo']], 'por_que': '', 'si_dices_que_si': ''})
    return grupos

def para_ti():
    """The plain-words list of what waits for Owner (cached by content)."""
    items = pendientes_tuyas(); file = STATE / 'para_ti.json'; cache = load(file, {})
    clave = digest(json.dumps(sorted((i['clave'], i.get('titulo') or i.get('texto', '')) for i in items), ensure_ascii=False))
    if cache.get('clave') == clave: return cache.get('grupos', [])
    if not items: save(file, {'clave': clave, 'grupos': []}); return []
    raw = None
    if not freno():
        nombres = {pid: project(pid)['name'] for pid in CONFIG['projects']}
        material = [{**{k: v for k, v in i.items() if k != 'proyecto'}, 'proyecto': nombres.get(i.get('proyecto'), i.get('proyecto')),
                     **({'de': DEPARTMENTS.get(i['de'], {}).get('nombre', i['de'])} if i.get('de') else {})} for i in items]
        prompt = ('Owner no programa y se pierde con la jerga. Esto es lo que su equipo de agentes le está pidiendo ahora mismo.\n'
                  'Agrupa lo que en el fondo es lo mismo (por ejemplo, varias fichas del mismo experimento) y explica cada grupo en español llano, '
                  'como a un socio de negocio: sin nombres de archivos, comandos, variables, códigos internos ni siglas (nada de rutas, BAD_TOKEN, '
                  'KC-R3, LOTE-1, webhook o curl; «paper» se dice «cuenta de prueba sin dinero real»).\n'
                  'Tipos: lanzar = algo que el equipo dejó listo y que solo Owner puede poner en marcha (publicar, enviar, probarlo él); '
                  'medir = algo que Owner ya puso en marcha y del que tiene que contar el resultado; decidir = una tarea que el equipo quiere '
                  'hacer y necesita su sí; pregunta = una pregunta del equipo. No mezcles tipos en un grupo y pon cada clave en un solo grupo.\n'
                  'Responde SOLO con un bloque ```json```: [{"claves": ["..."], "tipo": "...", "titulo": "verbo + qué, menos de 50 caracteres '
                  '(ej. «Publicar 3 reels de tu marca»)", "que_es": "una frase de menos de 140 caracteres", "tu_parte": "qué tiene que '
                  'hacer él exactamente y cuánto tarda, menos de 120 caracteres", "por_que": "para qué sirve, menos de 100 caracteres", '
                  '"si_dices_que_si": "solo en decidir: qué pasará, menos de 120 caracteres"}]\nSe lee en el móvil en un segundo: frases cortas.\n'
                  'Ordénalos por lo que más le acerca a validar con el mercado o a ganar dinero.\n\n' + json.dumps(material, ensure_ascii=False)[:24000])
        try: raw = json_block(claude(prompt, ROOT, 180, role='respuestas'))
        except Exception as e: log('para Owner: no he podido explicarlo:', e)
    grupos = grupos_validos(raw, items)
    if raw is not None: save(file, {'clave': clave, 'generado': now().isoformat(timespec='seconds'), 'grupos': grupos})
    return grupos

def para_ti_vigente(tb=None):
    """The cached plain list, keeping only what still waits for Owner (for status, without calling Claude)."""
    items = pendientes_tuyas(tb); vivas = {i['clave'] for i in items}
    grupos = []
    for g in load(STATE / 'para_ti.json', {}).get('grupos', []):
        claves = [c for c in g.get('claves', []) if c in vivas]
        if claves: grupos.append({**g, 'claves': claves})
    # anything new since the last explanation shows up as it is until the next run explains it
    vistas = {c for g in grupos for c in g['claves']}
    return grupos + grupos_validos(None, [i for i in items if i['clave'] not in vistas])

# ---------- the coach: closing the day and the week, and review cards from the week's work ----------
# One line at night («Hoy: 3/5 hecho. Mañana sigo con: X»), two lines on Sunday (tasks done, review right the first time,
# what keeps slipping) and, once a week, up to 8 review cards from what Owner worked on, so the active recall does not
# depend on him remembering to feed it. Numbers come only from his files; nothing is estimated.
CIERRE_DIA = (21, 30)
SEMANA_DIA, SEMANA_HORA = 6, 20          # Sunday, from 20:00

def cierre_dia(at=None):
    at = at or now()
    if (at.hour, at.minute) < CIERRE_DIA: return None
    file = WORKSPACE / 'hoy' / f'{at.date().isoformat()}.json'; day = load(file, None)
    if not day or not day.get('items') or day.get('cierre') or not day.get('mensaje'): return None
    hechas = sum(1 for i in day['items'] if i.get('hecho')); total = len(day['items'])
    abiertas = [i['t'] for i in day['items'] if not i.get('hecho')]
    texto = f'Hoy: {hechas}/{total} hecho.' + (f' Mañana sigo con: {abiertas[0]}.' if abiertas else ' Todo cerrado.')
    day = load(file, day); day['cierre'] = at.isoformat(timespec='seconds'); save(file, day)   # before sending: never twice
    telegram(texto)
    return texto

def cierre_semana(at=None):
    at = at or now()
    if at.weekday() != SEMANA_DIA or at.hour < SEMANA_HORA: return None
    semana = at.strftime('%G-W%V'); marca = WORKSPACE / 'hoy' / 'semanas.json'; hechas_semanas = load(marca, {})
    if semana in hechas_semanas: return None
    dias = [load(WORKSPACE / 'hoy' / f'{(at - timedelta(days=n)).date().isoformat()}.json', None) for n in range(7)]
    items = [i for d in dias if d for i in d.get('items', [])]
    if not items: return None
    hechas = sum(1 for i in items if i.get('hecho'))
    arrastra = {}
    for d in dias:
        for i in (d or {}).get('items', []):
            if not i.get('hecho'): arrastra[i['t'].lower()] = (arrastra.get(i['t'].lower(), (0, i['t']))[0] + 1, i['t'])
    peor = max(arrastra.values(), default=(0, ''))
    desde = (at - timedelta(days=6)).date().isoformat(); respuestas = []
    try:
        for line in (WORKSPACE / 'repaso' / 'registro.jsonl').read_text().splitlines():
            r = json.loads(line)
            if r.get('fecha', '') >= desde: respuestas.append(r)
    except (OSError, ValueError): pass
    linea = f'Semana: {hechas}/{len(items)} tareas'
    if respuestas: linea += f" · repaso {len(respuestas)} ({round(100 * sum(1 for r in respuestas if r.get('g') == 2) / len(respuestas))} % a la primera)"
    texto = linea + '.' + (f"\nSe arrastra: «{peor[1]}» ({peor[0]} días). ¿La hacemos o la quitamos?" if peor[0] >= 3 else '')
    hechas_semanas[semana] = at.isoformat(timespec='seconds'); save(marca, hechas_semanas)
    telegram(texto)
    return texto

REPASO_MAX = 8
PRIVADO = ('Administración', '.obsidian', '.trash', '08-Archive')

def repaso_semanal(at=None):
    """Up to 8 review cards from the week's work: the vault notes touched this week (never Administración) and the mesa's
    agreements. Not the team's lessons: those are about how the agents code and test (19/10: half the first deck was that).
    On Sundays, and the first time on any day so there is something to review."""
    at = at or now(); semana = at.strftime('%G-W%V')
    marca = WORKSPACE / 'repaso' / 'semanal.json'; previa = load(marca, {})
    if previa.get('semana') == semana or (previa and at.weekday() != SEMANA_DIA) or freno(): return None
    desde = time.time() - 7 * 86400; notas = []
    if VAULT.exists():
        for f in VAULT.rglob('*.md'):
            rel = f.relative_to(VAULT)
            if any(part in PRIVADO or part.startswith('.') for part in rel.parts): continue
            try: m = f.stat().st_mtime
            except OSError: continue
            if m >= desde: notas.append((m, rel, f))
    notas = sorted(notas, reverse=True)[:8]
    material = [f'## {rel}\n' + f.read_text(errors='ignore')[:1500] for _, rel, f in notas]
    for n in range(7):
        for a in (mesa_load((at - timedelta(days=n)).date().isoformat()).get('reunion') or {}).get('acuerdos', [])[:2]:
            material.append('Acuerdo de la mesa: ' + str(a.get('titulo', ''))[:200])
    if not material: save(marca, {'semana': semana, 'añadidas': 0}); return 0
    prompt = ('Eres Alfred y preparas tarjetas de repaso (active recall) para Owner con lo que ha trabajado esta semana.\n'
              f'Crea como mucho {REPASO_MAX} tarjetas sobre ideas que le conviene recordar: conceptos, reglas, decisiones y lecciones de sus proyectos '
              '(negocio, trading, contenido) y de lo que estudia. Preguntas cortas que obliguen a recordar (no de sí o no), respuestas de una frase. '
              'Nada de datos personales sensibles ni de cifras inventadas: solo lo que está en el material. Nada sobre programación, pruebas de código '
              'ni el funcionamiento interno del equipo de agentes: Owner no programa, le sirve lo de negocio, mercado, contenido y lo que aprende.\n'
              'Responde SOLO con un bloque ```json```: [{"tema": "una o dos palabras", "pregunta": "...", "respuesta": "..."}]\n\nMaterial:\n' + '\n\n'.join(material)[:24000])
    cards = json_block(claude(prompt, VAULT if VAULT.exists() else ROOT, 240, role='respuestas'))
    file = WORKSPACE / 'repaso' / 'tarjetas.json'; existing = load(file, [])
    norm = lambda t: re.sub(r'[^a-z0-9 ]', '', unicodedata.normalize('NFD', str(t).lower()).encode('ascii', 'ignore').decode()).strip()
    seen = {norm(c.get('q', '')) for c in existing if isinstance(c, dict)}; added = 0; fecha = at.date().isoformat()
    for c in (cards if isinstance(cards, list) else [])[:REPASO_MAX]:
        q, a = str((c or {}).get('pregunta', '')).strip()[:300], str((c or {}).get('respuesta', '')).strip()[:600]
        if len(q) < 4 or not a or norm(q) in seen: continue
        seen.add(norm(q)); added += 1
        existing.append({'id': os.urandom(4).hex(), 'q': q, 'a': a, **({'tema': str(c['tema'])[:40]} if c.get('tema') else {}), 'creada': fecha, 'due': fecha,
                         'interval': 0, 'ease': 2.5, 'reps': 0, 'fallos': 0, 'origen': 'semana'})
    if added: save(file, existing)
    save(marca, {'semana': semana, 'añadidas': added})
    if added: telegram(f"Repaso: {added} {'tarjeta nueva' if added == 1 else 'tarjetas nuevas'} de lo que trabajaste esta semana. Llegan con la to-do.")
    return added

# ---------- system health: what broke silently today must reach Owner by itself ----------
# Each check says what is wrong in one line and what to do in one line. Telegram only on change (a problem seen twice
# in a row, or a fix), never while everything is fine. Entregas shows the full list (status()['salud']).

WORKSPACE = TELEGRAM_LINK.parent
SALUD = STATE / 'salud.json'
HERMES_BIN = Path.home() / '.local/bin/hermes'

def _check(id, nombre, estado, detalle, accion=''):
    return {'id': id, 'nombre': nombre, 'estado': estado, 'detalle': detalle, 'accion': accion}

def check_claude():
    try:
        out = subprocess.run([claude_bin(), 'auth', 'status'], capture_output=True, text=True, timeout=30, env=clean_env()).stdout
        logged = json.loads(out).get('loggedIn')
    except (OSError, ValueError, subprocess.SubprocessError, Stop) as e:
        return _check('claude', 'Login de Claude', 'aviso', f'No he podido comprobarlo ({type(e).__name__}).')
    if logged: return _check('claude', 'Login de Claude', 'ok', 'Sesión iniciada: Alfred y el equipo pueden pensar.')
    return _check('claude', 'Login de Claude', 'fallo', 'Claude Code no tiene sesión: Alfred no contesta y el equipo no trabaja.',
                  f'En la Terminal: {claude_bin()} auth login')

def check_hermes():
    if not HERMES_BIN.exists(): return None
    try: out = subprocess.run([str(HERMES_BIN), 'auth', 'list'], capture_output=True, text=True, timeout=30, env=clean_env()).stdout
    except (OSError, subprocess.SubprocessError): return None
    if re.search(r'^anthropic\b', out, re.M):
        return _check('hermes', 'Login propio de Hermes', 'ok', 'Hermes usa su propio login de Claude.')
    return _check('hermes', 'Login propio de Hermes', 'aviso', 'Hermes toma prestado el login de Claude Code y puede volver a estropearlo.',
                  'En la Terminal: hermes auth add anthropic (y pega el código allí)')

def check_app():
    found = subprocess.run(['/usr/bin/pgrep', '-f', 'Prompted OS.app/Contents/MacOS/'], capture_output=True).returncode == 0
    if found: return _check('app', 'App del Mac', 'ok', 'Abierta: Alfred escucha y contesta por Telegram.')
    return _check('app', 'App del Mac', 'aviso', 'La app está cerrada: Alfred no contesta por Telegram ni escucha los chasquidos.',
                  'Abre Prompted OS.')

def check_voz():
    try: lines = (WORKSPACE / 'voz.log').read_text().splitlines()[-300:]
    except OSError: return None
    key = next((l.split('clave: ', 1)[1] for l in reversed(lines) if ' clave: ' in l), '')
    if key and not key.startswith(('llavero', 'archivo del curso')):
        return _check('voz', 'Voz de ElevenLabs', 'fallo', f'Sin clave de ElevenLabs ({key}): Alfred habla con la voz del Mac.',
                      'Ajustes del Sistema → Privacidad y seguridad → Archivos y carpetas → Prompted OS → Documentos.')
    # voz.log stamps are UTC (toISOString); the last hour only.
    cutoff = (now().astimezone(ZoneInfo('UTC')) - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%S')
    recent = [l for l in lines if l[:19] >= cutoff and re.search(r'tts (401|sin hueco|tiempo agotado|error)', l)]
    if len(recent) >= 3:
        return _check('voz', 'Voz de ElevenLabs', 'aviso', f'ElevenLabs ha fallado {len(recent)} veces en la última hora ({recent[-1][20:80]}).',
                      'Mira el saldo de caracteres en ElevenLabs; mientras, Alfred usa la voz del Mac.')
    return _check('voz', 'Voz de ElevenLabs', 'ok', 'Clave disponible y sin fallos recientes.')

def check_uso():
    seen = uso_actual()
    if not seen or now().timestamp() - seen[0].timestamp() > 6 * 3600: return None
    u = seen[1]; pct = lambda v: f"{round((v or 0) * 100)} %"
    detail = f"Plan de Claude: {pct(u.get('fiveHour'))} de las 5 h · {pct(u.get('sevenDay'))} de la semana."
    why = freno()
    if max(u.get('fiveHour') or 0, u.get('sevenDay') or 0) >= 0.97 and why:
        return _check('uso', 'Uso del plan', 'fallo', detail + ' Alfred puede quedarse sin respuesta; el equipo está frenado.', 'Evita trabajos largos hasta que se renueve.')
    if why: return _check('uso', 'Uso del plan', 'aviso', detail + f' Equipo frenado: {why}.', 'Nada que hacer: se reanuda solo.')
    return _check('uso', 'Uso del plan', 'ok', detail)

def check_fusiones():
    last = load(STATE / 'fusiones' / 'ultima.json', None)
    if not last: return None
    stuck = [pid for pid, r in (last.get('resultados') or {}).items()
             if pid in CONFIG['projects'] and str(r).startswith(('conflicto', 'error')) and pending_merge(pid)]
    if not stuck: return _check('fusiones', 'Fusión nocturna', 'ok', f"Última: {last.get('fecha', '')[:16].replace('T', ' ')}, sin nada atascado.")
    dormido = [pid for pid in stuck if 'reposo' in str(last['resultados'][pid])]
    stuck = [pid for pid in stuck if pid not in dormido]
    if not stuck:
        names = ', '.join(project(pid)['name'] for pid in dormido)
        return _check('fusiones', 'Fusión nocturna', 'aviso', f'Sin hacer: el Mac se durmió mientras probaba {names}.', 'Se repite esta noche; si lo quieres antes, «Fusionar ahora» en /lote.')
    names = ', '.join(project(pid)['name'] for pid in stuck)
    details = ', '.join(project(pid)['name'] + str(last['resultados'][pid]).partition('conflicto: necesita a Owner o a Hermes')[2] for pid in stuck)
    return _check('fusiones', 'Fusión nocturna', 'fallo', f'Atascada: {details}.', f'Pídeselo a Claude Code: «resuelve la fusión nocturna de {names}».')

def check_equipo():
    if (ROOT / 'PAUSED').exists(): return _check('equipo', 'Equipo de agentes', 'aviso', 'En pausa: no avanza ninguna tarea.', 'Reanúdalo en Entregas cuando quieras.')
    return _check('equipo', 'Equipo de agentes', 'ok', 'Activo: revisa el trabajo cada 5 minutos.')

def check_codex():
    m = modelos_hoy()
    if m['codex'].startswith('fuera'):
        return _check('codex', 'Codex', 'aviso', f"Sin uso hasta {m['codex'][12:]}: construye Claude.", 'Nada que hacer: vuelve solo.')
    if m['codex'].startswith('desde'):
        when = m['codex'][6:]
        return _check('codex', 'Codex', 'ok', f"Entra el {when[8:10]}/{when[5:7]}{' a las ' + when[11:] if len(when) > 10 else ''}; hasta entonces construye Claude.")
    return _check('codex', 'Codex', 'ok', 'Construye los primeros intentos.')

def alert(title, text):
    """One notice on the Mac and, if Owner linked it, on Telegram. Best effort: a failed notice never stops the run."""
    pf.notify(con_nombre(title), con_nombre(text), 'Salud del sistema')
    telegram(f'{title}\n{text}')

SALUD_CHECKS = [check_claude, check_voz, check_uso, check_app, check_fusiones, check_equipo, check_hermes, check_codex]

def salud(send=True):
    """Run every check, keep the result for Entregas and tell Owner on Telegram what changed."""
    prev = load(SALUD, {})
    before = {c['id']: c for c in prev.get('checks', [])}
    checks = []
    for fn in SALUD_CHECKS:
        try: c = fn()
        except Exception as e: c = _check(fn.__name__[6:], fn.__name__[6:], 'aviso', f'La comprobación ha fallado ({type(e).__name__}).')
        if not c: continue
        old = before.get(c['id'], {})
        # A problem must be seen twice in a row (5 min apart) before it is announced: no alarm for a quick restart.
        c['veces'] = old.get('veces', 0) + 1 if c['estado'] != 'ok' and old.get('estado') == c['estado'] else (1 if c['estado'] != 'ok' else 0)
        c['avisado'] = bool(old.get('avisado')) and c['estado'] != 'ok' and old.get('estado') == c['estado']
        if send and c['estado'] != 'ok' and c['veces'] >= 2 and not c['avisado']:
            alert(f"{'⚠️' if c['estado'] == 'fallo' else '🟡'} {c['nombre']}", c['detalle'] + (f"\nQué hacer: {c['accion']}" if c['accion'] else ''))
            c['avisado'] = True
        if send and c['estado'] == 'ok' and old.get('avisado'):
            alert(f"✅ {c['nombre']}", 'Resuelto. ' + c['detalle'])
        checks.append(c)
    order = {'fallo': 0, 'aviso': 1, 'ok': 2}
    checks.sort(key=lambda c: order[c['estado']])
    resumen = checks[0]['estado'] if checks else 'ok'
    out = {'hora': now().isoformat(timespec='seconds'), 'resumen': resumen, 'checks': checks}
    save(SALUD, out)
    return out

# ---------- approval ----------

def approve(pids, date=None, only=None):
    """Owner's approval: the batch becomes ready cards on each project board, hashed in the ledger."""
    with batch_lock(): _approve(pids, date, only)

DECIDIR = {'si': 'aprobada', 'no': 'descartada', 'luego': 'pospuesta'}

def decidir_lote(pid, indice, decision, date=None):
    """Owner decides one task of a pending batch (Telegram's Sí / No / Más tarde): yes puts it on the board, no discards it,
    later leaves it waiting; every other task keeps waiting untouched. indice is the task's stable index in the batch."""
    if decision not in DECIDIR: raise Stop('decisión no válida (si, no o luego)')
    date = date or today()
    with batch_lock():
        entry = (load(STATE / 'proposals' / f'{date}.json', {}).get('proyectos') or {}).get(pid)
        if not entry or entry.get('estado') != 'pendiente': return {'proyecto': pid, 'resultado': 'ya no está pendiente'}
        idx = entry.get('indices') or list(range(len(entry['tareas'])))
        if indice not in idx: return {'proyecto': pid, 'resultado': 'esa tarea ya está decidida'}
        pos = idx.index(indice); titulo = entry['tareas'][pos]['titulo']
        if decision != 'luego':
            others = {p for p in range(len(entry['tareas'])) if p != pos}
            _approve([pid], date, only=[pos] if decision == 'si' else [], hold=others)
        log(pid, f'Owner: {DECIDIR[decision]} «{titulo[:60]}»')
        return {'proyecto': pid, 'tarea': titulo, 'resultado': DECIDIR[decision]}

def _approve(pids, date=None, only=None, hold=None, by='Owner'):
    """only: positions (in the pending list) to approve. hold: positions to keep pending for Owner (not discarded)."""
    date = date or today(); file = STATE / 'proposals' / f'{date}.json'
    batch = load(file, None)
    if not batch: raise Stop('no hay lote para ' + date)
    ledger = load(STATE / 'approved.json', {})
    for pid in pids:
        entry = batch['proyectos'].get(pid)
        if not entry or entry.get('estado') != 'pendiente': continue
        idx = entry.get('indices') or list(range(len(entry['tareas'])))
        created = {int(k): v for k, v in (entry.get('tarjetas') or {}).items()}   # part already auto-approved
        held = [pos for pos in range(len(entry['tareas'])) if hold and pos in hold]
        if by != 'Owner':
            for pos in held: entry['tareas'][pos].setdefault('retenida', now().isoformat(timespec='seconds'))
        skipped = [t for pos, t in enumerate(entry['tareas']) if only is not None and pos not in only and pos not in held]
        if skipped: remember_discarded(pid, date, skipped)
        ronda = entry.get('ronda', 1); sufijo = '' if ronda == 1 else f'-r{ronda}'
        for pos, t in enumerate(entry['tareas']):
            i = idx[pos]
            if pos in held or (only is not None and pos not in only): continue
            quien = 'Owner' if by == 'Owner' else 'el equipo (tarea no importante, autorizado por Owner el 05/10/2026)'
            spec = {'proyecto': pid, 'lote': date, 'ronda': ronda, 'indice': i, **t, 'autorizacion': f'Lote del {date} (ronda {ronda}) aprobado por {quien}'}
            body = canonical(spec)
            # A dependency on an approved sibling keeps this card in todo until that one is done.
            parents = [created[d] for d in t['depende_de'] if d in created]
            with board(pid) as conn:
                tid = kb.create_task(conn, title=t['titulo'], body=body, assignee=LANE, created_by='equipo',
                                     workspace_kind='worktree', branch_name=f'equipo/tarea-{date}{sufijo}-{i}', parents=parents,
                                     idempotency_key=f'equipo:{pid}:{date}{sufijo}:{i}', max_retries=LIMITS['attempts'],
                                     board=project(pid)['board'])
            ledger[tid] = {'proyecto': pid, 'sha256': digest(body), 'aprobado': now().isoformat()}
            created[i] = tid
        if by == 'Owner':
            decisions = []
            for pos, t in enumerate(entry['tareas']):
                if pos in held: continue
                for start in (t.get('retenida'), entry.get('creado')):
                    try: minutes = round(max(0, (now() - datetime.fromisoformat(start)).total_seconds() / 60), 1)
                    except (ValueError, TypeError): continue
                    break
                else: continue
                decisions.append({'fecha': date, 'titulo': t['titulo'],
                                  'decision': 'descartada' if only is not None and pos not in only else 'aprobada',
                                  'minutos': minutes, 'ficha': 'lanzamiento' in t})
            if decisions:
                signals_file = STATE / 'senales' / f'{pid}.json'
                signals = load(signals_file, {'descartadas': []})
                signals['decisiones'] = (signals.get('decisiones', []) + decisions)[-50:]
                save(signals_file, signals)
        entry['tarjetas'] = {str(i): tid for i, tid in sorted(created.items())}
        entry.setdefault('todas', entry['tareas'])
        if held:
            entry['tareas'] = [entry['tareas'][pos] for pos in held]; entry['indices'] = [idx[pos] for pos in held]
        else:
            entry['estado'] = 'aprobado'; entry['tareas'] = entry.pop('todas'); entry.pop('indices', None)
        log(pid, f'{len(created)} tareas en el tablero' + (f', {len(held)} esperan a Owner' if held else ''))
    save(STATE / 'approved.json', ledger); save(file, batch)

# ---------- encargos: Owner asks Alfred (voice or Telegram) for one piece of development ----------
# Same road as an approved batch: validated spec, hashed in the ledger, builder → tests → independent review →
# team branch, budget brake and model table included. Alfred drafts it, Owner's spoken yes (the app's permission
# for this command) is the approval. Result goes to Telegram when it ends.

ENCARGOS = STATE / 'encargos.json'

def encargar(pid, t, by='Owner, de palabra a Alfred'):
    if pid not in CONFIG['projects']: raise Stop(f'proyecto desconocido: {pid} (usa ' + ', '.join(CONFIG['projects']) + ')')
    t = {**t, 'depende_de': []}
    try: t = validate_task(pid, t, 0, root=Path(project(pid)['repo']))
    except AssertionError as e: raise Stop(f'la tarea no es válida: falta o sobra «{e}»')
    known = load(ENCARGOS, {})
    n = 1 + sum(1 for v in known.values() if v.get('proyecto') == pid and v.get('fecha') == today())
    spec = {'proyecto': pid, 'lote': f'encargo-{today()}', 'ronda': 0, 'indice': n, **t, 'origen': 'alfred',
            'autorizacion': f'Encargo de {by} el {now().strftime("%d/%m/%Y %H:%M")}'}
    body = canonical(spec)
    with board(pid) as conn:
        tid = kb.create_task(conn, title=t['titulo'], body=body, assignee=LANE, created_by='alfred', workspace_kind='worktree',
                             branch_name=f'equipo/encargo-{today()}-{n}', priority=10, max_retries=LIMITS['attempts'],
                             idempotency_key=f'equipo:{pid}:encargo:{digest(body)[:16]}', board=project(pid)['board'])
    ledger = load(STATE / 'approved.json', {})
    ledger[tid] = {'proyecto': pid, 'sha256': digest(body), 'aprobado': now().isoformat(), 'origen': 'alfred'}
    save(STATE / 'approved.json', ledger)
    known[tid] = {'proyecto': pid, 'titulo': t['titulo'], 'fecha': today(), 'hora': now().isoformat(timespec='seconds')}
    save(ENCARGOS, known)
    model, effort = pick_model('constructor', t)
    log(pid, 'encargo de Alfred:', tid, t['titulo'])
    why = freno()
    return {'tarea': tid, 'proyecto': pid, 'titulo': t['titulo'], 'dificultad': t.get('dificultad'), 'constructor': model,
            'empieza': 'cuando se levante el freno: ' + why if why else ('cuando se reanude el equipo' if (ROOT / 'PAUSED').exists() else 'en 5 minutos como mucho')}

def avisar_encargo(task, estado, texto):
    """Encargos tell Owner themselves how they ended; batch tasks keep going through Entregas."""
    e = load(ENCARGOS, {}).get(task['id'])
    if not e: return
    name = project(e['proyecto'])['name']
    telegram(f"{'✅' if estado == 'hecha' else '⛔'} {name}: «{e['titulo']}»" + (' hecho. ' + first_sentence(texto) if estado == 'hecha' else ' bloqueado: ' + first_sentence(texto)))

def ficha_lanzamiento(task):
    """The launch card of a task when it has the four lines, else None."""
    try: ficha = json.loads(task.get('body') or '{}').get('lanzamiento')
    except (ValueError, AttributeError): return None
    return ficha if isinstance(ficha, dict) and all(ficha.get(k) for k in ('que', 'a_quien', 'metrica', 'umbral')) else None

def lanzamientos():
    """When each experiment was announced to Owner and when he launched it, by task id (path read per call: tests patch STATE)."""
    found = load(STATE / 'experimentos.json', {})
    return found if isinstance(found, dict) else {}

def avisar_lanzamiento(pid, task):
    """A finished task with a launch card tells Owner it is ready; launching is his, so the text never says anything went out."""
    ficha = ficha_lanzamiento(task)
    if not ficha: return None
    texto = (f"🚀 Listo para lanzar en {project(pid)['name']}: «{task['title']}»\n"
             f"Qué: {ficha['que']}\nA quién: {ficha['a_quien']}\nMétrica: {ficha['metrica']}\nUmbral: {ficha['umbral']}")
    spec = json.loads(task['body'])
    viene_de = spec.get('viene_de')
    if isinstance(viene_de, str) and TAREA_ID.fullmatch(viene_de):
        padre = next((r for r in reversed(resultados(pid)) if isinstance(r, dict) and r.get('tarea') == viene_de), None)
        if padre:
            resultado = padre.get('texto') or ''
            resultado = resultado if len(resultado) <= 140 else resultado[:139] + '…'
            texto += f"\nViene de: «{padre.get('titulo') or viene_de}» — {resultado}"
        else:
            texto += f"\nViene de: «{viene_de}»"
    boton_fusionar = None
    sin_fusionar = False
    try:
        receipt = load(STATE / 'runs' / task['id'] / 'receipt.json', {})
        commit = receipt.get('commit')
        p = project(pid)
        if commit and p.get('repo') and p.get('base') and not merged(p['repo'], p['base'], commit):
            sin_fusionar = True
            texto += '\nAún no está en la versión principal: se fusiona esta noche, o pulsa «Fusionar ahora» aquí abajo antes de lanzarlo.'
            callback_fusionar = BOTON_FUSIONAR + pid
            if PROYECTO_ID.fullmatch(pid) and len(callback_fusionar.encode()) <= 64:
                boton_fusionar = {'text': 'Fusionar ahora', 'callback_data': callback_fusionar}
            try:
                n = pending_merge(pid)
                if n > 1:
                    texto += f'\nAl fusionar entran también {n - 1} mejora más del equipo.' if n - 1 == 1 else f'\nAl fusionar entran también {n - 1} mejoras más del equipo.'
            except Exception:
                pass
    except Exception:
        pass
    registro = lanzamientos(); entry = registro.get(task['id']) or {}
    if not entry.get('avisado'):
        registro[task['id']] = {**entry, 'proyecto': pid, 'titulo': task['title'], 'avisado': now().isoformat(timespec='seconds')}
        if sin_fusionar: registro[task['id']]['sin_fusionar'] = True
        if isinstance(viene_de, str) and TAREA_ID.fullmatch(viene_de): registro[task['id']]['viene_de'] = viene_de
        save(STATE / 'experimentos.json', registro)
    post('alfred', ['owner'], texto, 'aviso', pid)   # 'alfred' is the id of Operaciones in departments.json
    callback_lanzado = BOTON_LANZADO + task['id']
    callback_no_lanzo = BOTON_NO_LANZO + task['id']
    botones = []
    if TAREA_ID.fullmatch(task['id']) and len(callback_lanzado.encode()) <= 64 and len(callback_no_lanzo.encode()) <= 64:
        botones.append([{'text': 'Ya lo he lanzado', 'callback_data': callback_lanzado}, {'text': 'No lo lanzo', 'callback_data': callback_no_lanzo}])
    if boton_fusionar:
        botones.append([boton_fusionar])
    telegram(texto, botones=botones or None)         # after the mesa, so the notice is there even if Telegram fails
    return texto

RECORDAR_MEDIR_HORAS = 48
LANZAR_ESPERA_HORAS = 48
POR_LANZAR_TOPE = 3
FRENO_LOTE_INTEGRADAS = 10

def recordar_medir():
    """Once, 48 h after Owner launched an experiment with no result written down, ask him what he measured.
    Costs no plan, so it runs even when the team is paused. Returns the ids reminded."""
    reminded = []
    registro = lanzamientos()
    for tid, entry in list(registro.items()):
        try:
            if not isinstance(entry, dict) or not entry.get('lanzado') or entry.get('recordado'): continue
            if now() - datetime.fromisoformat(entry['lanzado']) < timedelta(hours=RECORDAR_MEDIR_HORAS): continue
            pid = entry['proyecto']
            if tid in medidos(pid): continue
            task = next((t for t in tasks(pid) if t['id'] == tid), None)
            ficha = ficha_lanzamiento(task) if task else None
            title = (task or {}).get('title') or entry.get('titulo') or tid
            texto = f'¿Qué has medido en «{title}»?' + (f" Métrica: {ficha['metrica']}; umbral: {ficha['umbral']}." if ficha else '') + ' Cuéntaselo a Alfred y lo apunta.'
            registro[tid] = {**entry, 'recordado': now().isoformat(timespec='seconds')}
            save(STATE / 'experimentos.json', registro)   # before sending, so a failing Telegram never repeats the reminder
            reminded.append(tid)
            post('alfred', ['owner'], texto, 'aviso', pid)
            callback_data = BOTON_MEDIDO + tid
            if TAREA_ID.fullmatch(tid) and len(callback_data.encode()) <= 64:
                telegram(texto, botones=[[{'text': 'Apuntar lo medido', 'callback_data': callback_data}]])
            else:
                telegram(texto)
        except Exception as e: log('no he podido recordar medir', tid, e)
    return reminded

# Owner (15/10/2026): a decision that keeps a team idle for hours should reach him once, with how to settle it.
RECORDAR_DECISION_HORAS = 3
RECORDAR_VENTANA = (10, 21)          # local hours: never at night or first thing in the morning

def recordar_lanzar(at=None):
    """Once, 48 h to 7 days after announcing an experiment, ask Owner whether he will launch it.
    Costs no plan, so it runs even when the team is paused. Returns the ids reminded."""
    at = at or now()
    if not (RECORDAR_VENTANA[0] <= at.hour < RECORDAR_VENTANA[1]): return []
    reminded, por_proyecto = [], {}
    registro = lanzamientos()
    for tid, entry in list(registro.items()):
        try:
            if not isinstance(entry, dict): continue
            pid = entry.get('proyecto')
            if pid not in CONFIG['projects'] or entry.get('lanzado') or entry.get('descartado') or entry.get('recordado_lanzar'): continue
            age = at - datetime.fromisoformat(entry['avisado'])
            if not timedelta(hours=LANZAR_ESPERA_HORAS) <= age <= timedelta(days=7): continue
            if pid not in por_proyecto:
                por_proyecto[pid] = []   # a failed read must not be retried for every entry
                por_proyecto[pid] = tasks(pid)
            task = next((t for t in por_proyecto[pid] if t['id'] == tid), None)
            ficha = ficha_lanzamiento(task) if task else None
            if ficha is None: continue
            texto = (f"⏳ «{task['title']}» lleva {max(2, age.days)} días listo para lanzar en {project(pid)['name']}.\n"
                     f"Qué: {ficha['que']}\nA quién: {ficha['a_quien']}\nMétrica: {ficha['metrica']}\nUmbral: {ficha['umbral']}\n"
                     '¿Lo lanzas o te frena algo?')
            registro[tid] = {**entry, 'recordado_lanzar': at.isoformat(timespec='seconds')}
            save(STATE / 'experimentos.json', registro)   # before sending, so a failing Telegram never repeats the reminder
            reminded.append(tid)
            post('alfred', ['owner'], texto, 'aviso', pid)
            callback_lanzado, callback_no_lanzo = BOTON_LANZADO + tid, BOTON_NO_LANZO + tid
            if TAREA_ID.fullmatch(tid) and len(callback_lanzado.encode()) <= 64 and len(callback_no_lanzo.encode()) <= 64:
                telegram(texto, botones=[[{'text': 'Ya lo he lanzado', 'callback_data': callback_lanzado}, {'text': 'No lo lanzo', 'callback_data': callback_no_lanzo}]])
            else:
                telegram(texto)
        except Exception as e: log('no he podido recordar lanzar', tid, e)
    return reminded

def recordar_decisiones(at=None):
    """Once a day per project: a batch waiting for Owner 3 h or more while that project's team has nothing to do gets
    one reminder (Mac and Telegram) saying how long and how to approve it. Costs no plan. Returns the projects reminded."""
    at = at or now()
    if not (RECORDAR_VENTANA[0] <= at.hour < RECORDAR_VENTANA[1]): return []
    batch = load(STATE / 'proposals' / f'{today()}.json', None) or {}
    file = STATE / 'recordatorios.json'
    done = {k: v for k, v in load(file, {}).items() if k[:10] >= (at - timedelta(days=7)).date().isoformat()}
    sent = []
    for pid, entry in (batch.get('proyectos') or {}).items():
        try:
            if pid not in CONFIG['projects'] or entry.get('estado') != 'pendiente': continue
            key = f'{today()}:{pid}'
            if done.get(key): continue
            hours = (at - datetime.fromisoformat(entry['creado'])).total_seconds() / 3600
            if hours < RECORDAR_DECISION_HORAS: continue
            if any(t['status'] in ('ready', 'running') for t in tasks(pid)): continue   # the team still has work
            n = len(entry.get('tareas') or [])
            texto = f"{project(pid)['name']}: lote de hoy ({n} {'tarea' if n == 1 else 'tareas'}) esperando {int(hours)} h, equipo parado → /lote"
            done[key] = at.isoformat(timespec='seconds')
            save(file, done)                  # before sending, so a failing Telegram never repeats the reminder
            notify('Te espera una decisión', texto)
            sent.append(pid)
        except Exception as e: log(pid, 'no he podido recordar la decisión:', e)
    return sent

TAREA_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_-]{2,79}$')   # same rule as TAREA in mac/local-workspace/equipo-acciones.ts
ID_TABLERO = re.compile(r't_[0-9a-f]{8}')   # same rule as TAREA_TABLERO in alfred-os/lib/office-limits.ts
BOTON_LANZADO = 'l:'
BOTON_MEDIDO = 'm:'   # same prefix as BOTON_MEDIDO in equipo-acciones.ts
BOTON_NO_LANZO = 'n:'   # same prefix as BOTON_NO_LANZO in equipo-acciones.ts
PROYECTO_ID = re.compile(r'^[a-z0-9-]{2,30}$')   # same as BOTON_FUSIONAR / PROJECT in mac/local-workspace/equipo-acciones.ts
BOTON_FUSIONAR = 'f:'
RESULTADO_MIN, RESULTADO_MAX = 10, 600   # same limits as RESULTADO_MIN/RESULTADO_MAX in alfred-os/lib/office-limits.ts
DECISIONES = ('seguir', 'cambiar', 'parar')   # same values as DECISIONES in alfred-os/lib/office-limits.ts
FASE_FALTA_FUSIONAR = 'lista para lanzar · falta fusionar'   # same as FASE_FALTA_FUSIONAR in alfred-os/lib/office-limits.ts

VIAS_LANZADO = ('telegram', 'app')   # where Owner marked the launch; same values as the --via the server passes (equipo-acciones.ts)

def lanzado(tid, via=None):
    """Owner launched the experiment of this task: keep the time and tell the mesa once. The first time also keeps `via`
    (telegram or app); any other value is ignored and a repeated call never changes it."""
    if not isinstance(tid, str) or not TAREA_ID.fullmatch(tid): raise Stop('Tarea no válida.')
    for pid in CONFIG['projects']:
        task = next((t for t in tasks(pid) if t['id'] == tid), None)
        if task: break
    else: raise Stop(f'tarea desconocida: {tid}')
    ficha = ficha_lanzamiento(task)
    if not ficha: raise Stop('esa tarea no tiene ficha de lanzamiento')
    registro = lanzamientos(); entry = registro.get(tid) or {}
    if not entry.get('lanzado'):
        kept = {k: v for k, v in entry.items() if k not in ('descartado', 'motivo')}   # Owner changed his mind: the discard no longer applies
        entry = {**kept, 'proyecto': pid, 'titulo': task['title'], 'lanzado': now().isoformat(timespec='seconds'),
                 **({'via': via} if via in VIAS_LANZADO else {})}
        registro[tid] = entry
        save(STATE / 'experimentos.json', registro)
        post('alfred', ['todos'], f"Owner ha lanzado «{task['title']}». Métrica a medir: {ficha['metrica']}; umbral: {ficha['umbral']}.", 'aviso', pid)
    return {'tarea': tid, 'proyecto': entry.get('proyecto', pid), 'titulo': entry.get('titulo', task['title']),
            'avisado': entry.get('avisado'), 'lanzado': entry['lanzado']}

def descartar(tid, motivo):
    """Owner will not launch the experiment of this task: keep why, tell the mesa once and stop the planner from proposing the same."""
    if not isinstance(tid, str) or not TAREA_ID.fullmatch(tid): raise Stop('Tarea no válida.')
    for pid in CONFIG['projects']:
        task = next((t for t in tasks(pid) if t['id'] == tid), None)
        if task: break
    else: raise Stop(f'tarea desconocida: {tid}')
    if not ficha_lanzamiento(task): raise Stop('esa tarea no tiene ficha de lanzamiento')
    registro = lanzamientos(); entry = registro.get(tid) or {}
    if entry.get('lanzado'): raise Stop('ya está lanzado: apunta lo medido con equipo.py resultado')
    motivo = (motivo or '').strip()
    if len(motivo) < RESULTADO_MIN: raise Stop(f'cuenta por qué no lo lanzas (mínimo {RESULTADO_MIN} caracteres)')
    if not entry.get('descartado'):
        entry = {**entry, 'proyecto': pid, 'titulo': task['title'], 'descartado': now().isoformat(timespec='seconds'), 'motivo': motivo[:RESULTADO_MAX]}
        registro[tid] = entry
        save(STATE / 'experimentos.json', registro)
        post('alfred', ['todos'], f"Owner descarta «{task['title']}» sin lanzarlo: {entry['motivo']}", 'aviso', pid)
    return {'tarea': tid, 'proyecto': entry.get('proyecto', pid), 'titulo': entry.get('titulo', task['title']),
            'descartado': entry['descartado'], 'motivo': entry.get('motivo')}

def experimentos(pid, days=7):
    """How many announced experiments Owner launched in the last `days` days, the median hours from notice to launch
    and which launched ones still have no result written down regardless of notice age ('sin_medir', oldest launch first,
    at most 5, each with 'lanzado' in ISO format); 'sin_medir_total' counts them before the cap. 'por_lanzar' lists the
    announced ones neither launched nor discarded regardless of notice age (oldest notice first, at most 5);
    each one carries 'avisado' (ISO). 'por_lanzar_total' counts them before the cap. Notice, launch and discard metrics
    still only include notices in the last `days` days.
    'recordados_lanzar' counts launch reminders in the last `days` days,
    and 'decididos_tras_recordar_lanzar' counts those launched or discarded within 72 h of the reminder; broken dates are ignored.
    Announced ones Owner discarded without launching are counted in 'descartados', and 'motivos_descarte' keeps
    why for the latest 5 (most recent first) as {tarea, titulo, motivo}. 'lanzados_por_via' splits 'lanzados' by where
    Owner marked the launch: telegram, app or otra (an entry without a known 'via'). 'decisiones' counts the results written
    down in the last `days` days by what Owner decided: {'seguir', 'cambiar', 'parar', 'sin_decision'}; an entry without a
    valid decision counts as sin_decision and one with a broken 'hora' is ignored. 'seguir_con_siguiente' counts results
    in that window with decision 'seguir' and a task referenced by 'viene_de' in an announced experiment.
    'avisados_sin_fusionar' counts notices in the window with 'sin_fusionar'; 'fusionados_24h' counts those
    with 'fusionado' between the notice and 24 h later, inclusive. Broken dates are ignored."""
    since = now() - timedelta(days=days); avisados = lanzados = descartados = 0; hours = []; launched = []; pending = []; discarded = []; measured = medidos(pid)
    por_via = {'telegram': 0, 'app': 0, 'otra': 0}
    avisados_sin_fusionar = fusionados_24h = 0
    registro = lanzamientos()
    for tid, entry in registro.items():
        if not isinstance(entry, dict) or entry.get('proyecto') != pid or not entry.get('avisado'): continue
        try:
            notice = datetime.fromisoformat(entry['avisado'])
            in_window = notice >= since
        except (ValueError, TypeError): continue
        if not entry.get('lanzado') and not entry.get('descartado'):
            pending.append((notice, {'tarea': tid, 'titulo': entry.get('titulo') or tid, 'avisado': entry['avisado']}))
        if entry.get('lanzado') and tid not in measured:
            launched.append((str(entry['lanzado']), {'tarea': tid, 'titulo': entry.get('titulo') or tid, 'lanzado': str(entry['lanzado'])}))
        if not in_window: continue
        avisados += 1
        if entry.get('sin_fusionar'):
            avisados_sin_fusionar += 1
            try:
                if notice <= datetime.fromisoformat(entry['fusionado']) <= notice + timedelta(hours=24):
                    fusionados_24h += 1
            except (KeyError, ValueError, TypeError): pass
        if not entry.get('lanzado'):
            if entry.get('descartado'):
                descartados += 1
                discarded.append((str(entry['descartado']), {'tarea': tid, 'titulo': entry.get('titulo') or tid, 'motivo': entry.get('motivo') or ''}))
            continue
        lanzados += 1
        por_via[entry['via'] if entry.get('via') in VIAS_LANZADO else 'otra'] += 1
        try: hours.append((datetime.fromisoformat(entry['lanzado']) - notice).total_seconds() / 3600)
        except (ValueError, TypeError): pass
    reminded = answered = 0; written = resultados(pid)
    for tid, entry in registro.items():   # own loop: the reminder comes at least 48 h after the notice, so no filter by 'avisado'
        if not isinstance(entry, dict) or entry.get('proyecto') != pid: continue
        try:
            remembered = datetime.fromisoformat(entry['recordado'])
            if remembered < since: continue
        except (KeyError, ValueError, TypeError): continue
        reminded += 1
        for r in written:
            try:
                if isinstance(r, dict) and r.get('tarea') == tid and remembered <= datetime.fromisoformat(r['hora']) <= remembered + timedelta(hours=72):
                    answered += 1; break
            except (KeyError, ValueError, TypeError): continue
    reminded_launch = decided_launch = 0
    for entry in registro.values():
        if not isinstance(entry, dict) or entry.get('proyecto') != pid: continue
        try:
            remembered = datetime.fromisoformat(entry['recordado_lanzar'])
            if not since <= remembered <= since + timedelta(days=days): continue
        except (KeyError, ValueError, TypeError): continue
        reminded_launch += 1
        for key in ('lanzado', 'descartado'):
            try:
                if remembered <= datetime.fromisoformat(entry[key]) <= remembered + timedelta(hours=72):
                    decided_launch += 1; break
            except (KeyError, ValueError, TypeError): continue
    decisiones = {**{d: 0 for d in DECISIONES}, 'sin_decision': 0}
    seguir_con_siguiente = 0
    for r in written:   # same reading as above; an entry with a broken 'hora' is ignored
        try:
            if not isinstance(r, dict) or datetime.fromisoformat(r['hora']) < since: continue
        except (KeyError, ValueError, TypeError): continue
        decisiones[r['decision'] if r.get('decision') in DECISIONES else 'sin_decision'] += 1
        if r.get('decision') == 'seguir' and r.get('tarea') and any(
                isinstance(entry, dict) and entry.get('avisado') and entry.get('viene_de') == r['tarea'] for entry in registro.values()):
            seguir_con_siguiente += 1
    return {'avisados': avisados, 'lanzados': lanzados, 'lanzados_por_via': por_via, 'horas_hasta_lanzar': round(median(hours), 1) if hours else None,
            'avisados_sin_fusionar': avisados_sin_fusionar, 'fusionados_24h': fusionados_24h,
            'decisiones': decisiones, 'seguir_con_siguiente': seguir_con_siguiente,
            'sin_medir_total': len(launched),
            'sin_medir': [item for _, item in sorted(launched, key=lambda x: x[0])][:5],
            'recordados': reminded, 'medidos_tras_recordar': answered,
            'recordados_lanzar': reminded_launch, 'decididos_tras_recordar_lanzar': decided_launch,
            'por_lanzar_total': len(pending),
            'por_lanzar': [item for _, item in sorted(pending, key=lambda x: x[0])][:5],
            'descartados': descartados,
            'motivos_descarte': [item for _, item in sorted(discarded, key=lambda x: x[0], reverse=True)][:5]}

def resultados(pid):
    found = load(STATE / 'experimentos' / f'{pid}.json', [])
    return found if isinstance(found, list) else []

def medidos(pid):
    """Ids of the tasks whose experiment already has a result written down."""
    return {r['tarea'] for r in resultados(pid) if isinstance(r, dict) and r.get('tarea')}

def registrar_resultado(pid, texto, tarea=None, decision=None):
    """What Owner measured in an experiment he launched. The planner and the mesa read it to decide: follow, change or stop.
    `decision` (seguir, cambiar or parar) is what Owner decides with it; without it the entry stays as before."""
    if pid not in CONFIG['projects']: raise Stop(f'proyecto desconocido: {pid} (usa ' + ', '.join(CONFIG['projects']) + ')')
    if tarea is not None and (not isinstance(tarea, str) or not TAREA_ID.fullmatch(tarea)): raise Stop('Tarea no válida.')
    if decision not in (None, '') and decision not in DECISIONES: raise Stop('Decisión no válida: usa seguir, cambiar o parar.')
    texto = (texto or '').strip()
    if len(texto) < RESULTADO_MIN: raise Stop(f'el resultado es demasiado corto: cuenta qué mediste y qué salió (mínimo {RESULTADO_MIN} caracteres)')
    entry = {'fecha': today(), 'hora': now().isoformat(timespec='seconds'), 'texto': texto[:RESULTADO_MAX], 'decision': decision}
    if tarea:
        found = next((t for t in tasks(pid) if t['id'] == tarea), None)
        if not found: raise Stop(f'tarea desconocida en {pid}: {tarea}')
        try: spec = json.loads(found.get('body') or '{}')
        except ValueError: spec = {}
        entry.update(tarea=tarea, titulo=found.get('title'), lanzamiento=spec.get('lanzamiento') if isinstance(spec, dict) else None)
        if isinstance(spec, dict) and spec.get('viene_de'): entry['viene_de'] = spec['viene_de']
    entry = {k: v for k, v in entry.items() if v}
    save(STATE / 'experimentos' / f'{pid}.json', (resultados(pid) + [entry])[-30:])
    return entry

def decidir(tid, decision):
    if not isinstance(tid, str) or not TAREA_ID.fullmatch(tid): raise Stop('Tarea no válida.')
    if decision not in DECISIONES: raise Stop('Decisión no válida: usa seguir, cambiar o parar.')
    for pid in CONFIG['projects']:
        entries = resultados(pid)
        entry = next((r for r in reversed(entries) if isinstance(r, dict) and r.get('tarea') == tid), None)
        if entry is None: continue
        if entry.get('decision') in DECISIONES: raise Stop('ese resultado ya tiene decisión')
        entry.update(decision=decision, decidido=now().isoformat(timespec='seconds'))
        save(STATE / 'experimentos' / f'{pid}.json', entries)
        titulo = entry.get('titulo') or tid
        post('alfred', ['todos'], f"Owner decide {decision} en «{titulo}».", 'aviso', pid)
        return {'tarea': tid, 'proyecto': pid, 'titulo': titulo, 'decision': decision}
    raise Stop('ese experimento no tiene resultado apuntado: usa equipo.py resultado')

# ---------- execution ----------

BUILD_PROMPT = """Eres el CONSTRUCTOR del equipo de agentes de Owner, proyecto {name}. Trabajas en una copia aislada del repo.
REGLA DE OWNER (prioritaria): no propongas, construyas ni apruebes cambios hechos por motivos legales, éticos o de cumplimiento (quitar o suavizar cifras de premios, la palabra cashback, promesas, avisos, textos legales, marcar cifras como hipótesis). De eso se encarga Owner. Si ves un riesgo legal o ético, menciónaselo una vez a Owner en la mesa y no crees tareas para cambiarlo.
Tarea aprobada por Owner:
{spec}

Reglas: edita SOLO estos archivos (puedes crearlos): {files}. Lee antes lo que vayas a cambiar (leer el repo está permitido).
No ejecutes pruebas, git ni instalaciones, no toques configuración, credenciales ni otros archivos. Sigue el estilo del código que ya existe. Deja el producto funcionando.
Las pruebas «{tests}» se ejecutarán después automáticamente. Al terminar, explica en dos frases qué has cambiado. Si lo que pide la tarea ya está hecho en el código, no cambies nada y empieza tu respuesta con «YA ESTABA HECHO:» y di en qué archivo y líneas está.
{feedback}
{lessons}
Contexto del proyecto:
{context}
"""

REVIEW_PROMPT = """Eres el REVISOR independiente del equipo de agentes de Owner, proyecto {name}. No has escrito este cambio.
REGLA DE OWNER (prioritaria): no propongas, construyas ni apruebes cambios hechos por motivos legales, éticos o de cumplimiento (quitar o suavizar cifras de premios, la palabra cashback, promesas, avisos, textos legales, marcar cifras como hipótesis). De eso se encarga Owner. Si ves un riesgo legal o ético, menciónaselo una vez a Owner en la mesa y no crees tareas para cambiarlo.
Tarea aprobada por Owner:
{spec}

Diff del constructor:
```diff
{diff}
```
Resultado de las pruebas (todas en verde): {tests}
{lessons}
Revisa si cumple la tarea, si rompe algo, si se sale de los archivos permitidos o del alcance, y la calidad.
Puedes leer el repo para comprobarlo. Responde SOLO con un bloque ```json```:
{{"decision": "aprobar" | "cambios", "motivos": "breve y concreto", "seguimiento": ["ideas de tareas futuras, opcional"]}}
"""

def phase(folder, name, attempt):
    """Where a running task is right now, for the board: building, testing or in review."""
    save(folder / 'fase.json', {'fase': name, 'intento': attempt, 'desde': now().isoformat()})

def spec_of(pid, task):
    ledger = load(STATE / 'approved.json', {})
    entry = ledger.get(task['id'])
    if not entry or entry['proyecto'] != pid or entry['sha256'] != digest(task['body']): raise Stop('tarea_no_autorizada')
    return json.loads(task['body'])

def changed(path, allowed, links):
    """Files the builder touched, split into allowed and out of scope. Dependency links are not changes."""
    # -z: NUL-separated and never trimmed (a stripped " M path" would lose a character of the path).
    raw = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '-z', '--untracked-files=all'],
                         capture_output=True, text=True, env=clean_env(), check=True).stdout
    entries = raw.split('\0'); files = set(); i = 0
    while i < len(entries):
        entry = entries[i]
        if len(entry) > 3:
            files.add(entry[3:])
            if entry[0] in 'RC': i += 1          # renames carry the old path as the next field
        i += 1
    files = sorted(files)
    files = [f for f in files if not any(f == l or f.startswith(l + '/') for l in links)]
    return [f for f in files if f in allowed], [f for f in files if f not in allowed]

def discard(path, files):
    for f in files:
        if git(path, 'ls-files', '--', f): git(path, 'checkout', '--', f, check=False)
        else: (path / f).unlink(missing_ok=True)

def failure_excerpt(out, limit=3000):
    """Keep the first failure and its details, without successful test lines."""
    if limit <= 0: return ''
    lines = [line for line in out.splitlines(keepends=True)
             if not line.lstrip().startswith(('✔', 'PASS '))]
    start = next((i for i, line in enumerate(lines)
                  if re.match(r'^\s*(?:✖\s*)?failing\s+tests\b', line, re.I)), None)
    if start is None:
        start = next((i for i, line in enumerate(lines) if line.lstrip().startswith('======')), None)
    if start is not None: return ''.join(lines[start:])[:limit]
    failed = [line for line in lines
              if line.lstrip().startswith('FAIL') or 'error' in line.lower() or '✖' in line]
    return ''.join(failed)[:limit] if failed else ''.join(lines)[-limit:]

INFRA_MARKERS = ('did not become healthy', 'servidor terminó antes de estar sano', 'startDemoServer', 'waitForHealth')
ASERCION_FALLIDA = re.compile(r'AssertionError|ERR_ASSERTION')

def infra_marker(tests, files):
    """The first server-start marker, unless any failed test has an assertion or the diff touches the test server itself."""
    if any('test/helpers' in f or f.endswith('server.js') for f in files): return ''
    marker = ''
    for t in tests:
        if t.get('ok'): continue
        text = f"{t.get('fallo', '')}\n{t.get('salida', '')}"
        if ASERCION_FALLIDA.search(text): return ''
        if not marker: marker = next((m for m in INFRA_MARKERS if m in text), '')
    return marker

def pruebas_mixtas(tests):
    """Whether failed tests contain both an assertion and a server-start marker."""
    texts = [f"{t.get('fallo', '')}\n{t.get('salida', '')}" for t in tests if not t.get('ok')]
    return any(ASERCION_FALLIDA.search(text) for text in texts) and any(m in text for text in texts for m in INFRA_MARKERS)

def infra_failure(tests, files):
    return bool(infra_marker(tests, files))

def run_tests(pid, spec, path, folder, heartbeat, repeated_tests=None):
    """Run suites; the caller may already be spending the one retry for a named suite."""
    results = []
    for name in spec['pruebas']:
        test = project(pid)['tests'][name]
        cwd = path / test.get('cwd', '.')
        repeated_by = (repeated_tests or {}).get(name)
        repeated = bool(repeated_by)
        def run_steps(suffix=''):
            nonlocal repeated, repeated_by
            code, out, last = 0, '', ''
            for i, step in enumerate(test.get('steps') or [test['cmd']]):
                slept = sleep_clock()
                # A suite can ask for more time than the default (Norte's 2.000+ tests take over 4 minutes): «segundos» in projects.json.
                limit = int(test.get('segundos') or LIMITS['test_seconds'])
                try: code, text = run(step, cwd, limit, out=folder / f'test-{name}-{i}{suffix}.log', heartbeat=heartbeat)
                except Stop as e:
                    # Retry the whole suite once; any other stop (a lost claim...) goes up untouched.
                    if str(e) == 'timeout':
                        if saturado(): raise Saturado(f"Mac saturado (carga {carga():.1f} por núcleo) al probar {name}")
                        if repeated:
                            reason = f"timeout al probar {name} ({limit} s)"
                            try:
                                lines = (folder / f'test-{name}-{i}{suffix}.log').read_text().splitlines()
                                linea = next((line.strip() for line in reversed(lines) if line.strip()), '')[:200]
                            except Exception:
                                # Reading a partial log must never hide the timeout that blocked the task.
                                linea = ''
                            if linea: reason += f'; última línea: «{linea}»'
                            error = Stop(reason)
                            error.repetida_por = repeated_by
                            raise error
                        log(pid, 'timeout en', name, ', repito una vez')
                        repeated = True
                        repeated_by = 'timeout'
                        return run_steps('-t')
                    raise
                out += text; last = text
                if code != 0 and sleep_clock() - slept > SLEEP_GAP:
                    raise Asleep(f'reposo: el Mac se durmió durante {name}')
                if code != 0: break
            return code, out, last
        code, out, last = run_steps()
        if code != 0 and TIEMPO_AGOTADO.search(last) and saturado():
            raise Saturado(f"Mac saturado (carga {carga():.1f} por núcleo) al probar {name}")
        if not repeated and code != 0 and ARRANQUE_FALLIDO.search(last) and not ASERCION_FALLIDA.search(last):
            # The demo server was slow to start, not the work: run the whole test once more before it costs an attempt.
            # Only the step that failed counts: an earlier step that merely printed the message is not a failed start.
            log(pid, 'arranque fallido en', name, ', repito una vez')
            repeated = True
            repeated_by = 'arranque'
            code, out, last = run_steps('-r')
        if code != 0 and TIEMPO_AGOTADO.search(last) and saturado():
            raise Saturado(f"Mac saturado (carga {carga():.1f} por núcleo) al probar {name}")
        results.append({'prueba': name, 'ok': code == 0, 'salida': out[-1500:], **({'repetida': True, 'repetida_por': repeated_by} if repeated else {})})
        if code != 0:
            results[-1]['fallo'] = failure_excerpt(out)
            break
    return results

def follow_up(pid, items):
    file = STATE / 'followups' / f'{pid}.json'
    save(file, (load(file, []) + items)[-40:])

def motivo_bloqueo(e, folder):
    """Why a task blocked, never empty: an error without text says its type and the phase and attempt it was in."""
    reason = str(e).strip()[:500]
    if reason: return reason
    reason = f'fallo sin mensaje ({type(e).__name__})'
    fase = load(folder / 'fase.json', {})
    if not isinstance(fase, dict): return reason
    where = [f'en la fase {fase["fase"]}' if fase.get('fase') else '', f'intento {fase["intento"]}' if fase.get('intento') else '']
    where = ', '.join(w for w in where if w)
    return f'{reason} {where}' if where else reason

def execute(pid, task, claim, run_id):
    """Build → test → review (up to two cycles) → integrate on equipo/<pid>. Returns a short summary."""
    p = project(pid); spec = spec_of(pid, task); team = f'equipo/{pid}'
    folder = STATE / 'runs' / task['id']; folder.mkdir(parents=True, exist_ok=True)
    before = load(folder / 'receipt.json', {})
    def heartbeat():
        with board(pid) as conn: return kb.heartbeat_claim(conn, task['id'], ttl_seconds=600, claimer=claim)
    branch = f"equipo/tarea/{task['id']}"
    path = worktree(pid, task['id'], new_branch=branch, start=git(p['repo'], 'rev-parse', team))
    receipt = {'tarea': task['id'], 'proyecto': pid, 'titulo': spec['titulo'], 'inicio': now().isoformat(), 'ciclos': []}
    try:
        return attempts(pid, p, spec, task, team, branch, path, folder, receipt, heartbeat, relaunch_note(before))
    except Exception as e:
        # Blocked work is what the retrospective learns most from: always leave its receipt.
        if receipt.get('estado') != 'integrada':
            save(folder / 'receipt.json', {**receipt, 'estado': 'bloqueada', 'error': motivo_bloqueo(e, folder), 'fin': now().isoformat()})
        raise

def relaunch_note(prev):
    """A relaunched task starts from what blocked it last time (the reviewer's request or the failing test), not blind."""
    if not prev or prev.get('estado') != 'bloqueada': return ''
    last = (prev.get('ciclos') or [{}])[-1]
    review = last.get('revision') or {}
    if review.get('decision') == 'cambios': why = 'el revisor independiente pidió: ' + str(review.get('motivos', ''))[:1500]
    else:
        failed = [t.get('fallo') or t['salida'][-800:] for t in last.get('pruebas', []) if not t.get('ok') and not t.get('timeout')]
        why = ('las pruebas fallaron:\n' + '\n'.join(failed)) if failed else str(prev.get('error', ''))[:800]
    return f'Esta tarea se relanza tras bloquearse. La última vez {why}\nEmpieza resolviendo eso.'

TIMEOUT_NOTE = ('El intento anterior se quedó sin tiempo. El worktree conserva lo que dejó a medias: '
                'revísalo y termínalo en vez de empezar de cero.')

def retry_timeout(call, seconds, *who, retry=True):
    """A builder that runs out of time gets one more go with half as much time again and a note about the unfinished
    work. Any other stop (a lost claim...) goes up untouched; a timeout with no retry left says how long it was given,
    so the block reason names the phase. `who` prefixes the retry log line (project, task)."""
    try: return call(seconds)
    except Stop as e:
        if str(e) != 'timeout': raise
        if not retry: raise Stop(f'timeout al construir ({seconds} s)')
    longer = int(seconds * 1.5)
    log(*who, 'timeout al construir, reintento con más tiempo')
    try: return call(longer, TIMEOUT_NOTE)
    except Stop as e:
        if str(e) != 'timeout': raise
        raise Stop(f'timeout al construir ({seconds} s y {longer} s)')

# 01/11: two Norte tasks spent both attempts because an existing test outside their approved files still asserted the old
# behaviour the task changes on purpose (a doc test, an integration test); the builder may not touch it, so it blocked.
# After a failed run, the failing test files the task was not given are allowed on the next attempt, with a rule against
# weakening them, and the reviewer is told which ones so it checks exactly that.
FAILING_TEST_FILE = re.compile(r'test at ((?:[\w.@-]+/)*[\w.@-]+\.(?:test|spec)\.[cm]?[jt]sx?):\d+|File "([^"]*?/test_[\w-]+\.py)"')
MAX_PRUEBAS_AMPLIADAS = 3
AMPLIAR_NOTA = ('Estas pruebas fallan y no estaban en tus archivos: {files}. Ahora puedes editarlas, pero SOLO para actualizar lo que '
                'comprueba el comportamiento antiguo que esta tarea cambia a propósito. No las debilites, no quites comprobaciones ni '
                'las borres: si el fallo es de tu código, arregla tu código.')

def failing_test_files(failure, path, allowed):
    """Test files named in a failed run that exist in the worktree and the task was not given (at most three)."""
    out = []
    for m in FAILING_TEST_FILE.finditer(failure or ''):
        f = m.group(1) or m.group(2)
        try: f = (Path(path) / f).resolve().relative_to(Path(path).resolve()).as_posix()
        except ValueError: continue
        if not safe_rel(f): continue
        # A test-looking symlink must not grant permission to application code.
        if not re.search(r'(?:^|/)(?:test_[\w-]+\.py|[\w.@-]+\.(?:test|spec)\.[cm]?[jt]sx?)$', f): continue
        if f not in allowed and f not in out and (Path(path) / f).is_file(): out.append(f)
    return out[:MAX_PRUEBAS_AMPLIADAS]

# 13/11: a test outside the task that already fails on the team branch is not this task's to fix: the builder «fixed» it
# out of scope or spent both attempts on it. Before allowing it, run that one suite on a clean checkout of the team branch.
def base_roja(pid, spec, team, suite, linea, nuevas, folder, heartbeat, task_id=None):
    """A short text (the FAIL line or the test file) when `suite` also fails on `team` the same way, else ''. Never raises."""
    repo, base_path = None, None
    # Same path worktree() builds: if it fails after creating the folder (linking dependencies) there is no base_path to remove.
    nombre = f"{task_id or Path(folder).name}-base"
    esperada = ROOT / 'worktrees' / pid / nombre
    try:
        repo = project(pid)['repo']
        base_path = worktree(pid, nombre, start=git(repo, 'rev-parse', team))
        tests = run_tests(pid, {**spec, 'pruebas': [suite]}, base_path, Path(folder) / 'base', heartbeat)
        failed = [t for t in tests if not t.get('ok')]
        if not failed: return ''
        base = primera_fail('\n'.join(t.get('fallo') or t.get('salida') or '' for t in failed))
        if base and base == linea: return base
        comunes = [f for f in failing_test_files('\n'.join(f"{t.get('salida', '')}\n{t.get('fallo', '')}" for t in failed), base_path, []) if f in nuevas]
        return ', '.join(comunes)
    except Exception:
        # Checking the base is a hint, never a new way to block: the task goes on as before.
        return ''
    finally:
        if repo is not None and (base_path or esperada.exists()):
            try: git(repo, 'worktree', 'remove', '--force', str(base_path or esperada), check=False)
            except Exception: pass

MISMO_FALLO = ' (mismo fallo en los 2 intentos)'

def primera_fail(texto):
    """The first line (trimmed) that starts with FAIL or 'not ok ', or '' when there is none."""
    for line in (texto or '').splitlines():
        if line.lstrip().startswith(('FAIL', 'not ok ')): return line.strip()
    return ''

def cola_fallo(texto, room):
    """The end of a failed run without PASS lines or blank lines, cut from the start to fit room characters (whole lines when possible)."""
    lines = [l for l in (texto or '').splitlines() if l.strip() and not l.lstrip().startswith('PASS')]
    if len('\n'.join(lines)) <= room: return '\n'.join(lines)
    kept, size = [], 0
    for line in reversed(lines):
        size += len(line) + (1 if kept else 0)
        if size > room: break
        kept.insert(0, line)
    return '\n'.join(kept) if kept else (lines[-1][-room:] if room > 0 else '')

def fallo_repetido(ciclos):
    """The first FAIL line when the last two cycles with failed tests end on that same line, else ''."""
    lines = []
    for c in ciclos:
        failed = [t for t in c.get('pruebas') or [] if not t.get('ok')]
        if failed: lines.append(primera_fail('\n'.join(t.get('fallo') or t.get('salida') or '' for t in failed)))
    last = lines[-2:]
    return last[0] if len(last) == 2 and last[0] and last[0] == last[1] else ''

def attempts(pid, p, spec, task, team, branch, path, folder, receipt, heartbeat, feedback=''):
    failure = ''
    allowed = list(spec['archivos'])
    base_mirada = False
    for attempt in range(1, LIMITS['attempts'] + 1):
        failure = ''
        cycle = {'intento': attempt}
        log(pid, task['id'], f'construyendo (intento {attempt})'); phase(folder, 'construyendo', attempt)
        who = {}
        build = lambda seconds, note='': claude(BUILD_PROMPT.format(name=p['name'], spec=json.dumps(spec, ensure_ascii=False, indent=1), files=', '.join(allowed),
                                                           tests=', '.join(spec['pruebas']), feedback=f'{feedback}\n{note}'.strip(), lessons=lessons_text(pid, 'constructor'), context=context_text(pid, path)),
                                       path, seconds, write=True, out=folder / f'build-{attempt}.json', heartbeat=heartbeat,
                                       role='constructor', spec=spec, attempt=attempt, info=who,
                                       on_fallback=lambda: discard(path, changed(path, [], p.get('links', []))[1]))
        try: cycle['constructor'] = retry_timeout(build, LIMITS['builder_seconds'], pid, task['id'], retry=attempt == 1)[:2000]
        except Stop as e:
            # A timeout right after a failed test run tells the whole chain, so the block reason points at the fix.
            before = receipt['ciclos'][-1] if receipt['ciclos'] else {}
            failed = [t.get('prueba') or 'prueba sin nombre' for t in before.get('pruebas') or [] if not t.get('ok')]
            if str(e).startswith('timeout al construir') and failed:
                raise Stop(f"{e} tras fallar {', '.join(failed)} en el intento {before.get('intento')}"[:500])
            raise
        cycle['modelo'] = who.get('modelo')
        files, extra = changed(path, allowed, p.get('links', []))
        if extra:
            discard(path, extra); cycle['fuera_de_alcance'] = extra
        if not files:
            before = receipt['ciclos'][-1] if receipt['ciclos'] else {}
            failed = [t.get('prueba') or 'prueba sin nombre' for t in before.get('pruebas') or [] if not t.get('ok')]
            receipt['ciclos'].append(cycle)
            said = (cycle['constructor'] or '').strip()
            if failed:
                reason = f"el constructor no cambió ningún archivo permitido tras fallar {', '.join(failed)} en el intento {before.get('intento')}"
                if said: reason += '. Dijo: «' + said[-300:] + '»'
                raise Stop(reason[:500])
            if said.upper().startswith('YA ESTABA HECHO'):
                raise Stop('ya estaba hecho según el constructor: ' + said[len('YA ESTABA HECHO'):].lstrip(': ').strip()[:300])
            if said: raise Stop('el constructor no cambió ningún archivo permitido. Dijo: «' + said[-300:] + '»')
            raise Stop('el constructor no cambió ningún archivo permitido')
        phase(folder, 'probando', attempt)
        def test_run(where, previous=None):
            # A test that ran out of time stays in the receipt, so the block reason and the retro know which one it was.
            passed = []
            pending = spec
            repeated_tests = {}
            if previous:
                # Keep completed suites and still execute every suite after the failed one.
                first_failed = next(i for i, t in enumerate(previous) if not t.get('ok'))
                passed = previous[:first_failed]
                pending = {**spec, 'pruebas': spec['pruebas'][first_failed:]}
                repeated_tests = {previous[first_failed]['prueba']: 'arranque'}
            try:
                if repeated_tests: return passed + run_tests(pid, pending, path, where, heartbeat, repeated_tests=repeated_tests)
                return run_tests(pid, pending, path, where, heartbeat)
            except Stop as e:
                slow = re.match(r'timeout al probar (.+?) \(\d+ s\)(?:; última línea: «(.*)»)?$', str(e))
                if slow:
                    cycle['pruebas'] = passed + [{'prueba': slow.group(1), 'ok': False, 'timeout': True, 'repetida_por': getattr(e, 'repetida_por', 'timeout')}]
                    if slow.group(2) is not None: cycle['pruebas'][-1]['ultima_linea'] = slow.group(2)
                    if slow.group(1) in repeated_tests or getattr(e, 'repetida_por', None) == 'arranque': receipt['causa'] = 'infraestructura'
                    receipt['ciclos'].append(cycle)
                raise
        tests = test_run(folder / f'ciclo-{attempt}')
        cycle['pruebas'] = tests
        if not all(t['ok'] for t in tests) and pruebas_mixtas(tests): cycle['pruebas_mixtas'] = True
        if not all(t['ok'] for t in tests) and infra_failure(tests, files):
            # run_tests may already have used the one suite retry. Never stack a
            # second retry here or turn exhausted infrastructure into builder feedback.
            cycle['pruebas_infra'] = tests
            if not any(t.get('repetida') for t in tests if not t.get('ok')):
                log(pid, task['id'], 'fallo de arranque del servidor, repito las pruebas')
                tests = test_run(folder / f'ciclo-{attempt}-reintento', tests)
                cycle['pruebas'] = tests
            if not all(t['ok'] for t in tests) and infra_failure(tests, files):
                receipt['ciclos'].append(cycle); receipt['causa'] = 'infraestructura'
                prueba = next(t['prueba'] for t in tests if not t.get('ok'))
                raise Stop(f'infraestructura: el servidor de pruebas no arrancó en {prueba} ({infra_marker(tests, files)})')
        if not all(t['ok'] for t in tests):
            failure = '\n'.join(t.get('fallo') or t['salida'][-800:] for t in tests if not t['ok'])
            feedback = 'Intento anterior: las pruebas fallaron. Corrígelo.\n' + failure
            nuevas = failing_test_files('\n'.join(f"{t.get('fallo', '')}\n{t.get('salida', '')}" for t in tests if not t['ok']), path, allowed)
            if nuevas and not base_mirada:
                # A test that already fails without this change blocks the task instead of being handed to the builder.
                base_mirada = True
                suite = next(t['prueba'] for t in tests if not t['ok'])
                inicio = time.monotonic()
                texto = base_roja(pid, spec, team, suite, primera_fail(failure), nuevas, folder, heartbeat, task_id=task['id'])
                cycle['base_segundos'] = round(time.monotonic() - inicio, 1)
                if texto:
                    receipt['ciclos'].append(cycle); receipt['causa'] = 'base roja'
                    raise Stop(f"base roja: {suite} ya falla en {team} sin este cambio ({texto}); hay que arreglar esa prueba en otra tarea"[:500])
            if nuevas:
                allowed += nuevas; cycle['pruebas_ampliadas'] = nuevas
                feedback += '\n\n' + AMPLIAR_NOTA.format(files=', '.join(nuevas))
                log(pid, task['id'], 'pruebas fuera de alcance que fallan, permitidas en el siguiente intento:', ', '.join(nuevas))
            receipt['ciclos'].append(cycle)
            extra = [f for f in cycle.get('fuera_de_alcance', []) if f not in nuevas]
            if extra:
                failed = [t.get('prueba') or 'prueba sin nombre' for t in tests if not t['ok']]
                raise Stop((f"falta permitir {', '.join(extra)}: el constructor necesitó tocarlo y {', '.join(failed)} falla sin ese cambio; hay que ampliar los archivos permitidos")[:500])
            continue
        git(path, 'add', '--', *files)
        # Review and integrate exactly what was tested: nothing may stay outside the index.
        if git(path, 'diff', '--name-only'): raise Stop('quedan cambios sin incluir tras preparar la revisión')
        diff = git(path, 'diff', '--cached')
        log(pid, task['id'], 'revisando'); phase(folder, 'revisando', attempt)
        reviewer = {}
        try:
            ampliadas = [f for f in allowed if f not in spec['archivos']]
            revisar = {**spec, 'archivos': allowed, 'aviso_revisor': 'Estas pruebas no estaban aprobadas y se permitieron porque fallaban: ' + ', '.join(ampliadas)
                       + '. Comprueba que solo se actualizaron por el cambio que pide la tarea y que no se debilitaron.'} if ampliadas else spec
            verdict = json_block(claude(REVIEW_PROMPT.format(name=p['name'], spec=json.dumps(revisar, ensure_ascii=False, indent=1), diff=diff[:60000],
                                                             tests=', '.join(t['prueba'] for t in tests), lessons=lessons_text(pid, 'revisor')),
                                        path, LIMITS['review_seconds'], out=folder / f'review-{attempt}.json', heartbeat=heartbeat,
                                        role='revisor', spec=spec, attempt=attempt, diff_lines=diff.count('\n'),
                                        builder=cycle.get('modelo'), info=reviewer))
        except Stop as e:
            if str(e) == 'timeout': raise Stop(f"timeout al revisar ({LIMITS['review_seconds']} s)")
            raise
        cycle['revision'] = verdict; cycle['revisor'] = reviewer.get('modelo'); receipt['ciclos'].append(cycle)
        if verdict.get('seguimiento'):
            notes = [s for s in verdict['seguimiento'] if isinstance(s, str)][:5]
            follow_up(pid, [f'[{spec["titulo"]}] {s}' for s in notes])
            post('control', ['product'], f'Al revisar «{spec["titulo"]}» he visto: ' + '; '.join(notes), 'idea', pid)
        if verdict.get('decision') == 'aprobar':
            sha = integrate(pid, spec, path, branch, folder, heartbeat, builder=cycle.get('modelo'))
            receipt.update(commit=sha, fin=now().isoformat(), estado='integrada'); save(folder / 'receipt.json', receipt)
            return f"Integrada en {team} ({sha[:8]}). Revisión: {verdict.get('motivos', '')[:300]}"
        feedback = 'El revisor independiente pide cambios: ' + str(verdict.get('motivos', ''))[:2000]
        git(path, 'reset', '-q', check=False)
    # The same first FAIL line in both last failed cycles tells the planner it is a repeated failure, not a new one; the note is never cut.
    # When the first FAIL line would fall outside the 500 characters, it goes first so the planner sees which test fails.
    prefix = 'agotados los intentos: '
    linea = primera_fail(failure)
    def con_linea(room):
        return linea + ' · ' + failure if linea and linea not in failure[:room] else failure
    if failure and fallo_repetido(receipt['ciclos']):
        room = 500 - len(prefix) - len(MISMO_FALLO)
        raise Stop((prefix + con_linea(room))[:500 - len(MISMO_FALLO)] + MISMO_FALLO)
    room = 500 - len(prefix)
    # Without any FAIL line the real error is at the end of the output (where Node writes it), so the tail goes in.
    if failure and not linea: raise Stop((prefix + cola_fallo(failure, room))[:500])
    raise Stop((prefix + (con_linea(room) if failure else feedback[:400]))[:500])

def integrate(pid, spec, path, branch, folder, heartbeat, builder=None):
    p = project(pid); team = f'equipo/{pid}'
    who = f'Construido por Codex ({builder}) y revisado por Claude.' if is_codex(builder) else 'Construido y revisado por agentes Claude.'
    git(path, 'commit', '-q', '-m', f"equipo: {spec['titulo']}\n\n{spec.get('porque', '')}\n\nLote {spec['lote']} aprobado por Owner. {who}\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>")
    tip = git(p['repo'], 'rev-parse', team)
    if git(path, 'merge-base', 'HEAD', team) != tip:
        # Another task landed first: replay this one on top and test it again.
        if subprocess.run(['git', '-C', str(path), 'rebase', team], capture_output=True, env=clean_env()).returncode:
            git(path, 'rebase', '--abort', check=False); raise Stop('conflicto con otra tarea del equipo')
        if not all(t['ok'] for t in run_tests(pid, spec, path, folder / 'rebase', heartbeat)): raise Stop('las pruebas fallan tras rebasar')
    head = git(path, 'rev-parse', 'HEAD')
    # Fast-forward the team branch without checking it out anywhere; refuses if it moved meanwhile.
    git(p['repo'], 'update-ref', f'refs/heads/{team}', head, tip)
    git(p['repo'], 'worktree', 'remove', '--force', str(path), check=False); git(p['repo'], 'branch', '-D', branch, check=False)
    return head

def lista_conflictos(archivos, limite=5):
    if not archivos: return ''
    extra = f' y {len(archivos) - limite} más' if len(archivos) > limite else ''
    return ' (en ' + ', '.join(archivos[:limite]) + extra + ')'

def traer_base(pid):
    """03/11: Owner and Codex merge pull requests on GitHub, so the local base fell 39 commits behind in Norte and the team
    kept building on old code. Before syncing, the base takes what GitHub has when that is a plain fast-forward: the ref
    moves if the base is not checked out, or by `merge --ff-only` if it is and the folder has no changes. If they have
    diverged, nothing moves and Operaciones is told once."""
    p = project(pid); repo = p['repo']; base = p['base']
    got = subprocess.run(['git', '-C', str(repo), 'fetch', '-q', 'origin', base], capture_output=True, text=True, env=clean_env(), timeout=60)
    if got.returncode: return 'sin red'
    remote = git(repo, 'rev-parse', '--verify', '-q', f'refs/remotes/origin/{base}', check=False); local = git(repo, 'rev-parse', base)
    if not remote or remote == local: return 'al día'
    ancestor = lambda a, b: subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', a, b], env=clean_env()).returncode == 0
    if ancestor(remote, local): return 'por delante'
    if not ancestor(local, remote):
        seen = load(STATE / 'sync.json', {})
        if seen.get(f'{pid}:origin') != remote:
            post('product', ['alfred'], f"Tu rama {base} de este Mac y la de GitHub se han separado: el equipo sigue con la de este Mac hasta que se arregle.", 'aviso', pid)
            save(STATE / 'sync.json', {**seen, f'{pid}:origin': remote})
        return 'separadas'
    if git(repo, 'rev-parse', '--abbrev-ref', 'HEAD', check=False) == base:
        if git(repo, 'status', '--porcelain', '--untracked-files=no'): return 'con cambios sin guardar'
        if subprocess.run(['git', '-C', str(repo), 'merge', '--ff-only', '-q', remote], capture_output=True, env=clean_env()).returncode: return 'no avanza'
    else:
        git(repo, 'update-ref', f'refs/heads/{base}', remote, local)
    log(pid, base, 'al día con GitHub'); return 'traída'

def sync_base(pid):
    """Before a task, bring Owner's newer commits on the base into the team branch so the work builds on current
    code. Clean merges only: on a conflict the branch stays as it is and Operaciones is told once per base commit."""
    try: traer_base(pid)
    except Exception as e: log(pid, 'no he podido traer la base de GitHub:', e)
    p = project(pid); repo = p['repo']; ref = f'refs/heads/equipo/{pid}'
    tip = git(repo, 'rev-parse', ref); base = git(repo, 'rev-parse', p['base'])
    ancestor = lambda a, b: subprocess.run(['git', '-C', str(repo), 'merge-base', '--is-ancestor', a, b], env=clean_env()).returncode == 0
    if ancestor(base, tip): return 'al día'
    if ancestor(tip, base):
        git(repo, 'update-ref', ref, base, tip); log(pid, 'rama del equipo adelantada a', p['base']); return 'adelantada'
    merged = subprocess.run(['git', '-C', str(repo), 'merge-tree', '--write-tree', '--name-only', '--no-messages', tip, base], capture_output=True, text=True, env=clean_env())
    if merged.returncode:
        archivos = [line for line in merged.stdout.splitlines()[1:] if line.strip()] if merged.returncode == 1 else []
        seen = load(STATE / 'sync.json', {})
        if seen.get(pid) != base:
            post('product', ['alfred'], f"La rama del equipo no puede traer lo nuevo de {p['base']} sin conflictos{lista_conflictos(archivos)}; las tareas siguen sobre lo que tiene. Conviene que Owner fusione o resuelva.", 'aviso', pid)
            save(STATE / 'sync.json', {**seen, pid: base})
        log(pid, 'conflicto al traer', p['base']); return 'conflicto'
    commit = git(repo, 'commit-tree', merged.stdout.split()[0], '-p', tip, '-p', base, '-m', f"equipo: trae lo nuevo de {p['base']}")
    git(repo, 'update-ref', ref, commit, tip); log(pid, 'rama del equipo al día con', p['base']); return 'fusionada'

def run_project(pid):
    """One task end to end for this project, if any approved one is ready. Holds the project lock."""
    lock = (ROOT / 'locks'); lock.mkdir(parents=True, exist_ok=True)
    with open(lock / f'{pid}.lock', 'w') as fh:
        try: pf.lock(fh, block=False)
        except BlockingIOError: return 'ocupado'
        ledger = load(STATE / 'approved.json', {})
        ready = [t for t in tasks(pid, 'ready') if t['id'] in ledger and t['assignee'] == LANE]
        if not ready:
            try: return 'siguiente ronda: ' + (next_round(pid) or 'no')
            except Exception as e: log(pid, 'no he podido preparar la siguiente ronda:', e); return 'sin tareas'
        if lid_will_sleep(): return 'en reposo: tapa cerrada'
        if saturado(): return f'espero: el Mac está saturado (carga {carga():.1f} por núcleo)'
        task = sorted(ready, key=lambda t: t['created_at'])[0]
        claim = claimer()
        with board(pid) as conn: claimed = kb.claim_task(conn, task['id'], ttl_seconds=600, claimer=claim)
        if not claimed: return 'reclamada por otro'
        run_id = claimed.current_run_id
        caffeinate = pf.keep_awake()  # keep the computer awake while working
        slept = sleep_clock()
        try:
            try: sync_base(pid)
            except Exception as e: log(pid, 'no he podido traer la base:', e)
            summary = execute(pid, task, claim, run_id)
            with board(pid) as conn: kb.complete_task(conn, task['id'], result=summary, summary=summary, expected_run_id=run_id)
            log(pid, task['id'], 'hecha:', summary)
            try: avisar_encargo(task, 'hecha', summary)
            except Exception: pass
            try: avisar_lanzamiento(pid, task)
            except Exception: pass
            return 'hecha'
        except Exception as e:
            if isinstance(e, Asleep) or sleep_clock() - slept > SLEEP_GAP:
                return requeue_asleep(pid, task, str(e))
            if isinstance(e, Saturado):
                file = STATE / 'runs' / task['id'] / 'saturado.json'
                veces = load(file, {}).get('veces', 0)
                if veces < SATURADO_VECES:
                    save(file, {'veces': veces + 1, 'ultima': now().isoformat(timespec='seconds')})
                    return requeue_saturado(pid, task, str(e))
            if str(e).startswith(RED_AGOTADA):
                file = STATE / 'runs' / task['id'] / 'red.json'
                red = load(file, {})
                try:
                    veces = red.get('veces', 0)
                    if 'ultima' in red and now() - datetime.fromisoformat(red['ultima']) > timedelta(hours=RED_VENTANA_HORAS):
                        veces = 0
                except (AttributeError, TypeError, ValueError):
                    veces = 0
                if veces < 3:
                    save(file, {'veces': veces + 1, 'ultima': now().isoformat(timespec='seconds')})
                    return requeue_red(pid, task, str(e))
            reason = motivo_bloqueo(e, STATE / 'runs' / task['id'])
            with board(pid) as conn: kb.block_task(conn, task['id'], reason=reason, kind='needs_input', expected_run_id=run_id)
            follow_up(pid, [f'[bloqueada: {task["title"]}] {reason}'])
            post('product', ['alfred'], f'Se me ha bloqueado «{task["title"]}»: {reason[:300]}', 'aviso', pid)
            log(pid, task['id'], 'bloqueada:', reason)
            try: avisar_encargo(task, 'bloqueada', reason)
            except Exception: pass
            return 'bloqueada'
        finally:
            caffeinate.terminate()

def requeue_red(pid, task, why):
    """Retry a network outage without treating it as feedback on the work."""
    with board(pid) as conn: kb.reclaim_task(conn, task['id'], reason='la API de Claude no respondía', signal_fn=lambda *a, **k: None)
    file = STATE / 'runs' / task['id'] / 'receipt.json'
    receipt = load(file, {})
    save(file, {**receipt, 'estado': 'interrumpida', 'error': 'la API de Claude no respondía: vuelve a la cola', 'red': True, 'detalle': why[:300]})
    log(pid, task['id'], 'interrumpida: la API de Claude no respondía; vuelve a la cola')
    return 'interrumpida'

def requeue_saturado(pid, task, why):
    """Back to the queue: the test ran out of time because something else held the Mac, not because of the work."""
    with board(pid) as conn: kb.reclaim_task(conn, task['id'], reason='el Mac estaba saturado', signal_fn=lambda *a, **k: None)
    file = STATE / 'runs' / task['id'] / 'receipt.json'
    receipt = load(file, {})
    if receipt: save(file, {**receipt, 'estado': 'interrumpida', 'error': 'el Mac estaba saturado: vuelve a la cola', 'detalle': why[:300]})
    log(pid, task['id'], 'interrumpida: el Mac estaba saturado; vuelve a la cola')
    return 'interrumpida'

def requeue_asleep(pid, task, why):
    """Back to the queue with a fresh retry budget; the receipt says it was interrupted, so the next run does not take
    a frozen test as feedback."""
    with board(pid) as conn: kb.reclaim_task(conn, task['id'], reason='el Mac se durmió a mitad', signal_fn=lambda *a, **k: None)
    file = STATE / 'runs' / task['id'] / 'receipt.json'
    receipt = load(file, {})
    if receipt: save(file, {**receipt, 'estado': 'interrumpida', 'error': 'el Mac se durmió a mitad: vuelve a la cola', 'detalle': why[:300]})
    log(pid, task['id'], 'interrumpida: el Mac se durmió a mitad; vuelve a la cola')
    return 'interrumpida'

def desbloquear(pid, ids, limpio=False):
    """Owner puts blocked tasks back in the queue. `limpio`: the block was not the work's fault (the Mac asleep, the
    network), so the next run starts without the old failure as feedback."""
    out = {}
    with board(pid) as conn:
        for tid in ids:
            if limpio:
                file = STATE / 'runs' / tid / 'receipt.json'; receipt = load(file, {})
                if receipt: save(file, {**receipt, 'estado': 'interrumpida', 'detalle': str(receipt.get('error', ''))[:300]})
            # Hermes parks some blocked tasks in 'triage' (they show as blocked too): triage -> todo -> ready.
            ok = kb.unblock_task(conn, tid)
            if not ok and hasattr(kb, 'specify_triage_task') and kb.specify_triage_task(conn, tid):
                # without unfinished parents Hermes already lands it in 'ready'; otherwise it waits in 'todo' for them
                ok = kb.promote_task(conn, tid, actor='owner', reason='desbloqueada por Owner')[0] or kb._task_status(conn, tid) in ('ready', 'todo')
            if not ok: out[tid] = 'no estaba bloqueada'; continue
            out[tid] = 'a la cola'; log(pid, tid, 'desbloqueada por Owner' + (' (sin el fallo anterior)' if limpio else ''))
    return out

def archivar(pid, ids):
    """Owner retires tasks that no longer make sense (done by another task, superseded)."""
    out = {}
    with board(pid) as conn:
        for tid in ids:
            if not kb.archive_task(conn, tid, signal_fn=lambda *a, **k: None): out[tid] = 'no se ha podido'; continue
            out[tid] = 'archivada'; log(pid, tid, 'archivada por Owner')
    return out

def run_all():
    # Health runs even when the team is paused: a paused team is exactly when a silent failure goes unseen.
    try: salud()
    except Exception as e: log('no he podido comprobar la salud:', e)
    # Today's to-do when the app is closed: the person's day does not depend on the team being on.
    try: hoy_manana()
    except Exception as e: log('no he podido mandar la to-do de hoy:', e)
    try: buenos_dias()
    except Exception as e: log('no he podido mandar los buenos días:', e)
    for paso, nombre in ((para_ti, 'la lista de Owner en palabras llanas'), (mejoras, 'qué mejoras esperan a juntarse'), (cierre_dia, 'el cierre del día'), (cierre_semana, 'el cierre de la semana'), (repaso_semanal, 'las tarjetas de la semana')):
        try: paso()
        except Exception as e: log(f'no he podido hacer {nombre}:', e)
    # The reminder costs no plan either, so it also goes out while the team is paused.
    try: recordar_medir()
    except Exception as e: log('no he podido recordar medir lo lanzado:', e)
    try: recordar_lanzar()
    except Exception as e: log('no he podido recordar lanzar:', e)
    if (ROOT / 'PAUSED').exists(): log('pausado'); return
    why = freno()
    if why: log('freno:', why); return
    # Only when approving would put the team to work: not paused, not braked by the budget.
    try: recordar_decisiones()
    except Exception as e: log('no he podido recordar las decisiones:', e)
    if load(STATE / 'plan_aplazado.json', {}).get('fecha') == today():
        (STATE / 'plan_aplazado.json').unlink(missing_ok=True)
        log('el freno se ha levantado: hago el plan de hoy'); plan(list(CONFIG['projects']))
    try: answer_owner()
    except Exception as e: log('no he podido responder a Owner:', e)
    pids = list(CONFIG['projects'])
    with concurrent.futures.ThreadPoolExecutor(LIMITS['parallel']) as pool:
        for pid, result in zip(pids, pool.map(lambda pid: subprocess.run([sys.executable, __file__, 'run-project', pid], env=clean_env()).returncode, pids)):
            pass
    try: write_estado()
    except Exception as e: log('no he podido escribir el estado en Obsidian:', e)

# ---------- learning loops ----------
# Every day, before planning, the team looks back at what actually happened (receipts, blocks, reviews, what Owner
# discarded or merged) and keeps a short, evidence-backed list of lessons per project. Lessons are injected into the
# planner, builder and reviewer prompts. Process changes it suggests become Owner OS tasks, approved like any other.

LESSON_CAP = 20
ROLES = {'planificador', 'constructor', 'revisor', 'todos'}

def remember_discarded(pid, date, items):
    file = STATE / 'senales' / f'{pid}.json'
    signals = load(file, {'descartadas': []})
    signals['descartadas'] = (signals['descartadas'] + [{'fecha': date, 'titulo': t['titulo'], 'porque': t.get('porque', '')} for t in items])[-30:]
    save(file, signals)

def receipts(pid, since_hours=None):
    """Execution receipts of this project, newest last; optionally only the recent ones."""
    found = []
    ledger = load(STATE / 'approved.json', {})
    for path in (STATE / 'runs').glob('*/receipt.json'):
        r = load(path, None)
        if not r or (r.get('proyecto') or ledger.get(r.get('tarea'), {}).get('proyecto')) != pid: continue
        end = r.get('fin') or r.get('inicio')
        if since_hours and end and datetime.fromisoformat(end).timestamp() < now().timestamp() - since_hours * 3600: continue
        found.append(r)
    return sorted(found, key=lambda r: r.get('fin') or r.get('inicio') or '')

MOTIVO_NO_REGISTRADO = 'bloqueada antes del arreglo, motivo no registrado'

def motivo_visible(t):
    """Why a blocked task is stopped, as Owner sees it: the board's error, else its blocked receipt's error, else a fixed text; never empty."""
    board = str(t.get('last_failure_error') or '').strip()
    if board: return board
    receipt = load(STATE / 'runs' / t['id'] / 'receipt.json', {})
    error = str(receipt.get('error') or '').strip() if receipt.get('estado') == 'bloqueada' else ''
    return error or MOTIVO_NO_REGISTRADO

def merged_by_owner(pid, commits):
    """Which team commits Owner has already taken into his own branch (the strongest value signal)."""
    p = project(pid); out = []
    return [sha for sha in commits if merged(p['repo'], p['base'], sha)]

def metrics(pid, days=7):
    rs = receipts(pid, since_hours=days * 24)
    done = [r for r in rs if r.get('estado') == 'integrada']
    blocked_by_scope = [r for r in rs if r.get('estado') == 'bloqueada' and str(r.get('error') or '').startswith('falta permitir')]
    minutes = [(datetime.fromisoformat(r['fin']) - datetime.fromisoformat(r['inicio'])).total_seconds() / 60 for r in rs if r.get('fin') and r.get('inicio')]
    reviews = [c.get('revision', {}).get('decision') for r in rs for c in r.get('ciclos', []) if c.get('revision')]
    since = (now() - timedelta(days=days)).date().isoformat()
    decisions = [d for d in load(STATE / 'senales' / f'{pid}.json', {}).get('decisiones', []) if since <= d['fecha'] <= today()]
    with_card = [d['minutos'] for d in decisions if d.get('ficha')]
    without_card = [d['minutos'] for d in decisions if not d.get('ficha')]
    base_seconds = [c['base_segundos'] for r in rs for c in r.get('ciclos', []) if 'base_segundos' in c]
    return {
        'dias': days, 'tareas': len(rs), 'integradas': len(done),
        'a_la_primera': sum(1 for r in done if len(r.get('ciclos', [])) == 1),
        'bloqueadas': sum(1 for r in rs if r.get('estado') == 'bloqueada'),
        'bloqueadas_por_alcance': len(blocked_by_scope),
        'intentos_en_bloqueadas_por_alcance': sum(len(r.get('ciclos') or []) for r in blocked_by_scope),
        'cambios_pedidos': reviews.count('cambios'),
        'pruebas_fallidas': sum(1 for r in rs for c in r.get('ciclos', []) if any(not t.get('ok') for t in c.get('pruebas', []))),
        'repetidas': sum(1 for r in rs for c in r.get('ciclos', []) for t in c.get('pruebas', []) if t.get('repetida')),
        'repetidas_por_timeout': sum(1 for r in rs for c in r.get('ciclos', []) for t in c.get('pruebas', []) if t.get('repetida_por') == 'timeout'),
        'repetidas_por_timeout_en_verde': sum(1 for r in rs for c in r.get('ciclos', []) for t in c.get('pruebas', []) if t.get('repetida_por') == 'timeout' and t.get('ok')),
        'repetidas_por_arranque': sum(1 for r in rs for c in r.get('ciclos', []) for t in c.get('pruebas', []) if t.get('repetida_por') == 'arranque'),
        'repetidas_por_arranque_en_verde': sum(1 for r in rs for c in r.get('ciclos', []) for t in c.get('pruebas', []) if t.get('repetida_por') == 'arranque' and t.get('ok')),
        'fuera_de_alcance': sum(1 for r in rs for c in r.get('ciclos', []) if c.get('fuera_de_alcance')),
        'ya_estaba_hecho': sum(1 for r in rs if r.get('estado') == 'bloqueada' and str(r.get('error') or '').startswith('ya estaba hecho')),
        'reintentos_infra': sum(1 for r in rs for c in r.get('ciclos', []) if 'pruebas_infra' in c),
        'reintentos_infra_en_verde': sum(1 for r in rs for c in r.get('ciclos', []) if 'pruebas_infra' in c and c.get('pruebas') and all(t.get('ok') for t in c['pruebas'])),
        'pruebas_mixtas': sum(1 for r in rs for c in r.get('ciclos', []) if c.get('pruebas_mixtas')),
        'bases_miradas': len(base_seconds),
        'bloqueadas_base_roja': sum(1 for r in rs if r.get('estado') == 'bloqueada' and str(r.get('error') or '').startswith('base roja')),
        'segundos_base_medios': round(sum(base_seconds) / len(base_seconds), 1) if base_seconds else None,
        'minutos_medios': round(sum(minutes) / len(minutes), 1) if minutes else None,
        'fusionadas_por_ti': len(merged_by_owner(pid, [r['commit'] for r in done if r.get('commit')])),
        'decisiones_tuyas': len(decisions),
        'minutos_hasta_decision': round(median(d['minutos'] for d in decisions), 1) if decisions else None,
        'minutos_hasta_decision_con_ficha': round(median(with_card), 1) if with_card else None,
        'minutos_hasta_decision_sin_ficha': round(median(without_card), 1) if without_card else None,
        'decisiones_con_ficha': len(with_card),
        'decisiones_sin_ficha': len(without_card),
    }

def lessons(pid): return load(STATE / 'lecciones' / f'{pid}.json', [])

def merge_lessons(current, answer, today_=None):
    """Apply a retrospective: reinforce, retire and add lessons; keep the most useful LESSON_CAP."""
    today_ = today_ or today()
    by_id = {l['id']: l for l in current}
    for lid in answer.get('retirar', []) or []: by_id.pop(lid, None)
    for lid in answer.get('refuerzan', []) or []:
        if lid in by_id: by_id[lid]['refuerzos'] = by_id[lid].get('refuerzos', 0) + 1; by_id[lid]['ultima'] = today_
    n = max([int(l['id'][1:]) for l in current if l['id'][1:].isdigit()] + [0])
    for item in (answer.get('nuevas') or [])[:5]:
        text, role = str(item.get('texto', '')).strip(), item.get('para', 'todos')
        if len(text) < 15 or role not in ROLES: continue
        if any(text.lower() == l['texto'].lower() for l in by_id.values()): continue
        n += 1
        by_id[f'L{n}'] = {'id': f'L{n}', 'para': role, 'texto': text[:400], 'evidencia': [str(e) for e in item.get('evidencia', [])][:5],
                          'creada': today_, 'ultima': today_, 'refuerzos': 0}
    ranked = sorted(by_id.values(), key=lambda l: (l.get('refuerzos', 0), l.get('ultima', '')), reverse=True)
    return sorted(ranked[:LESSON_CAP], key=lambda l: int(l['id'][1:]) if l['id'][1:].isdigit() else 0)

def lessons_text(pid, role):
    chosen = [l for l in lessons(pid) if l['para'] in {role, 'todos'}]
    if not chosen: return ''
    return 'Lecciones aprendidas por el equipo en este proyecto (aplícalas):\n' + '\n'.join(f"- {l['texto']}" for l in chosen)

def _sin_medir_texto(e):
    text = 'Lanzados sin resultado apuntado: ' + ', '.join(f"«{x['titulo']}»" for x in e['sin_medir'])
    total = e.get('sin_medir_total')
    if isinstance(total, int) and not isinstance(total, bool) and total > len(e['sin_medir']):
        text += f" y {total - len(e['sin_medir'])} más"
    return text + '. Pide a Owner lo medido antes de proponer el siguiente experimento de esa línea.'


def planner_signals(pid):
    parts = [lessons_text(pid, 'planificador')]
    citas = citas_para_rehacer(pid)
    if citas['lotes'] > 0:
        line = (f"Propuestas con reels de «Para rehacer» delante (7 días): {citas['citan']} de {citas['propuestas']} "
                f"citan uno, en {citas['lotes']} lotes.")
        if citas['lotes'] < 5: line += ' Con menos de 5 lotes no es señal.'
        parts.append(line)
    recent = receipts(pid, since_hours=7 * 24)
    already_done, red_base = [], []
    seen_titles = set()
    for receipt in reversed(recent):
        if receipt.get('estado') != 'bloqueada': continue
        error = str(receipt.get('error') or '')
        title = receipt.get('titulo') or receipt.get('tarea')
        if error.startswith('base roja') and title and title not in red_base and len(red_base) < 3: red_base.append(title)
        if not error.startswith('ya estaba hecho') or len(already_done) == 5: continue
        if not title or title in seen_titles: continue
        already_done.append(title)
        seen_titles.add(title)
    if already_done:
        parts.append('Tareas que el constructor encontró ya hechas (7 días): '
                     + ', '.join(f'«{title}»' for title in reversed(already_done))
                     + '. No las propongas otra vez: si hace falta, propón el paso siguiente.')
    if red_base:
        parts.append('Bloqueadas porque la prueba ya fallaba antes del cambio (7 días): '
                     + ', '.join(f'«{title}»' for title in reversed(red_base))
                     + '. Propón primero una tarea pequeña que arregle esa prueba (con su archivo en los permitidos) y después repite la tarea.')
    discarded = load(STATE / 'senales' / f'{pid}.json', {}).get('descartadas', [])[-10:]
    if discarded: parts.append('Propuestas que Owner descartó al aprobar (no repitas ese tipo salvo que cambie el contexto): '
                               + json.dumps([d['titulo'] for d in discarded], ensure_ascii=False))
    agreed = [a for a in mesa_load().get('reunion', {}).get('acuerdos', []) if a.get('proyecto') == pid]
    if agreed: parts.append('Acuerdos de la mesa del equipo para este proyecto (priorízalos si encajan con el objetivo): '
                            + json.dumps([{k: a.get(k) for k in ('titulo', 'porque', 'detalle')} for a in agreed], ensure_ascii=False))
    measured = resultados(pid)[-8:]
    if measured:
        parts.append('Resultados de experimentos que Owner ha medido (son datos reales: úsalos para decidir seguir, cambiar o parar): '
                     + json.dumps([{k: r.get(k) for k in ('fecha', 'titulo', 'texto', 'decision')} for r in measured], ensure_ascii=False))
        for decision, text in (('parar', 'Owner decidió parar: {}. No propongas más experimentos de esa línea.'),
                               ('cambiar', 'Owner pidió cambiar: {}. Propón una variante con otra hipótesis u otro público, no la misma.'),
                               ('seguir', 'Owner decidió seguir: {}. Propón el siguiente paso de esa línea (más alcance, otro canal o la siguiente fase: contenido → oferta → producto) con su ficha de lanzamiento; no repitas el mismo experimento.')):
            titles = [f"«{r.get('titulo') or r.get('texto', '')}»" for r in measured if r.get('decision') == decision]
            if titles: parts.append(text.format(', '.join(titles)))
        parents = [f"«{r.get('titulo') or r.get('texto', '')}» → {r['tarea']}" for r in measured if r.get('decision') == 'seguir' and r.get('tarea')]
        if parents: parts.append('Si propones el siguiente paso de una de ellas, añade a la propuesta "viene_de" con el id de su tarea: ' + ', '.join(parents))
    e = experimentos(pid)
    if e['avisados'] > 0:
        launched = f"{e['lanzados']} lanzados por Owner, mediana {e['horas_hasta_lanzar']} h hasta lanzar" if e['lanzados'] else 'ninguno lanzado todavía'
        line = f"Experimentos listos para lanzar (7 días): {e['avisados']} avisados, {launched}. Si pocos se lanzan, propón experimentos más pequeños o fichas más claras."
        if e['sin_medir']: line += ' ' + _sin_medir_texto(e)
        parts.append(line)
    elif e['sin_medir']:
        parts.append(_sin_medir_texto(e))
    if e.get('avisados_sin_fusionar', 0) > 0:
        n, f = e['avisados_sin_fusionar'], e.get('fusionados_24h', 0)
        line = f'Avisos con trabajo aún sin fusionar (7 días): {n}, fusionados en 24 h: {f}'
        if n < 5: line += '. Con menos de 5 no es señal.'
        else:
            pct = round(f / n * 100)
            line += f' ({pct} %).'
            if pct >= 50: line += ' El botón «Fusionar ahora» funciona: mantenlo.'
            elif pct < 20: line += ' Menos del 20 %: propón otra forma de llevar el trabajo a la versión principal antes de lanzar.'
            else: line += ' Entre el 20 % y el 50 %: sin conclusión todavía.'
        parts.append(line)
    d = e.get('decisiones') or {}
    total = sum(d.get(k, 0) for k in (*DECISIONES, 'sin_decision'))
    if total > 0:
        line = f"Resultados apuntados (7 días): {total}, con decisión {total - d.get('sin_decision', 0)}."
        if d.get('sin_decision', 0) > 0: line += ' Pide a Owner en la mesa si sigue, cambia o para antes de proponer el siguiente experimento de esa línea.'
        parts.append(line)
    waiting = []
    if d.get('seguir', 0) > 0:
        parts.append(f"Con «seguir» (7 días): {d['seguir']}, con siguiente paso avisado: {e.get('seguir_con_siguiente', 0)}.")
    for x in e.get('por_lanzar', []):
        try:
            if now() - datetime.fromisoformat(x['avisado']) >= timedelta(hours=LANZAR_ESPERA_HORAS): waiting.append(x)
        except (ValueError, TypeError): continue
    if waiting:
        parts.append(f'Avisados hace más de {LANZAR_ESPERA_HORAS} h y sin lanzar: ' + ', '.join(f"«{x['titulo']}»" for x in waiting)
                     + '. Antes de proponer otro experimento de esa línea, propón uno más pequeño o pregunta a Owner en la mesa qué le frena.')
    if e.get('por_lanzar_total', 0) >= POR_LANZAR_TOPE:
        n = e['por_lanzar_total']
        parts.append(f"Esperan a Owner {n} experimentos avisados sin lanzar ni descartar."
                     f" Con {POR_LANZAR_TOPE} o más, no añadas fichas de lanzamiento nuevas en este lote salvo una más pequeña que sustituya a las que esperan; prioriza lo que le ayude a lanzarlas o descartarlas.")
    if e.get('motivos_descarte'):
        parts.append('Owner descartó sin lanzar: ' + ', '.join(f"«{x['titulo']}» ({x['motivo']})" for x in e['motivos_descarte'])
                     + '. No propongas otro experimento con ese mismo freno.')
    if e.get('recordados_lanzar', 0) > 0:
        r, done = e['recordados_lanzar'], e['decididos_tras_recordar_lanzar']
        line = f"Recordatorios de lanzar (7 días): {r} enviados, {done} lanzados o descartados en 72 h."
        if r < 5: line += ' Con menos de 5 no es señal.'
        parts.append(line)
    if e['recordados'] > 0:
        r, done = e['recordados'], e['medidos_tras_recordar']
        if r < 5: parts.append(f"Recordatorios de medir (7 días): {r} enviados, {done} con resultado en 72 h. Con menos de 5 no es señal.")
        else: parts.append(f"Recordatorios de medir (7 días): {r} enviados, {done} con resultado en 72 h ({round(done / r * 100)} %)."
                           + (' Menos de la mitad acaba medido: propón otra forma de pedir el resultado (por ejemplo, un botón en la app).'
                              if done / r < 0.5 else ' El recordatorio funciona: mantenlo.'))
    m = metrics(pid)
    if m['tareas']: parts.append('Métricas de los últimos 7 días: ' + json.dumps(m, ensure_ascii=False)
                                 + '. Si hay bloqueos o cambios pedidos, propón tareas más pequeñas y mejor acotadas.')
    decididos = e.get('lanzados', 0) + e.get('descartados', 0) + sum((e.get('decisiones') or {}).values())
    if m.get('integradas', 0) >= FRENO_LOTE_INTEGRADAS and decididos == 0 and e.get('por_lanzar_total', 0) >= 1:
        parts.append(f"Freno de lote: en 7 días se integraron {m['integradas']} tareas y Owner no ha lanzado, descartado ni medido ningún experimento (experimentos esperando a Owner: {e['por_lanzar_total']}). Propón como mucho 2 tareas en este lote y ninguna que añada otra cifra, comparación o aviso a lo que ya se mide; prioriza lo que le ayude a lanzar o descartar lo que espera, o propón menos.")
    if m.get('bloqueadas_por_alcance', 0) > 0:
        n, tries = m['bloqueadas_por_alcance'], m.get('intentos_en_bloqueadas_por_alcance', 0)
        mean = tries / n
        line = f"Bloqueadas por falta de permiso para un archivo (7 días): {n}, con {tries} intentos (media {f'{mean:.1f}'.replace('.', ',')} por tarea)."
        if mean > 1: line += ' Lista en cada tarea todos los archivos que tocará, pruebas incluidas.'
        parts.append(line)
    if m.get('repetidas_por_timeout', 0) > 0:
        r, green = m['repetidas_por_timeout'], m.get('repetidas_por_timeout_en_verde', 0)
        line = f'Pruebas repetidas por timeout (7 días): {r}, en verde {green}.'
        if green > 0: line += ' La repetición salva tareas: mantenla.'
        elif r >= 3: line += ' Ninguna repetición por timeout acabó en verde: propón quitarla o acelerar la suite.'
        else: line += ' Con menos de 3 no es señal.'
        parts.append(line)
    if m.get('repetidas_por_arranque', 0) > 0:
        r, green = m['repetidas_por_arranque'], m.get('repetidas_por_arranque_en_verde', 0)
        line = f'Pruebas repetidas porque el servidor de pruebas tardó en arrancar (7 días): {r}, en verde {green}.'
        if green > 0: line += ' La repetición salva tareas: mantenla.'
        elif r >= 3: line += ' Ninguna acabó en verde: propón arreglar el arranque del servidor de pruebas en vez de repetir.'
        else: line += ' Con menos de 3 no es señal.'
        parts.append(line)
    if m.get('bases_miradas', 0) > 0:
        n = m['bases_miradas']
        line = f"Comprobaciones de la prueba en la rama base (7 días): {n}, {m.get('bloqueadas_base_roja', 0)} acabaron en «base roja», media {m.get('segundos_base_medios')} s."
        if n < 5: line += ' Con menos de 5 no es señal.'
        parts.append(line)
    if m.get('decisiones_tuyas', 0) > 0:
        minutes = lambda v: 'sin datos' if v is None else f'{v} min'
        with_card, without_card = m.get('minutos_hasta_decision_con_ficha'), m.get('minutos_hasta_decision_sin_ficha')
        enough = m.get('decisiones_con_ficha', 0) >= 5 and m.get('decisiones_sin_ficha', 0) >= 5 and with_card is not None and without_card is not None
        if not enough: verdict = 'Con menos de 5 por grupo no es señal.'
        elif with_card == without_card: verdict = 'Señal: sin diferencia.'
        else: verdict = f"Señal: con ficha decides {'más rápido' if with_card < without_card else 'más lento'} que sin ficha."
        parts.append(f"Tiempo de decisión de Owner: con ficha {minutes(with_card)} (n={m.get('decisiones_con_ficha', 0)}), "
                     f"sin ficha {minutes(without_card)} (n={m.get('decisiones_sin_ficha', 0)}). {verdict}")
    return '\n'.join(p for p in parts if p)

RETRO_PROMPT = """Eres el RETROSPECTIVO del equipo de agentes de Owner, proyecto {name}. Tu trabajo es que el equipo mejore cada día.
REGLA DE OWNER (prioritaria): no propongas, construyas ni apruebes cambios hechos por motivos legales, éticos o de cumplimiento (quitar o suavizar cifras de premios, la palabra cashback, promesas, avisos, textos legales, marcar cifras como hipótesis). De eso se encarga Owner. Si ves un riesgo legal o ético, menciónaselo una vez a Owner en la mesa y no crees tareas para cambiarlo.
Analiza lo que pasó desde la última retrospectiva y decide qué aprender. Solo lecciones accionables y respaldadas por hechos
de abajo (cita el id de tarea como evidencia); nada genérico ni inventado. Puedes leer el repo para entender un fallo.
Capacidades reales de cada rol (una lección debe poder cumplirse con ellas): el planificador lee el repo y propone tareas
(archivos permitidos, pruebas por nombre, dependencias); el constructor SOLO lee y edita los archivos permitidos, sin
comandos, git ni pruebas; el revisor solo lee el diff y el repo. El motor (no los agentes) hace git, pruebas e integración:
si el fallo está en el motor o en la cadena, va en ajustes_proceso, nunca como lección para un agente.

Lecciones actuales: {current}
Recibos de tareas recientes (intentos, pruebas, revisiones, errores): {receipts}
Tareas bloqueadas en el tablero: {blocked}
Propuestas que Owner descartó: {discarded}
Métricas de 7 días: {metrics}

Responde SOLO con un bloque ```json```:
{{"nuevas": [{{"para": "planificador|constructor|revisor|todos", "texto": "regla concreta en una o dos frases", "evidencia": ["t_..."]}}],
  "refuerzan": ["L.. que los hechos de hoy confirman"],
  "retirar": ["L.. que ya no aplican o se contradicen con lo visto"],
  "ajustes_proceso": [{{"titulo": "...", "porque": "...", "detalle": "cambio concreto en el motor del equipo (prompts, tiempos, pruebas, validación)"}}]}}
Como mucho 5 nuevas y 2 ajustes de proceso. Si no hay nada que aprender, devuelve listas vacías.
"""

def condensed(r):
    return {'tarea': r.get('tarea'), 'titulo': r.get('titulo'), 'estado': r.get('estado'), 'error': r.get('error'),
            'ciclos': [{'intento': c.get('intento'), 'fuera_de_alcance': c.get('fuera_de_alcance'),
                        'pruebas': [{'prueba': t.get('prueba') or 'prueba sin nombre', 'ok': t.get('ok'), 'salida': '' if t.get('ok') else (t.get('fallo') or '')[:600] if t.get('fallo') else (t.get('salida') or '')[-300:]} for t in c.get('pruebas', []) if isinstance(t, dict)],
                        'revision': c.get('revision')} for c in r.get('ciclos', [])]}

def retro(pid):
    marker = STATE / 'retros' / f'{today()}-{pid}.json'
    if marker.exists(): return load(marker, {})
    recent = receipts(pid, since_hours=36)
    signals = load(STATE / 'senales' / f'{pid}.json', {}).get('descartadas', [])
    fresh_discards = [d for d in signals if d['fecha'] >= (now() - timedelta(hours=36)).strftime('%Y-%m-%d')]
    ts = tasks(pid); replaced = superseded(ts)
    blocked = [{'id': t['id'], 'titulo': t['title'], 'motivo': motivo_visible(t)} for t in ts if t['status'] in {'blocked', 'triage'} and t['id'] not in replaced]
    if not recent and not fresh_discards:
        save(marker, {'omitida': 'sin actividad nueva'}); return {}
    log(pid, 'retrospectiva…')
    p = project(pid)
    path = worktree(pid, '_plan', start=git(p['repo'], 'rev-parse', f'equipo/{pid}'))
    prompt = RETRO_PROMPT.format(name=p['name'], current=json.dumps(lessons(pid), ensure_ascii=False),
                                 receipts=json.dumps([condensed(r) for r in recent][-12:], ensure_ascii=False)[:30000],
                                 blocked=json.dumps(blocked[-10:], ensure_ascii=False), discarded=json.dumps(fresh_discards, ensure_ascii=False),
                                 metrics=json.dumps(metrics(pid), ensure_ascii=False))
    answer = json_block(claude(prompt, path, LIMITS['plan_seconds'], out=STATE / 'retros' / f'{today()}-{pid}.raw.json', role='retro'))
    if not isinstance(answer, dict): raise Stop('retrospectiva_sin_formato')
    updated = merge_lessons(lessons(pid), answer)
    save(STATE / 'lecciones' / f'{pid}.json', updated)
    adjustments = [a for a in (answer.get('ajustes_proceso') or [])[:2] if isinstance(a, dict) and a.get('titulo')]
    motor = CONFIG.get('proyecto_motor')   # the project that holds this engine, if the owner develops it with the team
    if adjustments and motor in CONFIG['projects']:
        follow_up(motor, [f"[mejora del proceso del equipo, vista en {p['name']}] {a['titulo']}: {a.get('detalle', '')}" for a in adjustments])
    save(marker, {'nuevas': len(answer.get('nuevas') or []), 'refuerzan': answer.get('refuerzan') or [], 'retirar': answer.get('retirar') or [],
                  'ajustes_proceso': adjustments, 'lecciones': len(updated)})
    log(pid, f"aprendizaje: {len(answer.get('nuevas') or [])} nuevas, {len(answer.get('retirar') or [])} retiradas, {len(updated)} activas")
    return answer

# ---------- la mesa: departments talking to each other ----------
# A shared conversation per day (~/Equipo/state/mesa/<date>.json). Every morning the six departments meet in three
# rounds (open, reply, agreements); the real work also speaks there (reviewer notes, blocked tasks), and Owner can write
# to any department and gets an answer within one run cycle. Agreements feed each project's planner.

DEPARTMENTS = json.loads((HERE / 'departments.json').read_text())
MESA_TYPES = {'idea', 'pregunta', 'aviso', 'propuesta', 'respuesta', 'apoyo', 'objecion'}

def mesa_file(date=None): return STATE / 'mesa' / f'{date or today()}.json'
def mesa_load(date=None): return load(mesa_file(date), {'fecha': date or today(), 'mensajes': []})


def post(de, para, texto, tipo='aviso', proyecto='general', responde_a=None, origen='trabajo', propuesta=None):
    """Append one message to today's mesa. Departments, 'owner' and 'todos' are valid participants."""
    valid = set(DEPARTMENTS) | {'owner', 'todos'}
    para = [p for p in (para if isinstance(para, list) else [para]) if p in valid] or ['todos']
    if de not in valid or not str(texto).strip(): return None
    with open(STATE / 'mesa.lock', 'a') as fh:
        pf.lock(fh)
        mesa = mesa_load(); n = len(mesa['mensajes']) + 1
        msg = {'id': f'M{n}', 'de': de, 'para': para, 'tipo': tipo if tipo in MESA_TYPES else 'aviso',
               'proyecto': proyecto if proyecto in CONFIG['projects'] else 'general', 'texto': str(texto).strip()[:3500 if tipo == 'respuesta' and origen == 'owner' else 1200],
               'hora': now().strftime('%H:%M'), 'origen': origen}
        if responde_a: msg['responde_a'] = responde_a
        if isinstance(propuesta, dict) and propuesta.get('titulo'):
            msg['propuesta'] = {k: str(propuesta.get(k, ''))[:600] for k in ('titulo', 'porque', 'detalle')}
        mesa['mensajes'].append(msg); save(mesa_file(), mesa)
        return msg

def project_brief(pid):
    p = project(pid); ts = tasks(pid); replaced = superseded(ts)
    return {'proyecto': pid, 'nombre': p['name'], 'objetivo': p['goal'], 'metricas_7d': metrics(pid),
            'hechas': [t['title'] for t in ts if t['status'] == 'done'][-5:],
            'en_curso_o_cola': [t['title'] for t in ts if t['status'] in {'running', 'ready', 'todo'}][:6],
            'bloqueadas': [f"{t['title']}: {motivo_visible(t)[:160]}" for t in ts if t['status'] in {'blocked', 'triage'} and t['id'] not in replaced][-4:],
            'lecciones': [l['texto'] for l in lessons(pid)][:5],
            'experimentos': resultados(pid)[-3:],
            'lanzamientos_7d': experimentos(pid),
            'contenido_medido': contenido_medido(pid), 'aperturas_medidas': aperturas_medidas(pid),
            'nota_obsidian': (ficha(pid) or {}).get('texto', '')[:1500], 'tendencias': tendencias(pid, 1500),
            'analisis_contenido': analisis_contenido(pid, 1200), 'calendario_por_medir': calendario_por_medir(pid, solo_cifras=True),
            'sin_enlace': sin_enlace_calendario(pid),
            'para_rehacer': para_rehacer(pid, 900),
            'calendario_pendiente': calendario_pendiente(pid)}

def transcript(msgs, limit=60):
    return '\n'.join(f"{m['id']} [{m['hora']}] {DEPARTMENTS.get(m['de'], {}).get('nombre', m['de'])} → "
                     f"{', '.join(DEPARTMENTS.get(x, {}).get('nombre', x) for x in m['para'])} ({m['tipo']}, {m['proyecto']})"
                     f"{' re ' + m['responde_a'] if m.get('responde_a') else ''}: {m['texto']}"
                     + (f" | PROPUESTA: {m['propuesta']['titulo']}" if m.get('propuesta') else '') for m in msgs[-limit:]) or '(todavía nadie ha hablado)'

MESA_PROMPT = """Eres {nombre}, un departamento del equipo de agentes que trabaja para Owner. Tu especialidad: {foco}
REGLA DE OWNER (prioritaria): no propongas, construyas ni apruebes cambios hechos por motivos legales, éticos o de cumplimiento (quitar o suavizar cifras de premios, la palabra cashback, promesas, avisos, textos legales, marcar cifras como hipótesis). De eso se encarga Owner. Si ves un riesgo legal o ético, menciónaselo una vez a Owner en la mesa y no crees tareas para cambiarlo.
{validar}
{estrategia}
Departamentos de la mesa: {departamentos}.
Proyectos y su estado real: {proyectos}
Conversación de la mesa (hoy y ayer): 
{conversacion}

{turno}

Reglas: habla como un compañero de oficina, en español de España, breve y concreto, siempre sobre el estado real de los
proyectos (cita tareas, métricas o bloqueos). Dirígete a quien de verdad le toca. Nada de relleno ni de saludos. Las ideas
se ejecutan solo si Owner aprueba el lote: nunca propongas publicar, enviar mensajes, gastar, abrir cuentas ni operar.
Hechos del entorno: el código de cada proyecto está, en solo lectura, en <proyecto>/_plan dentro de tu carpeta actual
(una carpeta por proyecto). Cada uno es un checkout del git de su propio proyecto en la rama
equipo/<proyecto>; el motor integra solo los archivos aprobados de cada tarea y nunca hace push ni toca la carpeta
de trabajo de Owner. Tu carpeta actual NO es un repo: no saques conclusiones de git desde aquí.{web}
Responde SOLO con un bloque ```json```: lista de como mucho {maximo} mensajes
[{{"para": ["id de departamento", "owner" o "todos"], "proyecto": "{ids}" o "general", "tipo": "{tipos}",
   "texto": "...", "responde_a": "M.. opcional", "propuesta": {{"titulo": "...", "porque": "...", "detalle": "..."}} solo si tipo es propuesta}}]
Ids de departamento: {dept_ids}. Si no tienes nada útil que decir, devuelve [].
No escribas a Owner durante la reunión: si algo es para él, díselo a Operaciones (alfred) empezando por «Para Owner:»; ella
le lleva al cierre como mucho tres decisiones. Antes de avisar de algo, mira si otro departamento ya lo ha dicho hoy: si es así, no lo repitas.
"""
TURNS = {
    'abre': 'TU TURNO (ronda 1): desde tu especialidad, ¿qué ves que nadie está mirando? Comparte una idea, una pregunta a otro departamento, un aviso o una propuesta concreta de tarea.',
    'responde': 'TU TURNO (ronda 2): responde a lo que te han dicho o pedido a ti (o a todos). Apoya, objeta con motivo o mejora las propuestas; puedes hacer una propuesta nueva si nace de la conversación.',
}
CONTROL_TURN = ' Como Control, revisa además cada propuesta de la ronda 1: si tiene un riesgo de seguridad, datos, alcance o autorización, objétala citando su id; si es segura, no hace falta decir nada.'

SYNTH_PROMPT = """Eres Operaciones y cierras la mesa de hoy del equipo de agentes de Owner.
REGLA DE OWNER (prioritaria): no propongas, construyas ni apruebes cambios hechos por motivos legales, éticos o de cumplimiento (quitar o suavizar cifras de premios, la palabra cashback, promesas, avisos, textos legales, marcar cifras como hipótesis). De eso se encarga Owner. Si ves un riesgo legal o ético, menciónaselo una vez a Owner en la mesa y no crees tareas para cambiarlo.
{validar}
{estrategia}
Proyectos: {proyectos}
Conversación completa de hoy:
{conversacion}

Decide los acuerdos: propuestas con apoyo y sin una objeción de Control sin resolver, concretas y alineadas con el objetivo
de su proyecto. Prefiere las que validan con el mercado o acercan el primer cobro sobre las que solo mejoran el producto. Cada acuerdo pasará al planificador de su proyecto como idea prioritaria (Owner aprobará después el lote).
Recoge aparte lo que solo puede decidir Owner (dinero, cuentas, estrategia, publicar, dudas de alcance): como mucho 3, sin repetir
lo mismo con otras palabras, cada una en una frase que se responda con sí o no (si hay opciones, la que recomendáis va en la pregunta),
en español llano: Owner no programa, nada de nombres de archivos, comandos ni códigos internos.
Responde SOLO con un bloque ```json```:
{{"resumen": "tres frases para Owner sobre de qué se ha hablado", "acuerdos": [{{"proyecto": "{ids}", "titulo": "...", "porque": "...",
  "detalle": "...", "departamentos": ["..."], "mensajes": ["M.."]}}], "para_ti": [{{"de": "id de departamento", "texto": "pregunta o decisión"}}]}}
Como mucho 2 acuerdos por proyecto.
"""

PREGUNTAS_MAX = 3

def para_en_mesa(dept, para):
    """During the meeting what is for Owner goes to Operaciones, who closes with his decisions (19/10: 17 messages to
    Owner in one day, 13 of them the same warning from five departments)."""
    para = [p for p in (para if isinstance(para, list) else [para]) if p]
    if 'owner' not in para: return para
    rest = [p for p in para if p != 'owner']
    return rest or (['todos'] if dept == 'alfred' else ['alfred'])

def mandar_preguntas(preguntas):
    """Each decision for Owner as its own Telegram message: Sí / No / Más tarde, or Responder to write the answer."""
    for m in preguntas[:PREGUNTAS_MAX]:
        quien = DEPARTMENTS.get(m['de'], {}).get('nombre', m['de'])
        telegram_send(f"{quien} te pregunta: {m['texto']}", {'inline_keyboard': [
            [{'text': 'Sí', 'callback_data': f"q:{m['id']}:s"}, {'text': 'No', 'callback_data': f"q:{m['id']}:n"}, {'text': 'Más tarde', 'callback_data': f"q:{m['id']}:l"}],
            [{'text': 'Responder', 'callback_data': f"q:{m['id']}:r"}]]})

def mesa_call(dept, turno, msgs, briefs, maximo=3, role='mesa'):
    d = DEPARTMENTS[dept]
    prompt = MESA_PROMPT.format(validar=VALIDAR, estrategia=estrategia(corta=True), nombre=d['nombre'], foco=d['foco'] + (CONTROL_TURN if dept == 'control' and turno == 'responde' else ''),
                                departamentos='; '.join(f"{k} = {v['nombre']}: {v['foco'][:90]}" for k, v in DEPARTMENTS.items()),
                                proyectos=json.dumps(briefs, ensure_ascii=False)[:20000], conversacion=transcript(msgs),
                                turno=TURNS.get(turno, turno), maximo=maximo, ids='|'.join(CONFIG['projects']),
                                tipos='|'.join(sorted(MESA_TYPES)), dept_ids=', '.join(DEPARTMENTS),
                                web='\nComo Inteligencia puedes buscar y leer la web (solo lectura): cita siempre URL y fecha de consulta.' if dept == 'radar' else '')
    # Inteligencia researches with web search/fetch (read-only); everyone else only reads the code.
    tools = 'Read,Glob,Grep,WebSearch,WebFetch' if dept == 'radar' else None
    answer = json_block(claude(prompt, ROOT / 'worktrees', LIMITS['review_seconds'], out=STATE / 'mesa' / today() / f'{str(turno)[:8]}-{dept}.json', tools=tools, role=role))
    return answer if isinstance(answer, list) else []

def mesa(force=False):
    """The daily meeting: open (everyone), reply (everyone), agreements (Operaciones). Idempotent per day."""
    current = mesa_load()
    if current.get('reunion') and not force: return current
    for pid in CONFIG['projects']:            # fresh read-only checkouts for anyone who wants to look at the code
        worktree(pid, '_plan', start=git(project(pid)['repo'], 'rev-parse', f'equipo/{pid}'))
    briefs = [project_brief(pid) for pid in CONFIG['projects']] + [{'cartera_completa_tuyas': cartera_resumen(), 'oportunidades_de_fuera': oportunidades()}]
    history = lambda: mesa_load((now() - timedelta(days=1)).strftime('%Y-%m-%d'))['mensajes'][-20:] + mesa_load()['mensajes']
    for turno in ('abre', 'responde'):
        log('mesa:', turno)
        with concurrent.futures.ThreadPoolExecutor(3) as pool:
            futures = {pool.submit(mesa_call, dept, turno, history(), briefs): dept for dept in DEPARTMENTS}
            for future in concurrent.futures.as_completed(futures):
                dept = futures[future]
                try: messages = future.result()
                except Exception as e: log('mesa:', dept, 'no habló:', e); continue
                for m in messages[:3]:
                    if isinstance(m, dict): post(dept, para_en_mesa(dept, m.get('para')), m.get('texto', ''), m.get('tipo'), m.get('proyecto', 'general'), m.get('responde_a'), 'mesa', m.get('propuesta'))
    log('mesa: acuerdos')
    today_msgs = mesa_load()['mensajes']
    try:
        synth = json_block(claude(SYNTH_PROMPT.format(validar=VALIDAR, estrategia=estrategia(corta=True), proyectos=json.dumps(briefs, ensure_ascii=False)[:20000], conversacion=transcript(today_msgs, 120),
                                                      ids='|'.join(CONFIG['projects'])), ROOT / 'worktrees', LIMITS['review_seconds'], out=STATE / 'mesa' / today() / 'acuerdos.json', role='sintesis'))
    except Stop as e:
        synth = {'resumen': 'La mesa no pudo cerrar acuerdos: ' + str(e)[:200], 'acuerdos': [], 'para_ti': []}
    agreements = [a for a in synth.get('acuerdos', []) if isinstance(a, dict) and a.get('proyecto') in CONFIG['projects'] and a.get('titulo')]
    for a in agreements:
        follow_up(a['proyecto'], [f"[acuerdo de la mesa · {', '.join(DEPARTMENTS.get(d, {}).get('nombre', d) for d in a.get('departamentos', []))}] {a['titulo']}: {a.get('porque', '')} {a.get('detalle', '')}"[:900]])
    preguntas = []
    for q in synth.get('para_ti', [])[:PREGUNTAS_MAX]:
        if isinstance(q, dict) and q.get('de') in DEPARTMENTS and str(q.get('texto', '')).strip():
            m = post(q['de'], ['owner'], q.get('texto', ''), 'pregunta', 'general', origen='mesa')
            if m: preguntas.append(m)
    with open(STATE / 'mesa.lock', 'a') as fh:
        pf.lock(fh)
        final = mesa_load(); final['reunion'] = {'hora': now().strftime('%H:%M'), 'resumen': str(synth.get('resumen', ''))[:800], 'acuerdos': agreements}
        save(mesa_file(), final)
    if final['reunion']['resumen']: notify('Mesa', first_sentence(final['reunion']['resumen']) + ' · /mesa')
    try: mandar_preguntas(preguntas)
    except Exception as e: log('mesa: no he podido mandar las preguntas a Owner:', e)
    log(f'mesa: {len(final["mensajes"])} mensajes, {len(agreements)} acuerdos')
    return final

def answer_owner():
    """Any department Owner wrote to replies in the next run cycle (one short message each)."""
    msgs = mesa_load()['mensajes']
    answered = {m.get('responde_a') for m in msgs if m.get('responde_a')}
    pending = [m for m in msgs if m['de'] == 'owner' and m['id'] not in answered]
    if not pending: return
    briefs = [project_brief(pid) for pid in CONFIG['projects']] + [{'cartera_completa_tuyas': cartera_resumen(), 'oportunidades_de_fuera': oportunidades()}]
    for m in pending:
        for dept in [d for d in m['para'] if d in DEPARTMENTS][:2] or ['alfred']:
            turn = f"TU TURNO: Owner te ha escrito ({m['id']}): «{m['texto']}». Respóndele a él directamente (para: owner, responde_a: {m['id']}). Lo que Owner te pide ya está aprobado: si es una investigación o un análisis que puedes hacer sin publicar, enviar, gastar ni comprar, hazlo AHORA y dale el resultado (como mucho unas 25 líneas, con fuentes si has buscado en la web), sin pedirle permiso otra vez. Si es una pregunta simple, una o dos frases. Si hace falta otro departamento, dilo."
            try:
                for r in mesa_call(dept, turn, msgs, briefs, maximo=1, role='respuestas')[:1]:
                    post(dept, ['owner'], r.get('texto', ''), 'respuesta', r.get('proyecto', m.get('proyecto', 'general')), m['id'], 'owner', r.get('propuesta'))
            except Exception as e:
                post(dept, ['owner'], f'No he podido responder ahora ({str(e)[:120]}). Lo vemos en la próxima mesa.', 'respuesta', m.get('proyecto', 'general'), m['id'], 'owner')
    notify('El equipo te ha respondido', 'Tienes respuesta en la mesa del equipo.')

# ---------- status ----------

CHECK = {'running': 'trabajando', 'ready': 'en cola', 'todo': 'en cola', 'blocked': 'bloqueada', 'triage': 'bloqueada', 'done': 'hecha'}

def checklist(ts):
    """The current work in plain order: unfinished tasks plus what finished in the last 24 h, oldest first."""
    recent = time.time() - 86400
    replaced = superseded(ts)
    keep = [t for t in ts if t['id'] not in replaced and t['status'] in CHECK
            and (t['status'] != 'done' or (t.get('completed_at') or 0) >= recent)]
    return [{'id': t['id'], 'titulo': t['title'], 'estado': CHECK[t['status']],
             'hora': datetime.fromtimestamp(t['completed_at'], MADRID).strftime('%H:%M') if t['status'] == 'done' and t.get('completed_at') else ''}
            for t in sorted(keep, key=lambda t: t['created_at'])][-12:]

def por_pr(pid):
    """Norte (30/10): the team's work reaches production through the daily PR Owner approves (norte-pr-diaria), never
    through the nightly merge. 03/11: the nightly merge still merged it into the local production branch, which then
    could not be pushed and drifted from GitHub."""
    return project(pid).get('fusion') == 'pr'

def pending_merge(pid):
    """Team commits Owner has not merged into the base yet (merges that only bring the base in don't count)."""
    p = project(pid)
    if por_pr(pid): return 0
    try: return int(git(p['repo'], 'rev-list', '--count', '--no-merges', f"{p['base']}..equipo/{pid}") or 0)
    except Stop: return 0

def por_juntar(pid):
    """The team's commits Owner has not merged yet, oldest first: [(sha, subject)]."""
    p = project(pid)
    try: out = git(p['repo'], 'log', '--reverse', '--no-merges', '--format=%h %s', f"{p['base']}..equipo/{pid}")
    except Stop: return []
    return [tuple(l.split(' ', 1)) if ' ' in l else (l, '') for l in out.splitlines() if l.strip()]

# What each pending improvement is, in plain words (19/10: «no me dice qué mejoras son… para ir enterándome»).
# One Sonnet call for the projects whose pending commits changed; the lines are kept per project in state/mejoras.json.
MEJORAS_MAX = 5

def mejoras_vigentes(pid, commits=None):
    commits = por_juntar(pid) if commits is None else commits
    entry = load(STATE / 'mejoras.json', {}).get(pid) or {}
    return entry.get('lineas', []) if commits and entry.get('clave') == digest(' '.join(c for c, _ in commits)) else []

def mejoras_seguras(pid):
    try: return mejoras_vigentes(pid)
    except Exception: return []

def mejoras():
    """Refresh the plain description of what is waiting to be merged, only where it changed."""
    file = STATE / 'mejoras.json'; cache = load(file, {}); falta = {}
    for pid in CONFIG['projects']:
        commits = por_juntar(pid)
        if not commits: cache.pop(pid, None); continue
        if (cache.get(pid) or {}).get('clave') != digest(' '.join(c for c, _ in commits)): falta[pid] = commits
    if falta and not freno():
        material = {pid: [re.sub(r'^equipo:\s*', '', t) for _, t in cs][-30:] for pid, cs in falta.items()}
        prompt = ('Owner no programa. Son los cambios que su equipo de agentes ha terminado y revisado, y que esperan a que él diga si se juntan '
                  'a su proyecto. Explícale qué se ha hecho, por proyecto, para que se vaya enterando de lo que avanza.\n'
                  f'Como mucho {MEJORAS_MAX} líneas por proyecto (junta lo que va de lo mismo), cada una de menos de 90 caracteres, en español llano: '
                  'qué cambia para él o para sus clientes. Sin nombres de archivos, comandos, rutas, códigos internos ni siglas (nada de KC-R3, LOTE-1, '
                  'UTM, Caddyfile, track-report o promo_copy).\n'
                  'Responde SOLO con un bloque ```json``` con las mismas claves que te paso: {"<clave del proyecto>": ["línea", ...]}\n\n'
                  + json.dumps({pid: {'nombre': project(pid)['name'], 'cambios': cs} for pid, cs in material.items()}, ensure_ascii=False)[:20000])
        try: raw = json_block(claude(prompt, ROOT, 180, role='respuestas'))
        except Exception as e: raw = None; log('mejoras: no he podido explicarlas:', e)
        if isinstance(raw, dict):
            for pid, commits in falta.items():
                lineas = [' '.join(str(l).split())[:110] for l in (raw.get(pid) or raw.get(project(pid)['name']) or []) if str(l).strip()][:MEJORAS_MAX]
                if lineas: cache[pid] = {'clave': digest(' '.join(c for c, _ in commits)), 'lineas': lineas}
    save(file, cache)
    return {pid: (cache.get(pid) or {}).get('lineas', []) for pid in CONFIG['projects'] if pid in cache}

# ---------- the operations board: every piece of work with its status and who has it ----------
# Columns: para-ti (waits for Owner) · cola · curso · bloqueado · hecho. Owners are departments, 'owner' or 'hermes'.

def iso(ts): return datetime.fromtimestamp(ts, MADRID).isoformat(timespec='minutes') if ts else ''

_REACHABLE = {}

def reachable(repo, base):
    """Every commit in base, read once per process: status() asks about ~250 commits and one git call each took ~40 s."""
    key = (str(repo), base)
    if key not in _REACHABLE:
        out = subprocess.run(['git', '-C', str(repo), 'rev-list', base], capture_output=True, text=True, env=clean_env())
        _REACHABLE[key] = set(out.stdout.split()) if out.returncode == 0 else set()
    return _REACHABLE[key]

def merged(repo, base, sha):
    if not sha: return False
    commits = reachable(repo, base)
    return sha in commits or (len(sha) < 40 and any(c.startswith(sha) for c in commits))

def answered_by_owner(msgs):
    """Question ids Owner has answered: linked replies, plus the older ones that only said «Sobre M37: …» in the text."""
    out = set()
    for r in msgs:
        if r.get('de') != 'owner': continue
        if r.get('responde_a'): out.add(r['responde_a'])
        hit = re.match(r'\s*Sobre (M\d{1,6})\s*:', r.get('texto') or '')
        if hit: out.add(hit.group(1))
    return out

def tablero():
    items = []
    batch = load(STATE / 'proposals' / f'{today()}.json', {'proyectos': {}})
    registro = lanzamientos()
    for pid, p in CONFIG['projects'].items():
        lote = batch['proyectos'].get(pid, {})
        if lote.get('estado') == 'pendiente' and lote.get('tareas'):
            items.append({'id': f'lote-{pid}', 'fuente': 'lote', 'proyecto': pid, 'titulo': f"Aprobar el lote de hoy ({len(lote['tareas'])} {'tarea' if len(lote['tareas']) == 1 else 'tareas'})",
                          'detalle': ' · '.join(t['titulo'] for t in lote['tareas']), 'columna': 'para-ti', 'responsable': 'owner', 'propone': 'alfred', 'hora': batch.get('creado', '')})
        unmerged = []
        ts = tasks(pid); replaced = superseded(ts); con_resultado = medidos(pid)
        decisiones = {}   # task -> decision of its latest result that has one
        horas_resultado = {}   # task -> time of its latest result, even when missing
        ultimos_resultados = {}   # task -> latest result, including text and title
        for r in resultados(pid):
            if isinstance(r, dict) and r.get('tarea'): ultimos_resultados[r['tarea']] = r
            if isinstance(r, dict) and r.get('tarea'): horas_resultado[r['tarea']] = r.get('hora')
            if isinstance(r, dict) and r.get('tarea') and r.get('decision'): decisiones[r['tarea']] = r['decision']
        for t in ts:
            st = t['status']
            if st == 'archived' or t['id'] in replaced: continue
            folder = STATE / 'runs' / t['id']; receipt = load(folder / 'receipt.json', {}); fase = load(folder / 'fase.json', {})
            try: spec = json.loads(t['body'] or '{}')
            except ValueError: spec = {}
            cycles = receipt.get('ciclos', []); last = cycles[-1] if cycles else {}
            item = {'id': t['id'], 'fuente': 'equipo', 'proyecto': pid, 'titulo': t['title'], 'detalle': spec.get('porque', ''),
                    'archivos': spec.get('archivos', []), 'pruebas': [{'prueba': x.get('prueba') or 'prueba sin nombre', 'ok': bool(x.get('ok'))} for x in last.get('pruebas', []) if isinstance(x, dict)],
                    'revision': {k: (last.get('revision') or {}).get(k) for k in ('decision', 'motivos')} if last.get('revision') else None,
                    'intentos': len(cycles), 'commit': receipt.get('commit'), 'rama': f'equipo/{pid}', 'creada': iso(t.get('created_at')),
                    'origen': 'alfred' if spec.get('origen') == 'alfred' else 'lote'}
            ficha = ficha_lanzamiento(t)
            if ficha:
                item['lanzamiento'] = ficha
                viene_de = spec.get('viene_de')
                if isinstance(viene_de, str) and TAREA_ID.fullmatch(viene_de):
                    padre = ultimos_resultados.get(viene_de, {})
                    item['viene_de'] = {'tarea': viene_de, 'titulo': padre.get('titulo') or (registro.get(viene_de) or {}).get('titulo') or viene_de}
                    if padre.get('texto'): item['viene_de']['resultado'] = padre['texto'][:200]
                hora = (registro.get(t['id']) or {}).get('lanzado')
                if hora: item['lanzado'] = hora
                if t['id'] in con_resultado:
                    item['medido'] = True
                    if t['id'] in decisiones: item['decision'] = decisiones[t['id']]
            if st == 'running':
                f = fase.get('fase', 'construyendo')
                item.update(columna='curso', fase=f, intento=fase.get('intento', 1), responsable='control' if f == 'revisando' else 'product', hora=fase.get('desde', ''))
            elif st in ('ready', 'todo'):
                item.update(columna='cola', responsable='product', fase='esperando dependencia' if st == 'todo' else 'lista para empezar', hora=item['creada'])
            elif st in ('blocked', 'triage'):
                item.update(columna='bloqueado', responsable='alfred', fase='bloqueada', motivo=motivo_visible(t)[:400], hora=iso(t.get('completed_at') or t.get('created_at')))
                if fase.get('fase'): item['parada'] = fase['fase']
                if fase.get('intento'): item['intento'] = fase['intento']
            elif st == 'done':
                # A launched experiment with no result after 48 h comes back to Owner, however old it is; an unreadable date leaves it in 'hecho'.
                falta_medir = False
                if ficha and item.get('lanzado') and not item.get('medido'):
                    try: falta_medir = now() - datetime.fromisoformat(item['lanzado']) >= timedelta(hours=RECORDAR_MEDIR_HORAS)
                    except (ValueError, TypeError): pass
                falta_decidir = False
                if ficha and item.get('lanzado') and item.get('medido') and t['id'] not in decisiones:
                    try: falta_decidir = timedelta(0) <= now() - datetime.fromisoformat(horas_resultado.get(t['id'])) <= timedelta(days=7)
                    except (ValueError, TypeError): pass
                # A finished experiment nobody has launched or discarded is Owner's decision, however old it is.
                descartado = ficha and not item.get('lanzado') and (registro.get(t['id']) or {}).get('descartado')
                pendiente = ficha and not item.get('lanzado') and not descartado
                if not falta_medir and not falta_decidir and not pendiente and (t.get('completed_at') or 0) < now().timestamp() - 7 * 86400: continue
                # Only a task the team built (its receipt has a commit) waits for a merge; a record or note is just done.
                commit = receipt.get('commit')
                done_in = merged(p['repo'], p['base'], commit) if commit else True
                item.update(columna='hecho', responsable='product', fase=('fusionada' if commit else 'hecha') if done_in else 'por fusionar', fusionada=done_in, hora=iso(t.get('completed_at')))
                # A finished experiment nobody has launched yet is Owner's decision: it waits in his tray, not in 'hecho'.
                if pendiente: item.update(columna='para-ti', responsable='owner', fase='lista para lanzar' if done_in else FASE_FALTA_FUSIONAR)
                elif descartado: item.update(fase='descartado', descartado=descartado, descarte=(registro.get(t['id']) or {}).get('motivo', ''))   # 'motivo' is the block reason
                elif falta_medir: item.update(columna='para-ti', responsable='owner', fase='falta medir')
                elif falta_decidir: item.update(columna='para-ti', responsable='owner', fase='falta decidir')
                if not done_in: unmerged.append(t['title'])
            else: continue
            items.append(item)
        if unmerged:
            items.append({'id': f'fusion-{pid}', 'fuente': 'fusion', 'proyecto': pid, 'titulo': f"Fusionar {len(unmerged)} {'cambio' if len(unmerged) == 1 else 'cambios'} del equipo en {p['base']}",
                          'detalle': unmerged, 'rama': f'equipo/{pid}', 'base': p['base'], 'columna': 'para-ti', 'responsable': 'owner', 'propone': 'control', 'fase': 'revisado por Control', 'hora': ''})
    m = mesa_load()
    for msg in m['mensajes']:
        if 'owner' in msg.get('para', []) and msg.get('de') != 'owner' and msg.get('tipo') == 'pregunta' \
                and msg['id'] not in answered_by_owner(m['mensajes']):
            items.append({'id': msg['id'], 'fuente': 'mesa', 'proyecto': msg.get('proyecto', 'general'), 'titulo': msg['texto'][:140], 'detalle': msg['texto'],
                          'columna': 'para-ti', 'responsable': 'owner', 'propone': msg['de'], 'fase': 'te pregunta', 'hora': msg.get('hora', '')})
    for b in hermes_overview().get('tableros', []):
        for col, key in (('curso', 'en_curso'), ('para-ti', 'revision'), ('bloqueado', 'bloqueadas')):
            for title in b.get(key, []):
                items.append({'id': f"h-{b['slug']}-{hashlib.sha1(title.encode()).hexdigest()[:8]}", 'fuente': 'hermes', 'proyecto': b['slug'], 'titulo': title,
                              'detalle': f"Tablero de Hermes «{b['nombre']}»", 'columna': col, 'responsable': 'owner' if col == 'para-ti' else 'hermes', 'fase': {'curso': 'en curso', 'para-ti': 'en revisión', 'bloqueado': 'bloqueada'}[col], 'hora': ''})
    return items

def modelos_hoy():
    """Who builds today, in one line for Entregas and Alfred: Claude only, or Codex from its date (and why not, if it failed)."""
    rule, down = MODEL_POLICY.get('codex') or {}, codex_outage()
    if not rule.get('desde'): return {'codex': 'sin configurar'}
    if clock() < rule['desde']: return {'codex': 'desde ' + rule['desde']}
    if codex_waiting(down): return {'codex': 'fuera hasta ' + down['hasta'], 'motivo': down.get('motivo')}
    return {'codex': 'activo', 'roles': rule.get('roles', [])}

def codex_probar():
    """A tiny read-only Codex call with the light model: clears today's outage if it answers."""
    names = MODEL_POLICY.get('modelos', {}); light = (MODEL_POLICY.get('dificultad_codex') or {}).get('baja', {})
    model = names.get(light.get('modelo'))
    if not model: return {'ok': False, 'motivo': 'sin modelo de Codex en modelos.json'}
    try: answer = codex_run('Responde solo: OK', ROOT, 120, model, 'low', role='prueba')
    except Stop as e: return {'ok': False, 'modelo': model, 'motivo': str(e)[:300]}
    with CODEX_LOCK: (STATE / 'codex.json').unlink(missing_ok=True)
    return {'ok': True, 'modelo': model, 'respuesta': answer.strip()[:80], **modelos_hoy()}

def status():
    batch = load(STATE / 'proposals' / f'{today()}.json', {'proyectos': {}})
    out = {'fecha': today(), 'pausado': (ROOT / 'PAUSED').exists(), 'proyectos': {}}
    for pid, p in CONFIG['projects'].items():
        ts = tasks(pid); replaced = superseded(ts)
        by = lambda s: [{'id': t['id'], 'titulo': t['title']} for t in ts if t['status'] == s]
        out['proyectos'][pid] = {
            'nombre': p['name'], 'rama': f'equipo/{pid}',
            'lote': batch['proyectos'].get(pid, {'estado': 'sin lote'}),
            'trabajando': by('running'), 'pendientes': by('ready') + by('todo'),
            'hechas': [{'id': t['id'], 'titulo': t['title'], 'resultado': (t.get('result') or '')[:200]} for t in ts if t['status'] == 'done'][-10:],
            'bloqueadas': [{'id': t['id'], 'titulo': t['title'], 'motivo': motivo_visible(t)[:200],
                            'desde': iso(t.get('completed_at') or t.get('created_at'))} for t in ts if t['status'] in {'blocked', 'triage'} and t['id'] not in replaced],
            'aprendizaje': {'lecciones': lessons(pid), 'metricas': metrics(pid)},
            'tareas': checklist(ts),
            'por_fusionar': pending_merge(pid), 'mejoras': mejoras_seguras(pid),
            'experimentos': resultados(pid)[-5:],
            'lanzamientos_7d': experimentos(pid),
            'cifras_calendario': cifras_calendario(pid),
            'atribucion_calendario': atribucion_calendario(pid),
        }
    out['hermes'] = hermes_overview() if HERMES else None
    out['modelos'] = modelos_hoy()
    out['salud'] = load(SALUD, None)
    out['tablero'] = tablero()
    out['para_ti'] = para_ti_vigente(out['tablero'])
    m = mesa_load(); out['mesa'] = {'reunion': m.get('reunion'), 'mensajes': m['mensajes'][-60:], 'departamentos': {k: v['nombre'] for k, v in DEPARTMENTS.items()}}
    return out

# ---------- Obsidian: the team's state as a generated note, so the vault (and Hermes) see it ----------

def write_estado():
    """04-Systems/Estado-generado/equipo.md, rewritten only when something changed. Never touches Owner's notes."""
    target = VAULT / '04-Systems/Estado-generado/equipo.md'
    if not target.parent.is_dir(): return False
    s = status(); lines = []
    for pid, p in s['proyectos'].items():
        lines += [f"## {p['nombre']}", '', f"- Lote del {s['fecha']}: {p['lote'].get('estado', 'sin lote')} · rama `{p['rama']}` (Owner fusiona a main)"]
        lines += [f"- Trabajando: {t['titulo']}" for t in p['trabajando']]
        if p['pendientes']: lines.append(f"- En cola: {len(p['pendientes'])}")
        lines += [f"- Hecha: {t['titulo']}" for t in p['hechas'][-5:]]
        lines += [f"- Bloqueada: {t['titulo']} — {t['motivo'][:160]}" for t in p['bloqueadas']]
        lines.append('')
    m = s.get('mesa') or {}; r = m.get('reunion') or {}
    preguntas = [x for x in m.get('mensajes', []) if 'owner' in x.get('para', [])]
    lines += ['## Mesa del equipo', '', r.get('resumen') or 'Sin reunión hoy.', '']
    lines += [f"- Acuerdo ({a.get('proyecto')}): {a.get('titulo')}" for a in r.get('acuerdos', [])]
    if preguntas: lines.append(f"- Mensajes para Owner: {len(preguntas)} (en Entregas o /mesa en Telegram)")
    body = '\n'.join(lines).strip() + '\n'
    old = target.read_text() if target.exists() else ''
    if body in old: return False
    head = f"# Equipo de agentes · estado generado\n\nGenerado por equipo.py el {now().strftime('%d/%m/%Y %H:%M')}. No editar: se reescribe solo. El contexto de cada proyecto vive en 01-Projects/Cartera.\n\n"
    target.write_text(head + body)
    return True

# ---------- Hermes: the rest of Owner's boards and scheduled jobs, read-only ----------

def hermes_overview():
    """Hermes' own boards (outside the team's) and its scheduled jobs, so Owner can see everything from one place."""
    import sqlite3
    out = {'tableros': [], 'programadas': [], 'reloj': None}
    if not HERMES: return out
    try:
        for b in kb.list_boards(include_archived=False):
            if b['slug'].startswith('equipo-'): continue
            try:
                conn = sqlite3.connect(f"file:{b['db_path']}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
                try: ts = kb.list_tasks(conn)
                finally: conn.close()
            except Exception: continue
            if not ts: continue
            count = {}
            for t in ts: count[t.status] = count.get(t.status, 0) + 1
            pick = lambda st: [t.title for t in ts if t.status in st][:5]
            out['tableros'].append({'slug': b['slug'], 'nombre': b['name'], 'estados': count,
                                    'revision': pick({'review'}), 'bloqueadas': pick({'blocked'}), 'en_curso': pick({'running'})})
    except Exception as e:
        out['error'] = type(e).__name__
    cron = Path.home() / '.hermes/cron'
    jobs = load(cron / 'jobs.json', {}).get('jobs', [])
    for j in jobs if isinstance(jobs, list) else []:
        out['programadas'].append({'nombre': j.get('name'), 'cada': (j.get('schedule') or {}).get('display'), 'activa': bool(j.get('enabled')),
                                   'ultima': j.get('last_run_at'), 'estado': j.get('last_status'), 'siguiente': j.get('next_run_at')})
    try: out['reloj'] = datetime.fromtimestamp(float((cron / "ticker_last_success").read_text().strip()), MADRID).isoformat(timespec='minutes')
    except Exception: pass
    return out

# ---------- nightly merge ----------
# Owner (05/10/2026): every night the team's branch goes into his base branch and to GitHub, if the project's tests are
# no worse than on the base. Backup ref first; fast-forward only, so his own uncommitted work is never overwritten.

FAIL_LINE = re.compile(r'^(?:✖|not ok|FAIL\b)\s*(.+?)(?:\s+\(\d[\d.]*m?s\))?$')

def failures(pid, path, folder, tag):
    names, ok = set(), True
    for name in project(pid)['tests']:
        res = run_tests(pid, {'pruebas': [name]}, path, folder / tag, None)[0]
        if not res['ok']:
            ok = False
            log_file = folder / tag / f'test-{name}-0.log'
            text = log_file.read_text(errors='ignore') if log_file.exists() else res['salida']
            names |= {f'{name}: ' + m.group(1).strip() for m in map(FAIL_LINE.match, text.splitlines()) if m} or {f'{name}: (falla)'}
    return ok, names

def fusionar_project(pid):
    p = project(pid); repo = p['repo']; base = p['base']; team = f'equipo/{pid}'
    if por_pr(pid): return 'nada que fusionar: lo junta la PR diaria'
    if not pending_merge(pid): return 'nada que fusionar'
    lock = ROOT / 'locks'; lock.mkdir(parents=True, exist_ok=True)
    with open(lock / f'{pid}.lock', 'w') as fh:
        try: pf.lock(fh, block=False)
        except BlockingIOError: return 'ocupado: se fusionará la próxima noche'
        folder = STATE / 'fusiones' / today() / pid
        base_sha = git(repo, 'rev-parse', base)
        path = worktree(pid, '_fusion', start=base_sha)
        try:
            base_ok, base_fail = failures(pid, path, folder, 'base')
            merged = subprocess.run(['git', '-C', str(path), '-c', 'user.name=Equipo', '-c', 'user.email=equipo@localhost',
                                     'merge', '--no-ff', '-q', '-m', f'equipo: fusión nocturna de {team} ({today()})', team],
                                    capture_output=True, text=True, env=clean_env())
            if merged.returncode:
                try:
                    diff = subprocess.run(['git', '-C', str(path), 'diff', '--name-only', '--diff-filter=U'],
                                          check=False, capture_output=True, text=True, env=clean_env())
                    archivos = [line for line in diff.stdout.splitlines() if line.strip()] if diff.returncode == 0 else []
                except (OSError, subprocess.SubprocessError):
                    archivos = []
                return 'conflicto: necesita a Owner o a Hermes' + lista_conflictos(archivos)
            new_ok, new_fail = failures(pid, path, folder, 'fusion')
            if not new_ok and not new_fail <= base_fail:
                return 'pruebas peores que en la base: ' + '; '.join(sorted(new_fail - base_fail))[:300]
            sha = git(path, 'rev-parse', 'HEAD')
        finally:
            git(repo, 'worktree', 'remove', '--force', str(path), check=False)
        git(repo, 'branch', '-f', f'backup/pre-fusion-{today()}', base_sha)
        checked_out = git(repo, 'rev-parse', '--abbrev-ref', 'HEAD', check=False) == base
        if checked_out:
            ff = subprocess.run(['git', '-C', str(repo), 'merge', '--ff-only', '-q', sha], capture_output=True, text=True, env=clean_env())
            if ff.returncode: return 'no he podido avanzar tu carpeta de trabajo sin tocar tus cambios: ' + ff.stderr.strip()[:200]
        else:
            git(repo, 'update-ref', f'refs/heads/{base}', sha, base_sha)
        pushed = subprocess.run(['git', '-C', str(repo), 'push', '-q', 'origin', base], capture_output=True, text=True, env=clean_env(), timeout=120)
        extra = '' if not base_fail else f' (siguen {len(base_fail)} fallos que ya estaban)'
        return f'fusionada en {base}' + (' y subida a GitHub' if pushed.returncode == 0 else ' (sin subir: ' + pushed.stderr.strip()[:120] + ')') + extra

def fusionar(pids):
    out = {}; lineas = {}
    for pid in pids:
        try: lineas[pid] = mejoras_vigentes(pid)
        except Exception: lineas[pid] = []
        try: out[pid] = fusionar_project(pid)
        except Exception as e: out[pid] = 'error: ' + str(e)[:200]
        log(pid, out[pid])
    try:
        registro = lanzamientos(); changed = False
        for pid, r in out.items():
            if not r.startswith('fusionada'): continue
            for entry in registro.values():
                if isinstance(entry, dict) and entry.get('proyecto') == pid and entry.get('sin_fusionar') and not entry.get('fusionado'):
                    entry['fusionado'] = now().isoformat(timespec='seconds')
                    changed = True
        if changed: save(STATE / 'experimentos.json', registro)
    except Exception as e:
        log('equipo', 'No se han podido actualizar los experimentos tras fusionar:', str(e))
    save(STATE / 'fusiones' / 'ultima.json', {'fecha': now().isoformat(timespec='seconds'), 'resultados': out})
    hechos = {pid: r for pid, r in out.items() if r != 'nada que fusionar'}
    if hechos:
        texto = [f"{project(pid)['name']}: {r}" + ''.join('\n• ' + l for l in lineas.get(pid, []) if r.startswith('fusionada')) for pid, r in hechos.items()]
        for pid, r in hechos.items():
            if not r.startswith('fusionada'): continue
            try:
                e = experimentos(pid)
                pendientes = e['por_lanzar']
                if pendientes:
                    titulos = ', '.join(f"«{x['titulo']}»" for x in pendientes[:5])
                    restantes = e.get('por_lanzar_total', len(pendientes)) - len(pendientes[:5])
                    if restantes > 0: titulos += f' y {restantes} más'
                    texto.append(f"{project(pid)['name']}: ya en la versión principal, listo para lanzar: {titulos} (/lote)")
            except Exception as e:
                log(pid, 'No se han podido leer los experimentos tras fusionar:', str(e))
        notify('Mejoras del equipo', '\n'.join(texto))
    return out

def stay_awake():
    """Keep the Mac from idle-sleeping while this process works (released when it exits). On 6 Oct the 7:30 plan crept
    along for five hours in Power Nap slivers. A closed lid still wins: that needs Owner to leave it open on power."""
    pf.keep_awake(system=True)

def parser():
    ap = argparse.ArgumentParser()
    ap.add_argument('cmd', choices=['setup', 'fusionar', 'plan', 'reintentar', 'retro', 'mesa', 'mensaje', 'proposals', 'approve', 'run', 'run-project', 'status', 'pause', 'resume', 'estado', 'cartera', 'codex', 'salud', 'encargar', 'resultado', 'decidir', 'lanzado', 'descartar', 'desbloquear', 'archivar'])
    ap.add_argument('project', nargs='?')
    ap.add_argument('--tarea', help="JSON de la tarea, o '-' para leerlo de la entrada"); ap.add_argument('--texto'); ap.add_argument('--experimento', help='id de la tarea del experimento'); ap.add_argument('--responde-a', dest='responde_a'); ap.add_argument('--indice', type=int); ap.add_argument('--date'); ap.add_argument('--only', help='índices separados por comas'); ap.add_argument('--ids', help='ids de tarea separados por comas'); ap.add_argument('--limpio', action='store_true'); ap.add_argument('--via', choices=list(VIAS_LANZADO), help='desde dónde se marca el lanzamiento'); ap.add_argument('--decision', choices=list(DECISIONES) + list(DECIDIR), help='con lo medido: seguir|cambiar|parar; en un lote (con --indice): si|no|luego')
    return ap

def main():
    a = parser().parse_args()
    pids = [a.project] if a.project else list(CONFIG['projects'])
    def on_term(*_):
        for child in list(CHILDREN): pf.kill_now(child)
        sys.exit(1)
    signal.signal(signal.SIGTERM, on_term)
    if a.cmd in {'plan', 'run', 'run-project', 'fusionar', 'reintentar', 'retro', 'mesa'}: stay_awake()
    try:
        if a.cmd == 'setup': setup()
        elif a.cmd == 'mesa': mesa(force=True)
        elif a.cmd == 'fusionar': print(json.dumps(fusionar(pids), ensure_ascii=False, indent=1))
        elif a.cmd == 'mensaje': print(json.dumps(post('owner', [a.project or 'todos'], a.texto or '', 'pregunta', origen='owner',
                                                       responde_a=a.responde_a if a.responde_a and re.fullmatch(r'M\d{1,6}', a.responde_a) else None), ensure_ascii=False))
        elif a.cmd == 'retro': print(json.dumps({pid: retro(pid) for pid in pids}, ensure_ascii=False, indent=1))
        elif a.cmd == 'plan': print(json.dumps(plan(pids), ensure_ascii=False, indent=1))
        elif a.cmd == 'proposals': print(json.dumps(load(STATE / 'proposals' / f'{a.date or today()}.json', {}), ensure_ascii=False, indent=1))
        elif a.cmd == 'approve': approve(pids, a.date, [int(x) for x in a.only.split(',')] if a.only else None)
        elif a.cmd == 'run': run_all()
        elif a.cmd == 'run-project': print(run_project(a.project))
        elif a.cmd == 'status': print(json.dumps(con_nombre_todo(status()), ensure_ascii=False, indent=1))
        elif a.cmd in ('desbloquear', 'archivar'):
            ids = [x for x in (a.ids or '').split(',') if ID_TABLERO.fullmatch(x)]
            if a.project not in CONFIG['projects'] or not ids: raise Stop(f'usa: equipo.py {a.cmd} <proyecto> --ids t_xxxxxxxx,t_yyyyyyyy')
            (ROOT / 'locks').mkdir(parents=True, exist_ok=True)
            with open(ROOT / 'locks' / f'{a.project}.lock', 'w') as fh:
                pf.lock(fh)
                done = desbloquear(a.project, ids, a.limpio) if a.cmd == 'desbloquear' else archivar(a.project, ids)
            print(json.dumps(done, ensure_ascii=False, indent=1))
        elif a.cmd == 'reintentar':
            # Same project lock as run-project, so a relaunch never overlaps a build or the automatic retry.
            (ROOT / 'locks').mkdir(parents=True, exist_ok=True)
            for pid in pids:
                with open(ROOT / 'locks' / f'{pid}.lock', 'w') as fh:
                    pf.lock(fh)
                    print(pid, retry_round(pid) or 'nada que relanzar')
        elif a.cmd == 'estado': print('escrito' if write_estado() else 'sin cambios')
        elif a.cmd == 'cartera': print(json.dumps(cartera_resumen(), ensure_ascii=False, indent=1))
        elif a.cmd == 'encargar':
            raw = sys.stdin.read() if a.tarea == '-' else (a.tarea or '')
            try: t = json.loads(raw)
            except ValueError: raise Stop('--tarea no es JSON válido')
            if not isinstance(t, dict): raise Stop('--tarea debe ser un objeto JSON')
            print(json.dumps(encargar(a.project, t), ensure_ascii=False, indent=1))
        elif a.cmd == 'resultado': print(json.dumps(registrar_resultado(a.project, a.texto, a.experimento, a.decision), ensure_ascii=False, indent=1))
        elif a.cmd == 'decidir':
            # a task of today's batch (Sí / No / Más tarde) or what to do with a measured experiment (seguir / cambiar / parar)
            if a.indice is not None:
                if a.project not in CONFIG['projects'] or a.decision not in DECIDIR: raise Stop('usa: equipo.py decidir <proyecto> --indice N --decision si|no|luego')
                print(json.dumps(decidir_lote(a.project, a.indice, a.decision), ensure_ascii=False))
            else:
                if not a.project or a.decision not in DECISIONES: raise Stop('uso: equipo.py decidir <id de tarea> --decision seguir|cambiar|parar')
                print(json.dumps(decidir(a.project, a.decision), ensure_ascii=False, indent=1))
        elif a.cmd == 'lanzado':
            if not a.project: raise Stop('uso: equipo.py lanzado <id de tarea>')
            print(json.dumps(lanzado(a.project, a.via), ensure_ascii=False, indent=1))
        elif a.cmd == 'descartar':
            if not a.project: raise Stop('uso: equipo.py descartar <id de tarea> --texto <motivo>')
            print(json.dumps(descartar(a.project, a.texto), ensure_ascii=False, indent=1))
        elif a.cmd == 'salud': print(json.dumps(salud(send=a.project != 'silencio'), ensure_ascii=False, indent=1))
        elif a.cmd == 'codex': print(json.dumps(codex_probar() if a.project == 'probar' else {**modelos_hoy(), 'politica': MODEL_POLICY.get('codex')}, ensure_ascii=False, indent=1))
        elif a.cmd == 'pause': (ROOT / 'PAUSED').write_text(now().isoformat()); log('pausado')
        elif a.cmd == 'resume': (ROOT / 'PAUSED').unlink(missing_ok=True); log('reanudado')
    except Stop as e:
        log('parado:', e); sys.exit(2)

if __name__ == '__main__':
    main()
