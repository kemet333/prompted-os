"""Unit tests for the team engine. Run: ~/Equipo/venv/bin/python -m unittest discover -s equipo"""
import inspect, io, json, os, re, shutil, subprocess, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

# Fixed example projects, never this computer's real configuration (paths, boards).
os.environ.setdefault('EQUIPO_CONFIG', str(Path(__file__).resolve().with_name('test_proyectos.json')))
import equipo

# No test may reach Owner's real data folder or send him anything: run_all() now writes and sends the morning to-do
# (19/10: a run_all test with a simulated date sent him a real «Hoy, jue 15»). Tests that need them patch these again.
_ISOLATION = tempfile.TemporaryDirectory()
_GUARDS = [mock.patch.object(equipo, 'WORKSPACE', Path(_ISOLATION.name)), mock.patch.object(equipo, 'TELEGRAM_LINK', Path(_ISOLATION.name) / 'telegram.json'),
           mock.patch.object(equipo, 'telegram_send', lambda *a, **k: None),   # telegram() finds no linked chat in the temporary folder
           mock.patch.object(equipo, 'carga', lambda: 0.0),   # the real load of the Mac running the tests must not change what they see
           mock.patch.object(equipo, 'en_cristiano', lambda text, botones=None: text),   # tests never ask Claude to reword a notice
           mock.patch.object(equipo, 'buenos_dias', lambda at=None: None)]               # nor write a real good morning from Owner's notes
def setUpModule():
    for g in _GUARDS: g.start()
def tearDownModule():
    for g in _GUARDS: g.stop()
    _ISOLATION.cleanup()
# The tests describe Codex arriving on 10/10 at 14:30, whatever date modelos.json holds today.
equipo.MODEL_POLICY = {**equipo.MODEL_POLICY, 'codex': {**equipo.MODEL_POLICY.get('codex', {}), 'desde': '2026-10-10 14:30'}}


def solo_claude():
    """Claude builds: the model checks must not depend on the date or on this Mac's Codex."""
    return mock.patch.object(equipo, 'codex_ready', lambda: False)


class FusionarExperimentos(unittest.TestCase):
    ahora = datetime.fromisoformat('2026-10-20T02:00:00+02:00')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        patches = {'STATE': self.state, 'now': mock.Mock(return_value=self.ahora),
                   'fusionar_project': mock.Mock(return_value='fusionada en main'),
                   'experimentos': mock.Mock(return_value={'por_lanzar': []}),
                   'project': mock.Mock(return_value={'name': 'Owner OS'}),
                   'log': mock.Mock(), 'notify': mock.Mock(), 'mejoras_vigentes': mock.Mock(return_value=[])}
        for name, value in patches.items():
            patch = mock.patch.object(equipo, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def test_marks_only_unmerged_experiments_of_successful_project(self):
        entry = {'proyecto': 'prompted-os', 'sin_fusionar': True}
        registro = {'t_a': entry, 't_b': {**entry, 'proyecto': 'norte'},
                    't_c': {**entry, 'fusionado': '2026-10-18T12:00:00+02:00'},
                    't_d': {'proyecto': 'prompted-os'}, 't_rota': None}
        equipo.save(self.state / 'experimentos.json', registro)
        equipo.fusionar_project.side_effect = lambda pid: 'fusionada en main' if pid == 'prompted-os' else 'conflicto: necesita a Owner'
        with mock.patch.object(equipo, 'lanzamientos', wraps=equipo.lanzamientos) as leer:
            equipo.fusionar(['prompted-os', 'norte'])
        leer.assert_called_once_with()
        self.assertEqual(equipo.lanzamientos(), {**registro, 't_a': {**entry, 'fusionado': self.ahora.isoformat(timespec='seconds')}})

    def test_conflict_does_not_mark_or_save_experiments(self):
        registro = {'t_a': {'proyecto': 'prompted-os', 'sin_fusionar': True}}
        equipo.save(self.state / 'experimentos.json', registro)
        equipo.fusionar_project.return_value = 'conflicto: necesita a Owner'
        with mock.patch.object(equipo, 'save', wraps=equipo.save) as guardar:
            equipo.fusionar(['prompted-os'])
        self.assertEqual(equipo.lanzamientos(), registro)
        self.assertEqual([c.args[0] for c in guardar.call_args_list], [self.state / 'fusiones' / 'ultima.json'])

    def test_registry_write_failure_is_logged_without_interrupting_notice(self):
        equipo.save(self.state / 'experimentos.json', {'t_a': {'proyecto': 'prompted-os', 'sin_fusionar': True}})
        save = equipo.save
        def guardar(path, value):
            if path == self.state / 'experimentos.json': raise OSError('sin espacio')
            return save(path, value)
        with mock.patch.object(equipo, 'save', side_effect=guardar):
            self.assertEqual(equipo.fusionar(['prompted-os']), {'prompted-os': 'fusionada en main'})
        equipo.log.assert_any_call('equipo', 'No se han podido actualizar los experimentos tras fusionar:', 'sin espacio')
        equipo.notify.assert_called_once_with('Mejoras del equipo', 'Owner OS: fusionada en main')

    def test_success_lists_two_pending_titles_once(self):
        equipo.experimentos.return_value = {'por_lanzar': [{'titulo': 'A'}, {'titulo': 'B'}]}
        equipo.fusionar(['prompted-os'])
        equipo.experimentos.assert_called_once_with('prompted-os')
        equipo.notify.assert_called_once_with('Mejoras del equipo', 'Owner OS: fusionada en main\n'
                                             'Owner OS: ya en la versión principal, listo para lanzar: «A», «B» (/lote)')

    def test_success_without_pending_keeps_notice(self):
        equipo.fusionar(['prompted-os'])
        equipo.experimentos.assert_called_once_with('prompted-os')
        equipo.notify.assert_called_once_with('Mejoras del equipo', 'Owner OS: fusionada en main')

    def test_success_lists_remaining_pending_titles_count(self):
        equipo.experimentos.return_value = {'por_lanzar': [{'titulo': title} for title in 'ABCDE'], 'por_lanzar_total': 7}
        equipo.fusionar(['prompted-os'])
        equipo.notify.assert_called_once_with('Mejoras del equipo', 'Owner OS: fusionada en main\n'
                                             'Owner OS: ya en la versión principal, listo para lanzar: «A», «B», «C», «D», «E» y 2 más (/lote)')

    def test_success_with_five_pending_titles_has_no_remaining_count(self):
        equipo.experimentos.return_value = {'por_lanzar': [{'titulo': title} for title in 'ABCDE'], 'por_lanzar_total': 5}
        equipo.fusionar(['prompted-os'])
        equipo.notify.assert_called_once_with('Mejoras del equipo', 'Owner OS: fusionada en main\n'
                                             'Owner OS: ya en la versión principal, listo para lanzar: «A», «B», «C», «D», «E» (/lote)')
        self.assertNotIn('más', equipo.notify.call_args.args[1])

    def test_unsuccessful_results_do_not_read_experiments(self):
        for result in ('conflicto: necesita a Owner o a Hermes', 'pruebas peores que en la base: equipo',
                       'error: fallo', 'nada que fusionar'):
            with self.subTest(result=result):
                equipo.fusionar_project.return_value = result
                equipo.notify.reset_mock()
                equipo.fusionar(['prompted-os'])
                equipo.experimentos.assert_not_called()
                if result == 'nada que fusionar':
                    equipo.notify.assert_not_called()
                else:
                    equipo.notify.assert_called_once_with('Mejoras del equipo', f'Owner OS: {result}')

    def test_experiment_failure_is_logged_and_notice_still_sent(self):
        equipo.experimentos.side_effect = RuntimeError('registro ilegible')
        equipo.fusionar(['prompted-os'])
        equipo.experimentos.assert_called_once_with('prompted-os')
        equipo.log.assert_any_call('prompted-os', 'No se han podido leer los experimentos tras fusionar:', 'registro ilegible')
        equipo.notify.assert_called_once_with('Mejoras del equipo', 'Owner OS: fusionada en main')

    def test_return_and_saved_results_stay_unchanged_for_multiple_projects(self):
        results = {'prompted-os': 'fusionada en main', 'norte': 'fusionada en main y subida a GitHub',
                   'tf': 'conflicto: necesita a Owner o a Hermes', 'otro': 'nada que fusionar'}
        equipo.fusionar_project.side_effect = results.__getitem__
        equipo.project.side_effect = lambda pid: {'name': pid}
        equipo.experimentos.return_value = {'por_lanzar': [{'titulo': 'A'}, {'titulo': 'B'}]}
        self.assertEqual(equipo.fusionar(list(results)), results)
        self.assertEqual(json.loads((self.state / 'fusiones' / 'ultima.json').read_text()),
                         {'fecha': self.ahora.isoformat(timespec='seconds'), 'resultados': results})
        self.assertEqual(equipo.experimentos.call_args_list, [mock.call('prompted-os'), mock.call('norte')])
        equipo.notify.assert_called_once_with('Mejoras del equipo',
                                             'prompted-os: fusionada en main\nnorte: fusionada en main y subida a GitHub\n'
                                             'tf: conflicto: necesita a Owner o a Hermes\n'
                                             'prompted-os: ya en la versión principal, listo para lanzar: «A», «B» (/lote)\n'
                                             'norte: ya en la versión principal, listo para lanzar: «A», «B» (/lote)')


class Validation(unittest.TestCase):
    def test_safe_paths(self):
        self.assertTrue(equipo.safe_rel('alfred-os/app/x.tsx'))
        for bad in ('/etc/passwd', '../x', 'a/../../b', '.env', 'conf/.env.local', 'node_modules/x.js', 'CLAUDE.md', '.git/config', ''):
            self.assertFalse(equipo.safe_rel(bad), bad)

    def test_task_shape(self):
        ok = {'titulo': 'Una tarea', 'porque': 'x', 'detalle': 'Criterios de aceptación concretos', 'archivos': ['alfred-os/app/a.tsx'], 'pruebas': ['tipos'], 'depende_de': []}
        self.assertEqual(equipo.validate_task('prompted-os', ok, 0)['archivos'], ['alfred-os/app/a.tsx'])
        for change in ({'pruebas': ['rm -rf']}, {'archivos': ['../fuera']}, {'archivos': []}, {'depende_de': [0]}, {'titulo': 'x'}):
            with self.assertRaises(AssertionError, msg=change):
                equipo.validate_task('prompted-os', {**ok, **change}, 0)

    def test_long_title_is_trimmed_not_fatal(self):
        long = {'titulo': 'Prueba de integración de KC_SEED: misma semilla en dos arranques da las mismas posiciones, y otra cosa más larga que sigue y sigue hasta pasar el límite', 'porque': 'x',
                'detalle': 'Criterios de aceptación concretos', 'archivos': ['alfred-os/app/a.tsx'], 'pruebas': ['tipos'], 'depende_de': []}
        out = equipo.validate_task('prompted-os', long, 0)
        self.assertLessEqual(len(out['titulo']), 120)
        self.assertTrue(out['titulo'].endswith('…'))

    def test_json_block(self):
        self.assertEqual(equipo.json_block('Texto\n```json\n[{"a": 1}]\n```\nfin'), [{'a': 1}])
        self.assertEqual(equipo.json_block('respuesta {"decision": "aprobar"}'), {'decision': 'aprobar'})
        with self.assertRaises(equipo.Stop):
            equipo.json_block('sin json')


class SiguienteExperimento(unittest.TestCase):
    ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 %'}
    ahora = datetime.fromisoformat('2026-10-19T12:00:00+02:00')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.propuesta = {'titulo': 'Siguiente experimento', 'detalle': 'Criterios de aceptación concretos',
                          'archivos': ['equipo/equipo.py'], 'pruebas': ['equipo'], 'lanzamiento': self.ficha, 'viene_de': 't_padre'}
        self.padre = {'tarea': 't_padre', 'titulo': 'Experimento padre', 'decision': 'seguir', 'hora': self.ahora.isoformat()}
        patches = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'now', lambda: self.ahora),
                   mock.patch.object(equipo, 'lessons_text', lambda pid, role: ''), mock.patch.object(equipo, 'mesa_load', lambda: {}),
                   mock.patch.object(equipo, 'metrics', lambda pid: {'tareas': 0})]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [self.padre])

    def test_validation_keeps_only_a_followed_parent_with_a_launch_card(self):
        self.assertEqual(equipo.validate_task('prompted-os', self.propuesta, 0)['viene_de'], 't_padre')
        for change in ({'viene_de': 't_ausente'}, {'viene_de': '../mal'}, {'viene_de': 123},
                       {'lanzamiento': None}, {'lanzamiento': {'que': 'Incompleta'}}):
            with self.subTest(change=change):
                self.assertNotIn('viene_de', equipo.validate_task('prompted-os', {**self.propuesta, **change}, 0))
        sin_ficha = {k: v for k, v in self.propuesta.items() if k != 'lanzamiento'}
        self.assertNotIn('viene_de', equipo.validate_task('prompted-os', sin_ficha, 0))
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [{**self.padre, 'decision': 'parar'}])
        self.assertNotIn('viene_de', equipo.validate_task('prompted-os', self.propuesta, 0))
        sin_padre = {k: v for k, v in self.propuesta.items() if k != 'viene_de'}
        with mock.patch.object(equipo, 'resultados') as leer:
            self.assertNotIn('viene_de', equipo.validate_task('prompted-os', sin_padre, 0))
        leer.assert_not_called()

    def test_notice_keeps_valid_parent_only_on_first_notice_and_result_keeps_it(self):
        task = {'id': 't_hijo', 'title': 'Siguiente experimento', 'body': json.dumps(self.propuesta)}
        with mock.patch.object(equipo, 'telegram') as telegram, mock.patch.object(equipo, 'post') as post:
            texto = equipo.avisar_lanzamiento('prompted-os', task)
            registro = equipo.lanzamientos()
            self.assertEqual(registro['t_hijo']['viene_de'], 't_padre')
            telegram.assert_called_once()
            self.assertEqual(telegram.call_args.args[0], texto)
            self.assertEqual(post.call_args.args[2], texto)
            equipo.avisar_lanzamiento('prompted-os', {**task, 'body': json.dumps({**self.propuesta, 'viene_de': 't_otro'})})
            self.assertEqual(equipo.lanzamientos(), registro)
            for parent in ('../mal', 123, None):
                equipo.avisar_lanzamiento('prompted-os', {**task, 'id': 't_invalido', 'body': json.dumps({**self.propuesta, 'viene_de': parent})})
                self.assertNotIn('viene_de', equipo.lanzamientos()['t_invalido'])
                equipo.save(self.state / 'experimentos.json', registro)
        with mock.patch.object(equipo, 'tasks', return_value=[task]):
            resultado = equipo.registrar_resultado('prompted-os', '42 registros de 600 visitas', 't_hijo')
        self.assertEqual(resultado['viene_de'], 't_padre')
        self.assertEqual(equipo.resultados('prompted-os')[-1], resultado)

    def test_counts_followed_results_in_window_with_an_announced_child(self):
        self.assertEqual(equipo.experimentos('prompted-os')['seguir_con_siguiente'], 0)
        hijo = {'proyecto': 'prompted-os', 'viene_de': 't_padre'}
        equipo.save(self.state / 'experimentos.json', {'t_hijo': hijo})
        self.assertEqual(equipo.experimentos('prompted-os')['seguir_con_siguiente'], 0)
        hijo['avisado'] = self.ahora.isoformat()
        equipo.save(self.state / 'experimentos.json', {'t_hijo': hijo, 't_otro_hijo': hijo})
        with mock.patch.object(equipo, 'lanzamientos', wraps=equipo.lanzamientos) as leer:
            self.assertEqual(equipo.experimentos('prompted-os')['seguir_con_siguiente'], 1)
        leer.assert_called_once()
        for change in ({'decision': 'parar'}, {'hora': 'rota'}, {'hora': '2026-10-01T12:00:00+02:00'}, {'tarea': None}):
            with self.subTest(change=change):
                equipo.save(self.state / 'experimentos' / 'prompted-os.json', [{**self.padre, **change}])
                self.assertEqual(equipo.experimentos('prompted-os')['seguir_con_siguiente'], 0)

    def test_planner_names_parent_and_followed_count(self):
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Si propones el siguiente paso de una de ellas, añade a la propuesta "viene_de" con el id de su tarea: «Experimento padre» → t_padre', signals)
        self.assertIn('Con «seguir» (7 días): 1, con siguiente paso avisado: 0.', signals)
        equipo.save(self.state / 'experimentos.json', {'t_hijo': {'proyecto': 'prompted-os', 'viene_de': 't_padre', 'avisado': self.ahora.isoformat()}})
        self.assertIn('Con «seguir» (7 días): 1, con siguiente paso avisado: 1.', equipo.planner_signals('prompted-os'))

    def test_plan_prompt_formats_with_plan_project_arguments(self):
        prompt = equipo.PLAN_PROMPT.format(validar=equipo.VALIDAR, estrategia='', name='Owner OS', goal='Validar',
                                          n=equipo.LIMITS['tasks_per_batch'], max_files=equipo.LIMITS['max_files'],
                                          tests='equipo', board='[]', followups='[]', related='', learning='', context='')
        self.assertIn('"viene_de": "<id de esa tarea>"', prompt)


class Lessons(unittest.TestCase):
    def test_add_reinforce_retire_and_dedupe(self):
        current = [{'id': 'L1', 'para': 'constructor', 'texto': 'Actualiza siempre la prueba que cubre el archivo tocado.', 'refuerzos': 0, 'ultima': '2026-10-01'},
                   {'id': 'L2', 'para': 'planificador', 'texto': 'Divide las tareas de interfaz en tareas de menos de 100 líneas.', 'refuerzos': 0, 'ultima': '2026-10-01'}]
        out = equipo.merge_lessons(current, {'refuerzan': ['L1'], 'retirar': ['L2'], 'nuevas': [
            {'para': 'revisor', 'texto': 'Comprueba que el cambio no toca la pantalla Alfred.', 'evidencia': ['t_1']},
            {'para': 'constructor', 'texto': 'actualiza siempre la prueba que cubre el archivo tocado.'},   # duplicate
            {'para': 'nadie', 'texto': 'Rol inválido, se descarta entera.'},
            {'para': 'todos', 'texto': 'corta'}]}, today_='2026-10-05')
        self.assertEqual([l['id'] for l in out], ['L1', 'L3'])
        self.assertEqual(out[0]['refuerzos'], 1)
        self.assertEqual(out[1]['evidencia'], ['t_1'])

    def test_cap_keeps_the_most_reinforced(self):
        current = [{'id': f'L{i}', 'para': 'todos', 'texto': f'Lección número {i} con texto suficiente', 'refuerzos': i % 3, 'ultima': '2026-10-0' + str(1 + i % 5)} for i in range(1, 25)]
        out = equipo.merge_lessons(current, {})
        self.assertEqual(len(out), equipo.LESSON_CAP)
        self.assertTrue(all(l['refuerzos'] >= 1 for l in out[:10]) or len({l['refuerzos'] for l in out}) > 1)


class Metrics(unittest.TestCase):
    def test_counts_from_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, estado, ciclos):
                (state / 'runs' / tid).mkdir(parents=True)
                (state / 'runs' / tid / 'receipt.json').write_text(json.dumps({'tarea': tid, 'proyecto': 'prompted-os', 'estado': estado, 'commit': 'abc' if estado == 'integrada' else None,
                    'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': ciclos}))
            ok, bad = {'prueba': 'tipos', 'ok': True}, {'prueba': 'tipos', 'ok': False}
            receipt('t_a', 'integrada', [{'pruebas': [ok], 'revision': {'decision': 'aprobar'}}])
            receipt('t_b', 'integrada', [{'pruebas': [bad]}, {'pruebas': [ok], 'revision': {'decision': 'aprobar'}}])
            receipt('t_c', 'bloqueada', [{'pruebas': [ok], 'revision': {'decision': 'cambios'}, 'fuera_de_alcance': ['x']}])
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: c[:1]):
                m = equipo.metrics('prompted-os')
        self.assertEqual((m['tareas'], m['integradas'], m['a_la_primera'], m['bloqueadas']), (3, 2, 1, 1))
        self.assertEqual((m['cambios_pedidos'], m['pruebas_fallidas'], m['fuera_de_alcance'], m['fusionadas_por_ti']), (1, 1, 1, 1))
        self.assertEqual(m['decisiones_tuyas'], 0)
        self.assertIsNone(m['minutos_hasta_decision'])

    def test_counts_base_checks_red_bases_and_mean_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, estado, ciclos, error=None):
                (state / 'runs' / tid).mkdir(parents=True)
                (state / 'runs' / tid / 'receipt.json').write_text(json.dumps({'tarea': tid, 'proyecto': 'prompted-os', 'estado': estado, 'commit': None, 'error': error,
                    'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': ciclos}))
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                receipt('t_a', 'integrada', [{}])
                m = equipo.metrics('prompted-os')
                self.assertEqual((m['bases_miradas'], m['bloqueadas_base_roja'], m['segundos_base_medios']), (0, 0, None))
                receipt('t_b', 'bloqueada', [{'base_segundos': 30}], 'base roja: unitarias ya falla en equipo/x sin este cambio (FAIL a)')
                receipt('t_c', 'integrada', [{'base_segundos': 10}, {}])
                m = equipo.metrics('prompted-os')
        self.assertEqual((m['bases_miradas'], m['bloqueadas_base_roja'], m['segundos_base_medios']), (2, 1, 20.0))

    def test_planner_signals_base_check_line(self):
        signals = self.signals_with_metrics(bases_miradas=2, bloqueadas_base_roja=1, segundos_base_medios=20.0)
        self.assertIn('Comprobaciones de la prueba en la rama base (7 días): 2, 1 acabaron en «base roja», media 20.0 s. Con menos de 5 no es señal.', signals)
        signals = self.signals_with_metrics(bases_miradas=5, bloqueadas_base_roja=1, segundos_base_medios=20.0)
        self.assertIn('media 20.0 s.', signals)
        self.assertNotIn('Con menos de 5 no es señal', signals)
        self.assertNotIn('Comprobaciones de la prueba en la rama base', self.signals_with_metrics(bases_miradas=0, bloqueadas_base_roja=0, segundos_base_medios=None))

    def test_receipts_window_uses_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, fin):
                (state / 'runs' / tid).mkdir(parents=True)
                inicio = datetime.fromisoformat(fin) - timedelta(hours=1)
                (state / 'runs' / tid / 'receipt.json').write_text(json.dumps({'tarea': tid, 'proyecto': 'prompted-os', 'estado': 'integrada', 'commit': None,
                    'inicio': inicio.isoformat(), 'fin': fin, 'ciclos': []}))
            receipt('t_nuevo', '2026-10-24T12:00:00+02:00')
            receipt('t_viejo', '2026-10-17T11:00:00+02:00')
            receipt('t_limite', '2026-10-18T13:00:00+02:00')
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'now', return_value=datetime(2026, 10, 25, 12, tzinfo=equipo.MADRID)), \
                 mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                found = [r['tarea'] for r in equipo.receipts('prompted-os', since_hours=7 * 24)]
                m = equipo.metrics('prompted-os')
        self.assertEqual(found, ['t_limite', 't_nuevo'])
        self.assertEqual((m['tareas'], m['integradas']), (2, 2))

    def test_counts_already_done_and_infra_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, estado, ciclos, error=None):
                (state / 'runs' / tid).mkdir(parents=True)
                (state / 'runs' / tid / 'receipt.json').write_text(json.dumps({'tarea': tid, 'proyecto': 'prompted-os', 'estado': estado, 'commit': None, 'error': error,
                    'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': ciclos}))
            ok, bad = {'prueba': 'tipos', 'ok': True}, {'prueba': 'tipos', 'ok': False}
            receipt('t_a', 'bloqueada', [], 'ya estaba hecho según el constructor: x')
            receipt('t_b', 'bloqueada', [], 'agotados los intentos')
            receipt('t_c', 'bloqueada', [{'pruebas': [ok], 'pruebas_infra': {'motivo': 'arranque'}}], 'agotados los intentos')
            receipt('t_d', 'bloqueada', [{'pruebas': [ok, bad], 'pruebas_infra': {'motivo': 'arranque'}}], 'agotados los intentos')
            receipt('t_e', 'bloqueada', [{'pruebas': [bad], 'pruebas_mixtas': True}, {'pruebas': [ok]}], 'agotados los intentos')
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                m = equipo.metrics('prompted-os')
        self.assertEqual((m['ya_estaba_hecho'], m['reintentos_infra'], m['reintentos_infra_en_verde']), (1, 2, 1))
        self.assertEqual(m['pruebas_mixtas'], 1)

    def test_counts_scope_blocked_receipts_and_their_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, estado, ciclos, error=None):
                (state / 'runs' / tid).mkdir(parents=True)
                (state / 'runs' / tid / 'receipt.json').write_text(json.dumps({'tarea': tid, 'proyecto': 'prompted-os', 'estado': estado, 'commit': None, 'error': error,
                    'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': ciclos}))
            receipt('t_a', 'bloqueada', [{}, {}], 'falta permitir equipo/x.py: archivo fuera del alcance')
            receipt('t_b', 'bloqueada', [{}], 'falta permitir equipo/y.py: archivo fuera del alcance')
            receipt('t_c', 'bloqueada', [{}, {}], 'agotados los intentos')
            receipt('t_d', 'integrada', [{}])
            receipt('t_e', 'bloqueada', [], None)
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                m = equipo.metrics('prompted-os')
        self.assertEqual((m['bloqueadas_por_alcance'], m['intentos_en_bloqueadas_por_alcance']), (2, 3))

    def test_planner_signals_names_already_done_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, title, error):
                equipo.save(state / 'runs' / tid / 'receipt.json', {'tarea': tid, 'proyecto': 'prompted-os', 'titulo': title,
                    'estado': 'bloqueada', 'error': error, 'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': []})
            receipt('t_a', 'Motor: algo ya hecho', 'ya estaba hecho según el constructor: x')
            receipt('t_b', 'Motor: otra cosa', 'agotados los intentos')
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                signals = equipo.planner_signals('prompted-os')
        line = next(line for line in signals.splitlines() if 'encontró ya hechas' in line)
        self.assertIn('Tareas que el constructor encontró ya hechas (7 días): «Motor: algo ya hecho».', line)
        self.assertNotIn('«Motor: otra cosa»', line)

    def test_planner_signals_omits_already_done_line_without_matching_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            equipo.save(state / 'runs' / 't_a' / 'receipt.json', {'tarea': 't_a', 'proyecto': 'prompted-os', 'titulo': 'Motor: otra cosa',
                'estado': 'bloqueada', 'error': 'agotados los intentos', 'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': []})
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                signals = equipo.planner_signals('prompted-os')
        self.assertNotIn('encontró ya hechas', signals)

    def test_planner_signals_names_only_the_five_most_recent_already_done_receipts(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            start = equipo.now()
            for n in range(7):
                moment = start + timedelta(minutes=n)
                equipo.save(state / 'runs' / f't_{n}' / 'receipt.json', {'tarea': f't_{n}', 'proyecto': 'prompted-os', 'titulo': f'Motor: tarea {n}',
                    'estado': 'bloqueada', 'error': 'ya estaba hecho según el constructor: x', 'inicio': moment.isoformat(), 'fin': moment.isoformat(), 'ciclos': []})
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                signals = equipo.planner_signals('prompted-os')
        line = next(line for line in signals.splitlines() if 'encontró ya hechas' in line)
        self.assertNotIn('«Motor: tarea 0»', line)
        self.assertNotIn('«Motor: tarea 1»', line)
        for n in range(2, 7): self.assertIn(f'«Motor: tarea {n}»', line)

    def test_planner_signals_names_tasks_blocked_by_a_red_base(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            def receipt(tid, title, error):
                equipo.save(state / 'runs' / tid / 'receipt.json', {'tarea': tid, 'proyecto': 'prompted-os', 'titulo': title,
                    'estado': 'bloqueada', 'error': error, 'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': []})
            receipt('t_a', 'Motor: prueba roja', 'base roja: equipo ya falla en equipo/prompted-os sin este cambio (FAIL x); hay que arreglar esa prueba en otra tarea')
            receipt('t_b', 'Motor: otra cosa', 'agotados los intentos: FAIL x')
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                signals = equipo.planner_signals('prompted-os')
        line = next(line for line in signals.splitlines() if 'ya fallaba antes del cambio' in line)
        self.assertIn('Bloqueadas porque la prueba ya fallaba antes del cambio (7 días): «Motor: prueba roja».', line)
        self.assertIn('Propón primero una tarea pequeña que arregle esa prueba', line)
        self.assertNotIn('«Motor: otra cosa»', line)

    def test_planner_signals_omits_red_base_line_for_other_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            equipo.save(state / 'runs' / 't_a' / 'receipt.json', {'tarea': 't_a', 'proyecto': 'prompted-os', 'titulo': 'Motor: otra cosa',
                'estado': 'bloqueada', 'error': 'agotados los intentos', 'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(), 'ciclos': []})
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                signals = equipo.planner_signals('prompted-os')
        self.assertNotIn('ya fallaba antes del cambio', signals)


    def test_project_brief_uses_receipt_error_for_blocked_task(self):
        task = {'id': 't_1', 'title': 'Motor: bloqueada', 'status': 'blocked'}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); equipo.save(state / 'runs' / 't_1' / 'receipt.json', {'estado': 'bloqueada', 'error': 'agotados los intentos: X'})
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), \
                 mock.patch.object(equipo, 'tasks', lambda pid: [task]), mock.patch.object(equipo, 'metrics', lambda pid: {}), \
                 mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
                 mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'ficha', lambda pid: None):
                brief = equipo.project_brief('prompted-os')
        self.assertEqual(brief['bloqueadas'], ['Motor: bloqueada: agotados los intentos: X'])

    def test_motivo_visible_prefers_the_board_error_without_spaces(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); equipo.save(state / 'runs' / 't_1' / 'receipt.json', {'estado': 'bloqueada', 'error': 'recibo'})
            with mock.patch.object(equipo, 'STATE', state):
                self.assertEqual(equipo.motivo_visible({'id': 't_1', 'last_failure_error': '  tablero \n'}), 'tablero')

    def test_motivo_visible_uses_the_error_of_a_blocked_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); equipo.save(state / 'runs' / 't_1' / 'receipt.json', {'estado': 'bloqueada', 'error': ' agotados los intentos: X '})
            with mock.patch.object(equipo, 'STATE', state):
                self.assertEqual(equipo.motivo_visible({'id': 't_1', 'last_failure_error': '  '}), 'agotados los intentos: X')

    def test_motivo_visible_says_the_fixed_text_without_any_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)):
                self.assertEqual(equipo.motivo_visible({'id': 't_1', 'last_failure_error': None}), 'bloqueada antes del arreglo, motivo no registrado')
                equipo.save(Path(tmp) / 'runs' / 't_1' / 'receipt.json', {'estado': 'bloqueada', 'error': ' '})
                self.assertEqual(equipo.motivo_visible({'id': 't_1'}), 'bloqueada antes del arreglo, motivo no registrado')

    def test_motivo_visible_ignores_the_error_of_a_receipt_that_is_not_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); equipo.save(state / 'runs' / 't_1' / 'receipt.json', {'estado': 'integrada', 'error': 'error viejo'})
            with mock.patch.object(equipo, 'STATE', state):
                self.assertEqual(equipo.motivo_visible({'id': 't_1', 'last_failure_error': None}), 'bloqueada antes del arreglo, motivo no registrado')

    def test_status_blocked_task_without_a_board_reason_shows_the_fixed_text(self):
        ts = [{'id': 't_blocked', 'title': 'Motor: A', 'status': 'blocked', 'last_failure_error': None, 'created_at': equipo.time.time()}]
        config = {**equipo.CONFIG, 'projects': {'prompted-os': equipo.CONFIG['projects']['prompted-os']}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'CONFIG', config), mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'tasks', lambda pid: ts), mock.patch.object(equipo, 'lessons', lambda pid: []), \
                 mock.patch.object(equipo, 'metrics', lambda pid: {}), mock.patch.object(equipo, 'pending_merge', lambda pid: 0), \
                 mock.patch.object(equipo, 'mejoras_seguras', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
                 mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'hermes_overview', lambda: {}), \
                 mock.patch.object(equipo, 'modelos_hoy', lambda: {}), mock.patch.object(equipo, 'tablero', lambda: []), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}):
                project = equipo.status()['proyectos']['prompted-os']
        self.assertEqual([t['motivo'] for t in project['bloqueadas']], ['bloqueada antes del arreglo, motivo no registrado'])

    def _blocked_since(self, task):
        config = {**equipo.CONFIG, 'projects': {'prompted-os': equipo.CONFIG['projects']['prompted-os']}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'CONFIG', config), mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'tasks', lambda pid: [task]), mock.patch.object(equipo, 'lessons', lambda pid: []), \
                 mock.patch.object(equipo, 'metrics', lambda pid: {}), mock.patch.object(equipo, 'pending_merge', lambda pid: 0), \
                 mock.patch.object(equipo, 'mejoras_seguras', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
                 mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'hermes_overview', lambda: {}), \
                 mock.patch.object(equipo, 'modelos_hoy', lambda: {}), mock.patch.object(equipo, 'tablero', lambda: []), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}):
                project = equipo.status()['proyectos']['prompted-os']
        return [t['desde'] for t in project['bloqueadas']]

    def test_status_blocked_task_says_since_when_from_completed_at(self):
        completed, created = equipo.time.time() - 3 * 86400, equipo.time.time() - 5 * 86400
        task = {'id': 't_blocked', 'title': 'Motor: A', 'status': 'blocked', 'last_failure_error': None, 'created_at': created, 'completed_at': completed}
        self.assertEqual(self._blocked_since(task), [equipo.iso(completed)])

    def test_status_blocked_task_without_completed_at_says_since_when_from_created_at(self):
        created = equipo.time.time() - 5 * 86400
        task = {'id': 't_blocked', 'title': 'Motor: A', 'status': 'blocked', 'last_failure_error': None, 'created_at': created}
        self.assertEqual(self._blocked_since(task), [equipo.iso(created)])

    def test_repeated_tests_are_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                self.assertEqual(equipo.metrics('prompted-os')['repetidas'], 0)
                (state / 'runs' / 't_a').mkdir(parents=True)
                (state / 'runs' / 't_a' / 'receipt.json').write_text(json.dumps({'tarea': 't_a', 'proyecto': 'prompted-os', 'estado': 'integrada', 'commit': 'abc',
                    'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(),
                    'ciclos': [{'pruebas': [{'prueba': 'demo', 'ok': True, 'repetida': True}, {'prueba': 'tipos', 'ok': True}]}]}))
                m = equipo.metrics('prompted-os')
        self.assertEqual(m['repetidas'], 1)

    def test_repeated_tests_are_counted_by_reason_and_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'merged_by_owner', lambda pid, c: []):
                tests = [
                    {'prueba': 'equipo', 'ok': True, 'repetida': True, 'repetida_por': 'timeout'},
                    {'prueba': 'equipo', 'ok': False, 'timeout': True, 'repetida_por': 'timeout'},
                    {'prueba': 'demo', 'ok': False, 'repetida': True, 'repetida_por': 'arranque'},
                ]
                for i, test in enumerate(tests):
                    tid = f't_{i}'
                    equipo.save(state / 'runs' / tid / 'receipt.json', {'tarea': tid, 'proyecto': 'prompted-os',
                        'estado': 'integrada' if test['ok'] else 'bloqueada',
                        'inicio': equipo.now().isoformat(), 'fin': equipo.now().isoformat(),
                        'ciclos': [{'pruebas': [test]}]})
                m = equipo.metrics('prompted-os')
        self.assertEqual((m['repetidas_por_timeout'], m['repetidas_por_timeout_en_verde'],
                          m['repetidas_por_arranque'], m['repetidas_por_arranque_en_verde']), (2, 1, 1, 0))
        self.assertEqual(m['repetidas'], 2)

    def test_decision_median_and_date_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            current = equipo.now()
            recent = (current - equipo.timedelta(days=2)).date().isoformat()
            old = (current - equipo.timedelta(days=8)).date().isoformat()
            decisions = [{'fecha': recent, 'titulo': f'Tarea {i}', 'decision': 'aprobada' if i != 1 else 'descartada', 'minutos': minutes}
                         for i, minutes in enumerate([2, 10, 30])]
            equipo.save(state / 'senales' / 'norte.json', {'decisiones': decisions + [
                {'fecha': old, 'titulo': 'Antigua', 'decision': 'aprobada', 'minutos': 100}]})
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'now', return_value=current), \
                 mock.patch.object(equipo, 'merged_by_owner', return_value=[]):
                m = equipo.metrics('norte')
                self.assertEqual((m['decisiones_tuyas'], m['minutos_hasta_decision']), (3, 10))
                m = equipo.metrics('norte', days=1)
                self.assertEqual(m['decisiones_tuyas'], 0)
                self.assertIsNone(m['minutos_hasta_decision'])
                m = equipo.metrics('norte', days=10)
                self.assertEqual((m['decisiones_tuyas'], m['minutos_hasta_decision']), (4, 20))

    def test_decision_median_split_by_launch_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            current = equipo.now()
            recent = current.date().isoformat()
            decision = lambda minutes, **k: {'fecha': recent, 'titulo': 'T', 'decision': 'aprobada', 'minutos': minutes, **k}
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'now', return_value=current), \
                 mock.patch.object(equipo, 'merged_by_owner', return_value=[]):
                m = equipo.metrics('norte')
                self.assertEqual((m['minutos_hasta_decision_con_ficha'], m['minutos_hasta_decision_sin_ficha']), (None, None))
                self.assertEqual((m['decisiones_con_ficha'], m['decisiones_sin_ficha']), (0, 0))
                equipo.save(state / 'senales' / 'norte.json', {'decisiones': [
                    decision(4, ficha=True), decision(7.5, ficha=True), decision(30, ficha=False), decision(10), decision(50, ficha=False)]})
                m = equipo.metrics('norte')
                self.assertEqual((m['minutos_hasta_decision_con_ficha'], m['minutos_hasta_decision_sin_ficha']), (5.8, 30))
                self.assertEqual((m['decisiones_con_ficha'], m['decisiones_sin_ficha']), (2, 3))
                equipo.save(state / 'senales' / 'norte.json', {'decisiones': [decision(3, ficha=True)]})
                m = equipo.metrics('norte')
                self.assertEqual((m['minutos_hasta_decision_con_ficha'], m['minutos_hasta_decision_sin_ficha']), (3, None))
                self.assertEqual((m['decisiones_con_ficha'], m['decisiones_sin_ficha']), (1, 0))

    def test_planner_signals_decision_time_line(self):
        fake = {'tareas': 0, 'decisiones_tuyas': 1, 'minutos_hasta_decision_con_ficha': 3, 'minutos_hasta_decision_sin_ficha': None,
                'decisiones_con_ficha': 1, 'decisiones_sin_ficha': 0}
        with mock.patch.object(equipo, 'metrics', lambda pid, days=7: fake), mock.patch.object(equipo, 'lessons', lambda pid: []):
            self.assertIn('Tiempo de decisión de Owner: con ficha 3 min (n=1), sin ficha sin datos (n=0). Con menos de 5 por grupo no es señal.',
                          equipo.planner_signals('prompted-os'))
            fake['decisiones_tuyas'] = 0
            self.assertNotIn('Tiempo de decisión de Owner', equipo.planner_signals('prompted-os'))

    def test_planner_signals_decision_time_line_gives_a_signal_with_five_per_group(self):
        fake = {'tareas': 0, 'decisiones_tuyas': 10, 'minutos_hasta_decision_con_ficha': 3, 'minutos_hasta_decision_sin_ficha': 10,
                'decisiones_con_ficha': 5, 'decisiones_sin_ficha': 5}
        with mock.patch.object(equipo, 'metrics', lambda pid, days=7: fake), mock.patch.object(equipo, 'lessons', lambda pid: []):
            self.assertIn('con ficha decides más rápido que sin ficha', equipo.planner_signals('prompted-os'))
            fake['decisiones_sin_ficha'] = 4
            signals = equipo.planner_signals('prompted-os')
            self.assertIn('no es señal', signals)
            self.assertNotIn('decides más rápido', signals)

    def signals_with_metrics(self, experiments=None, **fields):
        fake = {'tareas': 1, **fields}
        minimal = {'avisados': 0, 'sin_medir': [], 'decisiones': {}, 'recordados': 0}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'metrics', lambda pid, days=7: fake), \
             mock.patch.object(equipo, 'experimentos', lambda pid: {**minimal, **experiments} if experiments is not None else minimal), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'receipts', lambda *a, **kw: []), \
             mock.patch.object(equipo, 'mesa_load', lambda: {}):
            return equipo.planner_signals('prompted-os')

    def test_planner_signals_batch_brake_when_integrations_wait_for_owner(self):
        signals = self.signals_with_metrics(
            integradas=39,
            experiments={'por_lanzar_total': 1, 'lanzados': 0, 'descartados': 0, 'decisiones': {}},
        )
        self.assertIn('Freno de lote: en 7 días se integraron 39 tareas', signals)
        self.assertIn('experimentos esperando a Owner: 1', signals)

    def test_planner_signals_batch_brake_starts_at_ten_integrations(self):
        experiments = {'por_lanzar_total': 1, 'lanzados': 0, 'descartados': 0, 'decisiones': {}}
        self.assertIn('Freno de lote', self.signals_with_metrics(integradas=10, experiments=experiments))
        self.assertNotIn('Freno de lote', self.signals_with_metrics(integradas=9, experiments=experiments))

    def test_planner_signals_batch_brake_omits_when_an_experiment_is_decided(self):
        for experiments in (
            {'por_lanzar_total': 1, 'lanzados': 1, 'descartados': 0, 'decisiones': {}},
            {'por_lanzar_total': 1, 'lanzados': 0, 'descartados': 1, 'decisiones': {}},
            {'por_lanzar_total': 1, 'lanzados': 0, 'descartados': 0, 'decisiones': {'seguir': 1}},
            {'por_lanzar_total': 1, 'lanzados': 0, 'descartados': 0, 'decisiones': {'sin_decision': 1}},
        ):
            with self.subTest(experiments=experiments):
                self.assertNotIn('Freno de lote', self.signals_with_metrics(integradas=39, experiments=experiments))

    def test_planner_signals_batch_brake_omits_without_waiting_experiments_or_keys(self):
        self.assertNotIn('Freno de lote', self.signals_with_metrics(
            integradas=39,
            experiments={'por_lanzar_total': 0, 'lanzados': 0, 'descartados': 0, 'decisiones': {}},
        ))
        self.assertNotIn('Freno de lote', self.signals_with_metrics(integradas=39))

    def test_planner_signals_scope_blocked_line_asks_for_all_files_when_mean_above_one(self):
        signals = self.signals_with_metrics(bloqueadas_por_alcance=2, intentos_en_bloqueadas_por_alcance=3)
        self.assertIn('Bloqueadas por falta de permiso para un archivo (7 días): 2, con 3 intentos (media 1,5 por tarea).', signals)
        self.assertIn('Lista en cada tarea', signals)

    def test_planner_signals_scope_blocked_line_without_advice_when_mean_is_one(self):
        signals = self.signals_with_metrics(bloqueadas_por_alcance=1, intentos_en_bloqueadas_por_alcance=1)
        self.assertIn('media 1,0 por tarea', signals)
        self.assertNotIn('Lista en cada tarea', signals)

    def test_planner_signals_omits_scope_blocked_line_without_blocked_tasks(self):
        self.assertNotIn('falta de permiso', self.signals_with_metrics(bloqueadas_por_alcance=0, intentos_en_bloqueadas_por_alcance=0))

    def test_planner_signals_timeout_retries_line(self):
        self.assertIn('mantenla', self.signals_with_metrics(repetidas_por_timeout=2, repetidas_por_timeout_en_verde=1))
        self.assertIn('propón quitarla', self.signals_with_metrics(repetidas_por_timeout=3, repetidas_por_timeout_en_verde=0))
        self.assertIn('Con menos de 3 no es señal.', self.signals_with_metrics(repetidas_por_timeout=1, repetidas_por_timeout_en_verde=0))
        self.assertNotIn('repetidas por timeout', self.signals_with_metrics(repetidas_por_timeout=0, repetidas_por_timeout_en_verde=0))

    def test_planner_signals_startup_retries_line(self):
        keep = self.signals_with_metrics(repetidas_por_arranque=2, repetidas_por_arranque_en_verde=1)
        self.assertIn('tardó en arrancar (7 días): 2, en verde 1.', keep)
        self.assertIn('mantenla', keep)
        self.assertIn('propón arreglar el arranque', self.signals_with_metrics(repetidas_por_arranque=3, repetidas_por_arranque_en_verde=0))
        self.assertIn('Con menos de 3 no es señal.', self.signals_with_metrics(repetidas_por_arranque=1, repetidas_por_arranque_en_verde=0))
        self.assertNotIn('tardó en arrancar', self.signals_with_metrics(repetidas_por_arranque=0, repetidas_por_arranque_en_verde=0))


class Changes(unittest.TestCase):
    def test_first_status_line_keeps_its_path(self):
        """Regression: a stripped ' M lib/x.js' once became 'ib/x.js' and the real change was dropped."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(['git', '-C', tmp, *a], check=True, capture_output=True)
            run('init', '-q'); (repo / 'lib').mkdir(); (repo / 'lib/x.js').write_text('a\n'); (repo / 'test.js').write_text('t\n')
            run('add', '.'); run('-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-qm', 'base')
            (repo / 'lib/x.js').write_text('b\n'); (repo / 'nuevo.js').write_text('n\n'); (repo / 'otro.js').write_text('o\n')
            (repo / 'node_modules').symlink_to(tmp)
            allowed, extra = equipo.changed(repo, ['lib/x.js', 'nuevo.js'], ['node_modules'])
        self.assertEqual(allowed, ['lib/x.js', 'nuevo.js'])
        self.assertEqual(extra, ['otro.js'])


class Worktree(unittest.TestCase):
    def test_dependency_links_stay_out_of_git_status_and_exclude_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(['git', '-C', tmp, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True, text=True).stdout.strip()
            run('init', '-q', '-b', 'main'); (repo / 'a.txt').write_text('a\n'); run('add', '.'); run('commit', '-qm', 'base')
            (repo / 'node_modules').mkdir(); (repo / 'node_modules/dep.js').write_text('d\n')
            env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
            cfg = {'repo': tmp, 'links': ['node_modules']}
            with mock.patch.object(equipo, 'project', lambda pid: cfg), mock.patch.object(equipo, 'ROOT', Path(root)), \
                 mock.patch.object(equipo, 'clean_env', lambda: env):
                equipo.worktree('x', 't', start='HEAD')
                path = equipo.worktree('x', 't', start='HEAD')
                self.assertTrue((path / 'node_modules').is_symlink())
                status = subprocess.run(['git', '-C', str(path), 'status', '--porcelain', '--untracked-files=all'], capture_output=True, text=True, env=env).stdout
                exclude = Path(run('rev-parse', '--path-format=absolute', '--git-path', 'info/exclude'))
            self.assertEqual(status, '')
            self.assertEqual(exclude.read_text().splitlines().count('/node_modules'), 1)


class BaseRojaLimpia(unittest.TestCase):
    def run_base_roja(self, crea_carpeta):
        """Runs base_roja with a worktree double that fails (after creating its folder or not); returns (result, git calls, expected path)."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); esperada = root / 'worktrees' / 'x' / 't1-base'; calls = []
            def falso_worktree(pid, name, branch=None, new_branch=None, start=None):
                if crea_carpeta: (root / 'worktrees' / pid / name).mkdir(parents=True)
                raise RuntimeError('fallo al enlazar dependencias')
            def falso_git(repo, *args, **kw): calls.append(args); return 'abc123'
            with mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'ROOT', root), \
                 mock.patch.object(equipo, 'project', lambda pid: {'repo': '/repo'}), mock.patch.object(equipo, 'worktree', falso_worktree), \
                 mock.patch.object(equipo, 'git', falso_git):
                result = equipo.base_roja('x', {}, 'equipo/x', 'suite', '', [], root / 'runs' / 't1', lambda *a, **k: None, task_id='t1')
            return result, calls, esperada

    def test_removes_the_base_copy_when_worktree_fails_after_creating_it(self):
        result, calls, esperada = self.run_base_roja(True)
        self.assertEqual(result, '')
        self.assertIn(('worktree', 'remove', '--force', str(esperada)), calls)

    def test_does_not_remove_when_worktree_fails_before_creating_the_folder(self):
        result, calls, esperada = self.run_base_roja(False)
        self.assertEqual(result, '')
        self.assertFalse([c for c in calls if c[:2] == ('worktree', 'remove')])


class SyncBase(unittest.TestCase):
    def test_fast_forward_clean_merge_and_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(['git', '-C', tmp, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True, text=True).stdout.strip()
            run('init', '-q', '-b', 'main'); (repo / 'a.txt').write_text('a\n'); (repo / 'b.txt').write_text('b\n')
            run('add', '.'); run('commit', '-qm', 'base'); run('branch', 'equipo/x')
            cfg = {'repo': tmp, 'base': 'main'}
            env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
            with mock.patch.object(equipo, 'project', lambda pid: cfg), mock.patch.object(equipo, 'STATE', repo / 'state'), \
                 mock.patch.object(equipo, 'clean_env', lambda: env), mock.patch.object(equipo, 'post') as post:
                (repo / 'state').mkdir()
                self.assertEqual(equipo.sync_base('x'), 'al día')
                (repo / 'a.txt').write_text('a2\n'); run('commit', '-qam', 'owner 1')
                self.assertEqual(equipo.sync_base('x'), 'adelantada')
                self.assertEqual(run('rev-parse', 'equipo/x'), run('rev-parse', 'main'))
                run('checkout', '-q', 'equipo/x'); (repo / 'b.txt').write_text('b-team\n'); run('commit', '-qam', 'team'); run('checkout', '-q', 'main')
                (repo / 'c.txt').write_text('c\n'); run('add', 'c.txt'); run('commit', '-qm', 'owner 2')
                self.assertEqual(equipo.sync_base('x'), 'fusionada')
                self.assertEqual(run('show', 'equipo/x:b.txt'), 'b-team'); self.assertEqual(run('show', 'equipo/x:c.txt'), 'c')
                run('checkout', '-q', 'equipo/x'); (repo / 'a.txt').write_text('team-a\n'); run('commit', '-qam', 'team a'); run('checkout', '-q', 'main')
                (repo / 'a.txt').write_text('owner-a\n'); run('commit', '-qam', 'owner a')
                before = run('rev-parse', 'equipo/x')
                self.assertEqual(equipo.sync_base('x'), 'conflicto'); self.assertEqual(equipo.sync_base('x'), 'conflicto')
                self.assertEqual(run('rev-parse', 'equipo/x'), before)
                self.assertEqual(post.call_count, 1)
                self.assertIn('a.txt', post.call_args.args[2])

class Conflictos(unittest.TestCase):
    def test_lista_conflictos(self):
        self.assertEqual(equipo.lista_conflictos([]), '')
        self.assertEqual(equipo.lista_conflictos(['a.txt']), ' (en a.txt)')
        self.assertEqual(equipo.lista_conflictos(['a.txt', 'b.txt', 'c.txt', 'd.txt', 'e.txt', 'f.txt', 'g.txt']),
                         ' (en a.txt, b.txt, c.txt, d.txt, e.txt y 2 más)')

    def test_fusionar_project_conflict(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as root:
            repo = Path(tmp)
            run = lambda *a: subprocess.run(['git', '-C', tmp, '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True, text=True).stdout.strip()
            run('init', '-q', '-b', 'main'); (repo / 'a.txt').write_text('a\n')
            run('add', '.'); run('commit', '-qm', 'base'); run('branch', 'equipo/x')
            run('checkout', '-q', 'equipo/x'); (repo / 'a.txt').write_text('team-a\n'); run('commit', '-qam', 'team a'); run('checkout', '-q', 'main')
            (repo / 'a.txt').write_text('owner-a\n'); run('commit', '-qam', 'owner a')
            before = run('rev-parse', 'main')
            cfg = {'repo': tmp, 'base': 'main', 'tests': {}}
            env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'}
            with mock.patch.object(equipo, 'project', lambda pid: cfg), mock.patch.object(equipo, 'ROOT', Path(root)), \
                 mock.patch.object(equipo, 'STATE', Path(root) / 'state'), mock.patch.object(equipo, 'clean_env', lambda: env):
                result = equipo.fusionar_project('x')
            self.assertTrue(result.startswith('conflicto'))
            self.assertIn('a.txt', result)
            self.assertEqual(run('rev-parse', 'main'), before)
            self.assertFalse((Path(root) / 'worktrees' / 'x' / '_fusion').exists())

class Cartera(unittest.TestCase):
    def test_reads_project_notes_and_finds_the_team_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / 'Huerto.md').write_text('---\ntipo: proyecto\nid: huerto\narea: social\nequipo: "norte"\n---\n# Huerto\n\n## Contexto para el equipo\nUna app social.\n')
            (d / 'Suelta.md').write_text('# Una nota cualquiera sin propiedades\n')
            (d / 'Idea.md').write_text('---\ntipo: idea\n---\n# Idea\n')
            with mock.patch.object(equipo, 'CARTERA', d):
                self.assertEqual([n['nombre'] for n in equipo.cartera()], ['Huerto'])
                self.assertIn('Una app social.', equipo.ficha('norte')['texto'])
                self.assertIsNone(equipo.ficha('otro'))
                self.assertEqual(equipo.cartera_resumen()[0]['area'], 'social')

class Models(unittest.TestCase):
    def setUp(self):
        patch = solo_claude(); patch.start(); self.addCleanup(patch.stop)

    def test_cheapest_model_that_fits(self):
        pick = equipo.pick_model
        self.assertEqual(pick('mesa'), ('claude-sonnet-5-5', 'low'))
        self.assertEqual(pick('planificador'), ('claude-opus-5-5', 'high'))
        self.assertEqual(pick('constructor', {'dificultad': 'baja'}), ('claude-sonnet-5-5', 'medium'))
        self.assertEqual(pick('constructor', {'dificultad': 'alta'})[0], 'claude-opus-5-5')
        self.assertEqual(pick('constructor', {'dificultad': 'baja', 'importante': 'toca package.json'})[0], 'claude-opus-5-5')
        self.assertEqual(pick('constructor', {'dificultad': 'baja'}, attempt=2)[0], 'claude-opus-5-5')
        self.assertEqual(pick('revisor', {'dificultad': 'media'}, diff_lines=900)[0], 'claude-opus-5-5')
        self.assertEqual(pick('revisor', {'dificultad': 'rara'})[0], 'claude-sonnet-5-5')        # unknown -> media
        self.assertEqual(pick(None), (equipo.CONFIG['model'], None))                            # no role: as before

    def test_planner_difficulty_is_kept_or_defaulted(self):
        ok = {'titulo': 'Una tarea', 'porque': 'x', 'detalle': 'Criterios de aceptación concretos', 'archivos': ['alfred-os/app/a.tsx'], 'pruebas': ['tipos'], 'depende_de': []}
        self.assertEqual(equipo.validate_task('prompted-os', {**ok, 'dificultad': 'alta'}, 0)['dificultad'], 'alta')
        self.assertEqual(equipo.validate_task('prompted-os', ok, 0)['dificultad'], 'media')

FAKE_CODEX = """#!/bin/sh
out=""
while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done
cat >/dev/null
echo "$*" >/dev/null
%s
"""
CODEX_OK = """printf 'Hecho.' > "$out"
echo '{"type":"thread.started"}'
echo '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":4,"output_tokens":5}}'"""
CODEX_DOWN = """echo '{"type":"error","message":"You have hit your usage limit"}'
exit 1"""
FAKE_CLAUDE = """#!/bin/sh
cat >/dev/null
echo '{"type":"rate_limit_event","rate_limit_info":{"unifiedWindows":{"five_hour":{"utilization":0.4,"resetsAt":1},"seven_day":{"utilization":0.2,"resetsAt":1}}}}'
echo '{"type":"result","result":"Claude hecho","total_cost_usd":0.01,"usage":{"output_tokens":3}}'
"""

@unittest.skipIf(os.name == 'nt', 'usa programas falsos de shell, que Windows no ejecuta')
class Codex(unittest.TestCase):
    """From 10/10 Codex builds first attempts by difficulty; Claude reviews, takes second attempts and covers outages."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.codex = root / 'codex'; self.fake_claude = root / 'claude'
        self.fake_claude.write_text(FAKE_CLAUDE); self.fake_claude.chmod(0o755)
        self.patches = [mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'CODEX_BIN', self.codex),
                        mock.patch.object(equipo, 'claude_bin', lambda: str(self.fake_claude)), mock.patch.object(equipo, 'post', mock.Mock())]
        for patch in self.patches: patch.start()
        self.fake(CODEX_OK)

    def tearDown(self):
        for patch in self.patches: patch.stop()
        self.tmp.cleanup()

    def fake(self, body):
        self.codex.write_text(FAKE_CODEX % body); self.codex.chmod(0o755)

    def on(self, day, at='15:00'):
        moment = equipo.datetime.fromisoformat(f'{day}T{at}').replace(tzinfo=equipo.MADRID)
        return mock.patch.object(equipo, 'now', lambda: moment)

    def test_codex_takes_first_builds_from_its_date(self):
        pick = equipo.pick_model
        with self.on('2026-10-10', '09:00'):
            self.assertEqual(pick('constructor', {'dificultad': 'baja'}), ('claude-sonnet-5-5', 'medium'))   # credits back at 14:26
        with self.on('2026-10-10'):
            self.assertEqual(pick('constructor', {'dificultad': 'baja'}), ('gpt-5.6-terra', 'low'))
            self.assertEqual(pick('constructor', {'dificultad': 'media'}), ('gpt-6-astra', 'medium'))
            self.assertEqual(pick('constructor', {'dificultad': 'baja', 'importante': 'sensible'}), ('gpt-6-astra', 'high'))
            self.assertEqual(pick('constructor', {'dificultad': 'baja'}, attempt=2)[0], 'claude-opus-5-5')     # fresh eyes
            self.assertEqual(pick('revisor', {'dificultad': 'media'}, builder='gpt-6-astra')[0], 'claude-sonnet-5-5')   # Claude reviews Codex
            self.assertEqual(pick('revisor', {'dificultad': 'media'}, builder='claude-sonnet-5-5'), ('gpt-6-astra', 'medium'))  # and Codex reviews Claude
            self.assertEqual(pick('revisor', {'dificultad': 'baja'}, attempt=2, builder='claude-opus-5-5'), ('gpt-6-astra', 'high'))
            self.assertEqual(pick('revisor', {'dificultad': 'media'})[0], 'claude-sonnet-5-5')                          # builder unknown
            self.assertEqual(pick('revisor', {'dificultad': 'baja'})[0], 'claude-sonnet-5-5')                 # other vendor reviews
            self.assertEqual(pick('planificador'), ('claude-opus-5-5', 'high'))
            self.assertEqual(pick('constructor', {'dificultad': 'baja'}, codex=False)[0], 'claude-sonnet-5-5')
            equipo.save(equipo.STATE / 'codex.json', {'hasta': '2026-10-10 18:00'})
            self.assertEqual(pick('constructor', {'dificultad': 'baja'})[0], 'claude-sonnet-5-5')             # out for now
        with self.on('2026-10-10', '18:00'):
            self.assertEqual(pick('constructor', {'dificultad': 'baja'})[0], 'gpt-5.6-terra')                 # back when it said

    def test_codex_builds_and_logs_tokens(self):
        who = {}
        with self.on('2026-10-10'):
            answer = equipo.claude('Haz la tarea', self.tmp.name, 30, write=True, role='constructor', spec={'dificultad': 'baja'}, info=who)
            lines = (equipo.STATE / 'consumo' / '2026-10-10.jsonl').read_text().splitlines()
        self.assertEqual((answer, who['modelo']), ('Hecho.', 'gpt-5.6-terra'))
        line = json.loads(lines[-1])
        self.assertEqual((line['rol'], line['modelo'], line['tokens']['output_tokens']), ('constructor', 'gpt-5.6-terra', 5))

    def test_codex_outage_falls_back_to_claude_once_a_day(self):
        self.fake(CODEX_DOWN); who = {}; undo = mock.Mock()
        with self.on('2026-10-10'):
            answer = equipo.claude('Haz la tarea', self.tmp.name, 30, write=True, role='constructor', spec={'dificultad': 'baja'}, info=who, on_fallback=undo)
            again = equipo.pick_model('constructor', {'dificultad': 'baja'})[0]
            equipo.codex_down('otra vez')
        self.assertEqual((answer, who['modelo'], again), ('Claude hecho', 'claude-sonnet-5-5', 'claude-sonnet-5-5'))
        undo.assert_called_once()
        down = equipo.load(equipo.STATE / 'codex.json', {})
        self.assertEqual(down['hasta'], '2026-10-10 23:59')                                                  # no time given: rest of the day
        self.assertIn('usage limit', down['motivo'])
        equipo.post.assert_called_once()                                                                    # Owner hears it once

    def test_back_time_is_read_from_codex(self):
        with self.on('2026-10-05'):
            self.assertEqual(equipo.codex_back("You've hit your usage limit. Visit https://x to purchase more credits or try again at Oct 10th, 2026 2:26 PM."), '2026-10-10 14:26')
            self.assertEqual(equipo.codex_back('network error'), '2026-10-05 15:05')

    def test_network_outage_expires_without_spending_another_probe(self):
        with self.on('2026-11-01'):
            equipo.codex_down('codex_error: Reconnecting... 2/5 (request timed out)')
            self.assertFalse(equipo.codex_ready())
            self.assertEqual(equipo.modelos_hoy()['codex'], 'fuera hasta 2026-11-01 15:05')
        with self.on('2026-11-01', '15:05'), mock.patch.object(equipo, 'run', side_effect=AssertionError('no probe')):
            self.assertTrue(equipo.codex_ready())
            self.assertEqual(equipo.modelos_hoy()['codex'], 'activo')

    def test_old_all_day_network_marker_recovers_without_mutating_state(self):
        marker = {'hora': '2026-11-01T12:18:54+01:00', 'hasta': '2026-11-01 23:59',
                  'motivo': 'codex_error: Reconnecting... 2/5 (request timed out)'}
        equipo.save(equipo.STATE / 'codex.json', marker)
        before = (equipo.STATE / 'codex.json').read_bytes()
        with self.on('2026-11-01', '17:00'):
            self.assertTrue(equipo.codex_ready())
            self.assertEqual(equipo.modelos_hoy()['codex'], 'activo')
        self.assertEqual((equipo.STATE / 'codex.json').read_bytes(), before)

    def test_quota_and_explicit_reset_are_not_treated_as_network_cooldowns(self):
        with self.on('2026-11-01'):
            self.assertEqual(equipo.codex_back('network error after usage limit'), '2026-11-01 23:59')
            self.assertEqual(equipo.codex_back('network error; try again at Nov 2nd, 2026 2:26 PM'), '2026-11-02 14:26')
            for reason in ['usage limit', 'network error after usage limit', 'network error; try again at Nov 2nd, 2026 2:26 PM']:
                equipo.save(equipo.STATE / 'codex.json', {'hora': '2026-11-01T12:00:00+01:00', 'hasta': '2026-11-01 23:59', 'motivo': reason})
                self.assertFalse(equipo.codex_ready())
            equipo.save(equipo.STATE / 'codex.json', {'hora': 'unreadable', 'hasta': '2026-11-01 23:59', 'motivo': 'network error'})
            self.assertFalse(equipo.codex_ready())

    def test_repeated_network_outage_does_not_repeat_notice_after_cooldown(self):
        with self.on('2026-11-01'):
            equipo.codex_down('network error')
        with self.on('2026-11-01', '15:04'):
            equipo.codex_down('network error')
        with self.on('2026-11-01', '15:06'):
            equipo.codex_down('network error')
            self.assertFalse(equipo.codex_ready())
        equipo.post.assert_called_once()
        with self.on('2026-11-01', '15:11'):
            self.assertTrue(equipo.codex_ready())

    def test_new_quota_from_an_inflight_call_promotes_an_active_network_cooldown(self):
        with self.on('2026-11-01'):
            equipo.codex_down('network error')
        with self.on('2026-11-01', '15:01'):
            equipo.codex_down("You've hit your usage limit; try again at Nov 2nd, 2026 2:26 PM")
        with self.on('2026-11-01', '15:06'):
            self.assertFalse(equipo.codex_ready())
            marker = equipo.load(equipo.STATE / 'codex.json', {})
            self.assertEqual((marker['tipo'], marker['hasta']), ('limite', '2026-11-02 14:26'))
            equipo.codex_down('network error')
            self.assertEqual(equipo.load(equipo.STATE / 'codex.json', {}), marker)

    def test_classification_survives_truncation_and_ambiguous_legacy_records_stay_blocked(self):
        reason = 'network error ' + 'x' * 286 + ' usage limit'
        with self.on('2026-11-01'):
            equipo.codex_down(reason)
            marker = equipo.load(equipo.STATE / 'codex.json', {})
            self.assertNotIn('usage limit', marker['motivo'])
            self.assertEqual(marker['tipo'], 'limite')
        with self.on('2026-11-01', '15:06'):
            self.assertFalse(equipo.codex_ready())
            del marker['tipo']   # historical truncated errors cannot safely be assumed to be transport failures
            equipo.save(equipo.STATE / 'codex.json', marker)
            self.assertFalse(equipo.codex_ready())

    def test_network_retry_is_five_elapsed_minutes_across_dst_fold(self):
        at = datetime(2026, 10, 25, 2, 58, tzinfo=equipo.MADRID, fold=0)
        with mock.patch.object(equipo, 'now', lambda: at):
            equipo.codex_down('network error')
            marker = equipo.load(equipo.STATE / 'codex.json', {})
            self.assertEqual(marker['hasta_epoch'], at.timestamp() + 300)
            self.assertEqual(equipo.codex_back('network error'), '2026-10-25 02:03')
        for legacy in [False, True]:
            if legacy:
                equipo.save(equipo.STATE / 'codex.json', {'hora': at.isoformat(), 'hasta': '2026-10-25 23:59', 'motivo': 'network error'})
            for seconds, ready in [(299, False), (300, True)]:
                tick = datetime.fromtimestamp(at.timestamp() + seconds, equipo.MADRID)
                with mock.patch.object(equipo, 'now', lambda: tick):
                    self.assertEqual(equipo.codex_ready(), ready)

    def test_status_line(self):
        with self.on('2026-10-09'): self.assertEqual(equipo.modelos_hoy(), {'codex': 'desde 2026-10-10 14:30'})
        with self.on('2026-10-10'): self.assertEqual(equipo.modelos_hoy()['codex'], 'activo')

class Salud(unittest.TestCase):
    """Silent failures must reach Owner: a problem seen twice is announced once, and its fix is announced too."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.sent = []
        self.state = {'estado': 'ok'}
        fake = lambda: equipo._check('demo', 'Demo', self.state['estado'], 'detalle', 'haz esto')
        self.patches = [mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'SALUD', root / 'state' / 'salud.json'),
                        mock.patch.object(equipo, 'WORKSPACE', root / 'ws'), mock.patch.object(equipo, 'SALUD_CHECKS', [fake]),
                        mock.patch.object(equipo, 'alert', lambda title, text: self.sent.append(title))]
        for patch in self.patches: patch.start()

    def tearDown(self):
        for patch in self.patches: patch.stop()
        self.tmp.cleanup()

    def test_problem_twice_then_once_then_fixed(self):
        equipo.salud(); self.assertEqual(self.sent, [])                         # all fine: silence
        self.state['estado'] = 'fallo'
        equipo.salud(); self.assertEqual(self.sent, [])                         # seen once: could be a restart
        equipo.salud(); self.assertEqual(self.sent, ['⚠️ Demo'])                # seen twice: announced
        equipo.salud(); self.assertEqual(len(self.sent), 1)                     # never repeated
        self.state['estado'] = 'ok'
        out = equipo.salud(); self.assertEqual(self.sent[-1], '✅ Demo')        # the fix is announced
        self.assertEqual(out['resumen'], 'ok')

    def test_voice_without_key_is_a_failure(self):
        equipo.WORKSPACE.mkdir(parents=True)
        (equipo.WORKSPACE / 'voz.log').write_text('2026-10-05T18:11:42 clave: archivo bloqueado (macOS pide permiso para Documentos)\n')
        self.assertEqual(equipo.check_voz()['estado'], 'fallo')
        (equipo.WORKSPACE / 'voz.log').write_text('2026-10-05T18:31:09 clave: archivo del curso\n')
        self.assertEqual(equipo.check_voz()['estado'], 'ok')

    def test_plan_usage_levels(self):
        equipo.WORKSPACE.mkdir(parents=True)
        later = equipo.now().timestamp() + 3600
        write = lambda five: equipo.save(equipo.WORKSPACE / 'uso.json', {'fiveHour': five, 'sevenDay': 0.2, 'resetsAt': later, 'hora': equipo.now().isoformat()})
        write(0.5); self.assertEqual(equipo.check_uso()['estado'], 'ok')
        write(0.9); self.assertEqual(equipo.check_uso()['estado'], 'aviso')      # team braked
        write(0.98); self.assertEqual(equipo.check_uso()['estado'], 'fallo')     # Alfred at risk

    def test_brake(self):
        equipo.WORKSPACE.mkdir(parents=True)
        t = equipo.now().timestamp()
        equipo.save(equipo.STATE / 'uso.json', {'fiveHour': 0.99, 'sevenDay': 0.35, 'resetsAt': t + 3600, 'hora': equipo.now().isoformat()})
        self.assertIn('99 %', equipo.freno())
        equipo.save(equipo.STATE / 'uso.json', {'fiveHour': 0.99, 'sevenDay': 0.35, 'resetsAt': t - 60, 'hora': equipo.now().isoformat()})
        self.assertIsNone(equipo.freno())                                          # window already reset
        equipo.save(equipo.STATE / 'uso.json', {'fiveHour': 0.1, 'sevenDay': 0.9, 'hora': equipo.now().isoformat()})
        self.assertIn('semana', equipo.freno())
        # The freshest reading wins: Alfred saw it lower afterwards.
        equipo.save(equipo.WORKSPACE / 'uso.json', {'fiveHour': 0.1, 'sevenDay': 0.2, 'hora': (equipo.now() + equipo.timedelta(minutes=1)).isoformat()})
        self.assertIsNone(equipo.freno())

    def test_braked_run_and_deferred_plan(self):
        equipo.save(equipo.STATE / 'uso.json', {'fiveHour': 0.95, 'sevenDay': 0.2, 'resetsAt': equipo.now().timestamp() + 3600, 'hora': equipo.now().isoformat()})
        with mock.patch.object(equipo, 'ROOT', Path(self.tmp.name)), mock.patch.object(equipo, 'answer_owner') as answer, \
             mock.patch.object(equipo, 'retro') as retro:
            equipo.run_all()
            answer.assert_not_called()
            self.assertIn('aplazado', equipo.plan(['prompted-os']))
            retro.assert_not_called()
        self.assertEqual(equipo.load(equipo.STATE / 'plan_aplazado.json', {})['fecha'], equipo.today())

    def test_stream_parsing(self):
        res, rate = equipo.parse_stream('basura\n{"type":"rate_limit_event","rate_limit_info":{"utilization":0.5}}\n{"type":"result","result":"OK"}\n')
        self.assertEqual((res['result'], rate['utilization']), ('OK', 0.5))

    def test_stuck_night_merge(self):
        equipo.save(equipo.STATE / 'fusiones' / 'ultima.json', {'fecha': '2026-10-05T23:47:53', 'resultados': {'beta': 'conflicto: necesita a Owner o a Hermes'}})
        with mock.patch.object(equipo, 'pending_merge', lambda pid: 3):
            self.assertEqual(equipo.check_fusiones()['estado'], 'fallo')
            equipo.save(equipo.STATE / 'fusiones' / 'ultima.json', {'fecha': '2026-10-05T23:47:53', 'resultados': {'beta': 'conflicto: necesita a Owner o a Hermes (en a.txt)'}})
            self.assertIn('a.txt', equipo.check_fusiones()['detalle'])
            equipo.save(equipo.STATE / 'fusiones' / 'ultima.json', {'fecha': '2026-11-01T01:40:50', 'resultados': {'beta': 'error: reposo: el Mac se durmió durante unitarias'}})
            asleep = equipo.check_fusiones()                                         # not a conflict: it retries tonight
            self.assertEqual(asleep['estado'], 'aviso')
            self.assertNotIn('conflicto', asleep['detalle'])
        with mock.patch.object(equipo, 'pending_merge', lambda pid: 0):            # resolved by hand since
            self.assertEqual(equipo.check_fusiones()['estado'], 'ok')

class Saturado(unittest.TestCase):
    """01/11: a render from another session held the Mac at full load and Norte's demo server missed its health check."""
    SPEC = {'pruebas': ['unitarias']}

    def _run(self, outputs, load, extra=None):
        calls = iter(outputs)
        def fake_run(step, cwd, limit, out=None, heartbeat=None):
            r = next(calls)
            if isinstance(r, Exception): raise r
            return r
        tests = {'unitarias': {'cmd': ['node', '--test'], **(extra or {})}}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'run', fake_run), mock.patch.object(equipo, 'carga', lambda: load), \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': tests}), mock.patch.object(equipo, 'log', lambda *a, **k: None):
            return equipo.run_tests('norte', self.SPEC, Path(tmp), Path(tmp), None)

    def test_slow_start_on_a_saturated_mac_does_not_cost_an_attempt(self):
        with self.assertRaises(equipo.Saturado):
            self._run([(1, 'demo journey server did not become healthy'), (1, 'demo journey server did not become healthy')], load=4.8)

    def test_suite_timeout_on_a_saturated_mac_does_not_cost_an_attempt(self):
        with self.assertRaises(equipo.Saturado):
            self._run([equipo.Stop('timeout')], load=4.8)
        with self.assertRaises(equipo.Stop) as e:
            self._run([equipo.Stop('timeout'), equipo.Stop('timeout')], load=0.5)
        self.assertNotIsInstance(e.exception, equipo.Saturado)
        self.assertIn('timeout al probar unitarias', str(e.exception))

    def test_a_real_failure_still_fails_even_when_the_mac_is_busy(self):
        out = self._run([(1, '✖ AssertionError: expected 3, got 4')], load=4.8)
        self.assertFalse(out[0]['ok'])
        out = self._run([(1, 'test timed out after 15000ms')], load=0.5)       # idle Mac: the timeout is the work's
        self.assertFalse(out[0]['ok'])

    def test_no_new_work_starts_on_a_saturated_mac(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            equipo.save(root / 'state' / 'approved.json', {'t1': {}})
            with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'STATE', root / 'state'), \
                 mock.patch.object(equipo, 'tasks', lambda pid, status=None: [{'id': 't1', 'assignee': equipo.LANE, 'created_at': 1}]), \
                 mock.patch.object(equipo, 'lid_will_sleep', return_value=False), mock.patch.object(equipo, 'carga', lambda: 5.0), \
                 mock.patch.object(equipo, 'claimer', side_effect=AssertionError('no debe reclamar')):
                self.assertTrue(equipo.run_project('norte').startswith('espero: el Mac está saturado'))

    def test_saturation_during_the_retry_returns_to_the_queue_without_feedback(self):
        task = {'id': 'fixture', 'title': 'Fixture', 'assignee': equipo.LANE, 'created_at': 1}
        @equipo.contextmanager
        def board(pid): yield None
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / 'state'; folder = state / 'runs' / task['id']
            equipo.save(state / 'approved.json', {task['id']: {}})
            equipo.save(folder / 'receipt.json', {'ciclos': [], 'estado': 'en_curso'})
            def execute(*a, **kw):
                with mock.patch.object(equipo, 'saturado', side_effect=[False, True]):
                    return self._run([equipo.Stop('timeout'), equipo.Stop('timeout')], load=4.8)
            with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'board', board), mock.patch.object(equipo, 'tasks', return_value=[task]), \
                 mock.patch.object(equipo, 'claimer', return_value='fixture'), \
                 mock.patch.object(equipo.kb, 'claim_task', return_value=mock.Mock(current_run_id=1)), \
                 mock.patch.object(equipo.kb, 'reclaim_task') as reclaim, mock.patch.object(equipo.kb, 'block_task') as block, \
                 mock.patch.object(equipo, 'lid_will_sleep', return_value=False), mock.patch.object(equipo, 'carga', return_value=0), \
                 mock.patch.object(equipo, 'sync_base'), mock.patch.object(equipo, 'execute', execute), \
                 mock.patch.object(equipo.pf, 'keep_awake', return_value=mock.Mock()), mock.patch.object(equipo, 'log'), \
                 mock.patch.object(equipo, 'follow_up') as follow, mock.patch.object(equipo, 'post') as post:
                self.assertEqual(equipo.run_project('norte'), 'interrumpida')
            reclaim.assert_called_once(); block.assert_not_called(); follow.assert_not_called(); post.assert_not_called()
            receipt = equipo.load(folder / 'receipt.json', {})
            self.assertEqual(receipt['ciclos'], [])
            self.assertEqual(receipt['estado'], 'interrumpida')
            self.assertEqual(equipo.relaunch_note(receipt), '')
            self.assertEqual(equipo.load(folder / 'saturado.json', {})['veces'], 1)

class BuenosDias(unittest.TestCase):
    """04/11/2026: a morning message with today's workout from his own notes, once a day, before the to-do."""
    REAL = equipo.buenos_dias

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        vault = self.tmp / 'vault'; salud = vault / 'Personal' / 'Salud y deporte'
        (salud / '01-Registros').mkdir(parents=True); (salud / '03-Planes').mkdir()
        (salud / '01-Registros' / 'Diario-de-entrenamiento.md').write_text('### 2026-10-21 · Tracciones\n- REALIZADO: dominadas +10 kg 1×5\n')
        (salud / '03-Planes' / 'Plan-vigente.md').write_text('Empuje, tracciones, pierna, cardio.')
        self.prompts, self.enviados = [], []
        for name, value in {'VAULT': vault, 'WORKSPACE': self.tmp / 'ws', 'freno': lambda: None, 'whatsapp_activo': lambda: True,
                            'hoy_decisiones': lambda: ['juntar 2 mejoras de Norte (/lote)'], 'APP_CONFIG': {},
                            'telegram_send': lambda texto, markup=None: self.enviados.append(texto) or 1}.items():
            patcher = mock.patch.object(equipo, name, value); patcher.start(); self.addCleanup(patcher.stop)

    def claude(self, answer):
        return mock.patch.object(equipo, 'claude', lambda prompt, *a, **k: self.prompts.append(prompt) or answer)

    def test_from_half_past_eight_once_a_day_from_his_notes(self):
        texto = 'Buenos días, Owner. A por ello.\n\n🏋️ Entreno de hoy: Empuje\n- Press inclinado: 26 kg/mano · 3×8'
        with self.claude(texto):
            self.assertIsNone(BuenosDias.REAL(datetime(2026, 11, 5, 8, 10, tzinfo=equipo.MADRID)))
            self.assertEqual(BuenosDias.REAL(datetime(2026, 11, 5, 8, 35, tzinfo=equipo.MADRID)), texto)
            self.assertIsNone(BuenosDias.REAL(datetime(2026, 11, 5, 9, 0, tzinfo=equipo.MADRID)))
        self.assertEqual(self.enviados, [texto])
        self.assertIn('dominadas +10 kg', self.prompts[0]); self.assertIn('juntar 2 mejoras de Norte', self.prompts[0])
        self.assertEqual((self.tmp / 'ws' / 'manana' / '2026-11-05.md').read_text(), texto + '\n')
        with self.claude('Buenos días, Owner.\n🏋️ Entreno de hoy: Pierna'):    # the next day sees what was proposed the day before
            BuenosDias.REAL(datetime(2026, 11, 6, 8, 40, tzinfo=equipo.MADRID))
        self.assertIn('Press inclinado: 26 kg/mano', self.prompts[1])

    def test_a_bad_answer_sends_nothing_and_is_not_retried_every_five_minutes(self):
        with self.claude('Lo siento, no puedo'):
            self.assertIsNone(BuenosDias.REAL(datetime(2026, 11, 5, 8, 35, tzinfo=equipo.MADRID)))
            self.assertIsNone(BuenosDias.REAL(datetime(2026, 11, 5, 8, 40, tzinfo=equipo.MADRID)))
        self.assertEqual((self.enviados, len(self.prompts)), ([], 1))


class EnCristiano(unittest.TestCase):
    """04/11/2026: Owner did not understand the team's technical notices; they reach him reworded with what he has to do."""
    REAL = equipo.en_cristiano          # captured before the module guards turn it into the identity

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        for name, value in {'STATE': Path(self.tmp.name), 'freno': lambda: None}.items():
            patcher = mock.patch.object(equipo, name, value); patcher.start(); self.addCleanup(patcher.stop)
        self.prompts = []

    def claude(self, answer):
        return mock.patch.object(equipo, 'claude', lambda prompt, *a, **k: self.prompts.append(prompt) or answer)

    def test_a_technical_notice_is_reworded_once_with_its_buttons_and_cached(self):
        aviso = '🚀 Listo para lanzar en Norte: «Landing VSL»\nAún no está en la versión principal: se fusiona esta noche.'
        botones = [[{'text': 'Ya lo he lanzado', 'callback_data': 'l:t_1'}]]
        llano = 'Tu página nueva de Norte está lista.\nTú: ábrela y publícala (5 min).\nYa lo he lanzado = me dices que ya está publicada.'
        with self.claude(llano):
            self.assertEqual(EnCristiano.REAL(aviso, botones), llano)
            self.assertEqual(EnCristiano.REAL(aviso, botones), llano)
        self.assertEqual(len(self.prompts), 1)
        self.assertIn('«Ya lo he lanzado»', self.prompts[0]); self.assertIn('Landing VSL', self.prompts[0])

    def test_short_plain_lines_brake_and_bad_answers_keep_the_original(self):
        with self.claude('no debería llamarse'):
            self.assertEqual(EnCristiano.REAL('Hoy: 3/5 hecho. Mañana sigo con: afiliaciones'), 'Hoy: 3/5 hecho. Mañana sigo con: afiliaciones')
        self.assertEqual(self.prompts, [])
        aviso = 'Norte: 2 mejoras fusionadas en la rama de producción'
        with self.claude('Una respuesta sin la línea de qué hacer'):
            self.assertEqual(EnCristiano.REAL(aviso), aviso)
        with mock.patch.object(equipo, 'freno', lambda: 'plan al 90 %'), self.claude('x\nTú: nada'):
            self.assertEqual(EnCristiano.REAL(aviso + ' hoy'), aviso + ' hoy')


class WhatsApp(unittest.TestCase):
    """01/11/2026: with the WhatsApp bridge connected, the team's notices and the morning list go to its outbox, not to Telegram."""
    REAL_SEND = equipo.telegram_send          # the module guards replace it with a no-op; here it is what is under test

    def setUp(self):
        self.dir = equipo.TELEGRAM_LINK.parent / 'whatsapp'
        shutil.rmtree(self.dir, ignore_errors=True)
        patcher = mock.patch.object(equipo, 'telegram_send', WhatsApp.REAL_SEND); patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.dir, True)

    def conectar(self, hace=0):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / 'estado.json').write_text(json.dumps({'conectado': True, 'hora': (equipo.now() - timedelta(seconds=hace)).isoformat()}))

    def salida(self):
        folder = self.dir / 'salida'
        return [json.loads(f.read_text()) for f in sorted(folder.glob('*.json'))] if folder.exists() else []

    def test_connected_bridge_gets_the_notice_with_its_buttons_and_telegram_is_not_called(self):
        self.conectar()
        botones = {'inline_keyboard': [[{'text': 'Empezar', 'callback_data': 'r:s'}]]}
        with mock.patch('urllib.request.urlopen', side_effect=AssertionError('no debe ir a Telegram')):
            ref = equipo.telegram_send('Repaso de hoy: 3 tarjetas (2 min).', botones)
            self.assertTrue(equipo.telegram('Equipo: lote listo'))
        out = self.salida()
        self.assertEqual([o['texto'] for o in out], ['Repaso de hoy: 3 tarjetas (2 min).', 'Equipo: lote listo'])
        self.assertEqual((out[0]['ref'], out[0]['botones']), (ref, botones['inline_keyboard']))

    def test_silent_bridge_or_turned_off_goes_back_to_telegram(self):
        self.conectar(hace=600)
        self.assertFalse(equipo.whatsapp_activo())
        self.conectar()
        with mock.patch.object(equipo, 'APP_CONFIG', {'whatsapp': {'avisos': False}}):
            self.assertFalse(equipo.whatsapp_activo())
        self.assertTrue(equipo.whatsapp_activo())

    def test_morning_list_goes_out_without_a_telegram_link(self):
        self.conectar()
        at = datetime(2026, 11, 4, 9, 5, tzinfo=equipo.MADRID)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'WORKSPACE', Path(tmp)), mock.patch.object(equipo, 'app_abierta', return_value=False), \
             mock.patch.object(equipo, 'freno', return_value=''), mock.patch.object(equipo, 'hoy_decisiones', return_value=[]), mock.patch.object(equipo, 'hoy_detalles', return_value=[]), \
             mock.patch.object(equipo, 'claude', return_value='- Grabar el reel :: 20 min\n- Llamar al gestor'):
            self.assertEqual(equipo.hoy_manana(at), ['Grabar el reel', 'Llamar al gestor'])
            guardado = json.loads((Path(tmp) / 'hoy' / '2026-11-04.json').read_text())
        lista = self.salida()[0]
        self.assertTrue(lista['texto'].startswith('Hoy, mié 4'))
        self.assertEqual([b[0]['callback_data'] for b in lista['botones']], ['h:2026-11-04:0', 'h:2026-11-04:1'])
        self.assertEqual(guardado['mensaje'], lista['ref'])

class TraerBase(unittest.TestCase):
    """03/11: PRs merged on GitHub never reached the local base, so the Norte team built on code 39 commits old."""
    def repos(self, tmp, checkout_base=False):
        run = lambda cwd, *a: subprocess.run(['git', '-C', str(cwd), '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True, capture_output=True, text=True).stdout.strip()
        origin, mac = Path(tmp) / 'origin', Path(tmp) / 'mac'
        subprocess.run(['git', 'init', '-q', '-b', 'prod', str(origin)], check=True)
        (origin / 'a.txt').write_text('1'); run(origin, 'add', '.'); run(origin, 'commit', '-qm', 'uno')
        subprocess.run(['git', 'clone', '-q', str(origin), str(mac)], check=True)
        if not checkout_base: run(mac, 'checkout', '-qb', 'otra')
        (origin / 'b.txt').write_text('2'); run(origin, 'add', '.'); run(origin, 'commit', '-qm', 'pr de github')
        return run, origin, mac

    def traer(self, mac):
        with mock.patch.object(equipo, 'project', lambda pid: {'repo': mac, 'base': 'prod'}), mock.patch.object(equipo, 'log', lambda *a, **k: None), \
             mock.patch.object(equipo, 'post', lambda *a, **k: self.avisos.append(a)), tempfile.TemporaryDirectory() as st, mock.patch.object(equipo, 'STATE', Path(st)):
            return equipo.traer_base('norte')

    def setUp(self): self.avisos = []

    def test_a_base_that_is_not_checked_out_moves_to_what_github_has(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, origin, mac = self.repos(tmp)
            self.assertEqual(self.traer(mac), 'traída')
            self.assertEqual(run(mac, 'rev-parse', 'prod'), run(origin, 'rev-parse', 'prod'))
            self.assertEqual(self.traer(mac), 'al día')

    def test_a_checked_out_base_moves_only_without_unsaved_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, origin, mac = self.repos(tmp, checkout_base=True)
            (mac / 'a.txt').write_text('a medias')
            self.assertEqual(self.traer(mac), 'con cambios sin guardar')
            run(mac, 'checkout', '--', 'a.txt')
            self.assertEqual(self.traer(mac), 'traída')
            self.assertTrue((mac / 'b.txt').exists())

    def test_diverged_branches_do_not_move_and_operations_hears_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, origin, mac = self.repos(tmp)
            run(mac, 'checkout', '-q', 'prod'); (mac / 'c.txt').write_text('3'); run(mac, 'add', '.'); run(mac, 'commit', '-qm', 'solo en el mac'); run(mac, 'checkout', '-q', 'otra')
            before = run(mac, 'rev-parse', 'prod')
            self.assertEqual(self.traer(mac), 'separadas')
            self.assertEqual(run(mac, 'rev-parse', 'prod'), before)
            self.assertEqual(len(self.avisos), 1)

    def test_norte_is_merged_by_the_daily_pr_not_by_the_night(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'repo': '/x', 'base': 'prod', 'fusion': 'pr'}):
            self.assertEqual(equipo.pending_merge('norte'), 0)
            self.assertIn('PR diaria', equipo.fusionar_project('norte'))

class Relaunch(unittest.TestCase):
    def test_relaunch_starts_from_what_blocked_it(self):
        prev = {'estado': 'bloqueada', 'ciclos': [{'revision': {'decision': 'cambios', 'motivos': 'Envuelve el fetch en el control de timedOut.'}}]}
        self.assertIn('timedOut', equipo.relaunch_note(prev))
        prev = {'estado': 'bloqueada', 'ciclos': [{'pruebas': [{'ok': False, 'salida': '400 !== 200'}]}]}
        self.assertIn('400 !== 200', equipo.relaunch_note(prev))
        self.assertEqual(equipo.relaunch_note({'estado': 'integrada'}), '')
        self.assertEqual(equipo.relaunch_note({}), '')

class FailureExcerpt(unittest.TestCase):
    def test_condensed_uses_first_600_failure_characters(self):
        failure = '✖ failing tests:' + ' detalle' * 200
        receipt = {'ciclos': [{'pruebas': [{'prueba': 'equipo', 'ok': False, 'salida': 'salida antigua', 'fallo': failure}]}]}
        output = equipo.condensed(receipt)
        self.assertEqual(output['ciclos'][0]['pruebas'][0]['salida'], failure[:600])
        self.assertTrue(output['ciclos'][0]['pruebas'][0]['salida'].startswith('✖ failing tests:'))

    def test_condensed_uses_output_tail_without_failure_excerpt(self):
        output_text = 'inicio' + 'x' * 500
        receipt = {'ciclos': [{'pruebas': [{'prueba': 'equipo', 'ok': False, 'salida': output_text}]}]}
        output = equipo.condensed(receipt)
        self.assertEqual(output['ciclos'][0]['pruebas'][0]['salida'], output_text[-300:])

    def test_condensed_keeps_passing_test_output_empty(self):
        receipt = {'ciclos': [{'pruebas': [{'prueba': 'equipo', 'ok': True, 'salida': 'todo correcto', 'fallo': 'no importa'}]}]}
        output = equipo.condensed(receipt)
        self.assertEqual(output['ciclos'][0]['pruebas'][0]['salida'], '')

    def test_condensed_keeps_unnamed_test_with_missing_status(self):
        receipt = {'ciclos': [{'pruebas': [{}]}]}
        output = equipo.condensed(receipt)
        self.assertEqual(output['ciclos'][0]['pruebas'][0], {'prueba': 'prueba sin nombre', 'ok': None, 'salida': ''})

    def test_node_keeps_failure_details_without_successes(self):
        out = ('✔ primera\n✖ suma\nℹ tests 2\n✖ failing tests:\n'
               'test at suma.test.js:12:3\n✖ suma\n  expected: 2\n  actual: 3\n  ✔ otra\n')
        excerpt = equipo.failure_excerpt(out)
        self.assertTrue(excerpt.startswith('✖ failing tests:'))
        self.assertIn('test at suma.test.js:12:3', excerpt)
        self.assertIn('expected: 2', excerpt)
        self.assertIn('actual: 3', excerpt)
        self.assertNotIn('✔', excerpt)
        self.assertEqual(equipo.failure_excerpt(out, 30), excerpt[:30])

    def test_unittest_keeps_failure_block(self):
        out = '✔ previa\n.F\n======\nFAIL: test_suma\nTraceback:\nAssertionError: 3 != 2\n  ✔ otra\n'
        excerpt = equipo.failure_excerpt(out)
        self.assertTrue(excerpt.startswith('======'))
        self.assertIn('FAIL: test_suma', excerpt)
        self.assertIn('AssertionError: 3 != 2', excerpt)
        self.assertNotIn('✔', excerpt)

    def test_tsc_keeps_error_lines(self):
        out = 'Compilando\n  ✔ previa\napp.ts(1,1): error TS2322: incompatible\nERROR: otro fallo\nFin\n'
        excerpt = equipo.failure_excerpt(out)
        self.assertEqual(excerpt, 'app.ts(1,1): error TS2322: incompatible\nERROR: otro fallo\n')
        self.assertNotIn('✔', excerpt)

    def test_keeps_fail_line_without_passes(self):
        out = ''.join(f'PASS caso {n}\n' for n in range(30))
        out += 'FAIL a single grouped launch drops: expected 1\n'
        out += ''.join(f'PASS caso final {n}\n' for n in range(5))
        excerpt = equipo.failure_excerpt(out)
        self.assertTrue(excerpt.startswith('FAIL a single grouped launch drops'))
        self.assertIn('expected 1', excerpt)
        self.assertNotIn('PASS', excerpt)

    def test_run_tests_keeps_fail_line_before_truncating_output(self):
        out = ''.join(f'PASS caso {n}\n' for n in range(30))
        out += 'FAIL a single grouped launch drops: expected 1\n'
        out += ''.join(f'PASS caso final {n}\n' for n in range(5))
        with mock.patch.object(equipo, 'project', lambda pid: {'tests': {'motor-local': {'cmd': ['motor-local']}}}), \
             mock.patch.object(equipo, 'run', lambda *a, **kw: (1, out)):
            results = equipo.run_tests('prompted-os', {'pruebas': ['motor-local']}, Path('/tmp'), Path('/tmp'), None)
        self.assertTrue(results[0]['fallo'].startswith('FAIL '))

    def test_fallback_keeps_tail_without_successes(self):
        self.assertEqual(equipo.failure_excerpt('inicio\n  ✔ previa\nfinal', 8), 'io\nfinal')
        self.assertEqual(equipo.failure_excerpt('error', 0), '')

    def test_run_tests_extracts_before_truncating_output(self):
        out = '✖ failing tests:\nexpected: 2\nactual: 3\n' + 'detalle\n' * 500
        with mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'cmd': ['tsc']}}}), \
             mock.patch.object(equipo, 'run', lambda *a, **kw: (1, out)):
            results = equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path('/tmp'), Path('/tmp'), None)
        self.assertEqual(results[0]['salida'], out[-1500:])
        self.assertEqual(results[0]['fallo'], out[:3000])
        self.assertFalse(results[0]['ok'])

    def test_relaunch_prefers_failure_excerpt(self):
        prev = {'estado': 'bloqueada', 'ciclos': [{'pruebas': [{'ok': False, 'salida': '...', 'fallo': 'BLOQUE-FALLO'}]}]}
        self.assertIn('BLOQUE-FALLO', equipo.relaunch_note(prev))

    def test_attempts_passes_failure_excerpt_to_retry_and_stop(self):
        prompts = []
        def claude(*a, **kw):
            prompts.append(a[0]); return 'hecho'
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(equipo.LIMITS, {'attempts': 2}), \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', lambda *a, **kw: [{'ok': False, 'salida': '...', 'fallo': 'BLOQUE-FALLO'}]), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertEqual(len(prompts), 2)
        self.assertIn('BLOQUE-FALLO', prompts[1])
        self.assertEqual(str(ctx.exception), 'agotados los intentos: BLOQUE-FALLO')

    def test_attempts_stops_with_fail_line_without_passes(self):
        prompts = []
        def claude(*a, **kw):
            prompts.append(a[0]); return 'hecho'
        out = ''.join(f'PASS caso {n}\n' for n in range(30))
        out += 'FAIL a single grouped launch drops: expected 1\n'
        out += ''.join(f'PASS caso final {n}\n' for n in range(5))
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        result = {'ok': False, 'salida': out[-1500:], 'fallo': equipo.failure_excerpt(out)}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(equipo.LIMITS, {'attempts': 2}), \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', lambda *a, **kw: [result]), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertTrue(str(ctx.exception).startswith('agotados los intentos: FAIL '))
        self.assertNotIn('PASS', str(ctx.exception))

    def run_attempts_with_test_results(self, results, files=('alfred-os/app/board.tsx',), attempts=2):
        """Runs attempts() with the given successive run_tests results; returns (prompts, test folders, receipt, error, integrated)."""
        prompts, folders, integrated = [], [], []
        def claude(*a, **kw):
            prompts.append(a[0])
            return '{"decision": "aprobar", "motivos": "bien"}' if kw.get('role') == 'revisor' else 'hecho'
        def run_tests(pid, spec, path, folder, heartbeat, repeated_tests=None):
            folders.append(folder); result = results[min(len(folders), len(results)) - 1]
            if isinstance(result, Exception): raise result
            return result
        def integrate(*a, **kw):
            integrated.append(len(prompts)); return 'a' * 40
        spec = {'titulo': 'x', 'archivos': list(files), 'pruebas': ['norte']}
        receipt = {'ciclos': []}; error = None
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(equipo.LIMITS, {'attempts': attempts}), \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (list(files), [])), \
             mock.patch.object(equipo, 'run_tests', run_tests), \
             mock.patch.object(equipo, 'integrate', integrate), \
             mock.patch.object(equipo, 'git', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'save', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'post', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'follow_up', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            try: equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
            except equipo.Stop as e: error = e
        return prompts, folders, receipt, error, integrated

    def test_attempts_repeats_tests_once_after_server_start_failure(self):
        down = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'Error: server did not become healthy'}]
        up = [{'prueba': 'norte', 'ok': True, 'salida': 'ok'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([down, up])
        self.assertIsNone(error)
        self.assertEqual(integrated, [2])   # one builder call and one reviewer call, all in attempt 1
        self.assertEqual([f.name for f in folders[:2]], [folders[0].name, f'{folders[0].name}-reintento'])
        self.assertEqual(receipt['ciclos'][0]['pruebas_infra'], down)
        self.assertEqual(receipt['ciclos'][0]['pruebas'], up)

    def test_attempts_stops_with_infrastructure_when_the_server_never_starts(self):
        down = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'Error: server did not become healthy'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([down, down])
        self.assertIsNotNone(error)
        self.assertTrue(str(error).startswith('infraestructura'))
        self.assertIn('en norte', str(error))
        self.assertIn('did not become healthy', str(error))
        self.assertEqual(len(prompts), 1)
        self.assertEqual(len(folders), 2)
        self.assertEqual(receipt['causa'], 'infraestructura')
        self.assertEqual(len(receipt['ciclos']), 1)
        self.assertNotIn('pruebas_mixtas', receipt['ciclos'][0])
        self.assertEqual(integrated, [])

    def test_attempts_passes_mixed_start_and_assertion_failure_to_builder(self):
        mixed = [{'prueba': 'norte', 'ok': False, 'salida': 'server did not become healthy\nAssertionError: 3 != 2'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([mixed], attempts=2)
        self.assertTrue(str(error).startswith('agotados los intentos'))
        self.assertNotIn('causa', receipt)
        self.assertEqual(len(receipt['ciclos']), 2)
        self.assertTrue(receipt['ciclos'][0].get('pruebas_mixtas'))
        self.assertTrue(all('pruebas_infra' not in cycle for cycle in receipt['ciclos']))
        self.assertEqual(len(folders), 2)
        self.assertEqual(len(prompts), 2)
        self.assertIn('AssertionError: 3 != 2', prompts[1])
        self.assertEqual(integrated, [])

    def test_attempts_notes_when_both_attempts_fail_with_the_same_fail_line(self):
        same = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'FAIL a single grouped launch drops: expected 1\n' + 'detalle largo\n' * 60}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([same, same])
        message = str(error)
        self.assertTrue(message.startswith('agotados los intentos: FAIL a single grouped launch drops'))
        self.assertTrue(message.endswith(' (mismo fallo en los 2 intentos)'))
        self.assertLessEqual(len(message), 500)
        self.assertEqual(integrated, [])

    def test_attempts_does_not_note_different_fail_lines(self):
        uno = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'FAIL uno'}]
        dos = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'FAIL dos'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([uno, dos])
        self.assertEqual(str(error), 'agotados los intentos: FAIL dos')
        self.assertNotIn('mismo fallo', str(error))

    def test_attempts_puts_the_fail_line_first_when_the_excerpt_would_cut_it(self):
        def result(final):
            salida = ''.join(f'PASS caso {n}\n' for n in range(40)) + 'FAIL choosing the first reason drops: expected 1\n' + ''.join(f'PASS {final} {n}\n' for n in range(5))
            return [{'prueba': 'norte', 'ok': False, 'fallo': '', 'salida': salida}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([result('uno'), result('dos')])
        message = str(error)
        self.assertTrue(message.startswith('agotados los intentos: FAIL choosing the first reason drops'))
        self.assertLessEqual(len(message), 500)

    def test_attempts_puts_the_fail_line_first_before_the_same_fail_note(self):
        same = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'detalle de cabecera larga\n' * 30 + 'FAIL z'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([same, same])
        message = str(error)
        self.assertTrue(message.startswith('agotados los intentos: FAIL z'))
        self.assertTrue(message.endswith(' (mismo fallo en los 2 intentos)'))
        self.assertLessEqual(len(message), 500)

    def test_attempts_shows_the_real_error_at_the_end_when_there_is_no_fail_line(self):
        salida = ''.join(f'PASS caso {n}\n' for n in range(40)) + 'file:///x.mjs:10\n    throw new Error("boom")\n\nError: el servidor no arrancó: boom\n    at main (file:///x.mjs:10:11)\n\nNode.js v22.11.0\n'
        result = [{'prueba': 'norte', 'ok': False, 'fallo': '', 'salida': salida}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([result, result])
        message = str(error)
        self.assertTrue(message.startswith('agotados los intentos: '))
        self.assertIn('Error: el servidor no arrancó: boom', message)
        self.assertNotIn('PASS', message)
        self.assertLessEqual(len(message), 500)

    def test_attempts_keeps_the_tail_of_a_long_failure_without_fail_line(self):
        fallo = ''.join(f'detalle {n}\n' for n in range(80)) + 'Error: final de verdad'
        result = [{'prueba': 'norte', 'ok': False, 'fallo': fallo, 'salida': '...'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([result, result])
        message = str(error)
        self.assertIn('Error: final de verdad', message)
        self.assertNotIn('detalle 0\n', message)
        self.assertLessEqual(len(message), 500)

    def test_cola_fallo_cuts_a_single_long_line_from_the_start(self):
        self.assertEqual(equipo.cola_fallo('x' * 50 + 'FINAL', 20), 'x' * 15 + 'FINAL')
        self.assertEqual(equipo.cola_fallo('PASS a\n\n  PASS b\nError: c\n', 100), 'Error: c')

    def test_primera_fail(self):
        self.assertEqual(equipo.primera_fail('PASS x\n  FAIL y\nFAIL z'), 'FAIL y')
        self.assertEqual(equipo.primera_fail('ok 1 a\n  not ok 2 b  \nFAIL z'), 'not ok 2 b')
        self.assertEqual(equipo.primera_fail('PASS x'), '')

    def test_run_tests_does_not_repeat_mixed_start_and_assertion_failure(self):
        with mock.patch.object(equipo, 'run', return_value=(1, 'server did not become healthy\nAssertionError: x')) as run, \
             mock.patch.object(equipo, 'project', return_value={'tests': {'demo': {'cmd': ['fixture']}}}):
            results = equipo.run_tests('fixture', {'pruebas': ['demo']}, Path('/tmp'), Path('/tmp'), None)
        run.assert_called_once()
        self.assertEqual(run.call_args.kwargs['out'].name, 'test-demo-0.log')
        self.assertFalse(results[0]['ok'])
        self.assertNotIn('repetida', results[0])

    def test_attempts_and_run_tests_share_the_single_infrastructure_retry(self):
        # Both functions run for real: only the subprocess/model boundaries are fake.
        # This used to execute four suites because each layer retried independently.
        cases = [((1, 'did not become healthy'), (1, 'did not become healthy'), 'arranque', '-r'),
                 (equipo.Stop('timeout'), (1, 'did not become healthy'), 'timeout', '-t'),
                 ((1, 'waitForHealth timeout'), (1, 'did not become healthy'), 'arranque', ''),
                 ((1, 'startDemoServer'), equipo.Stop('timeout'), 'arranque', '')]
        for first, second, cause, suffix in cases:
            with self.subTest(first=str(first), second=str(second)), tempfile.TemporaryDirectory() as tmp:
                runs = []; receipt = {'ciclos': []}
                def run(*args, **kwargs):
                    runs.append(kwargs['out'])
                    result = first if len(runs) == 1 else second
                    if isinstance(result, Exception):
                        kwargs['out'].parent.mkdir(parents=True, exist_ok=True)
                        kwargs['out'].write_text('fixture_waiting ...\n')
                        raise result
                    return result
                spec = {'titulo': 'Fixture', 'archivos': ['src/fixture.js'], 'pruebas': ['demo']}
                with mock.patch.object(equipo, 'run', run), \
                     mock.patch.object(equipo, 'project', return_value={'tests': {'demo': {'cmd': ['fixture']}}}), \
                     mock.patch.object(equipo, 'claude', return_value='hecho') as model, \
                     mock.patch.object(equipo, 'changed', return_value=(['src/fixture.js'], [])), \
                     mock.patch.object(equipo, 'phase'), mock.patch.object(equipo, 'lessons_text', return_value=''), \
                     mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'log'), \
                     mock.patch.object(equipo, 'git', side_effect=AssertionError('no review or Git after infrastructure failure')), \
                     mock.patch.object(equipo, 'integrate') as integrate:
                    expected = '^timeout al probar' if isinstance(second, Exception) else '^infraestructura:'
                    with self.assertRaisesRegex(equipo.Stop, expected):
                        equipo.attempts('fixture', {'name':'Fixture'}, spec, {'id':'fixture'}, 'equipo/fixture', 'b', Path(tmp), Path(tmp), receipt, None)
                self.assertEqual([p.name for p in runs], ['test-demo-0.log', f'test-demo-0{suffix}.log'])
                self.assertEqual(model.call_count, 1, 'infrastructure must not consume another builder attempt')
                integrate.assert_not_called()
                self.assertEqual(receipt['causa'], 'infraestructura')
                self.assertEqual(len(receipt['ciclos']), 1)
                self.assertEqual(receipt['ciclos'][0]['pruebas'][0]['repetida_por'], cause)
                if isinstance(second, Exception):
                    self.assertTrue(receipt['ciclos'][0]['pruebas'][0]['timeout'])
                    self.assertEqual(receipt['ciclos'][0]['pruebas'][0]['ultima_linea'], 'fixture_waiting ...')
                else: self.assertTrue(receipt['ciclos'][0]['pruebas'][0]['repetida'])

    def test_external_infrastructure_retry_keeps_passed_suites_and_runs_the_remaining_ones(self):
        receipt = {'ciclos': []}; calls = []
        def run(step, *args, **kwargs):
            calls.append(step[0])
            return (1, 'waitForHealth timeout') if calls == ['a', 'b'] else (0, 'ok')
        def model(*args, **kwargs):
            return '{"decision":"aprobar"}' if kwargs.get('role') == 'revisor' else 'hecho'
        spec = {'titulo':'Fixture', 'archivos':['src/fixture.js'], 'pruebas':['a', 'b', 'c']}
        tests = {name: {'cmd':[name]} for name in spec['pruebas']}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'run', run), \
             mock.patch.object(equipo, 'project', return_value={'tests':tests}), mock.patch.object(equipo, 'claude', model), \
             mock.patch.object(equipo, 'changed', return_value=(['src/fixture.js'], [])), \
             mock.patch.object(equipo, 'phase'), mock.patch.object(equipo, 'lessons_text', return_value=''), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'git', return_value=''), mock.patch.object(equipo, 'integrate', return_value='fixture-commit') as integrate:
            self.assertIn('Integrada', equipo.attempts('fixture', {'name':'Fixture'}, spec, {'id':'fixture'}, 'equipo/fixture', 'b', Path(tmp), Path(tmp), receipt, None))
        self.assertEqual(calls, ['a', 'b', 'b', 'c'])
        results = receipt['ciclos'][0]['pruebas']
        self.assertEqual([t['prueba'] for t in results], ['a', 'b', 'c'])
        self.assertTrue(all(t['ok'] for t in results))
        self.assertNotIn('repetida', results[0]); self.assertNotIn('repetida', results[2])
        self.assertEqual(results[1]['repetida_por'], 'arranque')
        integrate.assert_called_once()

    def test_later_suite_timeout_does_not_inherit_the_recovered_infrastructure_cause(self):
        receipt = {'ciclos': []}; calls = []
        def run(step, *args, **kwargs):
            calls.append(step[0])
            if step[0] == 'c': raise equipo.Stop('timeout')
            return (1, 'waitForHealth timeout') if calls == ['a', 'b'] else (0, 'ok')
        spec = {'titulo':'Fixture', 'archivos':['src/fixture.js'], 'pruebas':['a', 'b', 'c']}
        tests = {name: {'cmd':[name]} for name in spec['pruebas']}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'run', run), \
             mock.patch.object(equipo, 'project', return_value={'tests':tests}), mock.patch.object(equipo, 'claude', return_value='hecho'), \
             mock.patch.object(equipo, 'changed', return_value=(['src/fixture.js'], [])), \
             mock.patch.object(equipo, 'phase'), mock.patch.object(equipo, 'lessons_text', return_value=''), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'log'):
            with self.assertRaisesRegex(equipo.Stop, '^timeout al probar c'):
                equipo.attempts('fixture', {'name':'Fixture'}, spec, {'id':'fixture'}, 'equipo/fixture', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertEqual(calls, ['a', 'b', 'b', 'c', 'c'])
        self.assertNotIn('causa', receipt)
        self.assertEqual(receipt['ciclos'][0]['pruebas'][-1]['repetida_por'], 'timeout')

    def test_attempts_keeps_the_cycle_when_the_repeated_tests_time_out(self):
        down = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'Error: server did not become healthy'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([down, equipo.Stop('timeout al probar norte (900 s)')])
        self.assertEqual(str(error), 'timeout al probar norte (900 s)')
        self.assertEqual(len(folders), 2)
        self.assertEqual(len(receipt['ciclos']), 1)
        self.assertEqual(receipt['ciclos'][0]['pruebas'], [{'prueba': 'norte', 'ok': False, 'timeout': True, 'repetida_por': 'timeout'}])
        self.assertEqual(receipt['ciclos'][0]['pruebas_infra'], down)
        self.assertEqual(integrated, [])

    def test_attempts_lets_other_stops_in_the_repeated_tests_through(self):
        down = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'Error: server did not become healthy'}]
        prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([down, equipo.Stop('claim_lost')])
        self.assertEqual(str(error), 'claim_lost')
        self.assertEqual(receipt['ciclos'], [])

    def test_attempts_does_not_repeat_when_the_diff_touches_the_test_server(self):
        down = [{'prueba': 'norte', 'ok': False, 'salida': '...', 'fallo': 'Error: server did not become healthy'}]
        for files in (['norte/test/helpers/server.js'], ['norte/src/server.js']):
            with self.subTest(files=files):
                prompts, folders, receipt, error, integrated = self.run_attempts_with_test_results([down], files=files)
                self.assertTrue(str(error).startswith('agotados los intentos'))
                self.assertEqual(len(prompts), 2)
                self.assertEqual(len(folders), 2)   # one run per attempt, no repeat
                self.assertNotIn('causa', receipt)

    def test_infra_failure_ignores_a_normal_assertion_failure(self):
        normal = [{'prueba': 'norte', 'ok': False, 'salida': 'AssertionError: 3 != 2', 'fallo': 'AssertionError: 3 != 2'}]
        self.assertFalse(equipo.infra_failure(normal, ['norte/src/app.js']))
        mixed = [{'prueba': 'norte', 'ok': False, 'salida': 'server did not become healthy\nAssertionError: 3 != 2'}]
        self.assertFalse(equipo.infra_failure(mixed, ['norte/src/app.js']))
        down = [{'prueba': 'norte', 'ok': False, 'salida': 'waitForHealth timeout'}]
        self.assertTrue(equipo.infra_failure(down, ['norte/src/app.js']))
        self.assertFalse(equipo.infra_failure(down, ['norte/test/helpers/demo.js']))

    def test_infra_failure_assertions_override_markers_in_other_failed_tests(self):
        down = {'prueba': 'norte', 'ok': False, 'salida': 'waitForHealth timeout'}
        for field in ('fallo', 'salida'):
            for assertion in ('AssertionError: 3 != 2', 'ERR_ASSERTION'):
                failed = {'prueba': 'demo', 'ok': False, field: assertion}
                for tests in ([down, failed], [failed, down]):
                    with self.subTest(field=field, assertion=assertion, tests=tests):
                        self.assertFalse(equipo.infra_failure(tests, ['norte/src/app.js']))
                self.assertTrue(equipo.infra_failure([down, {**failed, 'ok': True}], ['norte/src/app.js']))

    def test_pruebas_mixtas(self):
        marker = {'prueba': 'norte', 'ok': False, 'salida': 'waitForHealth timeout'}
        assertion = {'prueba': 'demo', 'ok': False, 'fallo': 'AssertionError: 3 != 2'}
        self.assertTrue(equipo.pruebas_mixtas([{**marker, 'fallo': 'ERR_ASSERTION'}]))
        self.assertTrue(equipo.pruebas_mixtas([marker, assertion]))
        self.assertFalse(equipo.pruebas_mixtas([assertion]))
        self.assertFalse(equipo.pruebas_mixtas([marker]))
        self.assertFalse(equipo.pruebas_mixtas([{**marker, 'ok': True}, assertion]))

    def test_attempts_no_changes_keeps_what_the_builder_said(self):
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        def no_tests(*a, **kw): raise AssertionError('no deben ejecutarse las pruebas')
        cases = [('YA ESTABA HECHO: board.tsx líneas 10-20', 'ya estaba hecho según el constructor: board.tsx líneas 10-20'),
                 ('No he sabido hacerlo', None), ('', 'el constructor no cambió ningún archivo permitido')]
        for said, expected in cases:
            with self.subTest(said=said):
                prompts = []
                def claude(*a, **kw):
                    prompts.append(a[0]); return said
                receipt = {'ciclos': []}
                with tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.object(equipo, 'claude', claude), \
                     mock.patch.object(equipo, 'changed', lambda *a, **kw: ([], [])), \
                     mock.patch.object(equipo, 'run_tests', no_tests), \
                     mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
                     mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
                     mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
                     mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
                     mock.patch.object(equipo, 'log', lambda *a, **kw: None):
                    with self.assertRaises(equipo.Stop) as ctx:
                        equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
                if expected: self.assertEqual(str(ctx.exception), expected)
                else:
                    self.assertIn('no cambió ningún archivo permitido', str(ctx.exception))
                    self.assertIn(said, str(ctx.exception))
                self.assertEqual(len(receipt['ciclos']), 1)
                self.assertIn('YA ESTABA HECHO', prompts[0])

    def test_failing_test_outside_the_task_is_allowed_next_attempt_and_the_reviewer_is_told(self):
        if equipo.LIMITS['attempts'] < 2: self.skipTest('hace falta más de un intento')
        spec = {'titulo': 'Contador', 'archivos': ['lib/link-clicks.js'], 'pruebas': ['unitarias']}
        prompts, changed_with = [], []
        def claude(prompt, *a, **kw):
            prompts.append((kw.get('role'), prompt))
            return '{"decision": "aprobar", "motivos": "bien"}' if kw.get('role') == 'revisor' else 'hecho'
        runs = iter([[{'prueba': 'unitarias', 'ok': False, 'salida': 'x', 'fallo': '✖ failing tests:\n\ntest at test/contenido-reels-doc.test.js:26:1\n✖ la versión mínima…'}],
                     [{'prueba': 'unitarias', 'ok': True, 'salida': 'ok'}]])
        def changed(path, allowed, links):
            changed_with.append(list(allowed)); return [f for f in allowed], []
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'test').mkdir(); (Path(tmp) / 'test' / 'contenido-reels-doc.test.js').write_text('//')
            with mock.patch.object(equipo, 'claude', claude), mock.patch.object(equipo, 'changed', changed), \
                 mock.patch.object(equipo, 'run_tests', lambda *a, **kw: next(runs)), mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
                 mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
                 mock.patch.object(equipo, 'discard', lambda *a, **kw: None), mock.patch.object(equipo, 'log', lambda *a, **kw: None), \
                 mock.patch.object(equipo, 'git', lambda path, *args, **kw: 'diff --git a b' if '--cached' in args else ''), \
                 mock.patch.object(equipo, 'integrate', lambda *a, **kw: 'abc12345'), mock.patch.object(equipo, 'base_roja', lambda *a, **kw: ''):
                out = equipo.attempts('norte', {'name': 'Norte'}, spec, {'id': 't_1'}, 'equipo/norte', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertIn('Integrada', out)
        self.assertEqual(changed_with, [['lib/link-clicks.js'], ['lib/link-clicks.js', 'test/contenido-reels-doc.test.js']])
        builds = [p for r, p in prompts if r == 'constructor']
        self.assertNotIn('test/contenido-reels-doc.test.js', builds[0].split('Reglas:')[1].split('\n')[0])
        self.assertIn('test/contenido-reels-doc.test.js', builds[1])
        self.assertIn('No las debilites', builds[1])
        review = [p for r, p in prompts if r == 'revisor'][0]
        self.assertIn('aviso_revisor', review)
        self.assertEqual(receipt['ciclos'][0]['pruebas_ampliadas'], ['test/contenido-reels-doc.test.js'])
        self.assertEqual(spec['archivos'], ['lib/link-clicks.js'])          # the approved spec itself is not rewritten

    def test_failing_test_files_reads_node_and_python_output_and_skips_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            for f in ('test/a.test.js', 'test/b.integration.test.js', 'equipo/test_x.py', 'test/c.test.mjs'):
                (Path(tmp) / f).parent.mkdir(parents=True, exist_ok=True); (Path(tmp) / f).write_text('')
            out = equipo.failing_test_files(f'test at test/a.test.js:3:1\ntest at test/a.test.js:9:1\ntest at test/b.integration.test.js:138:1\n'
                                            f'File "{Path(tmp).resolve()}/equipo/test_x.py", line 4\ntest at test/no-existe.test.js:1:1\ntest at test/c.test.mjs:2:1', tmp, ['test/b.integration.test.js'])
        self.assertEqual(out, ['test/a.test.js', 'equipo/test_x.py', 'test/c.test.mjs'])

    def test_failing_test_files_never_expands_to_external_paths_or_source_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'repo'; root.mkdir()
            (root / 'test').mkdir(); (root / 'src').mkdir()
            (root / 'src' / 'app.js').write_text('// source')
            (root / 'test' / 'alias.test.js').symlink_to(root / 'src' / 'app.js')
            (Path(tmp) / 'outside.test.js').write_text('// external')
            (root / 'test' / 'outside.test.js').symlink_to(Path(tmp) / 'outside.test.js')
            (root / 'test' / 'real.test.js').write_text('// test')
            failure = '\n'.join(f'test at {f}:1:1' for f in ['../outside.test.js', 'test/outside.test.js', 'test/alias.test.js', 'test/real.test.js'])
            self.assertEqual(equipo.failing_test_files(failure, root, []), ['test/real.test.js'])

    def test_failing_test_expansion_keeps_the_three_file_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = [f'{i}.test.js' for i in range(4)]
            for name in names: (Path(tmp) / name).write_text('// fixture')
            failure = '\n'.join(f'test at {name}:1:1' for name in names)
            self.assertEqual(equipo.failing_test_files(failure, tmp, []), names[:3])

    def test_failing_test_expansion_obeys_safe_rel_after_resolving_links(self):
        forbidden = ['.codex/guard.test.js', 'node_modules/vendor/value.test.js', '.env.behavior.test.js',
                     '.claude/guard.test.js', '.git/hook.test.js', 'fixtures/.env.behavior.test.js']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in forbidden + ['test/real.test.js']:
                (root / name).parent.mkdir(parents=True, exist_ok=True); (root / name).write_text('// fixture')
            (root / 'test/guard-alias.test.js').symlink_to(root / '.codex/guard.test.js')
            for name in forbidden: self.assertFalse(equipo.safe_rel(name), name)
            failure = '\n'.join(f'test at {name}:1:1' for name in forbidden + ['test/guard-alias.test.js', 'test/real.test.js'])
            self.assertEqual(equipo.failing_test_files(failure, root, []), ['test/real.test.js'])

    def test_failing_tests_only_expand_their_paths_and_other_source_still_blocks(self):
        test_file = 'test/behavior.test.js'
        source_file = 'src/unapproved.js'
        spec = {'titulo': 'Comportamiento', 'archivos': ['src/approved.js'], 'pruebas': ['unitarias']}
        failed = [{'prueba': 'unitarias', 'ok': False, 'salida': f'test at {test_file}:1:1', 'fallo': 'Expected new behavior'}]
        for extra in ([test_file], [test_file, source_file], [source_file]):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp); (path / 'test').mkdir(); (path / test_file).write_text('// test')
                receipt = {'ciclos': []}; scopes = []; test_calls = []
                def changed(where, allowed, links):
                    scopes.append(list(allowed))
                    return list(allowed), [f for f in extra if f not in allowed]
                def tests(*args, **kwargs):
                    test_calls.append(1)
                    return failed if len(test_calls) == 1 else [{'prueba': 'unitarias', 'ok': True, 'salida': 'ok'}]
                def claude(*args, **kwargs):
                    return '{"decision":"aprobar"}' if kwargs.get('role') == 'revisor' else 'hecho'
                with mock.patch.object(equipo, 'changed', changed), mock.patch.object(equipo, 'claude', claude), \
                     mock.patch.object(equipo, 'run_tests', tests), mock.patch.object(equipo, 'discard') as discard, \
                     mock.patch.object(equipo, 'phase'), mock.patch.object(equipo, 'lessons_text', return_value=''), \
                     mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'log'), \
                     mock.patch.object(equipo, 'git', side_effect=lambda path, *a, **kw: 'diff' if '--cached' in a else ''), \
                     mock.patch.object(equipo, 'integrate', return_value='fixture-commit') as integrate, \
                     mock.patch.object(equipo, 'base_roja', lambda *a, **kw: ''):
                    if source_file in extra:
                        with self.assertRaisesRegex(equipo.Stop, 'falta permitir src/unapproved.js'):
                            equipo.attempts('norte', {'name':'Fixture'}, spec, {'id':'fixture'}, 'equipo/norte', 'b', path, path, receipt, None)
                        integrate.assert_not_called(); self.assertEqual(len(test_calls), 1)
                    else:
                        self.assertIn('Integrada', equipo.attempts('norte', {'name':'Fixture'}, spec, {'id':'fixture'}, 'equipo/norte', 'b', path, path, receipt, None))
                        self.assertEqual(len(test_calls), 2); integrate.assert_called_once()
                        self.assertEqual(scopes[1], ['src/approved.js', test_file])
                    discard.assert_called_once_with(path, extra)
                self.assertEqual(receipt['ciclos'][0]['pruebas_ampliadas'], [test_file])
                self.assertEqual(spec['archivos'], ['src/approved.js'])

    def base_roja_with(self, base_tests, linea='FAIL a', nuevas=('test/x.test.js',), worktree_error=None):
        """base_roja with doubles for project, worktree, git and run_tests; never real git. base_tests may be an exception."""
        calls, made, suites = [], [], []
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / 'base'; (base / 'test').mkdir(parents=True); (base / 'test' / 'x.test.js').write_text('//')
            def worktree(pid, name, **kw):
                if worktree_error: raise worktree_error
                made.append((name, kw.get('start'))); return base
            def git(repo, *args, **kw):
                calls.append(args); return 'abc123' if args[0] == 'rev-parse' else ''
            def run_tests(pid, spec, path, folder, heartbeat):
                suites.append((spec['pruebas'], path))
                if isinstance(base_tests, Exception): raise base_tests
                return base_tests
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'project', lambda pid: {'repo': '/repo', 'tests': {}}), \
                 mock.patch.object(equipo, 'worktree', worktree), mock.patch.object(equipo, 'git', git), mock.patch.object(equipo, 'run_tests', run_tests):
                out = equipo.base_roja('norte', {'pruebas': ['unitarias', 'otra']}, 'equipo/norte', 'unitarias', linea, list(nuevas), Path(tmp) / 'runs' / 't_1', None)
            removed = ('worktree', 'remove', '--force', str(base)) in calls
        return out, made, suites, removed

    def test_base_roja_names_the_same_fail_line_on_the_team_branch(self):
        out, made, suites, removed = self.base_roja_with([{'prueba': 'unitarias', 'ok': False, 'salida': '...', 'fallo': 'FAIL a\n  esperado 2'}])
        self.assertIn('FAIL a', out)
        self.assertEqual(made, [('t_1-base', 'abc123')])
        self.assertEqual([s for s, _ in suites], [['unitarias']])        # only the suite that failed
        self.assertTrue(removed)

    def test_base_roja_is_empty_when_the_base_is_green_or_fails_otherwise(self):
        for tests in ([{'prueba': 'unitarias', 'ok': True, 'salida': 'ok'}],
                      [{'prueba': 'unitarias', 'ok': False, 'salida': 'test at test/otra.test.js:1:1', 'fallo': 'FAIL b'}]):
            with self.subTest(tests=tests):
                out, made, suites, removed = self.base_roja_with(tests)
                self.assertEqual(out, '')
                self.assertTrue(removed)

    def test_base_roja_names_a_failing_test_file_shared_with_the_task(self):
        out, made, suites, removed = self.base_roja_with([{'prueba': 'unitarias', 'ok': False, 'salida': 'test at test/x.test.js:3:1', 'fallo': 'FAIL b'}])
        self.assertIn('test/x.test.js', out)
        self.assertTrue(removed)

    def test_base_roja_is_empty_on_any_error(self):
        out, made, suites, removed = self.base_roja_with([], worktree_error=equipo.Stop('git_worktree: falla'))
        self.assertEqual((out, made, suites, removed), ('', [], [], False))
        for error in (equipo.Saturado('carga'), equipo.Asleep('reposo'), equipo.Stop('timeout al probar unitarias (60 s)'), ValueError('x')):
            with self.subTest(error=error):
                out, made, suites, removed = self.base_roja_with(error)
                self.assertEqual(out, '')
                self.assertTrue(removed)

    def test_attempts_stops_on_a_red_base_without_calling_the_builder_again(self):
        spec = {'titulo': 'Contador', 'archivos': ['lib/x.js'], 'pruebas': ['unitarias']}
        roles, asked = [], []
        def claude(prompt, *a, **kw):
            roles.append(kw.get('role')); return 'hecho'
        def base_roja(*a, **kw):
            asked.append(a); return 'FAIL x'
        failed = [{'prueba': 'unitarias', 'ok': False, 'salida': 'test at test/x.test.js:3:1', 'fallo': 'FAIL x\n  esperado 2'}]
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'test').mkdir(); (Path(tmp) / 'test' / 'x.test.js').write_text('//')
            with mock.patch.dict(equipo.LIMITS, {'attempts': 2}), mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'claude', claude), mock.patch.object(equipo, 'changed', lambda *a, **kw: (['lib/x.js'], [])), \
                 mock.patch.object(equipo, 'run_tests', lambda *a, **kw: failed), mock.patch.object(equipo, 'base_roja', base_roja), \
                 mock.patch.object(equipo, 'phase', lambda *a, **kw: None), mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
                 mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
                 mock.patch.object(equipo, 'log', lambda *a, **kw: None):
                with self.assertRaises(equipo.Stop) as ctx:
                    equipo.attempts('norte', {'name': 'Norte'}, spec, {'id': 't_1'}, 'equipo/norte', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertTrue(str(ctx.exception).startswith('base roja: '))
        self.assertIn('unitarias ya falla en equipo/norte sin este cambio (FAIL x)', str(ctx.exception))
        self.assertEqual(roles, ['constructor'])
        self.assertEqual(receipt['causa'], 'base roja')
        self.assertEqual(len(receipt['ciclos']), 1)
        self.assertNotIn('pruebas_ampliadas', receipt['ciclos'][0])
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0][3:6], ('unitarias', 'FAIL x', ['test/x.test.js']))
        self.assertGreaterEqual(receipt['ciclos'][0]['base_segundos'], 0)

    def test_attempts_notes_the_base_check_seconds_only_in_the_first_cycle(self):
        spec = {'titulo': 'Contador', 'archivos': ['lib/x.js'], 'pruebas': ['unitarias']}
        failed = [{'prueba': 'unitarias', 'ok': False, 'salida': 'test at test/x.test.js:3:1', 'fallo': 'FAIL x\n  esperado 2'}]
        receipt = {'ciclos': []}
        asked = []
        def base_roja(*a, **kw):
            asked.append(a); return ''
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'test').mkdir(); (Path(tmp) / 'test' / 'x.test.js').write_text('//')
            with mock.patch.dict(equipo.LIMITS, {'attempts': 2}), mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'claude', return_value='hecho'), mock.patch.object(equipo, 'changed', lambda *a, **kw: (['lib/x.js'], [])), \
                 mock.patch.object(equipo, 'run_tests', lambda *a, **kw: failed), mock.patch.object(equipo, 'base_roja', base_roja), \
                 mock.patch.object(equipo, 'phase', lambda *a, **kw: None), mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
                 mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
                 mock.patch.object(equipo, 'log', lambda *a, **kw: None):
                with self.assertRaises(equipo.Stop):
                    equipo.attempts('norte', {'name': 'Norte'}, spec, {'id': 't_1'}, 'equipo/norte', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertEqual(len(asked), 1)
        self.assertEqual(len(receipt['ciclos']), 2)
        self.assertIn('base_segundos', receipt['ciclos'][0])
        self.assertNotIn('base_segundos', receipt['ciclos'][1])

    def test_attempts_failed_tests_stop_only_with_out_of_scope_changes(self):
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        tests = [{'prueba': 'tipos', 'ok': False, 'salida': 'x', 'fallo': 'x'}]
        for extra in (['alfred-os/app/alfred.tsx'], []):
            with self.subTest(extra=extra):
                receipt = {'ciclos': []}
                with tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.dict(equipo.LIMITS, {'attempts': 2}), \
                     mock.patch.object(equipo, 'claude', return_value='hecho') as claude, \
                     mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], extra)), \
                     mock.patch.object(equipo, 'run_tests', lambda *a, **kw: tests), \
                     mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
                     mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
                     mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
                     mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
                     mock.patch.object(equipo, 'log', lambda *a, **kw: None):
                    with self.assertRaises(equipo.Stop) as ctx:
                        equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
                if extra:
                    self.assertIn('falta permitir alfred-os/app/alfred.tsx', str(ctx.exception))
                    self.assertIn('tipos', str(ctx.exception))
                    self.assertEqual(claude.call_count, 1)
                    self.assertEqual(len(receipt['ciclos']), 1)
                    self.assertEqual(receipt['ciclos'][0]['fuera_de_alcance'], extra)
                else:
                    self.assertGreaterEqual(claude.call_count, 2)
                    self.assertTrue(str(ctx.exception).startswith('agotados los intentos'))
                    self.assertEqual(len(receipt['ciclos']), 2)
                self.assertEqual(receipt['ciclos'][0]['pruebas'], tests)

    def test_attempts_no_changes_after_failed_tests_names_the_failure(self):
        if equipo.LIMITS['attempts'] < 2: self.skipTest('hace falta más de un intento')
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        for said in ('YA ESTABA HECHO: nada', 'No he sabido', ''):
            with self.subTest(said=said):
                receipt = {'ciclos': []}
                with tempfile.TemporaryDirectory() as tmp, \
                     mock.patch.object(equipo, 'claude', side_effect=['hecho', said]), \
                     mock.patch.object(equipo, 'changed', side_effect=[(['alfred-os/app/board.tsx'], []), ([], [])]), \
                     mock.patch.object(equipo, 'run_tests', lambda *a, **kw: [{'prueba': 'tipos', 'ok': False, 'salida': 'fallo'}]), \
                     mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
                     mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
                     mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
                     mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
                     mock.patch.object(equipo, 'log', lambda *a, **kw: None):
                    with self.assertRaises(equipo.Stop) as ctx:
                        equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
                self.assertIn('tras fallar tipos en el intento 1', str(ctx.exception))
                self.assertFalse(str(ctx.exception).lower().startswith('ya estaba hecho'))
                if said: self.assertIn('«' + said + '»', str(ctx.exception))
                self.assertEqual(len(receipt['ciclos']), 2)

NET_FAKE_CLAUDE = """#!/bin/sh
cat >/dev/null
n=$(cat COUNTER 2>/dev/null || echo 0)
echo $((n + 1)) > COUNTER
if [ "$n" -lt FAILS ]; then
  echo '{"type":"result","is_error":true,"result":"MESSAGE"}'
  exit 1
fi
echo '{"type":"result","result":"Claude hecho","total_cost_usd":0.01,"usage":{"output_tokens":3}}'
"""
NET_DOWN = "API Error: Can't reach the API server (ENOTFOUND)"

@unittest.skipIf(os.name == 'nt', 'usa programas falsos de shell, que Windows no ejecuta')
class NetworkRetry(unittest.TestCase):
    """A network error from Claude Code is retried with a wait; any other error stops at once."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.counter = root / 'COUNTER'; self.fake_claude = root / 'claude'
        self.patches = [mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'NET_WAITS', (0, 0)),
                        mock.patch.object(equipo, 'claude_bin', lambda: str(self.fake_claude))]
        for patch in self.patches: patch.start()

    def tearDown(self):
        for patch in self.patches: patch.stop()
        self.tmp.cleanup()

    def fake(self, fails, message):
        script = NET_FAKE_CLAUDE.replace('COUNTER', str(self.counter)).replace('FAILS', str(fails)).replace('MESSAGE', message.replace("'", "'\\''"))
        self.fake_claude.write_text(script); self.fake_claude.chmod(0o755)

    def calls(self): return int(self.counter.read_text())

    def test_one_network_failure_is_retried(self):
        self.fake(1, NET_DOWN)
        self.assertEqual(equipo.claude('Haz la tarea', self.tmp.name, 30), 'Claude hecho')
        self.assertEqual(self.calls(), 2)
        self.assertEqual(len((equipo.STATE / 'consumo' / f'{equipo.today()}.jsonl').read_text().splitlines()), 2)   # each attempt is logged

    def test_a_network_that_never_comes_back_blocks_with_its_reason(self):
        self.fake(99, NET_DOWN)
        with self.assertRaises(equipo.Stop) as ctx: equipo.claude('Haz la tarea', self.tmp.name, 30)
        self.assertTrue(str(ctx.exception).startswith('API inaccesible'))
        self.assertEqual(self.calls(), 3)

    def test_other_errors_are_not_retried(self):
        self.fake(99, 'Invalid API key')
        with self.assertRaises(equipo.Stop) as ctx: equipo.claude('Haz la tarea', self.tmp.name, 30)
        self.assertTrue(str(ctx.exception).startswith('claude_error: '))
        self.assertEqual(self.calls(), 1)

    def test_a_lost_claim_while_waiting_stops(self):
        self.fake(99, NET_DOWN)
        idle = lambda: bool(equipo.CHILDREN)    # alive while Claude runs, lost during the wait
        with mock.patch.object(equipo, 'NET_WAITS', (1,)):
            with self.assertRaises(equipo.Stop) as ctx: equipo.claude('Haz la tarea', self.tmp.name, 30, heartbeat=idle)
        self.assertEqual(str(ctx.exception), 'claim_lost')
        self.assertEqual(self.calls(), 1)

class TimeoutRetry(unittest.TestCase):
    def test_one_timeout_is_retried_with_more_time(self):
        seen = []
        def call(seconds, note=''):
            seen.append(seconds)
            if len(seen) == 1: raise equipo.Stop('timeout')
            return 'hecho'
        self.assertEqual(equipo.retry_timeout(call, 900), 'hecho')
        self.assertEqual(seen, [900, 1350])

    def test_retry_carries_a_note_about_unfinished_work(self):
        notes = []
        def call(seconds, note=''):
            notes.append(note)
            if len(notes) == 1: raise equipo.Stop('timeout')
            return 'hecho'
        equipo.retry_timeout(call, 900, 'prompted-os', 't_1')
        self.assertEqual(notes[0], '')
        self.assertTrue(notes[1])

    def test_two_timeouts_say_the_builder_timed_out(self):
        def call(seconds, note=''): raise equipo.Stop('timeout')
        with self.assertRaises(equipo.Stop) as ctx: equipo.retry_timeout(call, 900)
        self.assertIn('timeout al construir', str(ctx.exception))
        self.assertIn('1350', str(ctx.exception))

    def test_without_retry_a_timeout_is_one_call_and_names_the_phase(self):
        calls = []
        def call(seconds, note=''):
            calls.append(seconds); raise equipo.Stop('timeout')
        with self.assertRaises(equipo.Stop) as ctx: equipo.retry_timeout(call, 900, retry=False)
        self.assertEqual(calls, [900])
        self.assertIn('timeout al construir', str(ctx.exception))
        self.assertIn('900', str(ctx.exception))

    def test_other_stops_are_not_retried(self):
        calls = []
        def call(seconds, note=''):
            calls.append(seconds); raise equipo.Stop('claim_lost')
        with self.assertRaises(equipo.Stop) as ctx: equipo.retry_timeout(call, 900)
        self.assertEqual(str(ctx.exception), 'claim_lost')
        self.assertEqual(calls, [900])

    def test_attempts_does_not_retry_a_timeout_in_attempt_two(self):
        if equipo.LIMITS['attempts'] < 2: self.skipTest('hace falta más de un intento')
        calls = []
        def claude(*a, **kw):
            calls.append(kw.get('attempt'))
            if len(calls) == 1: return 'hecho'
            raise equipo.Stop('timeout')
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', lambda *a, **kw: [{'prueba': 'tipos', 'ok': False, 'salida': 'fallo'}]), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        seconds = equipo.LIMITS['builder_seconds']
        self.assertIn('timeout al construir', str(ctx.exception))
        self.assertIn(str(seconds), str(ctx.exception))
        self.assertNotIn(str(int(seconds * 1.5)), str(ctx.exception))
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(receipt['ciclos']), 1)
        self.assertEqual(receipt['ciclos'][0]['intento'], 1)
        self.assertFalse(all(t['ok'] for t in receipt['ciclos'][0]['pruebas']))
        self.assertIn(f'timeout al construir ({seconds} s) tras fallar tipos en el intento 1', str(ctx.exception))

    def test_attempts_names_a_nameless_failed_test_when_the_builder_times_out_in_attempt_two(self):
        if equipo.LIMITS['attempts'] < 2: self.skipTest('hace falta más de un intento')
        calls = []
        def claude(*a, **kw):
            calls.append(kw.get('attempt'))
            if len(calls) == 1: return 'hecho'
            raise equipo.Stop('timeout')
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', lambda *a, **kw: [{'ok': False, 'salida': 'fallo'}]), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertTrue(str(ctx.exception).startswith('timeout al construir'))
        self.assertIn('prueba sin nombre', str(ctx.exception))

    def test_attempts_timeouts_in_attempt_one_do_not_name_a_failed_test(self):
        def claude(*a, **kw): raise equipo.Stop('timeout')
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertIn('timeout al construir', str(ctx.exception))
        self.assertNotIn('tras fallar', str(ctx.exception))

    def test_run_tests_timeout_names_the_test(self):
        def slow(*a, **kw): raise equipo.Stop('timeout')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'cmd': ['tsc']}}}), \
             mock.patch.object(equipo, 'run', slow):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path(tmp), Path(tmp), None)
        self.assertIn('timeout al probar tipos', str(ctx.exception))
        self.assertIn(str(equipo.LIMITS['test_seconds']), str(ctx.exception))

    def test_real_projects_config_gives_equipo_480_seconds(self):
        real = Path(equipo.__file__).resolve().with_name('projects.json')
        if not real.exists():
            self.skipTest('sin projects.json propio')
        projects = json.loads(real.read_text())
        test = projects['projects']['prompted-os']['tests']['equipo']
        self.assertEqual(test['segundos'], 480)
        self.assertIsInstance(test['cmd'], list)
        self.assertIn('unittest', test['cmd'])
        self.assertIn('discover', test['cmd'])

    def test_run_tests_lets_other_stops_through(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'cmd': ['tsc']}}}), \
             mock.patch.object(equipo, 'run', side_effect=equipo.Stop('claim_lost')) as run:
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path('/tmp'), Path('/tmp'), None)
        self.assertEqual(str(ctx.exception), 'claim_lost')
        self.assertEqual(run.call_count, 1)

    def test_run_tests_retries_one_timeout(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'cmd': ['tsc']}}}), \
             mock.patch.object(equipo, 'run', side_effect=[equipo.Stop('timeout'), (0, 'ok')]) as run, \
             mock.patch.object(equipo, 'log') as log:
            results = equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path('/tmp'), Path('/tmp'), None)
        self.assertTrue(results[0]['ok'])
        self.assertTrue(results[0]['repetida'])
        self.assertEqual(results[0]['repetida_por'], 'timeout')
        self.assertEqual(run.call_count, 2)
        self.assertEqual([c.kwargs['out'].name for c in run.call_args_list], ['test-tipos-0.log', 'test-tipos-0-t.log'])
        log.assert_called_once_with('prompted-os', 'timeout en', 'tipos', ', repito una vez')

    def test_run_tests_two_timeouts_stop_after_one_retry(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'cmd': ['tsc'], 'segundos': 42}}}), \
             mock.patch.object(equipo, 'run', side_effect=equipo.Stop('timeout')) as run:
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path(tmp), Path(tmp), None)
        self.assertEqual(str(ctx.exception), 'timeout al probar tipos (42 s)')
        self.assertEqual(run.call_count, 2)

    def test_failed_start_then_timeout_has_one_retry_and_keeps_its_cause_in_the_receipt(self):
        spec = {'titulo': 'Fixture', 'archivos': ['src/fixture.js'], 'pruebas': ['fixture']}
        receipt = {'ciclos': []}
        calls = []
        def run(*args, **kwargs):
            calls.append(kwargs['out'])
            if len(calls) == 1: return 1, 'did not become healthy'
            kwargs['out'].parent.mkdir(parents=True, exist_ok=True)
            kwargs['out'].write_text('fixture_waiting ...\n')
            raise equipo.Stop('timeout')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'run', run), mock.patch.object(equipo, 'project', return_value={'tests': {'fixture': {'cmd': ['fixture'], 'segundos': 42}}}), \
             mock.patch.object(equipo, 'claude', return_value='hecho'), \
             mock.patch.object(equipo, 'changed', return_value=(['src/fixture.js'], [])), \
             mock.patch.object(equipo, 'phase'), mock.patch.object(equipo, 'lessons_text', return_value=''), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'integrate') as integrate:
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('fixture', {'name': 'Fixture'}, spec, {'id': 'fixture'}, 'equipo/fixture', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertEqual([p.name for p in calls], ['test-fixture-0.log', 'test-fixture-0-r.log'])
        self.assertEqual(str(ctx.exception), 'timeout al probar fixture (42 s); última línea: «fixture_waiting ...»')
        self.assertEqual(receipt['ciclos'][0]['pruebas'], [{'prueba':'fixture', 'ok':False, 'timeout':True, 'repetida_por':'arranque', 'ultima_linea':'fixture_waiting ...'}])
        integrate.assert_not_called()

    def test_run_tests_equipo_uses_480_seconds_after_two_timeouts(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'equipo': {'cmd': ['x'], 'segundos': 480}}}), \
             mock.patch.object(equipo, 'run', side_effect=equipo.Stop('timeout')) as run:
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.run_tests('prompted-os', {'pruebas': ['equipo']}, Path(tmp), Path(tmp), None)
        self.assertEqual(str(ctx.exception), 'timeout al probar equipo (480 s)')
        self.assertEqual([call.args[2] for call in run.call_args_list], [480, 480])

    def test_run_tests_timeout_includes_the_last_log_line(self):
        def slow(*a, **kw):
            kw['out'].write_text('test_a (m.C.test_a) ... ok\ntest_colgada (m.C.test_colgada) ... ')
            raise equipo.Stop('timeout')
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'cmd': ['tsc'], 'segundos': 42}}}), \
             mock.patch.object(equipo, 'run', side_effect=slow) as run:
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path(tmp), Path(tmp), None)
        self.assertEqual(str(ctx.exception), 'timeout al probar tipos (42 s); última línea: «test_colgada (m.C.test_colgada) ...»')
        self.assertEqual(run.call_count, 2)

    def test_run_tests_timeout_restarts_all_steps(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'tests': {'tipos': {'steps': [['prepare'], ['tsc']]}}}), \
             mock.patch.object(equipo, 'run', side_effect=[(0, 'ready'), equipo.Stop('timeout'), (0, 'ready'), (0, 'ok')]) as run:
            results = equipo.run_tests('prompted-os', {'pruebas': ['tipos']}, Path('/tmp'), Path('/tmp'), None)
        self.assertTrue(results[0]['ok'])
        self.assertTrue(results[0]['repetida'])
        self.assertEqual([c.args[0] for c in run.call_args_list], [['prepare'], ['tsc'], ['prepare'], ['tsc']])
        self.assertEqual([c.kwargs['out'].name for c in run.call_args_list],
                         ['test-tipos-0.log', 'test-tipos-1.log', 'test-tipos-0-t.log', 'test-tipos-1-t.log'])

    def test_run_timeout_preserves_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / 'logs' / 'timeout.log'
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.run([sys.executable, '-c', "print('hola', flush=True); import time; time.sleep(30)"], tmp, timeout=2, out=out)
            self.assertEqual(str(ctx.exception), 'timeout')
            self.assertIn('hola', out.read_text())

    def test_attempts_test_timeout_leaves_its_cycle_in_the_receipt(self):
        def slow(*a, **kw): raise equipo.Stop(f"timeout al probar tipos ({equipo.LIMITS['test_seconds']} s)")
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', lambda *a, **kw: 'hecho'), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', slow), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertIn('timeout al probar', str(ctx.exception))
        self.assertEqual(len(receipt['ciclos']), 1)
        self.assertEqual(receipt['ciclos'][-1]['intento'], 1)
        self.assertEqual(receipt['ciclos'][-1]['pruebas'], [{'prueba': 'tipos', 'ok': False, 'timeout': True, 'repetida_por': 'timeout'}])

    def test_attempts_equipo_timeout_records_the_repeat_reason(self):
        message = 'timeout al probar equipo (480 s)'
        spec = {'titulo': 'x', 'archivos': ['equipo/equipo.py'], 'pruebas': ['equipo']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', return_value='hecho'), \
             mock.patch.object(equipo, 'changed', return_value=(['equipo/equipo.py'], [])), \
             mock.patch.object(equipo, 'run_tests', side_effect=equipo.Stop(message)), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertEqual(str(ctx.exception), message)
        self.assertEqual(receipt['ciclos'][-1]['pruebas'][0]['repetida_por'], 'timeout')

    def test_attempts_test_timeout_preserves_the_last_line_in_the_receipt(self):
        message = 'timeout al probar equipo (240 s); última línea: «test_x (m.C.test_x) ...»'
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['equipo']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', lambda *a, **kw: 'hecho'), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', side_effect=equipo.Stop(message)), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertEqual(str(ctx.exception), message)
        self.assertIn('test_x', str(ctx.exception))
        self.assertEqual(len(receipt['ciclos']), 1)
        self.assertEqual(receipt['ciclos'][-1]['pruebas'],
                         [{'prueba': 'equipo', 'ok': False, 'timeout': True, 'repetida_por': 'timeout', 'ultima_linea': 'test_x (m.C.test_x) ...'}])

    def test_motivo_bloqueo_preserves_the_timeout_last_line(self):
        message = 'timeout al probar equipo (240 s); última línea: «test_x (m.C.test_x) ...»'
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(equipo.motivo_bloqueo(equipo.Stop(message), Path(tmp)), message)

    def test_relaunch_after_a_test_timeout_uses_the_block_reason(self):
        prev = {'estado': 'bloqueada', 'error': 'timeout al probar tipos (600 s)',
                'ciclos': [{'pruebas': [{'prueba': 'tipos', 'ok': False, 'timeout': True}]}]}
        self.assertIn('timeout al probar tipos', equipo.relaunch_note(prev))

    def test_attempts_review_timeout_names_the_phase(self):
        calls = []
        def claude(*a, **kw):
            calls.append(kw.get('role'))
            if kw.get('role') == 'constructor': return 'hecho'
            raise equipo.Stop('timeout')
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos']}
        receipt = {'ciclos': []}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'git', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'run_tests', lambda *a, **kw: [{'prueba': 'tipos', 'ok': True, 'salida': ''}]), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), receipt, None)
        self.assertIn('timeout al revisar', str(ctx.exception))
        self.assertIn(str(equipo.LIMITS['review_seconds']), str(ctx.exception))
        self.assertEqual(calls.count('constructor'), 1)
        self.assertEqual(calls.count('revisor'), 1)
        self.assertEqual(receipt['ciclos'], [])

class PlanDuplicates(unittest.TestCase):
    BOARD = [{'id': 't_a', 'title': 'Entregas: lista A', 'status': 'done'}, {'id': 't_b', 'title': 'Motor: B', 'status': 'ready'},
             {'id': 't_c', 'title': 'Motor: C', 'status': 'blocked'}, {'id': 't_d', 'title': 'D', 'status': 'archived'}]

    def items(self, *specs):
        return [{'titulo': t, 'depende_de': deps} for t, deps in specs]

    def test_board_and_batch_duplicates_are_dropped(self):
        items = self.items(('entregas: lista a ', []), ('Motor: C', []), ('D', []), ('E', []), ('E.', []), ('F', [0]))
        kept, dropped = equipo.drop_duplicates(items, self.BOARD)
        self.assertEqual([t['titulo'] for t in kept], ['Motor: C', 'D', 'E'])
        self.assertEqual(dropped, [{'titulo': 'entregas: lista a ', 'duplicada_de': 't_a'},
                                   {'titulo': 'E.', 'duplicada_de': 'el mismo lote'},
                                   {'titulo': 'F', 'duplicada_de': 'depende de una duplicada'}])

    def test_depende_de_is_renumbered_for_the_kept_ones(self):
        items = self.items(('Motor: B…', []), ('Motor: C', []), ('D', [1]), ('E', []), ('G', [1, 3]))
        kept, dropped = equipo.drop_duplicates(items, self.BOARD)
        self.assertEqual([(t['titulo'], t['depende_de']) for t in kept], [('Motor: C', []), ('D', [0]), ('E', []), ('G', [0, 2])])
        self.assertEqual(dropped, [{'titulo': 'Motor: B…', 'duplicada_de': 't_b'}])

    def test_nothing_to_drop_keeps_everything(self):
        items = self.items(('X', []), ('Y', [0]))
        kept, dropped = equipo.drop_duplicates(items, [])
        self.assertEqual(kept, items)
        self.assertEqual(dropped, [])

class TareaBloqueadaDesdeLaApp(unittest.TestCase):
    # Same cases as bloqueadaArgs in mac/local-workspace/runtime-checks.mjs.
    def test_id_tablero_accepts_only_one_lowercase_hex_id(self):
        for ok in ('t_1234abcd', 't_036cee09'):
            self.assertIsNotNone(equipo.ID_TABLERO.fullmatch(ok), ok)
        for bad in ('', 't_1234567', 't_123456789', 'T_1234ABCD', 't_1234ABCD', '../x', '--ids', 't_1234abcd,t_5678abcd', ' t_1234abcd ', 't_1234abcd\n'):
            self.assertIsNone(equipo.ID_TABLERO.fullmatch(bad), repr(bad))

    def test_parser_takes_what_the_app_sends(self):
        a = equipo.parser().parse_args(['desbloquear', 'prompted-os', '--ids', 't_1234abcd', '--limpio'])
        self.assertEqual((a.cmd, a.project, a.ids, a.limpio), ('desbloquear', 'prompted-os', 't_1234abcd', True))
        a = equipo.parser().parse_args(['archivar', 'prompted-os', '--ids', 't_1234abcd'])
        self.assertEqual((a.cmd, a.project, a.ids, a.limpio), ('archivar', 'prompted-os', 't_1234abcd', False))

class RutasConfiguradas(unittest.TestCase):
    def test_nested_content_source_and_flat_vault_remain_configurable(self):
        with mock.patch.object(equipo, 'APP_CONFIG', {'contenido': {'maquina': '/tmp/ejemplo.db'}, 'vault': '/tmp/vault'}), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(equipo._ruta('contenido.maquina', 'PROMPTED_OS_RF_DB'), Path('/tmp/ejemplo.db'))
            self.assertEqual(equipo._ruta('vault'), Path('/tmp/vault'))
        with mock.patch.object(equipo, 'APP_CONFIG', {}), mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(equipo._ruta('contenido.maquina', 'PROMPTED_OS_RF_DB'))

    def test_environment_overrides_configured_content_source(self):
        with mock.patch.object(equipo, 'APP_CONFIG', {'contenido': {'maquina': '/tmp/config.db'}}), mock.patch.dict(os.environ, {'PROMPTED_OS_RF_DB': '/tmp/override.db'}):
            self.assertEqual(equipo._ruta('contenido.maquina', 'PROMPTED_OS_RF_DB'), Path('/tmp/override.db'))


class ContenidoMedido(unittest.TestCase):
    def setUp(self):
        import sqlite3
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.db = self.root / 'machine.db'
        for name, value in (('WORKSPACE', self.root), ('RF_DB', self.db)):
            patch = mock.patch.object(equipo, name, value); patch.start(); self.addCleanup(patch.stop)
        conn = sqlite3.connect(self.db)
        try:
            conn.executescript('CREATE TABLE publications(id INTEGER, piece_id INTEGER, channel TEXT); '
                               'CREATE TABLE pieces(id INTEGER, brand TEXT, title TEXT, hook TEXT);')
            conn.executemany('INSERT INTO pieces VALUES (?, ?, ?, ?)', [
                (1, 'beta', 'Título TF de la base', 'Hook TF'),
                (2, 'norte-capital', None, 'Hook Norte de la base')])
            conn.executemany('INSERT INTO publications VALUES (?, ?, ?)', [(1, 1, 'instagram'), (2, 2, 'youtube')])
            conn.commit()
        finally: conn.close()
        self.file = self.root / 'contenido' / 'medidas.json'
        self.medidas = {
            'rf:1': self.medida(100, 10, 5, 1),
            'rf:2': self.medida(200, 20, 10, 2),
            'plan:abc': self.medida(50, 5, 5, 1),
            'fv:2026-10-19-full-visual-tf-40-cuentas': self.medida(40, 4, 4, 0)}
        equipo.save(self.file, self.medidas)

    def medida(self, vistas, visitas, clics, compras):
        return {'vistas': vistas, 'visitas': visitas, 'clics': clics, 'compras': compras, 'hora': '2026-10-23T10:00:00'}

    def medir(self, **por_id):
        equipo.save(self.file, {ident: self.medida(*cifras) for ident, cifras in por_id.items()})

    def test_total_shows_purchases_per_click(self):
        self.medir(**{'rf:1': (100, 10, 5, 1)})
        self.assertIn('Compras por clic en total: 20%.', equipo.contenido_medido('beta'))

    def test_total_formats_fractional_purchases_per_click(self):
        self.medir(**{'rf:1': (100, 10, 3, 1)})
        self.assertIn('Compras por clic en total: 33,3%.', equipo.contenido_medido('beta'))

    def test_total_omits_purchases_per_click_without_clicks(self):
        self.medir(**{'rf:1': (100, 10, 0, 1)})
        self.assertNotIn('Compras por clic', equipo.contenido_medido('beta'))

    def test_network_breakdown_shows_purchases_per_click_only_with_clicks(self):
        self.medir(**{'rf:1': (100, 10, 4, 2), 'fv:2026-10-19-full-visual-tf-cero': (40, 4, 0, 0)})
        por_red = equipo.contenido_medido('beta').split('Por red: ', 1)[1]
        self.assertIn('instagram: 1 publicaciones, 100 vistas, 4 clics, 2 compras, 4% de clics por vista, 50% de compras por clic', por_red)
        tramo_sin_clics = por_red.split('sin red: ', 1)[1]
        self.assertNotIn('compras por clic', tramo_sin_clics)

    def test_total_shows_zero_purchases_per_click(self):
        self.medir(**{'rf:1': (100, 10, 5, 0)})
        self.assertIn('Compras por clic en total: 0%.', equipo.contenido_medido('beta'))

    def test_tf_only_receives_its_publications_and_opens_database_once(self):
        import sqlite3
        with mock.patch.object(sqlite3, 'connect', wraps=sqlite3.connect) as connect:
            text = equipo.contenido_medido('beta')
        connect.assert_called_once_with(f'file:{self.db}?mode=ro', uri=True)
        self.assertIn('2 publicaciones): 140 vistas, 14 visitas al perfil, 9 clics, 1 compras.', text)
        self.assertIn('«Título TF de la base» (instagram): 100 vistas, 5 clics, 1 compras', text)
        self.assertIn('«tf 40 cuentas» (sin red): 40 vistas, 4 clics, 0 compras', text)
        self.assertIn('Clics por vista en total: 6,4%.', text)
        self.assertIn('«Título TF de la base» (instagram): 100 vistas, 5 clics, 1 compras, 5% de clics por vista', text)
        self.assertIn('«tf 40 cuentas» (sin red): 40 vistas, 4 clics, 0 compras, 10% de clics por vista', text)
        self.assertNotIn('()', text)
        self.assertNotIn('Hook Norte', text)
        self.assertNotIn('publicación del plan de vuelta', text)
        self.assertEqual(equipo.contenido_medido('beta', limit=35), text[:35])

    def test_norte_receives_plan_and_database_hook(self):
        text = equipo.contenido_medido('norte')
        self.assertIn('2 publicaciones): 250 vistas, 25 visitas al perfil, 15 clics, 3 compras.', text)
        self.assertIn('Clics por vista en total: 6%.', text)
        self.assertIn('«publicación del plan de vuelta» (sin red)', text)
        self.assertNotIn('()', text)
        self.assertIn('«Hook Norte de la base» (youtube)', text)
        self.assertNotIn('Título TF', text)
        self.assertNotIn('tf 40 cuentas', text)

    def test_calendar_publication_counts_with_its_title_and_network(self):
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': [
            {'id': 'tf', 'marca': 'beta', 'estado': 'publicado', 'fecha': '2026-10-30',
             'titulo': 'Reel del calendario', 'red': 'tiktok'}]})
        equipo.save(self.file, {**self.medidas, 'cal:tf': self.medida(300, 30, 12, 2)})
        text = equipo.contenido_medido('beta')
        self.assertIn('3 publicaciones): 440 vistas, 44 visitas al perfil, 21 clics, 3 compras.', text)
        self.assertIn('«Reel del calendario» (tiktok)', text)
        self.assertNotIn('Ganadores de los Trial Reels', text)

    def test_calendar_title_uses_first_nonempty_text_line(self):
        texto = self.root / 'publicacion.txt'; texto.write_text('\n  \n Primera frase \nSegunda frase\n')
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': [
            {'id': 'tf', 'marca': 'beta', 'texto': str(texto)}]})
        equipo.save(self.file, {**self.medidas, 'cal:tf': self.medida(300, 30, 12, 2)})
        self.assertIn('«Primera frase»', equipo.contenido_medido('beta'))

    def test_calendar_publications_are_filtered_by_brand(self):
        before = equipo.contenido_medido('beta')
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': [
            {'id': 'kc', 'marca': 'norte-capital', 'titulo': 'Calendario Norte', 'red': 'youtube'}]})
        equipo.save(self.file, {**self.medidas, 'cal:kc': self.medida(300, 30, 12, 2)})
        self.assertEqual(equipo.contenido_medido('beta'), before)
        self.assertIn('«Calendario Norte» (youtube)', equipo.contenido_medido('norte'))

    def test_missing_calendar_id_does_not_count(self):
        before = equipo.contenido_medido('beta')
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': []})
        equipo.save(self.file, {**self.medidas, 'cal:no-existe': self.medida(300, 30, 12, 2)})
        self.assertEqual(equipo.contenido_medido('beta'), before)

    def test_missing_or_malformed_calendar_is_ignored(self):
        before = equipo.contenido_medido('beta')
        equipo.save(self.file, {**self.medidas, 'cal:tf': self.medida(300, 30, 12, 2)})
        self.assertEqual(equipo.contenido_medido('beta'), before)
        calendario = self.root / 'contenido' / 'calendario.json'
        for content in ('{no es json', '[]', 'null', '{"entradas": {}}',
                        '{"entradas": [null, 3, {}, {"id": "tf", "marca": 3}, {"id": 3, "marca": "beta"}]}'):
            with self.subTest(content=content):
                calendario.write_text(content)
                self.assertEqual(equipo.contenido_medido('beta'), before)

    def test_calendar_is_loaded_once_and_only_when_needed(self):
        calendario = self.root / 'contenido' / 'calendario.json'
        equipo.save(calendario, {'entradas': [
            {'id': ident, 'marca': 'beta', 'titulo': ident} for ident in ('uno', 'dos')]})
        with mock.patch.object(equipo, 'load', wraps=equipo.load) as load:
            equipo.contenido_medido('beta')
        self.assertNotIn(mock.call(calendario, {}), load.call_args_list)
        equipo.save(self.file, {**self.medidas, 'cal:uno': self.medida(300, 30, 12, 2),
                               'cal:dos': self.medida(300, 30, 12, 2)})
        with mock.patch.object(equipo, 'load', wraps=equipo.load) as load:
            text = equipo.contenido_medido('beta')
        self.assertEqual(load.call_args_list.count(mock.call(calendario, {})), 1)
        self.assertIn('4 publicaciones): 740 vistas', text)

    def test_calendar_title_falls_back_to_valid_date_or_generic_title(self):
        calendario = self.root / 'contenido' / 'calendario.json'
        equipo.save(self.file, {**self.medidas, 'cal:tf': self.medida(300, 30, 12, 2)})
        for fecha, suffix in (('2026-10-30', ' del 30/10'), ('2026-02-30', ''), ('2026-1-1', ''), (None, '')):
            for texto in (str(self.root / 'ausente.txt'), '\x00'):
                with self.subTest(fecha=fecha, texto=texto):
                    equipo.save(calendario, {'entradas': [
                        {'id': 'tf', 'marca': 'beta', 'titulo': '  ', 'red': 3, 'fecha': fecha, 'texto': texto}]})
                    self.assertIn(f'«publicación del calendario{suffix}» (sin red)', equipo.contenido_medido('beta'))

    def test_calendar_publication_is_excluded_from_trial_winners_line(self):
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': [
            {'id': 'tf', 'marca': 'beta', 'titulo': 'Reel del calendario', 'fecha': '2026-10-30'}]})
        equipo.save(self.file, {**self.medidas, 'cal:tf': self.medida(300, 30, 12, 2),
                               'ritmo:tf-2000:2026-10-30': self.medida(100, 10, 1, 0)})
        with mock.patch.object(equipo, '_prefijos', return_value={'tf': 'beta'}), \
             mock.patch.object(equipo, 'REELS_DIA', None):
            text = equipo.contenido_medido('beta', limit=10000)
        self.assertIn('«Reel del calendario»', text)
        ganadores = text.split('Ganadores de los Trial Reels en la cuenta principal: ', 1)[1]
        self.assertIn('«reel bueno del 30/10»', ganadores)
        self.assertNotIn('Reel del calendario', ganadores)

    def test_breaks_down_multiple_networks_after_the_top(self):
        text = equipo.contenido_medido('beta')
        line = ('Por red: instagram: 1 publicaciones, 100 vistas, 5 clics, 1 compras, 5% de clics por vista, 20% de compras por clic; '
                'sin red: 1 publicaciones, 40 vistas, 4 clics, 0 compras, 10% de clics por vista, 0% de compras por clic.')
        self.assertIn(line, text)
        self.assertGreater(text.index('Por red:'), text.index('Más clics por vista:'))

    def test_shows_best_purchases_per_click_with_minimum_clicks(self):
        self.medir(**{'rf:1': (100, 10, 10, 2)})
        self.assertIn('Más compras por clic (publicaciones con 10 clics o más): «Título TF de la base» (instagram): 10 clics, 2 compras, 20% de compras por clic.',
                      equipo.contenido_medido('beta'))

    def test_best_purchases_per_click_excludes_publications_below_minimum_clicks(self):
        self.medir(**{'rf:1': (100, 10, 10, 1), 'fv:2026-10-19-full-visual-tf-pocos': (40, 4, 3, 1)})
        linea = equipo.contenido_medido('beta').split('Más compras por clic', 1)[1].split('\n', 1)[0]
        self.assertIn('Título TF de la base', linea)
        self.assertNotIn('tf pocos', linea)

    def test_best_purchases_per_click_is_omitted_below_minimum_clicks(self):
        self.medir(**{'rf:1': (100, 10, 9, 1), 'fv:2026-10-19-full-visual-tf-pocos': (40, 4, 3, 1)})
        self.assertNotIn('Más compras por clic', equipo.contenido_medido('beta'))

    def test_best_purchases_per_click_orders_by_rate(self):
        self.medir(**{'rf:1': (100, 10, 10, 1), 'fv:2026-10-19-full-visual-tf-mejor': (40, 4, 10, 2)})
        linea = equipo.contenido_medido('beta').split('Más compras por clic', 1)[1].split('\n', 1)[0]
        self.assertLess(linea.index('tf mejor'), linea.index('Título TF de la base'))

    def test_best_purchases_per_click_is_between_top_and_network_breakdown(self):
        self.medir(**{'rf:1': (100, 10, 10, 2), 'fv:2026-10-19-full-visual-tf-otra': (40, 4, 10, 1)})
        text = equipo.contenido_medido('beta')
        self.assertGreater(text.index('Más compras por clic'), text.index('Más clics por vista:'))
        self.assertLess(text.index('Más compras por clic'), text.index('Por red:'))

    def test_network_breakdown_orders_by_clicks(self):
        text = equipo.contenido_medido('norte')
        self.assertLess(text.index('youtube: 1 publicaciones, 200 vistas, 10 clics'),
                        text.index('sin red: 1 publicaciones, 50 vistas, 5 clics'))

    def test_network_breakdown_is_omitted_for_one_network(self):
        equipo.save(self.file, {'rf:1': self.medida(100, 10, 5, 1)})
        self.assertNotIn('Por red:', equipo.contenido_medido('beta'))

    def test_network_with_zero_views_has_no_rate(self):
        equipo.save(self.file, {
            'rf:1': self.medida(100, 10, 5, 1),
            'fv:2026-10-19-full-visual-tf-cero': self.medida(0, 7, 4, 0)})
        text = equipo.contenido_medido('beta')
        tramo = text.split('sin red: ', 1)[1].split('.', 1)[0]
        self.assertIn('1 publicaciones, 0 vistas, 4 clics, 0 compras', tramo)
        self.assertNotIn('de clics por vista', tramo)

    def test_long_top_titles_are_shortened_before_the_limit(self):
        import sqlite3
        title = 'x' * 300
        conn = sqlite3.connect(self.db)
        try:
            conn.executemany('INSERT INTO pieces VALUES (?, ?, ?, ?)', [(i, 'beta', title, '') for i in range(3, 6)])
            conn.executemany('INSERT INTO publications VALUES (?, ?, ?)', [(i, i, 'instagram') for i in range(3, 6)])
            conn.commit()
        finally: conn.close()
        equipo.save(self.file, {f'rf:{i}': self.medida(100, 0, i, 0) for i in range(3, 6)})
        text = equipo.contenido_medido('beta')
        top = text.split('Más clics por vista: ', 1)[1]
        titles = re.findall('«([^»]+)»', top)
        self.assertEqual(len(titles), 3)
        self.assertTrue(all(len(item) == 80 and item.endswith('…') for item in titles))
        self.assertLessEqual(len(text), 1500)
        self.assertTrue(text.endswith('.'))

    def test_no_brand_missing_or_corrupt_measurements(self):
        for pid in ('prompted-os', 'desconocido'):
            self.assertEqual(equipo.contenido_medido(pid), '')
        self.file.unlink()
        self.assertEqual(equipo.contenido_medido('norte'), '')
        for content in ('{no es json', '[]', 'null'):
            with self.subTest(content=content):
                self.file.write_text(content)
                self.assertEqual(equipo.contenido_medido('norte'), '')

    def test_missing_or_broken_database_omits_rf_without_creating_it(self):
        import sqlite3
        self.db.unlink()
        for broken in (False, True):
            with self.subTest(broken=broken):
                if broken: self.db.write_text('no es sqlite')
                with mock.patch.object(sqlite3, 'connect', wraps=sqlite3.connect) as connect:
                    text = equipo.contenido_medido('norte')
                connect.assert_called_once_with(f'file:{self.db}?mode=ro', uri=True)
                self.assertIn('1 publicaciones): 50 vistas, 5 visitas al perfil, 5 clics, 1 compras.', text)
                self.assertNotIn('Hook Norte', text)
                if not broken: self.assertFalse(self.db.exists())

    def test_unconfigured_database_keeps_file_measurements_without_opening_a_database(self):
        import sqlite3
        with mock.patch.object(equipo, 'RF_DB', None), mock.patch.object(sqlite3, 'connect') as connect:
            text = equipo.contenido_medido('beta')
        connect.assert_not_called()
        self.assertIn('«tf 40 cuentas»', text)
        self.assertNotIn('Título TF', text)

    def test_top_orders_by_rate_then_clicks_and_zero_views_only_add_to_totals(self):
        equipo.save(self.file, {
            'fv:2026-10-19-full-visual-tf-menor': self.medida(1000, 0, 10, 0),
            'fv:2026-10-19-full-visual-tf-empate-pequeno': self.medida(10, 0, 2, 0),
            'fv:2026-10-19-full-visual-tf-cero': self.medida(0, 7, 100, 3),
            'fv:2026-10-19-full-visual-tf-mejor': self.medida(5, 0, 2, 0),
            'fv:2026-10-19-full-visual-tf-empate-grande': self.medida(20, 0, 4, 0)})
        text = equipo.contenido_medido('beta')
        self.assertIn('5 publicaciones): 1035 vistas, 7 visitas al perfil, 118 clics, 3 compras.', text)
        top = text.split('Más clics por vista: ')[1].split('\nMás compras por clic', 1)[0]
        self.assertEqual(re.findall('«([^»]+)»', top), ['tf mejor', 'tf empate grande', 'tf empate pequeno'])

    def test_zero_views_omit_total_clicks_per_view(self):
        equipo.save(self.file, {'fv:2026-10-19-full-visual-tf-cero': self.medida(0, 7, 100, 3)})
        text = equipo.contenido_medido('beta')
        self.assertNotIn('Clics por vista en total', text)
        self.assertIn('1 publicaciones): 0 vistas, 7 visitas al perfil, 100 clics, 3 compras.', text)
        self.assertNotIn('Más clics por vista', text)

    def test_counts_publications_measured_in_last_seven_days(self):
        def con_hora(hora):
            medida = self.medida(10, 1, 1, 0)
            if hora is not None: medida['hora'] = hora
            else: del medida['hora']
            return medida
        equipo.save(self.file, {
            'fv:2026-10-19-full-visual-tf-reciente': con_hora('2026-10-24T08:00:00.000Z'),
            'fv:2026-10-19-full-visual-tf-antigua': con_hora('2026-10-10T08:00:00Z'),
            'fv:2026-10-19-full-visual-tf-rota': con_hora('rota'),
            'fv:2026-10-19-full-visual-tf-sin-zona': con_hora('2026-10-24T08:00:00'),
            'fv:2026-10-19-full-visual-tf-sin-hora': con_hora(None)})
        with mock.patch.object(equipo, 'now', return_value=datetime(2026, 10, 24, 12, tzinfo=equipo.MADRID)):
            text = equipo.contenido_medido('beta')
        self.assertIn('5 publicaciones)', text)
        self.assertIn('Apuntadas en los últimos 7 días: 1.', text)

    def test_invalid_figures_and_unknown_ids_are_ignored(self):
        medidas = {'rf:1': self.medidas['rf:1'], 'rf:999': self.medida(1, 1, 1, 1),
                   'fv:carpeta-sin-marca': self.medida(1, 1, 1, 1), 'otro:abc': self.medida(1, 1, 1, 1)}
        for field in ('vistas', 'visitas', 'clics', 'compras'):
            for index, bad in enumerate((-1, 1.5, '2', True, None)):
                medidas[f'fv:2026-10-19-full-visual-tf-{field}-{index}'] = {**self.medida(1, 1, 1, 1), field: bad}
        medidas['fv:2026-10-19-full-visual-tf-incompleto'] = {'vistas': 1}
        medidas['fv:2026-10-19-full-visual-tf-no-dict'] = []
        equipo.save(self.file, medidas)
        self.assertIn('1 publicaciones): 100 vistas, 10 visitas al perfil, 5 clics, 1 compras.', equipo.contenido_medido('beta'))
        self.assertEqual(equipo.contenido_medido('norte'), '')

    def test_full_visual_brand_aliases(self):
        for folder, pid in (('2026-10-19-full-visual-beta', 'beta'),
                            ('2026-10-19-full-visual-kc-video', 'norte'),
                            ('2026-10-19-full-visual-norte', 'norte'),
                            ('2026-10-19-full-visual-stop-liquidez', 'norte')):
            with self.subTest(folder=folder):
                equipo.save(self.file, {f'fv:{folder}': self.medida(10, 1, 2, 0)})
                self.assertIn('1 publicaciones): 10 vistas', equipo.contenido_medido(pid))

    def test_plan_project_includes_measured_content_once(self):
        p = {**equipo.project('norte'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'STATE', self.root), mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value='LO MEDIDO DE PRUEBA') as medido, \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('norte', batch, file)
        medido.assert_called_once_with('norte')
        self.assertIn('LO MEDIDO DE PRUEBA', claude.call_args.args[0])


class CalendarioPendiente(unittest.TestCase):
    CABECERA = 'Calendario de publicación (lo publica solo la tarea programada; estado real de PostOnce): '

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ahora = datetime(2026, 10, 31, 12, tzinfo=equipo.MADRID)
        for name, value in (('WORKSPACE', self.root), ('now', mock.Mock(return_value=self.ahora))):
            patch = mock.patch.object(equipo, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def entrada(self, ident='uno', horas=48, estado='publicado', **extra):
        at = self.ahora - timedelta(hours=horas)
        return {'id': ident, 'marca': 'beta', 'fecha': at.strftime('%Y-%m-%d'), 'hora': at.strftime('%H:%M'),
                'estado': estado, 'titulo': f'Título {ident}', 'red': 'instagram', **extra}

    def escribir(self, entradas, medidas=None):
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': entradas})
        if medidas is not None: equipo.save(self.root / 'contenido' / 'medidas.json', medidas)

    def test_counts_published_unmeasured_failed_and_upcoming(self):
        self.escribir([
            self.entrada('a', 48), self.entrada('b', 50), self.entrada('c', 10),
            self.entrada('f', 30, 'fallo', nota='PostOnce rechazó el vídeo'),
            self.entrada('p', -24, 'pendiente'), self.entrada('g', -48, 'programado'),
            self.entrada('lejos', -24 * 8, 'pendiente')], {'cal:b': {'vistas': 1}})
        self.assertEqual(equipo.calendario_pendiente('beta'),
                         self.CABECERA + '3 publicadas, 1 sin cifras pasadas 24 h, 1 con fallo, 2 por salir en 7 días.'
                         '\nSin cifras: «Título a» (instagram, 29/10). Pide a Owner que apunte vistas, visitas, clics y compras '
                         'en Contenido o con /medir antes de sacar conclusiones de esa línea.'
                         '\nFallos al publicar: «Título f» (instagram, 30/10): PostOnce rechazó el vídeo')

    def test_figures_line_leaves_out_measured_and_recent_publications(self):
        self.escribir([self.entrada('a', 48), self.entrada('b', 50), self.entrada('c', 10)], {'cal:b': {}})
        text = equipo.calendario_pendiente('beta')
        self.assertIn('3 publicadas, 1 sin cifras pasadas 24 h, 0 con fallo, 0 por salir en 7 días.', text)
        self.assertIn('\nSin cifras: «Título a» (instagram, 29/10)', text)
        for ident in ('b', 'c'): self.assertNotIn(f'Título {ident}', text)

    def test_published_less_than_24_hours_ago_does_not_count_as_unmeasured(self):
        self.escribir([self.entrada('c', 23)])
        text = equipo.calendario_pendiente('beta')
        self.assertEqual(text, self.CABECERA + '1 publicadas, 0 sin cifras pasadas 24 h, 0 con fallo, 0 por salir en 7 días.')

    def test_published_exactly_24_hours_ago_counts_as_unmeasured(self):
        self.escribir([self.entrada('c', 24)])
        self.assertIn('1 sin cifras pasadas 24 h', equipo.calendario_pendiente('beta'))

    def test_cancelled_other_brand_and_invalid_entries_do_not_count(self):
        self.escribir([
            self.entrada('x', 48, 'cancelado'), self.entrada('y', 48, marca='norte-capital'),
            self.entrada('z', 48, estado='raro'), self.entrada('sin-hora', 48, hora='9:00'),
            self.entrada('sin-fecha', 48, fecha='2026-02-30'), {**self.entrada('n', 48), 'id': 7}, 'texto', None,
            self.entrada('ok', 48)])
        text = equipo.calendario_pendiente('beta')
        self.assertIn('1 publicadas, 1 sin cifras pasadas 24 h, 0 con fallo, 0 por salir en 7 días.', text)
        self.assertIn('«Título ok»', text)

    def test_only_cancelled_or_other_brand_entries_give_nothing(self):
        self.escribir([self.entrada('x', 48, 'cancelado'), self.entrada('y', 48, marca='norte-capital')])
        self.assertEqual(equipo.calendario_pendiente('beta'), '')

    def test_five_unmeasured_list_the_three_most_recent(self):
        self.escribir([self.entrada(ident, horas) for ident, horas in zip('abcde', (70, 30, 60, 40, 50))])
        text = equipo.calendario_pendiente('beta')
        self.assertIn('5 publicadas, 5 sin cifras pasadas 24 h,', text)
        self.assertIn('\nSin cifras: «Título b» (instagram, 30/10); «Título d» (instagram, 29/10); «Título e» (instagram, 29/10) y 2 más', text)
        for ident in 'ac': self.assertNotIn(f'Título {ident}', text)

    def test_failures_list_three_and_say_how_many_more(self):
        self.escribir([self.entrada(str(n), 30 + n, 'fallo', nota=f'motivo {n}') for n in range(5)])
        text = equipo.calendario_pendiente('beta')
        self.assertIn('5 con fallo', text)
        self.assertIn('\nFallos al publicar: «Título 0» (instagram, 30/10): motivo 0; «Título 1» (instagram, 30/10): motivo 1; '
                      '«Título 2» (instagram, 30/10): motivo 2 y 2 más', text)

    def test_failure_without_note_says_sin_motivo(self):
        self.escribir([self.entrada('f', 30, 'fallo', red=None), self.entrada('g', 31, 'fallo', nota='  ')])
        text = equipo.calendario_pendiente('beta')
        self.assertIn('«Título f» (sin red, 30/10): sin motivo; «Título g» (instagram, 30/10): sin motivo', text)

    def test_title_comes_from_the_text_file_or_a_generic_one_and_is_cut_to_80(self):
        archivo = self.root / 'reel.txt'; archivo.write_text('\n  \n  Primera línea  \nSegunda\n')
        self.escribir([self.entrada('a', 48, titulo=' ', texto=str(archivo)), self.entrada('b', 49, titulo=None, texto=str(self.root / 'ausente.txt')),
                       self.entrada('c', 50, titulo='x' * 90), self.entrada('d', 51, titulo='', texto='\x00')])
        text = equipo.calendario_pendiente('beta')
        self.assertIn('«Primera línea» (instagram, 29/10)', text)
        self.assertIn('«publicación del calendario» (instagram, 29/10)', text)
        self.assertIn('«' + 'x' * 79 + '…»', text)
        self.assertNotIn('Segunda', text)

    def test_reads_each_file_once_and_cuts_to_limit(self):
        self.escribir([self.entrada('a', 48)], {})
        with mock.patch.object(equipo, 'load', wraps=equipo.load) as load:
            text = equipo.calendario_pendiente('beta')
        self.assertEqual(load.call_count, 2)
        self.assertEqual(equipo.calendario_pendiente('beta', limit=40), text[:40])

    def test_without_files_or_brand_gives_nothing(self):
        self.assertEqual(equipo.calendario_pendiente('beta'), '')
        self.escribir([self.entrada('a', 48)])
        self.assertEqual(equipo.calendario_pendiente('prompted-os'), '')
        for content in ('[]', '{"entradas": 3}', 'no es json'):
            with self.subTest(content=content):
                (self.root / 'contenido' / 'calendario.json').write_text(content)
                self.assertEqual(equipo.calendario_pendiente('beta'), '')

    def test_missing_figures_file_means_nothing_is_measured(self):
        self.escribir([self.entrada('a', 48)])
        self.assertIn('1 sin cifras pasadas 24 h', equipo.calendario_pendiente('beta'))

    def test_plan_project_includes_calendario_pendiente_once(self):
        p = {**equipo.project('beta'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'STATE', self.root), mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), \
             mock.patch.object(equipo, 'aperturas_medidas', return_value=''), \
             mock.patch.object(equipo, 'analisis_contenido', return_value=''), \
             mock.patch.object(equipo, 'calendario_por_medir', return_value=''), \
             mock.patch.object(equipo, 'calendario_pendiente', return_value='CALENDARIO DE PRUEBA') as pendiente, \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('beta', batch, file)
        pendiente.assert_called_once_with('beta')
        self.assertEqual(claude.call_args.args[0].count('CALENDARIO DE PRUEBA'), 1)

    def test_five_long_unmeasured_and_failures_fit_without_cutting(self):
        self.escribir([self.entrada(f's{n}', 48 + n, titulo=f'{n}' + 't' * 89) for n in range(5)]
                      + [self.entrada(f'f{n}', 30 + n, 'fallo', nota='n' * 90) for n in range(5)])
        text = equipo.calendario_pendiente('beta')
        self.assertLess(len(text), 1100)
        self.assertTrue(text.endswith(' y 2 más'))
        self.assertIn('Contenido o con /medir antes de sacar conclusiones de esa línea.', text)
        self.assertEqual(text.count('n' * 79 + '…'), 3)
        self.assertEqual(text.count('\nFallos al publicar: '), 1)

    def test_plan_project_sends_unmeasured_and_failed_only_once(self):
        self.escribir([self.entrada('a', 48), self.entrada('f', 30, 'fallo', nota='PostOnce rechazó el vídeo')])
        p = {**equipo.project('beta'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'STATE', self.root), mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), \
             mock.patch.object(equipo, 'aperturas_medidas', return_value=''), \
             mock.patch.object(equipo, 'analisis_contenido', return_value=''), \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('beta', batch, file)
        prompt = claude.call_args.args[0]
        self.assertEqual(prompt.count('Título a'), 1)
        self.assertEqual(prompt.count('Título f'), 1)
        self.assertNotIn('Publicaciones del calendario sin cifras', prompt)
        self.assertEqual(prompt.count('Cifras apuntadas del calendario'), 1)
        self.assertEqual(prompt.count('con /medir'), 1)

    def test_project_brief_has_empty_calendario_pendiente_without_brand(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), \
             mock.patch.object(equipo, 'tasks', lambda pid: []), mock.patch.object(equipo, 'metrics', lambda pid: {}), \
             mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'ficha', lambda pid: None), \
             mock.patch.object(equipo, 'tendencias', lambda pid, limit=0: ''):
            self.assertEqual(equipo.project_brief('prompted-os')['calendario_pendiente'], '')


class ContenidoMedidoReelBueno(unittest.TestCase):
    MARCAS = {'contenido': {'marcas': [{'id': 'beta', 'prefijo': 'tf'}, {'id': 'norte-capital', 'prefijo': 'kc'}]}}
    LINEA = 'Ganadores de los Trial Reels en la cuenta principal: '

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.reels = self.root / 'reels'
        for name, value in (('WORKSPACE', self.root), ('REELS_DIA', self.reels), ('APP_CONFIG', self.MARCAS)):
            patch = mock.patch.object(equipo, name, value); patch.start(); self.addCleanup(patch.stop)

    def medir(self, **por_id):
        equipo.save(self.root / 'contenido' / 'medidas.json', {
            ident: {'vistas': v, 'visitas': vi, 'clics': c, 'compras': co} for ident, (v, vi, c, co) in por_id.items()})

    def test_winner_title_and_trial_views_come_from_the_previous_day(self):
        folder = self.reels / '2026-10-26'; folder.mkdir(parents=True)
        (folder / 'ganadores.json').write_text(json.dumps({'beta': {'titulo': 'Gancho X', 'vistas': 900}}))
        self.medir(**{'ritmo:tf-2000:2026-10-27': (500, 20, 4, 1)})
        self.assertIn(f'\n{self.LINEA}«Gancho X» (27/10): 900 vistas en Trial Reels → 500 vistas, 4 clics, 1 compras en la principal.',
                      equipo.contenido_medido('beta'))

    def test_without_winners_file_the_title_is_generic(self):
        self.medir(**{'ritmo:tf-2000:2026-10-27': (500, 20, 4, 1)})
        self.assertIn(f'\n{self.LINEA}«reel bueno del 27/10» (27/10): sin vistas de Trial apuntadas → 500 vistas, 4 clics, 1 compras en la principal.',
                      equipo.contenido_medido('beta'))

    def test_only_the_three_most_recent_are_listed(self):
        self.medir(**{f'ritmo:tf-2000:2026-10-2{d}': (10 * d, 1, d, 0) for d in range(1, 6)})
        text = equipo.contenido_medido('beta')
        self.assertIn('(25/10): ', text.split(self.LINEA)[1])
        self.assertEqual(text.split(self.LINEA)[1].count('«'), 3)
        self.assertNotIn('(21/10)', text)

    def test_invalid_ritmo_ids_do_not_count(self):
        self.medir(**{'ritmo:tf-1030:2026-10-27': (1, 1, 1, 1), 'ritmo:zz-2000:2026-10-27': (1, 1, 1, 1),
                      'ritmo:tf-2000:2026-1-1': (1, 1, 1, 1), 'ritmo:tf-2000:2026-13-45': (1, 1, 1, 1)})
        self.assertEqual(equipo.contenido_medido('beta'), '')

    def test_winners_line_stays_whole_when_the_text_is_cut(self):
        self.medir(**{'ritmo:tf-2000:2026-10-27': (500, 20, 4, 1)})
        completo = equipo.contenido_medido('beta', limit=10000)
        limit = len(completo) - 20
        text = equipo.contenido_medido('beta', limit=limit)
        self.assertLessEqual(len(text), limit)
        self.assertIn('→ 500 vistas, 4 clics, 1 compras en la principal.', text)
        self.assertTrue(text.endswith(completo[completo.index('\n' + self.LINEA):]))

    def test_long_winner_title_is_shortened(self):
        titulo = ''.join(chr(97 + i % 26) for i in range(120))
        folder = self.reels / '2026-10-26'; folder.mkdir(parents=True)
        (folder / 'ganadores.json').write_text(json.dumps({'beta': {'titulo': titulo, 'vistas': 900}}))
        self.medir(**{'ritmo:tf-2000:2026-10-27': (500, 20, 4, 1)})
        text = equipo.contenido_medido('beta', limit=10000)
        self.assertIn(f'«{titulo[:79]}…»', text)
        self.assertNotIn(titulo, text)

    def test_other_brand_winner_does_not_show_in_tf(self):
        self.medir(**{'ritmo:kc-2000:2026-10-27': (500, 20, 4, 1)})
        self.assertEqual(equipo.contenido_medido('beta'), '')
        self.assertIn(self.LINEA, equipo.contenido_medido('norte'))


class AperturasMedidas(unittest.TestCase):
    MARCAS = {'contenido': {'marcas': [{'id': 'beta', 'prefijo': 'tf'}, {'id': 'norte-capital', 'prefijo': 'kc'}]}}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.reels = self.root / 'reels'
        for name, value in (('WORKSPACE', self.root), ('REELS_DIA', self.reels), ('APP_CONFIG', self.MARCAS)):
            patch = mock.patch.object(equipo, name, value); patch.start(); self.addCleanup(patch.stop)
        self.file = self.root / 'contenido' / 'dia.json'

    def fecha(self, atras=0):
        return (equipo.now().date() - timedelta(days=atras)).strftime('%Y-%m-%d')

    def dia(self, **por_fecha):
        equipo.save(self.file, {fecha: {'vistas': vistas} for fecha, vistas in por_fecha.items()})

    def gancho(self, fecha, franja, h, texto):
        folder = self.reels / fecha; folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{franja}-h{h}.txt').write_text(texto)

    def test_winner_line_with_hooks_ordered_by_visits_and_skipping_comenta(self):
        hoy = self.fecha()
        self.dia(**{hoy: {'tf-1030-h1': 300, 'tf-1030-h2': 1200, 'tf-1030-h3': 800}})
        self.gancho(hoy, 'tf-1030', 1, 'Gancho uno\nresto')
        self.gancho(hoy, 'tf-1030', 2, '\n  Comenta GUÍA y te la mando\n\nGancho dos\n')
        self.gancho(hoy, 'tf-1030', 3, 'x' * 100)
        text = equipo.aperturas_medidas('beta')
        self.assertEqual(text.splitlines()[0], 'Trial Reels con vistas apuntadas (últimos 7 días; cifras reales que apuntó Owner en Contenido → Hoy; '
                                               'cada franja son 3 aperturas del mismo vídeo con distinto gancho): 1 franjas.')
        self.assertEqual(text.splitlines()[1], f'{hoy[8:]}/{hoy[5:7]} 10:30: gana apertura 2 «Gancho dos» con 1200 vistas; '
                                               f'apertura 3 «{"x" * 79}…» 800 vistas; apertura 1 «Gancho uno» 300 vistas')
        self.assertEqual(len(text.splitlines()), 2)

    def test_brands_are_separated_by_prefix(self):
        hoy = self.fecha()
        self.dia(**{hoy: {'kc-1730-h1': 50, 'kc-1730-h2': 70}})
        self.assertEqual(equipo.aperturas_medidas('beta'), '')
        self.assertIn('17:30: gana apertura 2 con 70 vistas; apertura 1 50 vistas', equipo.aperturas_medidas('norte'))

    def test_only_the_last_seven_days_count(self):
        self.dia(**{self.fecha(8): {'tf-1030-h1': 10}, self.fecha(6): {'tf-1730-h1': 20}})
        text = equipo.aperturas_medidas('beta')
        self.assertIn('1 franjas', text)
        self.assertIn('17:30: apertura 1 20 vistas', text)
        self.assertNotIn('10:30', text)
        self.dia(**{self.fecha(8): {'tf-1030-h1': 10}})
        self.assertEqual(equipo.aperturas_medidas('beta'), '')

    def test_tie_and_single_opening_do_not_say_gana(self):
        hoy = self.fecha()
        self.dia(**{hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 100, 'tf-1030-h3': 5, 'tf-1730-h3': 40}})
        text = equipo.aperturas_medidas('beta')
        self.assertNotIn('gana', text)
        self.assertIn('17:30: apertura 3 40 vistas', text)
        self.assertIn('10:30: apertura 1 100 vistas; apertura 2 100 vistas; apertura 3 5 vistas', text)

    def test_invalid_values_are_ignored(self):
        hoy = self.fecha()
        self.dia(**{hoy: {'tf-1030-h1': True, 'tf-1030-h2': -3, 'tf-1030-h3': '900', 'tf-1730-h1': 1.5, 'tf-1730-h2': None}})
        self.assertEqual(equipo.aperturas_medidas('beta'), '')
        self.dia(**{hoy: {'tf-1030-h1': True, 'tf-1030-h2': 0, 'tf-1030-h3': '900'}})
        self.assertIn('10:30: apertura 2 0 vistas', equipo.aperturas_medidas('beta'))

    def test_empty_without_brand_prefix_or_file(self):
        hoy = self.fecha()
        self.dia(**{hoy: {'tf-1030-h1': 10, 'xx-1030-h1': 10}})
        self.assertEqual(equipo.aperturas_medidas('prompted-os'), '')
        for config in ({}, {'contenido': {'marcas': [{'id': 'beta'}]}},
                       {'contenido': {'marcas': [{'id': 'beta', 'prefijo': 'TF'}]}},
                       {'contenido': {'marcas': [{'id': 'beta', 'prefijo': 'tf1'}]}},
                       {'contenido': {'marcas': [{'id': 'beta', 'prefijo': 'abcdefg'}]}}):
            with self.subTest(config=config), mock.patch.object(equipo, 'APP_CONFIG', config):
                self.assertEqual(equipo.aperturas_medidas('beta'), '')
        self.file.unlink()
        self.assertEqual(equipo.aperturas_medidas('beta'), '')
        equipo.save(self.file, [1, 2])
        self.assertEqual(equipo.aperturas_medidas('beta'), '')

    def test_without_reels_folder_or_files_the_line_has_no_hook(self):
        hoy = self.fecha()
        self.dia(**{hoy: {'tf-1030-h1': 10, 'tf-1030-h2': 30}})
        expected = f'{hoy[8:]}/{hoy[5:7]} 10:30: gana apertura 2 con 30 vistas; apertura 1 10 vistas'
        with mock.patch.object(equipo, 'REELS_DIA', None):
            self.assertEqual(equipo.aperturas_medidas('beta').splitlines()[1], expected)
        self.assertEqual(equipo.aperturas_medidas('beta').splitlines()[1], expected)   # folder missing

    def test_at_most_six_slots_most_recent_first(self):
        self.dia(**{self.fecha(i): {'tf-1030-h1': i, 'tf-1730-h1': i + 100} for i in range(4)})
        lines = equipo.aperturas_medidas('beta').splitlines()
        self.assertIn('8 franjas; se listan las 6 más recientes.', lines[0])
        self.assertEqual(len(lines), 7)
        self.assertTrue(lines[1].endswith('17:30: apertura 1 100 vistas'))
        self.assertTrue(lines[2].endswith('10:30: apertura 1 0 vistas'))

    def test_header_for_two_slots_does_not_claim_listing(self):
        self.dia(**{self.fecha(): {'tf-1030-h1': 10}, self.fecha(1): {'tf-1730-h1': 20}})
        header = equipo.aperturas_medidas('beta').splitlines()[0]
        self.assertIn('2 franjas.', header)
        self.assertNotIn('se listan', header)

    def test_hook_type_counts_winners_beyond_the_six_listed_slots(self):
        vistas = {}
        for atras in range(4):
            fecha = self.fecha(atras)
            vistas[fecha] = {}
            for hora in ('1030', '1730'):
                franja = f'tf-{hora}'
                vistas[fecha].update({f'{franja}-h1': 100, f'{franja}-h2': 50})
                if atras < 3:
                    self.gancho(fecha, franja, 1, 'Mira esto')
                    self.gancho(fecha, franja, 2, '3 costes')
                else:
                    self.gancho(fecha, franja, 1, '¿Cuánto cuesta?')
                    self.gancho(fecha, franja, 2, 'Mira esto')
        self.dia(**vistas)
        lines = equipo.aperturas_medidas('beta').splitlines()
        self.assertEqual(lines[1], self.tipo(8) + 'pregunta ganó 2 de 2; cifra ganó 0 de 6; otro ganó 6 de 8.')
        self.assertIn('8 franjas; se listan las 6 más recientes.', lines[0])
        self.assertEqual(len(lines), 8)

    def test_hook_type_line_counts_a_winner_beyond_the_six_listed_slots(self):
        vistas = {}
        for atras in range(4):
            fecha = self.fecha(atras)
            vistas[fecha] = {}
            for hora in ('1030', '1730'):
                franja = f'tf-{hora}'
                vistas[fecha][f'{franja}-h1'] = 10
                if (atras == 0 and hora == '1730') or (atras == 3 and hora == '1730'):
                    vistas[fecha][f'{franja}-h2'] = 5
                    self.gancho(fecha, franja, 1, '¿Cuánto cuesta?')
                    self.gancho(fecha, franja, 2, 'Mira esto')
        self.dia(**vistas)
        lines = equipo.aperturas_medidas('beta').splitlines()
        self.assertEqual(lines[1], self.tipo(2) + 'pregunta ganó 2 de 2; otro ganó 0 de 2.')
        self.assertIn('8 franjas; se listan las 6 más recientes.', lines[0])

    def test_limit_cuts_the_text(self):
        self.dia(**{self.fecha(): {'tf-1030-h1': 10}})
        text = equipo.aperturas_medidas('beta')
        self.assertEqual(equipo.aperturas_medidas('beta', limit=30), text[:30])

    def tipo(self, n):
        return f'Tipo de gancho que gana ({n} franjas con ganador): '

    def dos_franjas_con_ganador(self):
        hoy, ayer = self.fecha(), self.fecha(1)
        self.dia(**{hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 50}, ayer: {'tf-1730-h1': 80, 'tf-1730-h2': 20}})
        self.gancho(hoy, 'tf-1030', 1, '¿Cuánto cuesta de verdad?')
        self.gancho(hoy, 'tf-1030', 2, '3 costes ocultos')
        self.gancho(ayer, 'tf-1730', 1, '2 reglas')
        self.gancho(ayer, 'tf-1730', 2, 'Mira esto')

    def test_hook_type_line_after_header_counts_winners_by_type(self):
        self.dos_franjas_con_ganador()
        lines = equipo.aperturas_medidas('beta').splitlines()
        self.assertEqual(lines[1], self.tipo(2) + 'pregunta ganó 1 de 1; cifra ganó 1 de 2; otro ganó 0 de 1.')
        self.assertIn('2 franjas.', lines[0])
        self.assertEqual(len(lines), 4)
        self.assertIn('10:30: gana apertura 1', lines[2])
        self.assertIn('17:30: gana apertura 1', lines[3])

    def test_hook_type_line_needs_two_winners_with_hook(self):
        hoy, ayer = self.fecha(), self.fecha(1)
        self.dia(**{hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 50}})
        self.gancho(hoy, 'tf-1030', 1, '¿Cuánto cuesta?')
        self.gancho(hoy, 'tf-1030', 2, 'Mira esto')
        self.assertNotIn('Tipo de gancho', equipo.aperturas_medidas('beta'))
        self.dia(**{hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 50}, ayer: {'tf-1730-h1': 80, 'tf-1730-h2': 20}})
        with mock.patch.object(equipo, 'REELS_DIA', None):
            text = equipo.aperturas_medidas('beta')
        self.assertIn('2 franjas', text)
        self.assertFalse(any(l.startswith('Tipo de gancho') for l in text.splitlines()))

    def test_hook_type_line_counts_only_winners_with_a_hook(self):
        hoy, ayer, antes = self.fecha(), self.fecha(1), self.fecha(2)
        self.dia(**{
            hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 50},
            ayer: {'tf-1730-h1': 80, 'tf-1730-h2': 20},
            antes: {'tf-1030-h1': 60, 'tf-1030-h2': 10},
        })
        self.gancho(hoy, 'tf-1030', 1, '¿Cuánto cuesta?')
        self.gancho(hoy, 'tf-1030', 2, 'Mira esto')
        self.gancho(ayer, 'tf-1730', 1, '2 reglas')
        self.gancho(ayer, 'tf-1730', 2, 'Mira esto')
        self.gancho(antes, 'tf-1030', 1, '')
        self.gancho(antes, 'tf-1030', 2, 'Mira esto')
        linea = equipo.aperturas_medidas('beta').splitlines()[1]
        self.assertTrue(linea.startswith(self.tipo(2)))

    def test_tie_does_not_count_in_hook_type_line(self):
        hoy, ayer = self.fecha(), self.fecha(1)
        self.dia(**{hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 100}, ayer: {'tf-1730-h1': 80, 'tf-1730-h2': 20}})
        self.gancho(hoy, 'tf-1030', 1, '¿Cuánto cuesta?')
        self.gancho(hoy, 'tf-1030', 2, '3 costes')
        self.gancho(ayer, 'tf-1730', 1, '2 reglas')
        self.gancho(ayer, 'tf-1730', 2, 'Mira esto')
        text = equipo.aperturas_medidas('beta')
        self.assertIn('2 franjas', text)
        self.assertNotIn('Tipo de gancho', text)

    def test_tipo_gancho(self):
        for texto, tipo in (('¿Sabías?', 'pregunta'), ('Pierdes 300 €?', 'pregunta'), ('3 costes', 'cifra'),
                            ('Mira esto', 'otro'), ('', '')):
            with self.subTest(texto=texto): self.assertEqual(equipo.tipo_gancho(texto), tipo)

    def test_hook_type_line_survives_the_limit(self):
        self.dos_franjas_con_ganador()
        # the header takes 179 characters, so 210 leaves room for the whole start of the new line and cuts the franja lines
        self.assertIn('Tipo de gancho que gana', equipo.aperturas_medidas('beta', limit=210))

    def test_hook_type_uses_the_hook_without_the_80_character_cut(self):
        hoy, ayer = self.fecha(), self.fecha(1)
        self.dia(**{hoy: {'tf-1030-h1': 100, 'tf-1030-h2': 50}, ayer: {'tf-1730-h1': 80, 'tf-1730-h2': 20}})
        for dia in (hoy, ayer):
            franja = 'tf-1030' if dia == hoy else 'tf-1730'
            self.gancho(dia, franja, 1, 'x' * 90 + ' 5')
            self.gancho(dia, franja, 2, 'Mira esto')
        self.assertEqual(equipo.aperturas_medidas('beta').splitlines()[1],
                         self.tipo(2) + 'cifra ganó 2 de 2; otro ganó 0 de 2.')

    def test_plan_project_includes_aperturas_once(self):
        p = {**equipo.project('beta'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'STATE', self.root), mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), \
             mock.patch.object(equipo, 'aperturas_medidas', return_value='APERTURAS DE PRUEBA') as aperturas, \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('beta', batch, file)
        aperturas.assert_called_once_with('beta')
        self.assertEqual(claude.call_args.args[0].count('APERTURAS DE PRUEBA'), 1)

    def test_project_brief_has_empty_aperturas_without_brand(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), \
             mock.patch.object(equipo, 'tasks', lambda pid: []), mock.patch.object(equipo, 'metrics', lambda pid: {}), \
             mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'ficha', lambda pid: None), \
             mock.patch.object(equipo, 'tendencias', lambda pid, limit=0: ''):
            self.assertEqual(equipo.project_brief('prompted-os')['aperturas_medidas'], '')


class CifrasCalendario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'
        self.ahora = datetime(2026, 10, 31, 12, tzinfo=equipo.MADRID)
        self.archivo = self.state / 'cifras_calendario' / 'prompted-os.json'
        for name, value in (('STATE', self.state), ('now', mock.Mock(return_value=self.ahora))):
            patch = mock.patch.object(equipo, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def guardar(self, cifras):
        equipo.save(self.archivo, cifras)

    def test_absent_file_returns_none_without_creating_it(self):
        self.assertIsNone(equipo.cifras_calendario('prompted-os'))
        self.assertFalse(self.archivo.exists())

    def test_ignores_invalid_entries_and_chooses_the_most_recent(self):
        self.guardar({'nota': [8, 9], '2026-10-31': [1], '2026-10-28': [1, 0],
                      '2026-10-27': [-1, 2], '2026-10-26': ['1', 2],
                      '2026-10-30': [2, 4], '2026-10-29': [1, 3]})
        self.assertEqual(equipo.cifras_calendario('prompted-os'),
                         {'fecha': '2026-10-30', 'apuntadas': 2, 'publicadas': 4, 'hace_7': None, 'hace_14': None})

    def test_entry_from_three_days_ago_is_old(self):
        self.guardar({'2026-10-28': [1, 2]})
        self.assertIsNone(equipo.cifras_calendario('prompted-os'))

    def test_tomorrow_is_ignored(self):
        self.guardar({'2026-10-30': [2, 4], '2026-11-01': [3, 4]})
        self.assertEqual(equipo.cifras_calendario('prompted-os')['fecha'], '2026-10-30')

    def test_finds_eight_days_ago_when_seven_is_missing(self):
        self.guardar({'2026-10-31': [2, 5], '2026-10-23': [1, 4]})
        self.assertEqual(equipo.cifras_calendario('prompted-os'),
                         {'fecha': '2026-10-31', 'apuntadas': 2, 'publicadas': 5,
                          'hace_7': {'dias': 8, 'apuntadas': 1, 'publicadas': 4}, 'hace_14': None})

    def test_finds_fourteen_day_comparisons(self):
        for dias in (14, 15, 13):
            with self.subTest(dias=dias):
                self.guardar({'2026-10-31': [2, 5],
                              (self.ahora - timedelta(days=dias)).strftime('%Y-%m-%d'): [1, 4]})
                self.assertEqual(equipo.cifras_calendario('prompted-os')['hace_14'],
                                 {'dias': dias, 'apuntadas': 1, 'publicadas': 4})

    def test_has_no_fourteen_day_comparison_when_no_nearby_day_is_valid(self):
        self.guardar({'2026-10-31': [2, 5]})
        self.assertIsNone(equipo.cifras_calendario('prompted-os')['hace_14'])

    def test_has_no_comparison_when_no_nearby_day_is_valid(self):
        self.guardar({'2026-10-31': [2, 5], '2026-10-24': [1, 0]})
        self.assertIsNone(equipo.cifras_calendario('prompted-os')['hace_7'])

    def test_never_modifies_existing_history(self):
        history = {'2026-10-31': [2, 5]}
        self.guardar(history)
        before = self.archivo.read_bytes()
        equipo.cifras_calendario('prompted-os')
        self.assertEqual(self.archivo.read_bytes(), before)

    def test_status_includes_calendar_figures(self):
        self.guardar({'2026-10-31': [2, 5]})
        equipo.save(self.state / 'atribucion_calendario' / 'prompted-os.json', {'2026-10-31': [1, 0, 2]})
        config = {**equipo.CONFIG, 'projects': {'prompted-os': equipo.CONFIG['projects']['prompted-os']}}
        patches = [mock.patch.object(equipo, 'CONFIG', config), mock.patch.object(equipo, 'ROOT', self.root),
                   mock.patch.object(equipo, 'SALUD', self.state / 'salud.json'),
                   mock.patch.object(equipo, 'tasks', lambda pid: []), mock.patch.object(equipo, 'metrics', lambda pid: {}),
                   mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'resultados', lambda pid: []),
                   mock.patch.object(equipo, 'hermes_overview', lambda: {}), mock.patch.object(equipo, 'modelos_hoy', lambda: {}),
                   mock.patch.object(equipo, 'tablero', lambda: []), mock.patch.object(equipo, 'pending_merge', lambda pid: 0),
                   mock.patch.object(equipo, 'checklist', lambda ts: []), mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}),
                   mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'mejoras_seguras', lambda pid: [])]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)
        self.assertEqual(equipo.status()['proyectos']['prompted-os']['cifras_calendario'],
                         {'fecha': '2026-10-31', 'apuntadas': 2, 'publicadas': 5, 'hace_7': None, 'hace_14': None})
        self.assertEqual(equipo.status()['proyectos']['prompted-os']['atribucion_calendario'],
                         {'fecha': '2026-10-31', 'con_clics': 1, 'con_compras': 0, 'con_cifras': 2,
                          'hace_7': None, 'hace_14': None})


class AtribucionCalendario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state'
        self.ahora = datetime(2026, 10, 31, 12, tzinfo=equipo.MADRID)
        self.archivo = self.state / 'atribucion_calendario' / 'prompted-os.json'
        for name, value in (('STATE', self.state), ('now', mock.Mock(return_value=self.ahora))):
            patch = mock.patch.object(equipo, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def guardar(self, cifras):
        equipo.save(self.archivo, cifras)

    def test_absent_file_returns_none_without_creating_it(self):
        self.assertIsNone(equipo.atribucion_calendario('prompted-os'))
        self.assertFalse(self.archivo.exists())

    def test_today_and_yesterday_are_valid(self):
        for fecha in ('2026-10-31', '2026-10-30'):
            with self.subTest(fecha=fecha):
                self.guardar({fecha: [2, 1, 5]})
                self.assertEqual(equipo.atribucion_calendario('prompted-os'),
                                 {'fecha': fecha, 'con_clics': 2, 'con_compras': 1, 'con_cifras': 5,
                                  'hace_7': None, 'hace_14': None})

    def test_day_before_yesterday_is_old(self):
        self.guardar({'2026-10-29': [2, 1, 5]})
        self.assertIsNone(equipo.atribucion_calendario('prompted-os'))

    def test_ignores_invalid_entries_and_future_dates(self):
        for valor in (None, [1], [1, 0, 0], [-1, 0, 2], ['1', 0, 2], [True, 0, 2],
                      [1.5, 0, 2], [3, 0, 2], [0, 3, 2]):
            with self.subTest(valor=valor):
                self.guardar({'nota': [1, 0, 2], '2026-02-30': [1, 0, 2], '2026-1-1': [1, 0, 2],
                              '2026-11-01': [2, 1, 5], '2026-10-31': valor, '2026-10-30': [1, 0, 2]})
                self.assertEqual(equipo.atribucion_calendario('prompted-os')['fecha'], '2026-10-30')

    def test_comparison_prefers_seven_then_eight_then_six_days(self):
        history = {'2026-10-31': [2, 1, 5], '2026-10-30': [1, 0, 2],
                   '2026-10-24': [1, 0, 4], '2026-10-23': [1, 0, 4], '2026-10-25': [1, 0, 4]}
        for dias in (7, 8, 6):
            with self.subTest(dias=dias):
                self.guardar(history)
                self.assertEqual(equipo.atribucion_calendario('prompted-os'),
                                 {'fecha': '2026-10-31', 'con_clics': 2, 'con_compras': 1, 'con_cifras': 5,
                                  'hace_7': {'dias': dias, 'con_clics': 1, 'con_compras': 0, 'con_cifras': 4},
                                  'hace_14': None})
                del history[(self.ahora - timedelta(days=dias)).strftime('%Y-%m-%d')]

    def test_finds_fourteen_day_comparisons(self):
        for dias in (14, 15, 13):
            with self.subTest(dias=dias):
                self.guardar({'2026-10-31': [2, 1, 5],
                              (self.ahora - timedelta(days=dias)).strftime('%Y-%m-%d'): [1, 0, 4]})
                self.assertEqual(equipo.atribucion_calendario('prompted-os')['hace_14'],
                                 {'dias': dias, 'con_clics': 1, 'con_compras': 0, 'con_cifras': 4})

    def test_has_no_fourteen_day_comparison_when_no_nearby_day_is_valid(self):
        self.guardar({'2026-10-31': [2, 1, 5]})
        self.assertIsNone(equipo.atribucion_calendario('prompted-os')['hace_14'])

    def test_invalid_comparison_is_ignored_without_modifying_history(self):
        self.guardar({'2026-10-31': [2, 1, 5], '2026-10-24': [3, 0, 2]})
        before = self.archivo.read_bytes()
        self.assertIsNone(equipo.atribucion_calendario('prompted-os')['hace_7'])
        self.assertEqual(self.archivo.read_bytes(), before)

    def test_non_dictionary_file_returns_none(self):
        for valor in ([], '3', None):
            with self.subTest(valor=valor):
                self.guardar(valor)
                self.assertIsNone(equipo.atribucion_calendario('prompted-os'))


class CalendarioPorMedir(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ahora = datetime(2026, 10, 31, 12, tzinfo=equipo.MADRID)
        self.state = self.root / 'state'
        self.historico = self.state / 'cifras_calendario' / 'beta.json'
        self.atribucion = self.state / 'atribucion_calendario' / 'beta.json'
        for name, value in (('WORKSPACE', self.root), ('STATE', self.state), ('now', mock.Mock(return_value=self.ahora))):
            patch = mock.patch.object(equipo, name, value)
            patch.start(); self.addCleanup(patch.stop)
        self.escribir([])

    def entrada(self, ident='uno', horas=48, **extra):
        at = self.ahora - timedelta(hours=horas)
        return {'id': ident, 'marca': 'beta', 'fecha': at.strftime('%Y-%m-%d'),
                'hora': at.strftime('%H:%M'), 'estado': 'publicado', 'titulo': f'Título {ident}',
                'red': 'instagram', **extra}

    def escribir(self, entradas, medidas=None):
        equipo.save(self.root / 'contenido' / 'calendario.json', {'entradas': entradas})
        equipo.save(self.root / 'contenido' / 'medidas.json', {} if medidas is None else medidas)

    def test_publication_without_figures(self):
        self.escribir([self.entrada()])
        text = equipo.calendario_por_medir('beta')
        self.assertIn('últimos 7 días): 0 de 1.', text)
        self.assertIn('sin cifras (publicadas hace más de 24 h, últimos 7 días): 1:', text)
        self.assertIn('«Título uno» (instagram, 29/10)', text)
        self.assertIn('vistas, visitas, clics y compras en Contenido o con /medir', text)

    def test_measured_publication_is_excluded_even_with_empty_measure(self):
        self.escribir([self.entrada()], {'cal:uno': {}})
        text = equipo.calendario_por_medir('beta')
        self.assertEqual(text, 'Cifras apuntadas del calendario (publicadas hace más de 24 h, últimos 7 días): 1 de 1.')
        self.assertNotIn('sin cifras', text)

    def preparar_atribucion(self):
        self.escribir([self.entrada('uno'), self.entrada('dos'), self.entrada('tres')], {
            'cal:uno': {'vistas': 30, 'visitas': 5, 'clics': 3, 'compras': 1},
            'cal:dos': {'vistas': 0, 'visitas': 0, 'clics': 0, 'compras': 0}})
        return 'Atribución del calendario (mismas publicaciones): de 2 con cifras, 1 con clics y 1 con compras.'

    def test_attribution_counts_valid_figures_and_saves_using_now(self):
        linea = self.preparar_atribucion()
        with mock.patch.object(equipo, 'today', return_value='2000-01-01'):
            text = equipo.calendario_por_medir('beta')
        self.assertEqual(text.splitlines()[1], linea)
        self.assertEqual(json.loads(self.atribucion.read_text()), {'2026-10-31': [1, 1, 2]})
        self.assertEqual(json.loads(self.historico.read_text()), {'2026-10-31': [2, 3]})

    def test_invalid_measures_do_not_create_attribution(self):
        cero = {'vistas': 0, 'visitas': 0, 'clics': 0, 'compras': 0}
        invalidas = [{}, None, []]
        for campo in cero:
            invalidas.extend({**cero, campo: valor} for valor in (True, -1, 0.0, None, '0'))
        for medida in invalidas:
            with self.subTest(medida=medida):
                self.escribir([self.entrada()], {'cal:uno': medida})
                self.assertNotIn('Atribución', equipo.calendario_por_medir('beta'))
                self.assertFalse(self.atribucion.exists())

    def test_attribution_compares_seven_days_ago_first(self):
        linea = self.preparar_atribucion()
        equipo.save(self.atribucion, {'2026-10-24': [0, 0, 3], '2026-10-23': [1, 1, 3],
                                      '2026-10-25': [2, 2, 3]})
        text = equipo.calendario_por_medir('beta')
        self.assertEqual(text.splitlines()[1], linea + ' Hace 7 días: 0 con clics y 0 con compras de 3.')

    def test_attribution_includes_seven_and_fourteen_day_comparisons_in_order(self):
        self.preparar_atribucion()
        equipo.save(self.atribucion, {'2026-10-24': [0, 0, 3], '2026-10-17': [1, 1, 4]})
        linea = equipo.calendario_por_medir('beta').splitlines()[1]
        self.assertLess(linea.index(' Hace 7 días: '), linea.index(' Hace 14 días: '))

    def test_attribution_includes_only_fourteen_day_comparison_when_seven_is_missing(self):
        self.preparar_atribucion()
        equipo.save(self.atribucion, {'2026-10-17': [1, 1, 4]})
        linea = equipo.calendario_por_medir('beta').splitlines()[1]
        self.assertIn(' Hace 14 días: ', linea)
        self.assertNotIn(' Hace 7 días: ', linea)

    def test_invalid_previous_attribution_does_not_change_line(self):
        linea = self.preparar_atribucion()
        for valor in ([2, 0, 1], [0, 2, 1], [0, 0, 0], [0, 0], [True, 0, 3],
                      [0, 0, 3.0], None, [-1, 0, 3], [0, 0, 3, 4], {}):
            with self.subTest(valor=valor):
                equipo.save(self.atribucion, {'2026-10-24': valor})
                text = equipo.calendario_por_medir('beta')
                self.assertEqual(text.splitlines()[1], linea)

    def test_attribution_prefers_eight_days_ago_over_six(self):
        linea = self.preparar_atribucion()
        equipo.save(self.atribucion, {'2026-10-24': [2, 0, 1], '2026-10-23': [0, 0, 3],
                                      '2026-10-25': [1, 1, 2]})
        text = equipo.calendario_por_medir('beta')
        self.assertEqual(text.splitlines()[1], linea + ' Hace 8 días: 0 con clics y 0 con compras de 3.')

    def test_attribution_uses_six_days_ago_when_others_are_invalid(self):
        linea = self.preparar_atribucion()
        equipo.save(self.atribucion, {'2026-10-24': None, '2026-10-23': [0, 0, 0],
                                      '2026-10-25': [0, 0, 3]})
        text = equipo.calendario_por_medir('beta')
        self.assertEqual(text.splitlines()[1], linea + ' Hace 6 días: 0 con clics y 0 con compras de 3.')

    def test_solo_cifras_includes_attribution(self):
        linea = self.preparar_atribucion()
        text = equipo.calendario_por_medir('beta', solo_cifras=True)
        self.assertEqual(len(text.splitlines()), 2)
        self.assertEqual(text.splitlines()[1], linea)
        self.assertEqual(json.loads(self.atribucion.read_text()), {'2026-10-31': [1, 1, 2]})

    def test_attribution_excludes_old_recent_other_brand_and_other_states(self):
        medida = {'vistas': 30, 'visitas': 5, 'clics': 3, 'compras': 1}
        for extra in ({'horas': 8 * 24}, {'horas': 10}, {'horas': -24},
                      {'marca': 'norte-capital'}, {'estado': 'programado'}, {'estado': 'fallo'}):
            with self.subTest(extra=extra):
                linea = self.preparar_atribucion()
                self.escribir([self.entrada('uno'), self.entrada('dos'), self.entrada('fuera', **extra)], {
                    'cal:uno': medida, 'cal:dos': dict.fromkeys(medida, 0), 'cal:fuera': medida})
                text = equipo.calendario_por_medir('beta')
                self.assertEqual(text.splitlines()[1], linea)
                self.assertEqual(json.loads(self.atribucion.read_text()), {'2026-10-31': [1, 1, 2]})

    def test_no_valid_figures_leave_existing_attribution_untouched(self):
        history = {'2026-10-24': [0, 0, 3]}
        equipo.save(self.atribucion, history)
        self.escribir([self.entrada()], {'cal:uno': {}})
        self.assertNotIn('Atribución', equipo.calendario_por_medir('beta'))
        self.assertEqual(json.loads(self.atribucion.read_text()), history)

    def test_attribution_keeps_sixty_most_recent_dates(self):
        self.preparar_atribucion()
        history = {(self.ahora - timedelta(days=i)).strftime('%Y-%m-%d'): [0, 0, 3]
                   for i in range(1, 62)}
        equipo.save(self.atribucion, {**history, 'notas': 'pendiente'})
        equipo.calendario_por_medir('beta')
        expected = {fecha: history[fecha] for fecha in sorted(history)[-59:]}
        self.assertEqual(json.loads(self.atribucion.read_text()), {**expected, '2026-10-31': [1, 1, 2]})

    def test_invalid_attribution_history_does_not_interrupt_text(self):
        self.preparar_atribucion()
        original = equipo.calendario_por_medir('beta')
        for history in ([], None, 'incorrecto'):
            with self.subTest(history=history):
                equipo.save(self.atribucion, history)
                self.assertEqual(equipo.calendario_por_medir('beta'), original)

    def test_attribution_write_failure_preserves_text_and_comparison(self):
        self.preparar_atribucion()
        equipo.save(self.atribucion, {'2026-10-24': [0, 0, 3]})
        original = equipo.calendario_por_medir('beta')
        save = equipo.save
        def guardar(path, value):
            if path == self.atribucion: raise OSError('sin espacio')
            return save(path, value)
        with mock.patch.object(equipo, 'save', side_effect=guardar):
            self.assertEqual(equipo.calendario_por_medir('beta'), original)

    def test_saves_daily_figures_using_now(self):
        self.escribir([self.entrada()])
        with mock.patch.object(equipo, 'today', return_value='2000-01-01'):
            equipo.calendario_por_medir('beta')
        self.assertEqual(json.loads(self.historico.read_text()), {'2026-10-31': [0, 1]})

    def test_overwrites_figures_for_the_same_day(self):
        self.escribir([self.entrada()])
        equipo.calendario_por_medir('beta')
        self.escribir([self.entrada()], {'cal:uno': {}})
        equipo.calendario_por_medir('beta')
        self.assertEqual(json.loads(self.historico.read_text()), {'2026-10-31': [1, 1]})

    def test_compares_figures_from_seven_days_ago(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-24': [1, 4]})
        text = equipo.calendario_por_medir('beta')
        self.assertTrue(text.splitlines()[0].endswith('0 de 1. Hace 7 días: 1 de 4.'))
        self.assertEqual(json.loads(self.historico.read_text()),
                         {'2026-10-24': [1, 4], '2026-10-31': [0, 1]})

    def test_missing_or_invalid_previous_figures_do_not_change_text(self):
        self.escribir([self.entrada()])
        original = equipo.calendario_por_medir('beta')
        histories = [{'2026-10-21': [1, 4]}, {'2026-10-26': [1, 4]}] + [
            {'2026-10-24': value} for value in (
                [0, 0], ['x', 4], [1, 'x'], [True, 4], [1, True], [-1, 4], [1, -1],
                [1.0, 4], [1, 4.0], [1], [1, 4, 5], None, {'apuntadas': 1})]
        for history in histories:
            with self.subTest(history=history):
                equipo.save(self.historico, history)
                text = equipo.calendario_por_medir('beta')
                self.assertNotIn('Hace ', text)
                self.assertEqual(text, original)

    def test_compares_valid_figures_from_eight_days_ago(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-23': [1, 4]})
        self.assertTrue(equipo.calendario_por_medir('beta').splitlines()[0].endswith('0 de 1. Hace 8 días: 1 de 4.'))

    def test_compares_valid_figures_from_six_days_ago(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-25': [1, 4]})
        self.assertTrue(equipo.calendario_por_medir('beta').splitlines()[0].endswith('0 de 1. Hace 6 días: 1 de 4.'))

    def test_prefers_eight_days_ago_over_six_days_ago(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-23': [1, 4], '2026-10-25': [2, 4]})
        self.assertTrue(equipo.calendario_por_medir('beta').splitlines()[0].endswith('0 de 1. Hace 8 días: 1 de 4.'))

    def test_uses_six_days_ago_when_eight_days_ago_is_invalid(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-24': [0, 0], '2026-10-25': [1, 4]})
        self.assertTrue(equipo.calendario_por_medir('beta').splitlines()[0].endswith('0 de 1. Hace 6 días: 1 de 4.'))

    def primera_linea(self):
        return equipo.calendario_por_medir('beta').splitlines()[0]

    def test_compares_figures_from_fourteen_days_ago_alone(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-17': [1, 4]})
        self.assertTrue(self.primera_linea().endswith('0 de 1. Hace 14 días: 1 de 4.'))

    def test_compares_both_weeks(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-24': [2, 4], '2026-10-17': [1, 4]})
        self.assertTrue(self.primera_linea().endswith('Hace 7 días: 2 de 4. Hace 14 días: 1 de 4.'))

    def test_compares_figures_from_fifteen_days_ago(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-16': [1, 4]})
        self.assertTrue(self.primera_linea().endswith('0 de 1. Hace 15 días: 1 de 4.'))

    def test_uses_thirteen_days_ago_when_fourteen_is_invalid(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-17': [0, 0], '2026-10-18': [1, 4]})
        self.assertTrue(self.primera_linea().endswith('0 de 1. Hace 13 días: 1 de 4.'))

    def test_invalid_fourteen_days_ago_adds_nothing(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-17': [True, 4]})
        self.assertNotIn('Hace ', self.primera_linea())

    def test_only_failed_publications_do_not_create_history(self):
        self.escribir([self.entrada(estado='fallo')])
        equipo.calendario_por_medir('beta')
        self.assertFalse(self.historico.exists())

    def test_without_publications_does_not_modify_existing_history(self):
        self.escribir([self.entrada(estado='fallo')])
        history = {'2026-10-24': [1, 4]}
        equipo.save(self.historico, history)
        equipo.calendario_por_medir('beta')
        self.assertEqual(json.loads(self.historico.read_text()), history)

    def test_keeps_only_the_sixty_most_recent_dates(self):
        self.escribir([self.entrada()])
        history = {(self.ahora - timedelta(days=i)).strftime('%Y-%m-%d'): [1, 4]
                   for i in range(1, 62)}
        equipo.save(self.historico, history)
        equipo.calendario_por_medir('beta')
        saved = json.loads(self.historico.read_text())
        expected = {fecha: history[fecha] for fecha in sorted(history)[-59:]}
        self.assertEqual(saved, {**expected, '2026-10-31': [0, 1]})
        self.assertEqual(len(saved), 60)
        self.assertNotIn(min(history), saved)

    def test_discards_non_date_history_keys_when_saving(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'notas': 'pendiente', '2026-10-24': [1, 4]})
        equipo.calendario_por_medir('beta')
        self.assertNotIn('notas', json.loads(self.historico.read_text()))

    def test_invalid_history_file_does_not_interrupt_text(self):
        self.escribir([self.entrada()])
        original = equipo.calendario_por_medir('beta')
        for raw in ('[]', '{broken'):
            with self.subTest(raw=raw):
                self.historico.write_text(raw)
                self.assertEqual(equipo.calendario_por_medir('beta'), original)

    def test_history_read_failure_does_not_interrupt_text(self):
        self.escribir([self.entrada()])
        original = equipo.calendario_por_medir('beta')
        read_text = Path.read_text
        def leer(path, *args, **kwargs):
            if path == self.historico: raise OSError('sin permiso')
            return read_text(path, *args, **kwargs)
        with mock.patch.object(Path, 'read_text', autospec=True, side_effect=leer):
            self.assertEqual(equipo.calendario_por_medir('beta'), original)

    def test_history_write_failure_preserves_text_and_comparison(self):
        self.escribir([self.entrada()])
        equipo.save(self.historico, {'2026-10-24': [1, 4]})
        original = equipo.calendario_por_medir('beta')
        with mock.patch.object(equipo, 'save', side_effect=OSError('sin espacio')):
            self.assertEqual(equipo.calendario_por_medir('beta'), original)

    def test_counts_measured_and_pending_publications(self):
        self.escribir([self.entrada('medida', 48), self.entrada('pendiente', 72)], {'cal:medida': {}})
        text = equipo.calendario_por_medir('beta')
        self.assertIn('últimos 7 días): 1 de 2.', text)
        self.assertIn('«Título pendiente» (instagram, 28/10)', text)
        self.assertNotIn('«Título medida»', text)

    def test_publication_from_less_than_24_hours_ago_is_not_counted(self):
        self.escribir([self.entrada(horas=10)])
        self.assertEqual(equipo.calendario_por_medir('beta'), '')

    def test_recent_old_other_brand_and_other_states_are_excluded(self):
        for extra in ({'horas': 10}, {'horas': 8 * 24}, {'marca': 'norte-capital'},
                      {'estado': 'programado'}, {'horas': -24}):
            with self.subTest(extra=extra):
                self.escribir([self.entrada(**extra)])
                self.assertEqual(equipo.calendario_por_medir('beta'), '')

    def test_recent_failures_include_note_but_old_and_future_failures_do_not(self):
        self.escribir([self.entrada('fallida', 24, estado='fallo', nota='Error de publicación'),
                       self.entrada('vieja', 8 * 24, estado='fallo', nota='Error antiguo'),
                       self.entrada('futura', -1, estado='fallo')])
        self.assertEqual(equipo.calendario_por_medir('beta'),
                         'Publicaciones del calendario que fallaron al publicar (7 días): 1: '
                         '«Título fallida» (instagram, 30/10): Error de publicación')

    def test_recent_failures_without_note_do_not_end_with_colon(self):
        for nota in (None, '  '):
            with self.subTest(nota=nota):
                entrada = self.entrada('fallida', 24, estado='fallo')
                if nota is not None: entrada['nota'] = nota
                self.escribir([entrada])
                self.assertEqual(equipo.calendario_por_medir('beta'),
                                 'Publicaciones del calendario que fallaron al publicar (7 días): 1: '
                                 '«Título fallida» (instagram, 30/10)')

    def test_failed_publication_without_note_has_no_trailing_colon(self):
        self.escribir([self.entrada(estado='fallo')])
        text = equipo.calendario_por_medir('beta')
        self.assertIn('«Título uno» (instagram, 29/10)', text)
        self.assertNotIn('29/10): ', text)

    def test_only_three_most_recent_and_remaining_count(self):
        self.escribir([self.entrada(str(i), 24 * (i + 1)) for i in range(5, 0, -1)])
        text = equipo.calendario_por_medir('beta')
        self.assertIn('últimos 7 días): 5:', text)
        self.assertEqual(re.findall(r'«([^»]+)»', text), ['Título 1', 'Título 2', 'Título 3'])
        self.assertIn('y 2 más', text)

    def test_without_brand(self):
        with mock.patch.object(equipo, 'load') as load:
            self.assertEqual(equipo.calendario_por_medir('prompted-os'), '')
        load.assert_not_called()

    def test_invalid_file_shapes(self):
        for filename, value in (('calendario.json', []), ('medidas.json', []),
                                ('calendario.json', {}), ('calendario.json', {'entradas': {}})):
            with self.subTest(filename=filename, value=value):
                self.escribir([self.entrada()])
                equipo.save(self.root / 'contenido' / filename, value)
                self.assertEqual(equipo.calendario_por_medir('beta'), '')

    def test_invalid_entries_are_ignored(self):
        invalid = [None, [], 'entrada'] + [self.entrada(**extra) for extra in (
            {'id': 3}, {'fecha': '2026-02-30'}, {'fecha': '2026-10-1'}, {'fecha': None},
            {'hora': '24:00'}, {'hora': '12:60'}, {'hora': '1:00'}, {'hora': None})]
        self.escribir(invalid)
        self.assertEqual(equipo.calendario_por_medir('beta'), '')

    def test_reads_only_two_json_files_and_uses_fallback_title(self):
        self.escribir([self.entrada(titulo=' ', texto=str(self.root / 'never-read.txt'))])
        original = Path.read_text
        with mock.patch.object(Path, 'read_text', autospec=True, side_effect=original) as read:
            text = equipo.calendario_por_medir('beta')
        self.assertIn('«publicación del calendario del 29/10»', text)
        self.assertEqual([call.args[0] for call in read.call_args_list],
                         [self.root / 'contenido' / 'calendario.json', self.root / 'contenido' / 'medidas.json',
                          self.historico])

    def test_solo_cifras_gives_only_the_figures_line_and_saves_history(self):
        self.escribir([self.entrada(), self.entrada('fallo', 30, estado='fallo', nota='Error de publicación')])
        equipo.save(self.historico, {'2026-10-24': [1, 4]})
        text = equipo.calendario_por_medir('beta', solo_cifras=True)
        self.assertEqual(len(text.splitlines()), 1)
        self.assertTrue(text.endswith('0 de 1. Hace 7 días: 1 de 4.'))
        self.assertNotIn('sin cifras (', text)
        self.assertNotIn('fallaron', text)
        self.assertEqual(json.loads(self.historico.read_text()), {'2026-10-24': [1, 4], '2026-10-31': [0, 1]})

    def test_solo_cifras_with_only_failures_gives_nothing(self):
        self.escribir([self.entrada(estado='fallo', nota='Error de publicación')])
        self.assertEqual(equipo.calendario_por_medir('beta', solo_cifras=True), '')

    def test_three_lines_truncation_and_limit(self):
        self.escribir([self.entrada(titulo='t' * 90),
                       self.entrada('fallo', estado='fallo', nota='n' * 90)])
        text = equipo.calendario_por_medir('beta')
        self.assertEqual(len(text.splitlines()), 3)
        self.assertIn('«' + 't' * 79 + '…»', text)
        self.assertTrue(text.endswith('n' * 79 + '…'))
        self.assertEqual(equipo.calendario_por_medir('beta', limit=40), text[:40])

    def test_plan_project_includes_calendario_once(self):
        p = {**equipo.project('beta'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'STATE', self.root), mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), \
             mock.patch.object(equipo, 'aperturas_medidas', return_value='APERTURAS DE PRUEBA') as aperturas, \
             mock.patch.object(equipo, 'analisis_contenido', return_value=''), \
             mock.patch.object(equipo, 'calendario_por_medir', return_value='CALENDARIO DE PRUEBA') as calendario, \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('beta', batch, file)
        calendario.assert_called_once_with('beta', solo_cifras=True)
        self.assertEqual(claude.call_args.args[0].count('CALENDARIO DE PRUEBA'), 1)

    def test_project_brief_has_empty_calendario_without_brand(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), \
             mock.patch.object(equipo, 'tasks', lambda pid: []), mock.patch.object(equipo, 'metrics', lambda pid: {}), \
             mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'ficha', lambda pid: None), \
             mock.patch.object(equipo, 'tendencias', lambda pid, limit=0: ''):
            self.assertEqual(equipo.project_brief('prompted-os')['calendario_por_medir'], '')


class SinEnlaceCalendario(unittest.TestCase):
    ahora = datetime.fromisoformat('2026-11-06T07:30:00+01:00')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archivo = self.root / 'contenido' / 'sin-enlace.json'
        for name, value in {'WORKSPACE': self.root, 'STATE': self.root,
                            'now': mock.Mock(return_value=self.ahora)}.items():
            patch = mock.patch.object(equipo, name, value)
            patch.start(); self.addCleanup(patch.stop)

    def entrada(self, cantidad):
        return {'total': cantidad, 'marcas': {'norte-capital': cantidad}}

    def test_line_and_seven_days_preferred_over_eight_and_six(self):
        equipo.save(self.archivo, {'2026-11-06': self.entrada(3), '2026-10-30': self.entrada(5),
                                   '2026-10-29': self.entrada(8), '2026-10-31': self.entrada(6)})
        self.assertEqual(equipo.sin_enlace_calendario('norte'),
                         'Publicaciones de los próximos 7 días que saldrían sin enlace medido '
                         '(sus clics y compras no se podrán atribuir): 3. Hace 7 días: 5.')

    def test_comparison_falls_back_to_eight_then_six(self):
        for dias in (8, 6):
            with self.subTest(dias=dias):
                historico = {'2026-11-06': self.entrada(3), '2026-10-30': {'total': -1, 'marcas': {}},
                             '2026-10-31': self.entrada(6)}
                if dias == 8: historico['2026-10-29'] = self.entrada(8)
                equipo.save(self.archivo, historico)
                self.assertTrue(equipo.sin_enlace_calendario('norte').endswith(f'Hace {dias} días: {dias}.'))

    def test_yesterday_is_usable_and_comparison_is_relative_to_it(self):
        equipo.save(self.archivo, {'2026-11-05': self.entrada(3), '2026-10-29': self.entrada(5),
                                   '2026-11-07': self.entrada(9)})
        self.assertTrue(equipo.sin_enlace_calendario('norte').endswith(': 3. Hace 7 días: 5.'))

    def test_only_day_before_yesterday_is_stale(self):
        equipo.save(self.archivo, {'2026-11-04': self.entrada(3)})
        self.assertEqual(equipo.sin_enlace_calendario('norte'), '')

    def test_tf_and_project_without_brand_return_nothing(self):
        equipo.save(self.archivo, {'2026-11-06': {'total': 5, 'marcas': {'beta': 5}}})
        with mock.patch.object(equipo, 'load') as load:
            for pid in ('beta', 'prompted-os'):
                self.assertEqual(equipo.sin_enlace_calendario(pid), '')
        load.assert_not_called()

    def test_zero_with_previous_positive_count_is_reported(self):
        equipo.save(self.archivo, {'2026-11-06': self.entrada(0), '2026-10-30': self.entrada(2)})
        text = equipo.sin_enlace_calendario('norte')
        self.assertIn(': 0.', text)
        self.assertIn('Hace 7 días: 2.', text)

    def test_missing_brand_counts_as_zero(self):
        equipo.save(self.archivo, {'2026-11-06': {'total': 4, 'marcas': {'otra': 4}},
                                   '2026-10-30': self.entrada(2)})
        self.assertTrue(equipo.sin_enlace_calendario('norte').endswith(': 0. Hace 7 días: 2.'))
        equipo.save(self.archivo, {'2026-11-06': self.entrada(3), '2026-10-30': {'total': 4, 'marcas': {}}})
        self.assertTrue(equipo.sin_enlace_calendario('norte').endswith('Hace 7 días: 0.'))

    def test_zero_without_positive_comparison_is_silent(self):
        for anterior in (None, self.entrada(0), {'total': 3, 'marcas': {}}):
            with self.subTest(anterior=anterior):
                equipo.save(self.archivo, {'2026-11-06': self.entrada(0), '2026-10-30': anterior})
                self.assertEqual(equipo.sin_enlace_calendario('norte'), '')

    def test_invalid_entries_are_ignored(self):
        invalidas = [None, [], {}, {'total': 3, 'marcas': []}]
        invalidas += [{'total': total, 'marcas': {'norte-capital': 3}} for total in (-1, 1.5, '3', True)]
        invalidas += [{'total': 3, 'marcas': {'norte-capital': n}} for n in (-1, 1.5, '3', True, None)]
        for entrada in invalidas:
            with self.subTest(entrada=entrada):
                equipo.save(self.archivo, {'2026-11-06': entrada, '2026-11-05': self.entrada(2),
                                           '2026-10-29': entrada})
                self.assertTrue(equipo.sin_enlace_calendario('norte').endswith(': 2.'))
                self.assertNotIn('Hace ', equipo.sin_enlace_calendario('norte'))

    def test_invalid_dates_and_future_are_ignored(self):
        equipo.save(self.archivo, {fecha: self.entrada(3) for fecha in
                                   ('2026-11-07', '2026-11-6', '2026-02-30', 'sin fecha')})
        self.assertEqual(equipo.sin_enlace_calendario('norte'), '')

    def test_file_not_dict_returns_nothing(self):
        for valor in ([], None, '3', 3):
            with self.subTest(valor=valor):
                equipo.save(self.archivo, valor)
                self.assertEqual(equipo.sin_enlace_calendario('norte'), '')

    def test_plan_project_includes_sin_enlace_once_after_calendario(self):
        p = {**equipo.project('norte'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), mock.patch.object(equipo, 'calendario_pendiente', return_value=''), \
             mock.patch.object(equipo, 'aperturas_medidas', return_value=''), mock.patch.object(equipo, 'analisis_contenido', return_value=''), \
             mock.patch.object(equipo, '_notas_para_rehacer', return_value=[]), \
             mock.patch.object(equipo, 'calendario_por_medir', return_value='CALENDARIO DE PRUEBA'), \
             mock.patch.object(equipo, 'sin_enlace_calendario', return_value='SIN ENLACE DE PRUEBA') as sin_enlace, \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('norte', batch, file)
        sin_enlace.assert_called_once_with('norte')
        prompt = claude.call_args.args[0]
        self.assertEqual(prompt.count('SIN ENLACE DE PRUEBA'), 1)
        self.assertIn('CALENDARIO DE PRUEBA\nSIN ENLACE DE PRUEBA\n', prompt)

    def test_project_brief_has_empty_sin_enlace_without_brand(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), \
             mock.patch.object(equipo, 'tasks', lambda pid: []), mock.patch.object(equipo, 'metrics', lambda pid: {}), \
             mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'ficha', lambda pid: None), \
             mock.patch.object(equipo, 'tendencias', lambda pid, limit=0: ''):
            self.assertEqual(equipo.project_brief('prompted-os')['sin_enlace'], '')


class PlanProjectWiring(unittest.TestCase):
    """plan_project wires drop_duplicates: an empty batch says why, and a kept one carries no motivo."""
    BOARD = [{'id': 't_a', 'title': 'Motor: A', 'status': 'done'}]

    def plan(self, titles):
        proposals = [{'titulo': t, 'archivos': ['equipo/equipo.py'], 'pruebas': ['equipo']} for t in titles]
        answer = 'Propuesta:\n```json\n' + json.dumps(proposals, ensure_ascii=False) + '\n```'
        messages = []
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g', 'tests': ['equipo'], 'repo': '/tmp/x'}), \
             mock.patch.object(equipo, 'git', lambda *a, **kw: 'abc'), \
             mock.patch.object(equipo, 'worktree', lambda *a, **kw: Path(tmp)), \
             mock.patch.object(equipo, 'tasks', lambda pid: self.BOARD), \
             mock.patch.object(equipo, 'claude', lambda *a, **kw: answer), \
             mock.patch.object(equipo, 'validate_task', lambda pid, t, i, root=None: {**t, 'depende_de': t.get('depende_de', [])}), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'planner_signals', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: messages.append(' '.join(str(p) for p in a))):
            file = Path(tmp) / 'proposals' / f'{equipo.today()}.json'
            batch = {'fecha': equipo.today(), 'proyectos': {}}
            entry = equipo.plan_project('prompted-os', batch, file)
            saved = json.loads(file.read_text())
        self.assertEqual(saved['proyectos']['prompted-os'], entry)
        return entry, messages

    def test_everything_repeated_says_why_the_batch_is_empty(self):
        entry, messages = self.plan(['Motor: A'])
        self.assertEqual(entry['estado'], 'sin propuestas')
        self.assertIn('ya estaba en el tablero', entry['motivo'])
        self.assertEqual(len(entry['duplicadas']), 1)
        self.assertTrue(any('lote vacío' in m for m in messages))

    def test_some_repeated_keeps_the_rest_without_motivo(self):
        entry, messages = self.plan(['Motor: A', 'Motor: B'])
        self.assertEqual(entry['estado'], 'pendiente')
        self.assertEqual([t['titulo'] for t in entry['tareas']], ['Motor: B'])
        self.assertNotIn('motivo', entry)
        self.assertEqual(len(entry['duplicadas']), 1)
        self.assertFalse(any('lote vacío' in m for m in messages))

    def test_planner_proposing_nothing_has_no_motivo(self):
        entry, messages = self.plan([])
        self.assertEqual(entry['estado'], 'sin propuestas')
        self.assertNotIn('motivo', entry)
        self.assertNotIn('duplicadas', entry)
        self.assertFalse(any('lote vacío' in m for m in messages))

class Encargos(unittest.TestCase):
    """Alfred's encargo goes the same road as an approved batch: valid spec, ledger hash, team board, notice at the end."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        from contextlib import contextmanager
        @contextmanager
        def fake_board(pid): yield None
        self.created = []
        def create(conn, **kw): self.created.append(kw); return 't_encargo1'
        self.sent = []
        self.patches = [mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'ENCARGOS', root / 'state' / 'encargos.json'),
                        mock.patch.object(equipo, 'board', fake_board), mock.patch.object(equipo.kb, 'create_task', create),
                        mock.patch.object(equipo, 'freno', lambda: None), mock.patch.object(equipo, 'telegram', lambda t, botones=None: self.sent.append(t)),
                        solo_claude()]
        for patch in self.patches: patch.start()

    def tearDown(self):
        for patch in self.patches: patch.stop()
        self.tmp.cleanup()

    def task(self, **kw):
        return {'titulo': 'Lista de espera: evitar el envío doble', 'porque': 'Owner lo pidió por Telegram',
                'detalle': 'El botón se desactiva mientras se envía; prueba que un segundo clic no envía dos veces.',
                'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['tipos'], 'dificultad': 'baja', **kw}

    @mock.patch.object(equipo, 'codex_ready', lambda: False)
    def test_encargo_is_approved_work_for_the_team(self):
        out = equipo.encargar('prompted-os', self.task())
        self.assertEqual((out['tarea'], self.created[0]['priority'], self.created[0]['assignee']), ('t_encargo1', 10, equipo.LANE))
        spec = equipo.spec_of('prompted-os', {'id': 't_encargo1', 'body': self.created[0]['body']})       # passes the ledger check
        self.assertEqual((spec['origen'], spec['depende_de']), ('alfred', []))
        self.assertEqual(out['constructor'], 'claude-sonnet-5-5')
        equipo.avisar_encargo({'id': 't_encargo1'}, 'hecha', 'Integrada en equipo/prompted-os (abc).')
        equipo.avisar_encargo({'id': 'otra'}, 'hecha', 'no es un encargo')
        self.assertEqual(len(self.sent), 1); self.assertIn('evitar el envío doble', self.sent[0])

    @mock.patch.object(equipo, 'codex_ready', lambda: False)
    def test_encargo_with_a_new_test_file_adds_package_json(self):
        real = equipo.project('beta')
        with tempfile.TemporaryDirectory() as repo:
            with mock.patch.object(equipo, 'project', lambda pid: {**real, 'repo': repo}):
                task = self.task(archivos=['lib/x.js', 'test/nuevo.test.mjs'], pruebas=['unitarias'])
                equipo.encargar('beta', task)
                self.assertEqual(json.loads(self.created[0]['body'])['archivos'], ['lib/x.js', 'test/nuevo.test.mjs', 'package.json'])
                (Path(repo) / 'test').mkdir()
                (Path(repo) / 'test' / 'nuevo.test.mjs').write_text('')
                equipo.encargar('beta', task)
                self.assertEqual(json.loads(self.created[1]['body'])['archivos'], ['lib/x.js', 'test/nuevo.test.mjs'])

    def test_bad_encargo_is_refused(self):
        with self.assertRaises(equipo.Stop): equipo.encargar('inventado', self.task())
        with self.assertRaises(equipo.Stop): equipo.encargar('prompted-os', self.task(archivos=['../fuera']))
        with self.assertRaises(equipo.Stop): equipo.encargar('prompted-os', self.task(pruebas=['rm -rf /']))
        self.assertEqual(self.created, [])

class Answered(unittest.TestCase):
    def test_linked_and_legacy_replies_count(self):
        msgs = [{'id': 'M1', 'de': 'control', 'para': ['owner'], 'texto': '¿Aceptas?'},
                {'id': 'M2', 'de': 'owner', 'para': ['control'], 'texto': 'Sobre M1: acepto'},
                {'id': 'M3', 'de': 'product', 'para': ['owner'], 'texto': '¿Y esto?'},
                {'id': 'M4', 'de': 'owner', 'para': ['product'], 'texto': 'sí', 'responde_a': 'M3'},
                {'id': 'M5', 'de': 'business', 'para': ['owner'], 'texto': '¿Precio?'},
                {'id': 'M6', 'de': 'control', 'para': ['owner'], 'texto': 'Sobre M5: no soy Owner'}]
        self.assertEqual(equipo.answered_by_owner(msgs), {'M1', 'M3'})

class Mesa(unittest.TestCase):
    def test_messages_are_validated_and_numbered(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)):
                (Path(tmp) / 'mesa').mkdir()
                a = equipo.post('business', ['studio', 'nadie'], 'Para la afiliación necesitamos una página de condiciones.', 'pregunta', 'beta', origen='mesa')
                b = equipo.post('studio', ['business'], 'La preparo con el tono de la marca.', 'inventado', 'proyecto-falso', responde_a='M1')
                self.assertIsNone(equipo.post('intruso', ['todos'], 'hola'))
                self.assertIsNone(equipo.post('owner', ['todos'], '   '))
                saved = equipo.mesa_load()['mensajes']
        self.assertEqual((a['id'], a['para'], a['proyecto']), ('M1', ['studio'], 'beta'))
        self.assertEqual((b['id'], b['tipo'], b['proyecto'], b['responde_a']), ('M2', 'aviso', 'general', 'M1'))
        self.assertEqual(len(saved), 2)
        self.assertIn('Negocio → Creativo', equipo.transcript(saved))


class Notices(unittest.TestCase):
    def test_telegram_notices_are_one_short_sentence(self):
        self.assertEqual(equipo.first_sentence('Hecho el cambio. Además he revisado tres cosas más que...'), 'Hecho el cambio.')
        long = 'palabra ' * 60
        self.assertTrue(len(equipo.first_sentence(long)) <= 160 and equipo.first_sentence(long).endswith('…'))
        sent = []
        with mock.patch.object(equipo.subprocess, 'run'), mock.patch.object(equipo, 'telegram', sent.append):
            equipo.notify('Norte: ronda 2', 'en marcha, 3 tareas · 1 espera tu OK → /lote')
        self.assertEqual(sent, ['Norte: ronda 2: en marcha, 3 tareas · 1 espera tu OK → /lote'])

class HoyManana(unittest.TestCase):
    """Today's to-do goes out from the team's service when the app is closed, once, with the same file and buttons."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.ws = root / 'ws'; (self.ws).mkdir(); (root / 'state' / 'mesa').mkdir(parents=True)
        (self.ws / 'telegram.json').write_text(json.dumps({'chatId': 42}))
        self.sent = []; self.answer = '- Aprobar el lote de TF\n- Sol 10 min\n- Llamar al gestor'
        def send(text, markup=None): self.sent.append((text, markup)); return 100 + len(self.sent)
        self.patches = [mock.patch.object(equipo, 'WORKSPACE', self.ws), mock.patch.object(equipo, 'TELEGRAM_LINK', self.ws / 'telegram.json'),
                        mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'telegram_send', send),
                        mock.patch.object(equipo, 'app_abierta', lambda: False), mock.patch.object(equipo, 'freno', lambda: None),
                        mock.patch.object(equipo, 'pending_merge', lambda pid: 0), mock.patch.object(equipo, 'VAULT', root / 'vault'),
                        mock.patch.object(equipo, 'claude', lambda prompt, *a, **k: self.answer), mock.patch.object(equipo, 'tablero', lambda: self.board)]
        self.board = []
        for p in self.patches: p.start()
        self.at = equipo.now().replace(hour=10, minute=0, second=0, microsecond=0)

    def tearDown(self):
        for p in self.patches: p.stop()
        self.tmp.cleanup()

    def day(self): return json.loads((self.ws / 'hoy' / f'{self.at.date().isoformat()}.json').read_text())

    def test_sends_once_with_a_button_per_task(self):
        self.assertEqual(equipo.hoy_manana(self.at), ['Aprobar el lote de TF', 'Sol 10 min', 'Llamar al gestor'])
        text, markup = self.sent[0]
        self.assertTrue(text.startswith('Hoy, ') and '○ Sol 10 min' in text)
        self.assertEqual(markup['inline_keyboard'][0][0]['callback_data'], f'h:{self.at.date().isoformat()}:0')
        self.assertEqual((self.day()['mensaje'], len(self.day()['items'])), (101, 3))
        self.assertIsNone(equipo.hoy_manana(self.at)); self.assertEqual(len(self.sent), 1)

    def test_a_reminder_kept_for_today_comes_first_and_only_once(self):
        # 15/11: «recuérdame que mañana…» — kept by hoy.ts in hoy/futuro.json for that day
        (self.ws / 'hoy').mkdir(exist_ok=True); hoy = self.at.date().isoformat()
        (self.ws / 'hoy' / 'futuro.json').write_text(json.dumps([{'fecha': hoy, 't': 'Aceptar usuarios del indicador', 'n': 'En el panel de acceso; 5 min'},
                                                                  {'fecha': '2999-01-01', 't': 'Más adelante'}, {'fecha': hoy, 't': 'Sol 10 min'}]))
        self.assertEqual(equipo.hoy_manana(self.at), ['Aceptar usuarios del indicador', 'Aprobar el lote de TF', 'Sol 10 min', 'Llamar al gestor'])
        self.assertEqual(self.day()['items'][0]['n'], 'En el panel de acceso; 5 min')
        self.assertEqual(json.loads((self.ws / 'hoy' / 'futuro.json').read_text()), [{'fecha': '2999-01-01', 't': 'Más adelante'}])

    def test_not_with_the_app_open_too_early_or_too_late(self):
        with mock.patch.object(equipo, 'app_abierta', lambda: True): self.assertIsNone(equipo.hoy_manana(self.at))
        self.assertIsNone(equipo.hoy_manana(self.at.replace(hour=8)))
        self.assertIsNone(equipo.hoy_manana(self.at.replace(hour=21)))
        self.assertEqual(self.sent, [])

    def test_without_alfred_the_decisions_and_yesterday_come_first(self):
        y = (self.at - equipo.timedelta(days=1)).date().isoformat(); (self.ws / 'hoy').mkdir()
        (self.ws / 'hoy' / f'{y}.json').write_text(json.dumps({'fecha': y, 'items': [{'t': 'Llamar al gestor', 'hecho': False}, {'t': 'Hecha', 'hecho': True}]}))
        self.answer = 'No puedo.'
        self.assertEqual(equipo.hoy_manana(self.at), ['Llamar al gestor'])
        self.assertIn('respaldo', (self.ws / 'hoy' / 'registro.log').read_text())

    def test_due_cards_bring_the_review_invitation(self):
        (self.ws / 'repaso').mkdir(); (self.ws / 'repaso' / 'tarjetas.json').write_text(json.dumps([{'due': '2026-01-01'}, {'due': '2099-01-01'}]))
        equipo.hoy_manana(self.at)
        self.assertEqual(self.sent[1][0], 'Repaso de hoy: 1 tarjeta (2 min).')
        self.assertEqual(self.sent[1][1]['inline_keyboard'][0][0]['callback_data'], 'r:s')

    def test_a_failed_send_leaves_no_file_so_it_retries(self):
        with mock.patch.object(equipo, 'telegram_send', lambda *a, **k: None): self.assertIsNone(equipo.hoy_manana(self.at))
        self.assertFalse((self.ws / 'hoy' / f'{self.at.date().isoformat()}.json').exists())

    def test_decisions_name_experiments_batches_and_real_questions(self):
        self.board = [{'fase': 'lista para lanzar'}, {'fase': 'lista para lanzar · falta fusionar'}, {'fase': 'falta medir'}]
        equipo.save(equipo.STATE / 'proposals' / f'{equipo.today()}.json', {'proyectos': {'norte': {'estado': 'pendiente', 'tareas': [{}, {}]}}})
        equipo.save(equipo.STATE / 'mesa' / f'{equipo.today()}.json', {'mensajes': [
            {'id': 'M1', 'de': 'radar', 'para': ['owner'], 'tipo': 'aviso', 'texto': 'x'},
            {'id': 'M2', 'de': 'product', 'para': ['owner'], 'tipo': 'pregunta', 'texto': '¿Sí?'},
            {'id': 'M3', 'de': 'studio', 'para': ['owner'], 'tipo': 'pregunta', 'texto': '¿Y?'},
            {'id': 'M4', 'de': 'owner', 'para': ['studio'], 'tipo': 'pregunta', 'texto': 'Sí', 'responde_a': 'M3'}]})
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Norte Capital'}):
            self.assertEqual(equipo.hoy_decisiones(), ['lanzar 2 experimentos (/lote)', 'apuntar lo medido de 1 experimento (/lote)',
                                                       'decidir 2 cosas de Norte Capital (/lote)', 'responder 1 pregunta del equipo (/mesa)'])

    def test_one_line_lists_are_split(self):
        self.assertEqual(equipo.hoy_lineas('- Aprobar lote - Sol 10 min | Llamar'), ['Aprobar lote', 'Sol 10 min', 'Llamar'])

    def test_each_task_keeps_its_plain_explanation(self):
        items = equipo.hoy_items('- Pedir afiliación a Apex :: Entra en su web, pega el texto y envíalo; 10 min.\n- Sol 10 min')
        self.assertEqual(items, [{'t': 'Pedir afiliación a Apex', 'n': 'Entra en su web, pega el texto y envíalo; 10 min'}, {'t': 'Sol 10 min', 'n': ''}])
        self.assertIn('Título :: explicación', equipo.hoy_prompt([], None, ['Probar Automate — su parte: el ensayo']))
        self.assertIn('su parte: el ensayo', equipo.hoy_prompt([], None, ['Probar Automate — su parte: el ensayo']))

class ParaRehacer(unittest.TestCase):
    ahora = datetime(2026, 11, 4, 12, 0, tzinfo=equipo.MADRID)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.carpeta = self.root / '01-Projects/Contenido/Para rehacer'
        for name, value in (('VAULT', self.root), ('now', lambda: self.ahora),
                            ('_prefijos', lambda: {'tf': 'beta', 'kc': 'norte-capital'})):
            patch = mock.patch.object(equipo, name, value); patch.start(); self.addCleanup(patch.stop)

    def nota(self, titulo, dias=0, pedido='', recibido=None, resumen='Un resumen'):
        self.carpeta.mkdir(parents=True, exist_ok=True)
        fecha = recibido if recibido is not None else f'{self.ahora - timedelta(days=dias):%Y-%m-%d %H:%M}'
        path = self.carpeta / f'{titulo}.md'
        path.write_text('\n'.join([
            '---', 'autor: Cuenta', f'recibido: {fecha} (WhatsApp, grupo Alfred)',
            *([f'pedido: "{pedido}"'] if pedido else []), '---', f'# {titulo} — Cuenta', '',
            *([f'> {resumen}', ''] if resumen else []), '## Transcripción (Whisper en este Mac)',
            '> Esto es transcripción, no resumen', '']))
        return path

    def test_requests_filter_brands_and_keep_shared_notes(self):
        self.nota('Para Norte', pedido='para Norte')
        self.nota('Para TF', pedido='idea para TF')
        self.nota('Compartido')
        norte = equipo.para_rehacer('norte')
        tf = equipo.para_rehacer('beta')
        self.assertIn('«Para Norte — Cuenta» (04/11): Un resumen. Pide: «para Norte».', norte)
        self.assertNotIn('Para Norte — Cuenta', tf)
        self.assertIn('«Para TF — Cuenta» (04/11): Un resumen. Pide: «idea para TF».', tf)
        self.assertNotIn('Para TF — Cuenta', norte)
        for texto in (norte, tf):
            self.assertIn('«Compartido — Cuenta» (04/11): Un resumen.', texto)
            self.assertEqual(texto.count('Pide:'), 1)
            self.assertTrue(texto.splitlines()[0].endswith(': 2.'))

    def test_brand_aliases_and_unmentioned_brands(self):
        for pedido in ('para KC', 'Norte Capital', 'norte-capital', 'NORTECAPITAL'):
            with self.subTest(pedido=pedido):
                path = self.nota('Alias', pedido=pedido)
                self.assertIn('Alias — Cuenta', equipo.para_rehacer('norte'))
                self.assertEqual(equipo.para_rehacer('beta'), '')
                path.unlink()
        self.nota('General', pedido='idea efectiva para todos')
        self.nota('Ambos', pedido='para Norte y TF')
        for pid in ('norte', 'beta'):
            self.assertIn('General — Cuenta', equipo.para_rehacer(pid))
            self.assertIn('Ambos — Cuenta', equipo.para_rehacer(pid))

    def test_old_invalid_missing_and_future_dates_are_ignored(self):
        self.nota('Antiguo', dias=15)
        self.nota('Invalido', recibido='2026-02-30 12:00')
        self.nota('Sin fecha', recibido='sin fecha')
        path = self.nota('Sin recibido')
        path.write_text('---\nautor: Cuenta\n---\n# Sin recibido\n')
        self.nota('Futuro', dias=-1)
        self.assertEqual(equipo.para_rehacer('norte'), '')
        self.nota('Limite', dias=14)
        self.assertIn('Limite — Cuenta', equipo.para_rehacer('norte'))

    def test_three_newest_notes_and_remaining_count(self):
        for titulo, dias in (('Antigua', 3), ('Segunda', 1), ('Reciente', 0), ('Tercera', 2)):
            self.nota(titulo, dias=dias)
        lineas = equipo.para_rehacer('norte').splitlines()
        self.assertTrue(lineas[0].endswith(': 4.'))
        for linea, titulo in zip(lineas[1:4], ('Reciente', 'Segunda', 'Tercera')):
            self.assertTrue(linea.startswith(f'«{titulo} — Cuenta»'))
        self.assertEqual(lineas[4], 'y 1 más')
        self.assertNotIn('Antigua', '\n'.join(lineas))

    def test_empty_without_brand_or_folder(self):
        self.assertEqual(equipo.para_rehacer('norte'), '')
        self.nota('Referencia')
        self.assertEqual(equipo.para_rehacer('prompted-os'), '')

    def test_limit_and_title_truncation_without_summary(self):
        self.nota('x' * 90, resumen='')
        texto = equipo.para_rehacer('norte')
        self.assertIn('«' + 'x' * 79 + '…» (04/11).', texto)
        self.assertNotIn('transcripción', texto)
        self.assertNotIn('Pide:', texto)
        self.assertEqual(equipo.para_rehacer('norte', limit=40), texto[:40])

    def test_unreadable_note_is_ignored(self):
        self.nota('No legible')
        with mock.patch.object(Path, 'read_text', side_effect=OSError('no se puede leer')):
            self.assertEqual(equipo.para_rehacer('norte'), '')

    def test_plan_project_includes_para_rehacer_once(self):
        p = {**equipo.project('beta'), 'related_boards': []}
        file = self.root / 'proposals' / f'{equipo.today()}.json'
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'STATE', self.root), mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), mock.patch.object(equipo, 'aperturas_medidas', return_value=''), \
             mock.patch.object(equipo, 'calendario_pendiente', return_value=''), mock.patch.object(equipo, 'calendario_por_medir', return_value=''), \
             mock.patch.object(equipo, 'analisis_contenido', return_value=''), \
             mock.patch.object(equipo, '_notas_para_rehacer', return_value=[(self.ahora, 'Título conocido', 'REFS DE PRUEBA')]) as referencias, \
             mock.patch.object(equipo, 'claude', return_value='```json\n[]\n```') as claude:
            equipo.plan_project('beta', batch, file)
        referencias.assert_called_once_with('beta')
        self.assertEqual(claude.call_args.args[0].count('REFS DE PRUEBA'), 1)

    def test_project_brief_has_empty_para_rehacer_without_brand(self):
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), \
             mock.patch.object(equipo, 'tasks', lambda pid: []), mock.patch.object(equipo, 'metrics', lambda pid: {}), \
             mock.patch.object(equipo, 'lessons', lambda pid: []), mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'experimentos', lambda pid: {}), mock.patch.object(equipo, 'ficha', lambda pid: None), \
             mock.patch.object(equipo, 'tendencias', lambda pid, limit=0: ''):
            self.assertEqual(equipo.project_brief('prompted-os')['para_rehacer'], '')


class CitasParaRehacer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ahora = datetime(2026, 11, 4, 12, tzinfo=equipo.MADRID)
        self.file = self.root / 'para_rehacer_citas' / 'norte.json'
        for name, value in (('STATE', self.root), ('now', lambda: self.ahora)):
            patch = mock.patch.object(equipo, name, value); patch.start(); self.addCleanup(patch.stop)

    def plan(self, notas, items):
        p = {**equipo.project('norte'), 'related_boards': []}
        batch = {'fecha': equipo.today(), 'proyectos': {}}
        with mock.patch.object(equipo, 'project', return_value=p), \
             mock.patch.object(equipo, 'git', return_value='abc'), mock.patch.object(equipo, 'worktree', return_value=self.root), \
             mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
             mock.patch.object(equipo, 'context_text', return_value=''), mock.patch.object(equipo, 'planner_signals', return_value=''), \
             mock.patch.object(equipo, 'estrategia', return_value=''), mock.patch.object(equipo, 'tendencias', return_value=''), \
             mock.patch.object(equipo, 'contenido_medido', return_value=''), mock.patch.object(equipo, 'aperturas_medidas', return_value=''), \
             mock.patch.object(equipo, 'calendario_pendiente', return_value=''), mock.patch.object(equipo, 'calendario_por_medir', return_value=''), \
             mock.patch.object(equipo, 'analisis_contenido', return_value=''), \
             mock.patch.object(equipo, '_notas_para_rehacer', return_value=notas) as referencias, \
             mock.patch.object(equipo, 'validate_task', side_effect=lambda pid, t, i, root: t), \
             mock.patch.object(equipo, 'drop_duplicates', side_effect=lambda items, tasks: (items, [])), \
             mock.patch.object(equipo, 'claude', return_value=json.dumps(items)) as claude:
            entry = equipo.plan_project('norte', batch, self.root / 'proposals' / 'lote.json', ronda=3)
        referencias.assert_called_once_with('norte')
        return entry, claude.call_args.args[0]

    def test_plan_counts_proposals_citing_a_reference(self):
        titulo = 'Una referencia conocida'
        entry, prompt = self.plan([(self.ahora, titulo, f'«{titulo}».')], [
            {'titulo': 'Primera propuesta', 'detalle': f'Rehacer {titulo.upper()}'},
            {'titulo': 'Segunda propuesta', 'detalle': 'Otro contenido'}])
        self.assertEqual(entry['estado'], 'pendiente')
        self.assertEqual(prompt.count(titulo), 1)
        self.assertEqual(equipo.load(self.file, []), [
            {'fecha': '2026-11-04', 'ronda': 3, 'propuestas': 2, 'citan': 1}])

    def test_plan_without_notes_does_not_write_counts(self):
        self.plan([], [{'titulo': 'Una propuesta', 'detalle': 'Contenido'}])
        self.assertFalse(self.file.exists())

    def test_plan_without_items_does_not_write_counts(self):
        self.plan([(self.ahora, 'Referencia conocida', 'Una referencia.')], [])
        self.assertFalse(self.file.exists())

    def test_counts_only_three_titles_and_ignores_short_titles_and_ellipsis(self):
        notas = [(self.ahora, titulo, titulo) for titulo in
                 ('Referencia truncada…', 'Corto', 'Tercera referencia', 'Cuarta referencia')]
        self.plan(notas, [
            {'titulo': 'REFERENCIA TRUNCADA y tercera referencia', 'porque': 'Tercera referencia'},
            {'porque': 'tercera referencia'}, {'detalle': 'Corto y Cuarta referencia'}])
        self.assertEqual(equipo.load(self.file, [])[-1]['citan'], 2)

    def test_counts_keep_last_sixty_batches(self):
        equipo.save(self.file, [{'fecha': '2026-11-03', 'ronda': i, 'propuestas': 1, 'citan': 0} for i in range(60)])
        self.plan([(self.ahora, 'Referencia conocida', 'Una referencia.')], [{'titulo': 'Una propuesta'}])
        entradas = equipo.load(self.file, [])
        self.assertEqual(len(entradas), 60)
        self.assertEqual(entradas[0]['ronda'], 1)
        self.assertEqual(entradas[-1]['fecha'], '2026-11-04')

    def test_failed_counts_write_does_not_interrupt_plan(self):
        save = equipo.save
        def guardar(path, value):
            if path == self.file: raise OSError('sin espacio')
            return save(path, value)
        with mock.patch.object(equipo, 'save', side_effect=guardar):
            entry, _ = self.plan([(self.ahora, 'Referencia conocida', 'Una referencia.')], [{'titulo': 'Una propuesta'}])
        self.assertEqual(entry['estado'], 'pendiente')
        self.assertFalse(self.file.exists())

    def test_recent_counts_ignore_old_and_broken_entries(self):
        entrada = {'fecha': '2026-11-04', 'ronda': 1, 'propuestas': 2, 'citan': 1}
        equipo.save(self.file, [entrada, {**entrada, 'fecha': '2026-10-27'}, None, 'rota', {},
                                {**entrada, 'fecha': '2026-02-30'}, {**entrada, 'fecha': '2026-11-05'},
                                {**entrada, 'propuestas': '2'}, {**entrada, 'citan': True}])
        self.assertEqual(equipo.citas_para_rehacer('norte'), {'lotes': 1, 'propuestas': 2, 'citan': 1})
        self.assertEqual(equipo.citas_para_rehacer('norte', days=8), {'lotes': 2, 'propuestas': 4, 'citan': 2})

    def test_planner_signals_includes_exact_counts_line(self):
        equipo.save(self.file, [{'fecha': '2026-11-04', 'ronda': 1, 'propuestas': 2, 'citan': 1}])
        with mock.patch.object(equipo, 'lessons_text', return_value=''), \
             mock.patch.object(equipo, 'receipts', return_value=[]), mock.patch.object(equipo, 'mesa_load', return_value={}), \
             mock.patch.object(equipo, 'resultados', return_value=[]), \
             mock.patch.object(equipo, 'experimentos', return_value={'avisados': 0, 'sin_medir': 0, 'recordados': 0}), \
             mock.patch.object(equipo, 'metrics', return_value={'tareas': 0}):
            texto = equipo.planner_signals('norte')
        self.assertIn('Propuestas con reels de «Para rehacer» delante (7 días): 1 de 2 citan uno, en 1 lotes.'
                      ' Con menos de 5 lotes no es señal.', texto)


class AnalisisContenido(unittest.TestCase):
    """The daily analysis of our own reels reaches the planner of its brand, and only while it is fresh."""
    def analizar(self, marca):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'telegram.json'; (Path(tmp) / 'contenido' / 'analisis').mkdir(parents=True)
            (Path(tmp) / 'contenido' / 'analisis' / 'ultimo.json').write_text(json.dumps({
                'fecha': '2026-10-30', 'marcas': {'norte-capital': marca}}))
            with mock.patch.object(equipo, 'TELEGRAM_LINK', link), mock.patch.dict(equipo.CONFIG, {'projects': {'norte': {'marca': 'norte-capital'}}}):
                return equipo.analisis_contenido('norte', hoy='2026-10-31')

    def test_winners_and_losers_include_views_and_account_median(self):
        texto = self.analizar({'ganadores': [
            {'titulo': 'Reel A', 'red': 'instagram', 'vistas': 1200, 'x_mediana': 2.4},
            {'titulo': 'Reel B', 'red': 'tiktok', 'vistas': 1500, 'x_mediana': 3.0}],
            'perdedores': [{'titulo': 'Reel C', 'red': '', 'vistas': 80, 'x_mediana': 0.3}]})
        self.assertIn('Ganan frente a la mediana de su cuenta: «Reel A» (instagram): 1200 vistas, ×2,4; «Reel B» (tiktok): 1500 vistas, ×3.', texto)
        self.assertIn('Pierden frente a la mediana: «Reel C» (sin red): 80 vistas, ×0,3.', texto)

    def test_only_the_first_three_valid_reels_in_file_order_are_shown(self):
        for campo in ('ganadores', 'perdedores'):
            with self.subTest(campo=campo):
                texto = self.analizar({campo: [None] + [
                    {'titulo': f'Reel {i}', 'vistas': 100, 'x_mediana': 2} for i in range(5)]})
                for i in range(3): self.assertIn(f'«Reel {i}»', texto)
                for i in range(3, 5): self.assertNotIn(f'«Reel {i}»', texto)
                self.assertLess(texto.index('«Reel 0»'), texto.index('«Reel 1»'))
                self.assertLess(texto.index('«Reel 1»'), texto.index('«Reel 2»'))

    def test_invalid_reels_are_skipped_without_an_empty_heading(self):
        texto = self.analizar({'resumen': 'Resumen', 'ganadores': [
            {'titulo': 'Mucho', 'vistas': 100, 'x_mediana': 'mucho'},
            {'titulo': 'Booleano', 'vistas': True, 'x_mediana': 2}, 'No es un dict']})
        for fragmento in ('Mucho', 'Booleano', 'No es un dict', 'Ganan frente a la mediana'):
            self.assertNotIn(fragmento, texto)
        for campo in ('vistas', 'x_mediana'):
            for valor in (True, None, '100', -1, float('nan'), float('inf'), float('-inf')):
                with self.subTest(campo=campo, valor=valor):
                    reel = {'titulo': 'Inválido', 'vistas': 100, 'x_mediana': 2, campo: valor}
                    self.assertEqual(self.analizar({'ganadores': [reel], 'perdedores': [reel]}), '')

    def test_long_conclusions_do_not_push_winners_out_of_the_limit(self):
        texto = self.analizar({'resumen': 'Resumen', 'ganadores': [
            {'titulo': 'Reel A', 'red': 'instagram', 'vistas': 1200, 'x_mediana': 2.4}],
            'conclusiones': ['C' * 1600]})
        self.assertIn('Ganan frente a la mediana de su cuenta: «Reel A»', texto)
        self.assertLessEqual(len(texto), 1500)

    def test_titles_are_shortened_to_eighty_characters(self):
        texto = self.analizar({'ganadores': [
            {'titulo': 'A' * 81, 'vistas': 0.0, 'x_mediana': 0},
            {'titulo': 'B' * 80, 'vistas': 100, 'x_mediana': 2.46}]})
        self.assertIn('«' + 'A' * 79 + '…» (sin red): 0.0 vistas, ×0', texto)
        self.assertNotIn('A' * 80, texto)
        self.assertIn('«' + 'B' * 80 + '» (sin red): 100 vistas, ×2,5', texto)

    def test_fresh_analysis_of_the_brand_reaches_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / 'telegram.json'; (Path(tmp) / 'contenido' / 'analisis').mkdir(parents=True)
            (Path(tmp) / 'contenido' / 'analisis' / 'ultimo.json').write_text(json.dumps({'fecha': '2026-10-30', 'marcas': {'norte-capital': {
                'resumen': 'Gana el initial balance.', 'conclusiones': ['IB: 3 de 3 variantes por encima de 2×'], 'evitar': ['subir repetidos'],
                'exprimir': {'concepto': 'Initial balance', 'por_que': '3× la mediana', 'variantes': [{'gancho': '¿Sabes dónde acaba el día?', 'angulo': 'pregunta'}]},
                'validados': ['Initial balance']}}}))
            projects = {'norte': {'marca': 'norte-capital'}, 'otro': {}}
            with mock.patch.object(equipo, 'TELEGRAM_LINK', link), mock.patch.dict(equipo.CONFIG, {'projects': projects}):
                texto = equipo.analisis_contenido('norte', hoy='2026-10-31')
                self.assertIn('Exprimir con variantes en Trial Reels: Initial balance (3× la mediana)', texto)
                self.assertIn('IB: 3 de 3 variantes', texto); self.assertIn('Evitar: subir repetidos', texto)
                self.assertIn('ya validados', texto)
                self.assertEqual(equipo.analisis_contenido('otro', hoy='2026-10-31'), '')
                self.assertEqual(equipo.analisis_contenido('norte', hoy='2026-11-05'), '')     # older than three days

class Coach(unittest.TestCase):
    """One line at night, two on Sunday, and review cards from the week's work — all from Owner's own files."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); root = Path(self.tmp.name)
        self.ws = root / 'ws'; (self.ws / 'hoy').mkdir(parents=True); (root / 'state' / 'mesa').mkdir(parents=True); self.vault = root / 'vault'; self.vault.mkdir()
        self.sent = []; self.cards = '[{"tema": "Trading", "pregunta": "¿Qué es el drawdown máximo?", "respuesta": "La mayor caída desde un pico."}]'
        self.p = [mock.patch.object(equipo, 'WORKSPACE', self.ws), mock.patch.object(equipo, 'STATE', root / 'state'), mock.patch.object(equipo, 'VAULT', self.vault),
                  mock.patch.object(equipo, 'telegram', self.sent.append), mock.patch.object(equipo, 'freno', lambda: None),
                  mock.patch.object(equipo, 'claude', lambda prompt, *a, **k: '```json\n' + self.cards + '\n```'), mock.patch.object(equipo, 'lessons', lambda pid: [])]
        for x in self.p: x.start()
        self.sunday = equipo.now().replace(year=2026, month=10, day=18, hour=21, minute=45, second=0, microsecond=0)

    def tearDown(self):
        for x in self.p: x.stop()
        self.tmp.cleanup()

    def day(self, fecha, items, **kw): equipo.save(self.ws / 'hoy' / f'{fecha}.json', {'fecha': fecha, 'items': items, 'mensaje': 1, **kw})

    def test_the_day_closes_once_with_what_comes_tomorrow(self):
        self.day('2026-10-18', [{'t': 'Aprobar lote', 'hecho': True}, {'t': 'Llamar al gestor', 'hecho': False}])
        self.assertIsNone(equipo.cierre_dia(self.sunday.replace(hour=20)))
        self.assertEqual(equipo.cierre_dia(self.sunday), 'Hoy: 1/2 hecho. Mañana sigo con: Llamar al gestor.')
        self.assertIsNone(equipo.cierre_dia(self.sunday)); self.assertEqual(len(self.sent), 1)

    def test_the_week_counts_tasks_review_and_what_keeps_slipping(self):
        for n in range(3): self.day(f'2026-10-{18 - n}', [{'t': 'Llamar al gestor', 'hecho': False}, {'t': f'Hecha {n}', 'hecho': True}])
        (self.ws / 'repaso').mkdir(); (self.ws / 'repaso' / 'registro.jsonl').write_text('\n'.join(json.dumps({'fecha': '2026-10-17', 'id': 'x', 'g': g}) for g in (2, 2, 0, 1)))
        self.assertEqual(equipo.cierre_semana(self.sunday), 'Semana: 3/6 tareas · repaso 4 (50 % a la primera).\nSe arrastra: «Llamar al gestor» (3 días). ¿La hacemos o la quitamos?')
        self.assertIsNone(equipo.cierre_semana(self.sunday)); self.assertIsNone(equipo.cierre_semana(self.sunday.replace(day=17)))

    def test_weekly_cards_from_recent_notes_never_from_administracion(self):
        (self.vault / 'Administración').mkdir(); (self.vault / 'Administración' / 'iva.md').write_text('IBAN secreto')
        (self.vault / 'Trading.md').write_text('El drawdown máximo es la mayor caída desde un pico.')
        seen = []
        with mock.patch.object(equipo, 'claude', lambda prompt, *a, **k: seen.append(prompt) or '```json\n' + self.cards + '\n```'):
            self.assertEqual(equipo.repaso_semanal(self.sunday.replace(day=15)), 1)
        self.assertIn('Trading.md', seen[0]); self.assertNotIn('IBAN', seen[0])
        cards = json.loads((self.ws / 'repaso' / 'tarjetas.json').read_text())
        self.assertEqual((cards[0]['tema'], cards[0]['due'], cards[0]['reps']), ('Trading', '2026-10-15', 0))
        self.assertIsNone(equipo.repaso_semanal(self.sunday.replace(day=16)))      # not again until Sunday
        self.assertIsNone(equipo.repaso_semanal(self.sunday))                        # the same week, even on Sunday
        self.assertEqual(equipo.repaso_semanal(self.sunday.replace(day=25)), 0)      # next Sunday: the same card is not repeated

class Decisiones(unittest.TestCase):
    """Owner decides the important tasks of a batch one at a time, and the mesa reaches him with few, clear questions."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.state = Path(self.tmp.name); (self.state / 'proposals').mkdir()
        self.calls = []
        self.p = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, '_approve', lambda pids, date=None, only=None, hold=None, by='Owner': self.calls.append((pids, only, sorted(hold or []))))]
        for x in self.p: x.start()
        equipo.save(self.state / 'proposals' / f'{equipo.today()}.json', {'proyectos': {'tf': {'estado': 'pendiente', 'indices': [3, 5, 6],
            'tareas': [{'titulo': 'Un reel'}, {'titulo': 'Afiliación'}, {'titulo': 'Mensajes'}]}}})

    def tearDown(self):
        for x in self.p: x.stop()
        self.tmp.cleanup()

    def test_yes_approves_only_that_task_and_keeps_the_rest_waiting(self):
        self.assertEqual(equipo.decidir_lote('tf', 5, 'si')['resultado'], 'aprobada')
        self.assertEqual(self.calls, [(['tf'], [1], [0, 2])])

    def test_no_discards_only_that_task_and_later_changes_nothing(self):
        equipo.decidir_lote('tf', 6, 'no'); equipo.decidir_lote('tf', 3, 'luego')
        self.assertEqual(self.calls, [(['tf'], [], [0, 1])])

    def test_unknown_or_decided_tasks_and_bad_answers(self):
        self.assertEqual(equipo.decidir_lote('tf', 9, 'si')['resultado'], 'esa tarea ya está decidida')
        with self.assertRaises(equipo.Stop): equipo.decidir_lote('tf', 3, 'quizás')

    def test_the_command_line_takes_both_kinds_of_decision(self):
        a = equipo.parser().parse_args(['decidir', 'tf', '--indice', '5', '--decision', 'si'])
        self.assertEqual((a.project, a.indice, a.decision), ('tf', 5, 'si'))
        a = equipo.parser().parse_args(['decidir', 't_0000abcd', '--decision', 'seguir'])
        self.assertEqual((a.project, a.indice, a.decision), ('t_0000abcd', None, 'seguir'))

    def test_in_the_mesa_nothing_goes_straight_to_owner(self):
        self.assertEqual(equipo.para_en_mesa('radar', ['owner']), ['alfred'])
        self.assertEqual(equipo.para_en_mesa('product', ['owner', 'control']), ['control'])
        self.assertEqual(equipo.para_en_mesa('alfred', ['owner']), ['todos'])
        self.assertEqual(equipo.para_en_mesa('studio', ['business']), ['business'])

    def test_questions_go_one_per_message_with_buttons(self):
        sent = []
        with mock.patch.object(equipo, 'telegram_send', lambda text, markup=None: sent.append((text, markup))):
            equipo.mandar_preguntas([{'id': f'M{i}', 'de': 'product', 'texto': f'¿Lanzo {i}?'} for i in range(5)])
        self.assertEqual(len(sent), equipo.PREGUNTAS_MAX)
        self.assertTrue(sent[0][0].endswith('te pregunta: ¿Lanzo 0?'))
        self.assertEqual([b['callback_data'] for row in sent[0][1]['inline_keyboard'] for b in row], ['q:M0:s', 'q:M0:n', 'q:M0:l', 'q:M0:r'])

class ParaTi(unittest.TestCase):
    """What waits for Owner is explained plainly and grouped; nothing can be lost on the way and Claude is asked only when it changes."""
    ITEMS = [{'clave': 'l:t_00000001', 'tipo': 'lanzar', 'proyecto': 'tf', 'titulo': 'Docs 28', 'ficha': {'que': 'Un reel'}},
             {'clave': 'l:t_00000002', 'tipo': 'lanzar', 'proyecto': 'tf', 'titulo': 'Docs 29', 'ficha': {'que': 'Otro reel'}},
             {'clave': 'd:tf:3', 'tipo': 'decidir', 'proyecto': 'tf', 'titulo': 'Afiliación', 'motivo': 'Cambia el precio'},
             {'clave': 'q:M7', 'tipo': 'pregunta', 'de': 'control', 'texto': '¿Aceptas X?'}]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.state = Path(self.tmp.name)
        self.p = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'freno', lambda: None),
                  mock.patch.object(equipo, 'pendientes_tuyas', lambda tb=None: self.items)]
        for x in self.p: x.start()
        self.items = list(self.ITEMS)

    def tearDown(self):
        for x in self.p: x.stop()
        self.tmp.cleanup()

    def test_unknown_keys_mixed_types_and_leftovers(self):
        raw = [{'claves': ['l:t_00000001', 'l:t_00000002', 'd:tf:3', 'x:inventada'], 'tipo': 'lanzar', 'titulo': 'Publicar 2 reels', 'que_es': 'Dos vídeos'},
               {'claves': ['l:t_00000001'], 'titulo': 'Repetida'}, 'basura']
        g = equipo.grupos_validos(raw, self.items)
        self.assertEqual(g[0]['claves'], ['l:t_00000001', 'l:t_00000002']); self.assertEqual(g[0]['titulo'], 'Publicar 2 reels')
        self.assertEqual(g[0]['tu_parte'], equipo.TU_PARTE['lanzar'])
        self.assertEqual(sorted(c for x in g for c in x['claves']), sorted(i['clave'] for i in self.items))
        sola = next(x for x in g if x['claves'] == ['d:tf:3'])
        self.assertEqual((sola['tipo'], sola['que_es']), ('decidir', 'Cambia el precio'))
        self.assertEqual(equipo.grupos_validos(None, self.items)[3]['titulo'], '¿Aceptas X?')

    def test_claude_is_asked_only_when_what_waits_changes(self):
        calls = []
        def fake(prompt, *a, **k):
            calls.append(prompt); return '```json\n[{"claves": ["l:t_00000001", "l:t_00000002"], "tipo": "lanzar", "titulo": "Publicar 2 reels"}]\n```'
        with mock.patch.object(equipo, 'claude', fake):
            first = equipo.para_ti(); equipo.para_ti()
            self.assertEqual(len(calls), 1); self.assertEqual(first[0]['titulo'], 'Publicar 2 reels')
            self.assertNotIn('Docs 28', ' '.join(x['titulo'] for x in first))
            self.items = self.items[:2]; equipo.para_ti()
            self.assertEqual(len(calls), 2)

    def test_status_keeps_only_what_still_waits(self):
        equipo.save(self.state / 'para_ti.json', {'clave': 'x', 'grupos': equipo.grupos_validos(None, self.items)})
        self.items = self.items[1:]
        self.assertNotIn('l:t_00000001', [c for g in equipo.para_ti_vigente() for c in g['claves']])

    def test_something_new_shows_up_before_it_is_explained(self):
        equipo.save(self.state / 'para_ti.json', {'clave': 'x', 'grupos': equipo.grupos_validos(None, self.items[:2])})
        claves = [c for g in equipo.para_ti_vigente() for c in g['claves']]
        self.assertEqual(sorted(claves), sorted(i['clave'] for i in self.items))

    def test_todo_uses_the_plain_titles(self):
        equipo.save(self.state / 'para_ti.json', {'clave': 'x', 'grupos': equipo.grupos_validos(
            [{'claves': ['l:t_00000001', 'l:t_00000002'], 'titulo': 'Publicar 2 reels de TF'}], self.items)})
        with mock.patch.object(equipo, 'pending_merge', lambda pid: 0):
            out = equipo.hoy_decisiones()
        self.assertEqual(out[0], 'publicar 2 reels de TF (/lote)'); self.assertIn('responder 1 pregunta del equipo (/mesa)', out)
        self.assertFalse(any('Docs' in x for x in out))


class Mejoras(unittest.TestCase):
    """What is waiting to be merged is described in plain words, regenerated only when the pending commits change."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.state = Path(self.tmp.name)
        self.commits = {'norte': [('abc1234', 'equipo: Ficha de reels KC-R3')]}; self.calls = []
        def fake(prompt, *a, **k):
            self.calls.append(prompt); return '```json\n{"norte": ["Un reel listo para publicar hoy"]}\n```'
        self.p = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'freno', lambda: None),
                  mock.patch.object(equipo, 'por_juntar', lambda pid: self.commits.get(pid, [])), mock.patch.object(equipo, 'claude', fake)]
        for x in self.p: x.start()

    def tearDown(self):
        for x in self.p: x.stop()
        self.tmp.cleanup()

    def test_described_once_and_again_only_when_new_work_arrives(self):
        self.assertEqual(equipo.mejoras(), {'norte': ['Un reel listo para publicar hoy']}); equipo.mejoras()
        self.assertEqual(len(self.calls), 1); self.assertNotIn('equipo: Ficha', self.calls[0])
        self.assertEqual(equipo.mejoras_vigentes('norte'), ['Un reel listo para publicar hoy'])
        self.commits['norte'].append(('def5678', 'equipo: Contador'))
        self.assertEqual(equipo.mejoras_vigentes('norte'), [])
        equipo.mejoras(); self.assertEqual(len(self.calls), 2)

    def test_the_nightly_merge_says_what_went_in(self):
        equipo.mejoras(); sent = []
        with mock.patch.object(equipo, 'fusionar_project', lambda pid: 'fusionada en main' if pid == 'norte' else 'nada que fusionar'), \
             mock.patch.object(equipo, 'notify', lambda t, x: sent.append(x)), mock.patch.object(equipo, 'log', lambda *a: None):
            equipo.fusionar(list(equipo.CONFIG['projects']))
        self.assertIn('fusionada en main\n• Un reel listo para publicar hoy', sent[0])


class Tablero(unittest.TestCase):
    def test_launch_card_carries_latest_parent_result(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        texto = 'Se registraron 12 personas. ' * 10
        def task(tid, spec):
            return {'id': tid, 'title': tid, 'status': 'ready', 'created_at': 1790000000, 'body': json.dumps(spec)}
        def fake_tasks(pid):
            return [task('t_hija', {'lanzamiento': ficha, 'viene_de': 't_padre'}),
                    task('t_sin', {'lanzamiento': ficha}),
                    task('t_invalida', {'lanzamiento': ficha, 'viene_de': '../padre'}),
                    task('t_sin_ficha', {'viene_de': 't_padre'}),
                    task('t_registro', {'lanzamiento': ficha, 'viene_de': 't_anterior'}),
                    task('t_id', {'lanzamiento': ficha, 'viene_de': 't_desconocida'})] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            equipo.save(Path(tmp) / 'experimentos' / 'prompted-os.json', [
                {'tarea': 't_padre', 'titulo': 'Título anterior', 'texto': 'Resultado anterior', 'decision': 'seguir'},
                {'tarea': 't_padre', 'titulo': 'Experimento padre', 'texto': texto, 'decision': 'seguir'}])
            equipo.save(Path(tmp) / 'experimentos.json', {
                't_padre': {'titulo': 'Título del registro'}, 't_anterior': {'titulo': 'Experimento anterior'}})
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_hija']['viene_de'], {'tarea': 't_padre', 'titulo': 'Experimento padre', 'resultado': texto[:200]})
        self.assertNotIn('viene_de', items['t_sin'])
        self.assertNotIn('viene_de', items['t_invalida'])
        self.assertNotIn('viene_de', items['t_sin_ficha'])
        self.assertEqual(items['t_registro']['viene_de'], {'tarea': 't_anterior', 'titulo': 'Experimento anterior'})
        self.assertEqual(items['t_id']['viene_de'], {'tarea': 't_desconocida', 'titulo': 't_desconocida'})

    def test_team_tasks_say_whether_alfred_ordered_them(self):
        def task(tid, spec):
            return {'id': tid, 'title': tid, 'status': 'ready', 'created_at': 1790000000, 'body': json.dumps(spec)}
        def fake_tasks(pid):
            return [task('t_alfred', {'porque': 'Lo pidió Owner', 'origen': 'alfred'}), task('t_lote', {'porque': 'Del lote'})] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_alfred']['origen'], 'alfred')
        self.assertEqual(items['t_lote']['origen'], 'lote')

    def test_team_task_with_malformed_receipt_tests_keeps_the_board_visible(self):
        def fake_tasks(pid):
            return [{'id': 't_bloqueada', 'title': 't_bloqueada', 'status': 'blocked', 'created_at': 1790000000, 'body': '{}'}] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            equipo.save(Path(tmp) / 'runs' / 't_bloqueada' / 'receipt.json', {
                'ciclos': [{'pruebas': [{'ok': False}, 'basura', {'prueba': 'tipos', 'ok': True}]}]})
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_bloqueada']['pruebas'], [
            {'prueba': 'prueba sin nombre', 'ok': False}, {'prueba': 'tipos', 'ok': True}])

    def test_team_tasks_carry_their_launch_card_in_any_column(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        def task(tid, status, spec):
            return {'id': tid, 'title': tid, 'status': status, 'created_at': 1790000000, 'body': json.dumps(spec)}
        def fake_tasks(pid):
            return [task('t_ficha', 'ready', {'lanzamiento': ficha}), task('t_sin', 'ready', {'porque': 'Sin ficha'}),
                    task('t_bloqueada', 'blocked', {'lanzamiento': ficha})] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_ficha']['lanzamiento'], ficha)
        self.assertNotIn('lanzamiento', items['t_sin'])
        self.assertEqual((items['t_bloqueada']['columna'], items['t_bloqueada']['lanzamiento']), ('bloqueado', ficha))

    def test_done_task_with_launch_card_waits_for_owner_until_launched(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        def task(tid, status, spec):
            return {'id': tid, 'title': tid, 'status': status, 'created_at': 1790000000, 'completed_at': equipo.time.time(), 'body': json.dumps(spec)}
        def fake_tasks(pid):
            return [task('t_lista', 'done', {'lanzamiento': ficha}), task('t_lanzada', 'done', {'lanzamiento': ficha}),
                    task('t_sin', 'done', {'porque': 'Sin ficha'}), task('t_cola', 'ready', {'lanzamiento': ficha})] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'experimentos.json').write_text(json.dumps({'t_lanzada': {'avisado': '2026-10-16T08:00:00', 'lanzado': (equipo.now() - timedelta(hours=1)).isoformat()}}))
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual((items['t_lista']['columna'], items['t_lista']['responsable'], items['t_lista']['fase']), ('para-ti', 'owner', 'lista para lanzar'))
        self.assertTrue(items['t_lista']['fusionada'])
        self.assertEqual((items['t_lanzada']['columna'], items['t_lanzada']['responsable']), ('hecho', 'product'))
        self.assertEqual(items['t_sin']['columna'], 'hecho')
        self.assertEqual(items['t_cola']['columna'], 'cola')

    def test_launched_experiment_without_result_after_48_hours_returns_to_owner(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        # El cambio de hora no convierte 47 horas transcurridas en 48.
        at = datetime(2026, 10, 25, 12, tzinfo=equipo.MADRID)
        recent, old = at.timestamp(), at.timestamp() - 10 * 86400
        def task(tid, completed_at):
            return {'id': tid, 'title': tid, 'status': 'done', 'created_at': 1790000000, 'completed_at': completed_at, 'body': json.dumps({'lanzamiento': ficha})}
        def fake_tasks(pid):
            return [task('t_sin_medir', recent), task('t_medida', recent), task('t_vieja', old), task('t_reciente', recent),
                    task('t_fecha_rota', recent), task('t_sin_fusionar', recent), task('t_fusionada', recent)] if pid == 'prompted-os' else []
        hace = lambda horas: datetime.fromtimestamp(at.timestamp() - horas * 3600, equipo.MADRID).isoformat()
        registro = {'t_sin_medir': {'lanzado': hace(49)}, 't_medida': {'lanzado': hace(49)}, 't_vieja': {'lanzado': hace(49)},
                    't_reciente': {'lanzado': hace(47)}, 't_fecha_rota': {'lanzado': 'ayer por la tarde'}}
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'experimentos.json').write_text(json.dumps(registro))
            (Path(tmp) / 'experimentos').mkdir()
            (Path(tmp) / 'experimentos' / 'prompted-os.json').write_text(json.dumps([{'fecha': '2026-10-16', 'texto': 'Convirtió el 7 %', 'tarea': 't_medida'}]))
            for tid, commit in (('t_sin_fusionar', 'aaa111'), ('t_fusionada', 'bbb222')):
                (Path(tmp) / 'runs' / tid).mkdir(parents=True)
                (Path(tmp) / 'runs' / tid / 'receipt.json').write_text(json.dumps({'commit': commit}))
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), mock.patch.object(equipo, 'now', lambda: at), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: commit == 'bbb222'), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual((items['t_sin_medir']['columna'], items['t_sin_medir']['responsable'], items['t_sin_medir']['fase']), ('para-ti', 'owner', 'falta medir'))
        self.assertEqual(items['t_medida']['columna'], 'hecho')
        self.assertEqual((items['t_vieja']['columna'], items['t_vieja']['fase']), ('para-ti', 'falta medir'))
        self.assertEqual(items['t_reciente']['columna'], 'hecho')
        self.assertEqual(items['t_fecha_rota']['columna'], 'hecho')
        self.assertEqual((items['t_sin_fusionar']['columna'], items['t_sin_fusionar']['fase']), ('para-ti', 'lista para lanzar · falta fusionar'))
        self.assertEqual((items['t_fusionada']['columna'], items['t_fusionada']['fase']), ('para-ti', 'lista para lanzar'))

    def test_launched_task_with_a_saved_result_is_marked_as_measured(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        def task(tid):
            return {'id': tid, 'title': tid, 'status': 'done', 'created_at': 1790000000, 'completed_at': equipo.time.time(), 'body': json.dumps({'lanzamiento': ficha})}
        def fake_tasks(pid):
            return [task('t_medida'), task('t_sin_medir')] if pid == 'prompted-os' else []
        calls = []
        real = equipo.medidos
        def counting(pid):
            calls.append(pid)
            return real(pid)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'experimentos.json').write_text(json.dumps({t: {'avisado': '2026-10-16T08:00:00', 'lanzado': '2026-10-16T09:00:00'} for t in ('t_medida', 't_sin_medir')}))
            (Path(tmp) / 'experimentos').mkdir()
            (Path(tmp) / 'experimentos' / 'prompted-os.json').write_text(json.dumps([{'fecha': '2026-10-16', 'texto': 'Convirtió el 7 %', 'tarea': 't_medida'}]))
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), mock.patch.object(equipo, 'medidos', counting), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertTrue(items['t_medida']['medido'])
        self.assertNotIn('medido', items['t_sin_medir'])
        self.assertEqual(sorted(calls), sorted(set(calls)))
        self.assertEqual(calls.count('prompted-os'), 1)

    def test_measured_task_carries_the_latest_decision_it_has(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        def task(tid):
            return {'id': tid, 'title': tid, 'status': 'done', 'created_at': 1790000000, 'completed_at': equipo.time.time(), 'body': json.dumps({'lanzamiento': ficha})}
        def fake_tasks(pid):
            return [task('t_decidida'), task('t_sin_decision'), task('t_corregida')] if pid == 'prompted-os' else []
        resultados = [{'fecha': '2026-10-16', 'texto': 'Convirtió el 7 %', 'tarea': 't_decidida', 'decision': 'parar'},
                      {'fecha': '2026-10-16', 'texto': 'Convirtió el 3 %', 'tarea': 't_sin_decision'},
                      {'fecha': '2026-10-16', 'texto': 'Primera lectura', 'tarea': 't_corregida', 'decision': 'seguir'},
                      {'fecha': '2026-10-17', 'texto': 'Segunda lectura', 'tarea': 't_corregida', 'decision': 'cambiar'},
                      {'fecha': '2026-10-18', 'texto': 'Tercera lectura sin decisión', 'tarea': 't_corregida'}]
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'experimentos.json').write_text(json.dumps({t: {'avisado': '2026-10-16T08:00:00', 'lanzado': '2026-10-16T09:00:00'} for t in ('t_decidida', 't_sin_decision', 't_corregida')}))
            (Path(tmp) / 'experimentos').mkdir()
            (Path(tmp) / 'experimentos' / 'prompted-os.json').write_text(json.dumps(resultados))
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_decidida']['decision'], 'parar')
        self.assertTrue(items['t_sin_decision']['medido'])
        self.assertNotIn('decision', items['t_sin_decision'])
        self.assertEqual(items['t_corregida']['decision'], 'cambiar')

    def test_recent_measured_experiment_without_decision_returns_to_owner(self):
        ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}
        # Siete días son 168 horas, también en el fin del horario de verano.
        at = datetime(2026, 10, 25, 12, tzinfo=equipo.MADRID)
        recent, old = at.timestamp(), at.timestamp() - 10 * 86400
        tids = ('t_reciente', 't_decidida', 't_antigua', 't_completada', 't_sin_hora', 't_hora_rota', 't_limite', 't_decision_previa')
        def task(tid):
            return {'id': tid, 'title': tid, 'status': 'done', 'created_at': 1790000000, 'completed_at': old if tid == 't_completada' else recent,
                    'body': json.dumps({'lanzamiento': ficha})}
        def fake_tasks(pid):
            return [task(tid) for tid in tids] if pid == 'prompted-os' else []
        hace = lambda dias: datetime.fromtimestamp(at.timestamp() - dias * 86400, equipo.MADRID).isoformat()
        resultados = [{'tarea': tid, 'hora': hace(1)} for tid in tids]
        resultados[1]['decision'] = 'seguir'
        resultados[7]['decision'] = 'cambiar'
        resultados.extend([{'tarea': 't_antigua', 'hora': hace(8)}, {'tarea': 't_sin_hora'},
                           {'tarea': 't_hora_rota', 'hora': 'ayer por la tarde'}, {'tarea': 't_limite', 'hora': hace(7)},
                           {'tarea': 't_decision_previa', 'hora': hace(1)}])
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'experimentos.json').write_text(json.dumps({tid: {'lanzado': hace(10)} for tid in tids}))
            (Path(tmp) / 'experimentos').mkdir()
            (Path(tmp) / 'experimentos' / 'prompted-os.json').write_text(json.dumps(resultados))
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), mock.patch.object(equipo, 'now', lambda: at), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        for tid in ('t_reciente', 't_completada', 't_limite'):
            with self.subTest(tarea=tid):
                self.assertEqual((items[tid]['columna'], items[tid]['responsable'], items[tid]['fase']), ('para-ti', 'owner', 'falta decidir'))
        for tid in ('t_decidida', 't_antigua', 't_sin_hora', 't_hora_rota', 't_decision_previa'):
            with self.subTest(tarea=tid):
                self.assertEqual(items[tid]['columna'], 'hecho')
        self.assertEqual(items['t_decision_previa']['decision'], 'cambiar')

    def test_blocked_team_tasks_say_the_phase_they_stopped_in(self):
        def task(tid):
            return {'id': tid, 'title': tid, 'status': 'blocked', 'created_at': 1790000000, 'body': '{}'}
        def fake_tasks(pid):
            return [task('t_con_fase'), task('t_sin_fase')] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'runs' / 't_con_fase').mkdir(parents=True)
            (Path(tmp) / 'runs' / 't_con_fase' / 'fase.json').write_text(json.dumps({'fase': 'probando', 'intento': 2}))
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual((items['t_con_fase']['fase'], items['t_con_fase']['parada'], items['t_con_fase']['intento']), ('bloqueada', 'probando', 2))
        self.assertNotIn('parada', items['t_sin_fase'])
        self.assertNotIn('intento', items['t_sin_fase'])

    def test_superseded_matches_done_title(self):
        for status in ('blocked', 'triage'):
            with self.subTest(status=status):
                ts = [{'id': 't_blocked', 'title': 'Entregas: lista A', 'status': status},
                      {'id': 't_done', 'title': 'entregas: lista a…', 'status': 'done'}]
                self.assertEqual(equipo.superseded(ts), {'t_blocked'})

    def test_superseded_keeps_blocked_without_done_twin(self):
        ts = [{'id': 't_blocked', 'title': 'Entregas: lista A', 'status': 'blocked'},
              {'id': 't_done', 'title': 'Entregas: lista B', 'status': 'done'}]
        self.assertEqual(equipo.superseded(ts), set())

    def test_superseded_keeps_blocked_with_only_ready_twin(self):
        ts = [{'id': 't_blocked', 'title': 'Entregas: lista A', 'status': 'blocked'},
              {'id': 't_ready', 'title': 'entregas: lista a…', 'status': 'ready'}]
        self.assertEqual(equipo.superseded(ts), set())

    def test_checklist_hides_superseded_blocked_task(self):
        moment = equipo.time.time()
        ts = [{'id': 't_blocked', 'title': 'Entregas: lista A', 'status': 'blocked', 'created_at': moment - 30},
              {'id': 't_done', 'title': 'entregas: lista a…', 'status': 'done', 'created_at': moment - 20, 'completed_at': moment - 10},
              {'id': 't_other', 'title': 'Entregas: lista B', 'status': 'blocked', 'created_at': moment}]
        items = {t['id']: t for t in equipo.checklist(ts)}
        self.assertNotIn('t_blocked', items)
        self.assertEqual((items['t_done']['estado'], items['t_other']['estado']), ('hecha', 'bloqueada'))

    def test_project_brief_hides_superseded_blocked_task(self):
        for status in ('blocked', 'triage'):
            with self.subTest(status=status):
                ts = [{'id': 't_blocked', 'title': 'Motor: A', 'status': status},
                      {'id': 't_done', 'title': 'motor: a…', 'status': 'done'},
                      {'id': 't_other', 'title': 'Motor: B', 'status': status}]
                with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Motor', 'goal': 'Mejorar el motor'}), \
                     mock.patch.object(equipo, 'tasks', return_value=ts) as tasks, \
                     mock.patch.object(equipo, 'STATE', Path(_ISOLATION.name)), \
                     mock.patch.object(equipo, 'metrics', lambda pid: {}), mock.patch.object(equipo, 'lessons', lambda pid: []), \
                     mock.patch.object(equipo, 'ficha', lambda pid: None):
                    brief = equipo.project_brief('prompted-os')
                tasks.assert_called_once_with('prompted-os')
                self.assertEqual(brief['bloqueadas'], ['Motor: B: bloqueada antes del arreglo, motivo no registrado'])

    def test_retro_hides_superseded_blocked_task_in_prompt(self):
        for status in ('blocked', 'triage'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                ts = [{'id': 't_blocked', 'title': 'Motor: A', 'status': status},
                      {'id': 't_done', 'title': 'motor: a…', 'status': 'done'},
                      {'id': 't_other', 'title': 'Motor: B', 'status': status}]
                with mock.patch.object(equipo, 'STATE', Path(tmp)), \
                     mock.patch.object(equipo, 'receipts', return_value=[{'tarea': 't_done', 'estado': 'integrada'}]), \
                     mock.patch.object(equipo, 'tasks', return_value=ts) as tasks, \
                     mock.patch.object(equipo, 'project', lambda pid: {'name': 'Motor', 'repo': tmp}), \
                     mock.patch.object(equipo, 'git', return_value='abc'), mock.patch.object(equipo, 'worktree', return_value=Path(tmp)), \
                     mock.patch.object(equipo, 'metrics', lambda pid: {}), mock.patch.object(equipo, 'lessons', lambda pid: []), \
                     mock.patch.object(equipo, 'log'), mock.patch.object(equipo, 'claude', return_value='```json\n{}\n```') as claude:
                    self.assertEqual(equipo.retro('prompted-os'), {})
                tasks.assert_called_once_with('prompted-os')
                claude.assert_called_once()
                prompt = claude.call_args[0][0]
                self.assertIn('t_other', prompt)
                self.assertNotIn('t_blocked', prompt)

    def test_status_checklist_matches_blocked_tasks_after_replacement(self):
        moment = equipo.time.time()
        ts = [{'id': 't_blocked', 'title': 'Entregas: lista A', 'status': 'blocked', 'created_at': moment - 30},
              {'id': 't_done', 'title': 'entregas: lista a…', 'status': 'done', 'created_at': moment - 20, 'completed_at': moment - 10},
              {'id': 't_other', 'title': 'Entregas: lista B', 'status': 'blocked', 'created_at': moment}]
        config = {**equipo.CONFIG, 'projects': {'prompted-os': equipo.CONFIG['projects']['prompted-os']}}
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'CONFIG', config), mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'tasks', lambda pid: ts), mock.patch.object(equipo, 'lessons', lambda pid: []), \
                 mock.patch.object(equipo, 'metrics', lambda pid: {}), mock.patch.object(equipo, 'pending_merge', lambda pid: 0), \
                 mock.patch.object(equipo, 'hermes_overview', lambda: {}), mock.patch.object(equipo, 'modelos_hoy', lambda: {}), \
                 mock.patch.object(equipo, 'tablero', lambda: []), mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}):
                project = equipo.status()['proyectos']['prompted-os']
        self.assertEqual([t['id'] for t in project['bloqueadas']], ['t_other'])
        self.assertEqual([t['estado'] for t in project['tareas']].count('bloqueada'), 1)

    def test_board_hides_superseded_blocked_tasks(self):
        def task(tid, title, status):
            return {'id': tid, 'title': title, 'status': status, 'created_at': 1790000000, 'body': '{}'}
        def fake_tasks(pid):
            return [task('t_blocked', 'Entregas: lista A', 'blocked'),
                    task('t_done', 'entregas: lista a…', 'done'),
                    task('t_other', 'Entregas: lista B', 'blocked')] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero()}
        self.assertNotIn('t_blocked', items)
        self.assertEqual(items['t_other']['columna'], 'bloqueado')

    def test_board_blocked_column_uses_motivo_visible(self):
        def task(tid, title, error=None):
            t = {'id': tid, 'title': title, 'status': 'blocked', 'created_at': 1790000000, 'body': '{}'}
            if error is not None: t['last_failure_error'] = error
            return t
        def fake_tasks(pid):
            return [task('t_a', 'Alfa: uno'), task('t_b', 'Beta: dos'), task('t_c', 'Gamma: tres'), task('t_d', 'Delta: cuatro', 'x' * 1000)] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            equipo.save(state / 'runs' / 't_a' / 'receipt.json', {'estado': 'bloqueada', 'error': 'agotados los intentos: X'})
            equipo.save(state / 'runs' / 't_b' / 'receipt.json', {'estado': 'integrada', 'error': 'viejo'})
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'tasks', fake_tasks), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                items = {i['id']: i for i in equipo.tablero()}
        self.assertEqual(items['t_a']['motivo'], 'agotados los intentos: X')
        self.assertEqual(items['t_b']['motivo'], 'bloqueada antes del arreglo, motivo no registrado')
        self.assertEqual(items['t_c']['motivo'], 'bloqueada antes del arreglo, motivo no registrado')
        self.assertEqual(len(items['t_d']['motivo']), 400)

class LaunchNotice(unittest.TestCase):
    """A finished task with a launch card tells Owner it is ready; a failing notice never blocks the task."""
    ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}

    def task(self, spec):
        return {'id': 't_lanza', 'title': 'Landing nueva', 'status': 'ready', 'assignee': equipo.LANE, 'created_at': 1790000000, 'body': json.dumps(spec)}

    def test_card_sends_text_with_four_lines_to_owner(self):
        sent, posted = [], []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append(t)), \
             mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
            out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertEqual(sent, [out])
        for line in ('Owner OS', 'Landing nueva', 'Qué: Landing A/B', 'A quién: Tráfico frío', 'Métrica: Registros', 'Umbral: 5 % de conversión'):
            self.assertIn(line, out)
        self.assertEqual(posted[0][:2], ('alfred', ['owner']))
        self.assertIn('alfred', equipo.DEPARTMENTS)

    def test_card_with_parent_result_sends_its_title_and_truncated_result(self):
        sent, posted = [], []
        resultado = 'x' * 150
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), \
             mock.patch.object(equipo, 'resultados', lambda pid: [{'tarea': 't_padre', 'titulo': 'Experimento padre', 'texto': resultado}]), \
             mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append(t)), \
             mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
            out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha, 'viene_de': 't_padre'}))
        linea = f"Viene de: «Experimento padre» — {'x' * 139}…"
        self.assertIn(linea, out)
        self.assertEqual(sent, [out])
        self.assertEqual(posted[0][2], out)

    def test_card_with_parent_without_result_sends_its_id(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), \
             mock.patch.object(equipo, 'resultados', lambda pid: []), \
             mock.patch.object(equipo, 'telegram'), mock.patch.object(equipo, 'post'):
            out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha, 'viene_de': 't_padre'}))
        self.assertTrue(out.endswith('\nViene de: «t_padre»'))

    def test_card_without_parent_keeps_the_original_text(self):
        esperado = ('🚀 Listo para lanzar en Owner OS: «Landing nueva»\n'
                    'Qué: Landing A/B\nA quién: Tráfico frío\nMétrica: Registros\nUmbral: 5 % de conversión')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), \
             mock.patch.object(equipo, 'telegram'), mock.patch.object(equipo, 'post'):
            out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertEqual(out, esperado)

    def test_notice_records_unmerged_status_only_on_first_notice(self):
        for merged in (False, True):
            with self.subTest(merged=merged), tempfile.TemporaryDirectory() as tmp, \
                 mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'project', return_value={'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', return_value=merged) as fusionado, \
                 mock.patch.object(equipo, 'pending_merge', return_value=1), \
                 mock.patch.object(equipo, 'telegram'), mock.patch.object(equipo, 'post'):
                equipo.save(Path(tmp) / 'runs' / 't_lanza' / 'receipt.json', {'commit': 'abc123'})
                task = self.task({'lanzamiento': self.ficha})
                with mock.patch.object(equipo, 'save', wraps=equipo.save) as guardar:
                    equipo.avisar_lanzamiento('prompted-os', task)
                guardar.assert_called_once()
                registro = equipo.lanzamientos()
                if merged: self.assertNotIn('sin_fusionar', registro['t_lanza'])
                else: self.assertIs(registro['t_lanza']['sin_fusionar'], True)
                fusionado.return_value = not merged
                with mock.patch.object(equipo, 'save', wraps=equipo.save) as guardar:
                    equipo.avisar_lanzamiento('prompted-os', task)
                guardar.assert_not_called()
                self.assertEqual(equipo.lanzamientos(), registro)

    def test_card_with_unmerged_commit_explains_how_to_merge_before_launching(self):
        sent, posted = [], []
        line = 'Aún no está en la versión principal: se fusiona esta noche, o pulsa «Fusionar ahora» aquí abajo antes de lanzarlo.'
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: False), \
                 mock.patch.object(equipo, 'pending_merge', lambda pid: 1), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append(t)), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
                out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertTrue(out.endswith(line))
        self.assertEqual(sent, [out])
        self.assertEqual(posted[0][2], out)

    def test_card_with_unmerged_commit_sends_launch_and_merge_buttons(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: False), \
                 mock.patch.object(equipo, 'pending_merge', lambda pid: 1), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append((t, botones))), \
                 mock.patch.object(equipo, 'post'):
                equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertEqual(sent[0][1], [[{'text': 'Ya lo he lanzado', 'callback_data': 'l:t_lanza'}, {'text': 'No lo lanzo', 'callback_data': 'n:t_lanza'}],
                                       [{'text': 'Fusionar ahora', 'callback_data': 'f:prompted-os'}]])

    def test_card_with_merged_commit_keeps_the_original_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: True), \
                 mock.patch.object(equipo, 'telegram'), mock.patch.object(equipo, 'post'):
                out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertNotIn('Aún no está en la versión principal', out)

    def test_card_still_sends_when_merging_check_fails(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', side_effect=RuntimeError('git caído')), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append(t)), mock.patch.object(equipo, 'post'):
                out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertNotIn('Aún no está en la versión principal', out)
        self.assertEqual(sent, [out])

    def test_card_sends_launched_button(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: True), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append((t, botones))), \
                 mock.patch.object(equipo, 'post'):
                equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertEqual(sent[0][1], [[{'text': 'Ya lo he lanzado', 'callback_data': 'l:t_lanza'}, {'text': 'No lo lanzo', 'callback_data': 'n:t_lanza'}]])

    def test_card_with_invalid_task_id_and_unmerged_commit_sends_only_merge_button(self):
        sent = []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 'ab').mkdir(parents=True)
            (state / 'runs' / 'ab' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: False), \
                 mock.patch.object(equipo, 'pending_merge', lambda pid: 1), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append((t, botones))), \
                 mock.patch.object(equipo, 'post'):
                equipo.avisar_lanzamiento('prompted-os', {**self.task({'lanzamiento': self.ficha}), 'id': 'ab'})
        self.assertEqual(sent[0][1], [[{'text': 'Fusionar ahora', 'callback_data': 'f:prompted-os'}]])

    def test_card_with_unmerged_commit_names_three_other_improvements(self):
        sent, posted = [], []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: False), \
                 mock.patch.object(equipo, 'pending_merge', lambda pid: 4), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append(t)), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
                out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertTrue(out.endswith('Al fusionar entran también 3 mejoras más del equipo.'))
        self.assertEqual(sent, [out])
        self.assertEqual(posted[0][2], out)

    def test_card_with_unmerged_commit_names_one_other_improvement(self):
        sent, posted = [], []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: False), \
                 mock.patch.object(equipo, 'pending_merge', lambda pid: 2), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append(t)), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
                out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertTrue(out.endswith('Al fusionar entran también 1 mejora más del equipo.'))
        self.assertEqual(sent, [out])
        self.assertEqual(posted[0][2], out)

    def test_card_with_unmerged_commit_keeps_merge_button_when_count_fails(self):
        sent, posted = [], []
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            (state / 'runs' / 't_lanza').mkdir(parents=True)
            (state / 'runs' / 't_lanza' / 'receipt.json').write_text(json.dumps({'commit': 'abc123'}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': 'repo', 'base': 'main'}), \
                 mock.patch.object(equipo, 'merged', lambda repo, base, commit: False), \
                 mock.patch.object(equipo, 'pending_merge', side_effect=RuntimeError('git caído')), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append((t, botones))), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
                out = equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
        self.assertNotIn('Al fusionar entran también', out)
        self.assertEqual(sent[0][0], out)
        self.assertEqual(posted[0][2], out)
        self.assertIn({'text': 'Fusionar ahora', 'callback_data': 'f:prompted-os'}, sent[0][1][1])

    def test_card_without_valid_or_short_callback_sends_no_button(self):
        for tid in ('ab', 'a' * 70):
            sent = []
            with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), \
                 mock.patch.object(equipo, 'telegram', lambda t, botones=None: sent.append((t, botones))), \
                 mock.patch.object(equipo, 'post'):
                equipo.avisar_lanzamiento('prompted-os', {**self.task({'lanzamiento': self.ficha}), 'id': tid})
            self.assertIsNone(sent[0][1])

    def test_telegram_sends_buttons_only_when_given(self):
        link = {'chatId': 42}
        response = mock.Mock()
        with mock.patch.object(equipo, 'load', return_value=link), \
             mock.patch.object(equipo.pf, 'secret', return_value='123456:abcdefghijklmnopqrstuvwxyz'), \
             mock.patch('urllib.request.urlopen', return_value=response) as urlopen:
            equipo.telegram('Con botón', [[{'text': 'Lanzado', 'callback_data': 'l:t_lanza'}]])
            equipo.telegram('Sin botón')
        with_button = json.loads(urlopen.call_args_list[0].args[0].data)
        without_button = json.loads(urlopen.call_args_list[1].args[0].data)
        self.assertEqual(with_button['reply_markup']['inline_keyboard'], [[{'text': 'Lanzado', 'callback_data': 'l:t_lanza'}]])
        self.assertNotIn('reply_markup', without_button)

    def test_no_card_or_incomplete_card_sends_nothing(self):
        incomplete = {k: v for k, v in self.ficha.items() if k != 'umbral'}
        calls = []
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), mock.patch.object(equipo, 'telegram', lambda t, botones=None: calls.append(t)), \
             mock.patch.object(equipo, 'post', lambda *a, **k: calls.append(a)):
            for spec in ({'porque': 'Sin ficha'}, {'lanzamiento': incomplete}, {'lanzamiento': 'texto'}):
                self.assertIsNone(equipo.avisar_lanzamiento('prompted-os', self.task(spec)))
            self.assertIsNone(equipo.avisar_lanzamiento('prompted-os', {**self.task({}), 'body': None}))
        self.assertEqual(calls, [])

    def run_done(self, telegram):
        from contextlib import contextmanager
        @contextmanager
        def fake_board(pid): yield None
        task = self.task({'lanzamiento': self.ficha})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / 'state'; state.mkdir()
            (state / 'approved.json').write_text(json.dumps({task['id']: 'hash'}))
            with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'tasks', lambda pid, status=None: [task]), mock.patch.object(equipo, 'board', fake_board), \
                 mock.patch.object(equipo.kb, 'claim_task', lambda *a, **k: mock.Mock(current_run_id=1)), \
                 mock.patch.object(equipo.kb, 'complete_task', lambda *a, **k: None), mock.patch.object(equipo, 'sync_base', lambda pid: None), \
                 mock.patch.object(equipo, 'execute', lambda *a: 'Integrada.'), mock.patch.object(equipo.subprocess, 'Popen'), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), mock.patch.object(equipo, 'telegram', telegram), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: None), mock.patch.object(equipo, 'log'):
                return equipo.run_project('prompted-os')

    def test_run_project_notifies_and_survives_a_failing_notice(self):
        sent = []
        self.assertEqual(self.run_done(lambda t, botones=None: sent.append(t)), 'hecha')
        self.assertTrue(any('Listo para lanzar' in t and 'Landing nueva' in t for t in sent))
        def broken(text, botones=None): raise RuntimeError('Telegram caído')
        self.assertEqual(self.run_done(broken), 'hecha')

    def run_blocked(self, error, fase=None):
        from contextlib import contextmanager
        @contextmanager
        def fake_board(pid): yield None
        task = self.task({'lanzamiento': self.ficha}); reasons = []
        def raises(*a): raise error
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / 'state'; state.mkdir()
            (state / 'approved.json').write_text(json.dumps({task['id']: 'hash'}))
            if fase is not None:
                (state / 'runs' / task['id']).mkdir(parents=True)
                (state / 'runs' / task['id'] / 'fase.json').write_text(json.dumps(fase))
            with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'tasks', lambda pid, status=None: [task]), mock.patch.object(equipo, 'board', fake_board), \
                 mock.patch.object(equipo.kb, 'claim_task', lambda *a, **k: mock.Mock(current_run_id=1)), \
                 mock.patch.object(equipo.kb, 'block_task', lambda *a, **k: reasons.append(k['reason'])), mock.patch.object(equipo, 'sync_base', lambda pid: None), \
                 mock.patch.object(equipo, 'execute', raises), mock.patch.object(equipo.subprocess, 'Popen'), \
                 mock.patch.object(equipo, 'follow_up'), mock.patch.object(equipo, 'avisar_encargo'), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: None), mock.patch.object(equipo, 'log'):
                self.assertEqual(equipo.run_project('prompted-os'), 'bloqueada')
        return reasons[0]

    def run_network_failure(self, veces=None, ultima=None, reiniciar=False):
        from contextlib import contextmanager
        @contextmanager
        def fake_board(pid): yield None
        task = self.task({'lanzamiento': self.ficha})
        error = 'API inaccesible tras 3 intentos: ENOTFOUND'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); state = root / 'state'; state.mkdir()
            (state / 'approved.json').write_text(json.dumps({task['id']: 'hash'}))
            folder = state / 'runs' / task['id']; folder.mkdir(parents=True)
            red = {'veces': veces}
            if ultima is not None: red['ultima'] = ultima
            if veces is not None: equipo.save(folder / 'red.json', red)
            def raises(*a):
                equipo.save(folder / 'receipt.json', {'estado': 'bloqueada', 'error': error})
                raise equipo.Stop(error)
            with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'tasks', lambda pid, status=None: [task]), mock.patch.object(equipo, 'board', fake_board), \
                 mock.patch.object(equipo.kb, 'claim_task', lambda *a, **k: mock.Mock(current_run_id=1)), \
                 mock.patch.object(equipo.kb, 'reclaim_task') as reclaim, mock.patch.object(equipo.kb, 'block_task') as block, \
                 mock.patch.object(equipo, 'sync_base'), mock.patch.object(equipo, 'execute', raises), \
                 mock.patch.object(equipo.subprocess, 'Popen'), mock.patch.object(equipo, 'lid_will_sleep', return_value=False), \
                 mock.patch.object(equipo, 'sleep_clock', return_value=0), mock.patch.object(equipo, 'follow_up') as follow, \
                 mock.patch.object(equipo, 'avisar_encargo') as notice, mock.patch.object(equipo, 'post') as post, mock.patch.object(equipo, 'log'):
                result = equipo.run_project('prompted-os')
                if veces == 3 and not reiniciar:
                    self.assertEqual(result, 'bloqueada')
                    reclaim.assert_not_called()
                    block.assert_called_once()
                    self.assertEqual(block.call_args.kwargs['reason'], error)
                else:
                    self.assertEqual(result, 'interrumpida')
                    reclaim.assert_called_once()
                    self.assertEqual(reclaim.call_args.args, (None, task['id']))
                    self.assertEqual(reclaim.call_args.kwargs['reason'], 'la API de Claude no respondía')
                    block.assert_not_called()
                    follow.assert_not_called(); notice.assert_not_called(); post.assert_not_called()
                    receipt = equipo.load(folder / 'receipt.json', {})
                    self.assertEqual(receipt['estado'], 'interrumpida')
                    self.assertIs(receipt['red'], True)
                    self.assertEqual(receipt['error'], 'la API de Claude no respondía: vuelve a la cola')
                    self.assertEqual(receipt['detalle'], error)
                    self.assertEqual(equipo.relaunch_note(receipt), '')
                    self.assertNotIn('veces', receipt)
                saved_red = equipo.load(folder / 'red.json', {})
                if veces == 3 and not reiniciar:
                    self.assertEqual(saved_red, red)
                else:
                    self.assertEqual(saved_red['veces'], 1 if reiniciar else (veces or 0) + 1)
                    self.assertIn('ultima', saved_red)
                    datetime.fromisoformat(saved_red['ultima'])

    def test_network_failure_requeues_the_first_three_times(self):
        for veces in (None, 1, 2):
            with self.subTest(veces=veces): self.run_network_failure(veces)

    def test_fourth_network_failure_blocks(self):
        self.run_network_failure(3)

    def test_network_failure_requeues_after_seven_hours(self):
        self.run_network_failure(3, (equipo.now() - timedelta(hours=7)).isoformat(timespec='seconds'), reiniciar=True)

    def test_fourth_network_failure_within_one_hour_blocks(self):
        self.run_network_failure(3, (equipo.now() - timedelta(hours=1)).isoformat(timespec='seconds'))

    def test_blocked_without_message_says_type_phase_and_attempt(self):
        self.assertEqual(self.run_blocked(RuntimeError(), {'fase': 'probando', 'intento': 2}),
                         'fallo sin mensaje (RuntimeError) en la fase probando, intento 2')

    def test_blocked_without_message_or_phase_file_says_only_the_type(self):
        self.assertEqual(self.run_blocked(RuntimeError()), 'fallo sin mensaje (RuntimeError)')

    def test_blocked_with_a_message_keeps_it_exactly(self):
        self.assertEqual(self.run_blocked(equipo.Stop('conflicto con otra tarea del equipo'), {'fase': 'probando', 'intento': 2}),
                         'conflicto con otra tarea del equipo')

    def execute_blocked(self, error):
        task = self.task({'lanzamiento': self.ficha})
        def raises(*a, **k): raise error
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            folder = state / 'runs' / task['id']; folder.mkdir(parents=True)
            (folder / 'fase.json').write_text(json.dumps({'fase': 'revisando', 'intento': 1}))
            with mock.patch.object(equipo, 'STATE', state), \
                 mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'repo': tmp}), \
                 mock.patch.object(equipo, 'spec_of', lambda pid, t: {'titulo': 'Landing nueva'}), \
                 mock.patch.object(equipo, 'worktree', lambda *a, **k: Path(tmp) / 'wt'), mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), \
                 mock.patch.object(equipo, 'relaunch_note', lambda prev: ''), mock.patch.object(equipo, 'attempts', raises):
                with self.assertRaises(type(error)):
                    equipo.execute('prompted-os', task, 'claim', 1)
            return equipo.load(folder / 'receipt.json', {})

    def test_execute_leaves_a_blocked_receipt_with_a_reason_when_the_error_has_no_text(self):
        receipt = self.execute_blocked(RuntimeError())
        self.assertEqual(receipt['estado'], 'bloqueada')
        self.assertEqual(receipt['error'], 'fallo sin mensaje (RuntimeError) en la fase revisando, intento 1')
        self.assertTrue(receipt['fin'])

    def test_execute_receipt_keeps_the_text_of_a_stop(self):
        receipt = self.execute_blocked(equipo.Stop('timeout al probar equipo (900 s)'))
        self.assertEqual(receipt['estado'], 'bloqueada')
        self.assertEqual(receipt['error'], 'timeout al probar equipo (900 s)')

    def test_notice_records_when_it_was_sent_and_posts_even_if_telegram_fails(self):
        posted = []
        def broken(text, botones=None): raise RuntimeError('Telegram caído')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
             mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS'}), mock.patch.object(equipo, 'telegram', broken), \
             mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a)):
            with self.assertRaises(RuntimeError):
                equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
            first = equipo.load(Path(tmp) / 'experimentos.json', {})['t_lanza']
            with self.assertRaises(RuntimeError):
                equipo.avisar_lanzamiento('prompted-os', self.task({'lanzamiento': self.ficha}))
            again = equipo.load(Path(tmp) / 'experimentos.json', {})['t_lanza']
        self.assertEqual(len(posted), 2)
        self.assertEqual((first['proyecto'], first['titulo']), ('prompted-os', 'Landing nueva'))
        self.assertTrue(first['avisado'])
        self.assertEqual(again['avisado'], first['avisado'])   # only the first notice sets the time

    def launched(self, tmp, spec=None, posted=None):
        task = self.task({'lanzamiento': self.ficha} if spec is None else spec)
        return [mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', lambda pid, status=None: [task] if pid == 'prompted-os' else []),
                mock.patch.object(equipo, 'post', lambda *a, **k: posted.append(a))]

    def test_launched_keeps_the_time_and_posts_once(self):
        posted = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self.launched(tmp, posted=posted)
            for p in patches: p.start()
            try:
                first = equipo.lanzado('t_lanza'); second = equipo.lanzado('t_lanza')
                saved = equipo.load(Path(tmp) / 'experimentos.json', {})['t_lanza']
            finally:
                for p in patches: p.stop()
        self.assertEqual((first['tarea'], first['proyecto'], first['titulo']), ('t_lanza', 'prompted-os', 'Landing nueva'))
        self.assertTrue(first['lanzado'])
        self.assertEqual(second['lanzado'], first['lanzado'])
        self.assertEqual(saved['lanzado'], first['lanzado'])
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][:2], ('alfred', ['todos']))
        self.assertIn('Owner ha lanzado «Landing nueva»', posted[0][2])
        self.assertIn('Registros', posted[0][2]); self.assertIn('5 % de conversión', posted[0][2])

    def launch_with(self, *vias):
        with tempfile.TemporaryDirectory() as tmp:
            patches = self.launched(tmp, posted=[])
            for p in patches: p.start()
            try:
                for via in vias: equipo.lanzado('t_lanza', via)
                return equipo.load(Path(tmp) / 'experimentos.json', {})['t_lanza']
            finally:
                for p in patches: p.stop()

    def test_launched_keeps_where_it_was_marked(self):
        self.assertEqual(self.launch_with('telegram')['via'], 'telegram')
        self.assertEqual(self.launch_with('app')['via'], 'app')

    def test_launched_ignores_an_unknown_via(self):
        for via in ('x', '', None): self.assertNotIn('via', self.launch_with(via))

    def test_launched_again_with_another_via_keeps_the_first(self):
        self.assertEqual(self.launch_with('telegram', 'app')['via'], 'telegram')
        self.assertNotIn('via', self.launch_with(None, 'app'))

    def test_launched_keeps_the_notice_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            equipo.save(Path(tmp) / 'experimentos.json', {'t_lanza': {'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'avisado': '2026-10-15T08:00:00+02:00'}})
            patches = self.launched(tmp, posted=[])
            for p in patches: p.start()
            try: out = equipo.lanzado('t_lanza')
            finally:
                for p in patches: p.stop()
        self.assertEqual(out['avisado'], '2026-10-15T08:00:00+02:00')

    def test_launched_refuses_unknown_tasks_and_tasks_without_card(self):
        posted = []
        with tempfile.TemporaryDirectory() as tmp:
            patches = self.launched(tmp, {'porque': 'Sin ficha'}, posted)
            for p in patches: p.start()
            try:
                with self.assertRaisesRegex(equipo.Stop, 'tarea desconocida: t_nada'): equipo.lanzado('t_nada')
                with self.assertRaisesRegex(equipo.Stop, 'no tiene ficha de lanzamiento'): equipo.lanzado('t_lanza')
            finally:
                for p in patches: p.stop()
            self.assertFalse((Path(tmp) / 'experimentos.json').exists())
        self.assertEqual(posted, [])

    def test_board_shows_when_the_experiment_was_launched(self):
        def fake_tasks(pid, status=None):
            return [self.task({'lanzamiento': self.ficha}), {**self.task({'lanzamiento': self.ficha}), 'id': 't_otra'}] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), mock.patch.object(equipo, 'post', lambda *a, **k: None), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                out = equipo.lanzado('t_lanza')
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_lanza']['lanzado'], out['lanzado'])
        self.assertEqual(items['t_lanza']['lanzamiento'], self.ficha)
        self.assertNotIn('lanzado', items['t_otra'])

    def test_board_hides_a_half_filled_launch_card(self):
        half = {**self.ficha, 'umbral': ''}
        def fake_tasks(pid, status=None):
            return [self.task({'lanzamiento': self.ficha}), {**self.task({'lanzamiento': half}), 'id': 't_media'}] if pid == 'prompted-os' else []
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'tasks', fake_tasks), mock.patch.object(equipo, 'post', lambda *a, **k: None), \
                 mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
                equipo.save(Path(tmp) / 'experimentos.json', {'t_media': {'lanzado': '2026-10-15T09:00:00+02:00'}})
                items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        self.assertEqual(items['t_lanza']['lanzamiento'], self.ficha)
        self.assertNotIn('lanzamiento', items['t_media'])
        self.assertNotIn('lanzado', items['t_media'])

    def test_launched_command_without_task_explains_the_usage(self):
        logged = []
        with mock.patch.object(sys, 'argv', ['equipo.py', 'lanzado']), mock.patch.object(equipo, 'lanzado') as called, \
             mock.patch.object(equipo, 'log', lambda *parts: logged.append(' '.join(str(x) for x in parts))):
            with self.assertRaises(SystemExit) as cm: equipo.main()
        self.assertEqual(cm.exception.code, 2)
        called.assert_not_called()
        self.assertTrue(any('uso: equipo.py lanzado <id de tarea>' in line for line in logged))

    def test_alfred_prompt_knows_the_launch_and_result_commands(self):
        # Since 15/10 Owner's own instructions (team, experiments) live in his private profile, not in voice.ts.
        profile = Path.home() / 'Library/Application Support/com.prompted.os/Workspace/perfil/instrucciones.md'
        if not profile.exists(): self.skipTest('sin el perfil privado de Owner en este Mac')
        text = profile.read_text()
        for cmd in ('lanzado', 'resultado'):
            self.assertIn(f'equipo.py {cmd}', text)

class TareaIdValida(unittest.TestCase):
    """The engine checks the task id with the same rule as the server (TAREA in equipo-acciones.ts)."""
    malos = ['', '../x', 't;rm', '--texto', '-abc', '_abc', 'a' * 81]
    medida = 'Medí 12 clics en 3 días'

    def task(self, tid):
        return {'id': tid, 'title': 'Landing nueva', 'status': 'ready', 'assignee': equipo.LANE, 'created_at': 1790000000,
                'body': json.dumps({'lanzamiento': LaunchNotice.ficha})}

    def test_bad_ids_are_refused_before_reading_any_board(self):
        for tid in self.malos:
            with self.subTest(tid=tid), mock.patch.object(equipo, 'tasks') as board:
                with self.assertRaisesRegex(equipo.Stop, 'Tarea no válida.'): equipo.lanzado(tid)
                with self.assertRaisesRegex(equipo.Stop, 'Tarea no válida.'): equipo.registrar_resultado('prompted-os', self.medida, tid)
                board.assert_not_called()

    def test_good_ids_pass_the_validation(self):
        for tid in ('t_036cee09', 'a' * 80):
            with self.subTest(tid=tid), tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), \
                 mock.patch.object(equipo, 'tasks', lambda pid, status=None, tid=tid: [self.task(tid)] if pid == 'prompted-os' else []), \
                 mock.patch.object(equipo, 'post', lambda *a, **k: None):
                self.assertEqual(equipo.lanzado(tid)['tarea'], tid)
                self.assertEqual(equipo.registrar_resultado('prompted-os', self.medida, tid)['tarea'], tid)

    def test_parser_knows_the_launch_and_result_commands(self):
        cmd = next(action for action in equipo.parser()._actions if action.dest == 'cmd')
        for name in ('lanzado', 'resultado'): self.assertIn(name, cmd.choices)
        a = equipo.parser().parse_args(['resultado', 'prompted-os', '--texto', 'x', '--experimento', 't_1'])
        self.assertEqual((a.experimento, a.texto), ('t_1', 'x'))

    def test_parser_accepts_the_args_the_server_builds_for_a_launch(self):
        # Same args as lanzadoBoton() and experimentoArgs() in equipo-acciones.ts.
        for via in ('telegram', 'app'):
            a = equipo.parser().parse_args(['lanzado', 't_036cee09', '--via', via])
            self.assertEqual((a.cmd, a.project, a.via), ('lanzado', 't_036cee09', via))
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit): equipo.parser().parse_args(['lanzado', 't_036cee09', '--via', 'x'])

    def test_parser_accepts_the_decision_of_a_result(self):
        a = equipo.parser().parse_args(['resultado', 'prompted-os', '--texto', 'Medí 12 clics', '--experimento', 't_036cee09', '--decision', 'parar'])
        self.assertEqual((a.cmd, a.project, a.experimento, a.decision), ('resultado', 'prompted-os', 't_036cee09', 'parar'))
        self.assertIsNone(equipo.parser().parse_args(['resultado', 'prompted-os', '--texto', 'Medí 12 clics']).decision)
        with mock.patch.object(sys, 'stderr', io.StringIO()), self.assertRaises(SystemExit):
            equipo.parser().parse_args(['resultado', 'prompted-os', '--texto', 'Medí 12 clics', '--decision', 'x'])

class Descartar(unittest.TestCase):
    """Owner says why he will not launch an experiment: kept once, told to the mesa once, and undone if he launches it after all."""
    ficha = LaunchNotice.ficha
    motivo = 'No tengo tráfico para medirlo'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name); self.posted = []
        self.ts = [{'id': 't_lanza', 'title': 'Landing nueva', 'status': 'done', 'created_at': 1790000000, 'body': json.dumps({'lanzamiento': self.ficha})},
                   {'id': 't_sin', 'title': 'Sin ficha', 'status': 'done', 'created_at': 1790000000, 'body': json.dumps({'porque': 'Sin ficha'})}]
        for patch in (mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'tasks', lambda pid, status=None: self.ts if pid == 'prompted-os' else []),
                      mock.patch.object(equipo, 'post', lambda *a, **k: self.posted.append(a))):
            patch.start(); self.addCleanup(patch.stop)

    def saved(self): return equipo.load(self.state / 'experimentos.json', {})

    def test_bad_ids_are_refused_before_reading_any_board(self):
        for tid in TareaIdValida.malos + [None, 7]:
            with self.subTest(tid=tid), mock.patch.object(equipo, 'tasks') as board:
                with self.assertRaisesRegex(equipo.Stop, 'Tarea no válida.'): equipo.descartar(tid, self.motivo)
                board.assert_not_called()
        self.assertEqual(self.posted, [])

    def test_unknown_task_and_task_without_card_are_refused(self):
        with self.assertRaisesRegex(equipo.Stop, 'tarea desconocida: t_nada'): equipo.descartar('t_nada', self.motivo)
        with self.assertRaisesRegex(equipo.Stop, 'esa tarea no tiene ficha de lanzamiento'): equipo.descartar('t_sin', self.motivo)
        self.assertFalse((self.state / 'experimentos.json').exists())
        self.assertEqual(self.posted, [])

    def test_a_launched_experiment_cannot_be_discarded(self):
        equipo.save(self.state / 'experimentos.json', {'t_lanza': {'proyecto': 'prompted-os', 'lanzado': '2026-10-15T09:00:00+02:00'}})
        with self.assertRaisesRegex(equipo.Stop, 'ya está lanzado: apunta lo medido con equipo.py resultado'): equipo.descartar('t_lanza', self.motivo)
        self.assertNotIn('descartado', self.saved()['t_lanza'])
        self.assertEqual(self.posted, [])

    def test_the_reason_needs_the_minimum_length_after_trimming(self):
        for corto in ('', None, '   ', 'x' * (equipo.RESULTADO_MIN - 1), '  ' + 'x' * (equipo.RESULTADO_MIN - 1) + '  '):
            with self.subTest(motivo=corto), self.assertRaisesRegex(equipo.Stop, f'cuenta por qué no lo lanzas \\(mínimo {equipo.RESULTADO_MIN} caracteres\\)'):
                equipo.descartar('t_lanza', corto)
        self.assertFalse((self.state / 'experimentos.json').exists())
        out = equipo.descartar('t_lanza', '  ' + 'x' * equipo.RESULTADO_MIN + '  ')
        self.assertEqual(out['motivo'], 'x' * equipo.RESULTADO_MIN)

    def test_it_keeps_the_reason_cut_to_the_maximum_and_tells_the_mesa(self):
        equipo.save(self.state / 'experimentos.json', {'t_lanza': {'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'avisado': '2026-10-15T08:00:00+02:00'}})
        out = equipo.descartar('t_lanza', 'y' * (equipo.RESULTADO_MAX + 50))
        saved = self.saved()['t_lanza']
        self.assertEqual(set(out), {'tarea', 'proyecto', 'titulo', 'descartado', 'motivo'})
        self.assertEqual((out['tarea'], out['proyecto'], out['titulo']), ('t_lanza', 'prompted-os', 'Landing nueva'))
        self.assertEqual(len(out['motivo']), equipo.RESULTADO_MAX)
        self.assertEqual((saved['descartado'], saved['motivo'], saved['avisado']), (out['descartado'], out['motivo'], '2026-10-15T08:00:00+02:00'))
        self.assertEqual((saved['proyecto'], saved['titulo']), ('prompted-os', 'Landing nueva'))
        self.assertEqual(datetime.fromisoformat(saved['descartado']).microsecond, 0)
        self.assertEqual(len(self.posted), 1)
        self.assertEqual(self.posted[0][:2], ('alfred', ['todos']))
        self.assertEqual(self.posted[0][2], f"Owner descarta «Landing nueva» sin lanzarlo: {out['motivo']}")
        self.assertEqual(self.posted[0][3:], ('aviso', 'prompted-os'))

    def test_it_is_idempotent_and_posts_once(self):
        first = equipo.descartar('t_lanza', self.motivo)
        before = (self.state / 'experimentos.json').read_text()
        second = equipo.descartar('t_lanza', 'Otro motivo distinto del primero')
        self.assertEqual(second, first)
        self.assertEqual(second['motivo'], self.motivo)
        self.assertEqual((self.state / 'experimentos.json').read_text(), before)
        self.assertEqual(len(self.posted), 1)

    def test_launching_afterwards_removes_the_discard(self):
        equipo.descartar('t_lanza', self.motivo)
        out = equipo.lanzado('t_lanza')
        saved = self.saved()['t_lanza']
        self.assertNotIn('descartado', saved); self.assertNotIn('motivo', saved)
        self.assertEqual((saved['lanzado'], saved['proyecto'], saved['titulo']), (out['lanzado'], 'prompted-os', 'Landing nueva'))
        self.assertEqual(len(self.posted), 2)
        self.assertIn('Owner ha lanzado «Landing nueva»', self.posted[1][2])
        self.assertEqual(equipo.experimentos('prompted-os')['descartados'], 0)

    def test_board_keeps_an_old_undecided_experiment_for_owner_and_files_the_discarded_one(self):
        old, recent = equipo.time.time() - 10 * 86400, equipo.time.time()
        def task(tid, completed_at, spec=None):
            return {'id': tid, 'title': tid, 'status': 'done', 'created_at': 1790000000, 'completed_at': completed_at, 'body': json.dumps(spec or {'lanzamiento': self.ficha})}
        self.ts = [task('t_vieja_sin_lanzar', old), task('t_descartada', recent), task('t_descartada_vieja', old), task('t_sin_ficha_vieja', old, {'porque': 'Sin ficha'})]
        equipo.save(self.state / 'experimentos.json', {
            't_descartada': {'proyecto': 'prompted-os', 'descartado': '2026-10-15T09:00:00+02:00', 'motivo': self.motivo},
            't_descartada_vieja': {'proyecto': 'prompted-os', 'descartado': '2026-10-05T09:00:00+02:00', 'motivo': self.motivo}})
        with mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
            items = {i['id']: i for i in equipo.tablero() if i['fuente'] == 'equipo'}
        vieja = items['t_vieja_sin_lanzar']
        self.assertEqual((vieja['columna'], vieja['responsable'], vieja['fase']), ('para-ti', 'owner', 'lista para lanzar'))
        hecha = items['t_descartada']
        self.assertEqual((hecha['columna'], hecha['responsable'], hecha['fase']), ('hecho', 'product', 'descartado'))
        self.assertEqual((hecha['descartado'], hecha['descarte']), ('2026-10-15T09:00:00+02:00', self.motivo))
        self.assertNotIn('motivo', hecha)
        self.assertNotIn('t_descartada_vieja', items)   # the usual 7-day cut applies to a discarded one
        self.assertNotIn('t_sin_ficha_vieja', items)

    def test_board_returns_a_discarded_task_to_owner_once_launched(self):
        self.ts = [{'id': 't_lanza', 'title': 'Landing nueva', 'status': 'done', 'created_at': 1790000000, 'completed_at': equipo.time.time(), 'body': json.dumps({'lanzamiento': self.ficha})}]
        equipo.descartar('t_lanza', self.motivo)
        equipo.lanzado('t_lanza')
        with mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'hermes_overview', lambda: {}):
            item = next(i for i in equipo.tablero() if i['id'] == 't_lanza')
        self.assertEqual((item['columna'], item['fase']), ('hecho', 'hecha'))   # no commit in its receipt: just done
        self.assertNotIn('descartado', item); self.assertNotIn('descarte', item)
        self.assertTrue(item['lanzado'])

    def test_discard_command_calls_descartar_with_the_task_and_the_reason(self):
        argv = ['equipo.py', 'descartar', 't_036cee09', '--texto', 'No tengo tráfico para medirlo']
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(equipo, 'descartar', return_value={'tarea': 't_036cee09'}) as called, \
             mock.patch('builtins.print') as shown:
            equipo.main()
        called.assert_called_once_with('t_036cee09', 'No tengo tráfico para medirlo')
        self.assertIn('t_036cee09', shown.call_args[0][0])

    def test_discard_command_without_task_explains_the_usage(self):
        logged = []
        with mock.patch.object(sys, 'argv', ['equipo.py', 'descartar']), mock.patch.object(equipo, 'descartar') as called, \
             mock.patch.object(equipo, 'log', lambda *parts: logged.append(' '.join(str(x) for x in parts))):
            with self.assertRaises(SystemExit) as cm: equipo.main()
        self.assertEqual(cm.exception.code, 2)
        called.assert_not_called()
        self.assertTrue(any('uso: equipo.py descartar <id de tarea> --texto <motivo>' in line for line in logged))

    def test_parser_knows_the_discard_command(self):
        cmd = next(action for action in equipo.parser()._actions if action.dest == 'cmd')
        self.assertIn('descartar', cmd.choices)
        a = equipo.parser().parse_args(['descartar', 't_1', '--texto', 'por qué no'])
        self.assertEqual((a.project, a.texto), ('t_1', 'por qué no'))

class Experimentos(unittest.TestCase):
    """What Owner measured in a launched experiment is kept per project and read by the planner."""
    ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.state = Path(self.tmp.name) / 'state'
        self.ts = [{'id': 't_exp', 'title': 'Landing nueva', 'status': 'done', 'body': json.dumps({'lanzamiento': self.ficha})},
                   {'id': 't_sin', 'title': 'Sin ficha', 'status': 'done', 'body': None}]
        self.patches = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'tasks', lambda pid, status=None: self.ts),
                        mock.patch.object(equipo, 'lessons_text', lambda pid, role: ''), mock.patch.object(equipo, 'mesa_load', lambda date=None: {'mensajes': []}),
                        mock.patch.object(equipo, 'metrics', lambda pid, days=7: {'tareas': 0})]
        for patch in self.patches: patch.start()

    def tearDown(self):
        for patch in self.patches: patch.stop()
        self.tmp.cleanup()

    def saved(self): return equipo.load(self.state / 'experimentos' / 'prompted-os.json', [])

    def test_result_with_task_keeps_title_and_card(self):
        out = equipo.registrar_resultado('prompted-os', '  42 registros de 600 visitas (7 %)  ', 't_exp')
        self.assertEqual((out['titulo'], out['lanzamiento'], out['tarea']), ('Landing nueva', self.ficha, 't_exp'))
        self.assertEqual(out['texto'], '42 registros de 600 visitas (7 %)')
        self.assertEqual(self.saved(), [out])

    def test_result_without_task_is_kept_without_empty_keys(self):
        out = equipo.registrar_resultado('prompted-os', 'Probé el aviso a mano y nadie lo abrió')
        self.assertEqual(set(out), {'fecha', 'hora', 'texto'})
        out2 = equipo.registrar_resultado('prompted-os', 'Resultado de una tarea sin ficha', 't_sin')
        self.assertEqual((out2['titulo'], 'lanzamiento' in out2), ('Sin ficha', False))
        self.assertEqual(self.saved(), [out, out2])

    def test_valid_decision_is_kept_and_an_invalid_one_writes_nothing(self):
        for decision in equipo.DECISIONES:
            self.assertEqual(equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp', decision)['decision'], decision)
        self.assertEqual([r['decision'] for r in self.saved()], list(equipo.DECISIONES))
        self.assertNotIn('decision', equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp', ''))
        self.assertNotIn('decision', equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp', None))
        self.assertEqual(set(equipo.registrar_resultado('prompted-os', 'Probé el aviso a mano y nadie lo abrió')), {'fecha', 'hora', 'texto'})
        before = self.saved()
        with self.assertRaisesRegex(equipo.Stop, 'Decisión no válida: usa seguir, cambiar o parar.'):
            equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp', 'quizá')
        self.assertEqual(self.saved(), before)

    def test_invalid_decision_does_not_create_the_file(self):
        with self.assertRaises(equipo.Stop): equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp', 'quizá')
        self.assertFalse((self.state / 'experimentos').exists())

    def test_planner_signals_name_the_stopped_and_changed_lines(self):
        equipo.registrar_resultado('prompted-os', 'Solo 2 registros de 600 visitas', 't_exp', 'parar')
        equipo.registrar_resultado('prompted-os', 'Probé el aviso a mano y nadie lo abrió', None, 'cambiar')
        equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_sin', 'seguir')
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Owner decidió parar: «Landing nueva». No propongas más experimentos de esa línea.', signals)
        self.assertIn('Owner pidió cambiar: «Probé el aviso a mano y nadie lo abrió». Propón una variante con otra hipótesis u otro público, no la misma.', signals)
        titulo_seguir = next(r['titulo'] for r in equipo.resultados('prompted-os') if r.get('tarea') == 't_sin')
        self.assertIn(f'Owner decidió seguir: «{titulo_seguir}». Propón el siguiente paso de esa línea (más alcance, otro canal o la siguiente fase: contenido → oferta → producto) con su ficha de lanzamiento; no repitas el mismo experimento.', signals)
        self.assertIn('"decision": "seguir"', signals)

    def test_planner_signals_with_only_follow_adds_follow_line(self):
        equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp', 'seguir')
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Owner decidió seguir: «Landing nueva». Propón el siguiente paso de esa línea (más alcance, otro canal o la siguiente fase: contenido → oferta → producto) con su ficha de lanzamiento; no repitas el mismo experimento.', signals)
        self.assertNotIn('Owner decidió parar', signals)
        self.assertNotIn('Owner pidió cambiar', signals)

    def test_bad_input_stops_and_writes_nothing(self):
        for args in (('prompted-os', 'corto'), ('prompted-os', '          '), ('inventado', 'texto suficientemente largo'),
                     ('prompted-os', 'texto suficientemente largo', 't_no_existe')):
            with self.assertRaises(equipo.Stop): equipo.registrar_resultado(*args)
        self.assertFalse((self.state / 'experimentos').exists())

    def test_only_the_last_30_are_kept_and_text_is_cut(self):
        for n in range(35): equipo.registrar_resultado('prompted-os', f'resultado número {n:02d} ' + 'x' * 700)
        kept = self.saved()
        self.assertEqual(len(kept), 30)
        self.assertTrue(kept[0]['texto'].startswith('resultado número 05'))
        self.assertTrue(all(len(r['texto']) == 600 for r in kept))

    def test_planner_signals_status_and_brief_read_them(self):
        equipo.registrar_resultado('prompted-os', 'Convirtió el 7 % frente al 5 % esperado', 't_exp')
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Resultados de experimentos que Owner ha medido', signals)
        self.assertIn('Convirtió el 7 % frente al 5 % esperado', signals)
        self.assertIn('Landing nueva', signals)
        self.assertNotIn('Resultados de experimentos', equipo.planner_signals('norte'))
        with mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), mock.patch.object(equipo, 'lessons', lambda pid: []), \
             mock.patch.object(equipo, 'ficha', lambda pid: None):
            brief = equipo.project_brief('prompted-os')
        self.assertEqual([r['titulo'] for r in brief['experimentos']], ['Landing nueva'])

class Decidir(unittest.TestCase):
    ahora = datetime.fromisoformat('2026-10-19T12:00:00+02:00')
    tid = 't_036cee09'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.path = self.state / 'experimentos' / 'prompted-os.json'
        self.post = mock.Mock()
        ficha = {'que': 'Landing', 'a_quien': 'Frío', 'metrica': 'Registros', 'umbral': '5 %'}
        ts = [{'id': self.tid, 'title': 'Landing nueva', 'status': 'ready', 'body': json.dumps({'lanzamiento': ficha})}]
        patches = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'now', lambda: self.ahora),
                   mock.patch.object(equipo, 'tasks', lambda pid: ts if pid == 'prompted-os' else []),
                   mock.patch.object(equipo, 'hermes_overview', lambda: {}),
                   mock.patch.object(equipo, 'mesa_load', lambda: {'mensajes': []}), mock.patch.object(equipo, 'post', self.post)]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)

    def test_updates_only_latest_result_and_tablero_reads_decision(self):
        anterior = {'tarea': self.tid, 'titulo': 'Landing anterior', 'texto': 'Dos registros', 'decision': 'cambiar'}
        ultimo = {'tarea': self.tid, 'titulo': 'Landing nueva', 'texto': 'Siete registros'}
        otro = {'tarea': 't_otra', 'texto': 'Otro experimento'}
        equipo.save(self.path, [anterior, ultimo, otro])
        found = equipo.decidir(self.tid, 'seguir')
        self.assertEqual(found, {'tarea': self.tid, 'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'decision': 'seguir'})
        self.assertEqual(equipo.resultados('prompted-os'), [anterior, {**ultimo, 'decision': 'seguir', 'decidido': self.ahora.isoformat(timespec='seconds')}, otro])
        self.post.assert_called_once_with('alfred', ['todos'], 'Owner decide seguir en «Landing nueva».', 'aviso', 'prompted-os')
        item = next(i for i in equipo.tablero() if i['id'] == self.tid)
        self.assertEqual(item['decision'], 'seguir')

    def test_existing_decision_leaves_file_unchanged(self):
        for decision in equipo.DECISIONES:
            with self.subTest(decision=decision):
                equipo.save(self.path, [{'tarea': self.tid}, {'tarea': self.tid, 'decision': decision}])
                before = self.path.read_bytes()
                with self.assertRaisesRegex(equipo.Stop, '^ese resultado ya tiene decisión$'):
                    equipo.decidir(self.tid, 'seguir')
                self.assertEqual(self.path.read_bytes(), before)
        self.post.assert_not_called()

    def test_task_without_result_is_rejected(self):
        with self.assertRaisesRegex(equipo.Stop, '^ese experimento no tiene resultado apuntado: usa equipo.py resultado$'):
            equipo.decidir(self.tid, 'seguir')
        self.assertFalse(self.path.exists())
        self.post.assert_not_called()

    def test_invalid_task_and_decision_are_rejected(self):
        for tid in ('../x', '-abc'):
            with self.subTest(tid=tid), self.assertRaisesRegex(equipo.Stop, '^Tarea no válida\\.$'):
                equipo.decidir(tid, 'seguir')
        with self.assertRaisesRegex(equipo.Stop, '^Decisión no válida: usa seguir, cambiar o parar\\.$'):
            equipo.decidir(self.tid, 'quizá')
        self.assertFalse(self.path.exists())
        self.post.assert_not_called()

    def test_parser_accepts_decidir(self):
        args = equipo.parser().parse_args(['decidir', 't_036cee09', '--decision', 'seguir'])
        self.assertEqual((args.cmd, args.project, args.decision), ('decidir', 't_036cee09', 'seguir'))

class LanzamientosMedidos(unittest.TestCase):
    """The planner, the mesa and the status see how many announced experiments Owner launched and how long it took."""
    ahora = datetime.fromisoformat('2026-10-15T12:00:00+02:00')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        patches = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'now', lambda: self.ahora),
                   mock.patch.object(equipo, 'lessons_text', lambda pid, role: ''), mock.patch.object(equipo, 'mesa_load', lambda date=None: {'mensajes': []}),
                   mock.patch.object(equipo, 'metrics', lambda pid, days=7: {'tareas': 0})]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)

    def registro(self):
        equipo.save(self.state / 'experimentos.json', {
            't_a': {'proyecto': 'prompted-os', 'avisado': '2026-10-14T08:00:00+02:00', 'lanzado': '2026-10-14T11:00:00+02:00'},
            't_b': {'proyecto': 'prompted-os', 'avisado': '2026-10-15T09:00:00+02:00'},
            't_c': {'proyecto': 'norte', 'avisado': '2026-10-14T08:00:00+02:00', 'lanzado': '2026-10-14T09:00:00+02:00'},
            't_d': {'proyecto': 'prompted-os', 'avisado': '2026-10-05T08:00:00+02:00', 'lanzado': '2026-10-05T10:00:00+02:00'}})

    def test_counts_only_this_project_in_the_window(self):
        self.registro()
        self.assertEqual(equipo.experimentos('prompted-os'), {'avisados': 2, 'lanzados': 1, 'lanzados_por_via': {'telegram': 0, 'app': 0, 'otra': 1}, 'horas_hasta_lanzar': 3.0,
                                                           'avisados_sin_fusionar': 0, 'fusionados_24h': 0,
                                                           'sin_medir': [{'tarea': 't_d', 'titulo': 't_d', 'lanzado': '2026-10-05T10:00:00+02:00'}, {'tarea': 't_a', 'titulo': 't_a', 'lanzado': '2026-10-14T11:00:00+02:00'}], 'sin_medir_total': 2,
                                                           'recordados': 0, 'medidos_tras_recordar': 0,
                                                           'recordados_lanzar': 0, 'decididos_tras_recordar_lanzar': 0, 'por_lanzar_total': 1,
                                                           'decisiones': {'seguir': 0, 'cambiar': 0, 'parar': 0, 'sin_decision': 0}, 'seguir_con_siguiente': 0,
                                                           'por_lanzar': [{'tarea': 't_b', 'titulo': 't_b', 'avisado': '2026-10-15T09:00:00+02:00'}],
                                                           'descartados': 0, 'motivos_descarte': []})

    def test_without_record_returns_zeros(self):
        self.assertEqual(equipo.experimentos('prompted-os'), {'avisados': 0, 'lanzados': 0, 'lanzados_por_via': {'telegram': 0, 'app': 0, 'otra': 0}, 'horas_hasta_lanzar': None, 'sin_medir': [], 'sin_medir_total': 0,
                                                           'avisados_sin_fusionar': 0, 'fusionados_24h': 0,
                                                           'recordados': 0, 'medidos_tras_recordar': 0, 'por_lanzar': [],
                                                           'recordados_lanzar': 0, 'decididos_tras_recordar_lanzar': 0, 'por_lanzar_total': 0,
                                                           'decisiones': {'seguir': 0, 'cambiar': 0, 'parar': 0, 'sin_decision': 0}, 'seguir_con_siguiente': 0,
                                                           'descartados': 0, 'motivos_descarte': []})

    def test_counts_unmerged_notices_and_merges_within_24_hours(self):
        notice = self.ahora - timedelta(days=2)
        entry = {'proyecto': 'prompted-os', 'avisado': notice.isoformat(), 'sin_fusionar': True}
        for fusionado in ((notice + timedelta(hours=30)).isoformat(), 'rota', None,
                          (notice - timedelta(seconds=1)).isoformat(), '2026-10-14T08:00:00'):
            with self.subTest(fusionado=fusionado):
                equipo.save(self.state / 'experimentos.json', {
                    't_a': {**entry, 'fusionado': (notice + timedelta(hours=24)).isoformat()},
                    't_b': {**entry, 'fusionado': fusionado},
                    't_rota': {**entry, 'avisado': 'rota'},
                    't_vieja': {**entry, 'avisado': (self.ahora - timedelta(days=8)).isoformat()},
                    't_futura': {**entry, 'avisado': (self.ahora + timedelta(days=1)).isoformat()},
                    't_otro': {**entry, 'proyecto': 'norte'},
                    't_fusionada': {**entry, 'sin_fusionar': False, 'fusionado': notice.isoformat()}})
                found = equipo.experimentos('prompted-os')
                self.assertEqual(found['avisados_sin_fusionar'], 3)
                self.assertEqual(found['fusionados_24h'], 1)

    def test_planner_signals_reports_unmerged_merge_rate(self):
        self.assertNotIn('Avisos sin fusionar', equipo.planner_signals('prompted-os'))
        base = equipo.experimentos('prompted-os')
        cases = (
            (0, 0, None),
            (2, 1, ('Avisos con trabajo aún sin fusionar (7 días): 2, fusionados en 24 h: 1', 'no es señal')),
            (6, 3, ('Avisos con trabajo aún sin fusionar (7 días): 6, fusionados en 24 h: 3 (50 %).', 'mantenlo')),
            (10, 1, ('(10 %).', 'Menos del 20 %')),
            (10, 3, ('(30 %).', 'sin conclusión')))
        for total, fusionados, expected in cases:
            with self.subTest(total=total, fusionados=fusionados), mock.patch.object(equipo, 'experimentos', return_value={
                    **base, 'avisados_sin_fusionar': total, 'fusionados_24h': fusionados}):
                text = equipo.planner_signals('prompted-os')
            if expected is None:
                self.assertNotIn('Avisos con trabajo aún sin fusionar', text)
                continue
            for fragment in expected: self.assertIn(fragment, text)

    def test_por_lanzar_total_counts_all_seven_pending_before_the_cap(self):
        equipo.save(self.state / 'experimentos.json', {
            f't_{n}': {'proyecto': 'prompted-os', 'avisado': f'2026-10-14T0{n}:00:00+02:00'} for n in range(7)})
        with mock.patch.object(equipo, 'lanzamientos', wraps=equipo.lanzamientos) as read:
            found = equipo.experimentos('prompted-os')
        read.assert_called_once_with()
        self.assertEqual(found['por_lanzar_total'], 7)
        self.assertEqual(len(found['por_lanzar']), 5)

    def test_planner_does_not_report_pending_experiments_below_the_cap(self):
        equipo.save(self.state / 'experimentos.json', {
            f't_{n}': {'proyecto': 'prompted-os', 'avisado': f'2026-10-14T0{n}:00:00+02:00'} for n in range(2)})
        self.assertNotIn('Esperan a Owner', equipo.planner_signals('prompted-os'))

    def test_planner_reports_three_pending_experiments(self):
        equipo.save(self.state / 'experimentos.json', {
            f't_{n}': {'proyecto': 'prompted-os', 'avisado': f'2026-10-14T0{n}:00:00+02:00'} for n in range(3)})
        self.assertIn('Esperan a Owner 3 experimentos avisados sin lanzar ni descartar.', equipo.planner_signals('prompted-os'))

    def test_planner_reports_three_pending_experiments_with_old_notices(self):
        notice = (self.ahora - timedelta(days=9)).isoformat()
        equipo.save(self.state / 'experimentos.json', {
            f't_{n}': {'proyecto': 'prompted-os', 'avisado': notice} for n in range(3)})
        text = equipo.planner_signals('prompted-os')
        self.assertIn('Esperan a Owner 3 experimentos avisados sin lanzar ni descartar.', text)
        self.assertIn('Avisados hace más de 48 h y sin lanzar', text)

    def test_planner_ignores_launched_and_discarded_experiments_for_the_cap(self):
        equipo.save(self.state / 'experimentos.json', {
            't_a': {'proyecto': 'prompted-os', 'avisado': '2026-10-14T08:00:00+02:00'},
            't_b': {'proyecto': 'prompted-os', 'avisado': '2026-10-14T09:00:00+02:00'},
            't_lanzado': {'proyecto': 'prompted-os', 'avisado': '2026-10-14T10:00:00+02:00', 'lanzado': '2026-10-14T11:00:00+02:00'},
            't_descartado': {'proyecto': 'prompted-os', 'avisado': '2026-10-14T12:00:00+02:00', 'descartado': '2026-10-14T13:00:00+02:00'}})
        self.assertNotIn('Esperan a Owner', equipo.planner_signals('prompted-os'))

    def test_planner_reports_pending_experiments_at_the_cap(self):
        self.assertEqual(equipo.POR_LANZAR_TOPE, 3)
        equipo.save(self.state / 'experimentos.json', {
            f't_{n}': {'proyecto': 'prompted-os', 'avisado': f'2026-10-14T0{n}:00:00+02:00'}
            for n in range(equipo.POR_LANZAR_TOPE)})
        self.assertIn('Esperan a Owner 3 experimentos avisados sin lanzar ni descartar.', equipo.planner_signals('prompted-os'))

    def test_launch_reminders_count_decisions_within_72_hours(self):
        equipo.save(self.state / 'experimentos.json', {
            't_a': {'proyecto': 'prompted-os', 'recordado_lanzar': '2026-10-10T08:00:00+02:00', 'lanzado': '2026-10-10T18:00:00+02:00'},
            't_b': {'proyecto': 'prompted-os', 'recordado_lanzar': '2026-10-10T08:00:00+02:00', 'descartado': '2026-10-13T16:00:00+02:00'},
            't_otro': {'proyecto': 'norte', 'recordado_lanzar': '2026-10-10T08:00:00+02:00'},
            't_viejo': {'proyecto': 'prompted-os', 'recordado_lanzar': '2026-10-05T08:00:00+02:00'},
            't_roto': {'proyecto': 'prompted-os', 'recordado_lanzar': 'rota'}})
        found = equipo.experimentos('prompted-os')
        self.assertEqual(found['recordados_lanzar'], 2)
        self.assertEqual(found['decididos_tras_recordar_lanzar'], 1)
        self.assertEqual(equipo.experimentos('prompted-os', days=2)['recordados_lanzar'], 0)

    def test_launch_reminders_ignore_invalid_decision_dates_and_count_each_entry_once(self):
        base = {'proyecto': 'prompted-os', 'recordado_lanzar': '2026-10-10T08:00:00+02:00'}
        equipo.save(self.state / 'experimentos.json', {
            't_roto': {**base, 'lanzado': 'rota'},
            't_antes': {**base, 'descartado': '2026-10-10T07:00:00+02:00'},
            't_limite': {**base, 'lanzado': '2026-10-10T08:00:00+02:00', 'descartado': '2026-10-13T08:00:00+02:00'},
            't_72': {**base, 'descartado': '2026-10-13T08:00:00+02:00'}})
        found = equipo.experimentos('prompted-os')
        self.assertEqual(found['recordados_lanzar'], 4)
        self.assertEqual(found['decididos_tras_recordar_lanzar'], 2)

    def test_planner_signals_launch_reminders_only_when_present(self):
        self.assertNotIn('Recordatorios de lanzar', equipo.planner_signals('prompted-os'))
        base = equipo.experimentos('prompted-os')
        for count in (2, 5):
            with self.subTest(count=count), mock.patch.object(equipo, 'experimentos', return_value={
                    **base, 'recordados_lanzar': count, 'decididos_tras_recordar_lanzar': 1}):
                text = equipo.planner_signals('prompted-os')
            self.assertIn(f'Recordatorios de lanzar (7 días): {count} enviados, 1 lanzados o descartados en 72 h.', text)
            self.assertEqual('Con menos de 5 no es señal.' in text, count < 5)

    def test_decisiones_count_the_results_of_the_window_by_what_owner_decided(self):
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [
            {'hora': '2026-10-15T09:00:00+02:00', 'texto': 'Solo 2 registros de 600 visitas', 'decision': 'parar'},
            {'hora': '2026-10-15T10:00:00+02:00', 'texto': 'Convirtió el 7 % frente al 5 %', 'decision': 'seguir'},
            {'hora': '2026-10-15T11:00:00+02:00', 'texto': 'Nadie abrió el aviso'},
            {'hora': '2026-10-15T11:30:00+02:00', 'texto': 'Decisión inválida', 'decision': 'x'},
            {'hora': 'rota', 'texto': 'Hora rota', 'decision': 'seguir'},
            {'hora': '2026-10-05T09:00:00+02:00', 'texto': 'Hace diez días', 'decision': 'cambiar'}])
        self.assertEqual(equipo.experimentos('prompted-os')['decisiones'], {'seguir': 1, 'cambiar': 0, 'parar': 1, 'sin_decision': 2})

    def test_planner_signals_line_for_decisions_asks_the_mesa_only_when_some_are_missing(self):
        base = equipo.experimentos('prompted-os')
        def with_decisiones(**d):
            return mock.patch.object(equipo, 'experimentos', lambda pid, days=7: {**base, 'decisiones': {'seguir': 0, 'cambiar': 0, 'parar': 0, 'sin_decision': 0, **d}})
        with with_decisiones():
            self.assertNotIn('Resultados apuntados', equipo.planner_signals('prompted-os'))
        with with_decisiones(seguir=1, parar=1, sin_decision=2):
            signals = equipo.planner_signals('prompted-os')
        self.assertIn('Resultados apuntados (7 días): 4, con decisión 2. Pide a Owner en la mesa si sigue, cambia o para antes de proponer el siguiente experimento de esa línea.', signals)
        with with_decisiones(seguir=2):
            signals = equipo.planner_signals('prompted-os')
        self.assertIn('Resultados apuntados (7 días): 2, con decisión 2.', signals)
        self.assertNotIn('si sigue, cambia o para', signals)

    def test_discarded_leave_por_lanzar_and_are_counted_with_their_reason_latest_first(self):
        equipo.save(self.state / 'experimentos.json', {
            't_b': {'proyecto': 'prompted-os', 'titulo': 'Pendiente', 'avisado': '2026-10-15T09:00:00+02:00'},
            't_x': {'proyecto': 'prompted-os', 'titulo': 'Descartada ayer', 'avisado': '2026-10-13T08:00:00+02:00', 'descartado': '2026-10-14T10:00:00+02:00', 'motivo': 'No tengo tráfico para medirlo'},
            't_y': {'proyecto': 'prompted-os', 'titulo': 'Descartada hoy', 'avisado': '2026-10-14T08:00:00+02:00', 'descartado': '2026-10-15T09:00:00+02:00', 'motivo': 'Cuesta más de lo que vale'},
            't_otro': {'proyecto': 'norte', 'titulo': 'De otro proyecto', 'avisado': '2026-10-14T08:00:00+02:00', 'descartado': '2026-10-15T09:00:00+02:00', 'motivo': 'No es de este proyecto'},
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Fuera de la ventana', 'avisado': '2026-10-05T08:00:00+02:00', 'descartado': '2026-10-06T09:00:00+02:00', 'motivo': 'Muy antigua para contar'}})
        found = equipo.experimentos('prompted-os')
        self.assertEqual([x['tarea'] for x in found['por_lanzar']], ['t_b'])
        self.assertEqual((found['avisados'], found['lanzados'], found['descartados']), (3, 0, 2))
        self.assertEqual(found['motivos_descarte'], [{'tarea': 't_y', 'titulo': 'Descartada hoy', 'motivo': 'Cuesta más de lo que vale'},
                                                     {'tarea': 't_x', 'titulo': 'Descartada ayer', 'motivo': 'No tengo tráfico para medirlo'}])

    def test_motivos_descarte_are_capped_at_five_and_a_launched_one_is_not_discarded(self):
        registro = {f't_{n}': {'proyecto': 'prompted-os', 'titulo': f'Exp {n}', 'avisado': '2026-10-13T08:00:00+02:00',
                               'descartado': f'2026-10-14T0{n}:00:00+02:00', 'motivo': f'motivo número {n}'} for n in range(7)}
        registro['t_lanzada'] = {'proyecto': 'prompted-os', 'avisado': '2026-10-13T08:00:00+02:00', 'lanzado': '2026-10-14T12:00:00+02:00', 'descartado': '2026-10-14T23:00:00+02:00'}
        equipo.save(self.state / 'experimentos.json', registro)
        found = equipo.experimentos('prompted-os')
        self.assertEqual(found['descartados'], 7)
        self.assertEqual([x['tarea'] for x in found['motivos_descarte']], ['t_6', 't_5', 't_4', 't_3', 't_2'])
        self.assertEqual(found['lanzados'], 1)

    def test_planner_signals_name_the_discard_reasons(self):
        self.assertNotIn('Owner descartó sin lanzar', equipo.planner_signals('prompted-os'))
        equipo.save(self.state / 'experimentos.json', {
            't_x': {'proyecto': 'prompted-os', 'titulo': 'Landing vieja', 'avisado': '2026-10-14T08:00:00+02:00', 'descartado': '2026-10-14T10:00:00+02:00', 'motivo': 'No tengo tráfico para medirlo'}})
        text = equipo.planner_signals('prompted-os')
        self.assertIn('Owner descartó sin lanzar: «Landing vieja» (No tengo tráfico para medirlo). No propongas otro experimento con ese mismo freno.', text)
        self.assertNotIn('Avisados hace más de 48 h', text)
        self.assertNotIn('Owner descartó sin lanzar', equipo.planner_signals('norte'))

    def test_por_lanzar_includes_only_pending_old_notices_without_counting_them_in_the_window(self):
        notice = (self.ahora - timedelta(days=10)).isoformat()
        entry = {'proyecto': 'prompted-os', 'avisado': notice}
        equipo.save(self.state / 'experimentos.json', {
            't_pendiente': entry,
            't_lanzado': {**entry, 'lanzado': notice},
            't_descartado': {**entry, 'descartado': notice}})
        found = equipo.experimentos('prompted-os')
        self.assertEqual(found['por_lanzar'], [{'tarea': 't_pendiente', 'titulo': 't_pendiente', 'avisado': notice}])
        self.assertEqual(found['por_lanzar_total'], 1)
        self.assertEqual(found['avisados'], 0)

    def test_por_lanzar_is_oldest_notice_first_capped_at_five_and_includes_old_notices(self):
        avisos = {f't_{n}': {'proyecto': 'prompted-os', 'titulo': f'Exp {n}', 'avisado': f'2026-10-1{n + 1}T08:00:00+02:00'} for n in range(2)}
        avisos.update({f't_n{n}': {'proyecto': 'prompted-os', 'avisado': f'2026-10-14T0{n}:00:00+02:00'} for n in range(1, 7)})
        avisos['t_vieja'] = {'proyecto': 'prompted-os', 'avisado': '2026-10-05T08:00:00+02:00'}
        avisos['t_rota'] = {'proyecto': 'prompted-os', 'avisado': 'rota'}
        equipo.save(self.state / 'experimentos.json', dict(reversed(list(avisos.items()))))
        found = equipo.experimentos('prompted-os')['por_lanzar']
        self.assertEqual([x['tarea'] for x in found], ['t_vieja', 't_0', 't_1', 't_n1', 't_n2'])
        self.assertEqual(found[0], {'tarea': 't_vieja', 'titulo': 't_vieja', 'avisado': '2026-10-05T08:00:00+02:00'})
        self.assertEqual(equipo.experimentos('prompted-os')['por_lanzar_total'], 9)   # t_rota stays out of the queue
        self.assertEqual(equipo.experimentos('prompted-os')['avisados'], 8)   # t_0, t_1 and t_n1..t_n6; t_vieja (10 days old) and t_rota do not count

    def test_planner_names_only_notices_older_than_48_hours_without_launch(self):
        equipo.save(self.state / 'experimentos.json', {
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Landing vieja', 'avisado': '2026-10-13T10:00:00+02:00'},   # 50 h
            't_nueva': {'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'avisado': '2026-10-15T02:00:00+02:00'}})  # 10 h
        text = equipo.planner_signals('prompted-os')
        self.assertIn('Avisados hace más de 48 h y sin lanzar: «Landing vieja».', text)
        self.assertNotIn('Landing nueva»', text.split('Avisados hace más de 48 h')[1])

    def test_planner_has_no_waiting_line_without_old_notices(self):
        equipo.save(self.state / 'experimentos.json', {
            't_nueva': {'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'avisado': '2026-10-15T02:00:00+02:00'},
            't_lanzada': {'proyecto': 'prompted-os', 'titulo': 'Vieja lanzada', 'avisado': '2026-10-13T10:00:00+02:00', 'lanzado': '2026-10-13T12:00:00+02:00'}})
        self.assertNotIn('Avisados hace más de 48 h', equipo.planner_signals('prompted-os'))

    def test_tablero_reads_lanzamientos_once_with_two_cards(self):
        ficha = {'que': 'Landing', 'a_quien': 'Frío', 'metrica': 'Registros', 'umbral': '5 %'}
        ts = [{'id': f't_{n}', 'title': f'Tarea {n}', 'status': 'ready', 'body': json.dumps({'lanzamiento': ficha})} for n in range(2)]
        config = {**equipo.CONFIG, 'projects': {'prompted-os': equipo.CONFIG['projects']['prompted-os']}}
        calls = []
        def leer():
            calls.append(1); return {'t_0': {'lanzado': '2026-10-15T10:00:00+02:00'}}
        with mock.patch.object(equipo, 'CONFIG', config), mock.patch.object(equipo, 'tasks', lambda pid, status=None: ts), \
             mock.patch.object(equipo, 'lanzamientos', leer):
            items = {i['id']: i for i in equipo.tablero()}
        self.assertEqual(len(calls), 1)
        self.assertEqual(items['t_0']['lanzado'], '2026-10-15T10:00:00+02:00')
        self.assertNotIn('lanzado', items['t_1'])

    def recordatorios(self):
        base = {'avisado': '2026-10-10T08:00:00+02:00', 'lanzado': '2026-10-11T08:00:00+02:00'}
        equipo.save(self.state / 'experimentos.json', {
            't_r1': {**base, 'proyecto': 'prompted-os', 'recordado': '2026-10-13T08:00:00+02:00'},
            't_r2': {**base, 'proyecto': 'prompted-os', 'recordado': '2026-10-13T09:00:00+02:00'},
            't_r3': {**base, 'proyecto': 'norte', 'recordado': '2026-10-13T09:00:00+02:00'},
            't_r4': {**base, 'proyecto': 'prompted-os', 'recordado': 'roto'}})
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [
            {'texto': 'Convirtió el 7 %', 'tarea': 't_r1', 'hora': '2026-10-13T18:00:00+02:00'},
            {'texto': 'Convirtió el 3 %', 'tarea': 't_r2', 'hora': '2026-10-16T17:00:00+02:00'},
            {'texto': 'Convirtió el 1 %', 'tarea': 't_r4', 'hora': 'rota'}])
        equipo.save(self.state / 'experimentos' / 'norte.json', [{'texto': 'Convirtió el 9 %', 'tarea': 't_r3', 'hora': '2026-10-13T10:00:00+02:00'}])

    def test_recordados_count_results_within_72_hours_of_this_project_only(self):
        self.recordatorios()
        found = equipo.experimentos('prompted-os')
        self.assertEqual((found['recordados'], found['medidos_tras_recordar']), (2, 1))
        self.assertEqual(equipo.experimentos('norte')['recordados'], 1)

    def test_broken_dates_do_not_raise_and_old_reminders_are_ignored(self):
        equipo.save(self.state / 'experimentos.json', {
            't_x': {'proyecto': 'prompted-os', 'recordado': 'roto'}, 't_y': {'proyecto': 'prompted-os', 'recordado': '2026-10-01T08:00:00+02:00'},
            't_z': {'proyecto': 'prompted-os', 'recordado': '2026-10-14T08:00:00+02:00'}})
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [{'texto': 'sin hora válida', 'tarea': 't_z', 'hora': 'rota'}, 'basura'])
        found = equipo.experimentos('prompted-os')
        self.assertEqual((found['recordados'], found['medidos_tras_recordar']), (1, 0))

    def test_planner_signals_line_for_reminders_only_when_there_are_some(self):
        self.assertNotIn('Recordatorios de medir', equipo.planner_signals('prompted-os'))
        self.recordatorios()
        self.assertIn('Recordatorios de medir (7 días): 2 enviados, 1 con resultado en 72 h. Con menos de 5 no es señal.', equipo.planner_signals('prompted-os'))

    def test_planner_signals_reminder_line_gives_a_signal_with_five_or_more(self):
        found = {'avisados': 0, 'lanzados': 0, 'sin_medir': [], 'recordados': 6, 'medidos_tras_recordar': 2}
        with mock.patch.object(equipo, 'experimentos', lambda pid: found), mock.patch.object(equipo, 'lessons', lambda pid: []):
            self.assertIn('otra forma de pedir el resultado', equipo.planner_signals('prompted-os'))
            found['medidos_tras_recordar'] = 4
            signals = equipo.planner_signals('prompted-os')
            self.assertIn('El recordatorio funciona', signals)
            self.assertIn('(67 %)', signals)

    def test_launched_experiment_leaves_sin_medir_once_its_result_is_saved(self):
        self.registro()
        self.assertEqual(equipo.medidos('prompted-os'), set())
        self.assertEqual([x['tarea'] for x in equipo.experimentos('prompted-os')['sin_medir']], ['t_d', 't_a'])
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [{'fecha': '2026-10-15', 'texto': 'Convirtió el 7 %', 'tarea': 't_a'}, {'texto': 'sin tarea'}])
        self.assertEqual(equipo.medidos('prompted-os'), {'t_a'})
        self.assertEqual([x['tarea'] for x in equipo.experimentos('prompted-os')['sin_medir']], ['t_d'])

    def test_old_launch_without_result_stays_in_sin_medir_outside_the_window(self):
        launch = self.ahora - timedelta(days=20)
        equipo.save(self.state / 'experimentos.json', {
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Landing vieja',
                        'avisado': (launch - timedelta(hours=3)).isoformat(), 'lanzado': launch.isoformat()}})
        found = equipo.experimentos('prompted-os')
        self.assertEqual(found['sin_medir'], [{'tarea': 't_vieja', 'titulo': 'Landing vieja', 'lanzado': launch.isoformat()}])
        self.assertEqual((found['avisados'], found['lanzados']), (0, 0))
        self.assertEqual(found['lanzados_por_via'], {'telegram': 0, 'app': 0, 'otra': 0})
        self.assertIsNone(found['horas_hasta_lanzar'])

    def test_planner_reports_old_unmeasured_launch_without_recent_notices(self):
        launch = self.ahora - timedelta(days=20)
        equipo.save(self.state / 'experimentos.json', {
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Landing vieja',
                        'avisado': (launch - timedelta(hours=3)).isoformat(), 'lanzado': launch.isoformat()}})
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Lanzados sin resultado apuntado: «Landing vieja». Pide a Owner lo medido antes de proponer el siguiente experimento de esa línea.', signals)
        self.assertNotIn('Experimentos listos para lanzar', signals)

    def test_sin_medir_is_oldest_first_and_capped_at_five(self):
        equipo.save(self.state / 'experimentos.json', {
            f't_{n}': {'proyecto': 'prompted-os', 'titulo': f'Exp {n}', 'avisado': '2026-10-13T08:00:00+02:00', 'lanzado': f'2026-10-14T0{9 - n}:00:00+02:00'}
            for n in range(7)})
        self.assertEqual([x['tarea'] for x in equipo.experimentos('prompted-os')['sin_medir']], ['t_6', 't_5', 't_4', 't_3', 't_2'])

    def test_sin_medir_keeps_each_launch_date_in_oldest_first_order(self):
        old_launch = (self.ahora - timedelta(days=20)).isoformat()
        recent_launch = (self.ahora - timedelta(days=2)).isoformat()
        equipo.save(self.state / 'experimentos.json', {
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Landing vieja', 'avisado': old_launch, 'lanzado': old_launch},
            't_reciente': {'proyecto': 'prompted-os', 'titulo': 'Landing reciente', 'avisado': recent_launch, 'lanzado': recent_launch}})
        self.assertEqual(equipo.experimentos('prompted-os')['sin_medir'], [
            {'tarea': 't_vieja', 'titulo': 'Landing vieja', 'lanzado': old_launch},
            {'tarea': 't_reciente', 'titulo': 'Landing reciente', 'lanzado': recent_launch}])

    def test_sin_medir_total_counts_all_unmeasured_launches_before_the_cap(self):
        registro = {}
        for n in range(7):
            launch = self.ahora - timedelta(days=20 if n < 2 else 2, hours=n)
            registro[f't_{n}'] = {'proyecto': 'prompted-os', 'titulo': f'Exp {n}', 'avisado': (launch - timedelta(hours=3)).isoformat(), 'lanzado': launch.isoformat()}
        equipo.save(self.state / 'experimentos.json', registro)
        found = equipo.experimentos('prompted-os')
        self.assertEqual(found['sin_medir_total'], 7)
        self.assertEqual(len(found['sin_medir']), 5)
        self.assertEqual(found['avisados'], 5)

    def registro_sin_medir(self, total, dias):
        registro = {}
        for n in range(total):
            launch = self.ahora - timedelta(days=dias, hours=n)
            registro[f't_{n}'] = {'proyecto': 'prompted-os', 'titulo': f'Exp {n}', 'avisado': (launch - timedelta(hours=3)).isoformat(), 'lanzado': launch.isoformat()}
        equipo.save(self.state / 'experimentos.json', registro)

    def test_planner_says_how_many_unmeasured_launches_are_left_out_of_the_cap(self):
        self.registro_sin_medir(7, 2)
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('y 2 más. Pide a Owner lo medido', signals)
        self.assertEqual(signals.count('Lanzados sin resultado apuntado'), 1)

    def test_planner_does_not_add_more_when_unmeasured_launches_fit_the_cap(self):
        self.registro_sin_medir(2, 2)
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Lanzados sin resultado apuntado', signals)
        self.assertNotIn(' más. Pide a Owner', signals)

    def test_planner_says_how_many_are_left_out_without_recent_notices(self):
        self.registro_sin_medir(7, 20)
        signals = equipo.planner_signals('prompted-os')
        self.assertNotIn('Experimentos listos para lanzar', signals)
        self.assertIn('y 2 más. Pide a Owner lo medido', signals)
        self.assertEqual(signals.count('Lanzados sin resultado apuntado'), 1)

    def test_planner_signals_line_without_launches_and_with_unmeasured_ones(self):
        equipo.save(self.state / 'experimentos.json', {'t_b': {'proyecto': 'prompted-os', 'avisado': '2026-10-15T09:00:00+02:00'}})
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('1 avisados, ninguno lanzado todavía.', signals)
        self.assertNotIn('mediana None', signals)
        self.assertNotIn('Lanzados sin resultado apuntado', signals)
        equipo.save(self.state / 'experimentos.json', {
            't_a': {'proyecto': 'prompted-os', 'titulo': 'Landing A', 'avisado': '2026-10-14T08:00:00+02:00', 'lanzado': '2026-10-14T11:00:00+02:00'},
            't_e': {'proyecto': 'prompted-os', 'titulo': 'Landing B', 'avisado': '2026-10-14T08:00:00+02:00', 'lanzado': '2026-10-14T12:00:00+02:00'}})
        signals = equipo.planner_signals('prompted-os')
        self.assertIn('Lanzados sin resultado apuntado: «Landing A», «Landing B». Pide a Owner lo medido antes de proponer el siguiente experimento de esa línea.', signals)
        self.assertEqual(signals.count('Lanzados sin resultado apuntado'), 1)
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [{'texto': 'Convirtió el 7 %', 'tarea': 't_a'}, {'texto': 'Convirtió el 3 %', 'tarea': 't_e'}])
        self.assertNotIn('Lanzados sin resultado apuntado', equipo.planner_signals('prompted-os'))

    def test_status_and_brief_include_sin_medir(self):
        self.registro()
        config = {**equipo.CONFIG, 'projects': {'prompted-os': equipo.CONFIG['projects']['prompted-os']}}
        patches = [mock.patch.object(equipo, 'CONFIG', config), mock.patch.object(equipo, 'tasks', lambda pid, status=None: []),
                   mock.patch.object(equipo, 'project', lambda pid: {'name': 'Owner OS', 'goal': 'g'}), mock.patch.object(equipo, 'lessons', lambda pid: []),
                   mock.patch.object(equipo, 'ficha', lambda pid: None), mock.patch.object(equipo, 'hermes_overview', lambda: {}),
                   mock.patch.object(equipo, 'modelos_hoy', lambda: {}), mock.patch.object(equipo, 'tablero', lambda: []),
                   mock.patch.object(equipo, 'pending_merge', lambda pid: 0), mock.patch.object(equipo, 'checklist', lambda ts: [])]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)
        from_status = equipo.status()['proyectos']['prompted-os']['lanzamientos_7d']
        from_brief = equipo.project_brief('prompted-os')['lanzamientos_7d']
        for found in (from_status, from_brief):
            self.assertEqual(found['sin_medir'], [{'tarea': 't_d', 'titulo': 't_d', 'lanzado': '2026-10-05T10:00:00+02:00'}, {'tarea': 't_a', 'titulo': 't_a', 'lanzado': '2026-10-14T11:00:00+02:00'}])
            self.assertEqual((found['avisados'], found['lanzados']), (2, 1))
            self.assertEqual((found['recordados'], found['medidos_tras_recordar']), (0, 0))

    def test_planner_signals_line_only_when_there_are_notices(self):
        self.assertNotIn('Experimentos listos para lanzar', equipo.planner_signals('prompted-os'))
        self.registro()
        self.assertIn('Experimentos listos para lanzar (7 días): 2 avisados, 1 lanzados por Owner, mediana 3.0 h hasta lanzar.', equipo.planner_signals('prompted-os'))
        self.assertNotIn('Experimentos listos para lanzar', equipo.planner_signals('otro'))

class RecordarMedir(unittest.TestCase):
    """48 h after launching an experiment with no result, Owner is asked once what he measured."""
    ahora = datetime.fromisoformat('2026-10-15T12:00:00+02:00')
    ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.posted, self.sent, self.buttons = [], [], []
        self.ts = [{'id': 't_vieja', 'title': 'Landing nueva', 'body': json.dumps({'lanzamiento': self.ficha})},
                   {'id': 't_sin_ficha', 'title': 'Sin ficha', 'body': None}]
        patches = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'now', lambda: self.ahora),
                   mock.patch.object(equipo, 'tasks', lambda pid, status=None: self.ts),
                   mock.patch.object(equipo, 'post', lambda *a, **k: self.posted.append(a)), mock.patch.object(equipo, 'telegram', lambda t, botones=None: (self.sent.append(t), self.buttons.append(botones))),
                   mock.patch.object(equipo, 'log')]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)

    def registro(self, **extra):
        equipo.save(self.state / 'experimentos.json', {
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'avisado': '2026-10-13T08:00:00+02:00', 'lanzado': '2026-10-13T10:00:00+02:00'},
            't_reciente': {'proyecto': 'prompted-os', 'titulo': 'Reciente', 'avisado': '2026-10-15T00:00:00+02:00', 'lanzado': '2026-10-15T02:00:00+02:00'},
            't_medida': {'proyecto': 'prompted-os', 'titulo': 'Medida', 'avisado': '2026-10-12T08:00:00+02:00', 'lanzado': '2026-10-12T10:00:00+02:00'},
            't_hecha': {'proyecto': 'prompted-os', 'titulo': 'Hecha', 'avisado': '2026-10-12T08:00:00+02:00', 'lanzado': '2026-10-12T10:00:00+02:00',
                        'recordado': '2026-10-14T10:00:00+02:00'},
            't_sin_lanzar': {'proyecto': 'prompted-os', 'titulo': 'Sin lanzar', 'avisado': '2026-10-10T08:00:00+02:00'}, **extra})
        equipo.save(self.state / 'experimentos' / 'prompted-os.json', [{'texto': 'Convirtió el 7 %', 'tarea': 't_medida'}])

    def test_reminds_only_the_old_unmeasured_unreminded_launch_and_once(self):
        self.registro()
        self.assertEqual(equipo.recordar_medir(), ['t_vieja'])
        texto = '¿Qué has medido en «Landing nueva»? Métrica: Registros; umbral: 5 % de conversión. Cuéntaselo a Alfred y lo apunta.'
        self.assertEqual(self.sent, [texto])
        self.assertEqual(self.posted, [('alfred', ['owner'], texto, 'aviso', 'prompted-os')])
        self.assertEqual(equipo.lanzamientos()['t_vieja']['recordado'], '2026-10-15T12:00:00+02:00')
        self.assertNotIn('recordado', equipo.lanzamientos()['t_reciente'])
        self.assertEqual(equipo.recordar_medir(), [])
        self.assertEqual((len(self.sent), len(self.posted)), (1, 1))

    def test_reminder_carries_the_measure_button(self):
        self.registro()
        self.assertEqual(equipo.recordar_medir(), ['t_vieja'])
        self.assertEqual(self.buttons, [[[{'text': 'Apuntar lo medido', 'callback_data': 'm:t_vieja'}]]])

    def test_task_id_too_long_for_a_button_is_reminded_without_it(self):
        largo = 'a' * 80          # 'm:' + 80 characters is over the 64 bytes Telegram allows
        self.registro(**{largo: {'proyecto': 'prompted-os', 'titulo': 'Larga', 'lanzado': '2026-10-10T10:00:00+02:00'}})
        self.assertIn(largo, equipo.recordar_medir())
        self.assertIn('¿Qué has medido en «Larga»? Cuéntaselo a Alfred y lo apunta.', self.sent)
        self.assertEqual(self.buttons[self.sent.index('¿Qué has medido en «Larga»? Cuéntaselo a Alfred y lo apunta.')], None)

    def test_without_card_the_metric_and_threshold_are_left_out(self):
        self.registro(t_sin_ficha={'proyecto': 'prompted-os', 'titulo': 'Sin ficha', 'lanzado': '2026-10-10T10:00:00+02:00'})
        self.assertIn('t_sin_ficha', equipo.recordar_medir())
        self.assertIn('¿Qué has medido en «Sin ficha»? Cuéntaselo a Alfred y lo apunta.', self.sent)

    def test_telegram_failure_still_marks_it_and_one_failing_entry_does_not_stop_the_rest(self):
        self.registro(t_rota={'proyecto': 'prompted-os', 'lanzado': 'no es una fecha'})
        def broken(text, botones=None): raise RuntimeError('Telegram caído')
        with mock.patch.object(equipo, 'telegram', broken):
            self.assertEqual(equipo.recordar_medir(), ['t_vieja'])
        self.assertEqual(len(self.posted), 1)
        self.assertTrue(equipo.lanzamientos()['t_vieja']['recordado'])
        self.assertNotIn('recordado', equipo.lanzamientos()['t_rota'])
        self.assertEqual(equipo.recordar_medir(), [])

    def test_without_record_reminds_nothing(self):
        self.assertEqual(equipo.recordar_medir(), [])
        self.assertEqual((self.sent, self.posted), ([], []))

    def test_run_all_reminds_even_when_the_team_is_paused(self):
        root = Path(self.tmp.name) / 'root'; root.mkdir(); (root / 'PAUSED').touch()
        with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'salud') as salud, \
             mock.patch.object(equipo, 'recordar_medir') as recordar, mock.patch.object(equipo, 'answer_owner') as answer, \
             mock.patch.object(equipo, 'recordar_lanzar') as lanzar:
            equipo.run_all()
            salud.assert_called_once(); recordar.assert_called_once(); answer.assert_not_called()
            lanzar.assert_called_once()
        with mock.patch.object(equipo, 'ROOT', root), mock.patch.object(equipo, 'salud'), \
             mock.patch.object(equipo, 'recordar_medir', side_effect=RuntimeError('fallo')), \
             mock.patch.object(equipo, 'recordar_lanzar', side_effect=RuntimeError('fallo')) as lanzar:
            equipo.run_all()   # a failing reminder never stops run_all
            lanzar.assert_called_once()

class RecordarLanzar(unittest.TestCase):
    """An experiment ready for 48 h to 7 days reaches Owner once, during daytime only."""
    ahora = datetime.fromisoformat('2026-10-15T12:00:00+02:00')
    ficha = {'que': 'Landing A/B', 'a_quien': 'Tráfico frío', 'metrica': 'Registros', 'umbral': '5 % de conversión'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.posted, self.sent, self.buttons = [], [], []
        self.ts = [{'id': 't_vieja', 'title': 'Landing nueva', 'body': json.dumps({'lanzamiento': self.ficha})}]
        self.read_tasks = mock.Mock(side_effect=lambda pid: self.ts)
        patches = [mock.patch.object(equipo, 'STATE', self.state), mock.patch.object(equipo, 'now', lambda: self.ahora),
                   mock.patch.object(equipo, 'tasks', self.read_tasks),
                   mock.patch.object(equipo, 'post', lambda *a, **k: self.posted.append(a)),
                   mock.patch.object(equipo, 'telegram', lambda t, botones=None: (self.sent.append(t), self.buttons.append(botones))),
                   mock.patch.object(equipo, 'log')]
        for patch in patches: patch.start(); self.addCleanup(patch.stop)

    def registro(self, **extra):
        equipo.save(self.state / 'experimentos.json', {
            't_vieja': {'proyecto': 'prompted-os', 'titulo': 'Landing nueva', 'avisado': '2026-10-13T10:00:00+02:00'}, **extra})

    def test_reminds_only_the_old_pending_launch_and_once(self):
        self.registro()
        self.assertEqual(equipo.recordar_lanzar(), ['t_vieja'])
        texto = (f"⏳ «Landing nueva» lleva 2 días listo para lanzar en {equipo.project('prompted-os')['name']}.\n"
                 'Qué: Landing A/B\nA quién: Tráfico frío\nMétrica: Registros\nUmbral: 5 % de conversión\n'
                 '¿Lo lanzas o te frena algo?')
        self.assertEqual(self.sent, [texto])
        self.assertEqual(self.posted, [('alfred', ['owner'], texto, 'aviso', 'prompted-os')])
        self.assertEqual(equipo.lanzamientos()['t_vieja']['recordado_lanzar'], self.ahora.isoformat(timespec='seconds'))
        self.assertEqual(equipo.recordar_lanzar(), [])
        self.assertEqual((len(self.sent), len(self.posted)), (1, 1))

    def test_skips_launched_discarded_recent_expired_missing_cards_and_unknown_projects(self):
        base = {'proyecto': 'prompted-os', 'avisado': '2026-10-13T10:00:00+02:00'}
        entries = {'t_lanzada': {**base, 'lanzado': '2026-10-14T10:00:00+02:00'},
                   't_descartada': {**base, 'descartado': '2026-10-14T10:00:00+02:00'},
                   't_reciente': {**base, 'avisado': '2026-10-13T12:00:01+02:00'},
                   't_caducada': {**base, 'avisado': '2026-10-08T11:59:59+02:00'},
                   't_sin_ficha': base, 't_no_existe': base,
                   't_desconocida': {**base, 'proyecto': 'desconocido'}, 't_basura': 'roto'}
        for tid in entries:
            if tid != 't_no_existe': self.ts.append({**self.ts[0], 'id': tid})
        next(t for t in self.ts if t['id'] == 't_sin_ficha')['body'] = None
        self.registro(**entries)
        self.assertEqual(equipo.recordar_lanzar(), ['t_vieja'])
        for tid in entries:
            if isinstance(entries[tid], dict): self.assertNotIn('recordado_lanzar', equipo.lanzamientos()[tid])

    def test_reads_registry_once_and_tasks_once_per_project_at_inclusive_age_limits(self):
        self.registro(t_limite={'proyecto': 'prompted-os', 'avisado': '2026-10-13T12:00:00+02:00'},
                      t_semana={'proyecto': 'prompted-os', 'avisado': '2026-10-08T12:00:00+02:00'})
        for tid in ('t_limite', 't_semana'): self.ts.append({**self.ts[0], 'id': tid})
        with mock.patch.object(equipo, 'lanzamientos', wraps=equipo.lanzamientos) as leer:
            self.assertEqual(equipo.recordar_lanzar(), ['t_vieja', 't_limite', 't_semana'])
            leer.assert_called_once_with()
        self.read_tasks.assert_called_once_with('prompted-os')
        self.assertIn('lleva 7 días', self.sent[-1])

    def test_reminder_carries_exact_launch_and_discard_buttons(self):
        self.registro()
        equipo.recordar_lanzar()
        self.assertEqual(self.buttons, [[[{'text': 'Ya lo he lanzado', 'callback_data': 'l:t_vieja'},
                                         {'text': 'No lo lanzo', 'callback_data': 'n:t_vieja'}]]])

    def test_long_or_invalid_task_id_is_reminded_without_buttons(self):
        for tid in ('a' * 80, 'id inválido'):
            with self.subTest(tid=tid):
                self.ts.append({**self.ts[0], 'id': tid})
                self.registro(**{tid: {'proyecto': 'prompted-os', 'avisado': '2026-10-13T10:00:00+02:00'}})
                self.assertIn(tid, equipo.recordar_lanzar())
                self.assertIsNone(self.buttons[-1])

    def test_broken_date_and_telegram_failure_do_not_stop_the_rest_or_repeat(self):
        base = {'proyecto': 'prompted-os', 'avisado': '2026-10-13T10:00:00+02:00'}
        equipo.save(self.state / 'experimentos.json', {'t_rota': {**base, 'avisado': 'roto'}, 't_vieja': base, 't_otra': base})
        self.ts.append({**self.ts[0], 'id': 't_otra'})
        def broken(text, botones=None):
            self.assertTrue(equipo.lanzamientos()['t_vieja'].get('recordado_lanzar'))
            raise RuntimeError('Telegram caído')
        with mock.patch.object(equipo, 'telegram', side_effect=broken) as enviar:
            self.assertEqual(equipo.recordar_lanzar(), ['t_vieja', 't_otra'])
            self.assertEqual(enviar.call_count, 2)
        self.assertEqual(len(self.posted), 2)
        self.assertNotIn('recordado_lanzar', equipo.lanzamientos()['t_rota'])
        self.assertTrue(equipo.lanzamientos()['t_otra']['recordado_lanzar'])
        self.assertEqual(equipo.log.call_count, 3)
        self.assertEqual(equipo.recordar_lanzar(), [])

    def test_outside_window_does_not_read_or_send(self):
        self.registro()
        for hour in (8, 22):
            with self.subTest(hour=hour), mock.patch.object(equipo, 'lanzamientos') as leer:
                self.assertEqual(equipo.recordar_lanzar(at=self.ahora.replace(hour=hour)), [])
                leer.assert_not_called()
        self.read_tasks.assert_not_called()
        self.assertEqual((self.sent, self.posted), ([], []))

class LimitesCompartidos(unittest.TestCase):
    """The engine, the local server and the app must agree on the result limits and on the task id rule."""
    repo = Path(__file__).resolve().parent.parent

    def test_result_limits_match_office_limits(self):
        text = (self.repo / 'alfred-os' / 'lib' / 'office-limits.ts').read_text()
        for name in ('RESULTADO_MIN', 'RESULTADO_MAX'):
            found = re.search(rf'export const {name}\s*=\s*(\d+)', text)
            self.assertIsNotNone(found, name)
            self.assertEqual(int(found.group(1)), getattr(equipo, name), name)

    def test_por_lanzar_tope_matches_office_limits(self):
        text = (self.repo / 'alfred-os' / 'lib' / 'office-limits.ts').read_text()
        found = re.search(r'export const POR_LANZAR_TOPE\s*=\s*(\d+)', text)
        self.assertIsNotNone(found, 'POR_LANZAR_TOPE')
        self.assertEqual(int(found.group(1)), equipo.POR_LANZAR_TOPE)

    def test_decisions_match_office_limits(self):
        text = (self.repo / 'alfred-os' / 'lib' / 'office-limits.ts').read_text()
        found = re.search(r'export const DECISIONES\s*=\s*\[([^\]]*)\]', text)
        self.assertIsNotNone(found, 'no encuentro la constante DECISIONES en office-limits.ts')
        self.assertEqual(re.findall(r"'([^']*)'", found.group(1)), list(equipo.DECISIONES))

    def test_pending_merge_phase_matches_office_limits(self):
        text = (self.repo / 'alfred-os' / 'lib' / 'office-limits.ts').read_text()
        found = re.search(r"export const FASE_FALTA_FUSIONAR\s*=\s*'([^']*)'", text)
        self.assertIsNotNone(found, 'no encuentro la constante FASE_FALTA_FUSIONAR en office-limits.ts')
        self.assertEqual(found.group(1), equipo.FASE_FALTA_FUSIONAR)

    def test_task_id_rule_matches_equipo_acciones(self):
        text = (self.repo / 'mac' / 'local-workspace' / 'equipo-acciones.ts').read_text()
        self.assertIn("'decidir'", text)
        for decision in equipo.DECISIONES:
            args = equipo.parser().parse_args(['decidir', 't_036cee09', '--decision', decision])
            self.assertEqual((args.cmd, args.project, args.decision), ('decidir', 't_036cee09', decision))
        # the rule lives in experimentos-cola.ts (TAREA_ID), shared with Entregas; equipo-acciones.ts imports it as TAREA
        found = re.search(r'const TAREA=/(.+)/;', text) or re.search(r'export const TAREA_ID=/(.+)/;', (self.repo / 'alfred-os' / 'lib' / 'experimentos-cola.ts').read_text())
        self.assertIsNotNone(found)
        self.assertEqual(found.group(1), equipo.TAREA_ID.pattern)

    def test_launch_button_prefix_matches_equipo_acciones(self):
        archivo = 'mac/local-workspace/equipo-acciones.ts'
        found = re.search(r"export const BOTON_LANZADO\s*=\s*'([^']*)'", (self.repo / archivo).read_text())
        self.assertIsNotNone(found, f'no encuentro la constante BOTON_LANZADO en {archivo}')
        self.assertEqual(found.group(1), equipo.BOTON_LANZADO)

    def test_measure_button_prefix_matches_equipo_acciones(self):
        archivo = 'mac/local-workspace/equipo-acciones.ts'
        found = re.search(r"export const BOTON_MEDIDO\s*=\s*'([^']*)'", (self.repo / archivo).read_text())
        self.assertIsNotNone(found, f'no encuentro la constante BOTON_MEDIDO en {archivo}')
        self.assertEqual(found.group(1), equipo.BOTON_MEDIDO)

    def test_no_launch_button_prefix_matches_equipo_acciones(self):
        archivo = 'mac/local-workspace/equipo-acciones.ts'
        found = re.search(r"export const BOTON_NO_LANZO\s*=\s*'([^']*)'", (self.repo / archivo).read_text())
        self.assertIsNotNone(found, f'no encuentro la constante BOTON_NO_LANZO en {archivo}')
        self.assertEqual(found.group(1), equipo.BOTON_NO_LANZO)

    def test_merge_button_matches_equipo_acciones(self):
        archivo = 'mac/local-workspace/equipo-acciones.ts'
        text = (self.repo / archivo).read_text()
        boton = re.search(r"export const BOTON_FUSIONAR\s*=\s*'([^']*)'", text)
        project = re.search(r'export const PROJECT=/(.+)/;', text)
        self.assertIsNotNone(boton, f'no encuentro la constante BOTON_FUSIONAR en {archivo}')
        self.assertIsNotNone(project, f'no encuentro la constante PROJECT en {archivo}')
        self.assertEqual(boton.group(1), equipo.BOTON_FUSIONAR)
        self.assertEqual(project.group(1), equipo.PROYECTO_ID.pattern)
        for pid in ('prompted-os', 'norte', 'beta'):
            callback = equipo.BOTON_FUSIONAR + pid
            self.assertLessEqual(len(callback.encode()), 64)
            self.assertTrue(equipo.PROYECTO_ID.fullmatch(pid), pid)

    def test_task_id_edge_cases(self):
        for tid in ('abc', 'a' * 80): self.assertTrue(equipo.TAREA_ID.fullmatch(tid), tid)
        for tid in ('ab', 'a' * 81, '-abc', 'a b c'): self.assertFalse(equipo.TAREA_ID.fullmatch(tid), tid)

    def test_result_length_edges(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)):
            with self.assertRaises(equipo.Stop): equipo.registrar_resultado('prompted-os', 'x' * (equipo.RESULTADO_MIN - 1))
            self.assertFalse((Path(tmp) / 'experimentos').exists())
            self.assertEqual(len(equipo.registrar_resultado('prompted-os', 'x' * equipo.RESULTADO_MIN)['texto']), equipo.RESULTADO_MIN)
            self.assertEqual(len(equipo.registrar_resultado('prompted-os', 'x' * 700)['texto']), equipo.RESULTADO_MAX)
            self.assertEqual([len(r['texto']) for r in equipo.resultados('prompted-os')], [equipo.RESULTADO_MIN, equipo.RESULTADO_MAX])

if __name__ == '__main__':
    unittest.main()


class ContinuousRounds(unittest.TestCase):
    """A finished batch (every card done or blocked) leads to the next round the same day, within the daily cap."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name) / 'state'; self.root = Path(self.tmp.name)
        self.status = {'t1': 'done', 't2': 'blocked'}
        self.notes, self.approved = [], []
        cfg = {**equipo.CONFIG, 'continuo': True, 'projects': {**equipo.CONFIG['projects']}}
        cfg['projects']['norte'] = {**cfg['projects']['norte'], 'continuo': True, 'auto_aprobar': False}
        for target, value in (('STATE', self.state), ('ROOT', self.root), ('CONFIG', cfg),
                              ('LIMITS', {**equipo.LIMITS, 'rounds_per_day': 3}),
                              ('tasks', lambda pid, status=None: [{'id': k, 'status': v, 'title': k} for k, v in self.status.items()]),
                              ('notify', lambda title, text: self.notes.append(title)),
                              ('approve', lambda pids, *a: self.approved.append(pids))):
            p = mock.patch.object(equipo, target, value); p.start(); self.addCleanup(p.stop)

    def batch(self, **entry):
        equipo.save(self.state / 'proposals' / f'{equipo.today()}.json',
                    {'fecha': equipo.today(), 'proyectos': {'norte': {'estado': 'aprobado', 'tarjetas': {'0': 't1', '1': 't2'}, **entry}}})

    def planned(self, estado='pendiente'):
        calls = []
        def fake(pid, batch, file, ronda=1, waiting=()):
            calls.append(ronda); entry = {'estado': estado, 'tareas': [{'titulo': 'Siguiente paso', 'depende_de': []}], 'ronda': ronda}
            batch['proyectos'][pid] = entry; equipo.save(file, batch); return entry
        return calls, mock.patch.object(equipo, 'plan_project', fake)

    def test_unfinished_round_waits(self):
        self.status['t2'] = 'running'; self.batch()
        calls, patch = self.planned()
        with patch: self.assertIsNone(equipo.next_round('norte'))
        self.assertEqual(calls, [])

    def test_finished_round_plans_the_next_and_asks_owner(self):
        self.batch()
        calls, patch = self.planned()
        with patch: self.assertEqual(equipo.next_round('norte'), 'pendiente')
        self.assertEqual(calls, [2]); self.assertEqual(self.approved, []); self.assertIn('ronda 2', self.notes[0])

    def test_waiting_important_task_clears_empty_plan_reason(self):
        self.batch(estado='pendiente', tareas=[{'titulo': 'Importante', 'depende_de': [], 'importante': 'Decide Owner'}])
        def fake(pid, batch, file, ronda=1, waiting=()):
            entry = {'estado': 'sin propuestas', 'motivo': 'todo lo propuesto ya estaba en el tablero o repetido (2)',
                     'tareas': [], 'ronda': ronda}
            batch['proyectos'][pid] = entry; equipo.save(file, batch); return entry
        with mock.patch.object(equipo, 'plan_project', fake):
            self.assertEqual(equipo.next_round('norte'), 'pendiente')
        entry = equipo.load(self.state / 'proposals' / f'{equipo.today()}.json', None)['proyectos']['norte']
        self.assertEqual(entry['estado'], 'pendiente')
        self.assertNotIn('motivo', entry)
        self.assertEqual(entry['tareas'][0]['titulo'], 'Importante')
        self.assertIn('importante', entry['tareas'][0])

    def test_auto_approval_starts_it(self):
        equipo.CONFIG['projects']['norte']['auto_aprobar'] = True; self.batch(ronda=2)
        calls, patch = self.planned()
        held = []
        with patch, mock.patch.object(equipo, '_approve', lambda pids, hold=None, by='': held.append((pids, hold, by))):
            self.assertEqual(equipo.next_round('norte'), 'aprobada')
        self.assertEqual((calls, held), ([3], [(['norte'], set(), 'equipo')]))

    def test_failed_plan_is_retried_once_on_its_own(self):
        self.batch(estado='error', motivo='titulo', ronda=3)
        calls, patch = self.planned('error')
        with patch:
            self.assertEqual(equipo.next_round('norte'), 'error')
            self.assertIsNone(equipo.next_round('norte'))                        # no retry loop
        self.assertEqual(calls, [3])

    def test_relaunch_keeps_the_round_and_asks_owner(self):
        self.batch(estado='error', motivo='titulo', ronda=2)
        calls, patch = self.planned()
        with patch: self.assertEqual(equipo.retry_round('norte'), 'pendiente')
        self.assertEqual(calls, [2]); self.assertIn('ronda 2', self.notes[0])
        with patch: self.assertIsNone(equipo.retry_round('norte'))               # nothing failed any more

    def test_daily_cap_and_off_switches(self):
        self.batch(ronda=3)
        calls, patch = self.planned()
        with patch:
            self.assertIsNone(equipo.next_round('norte')); self.assertIsNone(equipo.next_round('norte'))
        self.assertEqual(calls, []); self.assertEqual(len(self.notes), 1)          # cap notice sent once
        self.batch()
        (self.root / 'PAUSED').write_text('x')
        with patch: self.assertIsNone(equipo.next_round('norte'))
        (self.root / 'PAUSED').unlink(); equipo.CONFIG['projects']['norte']['continuo'] = False
        with patch: self.assertIsNone(equipo.next_round('norte'))
        self.assertEqual(calls, [])

    def test_parallel_planners_do_not_overwrite_each_other(self):
        """Regression: two projects planning in separate processes each saved a stale copy and one round vanished."""
        file = self.state / 'proposals' / f'{equipo.today()}.json'
        equipo.save(file, {'fecha': equipo.today(), 'proyectos': {}})
        stale_a, stale_b = equipo.load(file, None), equipo.load(file, None)     # both read before either writes
        with mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', lambda *a, **k: self.root), \
             mock.patch.object(equipo, 'context_text', lambda *a: ''), mock.patch.object(equipo, 'planner_signals', lambda pid: ''), \
             mock.patch.object(equipo, 'claude', lambda *a, **k: '```json\n[]\n```'):
            equipo.plan_project('norte', stale_a, file, 2)
            equipo.plan_project('beta', stale_b, file, 2)
        self.assertEqual(set(equipo.load(file, None)['proyectos']), {'norte', 'beta'})


class ImportantTasks(unittest.TestCase):
    """Routine work runs on its own; Owner only decides what is marked important (or touches sensitive files)."""
    base = {'titulo': 'Una tarea', 'porque': 'x', 'detalle': 'Criterios de aceptación concretos', 'pruebas': ['unitarias'], 'depende_de': []}

    def test_launch_details_are_optional_and_normalized(self):
        ficha = {'que': 'Experimento', 'a_quien': 'Clientes', 'metrica': 'Respuestas', 'umbral': 'Seguir con 5 respuestas'}
        v = lambda **k: equipo.validate_task('norte', {**self.base, 'archivos': ['lib/engine.js'], **k}, 0)
        complete = {k: f'  {value}  ' for k, value in ficha.items()}
        out = v(lanzamiento={**complete, 'extra': 'Se descarta'})
        self.assertEqual(out['lanzamiento'], ficha)
        self.assertNotIn('importante', out)
        self.assertNotIn('lanzamiento', v())
        for key in ficha:
            for bad in ({k: value for k, value in ficha.items() if k != key},
                        {**ficha, key: ' \t\n'}, {**ficha, key: 123}):
                with self.subTest(key=key, lanzamiento=bad):
                    out = v(lanzamiento=bad)
                    self.assertNotIn('lanzamiento', out)
                    self.assertEqual(out['titulo'], self.base['titulo'])
            with self.subTest(long_value=key):
                self.assertEqual(v(lanzamiento={**ficha, key: '  ' + 'x' * 300 + '  '})['lanzamiento'][key], 'x' * 200)
        out = v(lanzamiento='Experimento')
        self.assertNotIn('lanzamiento', out)
        self.assertEqual(out['titulo'], self.base['titulo'])

    def test_plan_project_preserves_launch_details_in_proposals(self):
        ficha = {'que': 'Experimento', 'a_quien': 'Clientes', 'metrica': 'Respuestas', 'umbral': 'Seguir con 5 respuestas'}
        tarea = {**self.base, 'archivos': ['lib/engine.js'], 'lanzamiento': ficha}
        p = {**equipo.project('norte'), 'related_boards': []}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); file = state / 'proposals' / f'{equipo.today()}.json'
            batch = {'fecha': equipo.today(), 'proyectos': {}}
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'project', return_value=p), \
                 mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', lambda *a, **k: state), \
                 mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
                 mock.patch.object(equipo, 'context_text', lambda *a: ''), mock.patch.object(equipo, 'planner_signals', lambda pid: ''), \
                 mock.patch.object(equipo, 'claude', return_value='```json\n' + json.dumps([tarea]) + '\n```'):
                equipo.plan_project('norte', batch, file)
            entry = equipo.load(file, None)['proyectos']['norte']
            self.assertEqual(entry['estado'], 'pendiente')
            self.assertEqual(len(entry['tareas']), 1)
            self.assertEqual(entry['tareas'][0]['lanzamiento'], ficha)

    def test_new_npm_test_adds_package_json_to_the_files(self):
        v = lambda files, pid='beta', root=None, **k: equipo.validate_task(pid, {**self.base, 'archivos': files, **k}, 0, root=root)
        nueva = 'test/nuevo.test.mjs'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            propuesta = ['lib/x.js', nueva]
            out = v(propuesta, root=root)                                       # (a) new test, npm project
            self.assertEqual(out['archivos'], ['lib/x.js', nueva, 'package.json'])
            self.assertEqual(propuesta, ['lib/x.js', nueva])                    # the proposal's own list is untouched
            self.assertIn('package.json', out['importante'])
            self.assertTrue(out['detalle'].endswith(' Incluye package.json para que npm test ejecute la prueba nueva.'))
            largo = v(propuesta, root=root, detalle='x' * 5000)['detalle']
            self.assertEqual(len(largo), 4000); self.assertTrue(largo.endswith('ejecute la prueba nueva.'))
            self.assertEqual(v(propuesta)['archivos'], propuesta)               # (c) no root
            self.assertNotIn('importante', v(propuesta))
            self.assertEqual(v(['package.json', nueva], root=root)['archivos'], ['package.json', nueva])   # (d)
            self.assertNotIn('Incluye package.json', v(['package.json', nueva], root=root)['detalle'])
            with self.assertRaises(AssertionError) as caught:                   # (e) already at the maximum
                v([f'lib/{i}.js' for i in range(equipo.LIMITS['max_files'] - 1)] + [nueva], root=root)
            self.assertIn('package.json', str(caught.exception))
            self.assertEqual(v(['lib/x.js', 'test/a/b.test.mjs'], root=root)['archivos'], ['lib/x.js', 'test/a/b.test.mjs'])
            self.assertEqual(v(['lib/x.js', 'test/nuevo.test.js'], root=root)['archivos'], ['lib/x.js', 'test/nuevo.test.js'])
            (root / 'test').mkdir(); (root / nueva).write_text('x')             # (b) the test already exists
            out = v(propuesta, root=root)
            self.assertEqual(out['archivos'], propuesta)
            self.assertNotIn('importante', out)
            # (f) a project without npm tests is left alone
            out = v(['equipo/x.py', 'test/x.test.mjs'], pid='prompted-os', root=Path(tmp) / 'otro', pruebas=['equipo'])
            self.assertEqual(out['archivos'], ['equipo/x.py', 'test/x.test.mjs'])

    def test_plan_project_adds_package_json_for_a_new_npm_test(self):
        tarea = {**self.base, 'archivos': ['lib/x.js', 'test/nuevo.test.mjs']}
        p = {**equipo.project('beta'), 'related_boards': []}
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); file = state / 'proposals' / f'{equipo.today()}.json'
            batch = {'fecha': equipo.today(), 'proyectos': {}}
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'project', return_value=p), \
                 mock.patch.object(equipo, 'git', lambda *a, **k: 'abc'), mock.patch.object(equipo, 'worktree', lambda *a, **k: state), \
                 mock.patch.object(equipo, 'tasks', return_value=[]), mock.patch.object(equipo, 'log'), \
                 mock.patch.object(equipo, 'context_text', lambda *a: ''), mock.patch.object(equipo, 'planner_signals', lambda pid: ''), \
                 mock.patch.object(equipo, 'claude', return_value='```json\n' + json.dumps([tarea]) + '\n```'):
                equipo.plan_project('beta', batch, file)
            entry = equipo.load(file, None)['proyectos']['beta']
            self.assertEqual(entry['estado'], 'pendiente')
            self.assertIn('package.json', entry['tareas'][0]['archivos'])
            self.assertIn('importante', entry['tareas'][0])

    def test_sensitive_files_always_wait_and_tests_never_do(self):
        v = lambda files, **k: equipo.validate_task('norte', {**self.base, 'archivos': files, **k}, 0)
        self.assertNotIn('importante', v(['lib/engine.js', 'test/auth.test.js']))
        self.assertIn('package.json', v(['package.json'])['importante'])
        self.assertIn('auth', v(['lib/auth.js'])['importante'])
        self.assertIn('importante', v(['lib/engine.js'], importante=True, motivo_importante='Cambia el precio'))
        self.assertNotIn('importante', v(['lib/engine.js'], importante='sí'))       # only a real true counts

    def test_held_positions_follow_dependencies(self):
        t = lambda imp=False, deps=(): {'importante': 'x' if imp else None, 'depende_de': list(deps)}
        self.assertEqual(equipo.held_positions([t(), t(True), t(deps=[1]), t(deps=[0])]), {1, 2})

    def test_batch_records_owner_decisions_from_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); start = equipo.now().replace(microsecond=0); date = start.date().isoformat()
            tareas = [{**self.base, 'titulo': 'Con ficha', 'lanzamiento': {'que': 'x'}},
                      {**self.base, 'titulo': 'Sin ficha'}]
            equipo.save(state / 'proposals' / f'{date}.json', {'fecha': date, 'proyectos': {'norte': {
                'estado': 'pendiente', 'creado': (start - timedelta(minutes=25)).isoformat(), 'tareas': tareas}}})
            @__import__('contextlib').contextmanager
            def fake_board(pid): yield None
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'now', return_value=start), \
                 mock.patch.object(equipo, 'board', fake_board), mock.patch.object(equipo.kb, 'create_task', return_value='t1'), \
                 mock.patch.object(equipo, 'log'):
                equipo._approve(['norte'], only=[0])
                decisions = equipo.load(state / 'senales' / 'norte.json', {})['decisiones']
                self.assertEqual(decisions, [
                    {'fecha': date, 'titulo': 'Con ficha', 'decision': 'aprobada', 'minutos': 25.0, 'ficha': True},
                    {'fecha': date, 'titulo': 'Sin ficha', 'decision': 'descartada', 'minutos': 25.0, 'ficha': False}])
                m = equipo.metrics('norte')
                self.assertEqual(m['decisiones_tuyas'], 2)
                self.assertEqual(m['decisiones_con_ficha'], 1)

    def test_batch_auto_approval_and_missing_dates_record_no_decisions(self):
        for by in ('equipo', 'Owner'):
            with self.subTest(by=by), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp); start = equipo.now().replace(microsecond=0); date = start.date().isoformat()
                entry = {'estado': 'pendiente', 'tareas': [dict(self.base)]}
                if by == 'equipo': entry['creado'] = (start - timedelta(minutes=25)).isoformat()
                equipo.save(state / 'proposals' / f'{date}.json', {'fecha': date, 'proyectos': {'norte': entry}})
                @__import__('contextlib').contextmanager
                def fake_board(pid): yield None
                with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'now', return_value=start), \
                     mock.patch.object(equipo, 'board', fake_board), mock.patch.object(equipo.kb, 'create_task', return_value='t1'), \
                     mock.patch.object(equipo, 'log'):
                    equipo._approve(['norte'], by=by)
                self.assertFalse((state / 'senales' / 'norte.json').exists())

    def test_batch_decision_dates_fallback_and_never_go_negative(self):
        start = equipo.now().replace(microsecond=0)
        created = (start - timedelta(minutes=25)).isoformat()
        for retained, proposed, expected in (
                ((start - timedelta(seconds=92)).isoformat(), created, 1.5),
                ('invalid', created, 25.0), (123, created, 25.0),
                ((start + timedelta(minutes=5)).isoformat(), created, 0.0),
                ('invalid', 'invalid', None), (123, 123, None)):
            with self.subTest(retained=retained, proposed=proposed), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp); date = start.date().isoformat()
                entry = {'estado': 'pendiente', 'creado': proposed, 'tareas': [{**self.base, 'retenida': retained}]}
                equipo.save(state / 'proposals' / f'{date}.json', {'fecha': date, 'proyectos': {'norte': entry}})
                with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'now', return_value=start), \
                     mock.patch.object(equipo, 'log'):
                    equipo._approve(['norte'], only=[])
                decisions = equipo.load(state / 'senales' / 'norte.json', {}).get('decisiones', [])
                self.assertEqual([d['minutos'] for d in decisions], [] if expected is None else [expected])

    def test_held_tasks_record_owner_decisions_and_preserve_discards(self):
        for only in (None, [0], []):
            with self.subTest(only=only), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp); date = equipo.today(); file = state / 'proposals' / f'{date}.json'
                tareas = [{**self.base, 'titulo': f'Tarea {i}', 'archivos': ['lib/a.js']} for i in range(3)]
                equipo.save(file, {'fecha': date, 'proyectos': {'norte': {'estado': 'pendiente', 'tareas': tareas}}})
                made = []
                @__import__('contextlib').contextmanager
                def fake_board(pid): yield None
                def create(conn, title, **k): made.append(title); return f't{len(made)}'
                start = equipo.now().replace(microsecond=0)
                with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'board', fake_board), \
                     mock.patch.object(equipo.kb, 'create_task', create), mock.patch.object(equipo, 'now', return_value=start) as clock:
                    equipo._approve(['norte'], date=date, hold={1}, by='equipo')
                    entry = equipo.load(file, None)['proyectos']['norte']
                    self.assertEqual(entry['tareas'][0]['retenida'], start.isoformat(timespec='seconds'))
                    self.assertEqual([i for i, t in enumerate(entry['todas']) if 'retenida' in t], [1])
                    self.assertEqual(made, ['Tarea 0', 'Tarea 2'])
                    signals_file = state / 'senales' / 'norte.json'
                    self.assertFalse(signals_file.exists())
                    clock.return_value = start + equipo.timedelta(minutes=10)
                    equipo._approve(['norte'], date=date, hold={0}, by='equipo')
                    self.assertEqual(equipo.load(file, None)['proyectos']['norte']['tareas'][0]['retenida'], start.isoformat(timespec='seconds'))
                    equipo._approve(['norte'], date=date, only=only)
                    signals = equipo.load(signals_file, None)
                    decision = signals['decisiones'][0]
                    self.assertEqual(decision, {'fecha': date, 'titulo': 'Tarea 1',
                                               'decision': 'descartada' if only == [] else 'aprobada', 'minutos': 10.0, 'ficha': False})
                    self.assertGreaterEqual(decision['minutos'], 0)
                    self.assertEqual([t['titulo'] for t in signals['descartadas']], ['Tarea 1'] if only == [] else [])
                    self.assertEqual(made, ['Tarea 0', 'Tarea 2'] if only == [] else ['Tarea 0', 'Tarea 2', 'Tarea 1'])
                    equipo._approve(['norte'], date=date, only=only)
                    self.assertEqual(equipo.load(signals_file, None), signals)

    def test_held_task_with_launch_card_records_ficha(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); date = equipo.today(); file = state / 'proposals' / f'{date}.json'
            tareas = [{**self.base, 'titulo': 'Con ficha', 'archivos': ['lib/a.js'], 'lanzamiento': {'que': 'x'}}]
            equipo.save(file, {'fecha': date, 'proyectos': {'norte': {'estado': 'pendiente', 'tareas': tareas}}})
            start = equipo.now().replace(microsecond=0)
            @__import__('contextlib').contextmanager
            def fake_board(pid): yield None
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'board', fake_board), \
                 mock.patch.object(equipo.kb, 'create_task', lambda conn, title, **k: 't1'), mock.patch.object(equipo, 'now', return_value=start):
                equipo._approve(['norte'], date=date, hold={0}, by='equipo')
                equipo._approve(['norte'], date=date)
            decision = equipo.load(state / 'senales' / 'norte.json', None)['decisiones'][0]
            self.assertEqual((decision['titulo'], decision['ficha']), ('Con ficha', True))

    def test_partial_approval_creates_routine_cards_and_keeps_the_rest_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); file = state / 'proposals' / f'{equipo.today()}.json'
            tareas = [{**self.base, 'titulo': f'Tarea {i}', 'archivos': ['lib/a.js'], **({'importante': 'Toca precios'} if i == 1 else {})} for i in range(3)]
            equipo.save(file, {'fecha': equipo.today(), 'proyectos': {'norte': {'estado': 'pendiente', 'ronda': 2, 'tareas': tareas}}})
            made = []
            @__import__('contextlib').contextmanager
            def fake_board(pid): yield None
            def create(conn, title, body, **k): made.append((title, json.loads(body)['autorizacion'])); return f't{len(made)}'
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'board', fake_board), \
                 mock.patch.object(equipo.kb, 'create_task', create), mock.patch.object(equipo, 'remember_discarded', lambda *a: None):
                equipo._approve(['norte'], hold={1}, by='equipo')
                entry = equipo.load(file, None)['proyectos']['norte']
                self.assertEqual([m[0] for m in made], ['Tarea 0', 'Tarea 2'])
                self.assertIn('el equipo', made[0][1])
                self.assertEqual((entry['estado'], [t['titulo'] for t in entry['tareas']], entry['indices']), ('pendiente', ['Tarea 1'], [1]))
                equipo._approve(['norte'])                                            # Owner approves the important one later
                entry = equipo.load(file, None)['proyectos']['norte']
                self.assertEqual((made[-1], entry['estado'], len(entry['tareas']), entry['tarjetas']),
                                 (('Tarea 1', made[-1][1]), 'aprobado', 3, {'0': 't1', '1': 't3', '2': 't2'}))
                self.assertIn('Owner', made[-1][1])


class FailedStart(unittest.TestCase):
    """A test that fails because the demo server did not start runs once more before it costs an attempt."""
    def run_tests(self, outputs, calls, test=None):
        queue = list(outputs); ticks = [0]
        def run(*a, **k):
            calls.append(k.get('out')); return queue.pop(0)
        def clock():
            ticks[0] += 1; return ticks[0]
        with mock.patch.object(equipo, 'sleep_clock', clock), \
             mock.patch.object(equipo, 'run', run), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'demo': test or {'cmd': ['node', '--test']}}}):
            return equipo.run_tests('norte', {'pruebas': ['demo']}, Path('/tmp'), Path('/tmp/x'), None)

    def test_a_failed_start_is_repeated_and_the_repeat_counts(self):
        calls = []
        results = self.run_tests([(1, 'server did not become healthy'), (0, 'ok')], calls)
        self.assertEqual((results[0]['ok'], results[0]['repetida'], len(calls)), (True, True, 2))
        self.assertEqual(results[0]['repetida_por'], 'arranque')
        self.assertEqual([c.name for c in calls], ['test-demo-0.log', 'test-demo-0-r.log'])

    def test_two_failed_starts_are_a_normal_failure(self):
        calls = []
        results = self.run_tests([(1, 'did not become healthy'), (1, 'EADDRINUSE')], calls)
        self.assertFalse(results[0]['ok'])
        self.assertIn('fallo', results[0])
        self.assertEqual(len(calls), 2)

    def test_a_normal_failure_is_not_repeated(self):
        calls = []
        results = self.run_tests([(1, 'expected 2')], calls)
        self.assertFalse(results[0]['ok'])
        self.assertNotIn('repetida', results[0])
        self.assertNotIn('repetida_por', results[0])
        self.assertEqual(len(calls), 1)

    def test_only_the_failed_step_decides_the_repeat(self):
        calls = []
        results = self.run_tests([(0, 'did not become healthy'), (1, 'expected 2')], calls, {'steps': [['a'], ['b']]})
        self.assertFalse(results[0]['ok'])
        self.assertNotIn('repetida', results[0])
        self.assertEqual(len(calls), 2)

    def test_attempts_does_not_spend_an_attempt_on_a_failed_start(self):
        prompts, calls = [], []
        queue = [(1, 'did not become healthy'), (0, 'ok')]
        def run(*a, **kw):
            calls.append(a); return queue.pop(0)
        def claude(*a, **kw):
            prompts.append(a[0]); return 'hecho'
        def git(path, *args, **kw):
            raise equipo.Stop('revisión')                      # the tests passed: the next step is the review
        spec = {'titulo': 'x', 'archivos': ['alfred-os/app/board.tsx'], 'pruebas': ['demo']}
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.dict(equipo.LIMITS, {'attempts': 2}), \
             mock.patch.object(equipo, 'claude', claude), \
             mock.patch.object(equipo, 'run', run), \
             mock.patch.object(equipo, 'git', git), \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'demo': {'cmd': ['node', '--test']}}}), \
             mock.patch.object(equipo, 'sleep_clock', lambda: 0), \
             mock.patch.object(equipo, 'changed', lambda *a, **kw: (['alfred-os/app/board.tsx'], [])), \
             mock.patch.object(equipo, 'phase', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'lessons_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'context_text', lambda *a, **kw: ''), \
             mock.patch.object(equipo, 'discard', lambda *a, **kw: None), \
             mock.patch.object(equipo, 'log', lambda *a, **kw: None):
            with self.assertRaises(equipo.Stop) as ctx:
                equipo.attempts('prompted-os', {'name': 'Owner OS'}, spec, {'id': 't_1'}, 'equipo/prompted-os', 'b', Path(tmp), Path(tmp), {'ciclos': []}, None)
        self.assertEqual((len(prompts), len(calls), str(ctx.exception)), (1, 2, 'revisión'))
        self.assertNotIn('las pruebas fallaron', prompts[0])


class Asleep(unittest.TestCase):
    """15/10: on battery the Mac slept mid-test and six Norte tasks were blocked for failures that were only sleep."""
    def run_tests(self, clock, code):
        ticks = iter(clock)
        with mock.patch.object(equipo, 'sleep_clock', lambda: next(ticks)), \
             mock.patch.object(equipo, 'run', lambda *a, **k: (code, 'test timed out after 30000ms')), \
             mock.patch.object(equipo, 'project', lambda pid: {'tests': {'unitarias': {'cmd': ['node', '--test']}}}):
            return equipo.run_tests('norte', {'pruebas': ['unitarias']}, Path('/tmp'), Path('/tmp/x'), None)

    def test_a_test_frozen_by_sleep_is_not_a_failure(self):
        with self.assertRaises(equipo.Asleep): self.run_tests([0, 900], 1)

    def test_a_real_failure_while_awake_still_fails(self):
        self.assertFalse(self.run_tests([0, 3], 1)[0]['ok'])
        self.assertTrue(self.run_tests([0, 900], 0)[0]['ok'])                     # slept but passed: nothing to undo

    @unittest.skipUnless(sys.platform == 'darwin', 'la tapa solo se mira en macOS')
    def test_no_new_work_with_the_lid_closed_unless_a_display_keeps_it_awake(self):
        def lid(closed, sleeps):
            out = f'  "AppleClamshellCausesSleep" = {sleeps}\n  "AppleClamshellState" = {closed}\n'
            with mock.patch.object(equipo.subprocess, 'run', return_value=mock.Mock(stdout=out)): return equipo.lid_will_sleep()
        self.assertEqual([lid('Yes', 'Yes'), lid('Yes', 'No'), lid('No', 'Yes')], [True, False, False])

    def test_requeued_with_an_interrupted_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); folder = state / 'runs' / 't_1'; folder.mkdir(parents=True)
            equipo.save(folder / 'receipt.json', {'tarea': 't_1', 'estado': 'bloqueada', 'ciclos': [{'pruebas': [{'ok': False, 'fallo': 'x'}]}]})
            reclaimed = []
            @equipo.contextmanager
            def board(pid): yield 'conn'
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'board', board), \
                 mock.patch.object(equipo.kb, 'reclaim_task', lambda conn, tid, **k: reclaimed.append(tid) or True):
                self.assertEqual(equipo.requeue_asleep('norte', {'id': 't_1'}, 'reposo'), 'interrumpida')
            receipt = equipo.load(folder / 'receipt.json', {})
            self.assertEqual((reclaimed, receipt['estado'], equipo.relaunch_note(receipt)), (['t_1'], 'interrumpida', ''))

    def test_owner_requeues_without_the_old_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); folder = state / 'runs' / 't_1'; folder.mkdir(parents=True)
            equipo.save(folder / 'receipt.json', {'estado': 'bloqueada', 'error': 'test timed out', 'ciclos': [{'pruebas': [{'ok': False, 'fallo': 'x'}]}]})
            unblocked, archived = [], []
            @equipo.contextmanager
            def board(pid): yield 'conn'
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'board', board), \
                 mock.patch.object(equipo.kb, 'unblock_task', lambda conn, tid: unblocked.append(tid) or True), \
                 mock.patch.object(equipo.kb, 'archive_task', lambda conn, tid, **k: archived.append(tid) or True):
                self.assertEqual(equipo.desbloquear('norte', ['t_1'], limpio=True), {'t_1': 'a la cola'})
                self.assertEqual(equipo.archivar('norte', ['t_2']), {'t_2': 'archivada'})
            self.assertEqual((unblocked, archived, equipo.relaunch_note(equipo.load(folder / 'receipt.json', {}))), (['t_1'], ['t_2'], ''))


    def test_a_task_hermes_parked_in_triage_goes_back_to_the_queue(self):
        moved = []
        @equipo.contextmanager
        def board(pid): yield 'conn'
        with mock.patch.object(equipo, 'board', board), mock.patch.object(equipo.kb, 'unblock_task', lambda conn, tid: False), \
             mock.patch.object(equipo.kb, 'specify_triage_task', lambda conn, tid, **k: moved.append(('todo', tid)) or tid == 't_triage', create=True), \
             mock.patch.object(equipo.kb, 'promote_task', lambda conn, tid, **k: (moved.append(('ready', tid)) or True, None), create=True), \
             mock.patch.object(equipo.kb, '_task_status', lambda conn, tid: 'todo', create=True), \
             mock.patch.object(equipo, 'log', lambda *a, **k: None):
            self.assertEqual(equipo.desbloquear('norte', ['t_triage', 't_hecha']), {'t_triage': 'a la cola', 't_hecha': 'no estaba bloqueada'})
        self.assertEqual(moved, [('todo', 't_triage'), ('ready', 't_triage'), ('todo', 't_hecha')])

    def test_archivar_says_what_happened_to_each_task(self):
        # the app reads these literals from the printed dict (bloqueadaResultado in equipo-acciones.ts)
        archived = []
        @equipo.contextmanager
        def board(pid): yield 'conn'
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(equipo, 'STATE', Path(tmp)), mock.patch.object(equipo, 'board', board), \
             mock.patch.object(equipo.kb, 'archive_task', lambda conn, tid, **k: archived.append(tid) or tid == 't_a'), \
             mock.patch.object(equipo, 'log', lambda *a, **k: None):
            self.assertEqual(equipo.archivar('norte', ['t_a', 't_b']), {'t_a': 'archivada', 't_b': 'no se ha podido'})
        self.assertEqual(archived, ['t_a', 't_b'])

class RecordarDecisiones(unittest.TestCase):
    """15/10: a batch that keeps a team idle for hours reaches Owner once, with how to approve it."""
    def run_at(self, hour, created_hours_ago, statuses, sent_before=None):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp); at = equipo.now().replace(hour=hour, minute=0, second=0, microsecond=0)
            (state / 'proposals').mkdir(parents=True)
            equipo.save(state / 'proposals' / f'{equipo.today()}.json', {'proyectos': {'norte': {
                'estado': 'pendiente', 'creado': (at - equipo.timedelta(hours=created_hours_ago)).isoformat(), 'tareas': [{}, {}]}}})
            if sent_before: equipo.save(state / 'recordatorios.json', sent_before)
            notes = []
            with mock.patch.object(equipo, 'STATE', state), mock.patch.object(equipo, 'notify', lambda t, x: notes.append(x)), \
                 mock.patch.object(equipo, 'tasks', lambda pid, *a: [{'status': s} for s in statuses]):
                first = equipo.recordar_decisiones(at); again = equipo.recordar_decisiones(at)
            return first, again, notes

    def test_reminds_once_when_the_team_is_idle(self):
        first, again, notes = self.run_at(17, 4, ['done', 'blocked'])
        self.assertEqual((first, again, len(notes)), (['norte'], [], 1))
        self.assertIn('2 tareas', notes[0]); self.assertIn('4 h', notes[0]); self.assertIn('/lote', notes[0])

    def test_no_reminder_too_soon_at_night_or_while_the_team_works(self):
        self.assertEqual(self.run_at(17, 1, ['done'])[0], [])                 # waiting only one hour
        self.assertEqual(self.run_at(22, 6, ['done'])[0], [])                 # night
        self.assertEqual(self.run_at(17, 6, ['running'])[0], [])              # the team still has work
        self.assertEqual(self.run_at(17, 6, ['done'], {f'{equipo.today()}:norte': 'x'})[0], [])   # already reminded today
