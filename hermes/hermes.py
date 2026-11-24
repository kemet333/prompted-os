#!/usr/bin/env python3
"""Hermes supremo: chat local en el Mac que lleva un objetivo de Owner hasta el final con Claude, Codex y todos los motores.

  ~/Equipo/venv/bin/python hermes/hermes.py serve [--seco] [--puerto 4410] [--auto SEG]   chat en http://127.0.0.1:<puerto>
  ~/Equipo/venv/bin/python hermes/hermes.py tick                     un paso de cada objetivo (programable cada 5 min)
  ~/Equipo/venv/bin/python hermes/hermes.py nuevo "objetivo" [--proyecto id]
  ~/Equipo/venv/bin/python hermes/hermes.py estado | fijar <id> | aprobar <id> [--importantes] | parar <id>
  ~/Equipo/venv/bin/python hermes/hermes.py boton hm:<id>:a|i|d|p    un toque en WhatsApp/Telegram (lo llama la app)
  ~/Equipo/venv/bin/python hermes/hermes.py confianza <proyecto> 0|1|2
  ~/Equipo/venv/bin/python hermes/hermes.py demo                     demo en seco de punta a punta (sin modelos ni tableros)

--seco usa el adaptador falso y un estado aparte: no gasta uso del plan ni toca nada.
"""
import argparse, json, mimetypes, re, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from nucleo import Hermes, Parada, hoy            # noqa: E402
from adaptador import AdaptadorFalso              # noqa: E402


def crear(seco=False, carpeta=None):
    if seco: return Hermes(AdaptadorFalso(), carpeta or Path(tempfile.gettempdir()) / 'hermes-seco')
    from adaptador import AdaptadorReal
    return Hermes(AdaptadorReal(), carpeta)


def resumen(h):
    """Lo que pinta el chat: objetivos, uso del día, confianza, ranking y si el equipo puede trabajar."""
    try: freno = h.a.freno()
    except Exception as e: freno = f'no he podido leer el estado del equipo ({e})'
    try: proyectos = {pid: p['nombre'] for pid, p in h.a.proyectos().items()}
    except Exception: proyectos = {}
    try: ranking = h.a.ranking()
    except Exception: ranking = None
    conf = h.confianza()
    return {'objetivos': h.todos(), 'uso': h.uso(), 'topes': h.topes, 'freno': freno, 'hoy': hoy(), 'proyectos': proyectos,
            'confianza': {pid: {**conf.get(pid, {}), 'nivel': h.nivel(pid)} for pid in proyectos}, 'ranking': ranking,
            'noche': h.es_noche(), 'seco': isinstance(h.a, AdaptadorFalso)}


def crear_servidor(h, puerto=4410, auto=None):
    html = (HERE / 'chat.html').read_bytes()

    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _json(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code); self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path in ('/', '/index.html'):
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html))); self.end_headers(); self.wfile.write(html); return
            if self.path == '/api/estado': return self._json(200, resumen(h))
            m = re.fullmatch(r'/api/captura/(h_[0-9a-f]{8})/([\w.-]+\.png)', self.path)
            if m:
                f = h.dir / 'capturas' / m.group(1) / m.group(2)
                if f.exists():
                    data = f.read_bytes(); self.send_response(200); self.send_header('Content-Type', 'image/png')
                    self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
            self._json(404, {'error': 'no existe'})

        def do_POST(self):
            # Solo desde esta misma página: cabecera propia y origen local (otra web abierta en el navegador no puede mandar órdenes).
            origen = self.headers.get('Origin') or ''
            if self.headers.get('X-Hermes') != '1' or (origen and not origen.startswith(('http://127.0.0.1:', 'http://localhost:'))):
                return self._json(403, {'error': 'origen no permitido'})
            try:
                n = int(self.headers.get('Content-Length') or 0)
                if n > 20000: return self._json(413, {'error': 'demasiado largo'})
                b = json.loads(self.rfile.read(n) or b'{}'); ruta = self.path; o = None
                if ruta == '/api/objetivo': o = h.nuevo(b.get('texto'), b.get('proyecto') or None)
                elif ruta == '/api/contrato': o = h.fijar_contrato(b.get('id'), b.get('cambios') or None)
                elif ruta == '/api/aprobar': o = h.aprobar(b.get('id'), bool(b.get('importantes')))
                elif ruta == '/api/descartar': o = h.descartar(b.get('id'), b.get('motivo') or '')
                elif ruta == '/api/responder': o = h.responder(b.get('id'), b.get('texto'))
                elif ruta == '/api/parar': o = h.parar(b.get('id'))
                elif ruta == '/api/confianza': h.fijar_confianza(b.get('proyecto'), int(b.get('maximo')))
                elif ruta == '/api/avanzar': h.tick()
                else: return self._json(404, {'error': 'no existe'})
                if ruta in ('/api/objetivo', '/api/responder', '/api/descartar', '/api/contrato') and auto:   # respuesta sin esperar al ciclo
                    threading.Thread(target=h.tick, daemon=True).start()
                return self._json(200, {'ok': True, 'objetivo': o})
            except Parada as e: return self._json(400, {'error': str(e)})
            except (ValueError, KeyError, TypeError) as e: return self._json(400, {'error': f'petición no válida: {e}'})

    if auto:
        def bucle():
            while True:
                time.sleep(auto)
                try: h.tick()
                except Exception as e: print('tick falló:', e, flush=True)
        threading.Thread(target=bucle, daemon=True).start()
    return ThreadingHTTPServer(('127.0.0.1', puerto), Manejador)


