"""Pruebas de Hermes con el adaptador falso: sin modelos, sin tableros, estado en una carpeta temporal.

  ~/Equipo/venv/bin/python -m unittest hermes/test_hermes.py
"""
import json, sys, tempfile, threading, unittest, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nucleo import Hermes, Parada, MADRID          # noqa: E402
from adaptador import AdaptadorFalso, _categoria   # noqa: E402

T = lambda titulo, archivos=('src/a.js',), **k: {'proyecto': 'beta', 'titulo': titulo, 'detalle': 'Instrucciones concretas para el constructor.',
                                                  'archivos': list(archivos), 'pruebas': ['unitarias'], 'dificultad': 'baja', **k}
DIA = lambda h=12, m=0: (lambda: datetime(2026, 11, 13, h, m, tzinfo=MADRID))
MET = {'fuente': 'demo_conversiones', 'proyecto': 'beta', 'nombre': 'altas', 'objetivo': 3, 'sentido': '>=', 'dias': 7}


def hermes(guion, reloj=None, **k):
    topes = k.pop('topes', None)
    a = AdaptadorFalso(guion=guion, **k)
    return Hermes(a, tempfile.mkdtemp(prefix='hermes-test-'), topes, reloj=reloj or DIA()), a


def hasta(h, oid, estado, n=25, contrato=True):
    """Avanza el bucle; fija el contrato propuesto como haría Owner (salvo que la prueba quiera mirarlo)."""
    for _ in range(n):
        o = h.cargar(oid)
        if o['estado'] == estado: return o
        if o['estado'] == 'esperando_contrato' and contrato: h.fijar_contrato(oid); continue
        h.tick()
    return h.cargar(oid)


def objetivo(h, proyecto=None): return h.nuevo('Objetivo de prueba suficientemente largo', proyecto)


class Contrato(unittest.TestCase):
    def test_primero_el_contrato_y_nada_sin_fijarlo(self):
        h, a = hermes([{'resumen': 'r1', 'tareas': [T('Primera tarea')]}], contrato={'exito': 'Tres altas', 'metrica': MET, 'no_tocar': ['backend/pagos']})
        o = objetivo(h); o = hasta(h, o['id'], 'esperando_contrato', contrato=False)
        self.assertEqual(o['contrato_propuesto']['metrica']['nombre'], 'altas')
        for _ in range(3): h.tick()
        self.assertEqual(h.cargar(o['id'])['estado'], 'esperando_contrato', 'sin su sí no se planifica')
        h.fijar_contrato(o['id'], {'presupuesto_llamadas': 2})
        self.assertEqual(h.cargar(o['id'])['contrato']['presupuesto_llamadas'], 6, 'el presupuesto tiene un mínimo razonable')

    def test_metrica_que_no_se_puede_leer_se_quita(self):
        h, _ = hermes([], contrato={'exito': 'x', 'metrica': {'fuente': 'stripe', 'objetivo': 5}})
        o = hasta(h, objetivo(h)['id'], 'esperando_contrato', contrato=False)
        self.assertIsNone(o['contrato_propuesto']['metrica'])

    def test_responder_al_contrato_lo_rehace(self):
        h, a = hermes([])
        o = hasta(h, objetivo(h)['id'], 'esperando_contrato', contrato=False)
        h.responder(o['id'], 'Mejor mide solicitudes de TbT')
        o = hasta(h, o['id'], 'esperando_contrato', contrato=False)
        self.assertIn('Mejor mide solicitudes', [p for p in a.prompts if 'contrato' in p][-1])

    def test_no_tocar_del_contrato(self):
        h, _ = hermes([{'resumen': 'r', 'tareas': [T('Toca pagos', archivos=['backend/pagos/stripe.js']), T('Toca otra cosa')]}],
                      contrato={'exito': 'x', 'metrica': None, 'no_tocar': ['backend/pagos']})
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual([t['titulo'] for t in o['rondas'][0]['tareas']], ['Toca otra cosa'])


