"""Adaptadores de Hermes.

Real: usa los motores que ya existen, sin modificarlos.
- equipo/ (solo se importa): proyectos, estado de tareas, freno, `validate_task`, `claude()` y `codex_run()` (con su registro de
  consumo) y `encargar()`: el mismo camino que un encargo de Owner a Alfred (constructor y revisor de distinto proveedor,
  pruebas, integración en equipo/<pid>, nunca main).
- sem-meta-ads: lee su base de datos para medir y crea planes de campaña SOLO en ensayo (nunca `--ejecutar`).
- landings: lee las solicitudes guardadas (TbT) para medir.
- vault: deja ahí la investigación y los borradores de contenido.
- capturas: arranca la rama del equipo en una copia aparte, la abre con Chrome sin ventana y saca capturas.

Falso: para la demo en seco y las pruebas. No llama a modelos ni toca nada; todo avanza solo y de forma predecible.
"""
import itertools, json, os, re, shutil, signal, socket, sqlite3, statistics, subprocess, sys, tempfile, time
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAMPOS = ('titulo', 'porque', 'detalle', 'archivos', 'pruebas', 'dificultad', 'lanzamiento', 'importante', 'motivo_importante')
CHROME = os.environ.get('HERMES_CHROME') or '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
FUENTES = {
    'anuncios_leads': 'Leads de Meta Ads del proyecto en los últimos N días (sem-meta-ads)',
    'anuncios_coste_lead': 'Coste por lead de Meta Ads del proyecto en los últimos N días, en € (sem-meta-ads)',
    'solicitudes_tbt': 'Solicitudes recibidas en la landing de TbT en los últimos N días',
    'equipo_integradas': 'Tareas integradas por el equipo en el proyecto en los últimos N días',
}


def _categoria(archivos):
    if any(re.search(r'\.(tsx|jsx|css|html|swift)$', f) for f in archivos): return 'interfaz'
    if archivos and all(re.search(r'(^|/)(test|tests)/|\.test\.|test_', f) for f in archivos): return 'pruebas'
    if any(f.endswith('.py') for f in archivos): return 'motor'
    return 'lógica'


def _vendor(modelo): return 'Codex' if str(modelo or '').startswith('gpt-') else 'Claude'


def _slug(t): return re.sub(r'[^a-z0-9]+', '-', t.lower().encode('ascii', 'ignore').decode())[:50].strip('-') or 'nota'


