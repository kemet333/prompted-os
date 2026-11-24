"""Hermes supremo: el bucle que lleva un objetivo de Owner hasta el final coordinando a Claude, Codex y todos los motores.

Lógica pura: no llama a modelos ni al equipo directamente; todo pasa por un «adaptador» (adaptador.py: el real sobre
equipo/, sem-meta-ads y el vault; el falso para la demo y las pruebas). Así el bucle se prueba sin gastar uso del plan.

Ciclo de un objetivo:
  contrato ─ Claude propone cómo sabremos que está hecho (métrica de negocio), presupuesto y qué no se toca → Owner lo fija
  planificando ─ Claude planifica una ronda · Codex contrapropone la suya · un árbitro decide y explica la diferencia
  esperando_aprobacion ─ Owner aprueba (chat, WhatsApp, voz) · o se aprueba sola si Owner subió la confianza del proyecto
  en_marcha ─ cada tarea a su motor: equipo (código: construye uno, revisa el otro, integra en equipo/<pid>), anuncios
               (sem-meta-ads, solo ensayo), investigación (web, solo lectura) o contenido (borradores al vault)
  verificando ─ un tercer agente comprueba los criterios de aceptación contra el diff y se hacen capturas de la web
  → se mide la métrica del contrato → hecho, o planificando con todo lo aprendido
Paradas: necesita_owner (pregunta, tope, estancamiento, presupuesto), parado (Owner), freno del motor.
Turno de noche: trabaja de madrugada sin despertar a Owner y le deja un resumen por la mañana.

Nunca: aprobar por Owner sin que él haya subido la confianza, tocar pantallas protegidas, lanzar, publicar, activar
anuncios, gastar, push, main ni despliegues.
"""
import fcntl, functools, json, os, re, uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

MADRID = ZoneInfo('Europe/Madrid')
ESTADOS = ('contrato', 'esperando_contrato', 'planificando', 'esperando_aprobacion', 'en_marcha', 'necesita_owner', 'hecho', 'parado')
ACTIVOS = ('contrato', 'planificando', 'en_marcha')
TERMINAL_TAREA = {'done', 'blocked', 'triage', 'archived', 'error'}
MOTORES = ('equipo', 'anuncios', 'investigacion', 'contenido')
# Pantallas protegidas de Alfred (CLAUDE.md): Hermes no propone tareas que las toquen.
PROTEGIDAS = ('alfred-os/app/focus-console.tsx', 'alfred-os/app/orb.tsx', 'alfred-os/app/orb.css')
TOPES = {'rondas_por_objetivo': 8, 'rondas_por_dia': 6, 'tareas_por_ronda': 3, 'llamadas_por_dia': 40, 'llamadas_por_objetivo': 30}
NOCHE = {'desde': '00:00', 'hasta': '07:30', 'resumen': '08:25'}
# Confianza: lo que se aprueba solo en cada nivel. Owner fija el máximo por proyecto; el nivel real se gana con historial.
NIVELES = {0: (), 1: ('baja',), 2: ('baja', 'media')}
RACHA_PARA_SUBIR = 3


class Parada(Exception):
    """Algo que Owner tiene que ver: se muestra tal cual en el chat."""


def exclusivo(f):
    """El chat, los botones de WhatsApp y el tick pueden tocar el mismo objetivo: una operación a la vez (bloqueo de archivo)."""
    @functools.wraps(f)
    def envuelta(self, *a, **k):
        with self._bloqueo(): return f(self, *a, **k)
    return envuelta


def ahora(): return datetime.now(MADRID)
def hoy(): return ahora().strftime('%Y-%m-%d')
def hora(): return ahora().isoformat(timespec='seconds')
def _hhmm(s): h, m = s.split(':'); return int(h) * 60 + int(m)
def plural(n, s, p=None): return f'{n} {s if n == 1 else (p or s + "s")}'


def cumple(metrica, valor):
    if valor is None or not metrica: return False
    obj = float(metrica['objetivo'])
    return valor <= obj if metrica.get('sentido') == '<=' else valor >= obj