def servir(h, puerto=4410, auto=None):
    srv = crear_servidor(h, puerto, auto)
    print(f'Hermes en http://127.0.0.1:{srv.server_port}' + (' (en seco)' if isinstance(h.a, AdaptadorFalso) else ''), flush=True)
    srv.serve_forever()


def demo():
    """Un objetivo de punta a punta con el adaptador falso. Imprime la conversación tal como la vería Owner."""
    carpeta = Path(tempfile.mkdtemp(prefix='hermes-demo-'))
    h = Hermes(AdaptadorFalso(pasos=2, limpiar=True), carpeta, noche={'desde': '03:00', 'hasta': '03:01'})
    o = h.nuevo('Quiero saber qué empresas del comparador de Beta convierten y ordenarlo por eso.', 'beta')
    for _ in range(60):
        o = h.cargar(o['id'])
        if o['estado'] == 'esperando_contrato':
            print(f"\n— Contrato propuesto: {o['contrato_propuesto']['exito']}\n   (Owner pulsa «Fijar»)"); h.fijar_contrato(o['id'])
        elif o['estado'] == 'esperando_aprobacion':
            r = o['rondas'][-1]
            print(f"\n— Ronda {r['n']}: {r['resumen']}")
            for t in r['tareas']: print(f"   · [{t['motor']}] {t['titulo']}" + (f"  ({', '.join(t['archivos'])})" if t.get('archivos') else ''))
            if (r.get('debate') or {}).get('diferencia'): print(f"   Debate → {r['debate']['elegido']}: {r['debate']['diferencia']}")
            print('   (Owner pulsa «Aprobar» en WhatsApp)'); h.boton(f'hm:{o["id"]}:a')
        elif o['estado'] in ('hecho', 'parado', 'necesita_owner'): break
        else: h.tick()
    print('\nConversación:')
    for m in o['mensajes']: print(f"  {m['de']:>7}: {m['texto']}")
    print(f"\nEstado final: {o['estado']} · rondas: {len(o['rondas'])} · encargos al equipo: {len(h.a.encargos)} · otros motores: {h.a.ejecutados}")
    print(f"Avisos al móvil: {len(h.a.avisos)} · cierre: {o.get('cierre')} · uso: {h.uso()}")
    print(f'Estado guardado en {carpeta}')
    return o


def main():
    ap = argparse.ArgumentParser(description='Hermes supremo')
    ap.add_argument('cmd', choices=['serve', 'tick', 'nuevo', 'estado', 'fijar', 'aprobar', 'parar', 'boton', 'confianza', 'demo'])
    ap.add_argument('arg', nargs='?'); ap.add_argument('arg2', nargs='?')
    ap.add_argument('--seco', action='store_true'); ap.add_argument('--puerto', type=int, default=4410)
    ap.add_argument('--auto', type=int, help='segundos entre pasos automáticos mientras el chat está abierto')
    ap.add_argument('--proyecto'); ap.add_argument('--importantes', action='store_true')
    a = ap.parse_args()
    if a.cmd == 'demo': demo(); return
    h = crear(a.seco)
    try:
        if a.cmd == 'serve': servir(h, a.puerto, a.auto or (4 if a.seco else None))
        elif a.cmd == 'tick': print(json.dumps(h.tick(), ensure_ascii=False))
        elif a.cmd == 'nuevo': print(h.nuevo(a.arg, a.proyecto)['id'])
        elif a.cmd == 'estado': print(json.dumps(resumen(h), ensure_ascii=False, indent=1))
        elif a.cmd == 'fijar': print(h.fijar_contrato(a.arg)['estado'])
        elif a.cmd == 'aprobar': print(h.aprobar(a.arg, a.importantes, por='Owner por voz' if a.proyecto == 'voz' else 'Owner en el chat')['estado'])
        elif a.cmd == 'parar': print(h.parar(a.arg)['estado'])
        elif a.cmd == 'boton': print(h.boton(a.arg))
        elif a.cmd == 'confianza': print(json.dumps(h.fijar_confianza(a.arg, int(a.arg2)), ensure_ascii=False))
    except Parada as e:
        print(str(e)); sys.exit(2)


if __name__ == '__main__':
    main()