class AdaptadorReal:
    def __init__(self, sem_dir=None, tbt_dir=None, estado_dir=None):
        sys.path.insert(0, str(HERE.parent / 'equipo'))
        import equipo as eq            # importar es solo lectura: el motor no arranca nada al importarse
        self.eq = eq
        self.cwd = Path(eq.project(eq.CONFIG.get('proyecto_motor') or 'prompted-os')['repo'])
        self.sem = Path(sem_dir or os.environ.get('HERMES_SEM_DIR') or Path.home() / 'work' / 'sem-meta-ads')
        self.tbt = Path(tbt_dir or os.environ.get('HERMES_TBT_DIR') or Path.home() / 'work' / 'time-based-trading')
        self.estado_dir = Path(estado_dir or os.environ.get('HERMES_STATE') or eq.STATE / 'hermes')
        self.demos = json.loads((HERE / 'demos.json').read_text()) if (HERE / 'demos.json').exists() else {}

    # --- equipo ---
    def proyectos(self):
        eq = self.eq
        return {pid: {'nombre': p['name'], 'objetivo': p.get('goal', ''), 'pruebas': list(p.get('tests', {})),
                      'max_archivos': eq.LIMITS['max_files']} for pid, p in eq.CONFIG['projects'].items()}

    def normalizar(self, pid, t):
        eq = self.eq
        try: return eq.validate_task(pid, {**{k: t[k] for k in CAMPOS if k in t}, 'depende_de': []}, 0, root=Path(eq.project(pid)['repo']))
        except AssertionError as e: raise ValueError(f'falta o sobra «{e}»')

    def freno(self):
        if (self.eq.ROOT / 'PAUSED').exists(): return 'el equipo está en pausa'
        return self.eq.freno()

    def planificar(self, prompt):
        eq = self.eq
        return eq.json_block(eq.claude(prompt, self.cwd, eq.LIMITS['plan_seconds'], role='planificador'))

    arbitrar = planificar           # el árbitro es Claude (Opus, rol planificador): decide entre su plan y el de Codex

    def contraproponer(self, prompt):
        """Codex lee el repositorio y propone su propia ronda. Sin Codex, se sigue sin debate (se dice en el chat)."""
        eq = self.eq
        if not eq.codex_ready(): return {'resumen': 'Codex no está disponible ahora; ronda sin su contrapropuesta.'}
        modelo = (eq.MODEL_POLICY.get('modelos') or {}).get('codex', 'gpt-6-astra')
        try: return eq.json_block(eq.codex_run(prompt, self.cwd, eq.LIMITS['review_seconds'], modelo, 'medium', write=False, role='hermes-contra'))
        except eq.Stop as e: return {'resumen': f'Codex no pudo proponer ({e}).'}

    def encargar(self, pid, t, por):
        return self.eq.encargar(pid, {k: t[k] for k in CAMPOS if k in t and k not in ('importante', 'motivo_importante')}, by=por)

    def avisar(self, texto, botones=None):
        self.eq.telegram(texto, botones)  # el canal del motor: WhatsApp si el puente está conectado (botones → encuesta), si no Telegram

    def estado(self, pid, tid):
        for t in self.eq.tasks(pid):
            if t['id'] == tid:
                return {'estado': t['status'], 'resultado': t.get('result') or self.eq.motivo_visible(t)}
        return {'estado': 'archived', 'resultado': 'La tarea ya no está en el tablero.'}

    # --- métricas ---
    def fuentes(self): return FUENTES

    def medir(self, met):
        dias = int(met.get('dias') or 7); desde = (datetime.now() - timedelta(days=dias)).strftime('%Y-%m-%d'); pid = met.get('proyecto')
        f = met.get('fuente')
        if f in ('anuncios_leads', 'anuncios_coste_lead'):
            db = self.sem / 'data' / 'ads.db'
            if not db.exists(): return {'valor': None, 'detalle': 'sem-meta-ads aún no tiene datos (falta conectar la cuenta de Meta).'}
            with sqlite3.connect(f'file:{db}?mode=ro', uri=True) as c:
                g, l = c.execute('SELECT COALESCE(SUM(gasto),0), COALESCE(SUM(leads),0) FROM metricas_dia WHERE proyecto=? AND fecha>=?', (pid, desde)).fetchone()
            if f == 'anuncios_leads': return {'valor': float(l), 'detalle': f'{l} leads y {g:.2f} € en {dias} días.'}
            return {'valor': round(g / l, 2) if l else None, 'detalle': f'{g:.2f} € y {l} leads en {dias} días.'}
        if f == 'solicitudes_tbt':
            p = self.tbt / 'data' / 'solicitudes.jsonl'
            n = 0
            if p.exists():
                for line in p.read_text().splitlines():
                    try: n += json.loads(line).get('fecha', '') >= desde
                    except ValueError: pass
            return {'valor': float(n), 'detalle': f'{n} solicitudes en {dias} días.'}
        if f == 'equipo_integradas':
            n = sum(1 for r in self._recibos(dias) if r.get('proyecto') == pid and r.get('estado') == 'integrada')
            return {'valor': float(n), 'detalle': f'{n} tareas integradas en {dias} días.'}
        return None

    def _recibos(self, dias):
        desde = (datetime.now() - timedelta(days=dias)).isoformat()
        for f in (self.eq.STATE / 'runs').glob('t_*/receipt.json'):
            try: r = json.loads(f.read_text())
            except (ValueError, OSError): continue
            if (r.get('fin') or r.get('inicio') or '') >= desde: yield r

    # --- estimación y ranking (con el consumo y los recibos reales) ---
    def estimar(self, tareas):
        cache = self.estado_dir / 'coste-medio.json'
        hoy = datetime.now().strftime('%Y-%m-%d')
        m = json.loads(cache.read_text()) if cache.exists() else {}
        if m.get('fecha') != hoy:
            por_rol = {}
            for f in sorted((self.eq.STATE / 'consumo').glob('*.jsonl'))[-14:]:
                for line in f.read_text().splitlines():
                    try: x = json.loads(line)
                    except ValueError: continue
                    if isinstance(x.get('coste_usd'), (int, float)): por_rol.setdefault(x.get('rol'), []).append(x['coste_usd'])
            minutos = [(datetime.fromisoformat(r['fin']) - datetime.fromisoformat(r['inicio'])).total_seconds() / 60
                       for r in self._recibos(14) if r.get('fin') and r.get('inicio')]
            med = lambda k: statistics.median(por_rol[k]) if por_rol.get(k) else 0.3
            m = {'fecha': hoy, 'equipo_usd': med('constructor') + med('revisor'), 'otro_usd': med('sintesis') or 0.15,
                 'equipo_min': statistics.median(minutos) if minutos else 12}
            cache.parent.mkdir(parents=True, exist_ok=True); cache.write_text(json.dumps(m))
        eqs = sum(1 for t in tareas if t['motor'] == 'equipo')
        usd = eqs * m['equipo_usd'] + (len(tareas) - eqs) * m['otro_usd']
        # El equipo hace una tarea a la vez por proyecto: los minutos se suman por proyecto.
        por_pid = {}
        for t in tareas: por_pid[t.get('proyecto')] = por_pid.get(t.get('proyecto'), 0) + (m['equipo_min'] if t['motor'] == 'equipo' else 4)
        return {'usd': round(usd, 2), 'minutos': int(max(por_pid.values() or [0]) + 5)}

    def ranking(self, dias=30):
        """Quién integra a la primera cada tipo de trabajo (Codex o Claude), con los recibos reales del equipo."""
        cache = self.estado_dir / 'ranking.json'
        hoy = datetime.now().strftime('%Y-%m-%d')
        if cache.exists():
            r = json.loads(cache.read_text())
            if r.get('fecha') == hoy: return r
        tabla = {}
        for r in self._recibos(dias):
            if not r.get('ciclos'): continue
            repo = self.eq.CONFIG['projects'].get(r.get('proyecto'), {}).get('repo')
            archivos = []
            if repo and r.get('commit'):
                out = subprocess.run(['git', '-C', repo, 'show', '--name-only', '--format=', r['commit']], capture_output=True, text=True, timeout=10)
                archivos = [x for x in out.stdout.splitlines() if x.strip()]
            cat, quien = _categoria(archivos), _vendor(r['ciclos'][0].get('modelo'))
            e = tabla.setdefault(cat, {}).setdefault(quien, {'n': 0, 'primera': 0})
            e['n'] += 1; e['primera'] += int(r.get('estado') == 'integrada' and len(r['ciclos']) == 1)
        out = {'fecha': hoy, 'dias': dias, 'tabla': tabla}
        cache.parent.mkdir(parents=True, exist_ok=True); cache.write_text(json.dumps(out, ensure_ascii=False))
        return out

    # --- motores sin tablero ---
    def ejecutar(self, motor, t, o):
        nota_dir = Path(self.eq.VAULT) / '01-Projects' / 'Hermes' / o['id']
        if motor == 'anuncios':
            planes = self.sem / 'planes'; planes.mkdir(parents=True, exist_ok=True)
            f = planes / f'hermes-{o["id"]}-{_slug(t["titulo"])}.json'; f.write_text(json.dumps(t['plan'], ensure_ascii=False, indent=1))
            p = subprocess.run(['node', 'scripts/crear.mjs', str(f)], cwd=self.sem, capture_output=True, text=True, timeout=60)   # nunca --ejecutar
            return {'ok': p.returncode == 0, 'resultado': (p.stdout or p.stderr)[-1200:] + f'\nPlan en ensayo: {f}', 'ruta': str(f), 'llamadas': 0}
        eq = self.eq
        if motor == 'investigacion':
            prompt = (f'Investiga en la web, solo leyendo, para el objetivo de Owner «{o["objetivo"]}»: {t["detalle"]}\n'
                      'Responde en español de España, en Markdown: primero 3 conclusiones accionables, luego los datos con sus fuentes (enlaces). Nada inventado.')
            texto = eq.claude(prompt, self.cwd, 900, role='sintesis', tools='Read,Glob,Grep,WebSearch,WebFetch')
        else:
            ficha = self.proyectos().get(t.get('proyecto'), {})
            prompt = (f'Escribe borradores para el proyecto {ficha.get("nombre", t.get("proyecto"))} ({ficha.get("objetivo", "")[:500]}). '
                      f'Objetivo de Owner: «{o["objetivo"]}». Encargo: {t["detalle"]}\n'
                      'Español de España, directo, sin promesas de dinero. Markdown. Son borradores: nadie los publica sin Owner.')
            texto = eq.claude(prompt, self.cwd, 600, role='sintesis')
        nota_dir.mkdir(parents=True, exist_ok=True)
        f = nota_dir / f'{motor}-{_slug(t["titulo"])}.md'
        f.write_text(f'# {t["titulo"]}\n\nObjetivo: {o["objetivo"]}\n\n{texto}\n')
        return {'ok': bool(texto.strip()), 'resultado': texto.strip()[:600] + f'\n\nEn el vault: {f}', 'ruta': str(f), 'llamadas': 1}

    # --- demostrar: criterios contra el diff y capturas de la rama del equipo ---
    def verificar(self, o, r):
        eq = self.eq; out = {'tareas': {}, 'capturas': [], 'llamadas': 0}
        for t in r['tareas']:
            if t['motor'] != 'equipo' or t.get('estado') != 'done' or not t.get('criterios'): continue
            sha = re.search(r'\(([0-9a-f]{7,40})\)', t.get('resultado') or '')
            repo = eq.CONFIG['projects'][t['proyecto']]['repo']
            if not sha: continue
            diff = subprocess.run(['git', '-C', repo, 'show', '--stat', '--patch', sha.group(1)], capture_output=True, text=True, timeout=20).stdout[:18000]
            prompt = (f'Eres el verificador de Hermes: no construiste ni revisaste esto. Tarea: «{t["titulo"]}». {t.get("detalle", "")}\n'
                      f'Comprueba cada criterio de aceptación SOLO con este diff (y leyendo el repositorio si hace falta):\n{json.dumps(t["criterios"], ensure_ascii=False)}\n'
                      f'Diff:\n{diff}\nResponde solo con JSON: {{"criterios": [{{"criterio": "…", "ok": true|false, "nota": "una frase"}}]}}')
            try:
                v = eq.json_block(eq.claude(prompt, repo, 420, role='retro')); out['llamadas'] += 1
                out['tareas'][t['titulo']] = {'criterios': [c for c in v.get('criterios', []) if isinstance(c, dict)][:4]}
            except eq.Stop as e: out['tareas'][t['titulo']] = {'error': str(e)[:200]}
        pids = {t['proyecto'] for t in r['tareas'] if t['motor'] == 'equipo' and t.get('estado') == 'done'}
        for pid in pids:
            if pid in self.demos: out['capturas'] += self.capturar(o['id'], r['n'], pid)
        return out

    def capturar(self, oid, n, pid):
        """La rama equipo/<pid> en una copia aparte, su servidor en un puerto libre y Chrome sin ventana: escritorio y móvil."""
        cfg = self.demos[pid]; repo = self.eq.CONFIG['projects'][pid]['repo']
        destino = self.estado_dir / 'capturas' / oid; destino.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=f'hermes-{pid}-'))
        with socket.socket() as s: s.bind(('127.0.0.1', 0)); puerto = s.getsockname()[1]
        hechas, proc = [], None
        try:
            subprocess.run(['git', '-C', repo, 'worktree', 'add', '--detach', str(tmp / 'w'), f'equipo/{pid}'], check=True, capture_output=True, timeout=60)
            cmd = [x.replace('{puerto}', str(puerto)) for x in cfg['arrancar']]
            proc = subprocess.Popen(cmd, cwd=tmp / 'w', stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            for _ in range(int(cfg.get('espera', 15)) * 2):
                try: socket.create_connection(('127.0.0.1', puerto), 0.5).close(); break
                except OSError: time.sleep(0.5)
            for i, ruta in enumerate(cfg.get('rutas', ['/'])):
                for nombre, tam in (('escritorio', '1440,900'), ('movil', '390,844')):
                    f = destino / f'r{n}-{pid}-{i}-{nombre}.png'
                    subprocess.run([CHROME, '--headless=new', '--hide-scrollbars', '--disable-gpu', f'--window-size={tam}', '--virtual-time-budget=5000',
                                    f'--screenshot={f}', f'http://127.0.0.1:{puerto}{ruta}'], capture_output=True, timeout=60)
                    if f.exists() and f.stat().st_size > 2000: hechas.append(f.name)
        except (subprocess.SubprocessError, OSError, KeyError): pass
        finally:
            if proc:
                try: os.killpg(proc.pid, signal.SIGTERM)
                except OSError: pass
            subprocess.run(['git', '-C', repo, 'worktree', 'remove', '--force', str(tmp / 'w')], capture_output=True)
            shutil.rmtree(tmp, ignore_errors=True)
        return hechas


class AdaptadorFalso:
    """Planificador con guion, Codex de mentira y un equipo que termina cada tarea tras `pasos` consultas."""
    PROYECTOS = {
        'prompted-os': {'nombre': 'Owner OS', 'objetivo': 'El sistema operativo de Owner.', 'pruebas': ['app', 'equipo'], 'max_archivos': 8},
        'beta': {'nombre': 'Beta', 'objetivo': 'Comparador de cuentas de fondeo.', 'pruebas': ['unitarias'], 'max_archivos': 8},
    }

    def __init__(self, guion=None, pasos=2, frenado=None, fallan=(), contrato=None, contras=None, arbitrajes=None, medidas=None, criterios_fallan=(), limpiar=False):
        # Sin guion propio (demo y chat en seco) el plan sale de la fase en que está el objetivo: sobrevive a reinicios.
        self.libre = guion is None
        self.guion = list(guion or [])
        self.contrato = contrato or {'exito': 'Que funcione y Owner lo vea.', 'metrica': None, 'presupuesto_llamadas': 30}
        self.contras, self.arbitrajes, self.medidas = list(contras or []), list(arbitrajes or []), list(medidas or [])
        self.pasos, self.frenado, self.fallan, self.criterios_fallan = pasos, frenado, set(fallan), set(criterios_fallan)
        self.encargos, self.prompts, self.avisos, self.ejecutados, self._vistas, self._ids_vivos = [], [], [], [], {}, set()
        self._ids = itertools.count(int.from_bytes(os.urandom(3), 'big'))
        if self.libre and limpiar: self._HECHAS.unlink(missing_ok=True)      # la demo de terminal empieza de cero; el chat en seco no

    def proyectos(self): return self.PROYECTOS
    def fuentes(self): return {**FUENTES, 'demo_conversiones': 'Clics en ofertas que acaban en alta (demo)'}
    def freno(self): return self.frenado

    def normalizar(self, pid, t):
        if not isinstance(t.get('titulo'), str) or len(t['titulo']) < 4: raise ValueError('falta o sobra «titulo»')
        if not isinstance(t.get('detalle'), str) or len(t['detalle']) < 20: raise ValueError('falta o sobra «detalle»')
        if not t.get('archivos') or len(t['archivos']) > self.PROYECTOS[pid]['max_archivos']: raise ValueError('falta o sobra «archivos»')
        if not t.get('pruebas') or any(x not in self.PROYECTOS[pid]['pruebas'] for x in t['pruebas']): raise ValueError('falta o sobra «pruebas»')
        out = {k: t[k] for k in ('titulo', 'porque', 'detalle', 'archivos', 'pruebas') if k in t}
        out['dificultad'] = t.get('dificultad') if t.get('dificultad') in ('baja', 'media', 'alta') else 'media'
        if any(f.startswith('equipo/') for f in t['archivos']): out['importante'] = 'Toca el motor del equipo'
        return out

    @staticmethod
    def _rondas(prompt):
        m = re.search(r'LO HECHO HASTA AHORA[^:]*: (\{.*\})\n', prompt)
        try: return sum(1 for r in json.loads(m.group(1))['rondas'] if r['estado'] != 'descartada') if m else 0
        except ValueError: return 0

    def planificar(self, prompt):
        self.prompts.append(prompt)
        if 'Propón el contrato' in prompt: return CONTRATO_DEMO if self.libre else self.contrato
        if self.libre:
            n = self._rondas(prompt)
            return GUION_DEMO[n] if n < len(GUION_DEMO) else {'hecho': True, 'resumen': 'Nada más que hacer.'}
        return self.guion.pop(0) if self.guion else {'hecho': True, 'resumen': 'Nada más que hacer.'}

    def contraproponer(self, prompt):
        self.prompts.append(prompt)
        if self.libre: return CONTRA_DEMO[0] if 'Medir' in prompt and 'src/eventos.js' not in prompt else CONTRA_DEMO[1]
        return self.contras.pop(0) if self.contras else {'resumen': 'De acuerdo con el plan.'}

    def arbitrar(self, prompt):
        self.prompts.append(prompt)
        if self.libre: return {'elegido': 'codex', 'diferencia': 'Codex comparte el formato del evento entre el clic y el informe; así no se rompen el uno al otro. Me quedo con la suya.',
                               'resumen': 'Medir los clics con un formato común y saber qué empresas pagan más.'}
        return self.arbitrajes.pop(0) if self.arbitrajes else {'elegido': 'claude', 'diferencia': 'Las dos van en la misma línea.'}

    def encargar(self, pid, t, por):
        if t['titulo'] in self.fallan: raise RuntimeError('la tarea no es válida')
        tid = f't_{next(self._ids):08x}'; self._ids_vivos.add(tid)
        self.encargos.append({'proyecto': pid, 'tid': tid, 'titulo': t['titulo'], 'por': por})
        return {'tarea': tid, 'constructor': 'gpt-6-astra'}

    def avisar(self, texto, botones=None): self.avisos.append({'texto': texto, 'botones': botones})

    def estado(self, pid, tid):
        n = self._vistas[tid] = self._vistas.get(tid, 0) + 1
        if self.libre and tid not in self._ids_vivos: n = self.pasos      # tras un reinicio, lo ya encargado da por hecho
        if n < self.pasos: return {'estado': 'running', 'resultado': ''}
        if self.libre: self._apuntar_hecha(tid)
        return {'estado': 'done', 'resultado': 'Integrada en equipo/' + pid + ' (a1b2c3d4). Revisión: correcto.'}

    # En seco, lo «integrado» se guarda en un archivo temporal para que la métrica de la demo sobreviva a reiniciar el chat.
    _HECHAS = Path(tempfile.gettempdir()) / 'hermes-seco-hechas.json'

    def _apuntar_hecha(self, tid):
        try: h = set(json.loads(self._HECHAS.read_text()))
        except (OSError, ValueError): h = set()
        h.add(tid); self._HECHAS.write_text(json.dumps(sorted(h)))

    def medir(self, met):
        if self.medidas: return self.medidas.pop(0)
        if not self.libre: return None
        try: hechas = len(json.loads(self._HECHAS.read_text()))
        except (OSError, ValueError): hechas = 0
        return {'valor': float(min(hechas, 3)), 'detalle': f'{min(hechas, 3)} altas atribuidas al comparador (demo).'}

    def estimar(self, tareas): return {'usd': round(0.42 * sum(t['motor'] == 'equipo' for t in tareas) + 0.12 * sum(t['motor'] != 'equipo' for t in tareas), 2), 'minutos': 14}

    def ejecutar(self, motor, t, o):
        self.ejecutados.append((motor, t['titulo']))
        if motor == 'anuncios': return {'ok': True, 'resultado': '[ensayo] campaña TF_LEADS_ES_FRIO · PAUSED\nNo se ha enviado nada a Meta.', 'llamadas': 0}
        return {'ok': True, 'resultado': {'investigacion': 'FTMO y Topstep pagan la comisión más alta (20 %); Apex la más baja. Fuentes en la nota.',
                                          'contenido': '3 guiones: «La cuenta que más paga no es la más barata», «Ranking real de octubre», «Lo que nadie te dice del reset».'}[motor],
                'ruta': f'vault/Hermes/{o["id"]}/{motor}.md', 'llamadas': 1}

    def verificar(self, o, r):
        out = {'tareas': {}, 'capturas': [], 'llamadas': 0}
        for t in r['tareas']:
            if t['motor'] == 'equipo' and t.get('estado') == 'done' and t.get('criterios'):
                out['tareas'][t['titulo']] = {'criterios': [{'criterio': c, 'ok': c not in self.criterios_fallan, 'nota': 'Comprobado en el diff.' if c not in self.criterios_fallan else 'No aparece en el diff.'} for c in t['criterios']]}
                out['llamadas'] += 1
        return out

    def ranking(self, dias=30):
        return {'fecha': 'demo', 'dias': dias, 'tabla': {'interfaz': {'Claude': {'n': 41, 'primera': 31}, 'Codex': {'n': 58, 'primera': 35}},
                                                         'lógica': {'Claude': {'n': 22, 'primera': 15}, 'Codex': {'n': 96, 'primera': 77}},
                                                         'motor': {'Claude': {'n': 30, 'primera': 24}, 'Codex': {'n': 19, 'primera': 11}}}}


CONTRATO_DEMO = {'exito': 'Saber qué empresas del comparador traen altas y tenerlas arriba; hecho cuando haya 3 altas atribuidas en 7 días.',
                 'metrica': {'fuente': 'demo_conversiones', 'proyecto': 'beta', 'nombre': 'altas atribuidas al comparador', 'objetivo': 3, 'sentido': '>=', 'dias': 7},
                 'plazo_dias': 14, 'presupuesto_llamadas': 30, 'no_tocar': ['backend/pagos']}
_T1 = {'motor': 'equipo', 'proyecto': 'beta', 'titulo': 'Eventos de clic en las ofertas del comparador', 'porque': 'Sin medir no sabemos qué empresa convierte.',
       'detalle': 'Añade un evento propio al pulsar «Ver oferta» con empresa, posición en la tabla y origen (utm_source).',
       'archivos': ['src/comparador.js', 'test/eventos.test.js'], 'pruebas': ['unitarias'], 'dificultad': 'baja',
       'criterios': ['Pulsar «Ver oferta» registra empresa, posición y origen', 'La prueba nueva pasa']}
_T2 = {'motor': 'equipo', 'proyecto': 'beta', 'titulo': 'Informe diario de clics por empresa', 'porque': 'Owner decide qué empresas destacar.',
       'detalle': 'Script que agrega los eventos del día por empresa y origen y escribe una tabla en el informe.',
       'archivos': ['scripts/informe-clics.js', 'test/informe.test.js'], 'pruebas': ['unitarias'], 'dificultad': 'media',
       'criterios': ['El informe agrupa por empresa y origen']}
_INV = {'motor': 'investigacion', 'titulo': 'Qué empresas de fondeo pagan más comisión', 'pregunta': 'Comisión de afiliado de FTMO, Topstep, Apex y FundedNext para cuentas de futuros, con fuente.'}
GUION_DEMO = [
    {'resumen': 'Medir de dónde llegan las visitas del comparador y qué empresa paga más.', 'tareas': [_T1, _T2, _INV]},
    {'resumen': 'Ordenar el comparador por conversión real y preparar contenido con el ranking.', 'tareas': [
        {'motor': 'equipo', 'proyecto': 'beta', 'titulo': 'Orden por defecto según clics de 7 días', 'porque': 'Las que más convierten, arriba.',
         'detalle': 'Usa el informe de clics de los últimos 7 días para el orden por defecto, con desempate por precio.',
         'archivos': ['src/comparador.js', 'test/orden.test.js'], 'pruebas': ['unitarias'], 'dificultad': 'baja', 'criterios': ['El orden por defecto sigue los clics de 7 días']},
        {'motor': 'contenido', 'proyecto': 'beta', 'titulo': 'Tres guiones de reel con el ranking real', 'encargo': 'Tres guiones cortos para Instagram de TF con el ranking de conversión, gancho en la primera frase.'}]},
]
CONTRA_DEMO = [
    {'resumen': 'Igual, pero con un formato de evento común para clic e informe.', 'problemas_del_plan': ['el informe y el clic definirían el evento dos veces'],
     'tareas': [{**_T1, 'archivos': ['src/comparador.js', 'src/eventos.js', 'test/eventos.test.js']}, {**_T2, 'archivos': ['scripts/informe-clics.js', 'src/eventos.js', 'test/informe.test.js']}, _INV]},
    {'resumen': 'De acuerdo: toca solo el comparador y su prueba.'},
]