class Hermes:
    def __init__(self, adaptador, carpeta=None, topes=None, noche=None, reloj=None):
        self.a = adaptador
        self.dir = Path(carpeta or os.environ.get('HERMES_STATE') or Path.home() / 'Equipo' / 'state' / 'hermes')
        self.dir.mkdir(parents=True, exist_ok=True)
        self.topes = {**TOPES, **(topes or {})}
        self.noche = {**NOCHE, **(noche or {})}
        self.reloj = reloj or ahora            # las pruebas fijan la hora para el turno de noche

    @contextmanager
    def _bloqueo(self):
        if getattr(self, '_dentro', False): yield; return
        with open(self.dir / '.lock', 'w') as fh:
            fcntl.flock(fh, fcntl.LOCK_EX); self._dentro = True
            try: yield
            finally: self._dentro = False; fcntl.flock(fh, fcntl.LOCK_UN)

    # ---------- almacenamiento ----------
    def _ruta(self, oid):
        if not re.fullmatch(r'h_[0-9a-f]{8}', oid or ''): raise Parada('objetivo desconocido')
        return self.dir / f'{oid}.json'

    def cargar(self, oid):
        p = self._ruta(oid)
        if not p.exists(): raise Parada('objetivo desconocido')
        return json.loads(p.read_text())

    def guardar(self, o):
        o['actualizado'] = hora()
        tmp = self._ruta(o['id']).with_suffix('.tmp')
        tmp.write_text(json.dumps(o, ensure_ascii=False, indent=1)); tmp.replace(self._ruta(o['id']))
        return o

    def todos(self):
        out = []
        for f in sorted(self.dir.glob('h_*.json')):
            try: out.append(json.loads(f.read_text()))
            except ValueError: continue
        return sorted(out, key=lambda o: o.get('creado', ''), reverse=True)

    def _leer(self, nombre, defecto):
        f = self.dir / nombre
        try: return json.loads(f.read_text()) if f.exists() else defecto
        except ValueError: return defecto

    def _escribir(self, nombre, valor): (self.dir / nombre).write_text(json.dumps(valor, ensure_ascii=False, indent=1))

    def uso(self): return self._leer(f'uso-{hoy()}.json', {'fecha': hoy(), 'llamadas': 0, 'rondas': 0})

    def _llamada(self, o, n=1):
        u = self.uso(); u['llamadas'] = u.get('llamadas', 0) + n; self._escribir(f'uso-{hoy()}.json', u)
        o['llamadas'] = o.get('llamadas', 0) + n

    def _ronda_contada(self):
        u = self.uso(); u['rondas'] = u.get('rondas', 0) + 1; self._escribir(f'uso-{hoy()}.json', u)

    @staticmethod
    def _decir(o, de, texto):
        o.setdefault('mensajes', []).append({'de': de, 'texto': texto, 'hora': hora()})

    # ---------- confianza por proyecto ----------
    def confianza(self): return self._leer('confianza.json', {})

    @exclusivo
    def fijar_confianza(self, pid, maximo):
        if pid not in self.a.proyectos(): raise Parada(f'Proyecto desconocido: {pid}')
        if maximo not in NIVELES: raise Parada('El nivel va de 0 a 2.')
        c = self.confianza(); e = c.setdefault(pid, {'ganado': 0, 'racha': 0})
        e['maximo'] = maximo; self._escribir('confianza.json', c)
        return c

    def nivel(self, pid):
        e = self.confianza().get(pid, {})
        return min(e.get('maximo', 0), e.get('ganado', 0))

    def _historial_confianza(self, pid, bien):
        """Rondas de equipo aprobadas por Owner y bien terminadas suben el nivel ganado; un fallo o un descarte lo baja a 0."""
        c = self.confianza(); e = c.setdefault(pid, {'ganado': 0, 'racha': 0, 'maximo': 0})
        if bien:
            e['racha'] = e.get('racha', 0) + 1
            if e['racha'] >= RACHA_PARA_SUBIR and e.get('ganado', 0) < 2: e['ganado'] = e.get('ganado', 0) + 1; e['racha'] = 0
        else: e['ganado'] = 0; e['racha'] = 0
        self._escribir('confianza.json', c)

    def _se_aprueba_sola(self, r):
        tareas = r['tareas']
        if not tareas: return None
        pids = {t['proyecto'] for t in tareas if t.get('proyecto')}
        for t in tareas:
            if t.get('importante') or t['motor'] == 'anuncios': return None
            if t['motor'] == 'equipo' and t.get('dificultad') not in NIVELES[self.nivel(t['proyecto'])]: return None
            if t['motor'] != 'equipo' and not pids: return None
        if not pids or any(self.nivel(p) == 0 for p in pids): return None
        return min(self.nivel(p) for p in pids)

    # ---------- avisos y turno de noche ----------
    def es_noche(self):
        m = self.reloj().hour * 60 + self.reloj().minute
        a, b = _hhmm(self.noche['desde']), _hhmm(self.noche['hasta'])
        return a <= m < b if a < b else (m >= a or m < b)

    def _avisar(self, o, texto, botones=None):
        """Al móvil solo cuando Hermes necesita a Owner. De noche no despierta: se guarda para el resumen de la mañana."""
        linea = f'{o["objetivo"][:60]}: {texto}'
        if self.es_noche():
            cola = self._leer('noche.json', []); cola.append({'id': o['id'], 'texto': linea, 'hora': hora()}); self._escribir('noche.json', cola)
            return
        try: self.a.avisar(f'Hermes · {linea}', botones)
        except Exception: pass

    @staticmethod
    def botones_ronda(o):
        r = o['rondas'][-1]; imp = any(t.get('importante') for t in r['tareas'])
        fila = [{'text': 'Aprobar' + (' (revisadas)' if imp else ''), 'callback_data': f'hm:{o["id"]}:{"i" if imp else "a"}'},
                {'text': 'Descartar', 'callback_data': f'hm:{o["id"]}:d'}]
        return [fila, [{'text': 'Parar el objetivo', 'callback_data': f'hm:{o["id"]}:p'}]]

    def _resumen_manana(self):
        """Pasada la hora del resumen, un solo mensaje con lo que pasó de noche y lo que espera a Owner."""
        if self.es_noche() or self._leer('resumen.json', {}).get('fecha') == hoy(): return None
        m = self.reloj().hour * 60 + self.reloj().minute
        if m < _hhmm(self.noche['resumen']): return None
        cola = self._leer('noche.json', [])
        esperan = [o for o in self.todos() if o['estado'] in ('esperando_aprobacion', 'esperando_contrato', 'necesita_owner')]
        self._escribir('resumen.json', {'fecha': hoy()})
        if not cola and not esperan: return None
        lineas = ['Mientras dormías:'] + [f'• {c["texto"]}' for c in cola[-8:]]
        if esperan: lineas += ['', 'Te espera:'] + [f'• {o["objetivo"][:60]}: ' + {'esperando_aprobacion': 'aprobar la ronda',
                     'esperando_contrato': 'fijar cómo medimos el éxito', 'necesita_owner': (o.get('pregunta') or '')[:120]}[o['estado']] for o in esperan]
        botones = self.botones_ronda(esperan[0]) if esperan and esperan[0]['estado'] == 'esperando_aprobacion' else None
        try: self.a.avisar('Hermes · ' + '\n'.join(lineas), botones)
        except Exception: pass
        self._escribir('noche.json', [])
        return '\n'.join(lineas)

    # ---------- órdenes de Owner (chat, botones de WhatsApp, voz) ----------
    @exclusivo
    def nuevo(self, objetivo, proyecto=None):
        objetivo = (objetivo or '').strip()
        if len(objetivo) < 8: raise Parada('Escribe el objetivo con un poco más de detalle.')
        if proyecto and proyecto not in self.a.proyectos(): raise Parada(f'Proyecto desconocido: {proyecto}')
        o = {'id': 'h_' + uuid.uuid4().hex[:8], 'objetivo': objetivo[:2000], 'proyecto': proyecto, 'estado': 'contrato',
             'creado': hora(), 'rondas': [], 'mensajes': [], 'medidas': [], 'llamadas': 0}
        self._decir(o, 'owner', objetivo)
        self._decir(o, 'hermes', 'Recibido. Antes de nada te propongo cómo sabremos que está hecho; no mando nada al equipo sin tu sí.')
        return self.guardar(o)

    @exclusivo
    def fijar_contrato(self, oid, cambios=None):
        o = self.cargar(oid)
        if o['estado'] != 'esperando_contrato': raise Parada('No hay ningún contrato esperando.')
        c = {**o['contrato_propuesto'], **{k: v for k, v in (cambios or {}).items() if k in ('exito', 'metrica', 'presupuesto_llamadas', 'plazo_dias', 'no_tocar')}}
        c['presupuesto_llamadas'] = max(6, min(int(c.get('presupuesto_llamadas') or self.topes['llamadas_por_objetivo']), 120))
        o['contrato'] = c; o['estado'] = 'planificando'
        if c.get('plazo_dias'): o['vence'] = (ahora() + timedelta(days=int(c['plazo_dias']))).strftime('%Y-%m-%d')
        self._decir(o, 'owner', 'Contrato fijado.')
        self._decir(o, 'hermes', 'Hecho. Preparo la primera ronda.')
        return self.guardar(o)

    @exclusivo
    def aprobar(self, oid, importantes=False, por='Owner en el chat'):
        o = self.cargar(oid)
        if o['estado'] != 'esperando_aprobacion': raise Parada('No hay ninguna ronda esperando tu aprobación.')
        r = o['rondas'][-1]
        if any(t.get('importante') for t in r['tareas']) and not importantes:
            raise Parada('Esta ronda tiene tareas importantes: márcalas como revisadas para aprobarla.')
        bloqueo = self.a.freno()
        if bloqueo: raise Parada(f'El equipo está frenado ({bloqueo}). Aprueba cuando se levante.')
        self._decir(o, 'owner', f'Aprobada la ronda {r["n"]}' + (' (con las importantes revisadas)' if importantes else '') + (f' · {por}' if por != 'Owner en el chat' else ''))
        self._enviar(o, r, por)
        return self.guardar(o)

    def _enviar(self, o, r, por):
        r['estado'] = 'aprobada'; r['aprobada'] = hora(); r['aprobada_por'] = por
        for t in r['tareas']:
            if t['motor'] != 'equipo': t['estado'] = 'ready'; continue      # los demás motores corren dentro del tick
            try:
                res = self.a.encargar(t['proyecto'], t, f'{por}: ronda {r["n"]} del objetivo de Hermes «{o["objetivo"][:80]}»')
                t['tid'] = res['tarea']; t['estado'] = 'ready'; t['constructor'] = res.get('constructor')
            except Exception as e:
                t['estado'] = 'error'; t['resultado'] = f'No se pudo encargar: {e}'[:300]
        r['estado'] = 'enviada'; o['estado'] = 'en_marcha'
        n = len(r['tareas']); vivas = sum(1 for t in r['tareas'] if t['estado'] != 'error')
        self._decir(o, 'hermes', (f'{vivas} de {n} tareas en marcha.' if n != 1 else ('Tarea en marcha.' if vivas else 'No he podido mandar la tarea.'))
                    + ' Te aviso cuando termine y esté comprobada.')

    @exclusivo
    def descartar(self, oid, motivo=''):
        o = self.cargar(oid)
        if o['estado'] != 'esperando_aprobacion': raise Parada('No hay ninguna ronda que descartar.')
        r = o['rondas'][-1]; r['estado'] = 'descartada'; r['motivo_descarte'] = motivo.strip()[:500]
        for p in {t['proyecto'] for t in r['tareas'] if t.get('proyecto')}: self._historial_confianza(p, False)
        o['estado'] = 'planificando'
        self._decir(o, 'owner', 'Descarto esta ronda' + (f': {motivo.strip()}' if motivo.strip() else '.'))
        self._decir(o, 'hermes', 'Entendido. Replanteo la ronda con eso en cuenta.')
        return self.guardar(o)

    @exclusivo
    def responder(self, oid, texto):
        o = self.cargar(oid); texto = (texto or '').strip()
        if not texto: raise Parada('Escribe la respuesta.')
        self._decir(o, 'owner', texto[:2000])
        if o['estado'] == 'esperando_contrato':
            o['estado'] = 'contrato'; self._decir(o, 'hermes', 'Rehago el contrato con eso.')
        elif o['estado'] in ('necesita_owner', 'esperando_aprobacion'):
            if o['estado'] == 'esperando_aprobacion':
                o['rondas'][-1]['estado'] = 'descartada'; o['rondas'][-1]['motivo_descarte'] = texto[:500]
            p = o.get('pregunta') or ''
            if re.fullmatch(r'\s*sigue[.!]?\s*', texto, re.I):
                if 'Tope diario' in p: o['extra_hoy'] = hoy()                      # una ronda más hoy, solo este objetivo
                if 'presupuesto' in p.lower() and o.get('contrato'):                 # un tercio más de presupuesto
                    o['contrato']['presupuesto_llamadas'] = int(o['contrato']['presupuesto_llamadas'] * 1.34) + 1
                if 'rondas con este objetivo' in p: o['rondas_extra'] = o.get('rondas_extra', 0) + 4
                o['sin_avance_ok'] = len(o['rondas'])                                # estancamiento: Owner dice que insista
            o['estado'] = 'planificando'; o.pop('pregunta', None)
            self._decir(o, 'hermes', 'Gracias. Sigo con eso.')
        return self.guardar(o)

    @exclusivo
    def parar(self, oid):
        o = self.cargar(oid)
        if o['estado'] in ('hecho', 'parado'): return o
        vivas = [t['tid'] for r in o['rondas'] for t in r['tareas'] if t.get('tid') and t.get('estado') not in TERMINAL_TAREA]
        o['estado'] = 'parado'
        self._decir(o, 'owner', 'Para.')
        self._decir(o, 'hermes', 'Parado. No planifico ni encargo nada más.' + (
            f' Las tareas que ya estaban en el equipo siguen su curso ({", ".join(vivas)}); si quieres retirarlas: '
            f'equipo.py archivar <proyecto> --ids {",".join(vivas)}.' if vivas else ''))
        return self.guardar(o)

    @exclusivo
    def boton(self, data):
        """Un toque en un botón de WhatsApp/Telegram: hm:<id>:a|i|d|p. Devuelve el texto corto para el móvil."""
        m = re.fullmatch(r'hm:(h_[0-9a-f]{8}):([aidp])', data or '')
        if not m: raise Parada('Botón desconocido.')
        oid, acc = m.groups()
        if acc in 'ai': self.aprobar(oid, importantes=acc == 'i', por='Owner desde el móvil'); return 'Aprobada. El equipo se pone.'
        if acc == 'd': self.descartar(oid, 'desde el móvil'); return 'Descartada. Replanteo la ronda.'
        self.parar(oid); return 'Parado.'

    # ---------- el bucle ----------
    @exclusivo
    def tick(self):
        """Un paso de cada objetivo activo y, por la mañana, el resumen de la noche. Idempotente."""
        hechos = []
        for o in self.todos():
            if o['estado'] not in ACTIVOS: continue
            try: hechos.append((o['id'], self._paso(o)))
            except Parada as e:
                o['estado'] = 'necesita_owner'; o['pregunta'] = str(e); self._decir(o, 'hermes', str(e)); self.guardar(o)
                self._avisar(o, str(e)[:240])
                hechos.append((o['id'], 'parada: ' + str(e)))
        r = self._resumen_manana()
        if r: hechos.append(('resumen', r))
        return hechos

    def _paso(self, o):
        if o['estado'] == 'en_marcha': return self._vigilar(o)
        bloqueo = self.a.freno()
        if bloqueo:
            if o.get('frenado') != bloqueo:
                o['frenado'] = bloqueo; self._decir(o, 'hermes', f'Espero: {bloqueo}.'); self.guardar(o)
            return 'frenado'
        o.pop('frenado', None)
        if o['estado'] == 'contrato': return self._contrato(o)
        return self._planificar(o)

    def _contrato(self, o):
        self._topes(o, ronda=False)
        c = self.a.planificar(self._prompt_contrato(o)); self._llamada(o)
        if not isinstance(c, dict) or not c.get('exito'): raise Parada('No he sabido proponer cómo medir este objetivo. Dime qué cifra quieres mover.')
        met = c.get('metrica')
        if met and not (isinstance(met, dict) and met.get('fuente') in self.a.fuentes() and isinstance(met.get('objetivo'), (int, float))):
            c['metrica'] = None; c['nota_metrica'] = 'La métrica propuesta no se puede leer sola; la medimos contigo.'
        c.setdefault('presupuesto_llamadas', self.topes['llamadas_por_objetivo'])
        o['contrato_propuesto'] = c; o['estado'] = 'esperando_contrato'
        m = c.get('metrica')
        self._decir(o, 'hermes', 'Propuesta: así sabremos que está hecho' + (f' (lo mido yo solo: {m["nombre"]})' if m else '') + '. ¿Lo fijamos?')
        self.guardar(o); self._avisar(o, 'te propongo cómo medir el éxito; fíjalo en el chat de Hermes.')
        return 'contrato propuesto'

    def _topes(self, o, ronda=True):
        u = self.uso(); c = o.get('contrato') or {}
        hechas = sum(1 for r in o['rondas'] if r['estado'] != 'descartada')
        if ronda and hechas >= self.topes['rondas_por_objetivo'] + o.get('rondas_extra', 0):
            raise Parada(f'Llevo {hechas} rondas con este objetivo (tope). ¿Sigo, cambiamos el enfoque o lo dejamos aquí?')
        if ronda and u.get('rondas', 0) >= self.topes['rondas_por_dia'] and o.get('extra_hoy') != hoy():
            raise Parada(f'Tope diario de {self.topes["rondas_por_dia"]} rondas alcanzado. Sigo mañana, o respóndeme «sigue» si quieres una más hoy.')
        if u.get('llamadas', 0) + 3 > self.topes['llamadas_por_dia']:
            raise Parada(f'Tope diario de {self.topes["llamadas_por_dia"]} llamadas a modelos alcanzado. Sigo mañana.')
        if c.get('presupuesto_llamadas') and o.get('llamadas', 0) + 3 > c['presupuesto_llamadas']:
            raise Parada(f'He gastado el presupuesto de este objetivo ({o.get("llamadas", 0)} de {c["presupuesto_llamadas"]} llamadas). '
                         '¿Le doy un tercio más («sigue») o lo dejamos aquí?')

    def _medir(self, o):
        met = (o.get('contrato') or {}).get('metrica')
        if not met: return None
        try: v = self.a.medir(met)
        except Exception as e: v = {'valor': None, 'detalle': f'no se pudo medir: {e}'}
        if v is None: return None
        v['fecha'] = hora(); o['medidas'].append(v); o['medidas'] = o['medidas'][-30:]
        return v

    def _planificar(self, o):
        medida = self._medir(o)
        met = (o.get('contrato') or {}).get('metrica')
        if medida and cumple(met, medida.get('valor')):
            return self._cerrar(o, f'«{met["nombre"]}» está en {medida["valor"]:g} (objetivo {met.get("sentido", ">=")} {met["objetivo"]:g}). {medida.get("detalle", "")}'.strip())
        self._estancamiento(o)
        self._topes(o)
        proyectos = self.a.proyectos()
        plan = self.a.planificar(self._prompt_plan(o, proyectos, medida)); self._llamada(o)
        if not isinstance(plan, dict): raise Parada('El planificador no devolvió un plan legible.')
        if plan.get('hecho'):
            if met and not cumple(met, medida.get('valor') if medida else None):
                if o.get('hecho_rechazado') == len(o['rondas']):
                    raise Parada(f'El planificador cree que está hecho pero «{met["nombre"]}» sigue en {medida.get("valor") if medida else "sin medir"}. '
                                 f'¿Lo damos por bueno o cambiamos el objetivo?')
                o['hecho_rechazado'] = len(o['rondas']); self._decir(o, 'hermes', 'La métrica aún no se cumple: busco otra palanca.')
                plan = self.a.planificar(self._prompt_plan(o, proyectos, medida) + '\n\nOJO: dijiste «hecho» pero la métrica NO se cumple. Propón una ronda distinta.'); self._llamada(o)
                if plan.get('hecho') or not plan.get('tareas'): raise Parada(plan.get('necesita_owner') or 'No veo cómo mover más la métrica sin ti. ¿Qué probamos?')
            else: return self._cerrar(o, str(plan.get('resumen', ''))[:2000])
        if plan.get('necesita_owner'): raise Parada(str(plan['necesita_owner'])[:1000])
        tareas, avisos = self._validar(plan.get('tareas'), proyectos, o)
        if not tareas: raise Parada('No he sabido convertir el objetivo en tareas válidas: ' + '; '.join(avisos)[:600])
        debate = self._debatir(o, plan, tareas, proyectos)
        if debate and debate.get('tareas'): tareas, avisos = debate.pop('tareas'), debate.pop('avisos')
        n = 1 + len(o['rondas'])
        r = {'n': n, 'estado': 'propuesta', 'resumen': str((debate or {}).get('resumen') or plan.get('resumen', ''))[:1000], 'tareas': tareas,
             'debate': debate, 'avisos': avisos, 'estimacion': self._estimar(tareas), 'creada': hora()}
        o['rondas'].append(r); o['estado'] = 'esperando_aprobacion'; o.pop('extra_hoy', None)
        self._ronda_contada()
        imp = sum(1 for t in tareas if t.get('importante'))
        est = r['estimacion']
        self._decir(o, 'hermes', f'Ronda {n} lista: {plural(len(tareas), "tarea")}' + (f', {imp} importante(s)' if imp else '')
                    + (f' · ≈ {est["minutos"]} min y {est["usd"]:.2f} $ de uso'.replace('.', ',') if est else '')
                    + (f'. {debate["diferencia"]}' if debate and debate.get('diferencia') else '.'))
        sola = self._se_aprueba_sola(r)
        if sola is not None and not self.a.freno():
            self._enviar(o, r, f'aprobada sola (confianza nivel {sola} que tú fijaste)')
            self._decir(o, 'hermes', f'Se aprueba sola: solo tareas de dificultad {"/".join(NIVELES[sola])} en proyectos donde subiste la confianza.')
            self.guardar(o); return f'ronda {n} aprobada sola'
        self.guardar(o); self._avisar(o, f'ronda {n} lista para tu aprobación.', self.botones_ronda(o))
        return f'ronda {n} propuesta'

    def _cerrar(self, o, resumen):
        o['estado'] = 'hecho'; o['resumen_final'] = resumen; o['cerrado'] = hora()
        o['cierre'] = self._post_mortem(o)
        self._decir(o, 'hermes', 'Objetivo cumplido. ' + resumen)
        self.guardar(o); self._avisar(o, 'objetivo cumplido. ' + resumen[:200]); return 'hecho'

    def _post_mortem(self, o):
        rondas = [r for r in o['rondas'] if r['estado'] != 'descartada']
        tareas = [t for r in rondas for t in r['tareas']]
        cierre = {'rondas': len(rondas), 'descartadas': sum(1 for r in o['rondas'] if r['estado'] == 'descartada'),
                  'tareas': len(tareas), 'integradas': sum(1 for t in tareas if t.get('estado') == 'done'),
                  'llamadas': o.get('llamadas', 0), 'dias': max(1, (ahora() - datetime.fromisoformat(o['creado'])).days + 1),
                  'motores': sorted({t['motor'] for t in tareas})}
        libro = self._leer('playbooks.json', [])
        libro.append({'objetivo': o['objetivo'][:300], 'proyecto': o.get('proyecto'), 'contrato': o.get('contrato'), 'cierre': cierre,
                      'rondas': [{'resumen': r['resumen'], 'tareas': [t['titulo'] for t in r['tareas']]} for r in rondas]})
        self._escribir('playbooks.json', libro[-50:])
        return cierre

    def _estancamiento(self, o):
        """Dos rondas seguidas sin integrar nada, o la métrica quieta tres medidas: no insistir a ciegas."""
        if o.get('sin_avance_ok') == len(o['rondas']): return
        hechas = [r for r in o['rondas'] if r['estado'] == 'terminada']
        if len(hechas) >= 2 and all(not any(t.get('estado') == 'done' for t in r['tareas']) for r in hechas[-2:]):
            fallos = '; '.join(f'{t["titulo"]}: {(t.get("resultado") or "")[:80]}' for r in hechas[-2:] for t in r['tareas'])[:500]
            raise Parada(f'Dos rondas sin avanzar ({fallos}). ¿Cambio de enfoque, me dices qué falla o insisto («sigue»)?')
        vals = [m.get('valor') for m in o['medidas'] if m.get('valor') is not None]
        if len(hechas) >= 3 and len(vals) >= 3 and len(set(vals[-3:])) == 1:
            raise Parada(f'La métrica lleva tres medidas en {vals[-1]:g} sin moverse. ¿Cambiamos de palanca o insisto («sigue»)?')

    def _validar(self, tareas, proyectos, o):
        fijo = o.get('proyecto'); no_tocar = set((o.get('contrato') or {}).get('no_tocar') or [])
        ok, avisos = [], []
        for i, t in enumerate((tareas or [])[:self.topes['tareas_por_ronda']]):
            if not isinstance(t, dict): avisos.append(f'tarea {i + 1}: formato'); continue
            motor = t.get('motor') or 'equipo'
            if motor not in MOTORES: avisos.append(f'tarea {i + 1}: motor {motor!r} desconocido'); continue
            pid = t.get('proyecto') or fijo
            if motor != 'investigacion' and pid not in proyectos: avisos.append(f'tarea {i + 1}: proyecto {pid!r} desconocido'); continue
            if fijo and pid and pid != fijo: avisos.append(f'tarea {i + 1}: fuera del proyecto {fijo}'); continue
            criterios = [str(c)[:200] for c in (t.get('criterios') or []) if str(c).strip()][:4]
            base = {'proyecto': pid, 'motor': motor, 'estado': 'propuesta', 'criterios': criterios, 'titulo': str(t.get('titulo', '')).strip()[:120]}
            if len(base['titulo']) < 4: avisos.append(f'tarea {i + 1}: título'); continue
            if motor == 'equipo':
                files = t.get('archivos') or []
                if any(not isinstance(f, str) or PurePosixPath(f).is_absolute() or '..' in PurePosixPath(f).parts for f in files):
                    avisos.append(f'tarea {i + 1}: ruta no permitida'); continue
                if any(f in PROTEGIDAS for f in files): avisos.append(f'tarea {i + 1}: toca una pantalla protegida de Alfred; descartada'); continue
                if any(f in no_tocar or any(f.startswith(n.rstrip('/') + '/') for n in no_tocar) for f in files):
                    avisos.append(f'tarea {i + 1}: toca algo que dijiste que no se toca'); continue
                try: norm = self.a.normalizar(pid, {k: v for k, v in t.items() if k not in ('proyecto', 'motor', 'criterios')})
                except Exception as e: avisos.append(f'tarea {i + 1}: {e}'); continue
                ok.append({**norm, **base}); continue
            if motor == 'anuncios':
                if not isinstance(t.get('plan'), dict): avisos.append(f'tarea {i + 1}: falta el plan de campaña'); continue
                ok.append({**base, 'plan': {**t['plan'], 'proyecto': pid}, 'detalle': str(t.get('detalle') or 'Campaña en ensayo: no se crea nada en Meta.')[:600],
                           'dificultad': 'baja'}); continue
            pedido = str(t.get('pregunta') or t.get('encargo') or t.get('detalle') or '').strip()
            if len(pedido) < 20: avisos.append(f'tarea {i + 1}: falta qué investigar o escribir'); continue
            ok.append({**base, 'detalle': pedido[:1500], 'dificultad': 'baja'})
        if len(tareas or []) > self.topes['tareas_por_ronda']: avisos.append(f'solo se proponen {self.topes["tareas_por_ronda"]} tareas por ronda')
        return ok, avisos

    def _debatir(self, o, plan, tareas, proyectos):
        """Codex contrapropone su propia ronda leyendo el código; un árbitro Claude elige o mezcla y explica la diferencia."""
        contra = self.a.contraproponer(self._prompt_contra(o, plan, tareas)); self._llamada(o)
        if not contra or not contra.get('tareas'):
            return {'codex': (contra or {}).get('resumen') or 'Codex no está disponible ahora; ronda sin su contrapropuesta.', 'elegido': 'claude'} if contra is not None else None
        ct, cav = self._validar(contra['tareas'], proyectos, o)
        if not ct: return {'codex': contra.get('resumen', ''), 'elegido': 'claude', 'diferencia': 'La propuesta de Codex no era válida; sigo con la de Claude.'}
        fallo = self.a.arbitrar(self._prompt_arbitro(o, plan, tareas, contra, ct)); self._llamada(o)
        elegido = (fallo or {}).get('elegido') if (fallo or {}).get('elegido') in ('claude', 'codex', 'mezcla') else 'claude'
        out = {'claude': plan.get('resumen', ''), 'codex': contra.get('resumen', ''), 'elegido': elegido,
               'diferencia': str((fallo or {}).get('diferencia') or '')[:300], 'resumen': (fallo or {}).get('resumen')}
        if elegido == 'codex': out.update(tareas=ct, avisos=cav)
        elif elegido == 'mezcla' and (fallo or {}).get('tareas'):
            mt, mav = self._validar(fallo['tareas'], proyectos, o)
            if mt: out.update(tareas=mt, avisos=mav)
        return out

    def _estimar(self, tareas):
        try: return self.a.estimar(tareas)
        except Exception: return None

    def _vigilar(self, o):
        r = o['rondas'][-1]; cambios = False
        for t in r['tareas']:
            if t.get('estado') in TERMINAL_TAREA: continue
            if t['motor'] == 'equipo':
                if not t.get('tid'): continue
                st = self.a.estado(t['proyecto'], t['tid'])
                if st and st.get('estado') != t.get('estado'):
                    t['estado'] = st['estado']; t['resultado'] = (st.get('resultado') or '')[:600]; cambios = True
            elif t.get('estado') == 'ready':
                # Un motor sin tablero por tick: anuncios (ensayo), investigación o contenido.
                t['estado'] = 'running'; self.guardar(o)
                try:
                    res = self.a.ejecutar(t['motor'], t, o); self._llamada(o, res.get('llamadas', 0))
                    t['estado'] = 'done' if res.get('ok') else 'error'; t['resultado'] = str(res.get('resultado', ''))[:1500]
                    if res.get('ruta'): t['ruta'] = res['ruta']
                except Exception as e: t['estado'] = 'error'; t['resultado'] = f'Falló: {e}'[:300]
                self.guardar(o); return f'{t["motor"]}: {t["estado"]}'
        if all(t.get('estado') in TERMINAL_TAREA for t in r['tareas']): return self._terminar_ronda(o, r)
        if cambios: self.guardar(o)
        return 'vigilando'

    def _terminar_ronda(self, o, r):
        r['estado'] = 'terminada'; r['terminada'] = hora(); o['estado'] = 'planificando'
        hechas = [t for t in r['tareas'] if t['estado'] == 'done']
        try: r['verificacion'] = self.a.verificar(o, r) or {}
        except Exception as e: r['verificacion'] = {'error': str(e)[:200]}
        self._llamada(o, r['verificacion'].get('llamadas', 0))
        criterios = [c for v in (r['verificacion'].get('tareas') or {}).values() for c in v.get('criterios', [])]
        bien = sum(1 for c in criterios if c.get('ok'))
        capturas = len(r['verificacion'].get('capturas') or [])
        for p in {t['proyecto'] for t in r['tareas'] if t['motor'] == 'equipo'}:
            if 'aprobada sola' not in r.get('aprobada_por', ''):
                self._historial_confianza(p, all(t['estado'] == 'done' for t in r['tareas'] if t['proyecto'] == p) and bien == len(criterios))
        texto = (f'Ronda {r["n"]} terminada: {len(hechas)} de {plural(len(r["tareas"]), "tarea")} hechas'
                 + (f', {bien}/{len(criterios)} criterios comprobados' if criterios else '') + (f', {plural(capturas, "captura")}' if capturas else '') + '.')
        self._decir(o, 'hermes', texto + ' Mido y preparo lo siguiente.')
        self.guardar(o); self._avisar(o, texto)
        return 'ronda terminada'

    # ---------- prompts ----------
    def _historial(self, o):
        out = []
        for r in o['rondas']:
            v = r.get('verificacion') or {}
            out.append({'ronda': r['n'], 'estado': r['estado'], 'motivo_descarte': r.get('motivo_descarte'),
                        'tareas': [{'motor': t['motor'], 'proyecto': t.get('proyecto'), 'titulo': t['titulo'], 'estado': t.get('estado'),
                                    'resultado': (t.get('resultado') or '')[:300],
                                    'criterios_fallidos': [c['criterio'] for c in (v.get('tareas', {}).get(t['titulo'], {}).get('criterios') or []) if not c.get('ok')]}
                                   for t in r['tareas']]})
        charla = [m for m in o['mensajes'] if m['de'] == 'owner'][-8:]
        return json.dumps({'rondas': out, 'owner_dijo': [m['texto'] for m in charla]}, ensure_ascii=False)

    def _parecidos(self, o):
        """Objetivos ya cumplidos que se parecen a este: su plan sirve de punto de partida (playbooks)."""
        palabras = {w for w in re.findall(r'\w{5,}', o['objetivo'].lower())}
        libro = sorted(self._leer('playbooks.json', []), key=lambda p: -len(palabras & set(re.findall(r'\w{5,}', p['objetivo'].lower()))))
        return [p for p in libro[:2] if len(palabras & set(re.findall(r'\w{5,}', p['objetivo'].lower()))) >= 2]

    def _fichas(self, proyectos):
        return {pid: {'nombre': p['nombre'], 'objetivo': p['objetivo'][:500], 'pruebas': p['pruebas'], 'max_archivos': p['max_archivos']} for pid, p in proyectos.items()}

    def _prompt_contrato(self, o):
        charla = [m['texto'] for m in o['mensajes'] if m['de'] == 'owner']
        return f"""Eres Hermes, el orquestador de Owner. Owner no programa y quiere ganar dinero: valida y cobra cuanto antes.
Su objetivo: «{o['objetivo']}». Lo que ha dicho después: {json.dumps(charla[1:], ensure_ascii=False)}
Proyectos: {json.dumps(self._fichas(self.a.proyectos()), ensure_ascii=False)}
Fuentes de métrica que sé leer solo: {json.dumps(self.a.fuentes(), ensure_ascii=False)}
Propón el contrato del objetivo. Responde solo con JSON:
{{"exito": "una frase: cómo sabremos que está hecho, en cristiano", "metrica": {{"fuente": "id de fuente", "proyecto": "id", "nombre": "qué se mide",
 "objetivo": número, "sentido": ">=" o "<=", "dias": días de la ventana}} o null si no hay fuente que lo mida,
 "plazo_dias": número, "presupuesto_llamadas": número entre 12 y 60, "no_tocar": ["rutas o cosas que no se tocan"]}}"""

    def _prompt_plan(self, o, proyectos, medida=None):
        c = o.get('contrato') or {}
        parecidos = self._parecidos(o)
        return f"""Eres Hermes, el orquestador de Owner. Llevas su objetivo hasta el final repartiendo trabajo a sus motores:
- equipo: código (Codex construye, Claude revisa, o al revés; integra en la rama equipo/<proyecto>, nunca en main)
- anuncios: plan de campaña de Meta para sem-meta-ads, SOLO en ensayo (nunca se crea ni activa nada)
- investigacion: preguntar a la web en solo lectura (competencia, precios, mercado)
- contenido: borradores (guiones de reels, emails, textos de anuncio) que quedan en el vault; nadie publica

OBJETIVO DE OWNER: {o['objetivo']}
CONTRATO: {json.dumps(c, ensure_ascii=False)}
ÚLTIMA MEDIDA: {json.dumps(medida, ensure_ascii=False) if medida else 'sin métrica automática'}
{('PROYECTO FIJADO: ' + o['proyecto']) if o.get('proyecto') else 'Elige tú el proyecto o proyectos.'}
PROYECTOS: {json.dumps(self._fichas(proyectos), ensure_ascii=False)}
LO HECHO HASTA AHORA (rondas, resultados, criterios que fallaron y lo que ha dicho Owner): {self._historial(o)}
{('OBJETIVOS PARECIDOS YA CUMPLIDOS (úsalos de punto de partida): ' + json.dumps(parecidos, ensure_ascii=False)) if parecidos else ''}

Decide UNA de estas tres cosas y responde solo con JSON:
1. Objetivo cumplido según el contrato: {{"hecho": true, "resumen": "qué se ha conseguido y qué tiene que probar Owner"}}
2. Necesitas algo que solo Owner puede dar (decisión de producto, precio, clave, publicar, activar anuncios, gastar): {{"necesita_owner": "pregunta concreta"}}
3. La siguiente ronda, máximo {self.topes['tareas_por_ronda']} tareas pequeñas e independientes que muevan la métrica:
{{"resumen": "qué consigue esta ronda", "tareas": [
 {{"motor": "equipo", "proyecto": "id", "titulo": "…", "porque": "…", "detalle": "instrucciones concretas (≥ 20 caracteres)",
  "archivos": ["rutas relativas"], "pruebas": ["pruebas del proyecto"], "dificultad": "baja|media|alta", "criterios": ["cómo se comprueba que está bien (≤ 3)"]}},
 {{"motor": "anuncios", "proyecto": "id", "titulo": "…", "plan": {{"objetivo": "LEADS|VENTAS|TRAFICO", "presupuesto_diario_eur": n, "conjuntos": [...]}}}},
 {{"motor": "investigacion", "titulo": "…", "pregunta": "qué averiguar exactamente"}},
 {{"motor": "contenido", "proyecto": "id", "titulo": "…", "encargo": "qué escribir, para quién y con qué gancho"}}]}}
Reglas: validar y cobrar cuanto antes (MVP); nada de lanzar, publicar, activar, gastar, credenciales, push ni despliegues (eso lo hace Owner);
no toques {', '.join(PROTEGIDAS)} ni lo que el contrato dice que no se toca; no repitas lo ya hecho; si una ronda falló o un criterio no se cumplió, cambia el enfoque."""

    def _prompt_contra(self, o, plan, tareas):
        return f"""Eres Codex y revisas en solo lectura el plan de Hermes para el objetivo de Owner: «{o['objetivo']}».
Contrato: {json.dumps(o.get('contrato') or {}, ensure_ascii=False)}
Plan de Claude: {json.dumps({'resumen': plan.get('resumen'), 'tareas': tareas}, ensure_ascii=False)}
Lee el código del repositorio y propón TU mejor ronda (puede parecerse o ser distinta), con el mismo formato de tareas y como mucho {self.topes['tareas_por_ronda']}.
Responde solo con JSON: {{"resumen": "tu ronda en una frase", "problemas_del_plan": ["…"], "tareas": [...]}}"""

    def _prompt_arbitro(self, o, plan, tareas, contra, ct):
        return f"""Eres el árbitro de Hermes. Objetivo de Owner: «{o['objetivo']}». Contrato: {json.dumps(o.get('contrato') or {}, ensure_ascii=False)}
Propuesta de Claude: {json.dumps({'resumen': plan.get('resumen'), 'tareas': tareas}, ensure_ascii=False)}
Propuesta de Codex: {json.dumps({'resumen': contra.get('resumen'), 'problemas': contra.get('problemas_del_plan'), 'tareas': ct}, ensure_ascii=False)}
Elige la que más mueve la métrica con menos riesgo, o mézclalas. Responde solo con JSON:
{{"elegido": "claude|codex|mezcla", "diferencia": "en dos frases cortas y sin jerga, qué cambia entre las dos y por qué eliges",
 "resumen": "la ronda elegida en una frase", "tareas": [solo si es mezcla, con el mismo formato]}}"""