class Bucle(unittest.TestCase):
    def test_nada_se_encarga_sin_aprobacion_tuyas(self):
        h, a = hermes([{'resumen': 'r1', 'tareas': [T('Primera tarea')]}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        for _ in range(4): h.tick()
        self.assertEqual(a.encargos, [], 'esperar no es aprobar')
        h.aprobar(o['id'])
        self.assertEqual(len(a.encargos), 1)
        self.assertIn('Owner en el chat', a.encargos[0]['por'])

    def test_de_punta_a_punta_hasta_hecho(self):
        h, a = hermes([{'resumen': 'r1', 'tareas': [T('Tarea uno'), T('Tarea dos')]}, {'resumen': 'r2', 'tareas': [T('Tarea tres')]},
                       {'hecho': True, 'resumen': 'Listo para probar.'}])
        o = objetivo(h)
        for _ in range(2): o = hasta(h, o['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        o = hasta(h, o['id'], 'hecho')
        self.assertEqual(o['estado'], 'hecho'); self.assertEqual(len(a.encargos), 3)
        self.assertIn('Listo para probar', o['mensajes'][-1]['texto'])
        self.assertEqual(o['cierre']['integradas'], 3)
        self.assertTrue(any('Integrada en equipo/beta' in p for p in a.prompts), 'replanifica con los resultados')
        self.assertEqual(len(h._leer('playbooks.json', [])), 1, 'queda como playbook')

    def test_las_importantes_piden_confirmacion_aparte(self):
        h, a = hermes([{'resumen': 'r1', 'tareas': [T('Toca el motor', archivos=['equipo/equipo.py'])]}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        with self.assertRaises(Parada): h.aprobar(o['id'])
        self.assertEqual(a.encargos, [])
        h.aprobar(o['id'], importantes=True); self.assertEqual(len(a.encargos), 1)

    def test_pantallas_protegidas_y_validacion(self):
        h, _ = hermes(topes={'tareas_por_ronda': 4}, guion=[{'resumen': 'r', 'tareas': [
            {**T('Cambiar el orb'), 'proyecto': 'prompted-os', 'archivos': ['alfred-os/app/orb.tsx'], 'pruebas': ['app']},
            T('Ruta mala', archivos=['../fuera.js']), T('Corta', detalle='poco'), T('Buena de verdad')]}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual([t['titulo'] for t in o['rondas'][0]['tareas']], ['Buena de verdad'])
        self.assertTrue(any('protegida' in x for x in o['rondas'][0]['avisos']))

    def test_proyecto_fijado(self):
        h, _ = hermes([{'resumen': 'r', 'tareas': [{**T('Otro proyecto'), 'proyecto': 'prompted-os', 'pruebas': ['app']}]}])
        o = hasta(h, objetivo(h, 'beta')['id'], 'necesita_owner')
        self.assertIn('fuera del proyecto', o['pregunta'])

    def test_parar(self):
        h, a = hermes([{'resumen': 'r', 'tareas': [T('Tarea uno')]}], pasos=99)
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        o = h.parar(o['id'])
        self.assertEqual(o['estado'], 'parado'); self.assertIn('archivar', o['mensajes'][-1]['texto'])
        n = len(a.prompts); h.tick(); h.tick(); self.assertEqual(len(a.prompts), n)

    def test_necesita_owner_y_respuesta(self):
        h, a = hermes([{'necesita_owner': '¿Qué precio quieres para el plan Pro?'}, {'resumen': 'r', 'tareas': [T('Tarea uno')]}])
        o = hasta(h, objetivo(h)['id'], 'necesita_owner'); self.assertIn('precio', o['pregunta'])
        h.responder(o['id'], '29 € al mes'); hasta(h, o['id'], 'esperando_aprobacion')
        self.assertTrue(any('29 € al mes' in p for p in a.prompts))

    def test_descartar_replantea(self):
        h, a = hermes([{'resumen': 'r1', 'tareas': [T('Mala idea')]}, {'resumen': 'r2', 'tareas': [T('Mejor idea')]}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        h.descartar(o['id'], 'empieza por la medición')
        o = hasta(h, o['id'], 'esperando_aprobacion')
        self.assertEqual(o['rondas'][-1]['tareas'][0]['titulo'], 'Mejor idea')
        self.assertIn('empieza por la medición', [p for p in a.prompts if 'OBJETIVO' in p][-1])

    def test_encargo_que_falla_no_atasca_la_ronda(self):
        h, _ = hermes([{'resumen': 'r', 'tareas': [T('Rota'), T('Sana')]}, {'hecho': True, 'resumen': 'fin'}], fallan={'Rota'})
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        self.assertEqual(h.cargar(o['id'])['rondas'][0]['tareas'][0]['estado'], 'error')
        self.assertEqual(hasta(h, o['id'], 'hecho')['estado'], 'hecho')

    def test_ids_y_objetivo_corto(self):
        h, _ = hermes([])
        with self.assertRaises(Parada): h.cargar('../../etc/passwd')
        with self.assertRaises(Parada): h.nuevo('corto')


class Metrica(unittest.TestCase):
    def test_se_cierra_por_la_metrica_sin_preguntar_al_planificador(self):
        h, a = hermes([{'resumen': 'r', 'tareas': [T('Tarea uno')]}], contrato={'exito': 'x', 'metrica': MET},
                      medidas=[{'valor': 1, 'detalle': 'una'}, {'valor': 3, 'detalle': 'tres'}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        n = len(a.prompts); o = hasta(h, o['id'], 'hecho')
        self.assertEqual(o['estado'], 'hecho'); self.assertEqual(len(a.prompts), n, 'cumplida la cifra, no gasta en planificar')
        self.assertEqual([m['valor'] for m in o['medidas']], [1, 3])

    def test_hecho_del_planificador_no_vale_si_la_metrica_no_se_cumple(self):
        h, a = hermes([{'hecho': True, 'resumen': 'ya está'}, {'resumen': 'otra palanca', 'tareas': [T('Probar otra cosa')]}],
                      contrato={'exito': 'x', 'metrica': MET}, medidas=[{'valor': 1, 'detalle': 'una'}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual(o['rondas'][0]['tareas'][0]['titulo'], 'Probar otra cosa')
        self.assertTrue(any('NO se cumple' in p for p in a.prompts))


class Debate(unittest.TestCase):
    def test_gana_codex(self):
        h, a = hermes([{'resumen': 'claude', 'tareas': [T('Plan de Claude')]}],
                      contras=[{'resumen': 'codex', 'tareas': [T('Plan de Codex')]}], arbitrajes=[{'elegido': 'codex', 'diferencia': 'Codex reutiliza lo que ya hay.'}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion'); r = o['rondas'][0]
        self.assertEqual(r['tareas'][0]['titulo'], 'Plan de Codex'); self.assertEqual(r['debate']['elegido'], 'codex')
        self.assertIn('Codex reutiliza', o['mensajes'][-1]['texto'])

    def test_mezcla_y_codex_caido(self):
        h, _ = hermes([{'resumen': 'c', 'tareas': [T('De Claude')]}], contras=[{'resumen': 'x', 'tareas': [T('De Codex')]}],
                      arbitrajes=[{'elegido': 'mezcla', 'diferencia': 'Lo mejor de cada una.', 'tareas': [T('De Claude'), T('De Codex')]}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual([t['titulo'] for t in o['rondas'][0]['tareas']], ['De Claude', 'De Codex'])
        h2, _ = hermes([{'resumen': 'c', 'tareas': [T('De Claude')]}], contras=[{'resumen': 'Codex no está disponible ahora.'}])
        o2 = hasta(h2, objetivo(h2)['id'], 'esperando_aprobacion')
        self.assertEqual(o2['rondas'][0]['tareas'][0]['titulo'], 'De Claude'); self.assertIn('no está disponible', o2['rondas'][0]['debate']['codex'])


class Motores(unittest.TestCase):
    def test_investigacion_contenido_y_anuncios_en_ensayo(self):
        h, a = hermes([{'resumen': 'r', 'tareas': [
            {'motor': 'investigacion', 'titulo': 'Comisiones', 'pregunta': 'Qué comisión paga cada empresa de fondeo a sus afiliados.'},
            {'motor': 'contenido', 'proyecto': 'beta', 'titulo': 'Guiones', 'encargo': 'Tres guiones de reel con el ranking real.'},
            {'motor': 'anuncios', 'proyecto': 'beta', 'titulo': 'Campaña de prueba', 'plan': {'objetivo': 'LEADS', 'presupuesto_diario_eur': 10}}]},
            {'hecho': True, 'resumen': 'fin'}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual([t['motor'] for t in o['rondas'][0]['tareas']], ['investigacion', 'contenido', 'anuncios'])
        h.aprobar(o['id']); self.assertEqual(a.encargos, [], 'no van al tablero del equipo')
        o = hasta(h, o['id'], 'hecho')
        self.assertEqual([m for m, _ in a.ejecutados], ['investigacion', 'contenido', 'anuncios'])
        self.assertIn('ensayo', o['rondas'][0]['tareas'][2]['resultado'])

    def test_verificacion_de_criterios_alimenta_el_plan(self):
        h, a = hermes([{'resumen': 'r', 'tareas': [T('Con criterios', criterios=['Registra el origen', 'Pasa la prueba'])]}, {'hecho': True, 'resumen': 'fin'}],
                      criterios_fallan={'Registra el origen'})
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        o = hasta(h, o['id'], 'hecho')
        v = o['rondas'][0]['verificacion']['tareas']['Con criterios']['criterios']
        self.assertEqual([c['ok'] for c in v], [False, True])
        self.assertTrue(any('1/2 criterios' in m['texto'] for m in o['mensajes']))
        self.assertTrue(any('"criterios_fallidos": ["Registra el origen"]' in p for p in a.prompts))


class Confianza(unittest.TestCase):
    def test_sin_subir_la_confianza_nunca_se_aprueba_sola(self):
        h, a = hermes([{'resumen': f'r{i}', 'tareas': [T(f'Tarea {i}')]} for i in range(5)])
        o = objetivo(h)
        for _ in range(4): o = hasta(h, o['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        self.assertEqual(h.nivel('beta'), 0, 'ganado, pero el máximo lo fija Owner (0)')
        self.assertEqual(hasta(h, o['id'], 'esperando_aprobacion')['estado'], 'esperando_aprobacion')

    def test_nivel_ganado_y_aprobacion_sola(self):
        h, a = hermes([{'resumen': f'r{i}', 'tareas': [T(f'Tarea {i}', dificultad='baja' if i != 4 else 'alta')]} for i in range(6)])
        h.fijar_confianza('beta', 1)
        o = objetivo(h)
        for _ in range(3): o = hasta(h, o['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        for _ in range(20):
            o = h.cargar(o['id'])
            if len(o['rondas']) == 4: break
            h.tick()
        self.assertEqual(h.nivel('beta'), 1, 'tres rondas bien hechas suben un nivel')
        self.assertIn('aprobada sola', o['rondas'][-1]['aprobada_por'])
        self.assertEqual(len(a.encargos), 4)
        o = hasta(h, o['id'], 'esperando_aprobacion')
        self.assertEqual(o['rondas'][-1]['tareas'][0]['dificultad'], 'alta', 'la difícil espera a Owner')
        h.descartar(o['id'], 'no'); self.assertEqual(h.nivel('beta'), 0, 'un descarte baja la confianza')

    def test_anuncios_e_importantes_nunca_solas(self):
        h, _ = hermes([{'resumen': 'r', 'tareas': [{'motor': 'anuncios', 'proyecto': 'beta', 'titulo': 'Campaña', 'plan': {'objetivo': 'LEADS'}}]}])
        c = h.confianza(); c['beta'] = {'maximo': 2, 'ganado': 2, 'racha': 0}; h._escribir('confianza.json', c)
        self.assertEqual(hasta(h, objetivo(h)['id'], 'esperando_aprobacion')['estado'], 'esperando_aprobacion')


class Noche(unittest.TestCase):
    def test_de_noche_no_avisa_y_por_la_manana_resume(self):
        reloj = {'t': DIA(3)()}
        h, a = hermes([{'resumen': 'r', 'tareas': [T('Tarea nocturna')]}], reloj=lambda: reloj['t'])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual(a.avisos, [], 'de madrugada no despierta')
        self.assertTrue(h._leer('noche.json', []))
        reloj['t'] = DIA(8, 30)(); hechos = h.tick()
        self.assertTrue(any(k == 'resumen' for k, _ in hechos))
        self.assertIn('Mientras dormías', a.avisos[-1]['texto'])
        self.assertTrue(a.avisos[-1]['botones'][0][0]['callback_data'].startswith('hm:'), 'con los botones para aprobar')
        h.tick(); self.assertEqual(len(a.avisos), 1, 'una vez al día')


class Topes(unittest.TestCase):
    def test_topes_por_objetivo_y_dia(self):
        h, _ = hermes([{'resumen': f'r{i}', 'tareas': [T(f'Tarea {i}')]} for i in range(5)], topes={'rondas_por_objetivo': 2})
        o = objetivo(h)
        for _ in range(2): o = hasta(h, o['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        self.assertIn('tope', hasta(h, o['id'], 'necesita_owner')['pregunta'])
        h2, _ = hermes([{'resumen': 'r', 'tareas': [T('Tarea uno')]}] * 3, topes={'rondas_por_dia': 1})
        hasta(h2, h2.nuevo('Primer objetivo bastante largo')['id'], 'esperando_aprobacion')
        b = hasta(h2, h2.nuevo('Segundo objetivo bastante largo')['id'], 'necesita_owner')
        self.assertIn('Tope diario', b['pregunta'])
        h2.responder(b['id'], 'sigue')
        self.assertEqual(hasta(h2, b['id'], 'esperando_aprobacion')['estado'], 'esperando_aprobacion')

    def test_presupuesto_del_objetivo(self):
        h, _ = hermes([{'resumen': f'r{i}', 'tareas': [T(f'Tarea {i}')]} for i in range(9)], contrato={'exito': 'x', 'metrica': None, 'presupuesto_llamadas': 6})
        o = objetivo(h)
        for _ in range(6):
            o = hasta(h, o['id'], 'esperando_aprobacion')
            if o['estado'] != 'esperando_aprobacion': break
            h.aprobar(o['id'])
        o = hasta(h, o['id'], 'necesita_owner')
        self.assertIn('presupuesto', o['pregunta'])
        h.responder(o['id'], 'sigue')
        self.assertGreater(h.cargar(o['id'])['contrato']['presupuesto_llamadas'], 6)

    def test_estancamiento(self):
        h, _ = hermes([{'resumen': f'r{i}', 'tareas': [T(f'Falla {i}')]} for i in range(4)], fallan={'Falla 0', 'Falla 1'})
        o = objetivo(h)
        for _ in range(2): o = hasta(h, o['id'], 'esperando_aprobacion'); h.aprobar(o['id'])
        o = hasta(h, o['id'], 'necesita_owner')
        self.assertIn('Dos rondas sin avanzar', o['pregunta'])
        h.responder(o['id'], 'sigue')
        self.assertEqual(hasta(h, o['id'], 'esperando_aprobacion')['estado'], 'esperando_aprobacion', '«sigue» insiste una vez')


class Movil(unittest.TestCase):
    def test_botones(self):
        h, a = hermes([{'resumen': 'r', 'tareas': [T('Tarea uno')]}, {'resumen': 'r2', 'tareas': [T('Tarea dos')]}])
        o = hasta(h, objetivo(h)['id'], 'esperando_aprobacion')
        self.assertEqual(a.avisos[-1]['botones'][0][0]['callback_data'], f'hm:{o["id"]}:a')
        self.assertIn('Aprobada', h.boton(f'hm:{o["id"]}:a'))
        self.assertIn('desde el móvil', a.encargos[0]['por'])
        with self.assertRaises(Parada): h.boton(f'hm:{o["id"]}:a')            # ya no hay ronda que aprobar
        with self.assertRaises(Parada): h.boton('hm:../x:a')
        o = hasta(h, o['id'], 'esperando_aprobacion'); h.boton(f'hm:{o["id"]}:p')
        self.assertEqual(h.cargar(o['id'])['estado'], 'parado')

    def test_categorias_del_ranking(self):
        self.assertEqual(_categoria(['alfred-os/app/x.tsx', 'a.ts']), 'interfaz')
        self.assertEqual(_categoria(['test/x.test.js']), 'pruebas')
        self.assertEqual(_categoria(['equipo/equipo.py']), 'motor')
        self.assertEqual(_categoria(['src/a.js']), 'lógica')


class Servidor(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('hermes_cli', Path(__file__).resolve().parent / 'hermes.py')
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        self.h, _ = hermes([{'resumen': 'r', 'tareas': [T('Tarea uno')]}])
        self.srv = mod.crear_servidor(self.h, 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.srv.server_port}'

    def tearDown(self): self.srv.shutdown(); self.srv.server_close()

    def post(self, ruta, body, cabeceras=None):
        req = urllib.request.Request(self.url + ruta, json.dumps(body).encode(), {'Content-Type': 'application/json', **(cabeceras or {})})
        try:
            with urllib.request.urlopen(req) as r: return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e: return e.code, json.loads(e.read())

    def test_solo_desde_la_pagina_local(self):
        self.assertEqual(self.post('/api/objetivo', {'texto': 'Objetivo enviado sin cabecera'})[0], 403)
        self.assertEqual(self.post('/api/objetivo', {'texto': 'Objetivo desde otra web'}, {'X-Hermes': '1', 'Origin': 'https://malo.example'})[0], 403)
        code, j = self.post('/api/objetivo', {'texto': 'Objetivo desde el chat local', 'proyecto': 'beta'}, {'X-Hermes': '1', 'Origin': self.url})
        self.assertEqual(code, 200)
        with urllib.request.urlopen(self.url + '/api/estado') as r: est = json.loads(r.read())
        self.assertEqual(est['objetivos'][0]['id'], j['objetivo']['id'])
        self.assertIn('beta', est['confianza']); self.assertIn('tabla', est['ranking'])
        code, j = self.post('/api/aprobar', {'id': est['objetivos'][0]['id']}, {'X-Hermes': '1'})
        self.assertEqual(code, 400); self.assertIn('aprobación', j['error'])
        self.assertEqual(self.post('/api/confianza', {'proyecto': 'beta', 'maximo': 1}, {'X-Hermes': '1'})[0], 200)
        self.assertEqual(self.h.confianza()['beta']['maximo'], 1)
        self.assertEqual(self.post('/api/confianza', {'proyecto': 'beta', 'maximo': 7}, {'X-Hermes': '1'})[0], 400)
        try: urllib.request.urlopen(self.url + '/api/captura/h_00000000/../../x.png'); self.fail('no debe servir rutas fuera')
        except urllib.error.HTTPError as e: self.assertEqual(e.code, 404)


if __name__ == '__main__':
    unittest.main()
