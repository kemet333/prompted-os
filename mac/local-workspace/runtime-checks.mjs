import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import { randomUUID, createHash } from 'node:crypto';
import { spawn, execFileSync } from 'node:child_process';
import http from 'node:http';
import { DatabaseSync } from 'node:sqlite';
import { openDatabase } from './database.mjs';

const localRoot = path.dirname(fileURLToPath(import.meta.url));
// Two projects of this copy's built-in portfolio (the owner's or the open-source example), so the fixtures are valid in both.
const [P1, P2] = [...fs.readFileSync(path.resolve(localRoot, '../../alfred-os/lib/alfred-data.ts'), 'utf8').matchAll(/\{id:'([\w-]+)',division:/g)].map(m => m[1]);
const testRoot = path.resolve(localRoot, '../../../work/local-os-tests');
fs.mkdirSync(testRoot, { recursive: true, mode: 0o700 });
const runRoot = fs.mkdtempSync(path.join(testRoot, 'run-'));
const serverFile = path.join(localRoot, 'dist/server.mjs');
const LIMITS = {};
const limitsFile = path.resolve(localRoot, '../../alfred-os/lib/office-limits.ts');
const limitPattern = /^export const (\w+)\s*=\s*(\d+);/gm;
for (const [, name, value] of fs.readFileSync(limitsFile, 'utf8').matchAll(limitPattern)) {
  LIMITS[name] = Number(value);
}
for (const name of ['REVISE_FEEDBACK_MAX', 'REQUEST_BODY_MAX', 'ENTRY_TITLE_MAX', 'ENTRY_BODY_MAX', 'RESULTADO_MIN', 'RESULTADO_MAX']) {
  assert.ok(Number.isInteger(LIMITS[name]) && LIMITS[name] > 0, name + ' must be a positive integer in office-limits.ts');
}
const checks = [];
const processes = new Set();
let current;

function fixture() {
  const at = 1700000000000;
  return {
    version: 1, source: 'isolated-runtime-fixture', exportedAt: '2023-11-14T22:13:20.000Z',
    entries: [
      { id: 'imported-queued', kind: 'request', project: P1, title: 'Imported queued test', body: 'Synthetic fixture only.', status: 'queued', role: 'radar', created_at: at, updated_at: at },
      { id: 'imported-active', kind: 'request', project: P1, title: 'Imported active test', body: 'Synthetic fixture only.', status: 'dispatching', role: 'radar', created_at: at + 1, updated_at: at + 1 },
    ],
    office: [
      { id: 'settings', value: JSON.stringify({ focus: P2, energy: 'normal', action: 'Synthetic next step' }), updated_at: at },
      { id: 'focus-session', value: JSON.stringify({ id: 'isolated-focus', revision: 1, project: P2, title: 'Synthetic focus', doneWhen: 'Synthetic result saved', minutes: 5, status: 'paused', note: 'Existing synthetic note', elapsed: 1000, startedAt: null, completedAt: null, updatedAt: at }), updated_at: at },
    ],
  };
}

async function check(name, fn) {
  const started = Date.now();
  try {
    await fn();
    checks.push({ name, passed: true, durationMs: Date.now() - started });
    console.log('PASS ' + name);
  } catch (error) {
    checks.push({ name, passed: false, durationMs: Date.now() - started, error: error.message });
    console.log('FAIL ' + name + ': ' + error.message);
    throw error;
  }
}

function inspect(directory, fn) {
  const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'), { readOnly: true });
  try { return fn(db); } finally { db.close(); }
}

async function start(directory) {
  const child = spawn(process.execPath, [serverFile], {
    cwd: localRoot,
    env: { ...process.env, PROMPTED_OS_DATA_DIR: directory, PROMPTED_OS_DISABLE_BRIDGE: '1', PROMPTED_OS_DISABLE_TELEGRAM: '1', PROMPTED_OS_CLAUDE_BIN: '/usr/bin/false', PROMPTED_OS_EQUIPO: '' },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  processes.add(child);
  let buffer = '', stderr = '';
  child.stderr.on('data', piece => { stderr = (stderr + piece.toString()).slice(-5000); });
  child.on('exit', () => processes.delete(child));
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Isolated server did not become ready')); }, 10000);
    child.once('error', () => { clearTimeout(timer); reject(new Error('Cannot start isolated server')); });
    child.once('exit', code => { clearTimeout(timer); reject(new Error('Isolated server exited before ready: ' + code + '\n' + stderr)); });
    child.stdout.on('data', piece => {
      buffer += piece.toString();
      const match = buffer.match(/^READY (.+)$/m);
      if (match) {
        clearTimeout(timer);
        try { resolve(JSON.parse(match[1])); } catch { reject(new Error('Invalid local startup response')); }
      }
    });
  });
  return { child, ...ready, cookie: null };
}

async function stop(server) {
  if (server.child.exitCode !== null) return;
  await new Promise(resolve => {
    const timer = setTimeout(() => server.child.kill('SIGKILL'), 7000);
    server.child.once('exit', () => { clearTimeout(timer); resolve(); });
    server.child.stdin.end();
  });
}

async function request(route, options = {}) {
  const headers = { ...options.headers };
  const cookie = options.cookie === undefined ? current.cookie : options.cookie;
  if (cookie) headers.Cookie = cookie;
  if (options.origin !== undefined) headers.Origin = options.origin;
  if (options.value !== undefined) headers['Content-Type'] = 'application/json';
  const response = await fetch(current.origin + route, {
    method: options.method || 'GET', headers,
    body: options.value === undefined ? undefined : JSON.stringify(options.value),
    redirect: 'manual', signal: AbortSignal.timeout(5000),
  });
  const text = await response.text();
  let data; try { data = JSON.parse(text); } catch {}
  return { status: response.status, headers: response.headers, text, data };
}

const post = (route, value) => request(route, { method: 'POST', origin: current.origin, value });

function rawHostStatus(host) {
  return new Promise((resolve, reject) => {
    const req = http.get(current.origin + '/api/office', { headers: { Host: host, Cookie: current.cookie } }, response => {
      response.resume(); response.on('end', () => resolve(response.statusCode));
    });
    req.setTimeout(5000, () => req.destroy(new Error('Raw HTTP timeout')));
    req.on('error', reject);
  });
}

async function bootstrap() {
  const response = await request('/?native=1&view=now', { cookie: null, headers: { 'X-OS-Launch': current.bootstrapToken } });
  assert.equal(response.status, 200);
  const setCookie = response.headers.get('set-cookie');
  assert.ok(setCookie);
  assert.match(setCookie, /HttpOnly/i);
  assert.match(setCookie, /SameSite=Strict/i);
  assert.match(setCookie, /Path=\//i);
  current.cookie = setCookie.split(';')[0];
  assert.ok(!response.text.includes(current.bootstrapToken), 'Launch token must not appear in HTML');
  assert.ok(!response.text.includes(current.cookie.slice('os_local='.length)), 'Session must not appear in HTML');
  assert.ok(!setCookie.includes(current.bootstrapToken), 'Launch token is not the session cookie');
  assert.equal(response.headers.get('referrer-policy'), 'no-referrer');
  assert.ok(response.headers.get('content-security-policy')?.includes("connect-src 'self'"));
}

try {
  await check('database permissions, D1 results and atomic batch rollback', async () => {
    const directory = path.join(runRoot, 'database');
    const db = openDatabase(directory);
    try {
      if (process.platform !== 'win32') assert.equal(fs.statSync(directory).mode & 0o777, 0o700);   // Windows has no POSIX modes
      if (process.platform !== 'win32') assert.equal(fs.statSync(path.join(directory, 'workspace.sqlite')).mode & 0o777, 0o600);   // Windows has no POSIX modes
      const inserted = await db.DB.prepare('INSERT INTO office VALUES (?,?,?)').bind('one', '{"n":1}', 1).run();
      assert.equal(inserted.meta.changes, 1);
      assert.equal(await db.DB.prepare('SELECT value FROM office WHERE id=?').bind('one').first('value'), '{"n":1}');
      const rows = await db.DB.prepare('SELECT id FROM office').all();
      assert.deepEqual(rows.results.map(row => ({ ...row })), [{ id: 'one' }]);
      await assert.rejects(db.DB.batch([
        db.DB.prepare('INSERT INTO office VALUES (?,?,?)').bind('rollback-me', '{}', 2),
        db.DB.prepare('INSERT INTO office VALUES (?,?,?)').bind('one', '{}', 3),
      ]));
      assert.equal(await db.DB.prepare('SELECT id FROM office WHERE id=?').bind('rollback-me').first(), null);
      const changed = await db.DB.prepare('UPDATE office SET value=? WHERE id=? RETURNING id').bind('{"n":2}', 'one').all();
      assert.equal(changed.meta.changes, 1);
      assert.deepEqual(changed.results.map(row => ({ ...row })), [{ id: 'one' }]);
    } finally { db.close(); }
  });

  await check('invalid initial import rolls back every row', async () => {
    const directory = path.join(runRoot, 'invalid-import');
    const db = openDatabase(directory);
    try {
      const data = fixture(); data.entries.push({ ...data.entries[0], id: 'bad', kind: 'invalid' });
      const file = path.join(directory, 'initial-import.json'); fs.writeFileSync(file, JSON.stringify(data));
      assert.throws(() => db.importInitial(file));
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM entries').get().n, 0);
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM office').get().n, 0);
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM local_metadata').get().n, 0);
    } finally { db.close(); }
  });

  await check('outer transaction rolls back nested batch and admission together', async () => {
    const db = openDatabase(path.join(runRoot, 'transaction'));
    try {
      await assert.rejects(db.transaction(async () => {
        await db.DB.batch([db.DB.prepare('INSERT INTO office VALUES (?,?,?)').bind('atomic-work', '{}', 1)]);
        db.sqlite.prepare('INSERT INTO local_admissions VALUES (?,?)').run('atomic-work', 1);
        throw new Error('Synthetic interruption before commit');
      }));
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM office').get().n, 0);
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM local_admissions').get().n, 0);
      await db.transaction(async () => {
        await db.DB.batch([db.DB.prepare('INSERT INTO office VALUES (?,?,?)').bind('atomic-work', '{}', 2)]);
        db.sqlite.prepare('INSERT INTO local_admissions VALUES (?,?)').run('atomic-work', 2);
      });
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM office').get().n, 1);
      assert.equal(db.sqlite.prepare('SELECT COUNT(*) n FROM local_admissions').get().n, 1);
    } finally { db.close(); }
  });

  await check('contenido stores measurements and sums them by network', async () => {
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.resolve(localRoot, '../../alfred-os/package.json'));
    const bundle = path.join(runRoot, 'contenido.mjs');
    await require('esbuild').build({ entryPoints: [path.join(localRoot, 'contenido.ts')], outfile: bundle, bundle: true, platform: 'node', format: 'esm', target: 'node26', logLevel: 'warning' });
    const isolated = fs.mkdtempSync(path.join(runRoot, 'contenido-'));
    const sources = {
      PROMPTED_OS_RF_DB: path.join(isolated, 'machine.db'),
      PROMPTED_OS_FV_OUT: path.join(isolated, 'output'),
      PROMPTED_OS_COURSE_REELS: path.join(isolated, 'reels'),
      PROMPTED_OS_VAULT_DIR: path.join(isolated, 'vault'),
      PROMPTED_OS_CONTENT_PLAN: '01-Projects/norte-capital/content',
      PROMPTED_OS_DATA_DIR: path.join(isolated, 'Workspace'),
      PROMPTED_OS_DAY_REELS: path.join(isolated, 'reels-dia'),
    };
    const previous = Object.fromEntries(Object.keys(sources).map(key => [key, process.env[key]]));
    try {
      Object.assign(process.env, sources);
      fs.mkdirSync(sources.PROMPTED_OS_DATA_DIR, { recursive: true });
      fs.writeFileSync(path.join(sources.PROMPTED_OS_DATA_DIR, 'configuracion.json'), JSON.stringify({
        contenido: { planMarca: 'norte-capital', marcas: [{ id: 'norte-capital', nombre: 'Norte', prefijo: 'kc', perfiles: [{ red: 'instagram', usuario: 'norte', enlace: '' }] }] },
      }));
      const plan = path.join(sources.PROMPTED_OS_VAULT_DIR, '01-Projects/norte-capital/content/plan-prueba.md');
      fs.mkdirSync(path.dirname(plan), { recursive: true });
      fs.writeFileSync(plan, `## Semana 1\n### Mié 21 (${new Date().toISOString().slice(0, 10)})\n#### Instagram · Reel · 20:00: Reel 9 · Prueba\n`);
      const madridDia = n => new Date(Date.now() - n * 86400_000).toLocaleDateString('sv-SE', { timeZone: 'Europe/Madrid' });
      const ganadores = path.join(sources.PROMPTED_OS_DAY_REELS, madridDia(1), 'ganadores.json');
      fs.mkdirSync(path.dirname(ganadores), { recursive: true });
      fs.writeFileSync(ganadores, JSON.stringify({ 'norte-capital': { name: 'kc-1730', h: 'h2', titulo: 'Gancho prueba', vistas: 900 } }));
      const { avisoEnlacePerfil, primerPerfilSinEnlace, ofreceLanzado, perfilParaLanzar, avisoSinEnlace, baseEnlaceValida, enlaceMedido, etiquetaEnlace, sinEnlaceMedido, sinEnlaceHaceDias, esReelBueno, lineaPorMedir, lineaSinEnlace, lineaSinEnlaceMedida, medir, porMedir, porRed, read, reprogramar, marcar, guardarPerfil, PREGUNTA_CIFRAS_INICIO, preguntaCifras, cifrasDeTexto, publicacionDeRespuesta, siguientePorMedir, apuntadoCifras } = await import(bundle);
      const data = path.join(isolated, 'Workspace');
      const file = path.join(data, 'contenido', 'medidas.json');
      const cifras = { vistas: '100', visitas: 10, clics: 3, compras: 0 };
      const errorCifras = { message: 'Las cifras tienen que ser números enteros, sin decimales ni negativos.' };
      assert.equal(enlaceMedido({ id: 'rf:10', brand: 'beta', canal: 'instagram' }), 'https://beta.com/comparar.html?utm_source=instagram&utm_medium=social&utm_campaign=reel&utm_content=rf-10');
      assert.equal(etiquetaEnlace(enlaceMedido({ id: 'rf:10', brand: 'beta', canal: 'instagram' })), 'rf-10');
      for (const enlace of ['', 'no-es-url', 'http://norte.test/?utm_content=x', 'https://norte.test/journal']) assert.equal(etiquetaEnlace(enlace), '');
      const enlaceTikTok = enlaceMedido({ id: 'fv:2026-10-19-full-visual-tf-40-cuentas', brand: 'beta', canal: 'tiktok' });
      assert.ok(enlaceTikTok.includes('utm_source=tiktok'));
      assert.ok(enlaceTikTok.includes('utm_content=fv-2026-10-19-full-visual-tf-40-cuentas'));
      assert.equal(enlaceMedido({ id: 'rf:10', brand: 'norte-capital', canal: 'instagram' }), '');
      for (const base of ['', 'norte.test/journal', 'http://norte.test', 'https://', 'no es url']) assert.equal(baseEnlaceValida(base), false);
      for (const base of ['https://norte.test/journal', '  https://norte.test  ']) assert.equal(baseEnlaceValida(base), true);
      assert.equal(avisoEnlacePerfil('beta', ''), '');
      assert.equal(avisoEnlacePerfil('norte-capital', ''), 'vacio');
      assert.equal(avisoEnlacePerfil('norte-capital', '   '), 'vacio');
      assert.equal(avisoEnlacePerfil('norte-capital', 'http://norte.test'), 'invalido');
      assert.equal(avisoEnlacePerfil('norte-capital', 'norte.test/journal'), 'invalido');
      assert.equal(avisoEnlacePerfil('norte-capital', 'https://norte.test'), '');
      assert.equal(avisoEnlacePerfil('norte-capital', '  https://norte.test  '), '');
      assert.equal(primerPerfilSinEnlace([]), '');
      assert.equal(primerPerfilSinEnlace([{ brand: 'beta', red: 'instagram', enlace: '' }]), '');
      assert.equal(primerPerfilSinEnlace([{ brand: 'norte-capital', red: 'instagram', enlace: 'https://norte.test' }, { brand: 'norte-capital', red: 'tiktok', enlace: '' }]), 'norte-capitaltiktok');
      assert.equal(primerPerfilSinEnlace([{ brand: 'norte-capital', red: 'instagram', enlace: 'http://norte.test' }, { brand: 'norte-capital', red: 'tiktok', enlace: '' }]), 'norte-capitalinstagram');
      assert.equal(primerPerfilSinEnlace([{ brand: 'beta', red: 'instagram', enlace: '' }, { brand: 'norte-capital', red: 'x', enlace: '  ' }]), 'norte-capitalx');
      assert.equal(ofreceLanzado([], 'norte-capital', 'https://norte.test'), false);
      assert.equal(ofreceLanzado(['t_1'], 'norte-capital', 'https://norte.test'), true);
      assert.equal(ofreceLanzado(['t_1'], 'norte-capital', 'http://norte.test'), false);
      assert.equal(ofreceLanzado(['t_1'], 'norte-capital', '  '), false);
      assert.equal(ofreceLanzado(['t_1'], 'beta', ''), false);
      assert.equal(perfilParaLanzar([]), '');
      assert.equal(perfilParaLanzar([{ brand: 'norte-capital', red: 'instagram', enlace: 'https://norte.test' }, { brand: 'norte-capital', red: 'tiktok', enlace: '' }]), 'norte-capitaltiktok');
      assert.equal(perfilParaLanzar([{ brand: 'beta', red: 'instagram', enlace: 'https://tf.test' }, { brand: 'norte-capital', red: 'instagram', enlace: 'https://norte.test' }]), 'norte-capitalinstagram');
      assert.equal(perfilParaLanzar([{ brand: 'beta', red: 'instagram', enlace: '' }]), '');
      assert.equal(enlaceMedido({ id: 'rf:10', brand: 'norte-capital', canal: 'instagram' }, 'https://norte.test/journal?ref=bio'), 'https://norte.test/journal?ref=bio&utm_source=instagram&utm_medium=social&utm_campaign=reel&utm_content=rf-10');
      for (const base of ['http://norte.test', 'no es url', '']) {
        assert.equal(enlaceMedido({ id: 'rf:10', brand: 'norte-capital', canal: 'instagram' }, base), '');
      }
      assert.equal(enlaceMedido({ id: 'rf:10', brand: 'beta', canal: 'instagram' }, 'https://otra.test'), 'https://beta.com/comparar.html?utm_source=instagram&utm_medium=social&utm_campaign=reel&utm_content=rf-10');
      const enlaceLargo = enlaceMedido({ id: 'a'.repeat(100), brand: 'beta', canal: 'instagram' });
      const contenidoLargo = new URL(enlaceLargo).searchParams.get('utm_content');
      assert.ok(contenidoLargo.length <= 60);
      assert.ok(!contenidoLargo.endsWith('-'));
      const enlaceItem = { id: 'rf:10', brand: 'beta', canal: 'instagram' };
      assert.equal(enlaceMedido(enlaceItem), enlaceMedido(enlaceItem));
      await assert.rejects(medir(data, 'x:1', cifras), { message: 'No sé qué es eso.' });
      for (const campo of ['vistas', 'visitas', 'clics', 'compras']) {
        for (const valor of [-1, 1.5, 'abc', 1000000001, '', '  ', null, undefined, [], true, false, {}, '1e3', '0x10']) {
          await assert.rejects(medir(data, 'plan:abc', { ...cifras, [campo]: valor }), errorCifras);
          assert.equal(fs.existsSync(file), false);
        }
      }
      assert.equal(fs.existsSync(file), false);
      const first = await medir(data, 'plan:abc', cifras);
      assert.equal(first.ok, true);
      assert.deepEqual(first.medida, { vistas: 100, visitas: 10, clics: 3, compras: 0, hora: first.medida.hora });
      assert.equal(new Date(first.medida.hora).toISOString(), first.medida.hora);
      assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { 'plan:abc': first.medida });
      const second = await medir(data, 'plan:abc', { vistas: 50, visitas: 5, clics: 1, compras: 1 });
      assert.deepEqual(second.medida, { vistas: 50, visitas: 5, clics: 1, compras: 1, hora: second.medida.hora });
      assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { 'plan:abc': second.medida });
      await assert.rejects(medir(data, 'plan:abc', { ...cifras, vistas: -1 }), errorCifras);
      assert.deepEqual(JSON.parse(fs.readFileSync(file, 'utf8')), { 'plan:abc': second.medida });
      const conEspacios = await medir(data, 'plan:espacios', { vistas: ' 7 ', visitas: 0, clics: 0, compras: 0 });
      assert.equal(conEspacios.medida.vistas, 7);
      assert.equal(JSON.parse(fs.readFileSync(file, 'utf8'))['plan:espacios'].vistas, 7);
      const base = { fuente: 'plan', brand: 'beta', marca: 'Beta', canal: 'instagram', tipo: 'Reel', fecha: null, titulo: '', gancho: '', texto: '', nota: '', estado: 'publicado', detalle: '', url: '', videos: [], portadas: [], elegido: 0, horaEditable: true, marcable: true };
      const items = [
        { ...base, id: 'plan:tiktok', canal: 'tiktok', medida: second.medida },
        { ...base, id: 'plan:abc', medida: first.medida },
        { ...base, id: 'plan:sin-medida', medida: null },
        { ...base, id: 'plan:programado', estado: 'programado', medida: first.medida },
      ];
      assert.deepEqual(lineaPorMedir([]), []);
      assert.deepEqual(lineaPorMedir(items.map(it => ({ ...it, porMedir: false }))), []);
      assert.deepEqual(lineaPorMedir([{ ...items[0], porMedir: true }, { ...items[1], porMedir: false }]), ['apuntar lo medido de 1 publicación (/medir)']);
      assert.deepEqual(lineaPorMedir(items.slice(0, 3).map(it => ({ ...it, porMedir: true }))), ['apuntar lo medido de 3 publicaciones (/medir)']);
      const before = JSON.stringify(items);
      const ahora = Date.now();
      const fechaEn = dias => new Date(ahora + dias * 86400000).toISOString();
      const sinEnlace = { ...base, id: 'plan:sin-enlace', brand: 'norte-capital', marca: 'Norte', estado: 'programado', fecha: fechaEn(2), enlace: '', medida: null, porMedir: false };
      const excluidos = [
        { ...sinEnlace, brand: 'beta' },
        { ...sinEnlace, enlace: 'https://norte.test/?utm_content=reel' },
        { ...sinEnlace, estado: 'publicado' },
        { ...sinEnlace, fecha: null },
        { ...sinEnlace, fecha: fechaEn(-1) },
        { ...sinEnlace, fecha: fechaEn(8) },
        { ...sinEnlace, fecha: 'fecha inválida' },
        { ...sinEnlace, fecha: fechaEn(7) },
      ];
      assert.deepEqual(sinEnlaceMedido(excluidos, ahora), []);
      assert.deepEqual(sinEnlaceMedido([sinEnlace, ...excluidos], ahora), [{ marca: 'Norte', canal: 'instagram', n: 1 }]);
      assert.deepEqual(sinEnlaceMedido([{ ...sinEnlace, fecha: fechaEn(0) }], ahora), [{ marca: 'Norte', canal: 'instagram', n: 1 }]);
      assert.deepEqual(sinEnlaceMedido([sinEnlace], ahora, 2), []);
      assert.deepEqual(lineaSinEnlace([], ahora), []);
      assert.equal(avisoSinEnlace([], ahora), '');
      assert.ok(avisoSinEnlace([{ ...sinEnlace, fecha: fechaEn(1) }], ahora).endsWith(' (1 en las próximas 48 h)'));
      assert.ok(avisoSinEnlace([{ ...sinEnlace, fecha: fechaEn(0) }], ahora).endsWith(' (1 en las próximas 48 h)'));
      const avisoUrgente = avisoSinEnlace([{ ...sinEnlace, fecha: fechaEn(1) }, { ...sinEnlace, id: 'plan:tres-dias', fecha: fechaEn(3) }], ahora);
      assert.equal(avisoUrgente, 'poner un enlace https en el perfil de Norte en Instagram: 2 publicaciones de esta semana saldrían sin enlace medido (1 en las próximas 48 h)');
      assert.deepEqual(lineaSinEnlace([{ ...sinEnlace, fecha: fechaEn(1) }, { ...sinEnlace, id: 'plan:tres-dias', fecha: fechaEn(3) }], ahora), [avisoUrgente]);
      assert.deepEqual(lineaSinEnlace([sinEnlace], ahora), ['poner un enlace https en el perfil de Norte en Instagram: 1 publicación de esta semana saldría sin enlace medido']);
      assert.deepEqual(lineaSinEnlace([sinEnlace, { ...sinEnlace, id: 'plan:otro-sin-enlace' }], ahora), ['poner un enlace https en el perfil de Norte en Instagram: 2 publicaciones de esta semana saldrían sin enlace medido']);
      const cuatroGrupos = [sinEnlace, ...['tiktok', 'youtube', 'facebook'].map(canal => ({ ...sinEnlace, canal }))];
      assert.ok(lineaSinEnlace(cuatroGrupos, ahora)[0].includes(' y 1 más'));
      assert.deepEqual(sinEnlaceMedido([...cuatroGrupos, { ...sinEnlace, marca: 'Alfa' }, { ...sinEnlace, canal: 'youtube' }], ahora), [
        { marca: 'Norte', canal: 'youtube', n: 2 },
        { marca: 'Alfa', canal: 'instagram', n: 1 },
        { marca: 'Norte', canal: 'facebook', n: 1 },
        { marca: 'Norte', canal: 'instagram', n: 1 },
        { marca: 'Norte', canal: 'tiktok', n: 1 },
      ]);
      for (const [canal, red] of [['tiktok', 'TikTok'], ['youtube', 'YouTube'], ['facebook', 'Facebook'], ['x', 'X'], ['otra-red', 'otra-red']]) {
        assert.deepEqual(lineaSinEnlace([{ ...sinEnlace, canal }], ahora), [`poner un enlace https en el perfil de Norte en ${red}: 1 publicación de esta semana saldría sin enlace medido`]);
      }
      const hoySinEnlace = new Date(ahora).toLocaleDateString('sv-SE', { timeZone: 'Europe/Madrid' });
      const haceDias = dias => new Date(Date.parse(hoySinEnlace + 'T00:00:00Z') - dias * 86400000).toISOString().slice(0, 10);
      for (const dias of [7, 8, 6]) {
        assert.deepEqual(sinEnlaceHaceDias({ [haceDias(dias)]: { total: 3 } }, hoySinEnlace), { dias, n: 3 });
      }
      assert.deepEqual(sinEnlaceHaceDias({ [haceDias(7)]: { total: 3 }, [haceDias(8)]: { total: 4 }, [haceDias(6)]: { total: 5 } }, hoySinEnlace), { dias: 7, n: 3 });
      for (const total of [-1, 1.5, '3', null]) {
        assert.equal(sinEnlaceHaceDias({ [haceDias(7)]: { total } }, hoySinEnlace), null);
        assert.deepEqual(sinEnlaceHaceDias({ [haceDias(7)]: { total }, [haceDias(8)]: { total: 0 }, [haceDias(6)]: { total: 5 } }, hoySinEnlace), { dias: 8, n: 0 });
      }
      for (const historial of [null, undefined, [], '3', 3, false, {}]) assert.equal(sinEnlaceHaceDias(historial, hoySinEnlace), null);
      for (const entrada of [null, [], '3', {}]) assert.equal(sinEnlaceHaceDias({ [haceDias(7)]: entrada }, hoySinEnlace), null);
      const textoSinEnlace = 'poner un enlace https en el perfil de Norte en Instagram: 1 publicación de esta semana saldría sin enlace medido';
      const urgenteSinEnlace = { ...sinEnlace, fecha: fechaEn(1) };
      assert.equal(avisoSinEnlace([urgenteSinEnlace], ahora, { dias: 7, n: 3 }), textoSinEnlace + ' (1 en las próximas 48 h; hace 7 días: 3)');
      assert.equal(avisoSinEnlace([sinEnlace], ahora, { dias: 8, n: 0 }), textoSinEnlace + ' (hace 8 días: 0)');
      assert.equal(avisoSinEnlace([urgenteSinEnlace], ahora), textoSinEnlace + ' (1 en las próximas 48 h)');
      assert.equal(avisoSinEnlace([sinEnlace], ahora, null), textoSinEnlace);
      const historialFile = path.join(data, 'contenido', 'sin-enlace.json');
      const registroSinEnlace = { total: 1, marcas: { 'norte-capital': 1 } };
      const leerHistorial = () => JSON.parse(fs.readFileSync(historialFile, 'utf8'));
      assert.equal(fs.existsSync(historialFile), false);
      assert.deepEqual(await lineaSinEnlaceMedida(data, [sinEnlace], ahora), [textoSinEnlace]);
      assert.deepEqual(leerHistorial(), { [hoySinEnlace]: registroSinEnlace });
      fs.writeFileSync(historialFile, JSON.stringify({ [haceDias(7)]: { total: 3, marcas: { 'norte-capital': 3 } } }));
      assert.deepEqual(await lineaSinEnlaceMedida(data, [sinEnlace], ahora), [textoSinEnlace + ' (hace 7 días: 3)']);
      assert.deepEqual(leerHistorial()[hoySinEnlace], registroSinEnlace);
      assert.deepEqual(await lineaSinEnlaceMedida(data, excluidos, ahora), []);
      assert.deepEqual(leerHistorial()[hoySinEnlace], { total: 0, marcas: {} });
      const fechasHistorial = Array.from({ length: 61 }, (_, dias) => haceDias(dias));
      fs.writeFileSync(historialFile, JSON.stringify({ ...Object.fromEntries(fechasHistorial.map(fecha => [fecha, registroSinEnlace])), otra: registroSinEnlace, '2026-1-1': registroSinEnlace }));
      await lineaSinEnlaceMedida(data, [sinEnlace], ahora);
      assert.deepEqual(Object.keys(leerHistorial()).sort().reverse(), fechasHistorial.slice(0, 60));
      for (const raw of ['no es JSON', 'null', '[]', '3']) {
        fs.writeFileSync(historialFile, raw);
        assert.deepEqual(await lineaSinEnlaceMedida(data, [sinEnlace], ahora), [textoSinEnlace]);
        assert.deepEqual(leerHistorial(), { [hoySinEnlace]: registroSinEnlace });
      }
      const telegramFuente = fs.readFileSync(path.join(localRoot, 'telegram.ts'), 'utf8');
      const decisionsFuente = telegramFuente.match(/async function decisions\(\)\{([\s\S]*?)\n\}/)?.[1];
      assert.ok(decisionsFuente?.includes('lineaSinEnlaceMedida('));
      assert.equal((decisionsFuente.match(/contenido\.read\(/g) || []).length, 1);
      assert.equal(porMedir({ ...items[2], fecha: new Date(ahora - 25 * 3600_000).toISOString() }, ahora), true);
      assert.equal(porMedir({ ...items[2], fecha: new Date(ahora - 3600_000).toISOString() }, ahora), false);
      assert.equal(porMedir(items[2], ahora), true);
      assert.equal(porMedir(items[1], ahora), false);
      assert.equal(porMedir({ ...items[2], estado: 'programado' }, ahora), false);
      assert.deepEqual(porRed(items), [
        { brand: 'beta', marca: 'Beta', red: 'instagram', publicadas: 2, medidas: 1, vistas: 100, visitas: 10, clics: 3, compras: 0 },
        { brand: 'beta', marca: 'Beta', red: 'tiktok', publicadas: 1, medidas: 1, vistas: 50, visitas: 5, clics: 1, compras: 1 },
      ]);
      assert.equal(JSON.stringify(items), before);
      const pendiente = { ...items[2], titulo: 'Publicación de prueba', porMedir: true };
      const siguiente = { ...pendiente, id: 'plan:siguiente', titulo: 'Segunda publicación' };
      const lista = [{ ...items[0], porMedir: false }, pendiente, siguiente];
      assert.equal(siguientePorMedir([]), null);
      assert.equal(siguientePorMedir(items.map(it => ({ ...it, porMedir: false }))), null);
      assert.equal(siguientePorMedir(lista), pendiente);
      assert.equal(siguientePorMedir(lista, pendiente.id), siguiente);
      assert.equal(siguientePorMedir([pendiente], pendiente.id), null);
      assert.equal(siguientePorMedir([siguiente, pendiente]), siguiente);
      assert.equal(publicacionDeRespuesta(preguntaCifras(siguientePorMedir(lista, pendiente.id)), lista), siguiente);
      assert.equal(apuntadoCifras(pendiente, first.medida, 0), 'Apuntado: «Publicación de prueba» — 100 vistas, 10 visitas, 3 clics, 0 compras. Clics por vista: 3 %. Compras por clic: 0 %. No queda ninguna por medir.');
      assert.equal(apuntadoCifras(pendiente, first.medida, 1), 'Apuntado: «Publicación de prueba» — 100 vistas, 10 visitas, 3 clics, 0 compras. Clics por vista: 3 %. Compras por clic: 0 %. Quedan 1; te pregunto la siguiente.');
      assert.equal(apuntadoCifras(pendiente, first.medida, 2), 'Apuntado: «Publicación de prueba» — 100 vistas, 10 visitas, 3 clics, 0 compras. Clics por vista: 3 %. Compras por clic: 0 %. Quedan 2; te pregunto la siguiente.');
      assert.equal(apuntadoCifras(pendiente, { vistas: 0, visitas: 0, clics: 0, compras: 0, hora: '' }, 0).includes('Clics por vista'), false);
      assert.equal(apuntadoCifras(pendiente, { vistas: 10, visitas: 0, clics: 0, compras: 0, hora: '' }, 0).includes('Compras por clic'), false);
      assert.ok(apuntadoCifras(pendiente, { vistas: 10, visitas: 0, clics: 3, compras: 1, hora: '' }, 0).includes('Compras por clic: 33,3 %.'));
      const pregunta = preguntaCifras(pendiente);
      assert.ok(pregunta.startsWith(PREGUNTA_CIFRAS_INICIO));
      for (const valor of [pendiente.titulo, pendiente.marca, pendiente.canal]) assert.ok(pregunta.includes(valor));
      assert.equal(pregunta.includes('publicado el'), false);
      assert.equal(pregunta, '📊 Cifras de «Publicación de prueba» (Beta · instagram). Responde a este mensaje con cuatro números: vistas, visitas, clics y compras. Por ejemplo: 1200 40 8 0');
      const conEnlace = { ...pendiente, enlace: enlaceMedido({ id: 'rf:10', brand: 'beta', canal: 'instagram' }) };
      assert.ok(preguntaCifras(conEnlace).endsWith(' Los clics y compras de esta publicación son los que lleguen con la etiqueta «rf-10».'));
      assert.equal(preguntaCifras({ ...pendiente, enlace: '' }), preguntaCifras(pendiente));
      assert.equal(publicacionDeRespuesta(preguntaCifras(conEnlace), [conEnlace]), conEnlace);
      assert.ok(preguntaCifras({ ...pendiente, fecha: '2026-10-22T23:30:00.000Z' }).includes(', publicado el 23/10'));
      for (const texto of ['1200 40 8 0', '1.200, 40, 8, 0', '  1200   40\t8 0  ', '1200;40;8;0']) {
        assert.deepEqual(cifrasDeTexto(texto), { vistas: '1200', visitas: '40', clics: '8', compras: '0' });
      }
      for (const texto of ['', '1 2 3', '1 2 3 4 5', '1e3 1 1 1', '-1 1 1 1', '1,5 2 3 4', 'mil 1 1 1', '1.20 1 1 1']) {
        assert.equal(cifrasDeTexto(texto), null);
      }
      assert.equal(publicacionDeRespuesta(pregunta, [pendiente]), pendiente);
      assert.equal(publicacionDeRespuesta(pregunta, [{ ...pendiente, porMedir: false }]), null);
      assert.equal(publicacionDeRespuesta(pregunta + ' ', [pendiente]), null);
      assert.deepEqual((await read(data)).porRed, []);
      const post = (await read(data)).items.find(it => it.titulo === 'Reel 9 · Prueba');
      assert.ok(post);
      assert.ok((await read(data)).items.every(it => typeof it.enlace === 'string'));
      assert.equal(post.enlace, '');
      await guardarPerfil(data, { brand: 'norte-capital', red: 'instagram', enlace: '  https://norte.test/journal  ' });
      const postConEnlace = (await read(data)).items.find(it => it.id === post.id);
      assert.ok(postConEnlace.enlace.startsWith('https://norte.test/journal?'));
      assert.ok(postConEnlace.enlace.includes('utm_content='));
      await guardarPerfil(data, { brand: 'norte-capital', red: 'instagram', enlace: 'norte.test/journal' });
      assert.equal((await read(data)).items.find(it => it.id === post.id)?.enlace, '');
      await guardarPerfil(data, { brand: 'norte-capital', red: 'instagram', enlace: '' });
      await reprogramar(data, post.id, new Date(ahora - 2 * 86400_000).toISOString());
      await marcar(data, post.id, true);
      assert.equal((await read(data)).items.find(it => it.id === post.id)?.porMedir, true);
      assert.deepEqual(lineaPorMedir((await read(data)).items), ['apuntar lo medido de 1 publicación (/medir)']);
      const pendientes = (await read(data)).items;
      const citado = preguntaCifras(pendientes.find(it => it.id === post.id));
      const publicacion = publicacionDeRespuesta(citado, pendientes);
      assert.equal(publicacion?.id, post.id);
      const respuesta = cifrasDeTexto('1.200, 40, 8, 0');
      assert.ok(respuesta);
      await medir(data, publicacion.id, respuesta);
      assert.equal((await read(data)).items.find(it => it.id === post.id)?.medida.vistas, 1200);
      assert.equal((await read(data)).items.find(it => it.id === post.id)?.porMedir, false);
      assert.deepEqual(lineaPorMedir((await read(data)).items), []);
      await reprogramar(data, post.id, new Date(ahora - 20 * 86400_000).toISOString());
      await marcar(data, post.id, true);
      const medidas = JSON.parse(fs.readFileSync(file, 'utf8')); delete medidas[post.id];
      fs.writeFileSync(file, JSON.stringify(medidas));
      assert.equal((await read(data)).items.find(it => it.id === post.id)?.porMedir, true);
      await medir(data, post.id, cifras);
      assert.equal((await read(data)).items.find(it => it.id === post.id), undefined);
      assert.equal(fs.existsSync(sources.PROMPTED_OS_RF_DB), false);
      // a good reel already marked as published is still measured; the Trial Reels are not
      const hoy = madridDia(0), pasado = `ritmo:kc-2000:${madridDia(3)}`;
      assert.equal(esReelBueno(`ritmo:kc-2000:${hoy}`), true);
      for (const id of [`ritmo:kc-1030:${hoy}`, `ritmo:zz-2000:${hoy}`, 'ritmo:kc-2000:2026-1-1']) {
        assert.equal(esReelBueno(id), false);
        await assert.rejects(medir(data, id, cifras), { message: 'No sé qué es eso.' });
      }
      // yesterday's winner is no longer planned as today's good reel (Owner 29/10: a winner is squeezed with variants first)
      assert.equal((await read(data)).items.find(it => it.id === `ritmo:kc-2000:${hoy}`), undefined);
      await marcar(data, pasado, true);
      const marcado = (await read(data)).items.find(it => it.id === pasado);
      assert.equal(marcado?.estado, 'publicado');
      assert.equal(marcado.porMedir, true);
      await medir(data, pasado, { vistas: 500, visitas: 20, clics: 4, compras: 0 });
      const medido = await read(data);
      assert.equal(medido.items.find(it => it.id === pasado)?.medida.vistas, 500);
      assert.equal(medido.items.find(it => it.id === pasado)?.porMedir, false);
      assert.equal(medido.porRed.find(g => g.brand === 'norte-capital' && g.red === 'instagram')?.publicadas, 1);
      assert.equal(porMedir({ ...items[2], id: `ritmo:kc-1030:${madridDia(3)}`, fuente: 'ritmo', fecha: new Date(ahora - 3 * 86400_000).toISOString() }, ahora), false);
      const entradas = ['publicado', 'programado', 'cancelado', 'pendiente', 'fallo'].map(estado => ({
        id: `prueba-${estado}`, marca: 'norte-capital', red: 'instagram', fecha: madridDia(3), hora: '20:00', titulo: `Calendario ${estado}`, estado,
      }));
      fs.writeFileSync(path.join(data, 'contenido', 'calendario.json'), JSON.stringify({ entradas }));
      const calendarioItems = (await read(data)).items;
      const publicada = calendarioItems.find(it => it.id === 'cal:prueba-publicado');
      assert.equal(publicada?.porMedir, true);
      const respuestaCalendario = publicacionDeRespuesta(preguntaCifras(publicada), calendarioItems);
      assert.equal(respuestaCalendario, publicada);
      await medir(data, respuestaCalendario.id, cifrasDeTexto('1.200, 40, 8, 0'));
      const calendarioMedido = await read(data);
      assert.equal(calendarioMedido.items.find(it => it.id === publicada.id)?.medida.vistas, 1200);
      assert.equal(calendarioMedido.items.find(it => it.id === publicada.id)?.porMedir, false);
      const medidasCalendario = fs.readFileSync(file, 'utf8');
      for (const id of ['cal:prueba-programado', 'cal:prueba-cancelado', 'cal:prueba-pendiente', 'cal:prueba-fallo', 'cal:no-existe']) {
        await assert.rejects(medir(data, id, cifras), { message: 'No sé qué es eso.' });
        assert.equal(Object.hasOwn(JSON.parse(fs.readFileSync(file, 'utf8')), id), false);
        assert.equal(fs.readFileSync(file, 'utf8'), medidasCalendario);
      }
      assert.deepEqual(calendarioMedido.porRed.find(g => g.brand === 'norte-capital' && g.red === 'instagram'), {
        brand: 'norte-capital', marca: 'Norte', red: 'instagram', publicadas: 2, medidas: 2, vistas: 1700, visitas: 60, clics: 12, compras: 0,
      });
    } finally {
      for (const [key, value] of Object.entries(previous)) {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
      }
    }
  });

  await check('contenido compara la tasa de una publicación con la media de su red', async () => {
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.resolve(localRoot, '../../alfred-os/package.json'));
    const bundle = path.join(runRoot, 'contenido-tasas.mjs');
    await require('esbuild').build({ entryPoints: [path.resolve(localRoot, '../../alfred-os/lib/contenido-tasas.ts')], outfile: bundle, bundle: true, platform: 'node', format: 'esm', target: 'node26', logLevel: 'warning' });
    const { mejores, posicionTasa, publicados, tasa } = await import(bundle);
    const mezcla = [
      { id: 'normal', estado: 'publicado', fuente: 'machine', marcable: false },
      { id: 'ritmo marcable', estado: 'publicado', fuente: 'ritmo', marcable: true },
      { id: 'ritmo no marcable', estado: 'publicado', fuente: 'ritmo', marcable: false },
      { id: 'programado', estado: 'programado', fuente: 'machine', marcable: false },
    ];
    const mezclaAntes = JSON.stringify(mezcla);
    assert.deepEqual(publicados(mezcla).map(i => i.id), ['ritmo marcable', 'normal']);
    assert.equal(JSON.stringify(mezcla), mezclaAntes);
    assert.equal(posicionTasa(10, 100, 20, 200, 2), 'media');
    assert.equal(posicionTasa(30, 100, 20, 200, 2), 'encima');
    assert.equal(posicionTasa(5, 100, 20, 200, 2), 'debajo');
    assert.equal(posicionTasa(0, 0, 20, 200, 2), '');
    assert.equal(posicionTasa(1, 10, 0, 0, 2), '');
    assert.equal(posicionTasa(10, 100, 40, 400, 3), 'media');
    assert.equal(posicionTasa(10, 100, 20, 200, 1), 'primera');
    assert.equal(tasa(0, 0), '—');
    assert.equal(tasa(1, 3), '33,3 %');
    assert.equal(tasa(2, 10), '20 %');
    assert.equal(tasa(0, 5), '0 %');
    assert.deepEqual(mejores([]), []);
    const lista = [
      { id: 'programado', estado: 'programado', medida: { vistas: 10, visitas: 10, clics: 10 } },
      { id: 'sin-medida', estado: 'publicado', medida: null },
      { id: 'sin-vistas', estado: 'publicado', medida: { vistas: 0, visitas: 0, clics: 0 } },
      { id: '5/100', estado: 'publicado', medida: { vistas: 100, visitas: 50, clics: 5 } },
      { id: '1/10', estado: 'publicado', medida: { vistas: 10, visitas: 1, clics: 1 } },
      { id: '2/100', estado: 'publicado', medida: { vistas: 100, visitas: 20, clics: 2 } },
      { id: '1/100', estado: 'publicado', medida: { vistas: 100, visitas: 10, clics: 1 } },
    ];
    const antes = JSON.stringify(lista);
    const top = mejores(lista);
    assert.deepEqual(top.map(i => i.id), ['1/10', '5/100', '2/100']);
    assert.equal(top.length, 3);
    assert.deepEqual(mejores(lista, 10).map(i => i.id), ['1/10', '5/100', '2/100', '1/100']);
    assert.deepEqual(mejores(lista, 1).map(i => i.id), ['1/10']);
    assert.equal(JSON.stringify(lista), antes);
    const empateVisitas = [
      { id: 'menos-visitas', estado: 'publicado', medida: { vistas: 100, visitas: 10, clics: 5 } },
      { id: 'mas-visitas', estado: 'publicado', medida: { vistas: 20, visitas: 4, clics: 1 } },
    ];
    assert.deepEqual(mejores(empateVisitas).map(i => i.id), ['mas-visitas', 'menos-visitas']);
    const empateVistas = [
      { id: 'menos-vistas', estado: 'publicado', medida: { vistas: 20, visitas: 4, clics: 1 } },
      { id: 'mas-vistas', estado: 'publicado', medida: { vistas: 100, visitas: 20, clics: 5 } },
    ];
    assert.deepEqual(mejores(empateVistas).map(i => i.id), ['mas-vistas', 'menos-vistas']);
  });

  await check('contenido avisa cuando no ha llegado un análisis nuevo', async () => {
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.resolve(localRoot, '../../alfred-os/package.json'));
    const bundle = path.join(runRoot, 'contenido-analisis.mjs');
    await require('esbuild').build({ entryPoints: [path.resolve(localRoot, '../../alfred-os/lib/contenido-analisis.ts')], outfile: bundle, bundle: true, platform: 'node', format: 'esm', target: 'node26', logLevel: 'warning' });
    const { avisoAnalisis } = await import(bundle);
    const ahora = new Date('2026-10-30T12:00:00.000Z');
    const antigua = new Date(ahora.getTime() - 4 * 86400_000).toISOString();
    assert.equal(avisoAnalisis(true, antigua, ahora), '');
    for (const fecha of ['rota', null, undefined, 42, '2026-02-30', new Date(ahora.getTime() + 86400_000).toISOString()]) {
      assert.equal(avisoAnalisis(false, fecha, ahora), '');
    }
    const aviso = avisoAnalisis(false, antigua, ahora);
    assert.ok(aviso.startsWith('Último análisis: '));
    assert.ok(aviso.includes('hace 4 días'));
    assert.equal(aviso, 'Último análisis: 26/10 (hace 4 días). No ha llegado uno nuevo.');
    assert.ok(avisoAnalisis(false, '2026-10-26', ahora).includes('26/10'));
    const dosHoras = avisoAnalisis(false, new Date(ahora.getTime() - 2 * 3600_000).toISOString(), ahora);
    const unDia = avisoAnalisis(false, new Date(ahora.getTime() - 86400_000).toISOString(), ahora);
    const tresDias = avisoAnalisis(false, new Date(ahora.getTime() - 3 * 86400_000).toISOString(), ahora);
    assert.equal(dosHoras, 'Último análisis: 30/10. Ha llegado, pero no se ha podido leer.');
    assert.equal(unDia, 'Último análisis: 29/10. Ha llegado, pero no se ha podido leer.');
    assert.equal(tresDias, 'Último análisis: 27/10. Ha llegado, pero no se ha podido leer.');
    const tresDiasYUnMinuto = avisoAnalisis(false, new Date(ahora.getTime() - 3 * 86400_000 - 60_000).toISOString(), ahora);
    assert.ok(tresDiasYUnMinuto.includes('hace 3 días'));
    for (const resultado of [dosHoras, unDia, tresDias, tresDiasYUnMinuto]) {
      assert.ok(!resultado.includes('hace 0 días'));
      assert.ok(!resultado.includes('hace 1 días'));
    }
  });

  await check('experimentos avisa a Owner cuando la cola por lanzar llega al tope', async () => {
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.resolve(localRoot, '../../alfred-os/package.json'));
    const bundle = path.join(runRoot, 'experimentos-cola.mjs');
    await require('esbuild').build({ entryPoints: [path.resolve(localRoot, '../../alfred-os/lib/experimentos-cola.ts')], outfile: bundle, bundle: true, platform: 'node', format: 'esm', target: 'node26', logLevel: 'warning' });
    const { avisoColaLanzar, esperaDesde, etiquetaSinMedir, formularioVigente, lineaCifrasCalendario, lineaAtribucionCalendario, descartesDeLanzar, MOTIVOS_DESCARTE, pendientesDeLanzar, seLanzaEnPerfiles, tareasDeLanzar, TAREA_ID } = await import(bundle);
    // Entregas deja en sessionStorage las tareas que Contenido apunta como lanzadas: solo ids válidos, sin repetir y hasta 10.
    assert.deepEqual(tareasDeLanzar('["t_036cee09"]'), ['t_036cee09']);
    assert.deepEqual(tareasDeLanzar('["t_036cee09","t_036cee09"]'), ['t_036cee09']);
    assert.deepEqual(tareasDeLanzar('["../x","-abc","t_ok1"]'), ['t_ok1']);
    assert.equal(tareasDeLanzar(JSON.stringify(Array.from({ length: 12 }, (_, i) => `t_valida${i}`))).length, 10);
    for (const raw of ['no-json', null, '{}', '[3]']) assert.deepEqual(tareasDeLanzar(raw), []);
    assert.deepEqual(tareasDeLanzar(JSON.stringify(['a'.repeat(80)])), ['a'.repeat(80)]);
    assert.deepEqual(tareasDeLanzar(JSON.stringify(['a'.repeat(81)])), []);
    // «No lo voy a lanzar» en Contenido: un cuerpo de descartar por tarea válida, solo con uno de los motivos rápidos.
    assert.deepEqual(descartesDeLanzar(['t_036cee09', 't_036cee09', '../x'], MOTIVOS_DESCARTE[0]), [{ action: 'descartar', tarea: 't_036cee09', texto: MOTIVOS_DESCARTE[0] }]);
    assert.deepEqual(descartesDeLanzar(['t_036cee09'], 'Motivo inventado que no está en la lista.'), []);
    for (const ids of ['["t_036cee09"]', null, {}, 3]) assert.deepEqual(descartesDeLanzar(ids, MOTIVOS_DESCARTE[0]), []);
    assert.equal(descartesDeLanzar(Array.from({ length: 12 }, (_, i) => `t_valida${i}`), MOTIVOS_DESCARTE[1]).length, 10);
    // Tras un fallo a mitad, solo quedan por apuntar las tareas que aún no respondieron bien.
    assert.deepEqual(pendientesDeLanzar(['t_aaa1', 't_bbb2', 't_ccc3'], ['t_aaa1']), ['t_bbb2', 't_ccc3']);
    assert.deepEqual(pendientesDeLanzar(['t_aaa1', 't_bbb2'], ['t_aaa1', 't_bbb2']), []);
    for (const hechas of [null, '{}']) assert.deepEqual(pendientesDeLanzar(['t_aaa1', 't_bbb2'], hechas), ['t_aaa1', 't_bbb2']);
    assert.deepEqual(pendientesDeLanzar(['t_aaa1', 't_aaa1', '../x', 't_bbb2'], []), ['t_aaa1', 't_bbb2']);
    for (const ids of ['["t_aaa1"]', null, {}, 3]) assert.deepEqual(pendientesDeLanzar(ids, []), []);
    assert.equal(pendientesDeLanzar(Array.from({ length: 12 }, (_, i) => `t_valida${i}`)).length, 10);
    const accionesTarea = path.join(runRoot, 'equipo-acciones-tarea.mjs');
    await require('esbuild').build({ entryPoints: [path.join(localRoot, 'equipo-acciones.ts')], outfile: accionesTarea, bundle: true, platform: 'node', format: 'esm', target: 'node26', logLevel: 'warning' });
    const { experimentoArgs: argsTarea } = await import(pathToFileURL(accionesTarea).href);
    for (const tarea of ['t_036cee09', '../x']) assert.equal(TAREA_ID.test(tarea), !argsTarea({ action: 'lanzado', tarea }).error);
    // Mismos límites que RESULTADO_MIN/RESULTADO_MAX de office-limits.ts y equipo.py.
    assert.ok(Array.isArray(MOTIVOS_DESCARTE) && MOTIVOS_DESCARTE.length === 4);
    for (const motivo of MOTIVOS_DESCARTE) assert.ok(motivo.trim().length >= 10 && motivo.trim().length <= 600);
    assert.equal(new Set(MOTIVOS_DESCARTE).size, 4);
    assert.equal(formularioVigente('a', 'a'), true);
    assert.equal(formularioVigente('a', 'b'), false);
    assert.equal(formularioVigente(null, 'a'), false);
    assert.equal(formularioVigente('a', undefined), false);
    assert.equal(formularioVigente('', ''), false);
    assert.equal(seLanzaEnPerfiles(['Contenido: enlace medido por publicación también para Norte', 'tomado del enlace https de su perfil']), true);
    assert.equal(seLanzaEnPerfiles(['Pon un ENLACE en el PERFIL']), true);
    assert.equal(seLanzaEnPerfiles([]), false);
    assert.equal(seLanzaEnPerfiles([undefined, 3, null]), false);
    assert.equal(seLanzaEnPerfiles(['Pon un enlace en la bio']), false);
    assert.equal(seLanzaEnPerfiles(['Revisa el perfil']), false);
    assert.equal(seLanzaEnPerfiles([undefined, 'perfil', 'enlace']), true);
    assert.equal(etiquetaSinMedir('A', 0, 1, 1), 'Sin medir: «A»');
    assert.equal(etiquetaSinMedir('A', 0, 1, 3), 'Sin medir: «A» y 2 más');
    assert.equal(etiquetaSinMedir('B', 1, 5, 7), 'Sin medir (2 de 7): «B»');
    assert.equal(etiquetaSinMedir('B', 1, 2, NaN), 'Sin medir (2 de 2): «B»');
    assert.equal(etiquetaSinMedir('B', 0, 3, 1), 'Sin medir (1 de 3): «B»');
    const ahora = new Date('2026-10-28T12:00:00.000Z');
    const hace = horas => new Date(ahora.getTime() - horas * 3600000).toISOString();
    for (const avisado of [undefined, 'rota', new Date(ahora.getTime() + 3600000).toISOString()]) assert.equal(esperaDesde(avisado, ahora), '');
    assert.equal(esperaDesde(hace(0), ahora), 'desde hoy');
    assert.equal(esperaDesde(hace(23), ahora), 'desde hoy');
    assert.equal(esperaDesde(hace(24), ahora), 'desde hace 1 día');
    assert.equal(esperaDesde(hace(47), ahora), 'desde hace 1 día');
    assert.equal(esperaDesde(hace(49), ahora), 'desde hace 2 días');
    assert.equal(esperaDesde(hace(240), ahora), 'desde hace 10 días');
    for (const total of [0, 2, undefined, NaN, '3']) assert.equal(avisoColaLanzar(total), '');
    for (const total of [3, 7]) assert.equal(avisoColaLanzar(total), `Esperan ${total} experimentos: mientras haya 3 o más, el equipo no prepara otros nuevos. Lanza o descarta alguno.`);
    assert.equal(lineaCifrasCalendario({ apuntadas: 3, publicadas: 5 }), 'Cifras apuntadas del calendario: 3 de 5 publicaciones. Apunta el resto en Contenido → Por medir o con /medir en Telegram.');
    assert.ok(!lineaCifrasCalendario({ apuntadas: 5, publicadas: 5 }).includes('Apunta el resto'));
    const cifrasCalendario = { apuntadas: 3, publicadas: 5 };
    const lineaCifras = 'Cifras apuntadas del calendario: 3 de 5 publicaciones. Apunta el resto en Contenido → Por medir o con /medir en Telegram.';
    assert.equal(lineaCifrasCalendario({ ...cifrasCalendario, hace_7: { dias: 8, apuntadas: 1, publicadas: 4 } }), lineaCifras.replace('.', ' (hace 8 días: 1 de 4).'));
    assert.equal(lineaCifrasCalendario({ ...cifrasCalendario, hace_7: { dias: 7, apuntadas: 2, publicadas: 4 }, hace_14: { dias: 14, apuntadas: 1, publicadas: 3 } }), lineaCifras.replace('.', ' (hace 7 días: 2 de 4; hace 14 días: 1 de 3).'));
    assert.equal(lineaCifrasCalendario({ ...cifrasCalendario, hace_14: { dias: 14, apuntadas: 1, publicadas: 3 } }), lineaCifras.replace('.', ' (hace 14 días: 1 de 3).'));
    for (const hace_14 of [{ dias: 14, apuntadas: 0, publicadas: 0 }, { dias: 14, apuntadas: 4, publicadas: 3 }, { dias: 14.5, apuntadas: 1, publicadas: 3 }, { dias: '3', apuntadas: 1, publicadas: 3 }]) assert.equal(lineaCifrasCalendario({ ...cifrasCalendario, hace_14 }), lineaCifras);
    assert.ok(!lineaCifrasCalendario({ apuntadas: 3, publicadas: 5, hace_7: { dias: 8, apuntadas: 5, publicadas: 4 } }).includes('(hace'));
    for (const cifras of [null, { apuntadas: 3, publicadas: 0 }, { apuntadas: 5, publicadas: 3 }, '3']) assert.equal(lineaCifrasCalendario(cifras), '');
    const atribucion = { con_clics: 2, con_compras: 1, con_cifras: 5 };
    const lineaAtribucion = 'Del calendario con cifras: 2 de 5 con clics y 1 con compras';
    assert.equal(lineaAtribucionCalendario(atribucion), lineaAtribucion);
    assert.equal(lineaAtribucionCalendario({ ...atribucion, hace_7: { dias: 8, con_clics: 1, con_compras: 0, con_cifras: 4 } }), lineaAtribucion + ' (hace 8 días: 1 con clics y 0 con compras de 4)');
    assert.equal(lineaAtribucionCalendario({ ...atribucion, hace_7: { dias: 7, con_clics: 1, con_compras: 0, con_cifras: 4 }, hace_14: { dias: 14, con_clics: 0, con_compras: 0, con_cifras: 3 } }), lineaAtribucion + ' (hace 7 días: 1 con clics y 0 con compras de 4; hace 14 días: 0 con clics y 0 con compras de 3)');
    assert.equal(lineaAtribucionCalendario({ ...atribucion, hace_14: { dias: 14, con_clics: 0, con_compras: 0, con_cifras: 3 } }), lineaAtribucion + ' (hace 14 días: 0 con clics y 0 con compras de 3)');
    for (const hace_14 of [{ dias: 14, con_clics: 0, con_compras: 0, con_cifras: 0 }, { dias: 14, con_clics: 4, con_compras: 0, con_cifras: 3 }, { dias: 14.5, con_clics: 0, con_compras: 0, con_cifras: 3 }, { dias: '3', con_clics: 0, con_compras: 0, con_cifras: 3 }]) assert.equal(lineaAtribucionCalendario({ ...atribucion, hace_14 }), lineaAtribucion);
    for (const valor of [null, '3', { ...atribucion, con_cifras: 0 }]) assert.equal(lineaAtribucionCalendario(valor), '');
    for (const campo of ['con_clics', 'con_compras', 'con_cifras']) {
      for (const valor of [-1, 1.5, '3', null, true, NaN, Infinity]) {
        assert.equal(lineaAtribucionCalendario({ ...atribucion, [campo]: valor }), '');
      }
    }
    for (const campo of ['con_clics', 'con_compras']) assert.equal(lineaAtribucionCalendario({ ...atribucion, [campo]: 6 }), '');
    for (const hace_7 of [null, '3', { dias: 7, ...atribucion, con_cifras: 0 },
      { dias: 7, ...atribucion, con_clics: 6 }, { dias: 7, ...atribucion, con_compras: 6 },
      ...['dias', 'con_clics', 'con_compras', 'con_cifras'].map(campo => ({ dias: 7, ...atribucion, [campo]: 1.5 }))]) {
      assert.equal(lineaAtribucionCalendario({ ...atribucion, hace_7 }), lineaAtribucion);
    }
  });

  await check('Para lanzar excluye fichas en construcción, sin fusionar, lanzadas o descartadas', async () => {
    const appRoot = path.resolve(localRoot, '../../alfred-os');
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.join(appRoot, 'package.json'));
    const bundle = path.join(runRoot, 'launch-ready.cjs');
    await require('esbuild').build({
      stdin: { contents: `export {readyToLaunch} from './app/board';
        export {LaunchSheet} from './app/equipo-panel';`, resolveDir: appRoot },
      outfile: bundle, bundle: true, platform: 'node', format: 'cjs', target: 'node26',
      jsx: 'automatic', loader: { '.css': 'empty' }, logLevel: 'warning',
    });
    const { readyToLaunch, LaunchSheet } = require(bundle);
    const item = { id:'test-ready', fuente:'equipo', columna:'para-ti', fase:'lista para lanzar', fusionada:true,
      lanzamiento:{que:'Comparador <sin publicar>',a_quien:'Traders de futuros',metrica:'Clics',umbral:'Revisar con datos'} };
    assert.equal(readyToLaunch(item), true);
    assert.equal(readyToLaunch({...item,columna:'hecho',fase:'fusionada'}), true);
    for (const patch of [{columna:'cola'}, {columna:'curso'}, {columna:'bloqueado'}, {fuente:'lote'},
      {fuente:'encargo'}, {lanzamiento:undefined}, {fusionada:false}, {fase:'falta fusionar'},
      {lanzado:'2026-10-30T12:00:00Z'}, {descartado:'2026-10-30T12:00:00Z'}, {medido:true},
      {columna:'hecho',fase:'descartado'}]) assert.equal(readyToLaunch({...item,...patch}), false, JSON.stringify(patch));
    const {createElement} = require('react');
    const {renderToStaticMarkup} = require('react-dom/server');
    const rendered = renderToStaticMarkup(createElement(LaunchSheet,{sheet:item.lanzamiento}));
    for (const label of ['Qué se lanza','A quién','Cómo se mide','Umbral']) assert.ok(rendered.includes(label));
    assert.ok(rendered.includes('&lt;sin publicar&gt;'));
    assert.ok(!rendered.includes('<button'));
  });

  await check('experimentos antiguos sin medir siguen visibles aunque no haya actividad reciente', async () => {
    const appRoot = path.resolve(localRoot, '../../alfred-os');
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.join(appRoot, 'package.json'));
    const bundle = path.join(runRoot, 'experimentos-render.cjs');
    await require('esbuild').build({
      stdin: { contents: `import {createElement} from 'react';
        import {renderToStaticMarkup} from 'react-dom/server';
        import {ProjectExperiments} from './app/project-experiments';
        export function renderFixture(status) {
          globalThis.__experimentFixture = status;
          return renderToStaticMarkup(createElement(ProjectExperiments, {team:'demo'}));
        }`, resolveDir: appRoot },
      outfile: bundle, bundle: true, platform: 'node', format: 'cjs', target: 'node26',
      jsx: 'automatic', loader: { '.css': 'empty' }, logLevel: 'warning',
      plugins: [{ name: 'isolated-equipo-fixture', setup(build) {
        build.onResolve({ filter: /^\.\/equipo-panel$/ }, () => ({ path: 'equipo-fixture', namespace: 'fixture' }));
        build.onLoad({ filter: /.*/, namespace: 'fixture' }, () => ({ contents: 'export const useEquipo = () => globalThis.__experimentFixture; export const shareEquipo = () => {};', loader: 'js' }));
      } }],
    });
    const { renderFixture } = require(bundle);
    const status = { tablero: [], proyectos: { demo: { experimentos: [], lanzamientos_7d: {
      avisados: 0, por_lanzar: [], sin_medir_total: 1,
      sin_medir: [{ tarea: 't_old', titulo: 'Experimento de hace veinte días', lanzado: new Date(Date.now() - 20 * 86400000).toISOString() }],
    } } } };
    const rendered = renderFixture(status);
    assert.ok(rendered.includes('Sin medir:'));
    assert.ok(rendered.includes('Experimento de hace veinte días'));
    assert.ok(rendered.includes('Apuntar resultado'));
    status.proyectos.demo.lanzamientos_7d.sin_medir = [];
    status.proyectos.demo.lanzamientos_7d.sin_medir_total = 0;
    assert.equal(renderFixture(status), '');
    status.proyectos.demo.cifras_calendario = { fecha: '2026-11-03', apuntadas: 2, publicadas: 6, hace_7: null };
    const conCifras = renderFixture(status);
    assert.ok(conCifras.includes('Cifras apuntadas del calendario: 2 de 6'));
    assert.ok(conCifras.includes('Por medir'));
    delete status.proyectos.demo.cifras_calendario;
    assert.equal(renderFixture(status), '');
    status.proyectos.demo.atribucion_calendario = { fecha: '2026-11-03', con_clics: 1, con_compras: 0, con_cifras: 2, hace_7: null };
    assert.ok(renderFixture(status).includes('Del calendario con cifras: 1 de 2 con clics y 0 con compras'));
    delete status.proyectos.demo.atribucion_calendario;
    assert.equal(renderFixture(status), '');
    status.proyectos.demo.lanzamientos_7d.por_lanzar = [{ tarea: 't_pend01', titulo: 'Experimento por lanzar', avisado: new Date(Date.now() - 3 * 86400000).toISOString() }];
    status.proyectos.demo.lanzamientos_7d.por_lanzar_total = 1;
    const porLanzar = renderFixture(status);
    assert.ok(porLanzar.includes('Listo para lanzar: «Experimento por lanzar»'));
    assert.ok(porLanzar.includes('Ya lo he lanzado'));
    assert.ok(porLanzar.includes('No lo lanzo'));
    assert.ok(porLanzar.includes('desde hace 3 días'));
    status.proyectos.demo.lanzamientos_7d.por_lanzar = [];
    status.proyectos.demo.lanzamientos_7d.por_lanzar_total = 0;
    assert.equal(renderFixture(status), '');
    delete globalThis.__experimentFixture;
  });

  await check('equipo actions build exact args and refuse bad ids, projects and short results', async () => {
    const require = createRequire(process.env.PROMPTED_OS_DEPS_DIR ? path.join(process.env.PROMPTED_OS_DEPS_DIR, 'package.json') : path.resolve(localRoot, '../../alfred-os/package.json'));
    const bundle = path.join(runRoot, 'equipo-acciones.mjs');
    await require('esbuild').build({ entryPoints: [path.join(localRoot, 'equipo-acciones.ts')], outfile: bundle, bundle: true, platform: 'node', format: 'esm', target: 'node26', logLevel: 'warning' });
    const { experimentoArgs, bloqueadaArgs, bloqueadaResultado, lanzadoBoton, decisionExperimentoBoton, BOTON_DECIDIR, BOTON_LANZADO, BOTON_MEDIDO, BOTON_NO_LANZO, BOTON_MOTIVO, BOTON_FUSIONAR, boton, medidoBoton, noLanzoBoton, motivoBoton, motivosBotones, fusionarBoton, experimentosPendientes, decisionDeTexto, tareaUnicaDeLanzar, preguntaMotivo, tituloDeLanzar, ordenesGrupo } = await import(pathToFileURL(path.join(runRoot, 'equipo-acciones.mjs')).href);
    const { descartesDeLanzar: descartesContenido, MOTIVOS_DESCARTE } = await import(pathToFileURL(path.join(runRoot, 'experimentos-cola.mjs')).href);
    const texto = 'Medí 12 clics en 3 días y salió bien.';
    assert.deepEqual(experimentoArgs({ action: 'lanzado', tarea: 't_036cee09' }), { args: ['lanzado', 't_036cee09', '--via', 'app'] });
    for (const tarea of ['', '../x', 't;rm', '--texto', '-abc', '_abc', 'a'.repeat(81)]) assert.equal(experimentoArgs({ action: 'lanzado', tarea }).error, 'Tarea no válida.');
    for (const tarea of ['t_036cee09', 'a'.repeat(80)]) assert.deepEqual(experimentoArgs({ action: 'lanzado', tarea }), { args: ['lanzado', tarea, '--via', 'app'] });
    assert.equal(experimentoArgs({ action: 'resultado', project: 'demo', texto, tarea: '--x' }).error, 'Tarea no válida.');
    assert.equal(experimentoArgs({ action: 'lanzado' }).error, 'Tarea no válida.');
    for (const decision of ['seguir', 'cambiar', 'parar']) assert.deepEqual(experimentoArgs({ action: 'decidir', tarea: 't_036cee09', decision }), { args: ['decidir', 't_036cee09', '--decision', decision] });
    for (const tarea of ['', '../x', '-abc', undefined]) assert.equal(experimentoArgs({ action: 'decidir', tarea, decision: 'seguir' }).error, 'Tarea no válida.');
    for (const decision of ['quizá', '', undefined]) assert.equal(experimentoArgs({ action: 'decidir', tarea: 't_036cee09', decision }).error, 'Decisión no válida.');
    assert.deepEqual(experimentoArgs({ action: 'decidir', tarea: ' t_036cee09 ', decision: ' seguir ' }), { args: ['decidir', 't_036cee09', '--decision', 'seguir'] });
    assert.deepEqual(bloqueadaArgs({ action: 'desbloquear', project: 'prompted-os', tarea: 't_1234abcd' }), { args: ['desbloquear', 'prompted-os', '--ids', 't_1234abcd'] });
    assert.deepEqual(bloqueadaArgs({ action: 'desbloquear', project: 'prompted-os', tarea: 't_1234abcd', limpio: true }), { args: ['desbloquear', 'prompted-os', '--ids', 't_1234abcd', '--limpio'] });
    assert.deepEqual(bloqueadaArgs({ action: 'desbloquear', project: 'prompted-os', tarea: 't_1234abcd', limpio: 'si' }), { args: ['desbloquear', 'prompted-os', '--ids', 't_1234abcd'] });
    assert.deepEqual(bloqueadaArgs({ action: 'archivar', project: 'prompted-os', tarea: 't_1234abcd' }), { args: ['archivar', 'prompted-os', '--ids', 't_1234abcd'] });
    assert.deepEqual(bloqueadaArgs({ action: 'archivar', project: 'prompted-os', tarea: ' t_1234abcd ' }), { args: ['archivar', 'prompted-os', '--ids', 't_1234abcd'] });
    for (const tarea of ['', 't_1234567', 't_123456789', 'T_1234ABCD', 't_1234ABCD', '../x', '--ids', 't_1234abcd,t_5678abcd', undefined]) assert.equal(bloqueadaArgs({ action: 'desbloquear', project: 'prompted-os', tarea }).error, 'Tarea no válida.');
    for (const project of ['Alfa!', '']) assert.equal(bloqueadaArgs({ action: 'archivar', project, tarea: 't_1234abcd' }).error, 'Proyecto no válido.');
    assert.equal(bloqueadaArgs({ action: 'borrar', project: 'prompted-os', tarea: 't_1234abcd' }).error, 'Acción del equipo no válida.');
    const sinConfirmar = 'El equipo no ha confirmado el cambio: mira el tablero en unos segundos.';
    assert.deepEqual(bloqueadaResultado('{\n "t_1234abcd": "a la cola"\n}\n', 't_1234abcd'), { ok: true });
    assert.deepEqual(bloqueadaResultado('{"t_1234abcd": "archivada"}', 't_1234abcd'), { ok: true });
    assert.equal(bloqueadaResultado('{"t_1234abcd": "no estaba bloqueada"}', 't_1234abcd').error, 'Ya no estaba bloqueada: puede que el equipo ya la haya retomado. El tablero se actualiza en unos segundos.');
    assert.equal(bloqueadaResultado('{"t_1234abcd": "no se ha podido"}', 't_1234abcd').error, 'No se ha podido retirar: sigue en el tablero.');
    assert.equal(bloqueadaResultado('', 't_1234abcd').error, sinConfirmar);
    assert.equal(bloqueadaResultado('no json', 't_1234abcd').error, sinConfirmar);
    assert.equal(bloqueadaResultado('{"t_5678abcd": "a la cola"}', 't_1234abcd').error, sinConfirmar);
    assert.equal(bloqueadaResultado('{"t_1234abcd": "otra cosa"}', 't_1234abcd').error, sinConfirmar);
    assert.equal(BOTON_DECIDIR, 'e:');
    assert.deepEqual(decisionExperimentoBoton('e:parar:t_036cee09'), ['decidir', 't_036cee09', '--decision', 'parar']);
    for (const data of ['e:x:t_1', 'e:parar:../x', 'l:t_1', 'd:tf:5:s']) assert.equal(decisionExperimentoBoton(data), null);
    assert.deepEqual(experimentoArgs({ action: 'resultado', project: 'demo', texto }), { args: ['resultado', 'demo', '--texto', texto] });
    assert.deepEqual(experimentoArgs({ action: 'resultado', project: 'demo', texto, tarea: 't_036cee09' }), { args: ['resultado', 'demo', '--texto', texto, '--experimento', 't_036cee09'] });
    assert.equal(experimentoArgs({ action: 'resultado', project: 'demo', texto, tarea: '../x' }).error, 'Tarea no válida.');
    assert.equal(experimentoArgs({ action: 'resultado', project: 'demo', texto: 'x'.repeat(LIMITS.RESULTADO_MIN - 1) }).error, 'Cuenta qué mediste y qué salió (mínimo 10 caracteres).');
    assert.equal(experimentoArgs({ action: 'resultado', project: 'demo', texto: 'x'.repeat(LIMITS.RESULTADO_MIN) }).args[3].length, LIMITS.RESULTADO_MIN);
    assert.equal(experimentoArgs({ action: 'resultado', project: 'demo', texto: 'x'.repeat(LIMITS.RESULTADO_MAX + 100) }).args[3].length, LIMITS.RESULTADO_MAX);
    assert.equal(experimentoArgs({ action: 'resultado', project: 'Alfa!', texto }).error, 'Proyecto no válido.');
    for (const decision of ['seguir', 'cambiar', 'parar']) {
      assert.deepEqual(experimentoArgs({ action: 'resultado', project: 'demo', texto, tarea: 't_036cee09', decision }), { args: ['resultado', 'demo', '--texto', texto, '--experimento', 't_036cee09', '--decision', decision] });
      assert.deepEqual(experimentoArgs({ action: 'resultado', project: 'demo', texto, decision }), { args: ['resultado', 'demo', '--texto', texto, '--decision', decision] });
    }
    assert.equal(experimentoArgs({ action: 'resultado', project: 'demo', texto, decision: 'quizá' }).error, 'Decisión no válida.');
    assert.equal(decisionDeTexto('Parar: 0 clics'), 'parar');
    assert.equal(decisionDeTexto('  seguir. 12 altas'), 'seguir');
    assert.equal(decisionDeTexto('cambiar'), 'cambiar');
    assert.equal(decisionDeTexto('pararé luego'), null);
    assert.equal(decisionDeTexto('separar'), null);
    assert.equal(decisionDeTexto(null), null);
    const motivo = 'No tengo tiempo para lanzarlo esta semana.';
    assert.deepEqual(experimentoArgs({ action: 'descartar', tarea: 't_036cee09', texto: motivo }), { args: ['descartar', 't_036cee09', '--texto', motivo] });
    for (const tarea of ['', '../x', 't;rm', '--texto', '-abc', '_abc', 'a'.repeat(81)]) assert.equal(experimentoArgs({ action: 'descartar', tarea, texto: motivo }).error, 'Tarea no válida.');
    assert.equal(experimentoArgs({ action: 'descartar', texto: motivo }).error, 'Tarea no válida.');
    assert.equal(experimentoArgs({ action: 'descartar', tarea: 't_036cee09', texto: 'x'.repeat(LIMITS.RESULTADO_MIN - 1) }).error, `Cuenta por qué no lo lanzas (mínimo ${LIMITS.RESULTADO_MIN} caracteres).`);
    assert.equal(experimentoArgs({ action: 'descartar', tarea: 't_036cee09', texto: 'x'.repeat(LIMITS.RESULTADO_MIN) }).args[3].length, LIMITS.RESULTADO_MIN);
    assert.equal(experimentoArgs({ action: 'descartar', tarea: 't_036cee09', texto: 'x'.repeat(LIMITS.RESULTADO_MAX + 100) }).args[3].length, LIMITS.RESULTADO_MAX);
    assert.deepEqual(lanzadoBoton('l:t_036cee09'), { args: ['lanzado', 't_036cee09', '--via', 'telegram'] });
    for (const data of ['l:../x', 'l:-abc', 'l:', 'a:alfa', 'x', '']) assert.equal(lanzadoBoton(data), null);
    assert.equal(BOTON_LANZADO, 'l:');
    assert.equal(BOTON_FUSIONAR, 'f:');
    assert.equal(fusionarBoton('f:demo'), 'demo');
    assert.equal(fusionarBoton('f:betalabs'), 'betalabs');
    for (const data of ['f:', 'f:A', 'f:' + 'a'.repeat(31), 'f:x', 'l:demo', 'f:demo x']) assert.equal(fusionarBoton(data), null);
    assert.equal(lanzadoBoton(BOTON_LANZADO + 'a'.repeat(81)), null);
    for (const tarea of ['t_036cee09', 'a'.repeat(80)]) assert.deepEqual(lanzadoBoton(BOTON_LANZADO + tarea), { args: ['lanzado', tarea, '--via', 'telegram'] });
    assert.equal(BOTON_MEDIDO, 'm:');
    assert.equal(boton('l:', 't_036cee09'), 'l:t_036cee09');
    assert.equal(boton('l:', 'a'.repeat(80)), null);
    assert.equal(boton('m:', '../x'), null);
    assert.equal(medidoBoton('m:t_036cee09'), 't_036cee09');
    for (const data of ['m:../x', 'm:', 'l:t_036cee09', '']) assert.equal(medidoBoton(data), null);
    assert.equal(BOTON_NO_LANZO, 'n:');
    assert.equal(noLanzoBoton('n:t_036cee09'), 't_036cee09');
    for (const data of ['n:../x', 'n:', 'm:t_036cee09', '', 'n:' + 'a'.repeat(81)]) assert.equal(noLanzoBoton(data), null);
    assert.equal(BOTON_MOTIVO, 'k:');
    assert.deepEqual(motivoBoton('k:2:t_036cee09'), { tarea: 't_036cee09', motivo: MOTIVOS_DESCARTE[2] });
    assert.deepEqual(motivoBoton('k:o:t_036cee09'), { tarea: 't_036cee09', motivo: null });
    for (const data of ['k:4:t_036cee09', 'k:0:../x', 'k:0:', 'n:t_036cee09', '', 'k:0:' + 'a'.repeat(81)]) assert.equal(motivoBoton(data), null);
    const motivos = motivosBotones('t_036cee09').inline_keyboard;
    assert.equal(motivos.length, 5);
    assert.ok(motivos.every(fila => fila.length === 1));
    assert.deepEqual(motivos.slice(0, 4).map(fila => fila[0].text), MOTIVOS_DESCARTE);
    assert.equal(motivos[4][0].text, 'Otro motivo (escríbelo)');
    assert.deepEqual(motivos.map(fila => fila[0].callback_data), ['0', '1', '2', '3', 'o'].map(i => BOTON_MOTIVO + i + ':t_036cee09'));
    assert.equal(motivosBotones('a'.repeat(80)), null);
    for (const motivo of MOTIVOS_DESCARTE) assert.deepEqual(experimentoArgs({ action: 'descartar', tarea: 't_036cee09', texto: motivo }), { args: ['descartar', 't_036cee09', '--texto', motivo] });
    // Sincronía: el cuerpo que Contenido envía con «No lo voy a lanzar» lo acepta el servidor tal cual.
    for (const motivo of MOTIVOS_DESCARTE) for (const cuerpo of descartesContenido(['t_036cee09'], motivo)) assert.deepEqual(experimentoArgs(cuerpo), { args: ['descartar', 't_036cee09', '--texto', motivo] });
    assert.equal(tareaUnicaDeLanzar({ tipo: 'lanzar', claves: ['l:t_036cee09'] }), 't_036cee09');
    for (const grupo of [{ tipo: 'medir', claves: ['l:t_036cee09'] }, { tipo: 'lanzar', claves: ['l:t_a1', 'l:t_b2'] }, { tipo: 'lanzar', claves: [] }, { tipo: 'lanzar', claves: ['l:../x'] }, { tipo: 'lanzar', claves: ['q:M12'] }, undefined]) assert.equal(tareaUnicaDeLanzar(grupo), null);
    assert.equal(preguntaMotivo('Anuncio'), '¿Por qué no lanzas «Anuncio»?');
    assert.equal(tituloDeLanzar({ titulo: 'Grupo' }, [{ id: 't_036cee09', titulo: 'Ficha' }], 't_036cee09'), 'Ficha');
    assert.equal(tituloDeLanzar({ titulo: 'Grupo' }, [{ id: 't_otra', titulo: 'Ficha' }], 't_036cee09'), 'Grupo');
    assert.equal(tituloDeLanzar({ titulo: 'Grupo' }, undefined, 't_036cee09'), 'Grupo');
    assert.equal(tituloDeLanzar({ titulo: 'Grupo' }, [{ id: 't_036cee09', titulo: '' }], 't_036cee09'), 'Grupo');
    assert.equal(tituloDeLanzar(undefined, [], 't_036cee09'), '');
    assert.deepEqual(ordenesGrupo({ tipo: 'lanzar', claves: ['l:t_a1', 'l:t_b2'] }, 'x', {}), [
      ['descartar', 't_a1', '--texto', 'No se va a hacer (decidido desde Telegram).'],
      ['descartar', 't_b2', '--texto', 'No se va a hacer (decidido desde Telegram).'],
    ]);
    const lanzamiento = { que: 'Q', a_quien: 'A', metrica: 'M', umbral: 'U' };
    const { porLanzar, faltaMedir } = experimentosPendientes([
      { id: 't_por_lanzar', fuente: 'equipo', columna: 'para-ti', lanzamiento },
      { id: 't_falta_medir', fuente: 'equipo', columna: 'hecho', lanzamiento, lanzado: '2026-10-10T10:00:00', fase: 'falta medir' },
      { id: 't_medido', fuente: 'equipo', columna: 'hecho', lanzamiento, lanzado: '2026-10-10T10:00:00', medido: '2026-10-15T10:00:00', fase: 'falta medir' },
      { id: 't_mesa', fuente: 'mesa', columna: 'para-ti', lanzamiento, lanzado: '2026-10-10T10:00:00', fase: 'falta medir' },
    ]);
    assert.deepEqual(porLanzar.map(it => it.id), ['t_por_lanzar']);
    assert.deepEqual(faltaMedir.map(it => it.id), ['t_falta_medir']);
    const { decisionesExperimentos } = await import(pathToFileURL(bundle).href);
    assert.deepEqual(decisionesExperimentos([]), []);
    assert.deepEqual(decisionesExperimentos(undefined), []);
    const porLanzarItem = id => ({ id, fuente: 'equipo', columna: 'para-ti', lanzamiento });
    const faltaMedirItem = { id: 't_falta_medir', fuente: 'equipo', columna: 'hecho', lanzamiento, lanzado: '2026-10-10T10:00:00', fase: 'falta medir' };
    assert.deepEqual(decisionesExperimentos([porLanzarItem('t_a'), porLanzarItem('t_b'), faltaMedirItem]), ['lanzar 2 experimentos (/lote)', 'apuntar lo medido de 1 experimento (/lote)']);
    assert.deepEqual(decisionesExperimentos([porLanzarItem('t_a')]), ['lanzar 1 experimento (/lote)']);
    const faltaDecidirItem = { ...faltaMedirItem, id: 't_falta_decidir', medido: '2026-10-15T10:00:00', fase: 'falta decidir' };
    const pendientes = experimentosPendientes([
      faltaDecidirItem,
      { ...faltaDecidirItem, id: 't_decidido', decision: 'seguir' },
      { ...faltaDecidirItem, id: 't_mesa_decidir', fuente: 'mesa' },
      { ...faltaDecidirItem, id: 't_sin_lanzamiento', lanzamiento: null },
      { ...faltaDecidirItem, id: 't_sin_lanzar', lanzado: null },
      { ...faltaDecidirItem, id: 't_sin_medir', medido: null },
      { ...faltaDecidirItem, id: 't_otra_fase', fase: 'hecho' },
    ]);
    assert.deepEqual(pendientes.faltaDecidir.map(it => it.id), ['t_falta_decidir']);
    assert.deepEqual(pendientes.porLanzar, []);
    assert.deepEqual(pendientes.faltaMedir, []);
    assert.deepEqual(decisionesExperimentos([porLanzarItem('t_a'), faltaMedirItem, faltaDecidirItem]), ['lanzar 1 experimento (/lote)', 'apuntar lo medido de 1 experimento (/lote)', 'decidir 1 experimento (/lote)']);
    assert.deepEqual(decisionesExperimentos([faltaDecidirItem, { ...faltaDecidirItem, id: 't_otro' }]), ['decidir 2 experimentos (/lote)']);
  });

  await check('telegram reply to the measure question finds its experiment again from the quoted text', async () => {
    const { preguntaMedido, experimentoDeRespuesta, preguntaFreno, experimentoDeFreno } = await import(pathToFileURL(path.join(runRoot, 'equipo-acciones.mjs')).href);
    assert.equal(preguntaMedido('Anuncio medible'), '¿Qué mediste en «Anuncio medible»? Respóndeme a este mensaje.');
    const a = { id: 't_a', titulo: 'Anuncio medible' }, b = { id: 't_b', titulo: 'Otro anuncio' }, c = { id: 't_c', titulo: 'Prueba «x» de precio' };
    assert.equal(experimentoDeRespuesta(preguntaMedido(a.titulo), [b, a]), a);
    assert.equal(experimentoDeRespuesta(preguntaMedido(c.titulo), [a, c]), c);
    assert.equal(experimentoDeRespuesta(preguntaMedido(a.titulo), [a, { ...a, id: 't_dup' }]), null);
    assert.equal(experimentoDeRespuesta('Te respondo sobre «Anuncio medible»', [a]), null);
    assert.equal(experimentoDeRespuesta('', [a]), null);
    assert.equal(experimentoDeRespuesta(undefined, [a]), null);
    assert.equal(experimentoDeRespuesta(preguntaMedido(a.titulo), []), null);
    assert.equal(experimentoDeFreno(preguntaFreno(a.titulo), [b, a]), a);
    assert.equal(experimentoDeFreno(preguntaFreno(a.titulo), [a, { ...a, id: 't_dup' }]), null);
    assert.equal(experimentoDeFreno(preguntaMedido(a.titulo), [a]), null);
    assert.equal(experimentoDeFreno('', [a]), null);
    assert.equal(experimentoDeFreno(undefined, [a]), null);
    assert.equal(experimentoDeFreno(preguntaFreno(a.titulo), []), null);
  });

  await check('telegram morning to-do puts experiments before the other decisions', async () => {
    const { decisionesDelEquipo } = await import(pathToFileURL(path.join(runRoot, 'equipo-acciones.mjs')).href);
    const lanzamiento = { que: 'Q', a_quien: 'A', metrica: 'M', umbral: 'U' };
    const proyecto = nombre => ({ nombre, lote: { estado: 'pendiente', tareas: [{ id: 't' }] }, por_fusionar: 2 });
    const status = {
      proyectos: { a: proyecto('A'), b: proyecto('B'), c: proyecto('C') },
      mesa: { mensajes: [{ id: 'm1', de: 'control', para: ['owner'], tipo: 'pregunta' }, { id: 'm2', de: 'radar', para: ['owner'], tipo: 'aviso' }] },
      tablero: [
        { id: 't_a', fuente: 'equipo', columna: 'para-ti', lanzamiento },
        { id: 't_b', fuente: 'equipo', columna: 'para-ti', lanzamiento },
        { id: 't_m', fuente: 'equipo', columna: 'hecho', lanzamiento, lanzado: '2026-10-10T10:00:00', fase: 'falta medir' },
      ],
    };
    const lines = decisionesDelEquipo(status);
    assert.deepEqual(lines.slice(0, 2), ['lanzar 2 experimentos (/lote)', 'apuntar lo medido de 1 experimento (/lote)']);
    assert.ok(lines.slice(0, 5).includes('lanzar 2 experimentos (/lote)') && lines.slice(0, 5).includes('apuntar lo medido de 1 experimento (/lote)'));
    assert.equal(lines[2], 'decidir 1 cosa de A (/lote)');
    assert.equal(lines.at(-1), 'responder 1 pregunta del equipo (/mesa)');   // warnings do not count as questions
    assert.equal(decisionesDelEquipo({ ...status, tablero: undefined })[0], 'decidir 1 cosa de A (/lote)');
    assert.deepEqual(decisionesDelEquipo(undefined), []);
    assert.deepEqual(decisionesDelEquipo({}), []);
  });

  await check('telegram grouped decisions include measured experiments first and offer their messages', async () => {
    const { decisionesDelEquipo, mensajesFaltaDecidir } = await import(pathToFileURL(path.join(runRoot, 'equipo-acciones.mjs')).href);
    const item = { id: 't_falta_decidir', proyecto: 'a', titulo: 'Experimento', fuente: 'equipo', lanzamiento: { metrica: 'M', umbral: 'U' }, lanzado: '2026-10-10T10:00:00', medido: '2026-10-15T10:00:00', fase: 'falta decidir' };
    const status = { proyectos: { a: { nombre: 'A' } }, para_ti: [{ tipo: 'lanzar', titulo: 'Lanzar la campaña', claves: ['l:t_otro'] }], tablero: [item] };
    assert.deepEqual(decisionesDelEquipo(status), ['decidir 1 experimento (/lote)', 'lanzar la campaña (/lote)']);
    assert.deepEqual(decisionesDelEquipo({ ...status, tablero: [] }), ['lanzar la campaña (/lote)']);
    assert.deepEqual(decisionesDelEquipo({ ...status, tablero: [item, { ...item, id: 't_otro' }] }), ['decidir 2 experimentos (/lote)', 'lanzar la campaña (/lote)']);
    const texto = 'A — falta decidir: «Experimento»\nMétrica: M\nUmbral: U';
    assert.deepEqual(mensajesFaltaDecidir(status), [{ texto, reply_markup: { inline_keyboard: [[
      { text: 'Seguir', callback_data: 'e:seguir:t_falta_decidir' },
      { text: 'Cambiar', callback_data: 'e:cambiar:t_falta_decidir' },
      { text: 'Parar', callback_data: 'e:parar:t_falta_decidir' },
    ]] } }]);
    assert.deepEqual(mensajesFaltaDecidir({ ...status, tablero: [{ ...item, id: 'a'.repeat(81) }] }), [{ texto }]);
    assert.deepEqual(mensajesFaltaDecidir({ ...status, proyectos: {} })[0].texto, 'a — falta decidir: «Experimento»\nMétrica: M\nUmbral: U');
    assert.deepEqual(mensajesFaltaDecidir(undefined), []);
    assert.deepEqual(mensajesFaltaDecidir({}), []);
    assert.deepEqual(mensajesFaltaDecidir({ tablero: {} }), []);
  });

  await check('telegram launch groups warn and offer merging only for pending members', async () => {
    const { fusionPendienteDeGrupo, textoGrupo, botonesGrupo, mensajesFusion } = await import(pathToFileURL(path.join(runRoot, 'equipo-acciones.mjs')).href);
    const g = { id: '1234abcd', tipo: 'lanzar', titulo: 'Lanzar el experimento', claves: ['l:t_lanza'] };
    const item = { id: 't_lanza', fuente: 'equipo', fase: 'lista para lanzar · falta fusionar', proyecto: 'demo' };
    const original = { inline_keyboard: [[
      { text: '✓ Hecho, ya está', callback_data: 'g:1234abcd:h' },
      { text: 'Ahora no', callback_data: 'g:1234abcd:l' },
      { text: '✕ No lo haré', callback_data: 'g:1234abcd:x' },
    ]] };
    const aviso = 'Antes de lanzarlo: aún no está en la versión principal. Pulsa «Fusionar ahora» o espera a la fusión de esta noche.';
    assert.equal(fusionPendienteDeGrupo(g, [item]), 'demo');
    assert.deepEqual(botonesGrupo(g, [item]), { inline_keyboard: [...original.inline_keyboard, [{ text: 'Fusionar ahora', callback_data: 'f:demo' }]] });
    assert.equal(textoGrupo(g, [item]), '📣 Lanzar el experimento\n' + aviso);
    assert.deepEqual(botonesGrupo(g, [{ ...item, fase: 'lista para lanzar' }]), original);
    assert.equal(textoGrupo(g, [{ ...item, fase: 'lista para lanzar' }]), textoGrupo(g));
    assert.equal(fusionPendienteDeGrupo({ ...g, tipo: 'medir' }, [item]), null);
    assert.equal(fusionPendienteDeGrupo({ ...g, claves: ['l:t_desconocida'] }, [item]), null);
    assert.equal(fusionPendienteDeGrupo({ ...g, claves: ['m:t_lanza'] }, [item]), null);
    assert.equal(fusionPendienteDeGrupo(g, [{ ...item, fuente: 'otra' }]), null);
    assert.equal(fusionPendienteDeGrupo(g, [{ ...item, proyecto: 'Proyecto demo' }]), null);
    assert.equal(fusionPendienteDeGrupo(g, {}), null);
    assert.deepEqual(botonesGrupo(g), original);
    assert.equal(textoGrupo(g), '📣 Lanzar el experimento');
    assert.ok(new TextEncoder().encode(botonesGrupo(g, [item]).inline_keyboard[1][0].callback_data).length <= 64);
    const fusion = { proyectos: { 'demo': { nombre: 'Proyecto demo', por_fusionar: 2, mejoras: ['Una mejora', 'Otra mejora'] } } };
    assert.deepEqual(mensajesFusion(fusion), [{ texto: '🔗 Proyecto demo: 2 mejoras terminadas y revisadas por el equipo.\n• Una mejora\n• Otra mejora\nTu parte: decir si las junto a tu proyecto. Antes lo pruebo y solo lo junto si todo sigue funcionando.', reply_markup: { inline_keyboard: [[{ text: 'Sí, júntalas', callback_data: 'f:demo' }]] } }]);
    assert.deepEqual(mensajesFusion(fusion, ['demo']), []);
    assert.deepEqual(mensajesFusion({ proyectos: { a: { por_fusionar: 0 }, b: {} } }), []);
    assert.deepEqual(mensajesFusion({ proyectos: { a: { nombre: 'A', por_fusionar: 1 }, b: { nombre: 'B', por_fusionar: 1 } } }, ['a']), [{ texto: '🔗 B: una mejora terminada y revisada por el equipo.\nTu parte: decir si la junto a tu proyecto. Antes lo pruebo y solo lo junto si todo sigue funcionando.', reply_markup: { inline_keyboard: [[{ text: 'Sí, júntala', callback_data: 'f:b' }]] } }]);
  });

  await check('telegram offers no button when callback_data goes over 64 bytes', async () => {
    const { BOTON_LANZADO, BOTON_MEDIDO, BOTON_DECIDIR, BOTON_MOTIVO, boton } = await import(pathToFileURL(path.join(runRoot, 'equipo-acciones.mjs')).href);
    for (const prefijo of [BOTON_LANZADO, BOTON_MEDIDO, BOTON_MOTIVO + '0:', BOTON_MOTIVO + 'o:', ...['seguir', 'cambiar', 'parar'].map(d => BOTON_DECIDIR + d + ':')]) {
      assert.equal(boton(prefijo, 'a'.repeat(80)), null);
      assert.equal(boton(prefijo, 't_036cee09'), prefijo + 't_036cee09');
    }
  });

  const directory = path.join(runRoot, 'runtime');
  fs.mkdirSync(directory, { mode: 0o700 });
  fs.writeFileSync(path.join(directory, 'initial-import.json'), JSON.stringify(fixture()), { mode: 0o600 });
  current = await start(directory);

  await check('HTTP auth rejects missing/invalid cookie and misplaced launch token', async () => {
    assert.equal((await request('/api/office', { cookie: null })).status, 401);
    assert.equal((await request('/api/office', { cookie: 'os_local=invalid' })).status, 401);
    assert.equal((await request('/api/office', { cookie: null, headers: { 'X-OS-Launch': current.bootstrapToken } })).status, 401);
    const wrong = await request('/', { cookie: null, headers: { 'X-OS-Launch': 'invalid' } });
    assert.equal(wrong.status, 401); assert.equal(wrong.headers.get('set-cookie'), null);
  });

  await check('bootstrap sets private cookie without putting secrets in content', bootstrap);
  await check('import preserves history but admits no imported work', async () => {
    const response = await request('/api/office'); assert.equal(response.status, 200);
    assert.equal(response.data.settings.focus, P2);
    assert.equal(response.data.focusSession.note, 'Existing synthetic note');
    assert.equal(response.data.local.bridge, 'disabled');
    assert.equal(response.data.entries.find(e => e.id === 'imported-queued').status, 'parked');
    assert.equal(response.data.entries.find(e => e.id === 'imported-active').status, 'parked');
    assert.equal(inspect(directory, db => db.prepare('SELECT status FROM entries WHERE id=?').get('imported-queued').status), 'queued');
    assert.equal(inspect(directory, db => db.prepare('SELECT status FROM entries WHERE id=?').get('imported-active').status), 'dispatching');
    assert.equal(inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions').get().n), 0);
    assert.ok(!response.text.includes(current.bootstrapToken));
    assert.ok(!response.text.includes(current.cookie.slice('os_local='.length)));
  });

  await check('POST requires matching Origin and rejects cross-site/Host tricks', async () => {
    const value = { action: 'settings', energy: 'low' };
    assert.equal((await request('/api/office', { method: 'POST', value })).status, 403, 'Missing Origin');
    assert.equal((await request('/api/office', { method: 'POST', value, origin: 'https://example.invalid' })).status, 403, 'Foreign Origin');
    assert.equal(await rawHostStatus('example.invalid'), 403, 'Foreign Host');
    assert.equal((await request('/api/office', { headers: { 'Sec-Fetch-Site': 'cross-site' } })).status, 403, 'Cross-site request');
    assert.equal((await post('/api/office', value)).status, 200);
    const state = (await request('/api/office')).data;
    assert.equal(state.settings.energy, 'low'); assert.equal(state.settings.focus, P2);
  });

  await check('local cookie cannot invoke reserved bridge actions', async () => {
    assert.equal((await post('/api/office', { action: 'snapshot', snapshot: {} })).status, 403);
    assert.equal((await post('/api/office', { action: 'claim', id: 'imported-queued' })).status, 403);
    assert.equal((await post('/api/bridge', { method: 'tools/call', params: {} })).status, 401);
  });

  const captureId = randomUUID();
  await check('concurrent deterministic chat retries create one idea and message pair', async () => {
    const message = { id: captureId, text: 'Guarda idea: Synthetic isolated capture.' };
    const responses = await Promise.all([post('/api/chat', message), post('/api/chat', message)]);
    responses.forEach(r => assert.equal(r.status, 200));
    assert.equal(responses.filter(r => r.data.duplicate).length, 1);
    const data = (await request('/api/office')).data;
    assert.equal(data.entries.filter(e => e.id === 'idea-' + captureId).length, 1);
    assert.equal(data.messages.filter(m => m.id === captureId && m.author === 'user').length, 1);
    assert.equal(data.messages.filter(m => m.id === captureId && m.author === 'assistant').length, 1);
    assert.equal(inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions').get().n), 0);
    assert.equal((await post('/api/chat', { id: captureId, text: 'Guarda idea: Different content.' })).status, 400);
  });

  const conversationId = randomUUID();
  await check('explicit new work is admitted once while inference stays disabled', async () => {
    const response = await post('/api/chat', { id: conversationId, text: 'Analiza esta petición sintética aislada.' });
    assert.equal(response.status, 200); assert.equal(response.data.requestId, 'chat-' + conversationId);
    const duplicate = await post('/api/chat', { id: conversationId, text: 'Analiza esta petición sintética aislada.' });
    assert.equal(duplicate.data.duplicate, true);
    assert.equal(inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions WHERE id=?').get('chat-' + conversationId).n), 1);
    assert.equal(inspect(directory, db => db.prepare("SELECT COUNT(*) n FROM local_admissions WHERE id LIKE 'imported-%'").get().n), 0);
    assert.equal((await request('/api/office')).data.entries.find(e => e.id === 'chat-' + conversationId).status, 'queued');
  });

  await check('focus writes preserve context and reject stale revisions', async () => {
    const response = await post('/api/office', { action: 'focus', command: 'note', id: 'isolated-focus', revision: 1, note: 'Updated synthetic checkpoint' });
    assert.equal(response.status, 200); assert.equal(response.data.session.revision, 2);
    assert.equal(response.data.session.project, P2); assert.equal(response.data.session.status, 'paused');
    assert.equal((await post('/api/office', { action: 'focus', command: 'note', id: 'isolated-focus', revision: 1, note: 'Stale overwrite' })).status, 400);
    assert.equal((await request('/api/office')).data.focusSession.note, 'Updated synthetic checkpoint');
  });

  await check('second instance is rejected before mutating active local work', async () => {
    const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
    try { db.prepare("UPDATE entries SET status='dispatching' WHERE id=?").run('chat-' + conversationId); } finally { db.close(); }
    const child = spawn(process.execPath, [serverFile], {
      cwd: localRoot, env: { ...process.env, PROMPTED_OS_DATA_DIR: directory, PROMPTED_OS_DISABLE_BRIDGE: '1', PROMPTED_OS_DISABLE_TELEGRAM: '1' }, stdio: ['pipe', 'pipe', 'pipe'],
    });
    processes.add(child);
    let stdout = ''; child.stdout.on('data', data => { stdout += data.toString(); }); child.stderr.on('data', () => {});
    const code = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('Second instance failed to exit')); }, 7000);
      child.once('error', reject); child.once('exit', code => { clearTimeout(timer); processes.delete(child); resolve(code); });
    });
    assert.equal(code, 1); assert.ok(!stdout.includes('READY '));
    assert.equal(inspect(directory, db => db.prepare('SELECT status FROM entries WHERE id=?').get('chat-' + conversationId).status), 'dispatching');
    assert.equal((await request('/api/office')).status, 200);
  });

  await check('review can be returned with feedback and is re-admitted', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic revise', body: 'Synthetic body' });
    assert.equal(created.status, 200); const id = created.data.id;
    const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
    try { db.prepare("UPDATE entries SET status='review',result=? WHERE id=?").run('Synthetic result', id); db.prepare('DELETE FROM local_admissions WHERE id=?').run(id); } finally { db.close(); }
    assert.equal((await post('/api/office', { action: 'revise', id, feedback: '   ' })).status, 400);
    for (const value of [{ action: 'revise', id, feedback: 123 }, { action: 'revise', id }]) {
      const rejected = await post('/api/office', value);
      assert.equal(rejected.status, 400); assert.equal(rejected.data.error, 'Escribe qué hay que cambiar.');
    }
    assert.equal((await post('/api/office', { action: 'revise', id, feedback: 'Synthetic feedback' })).status, 200);
    const entry = (await request('/api/office')).data.entries.find(e => e.id === id);
    assert.equal(entry.status, 'queued'); assert.equal(entry.result, 'Synthetic result');
    assert.ok(entry.body.includes('Cambios pedidos (')); assert.ok(entry.body.includes('Synthetic feedback'));
    assert.equal(inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions WHERE id=?').get(id).n), 1);
    assert.equal((await post('/api/office', { action: 'revise', id, feedback: 'Synthetic feedback' })).status, 400);
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
  });

  await check('revise stops growing a request past the accumulated body limit', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic revise limit', body: 'x'.repeat(LIMITS.ENTRY_BODY_MAX) });
    assert.equal(created.status, 200); const id = created.data.id;
    const toReview = () => {
      const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
      try { db.prepare("UPDATE entries SET status='review',result=? WHERE id=?").run('Synthetic result', id); db.prepare('DELETE FROM local_admissions WHERE id=?').run(id); } finally { db.close(); }
    };
    let limited = null;
    for (let round = 0; round < 10 && !limited; round++) {
      toReview();
      const before = (await request('/api/office')).data.entries.find(e => e.id === id).body;
      const response = await post('/api/office', { action: 'revise', id, feedback: 'y'.repeat(LIMITS.REVISE_FEEDBACK_MAX) });
      if (response.status === 400) limited = { response, before };
      else assert.equal(response.status, 200);
    }
    assert.ok(limited, 'Revise never hit the body limit');
    assert.equal(limited.response.data.error, 'Este encargo ya acumula demasiados cambios. Acéptalo o crea uno nuevo.');
    const entry = (await request('/api/office')).data.entries.find(e => e.id === id);
    assert.equal(entry.status, 'review'); assert.equal(entry.body, limited.before);
    assert.ok(limited.before.length <= LIMITS.REQUEST_BODY_MAX);
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
  });

  await check('a reviewed request can be accepted and then stays closed', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic accept', body: 'Synthetic body' });
    assert.equal(created.status, 200); const id = created.data.id;
    const early = await post('/api/office', { action: 'update', id, status: 'accepted' });
    assert.equal(early.status, 400); assert.equal(early.data.error, 'Primero hace falta un resultado para revisar.');
    const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
    try { db.prepare("UPDATE entries SET status='review',result=? WHERE id=?").run('Synthetic result', id); db.prepare('DELETE FROM local_admissions WHERE id=?').run(id); } finally { db.close(); }
    assert.equal((await post('/api/office', { action: 'update', id, status: 'accepted' })).status, 200);
    const entry = (await request('/api/office')).data.entries.find(e => e.id === id);
    assert.equal(entry.status, 'accepted'); assert.equal(entry.result, 'Synthetic result');
    assert.equal((await post('/api/office', { action: 'revise', id, feedback: 'Synthetic feedback' })).status, 400);
    assert.equal((await post('/api/office', { action: 'update', id, status: 'queued' })).status, 400);
    for (const status of ['cancelled', 'parked']) {
      const closed = await post('/api/office', { action: 'update', id, status });
      assert.equal(closed.status, 400); assert.equal(closed.data.error, 'Este encargo ya está cerrado.');
    }
    assert.equal((await request('/api/office')).data.entries.find(e => e.id === id).status, 'accepted');
  });

  await check('create trims title and body to the shared limits', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'x'.repeat(LIMITS.ENTRY_TITLE_MAX + 20), body: 'y'.repeat(LIMITS.ENTRY_BODY_MAX + 100) });
    assert.equal(created.status, 200); const id = created.data.id;
    const entry = (await request('/api/office')).data.entries.find(e => e.id === id);
    assert.equal(entry.title.length, LIMITS.ENTRY_TITLE_MAX); assert.equal(entry.body.length, LIMITS.ENTRY_BODY_MAX);
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
    const exact = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'x'.repeat(LIMITS.ENTRY_TITLE_MAX), body: 'y'.repeat(LIMITS.ENTRY_BODY_MAX) });
    assert.equal(exact.status, 200);
    const kept = (await request('/api/office')).data.entries.find(e => e.id === exact.data.id);
    assert.equal(kept.title, 'x'.repeat(LIMITS.ENTRY_TITLE_MAX)); assert.equal(kept.body, 'y'.repeat(LIMITS.ENTRY_BODY_MAX));
    assert.equal((await post('/api/office', { action: 'update', id: exact.data.id, status: 'cancelled' })).status, 200);
  });

  await check('revise keeps feedback up to the shared limit', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic revise feedback limit', body: 'Synthetic body' });
    assert.equal(created.status, 200); const id = created.data.id;
    const toReview = () => {
      const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
      try { db.prepare("UPDATE entries SET status='review',result=? WHERE id=?").run('Synthetic result', id); db.prepare('DELETE FROM local_admissions WHERE id=?').run(id); } finally { db.close(); }
    };
    const bodyOf = async () => (await request('/api/office')).data.entries.find(e => e.id === id).body;
    const exact = 'f'.repeat(LIMITS.REVISE_FEEDBACK_MAX);
    toReview();
    assert.equal((await post('/api/office', { action: 'revise', id, feedback: exact })).status, 200);
    assert.ok((await bodyOf()).endsWith(exact));
    const over = 'g'.repeat(LIMITS.REVISE_FEEDBACK_MAX + 1);
    toReview();
    assert.equal((await post('/api/office', { action: 'revise', id, feedback: over })).status, 200);
    const body = await bodyOf();
    assert.ok(body.length <= LIMITS.REQUEST_BODY_MAX);
    assert.ok(body.endsWith('g'.repeat(LIMITS.REVISE_FEEDBACK_MAX)));
    assert.ok(!body.endsWith(over));
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
  });

  await check('a cancelled request stays closed', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic cancelled', body: 'Synthetic body' });
    assert.equal(created.status, 200); const id = created.data.id;
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
    const admissions = () => inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions WHERE id=?').get(id).n);
    const before = admissions();
    const accepted = await post('/api/office', { action: 'update', id, status: 'accepted' });
    assert.equal(accepted.status, 400); assert.equal(accepted.data.error, 'Primero hace falta un resultado para revisar.');
    const revised = await post('/api/office', { action: 'revise', id, feedback: 'Synthetic feedback' });
    assert.equal(revised.status, 400); assert.equal(revised.data.error, 'Solo se pueden pedir cambios sobre un resultado pendiente de revisión.');
    const parked = await post('/api/office', { action: 'update', id, status: 'parked' });
    assert.equal(parked.status, 400); assert.equal(parked.data.error, 'Este encargo ya está cerrado.');
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
    assert.equal((await request('/api/office')).data.entries.find(e => e.id === id).status, 'cancelled');
    assert.equal(admissions(), before);
  });

  await check('a blocked request can be retried or cancelled, and a cancelled one stays out of the queue', async () => {
    const force = (id, status, result, kind) => {
      const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
      try {
        db.prepare('UPDATE entries SET status=?,result=? WHERE id=?').run(status, result, id);
        if (kind) db.prepare('UPDATE entries SET kind=? WHERE id=?').run(kind, id);
        db.prepare('DELETE FROM local_admissions WHERE id=?').run(id);
      } finally { db.close(); }
    };
    const admissions = id => inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions WHERE id=?').get(id).n);
    const entryOf = async id => (await request('/api/office')).data.entries.find(e => e.id === id);
    const refused = 'No se puede volver a despachar este encargo.';

    const retried = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic blocked retry', body: 'Synthetic body' });
    assert.equal(retried.status, 200); const retryId = retried.data.id;
    force(retryId, 'blocked', 'Synthetic block reason');
    assert.equal((await post('/api/office', { action: 'update', id: retryId, status: 'queued', result: 'Owner override' })).status, 200);
    const requeued = await entryOf(retryId);
    assert.equal(requeued.status, 'queued'); assert.equal(requeued.result, 'Synthetic block reason');
    assert.equal(admissions(retryId), 1);
    assert.equal((await post('/api/office', { action: 'update', id: retryId, status: 'cancelled' })).status, 200);

    const cancelled = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic blocked cancel', body: 'Synthetic body' });
    assert.equal(cancelled.status, 200); const cancelId = cancelled.data.id;
    force(cancelId, 'blocked', 'Synthetic block reason');
    assert.equal((await post('/api/office', { action: 'update', id: cancelId, status: 'cancelled' })).status, 200);
    const before = admissions(cancelId);
    const again = await post('/api/office', { action: 'update', id: cancelId, status: 'queued' });
    assert.equal(again.status, 400); assert.equal(again.data.error, refused);
    assert.equal((await entryOf(cancelId)).status, 'cancelled');
    assert.equal(admissions(cancelId), before);

    const other = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic blocked conversation', body: 'Synthetic body' });
    assert.equal(other.status, 200); const otherId = other.data.id;
    force(otherId, 'blocked', 'x', 'conversation');
    const conversation = await post('/api/office', { action: 'update', id: otherId, status: 'queued' });
    assert.equal(conversation.status, 400); assert.equal(conversation.data.error, refused);
    force(otherId, 'cancelled', 'x');
  });

  await check('a blocked request can be retried and is re-admitted', async () => {
    const created = await post('/api/office', { action: 'create', kind: 'request', project: P1, role: 'radar', title: 'Synthetic blocked readmit', body: 'Synthetic body' });
    assert.equal(created.status, 200); const id = created.data.id;
    const db = new DatabaseSync(path.join(directory, 'workspace.sqlite'));
    try { db.prepare("UPDATE entries SET status='blocked',result=? WHERE id=?").run('Synthetic block reason', id); db.prepare('DELETE FROM local_admissions WHERE id=?').run(id); } finally { db.close(); }
    assert.equal((await post('/api/office', { action: 'update', id, status: 'queued' })).status, 200);
    assert.equal((await request('/api/office')).data.entries.find(e => e.id === id).status, 'queued');
    assert.equal(inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_admissions WHERE id=?').get(id).n), 1);
    assert.equal((await post('/api/office', { action: 'update', id, status: 'cancelled' })).status, 200);
  });

  await check('setup saves the profile, checks folders and repositories, and keeps the team off without its engine', async () => {
    let r = await request('/api/configurar');
    assert.equal(r.status, 200); assert.equal(r.data.listo, false); assert.equal(r.data.claude.instalado, false); assert.equal(r.data.equipo.instalado, false);
    assert.equal((await request('/api/configurar', { method: 'POST', origin: 'http://evil.test', value: { accion: 'perfil', nombre: 'Ana' } })).status, 403);
    assert.equal((await post('/api/configurar', { accion: 'perfil', nombre: '  ' })).status, 400);
    r = await post('/api/configurar', { accion: 'perfil', nombre: ' Ana ', apodo: 'Anita' });
    assert.equal(r.status, 200); assert.deepEqual(r.data.estado.perfil, { nombre: 'Ana', apodo: 'Anita' });
    assert.deepEqual(JSON.parse(fs.readFileSync(path.join(directory, 'perfil', 'perfil.json'), 'utf8')), { nombre: 'Ana', apodo: 'Anita' });
    assert.equal((await request('/api/perfil')).data.nombre, 'Ana');
    assert.equal((await post('/api/configurar', { accion: 'notas', ruta: path.join(runRoot, 'no-existe') })).data.error, 'Esa carpeta no existe.');
    const notas = path.join(runRoot, 'notas-ana'); fs.mkdirSync(notas, { recursive: true });
    r = await post('/api/configurar', { accion: 'notas', ruta: notas });
    assert.equal(r.status, 200); assert.equal(r.data.reiniciar, true);
    assert.equal(JSON.parse(fs.readFileSync(path.join(directory, 'configuracion.json'), 'utf8')).vault, notas);
    assert.equal((await post('/api/configurar', { accion: 'proyecto', nombre: 'Huerto', repo: notas })).data.error, 'Esa carpeta no es un repositorio de git.');
    const repo = path.join(runRoot, 'huerto'); fs.mkdirSync(repo, { recursive: true });
    for (const args of [['init', '-q', '-b', 'main'], ['-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '--allow-empty', '-m', 'x']]) execFileSync('git', ['-C', repo, ...args]);
    r = await post('/api/configurar', { accion: 'proyecto', nombre: 'Huerto Ana', repo, objetivo: 'Primeros clientes' });
    assert.equal(r.status, 200);
    const cfg = JSON.parse(fs.readFileSync(path.join(directory, 'equipo', 'projects.json'), 'utf8'));
    assert.deepEqual(Object.keys(cfg.projects), ['huerto-ana']);
    assert.equal(cfg.projects['huerto-ana'].base, 'main'); assert.equal(cfg.projects['huerto-ana'].board, 'equipo-huerto-ana');
    assert.equal((await post('/api/configurar', { accion: 'proyecto', nombre: 'Huerto Ana', repo })).data.error, 'Ya hay un proyecto con ese nombre.');
    assert.equal((await post('/api/configurar', { accion: 'servicio', activo: true })).data.error, 'El equipo no viene en esta copia.');
    assert.equal((await post('/api/configurar', { accion: 'quitar-proyecto', id: 'huerto-ana' })).status, 200);
    assert.equal((await post('/api/configurar', { accion: 'otra' })).status, 400);
  });

  const previous = { origin: current.origin, cookie: current.cookie, bootstrapToken: current.bootstrapToken };
  await stop(current);
  current = await start(directory);
  await check('restart preserves port and data while rotating authentication', async () => {
    assert.equal(current.origin, previous.origin);
    assert.notEqual(current.bootstrapToken, previous.bootstrapToken);
    assert.equal((await request('/api/office', { cookie: previous.cookie })).status, 401);
    await bootstrap();
    const data = (await request('/api/office')).data;
    assert.equal(data.settings.energy, 'low'); assert.equal(data.settings.focus, P2);
    assert.equal(data.focusSession.note, 'Updated synthetic checkpoint');
    assert.ok(data.entries.some(e => e.id === 'idea-' + captureId));
    assert.ok(data.entries.some(e => e.id === 'chat-' + conversationId));
    assert.equal(data.entries.find(e => e.id === 'chat-' + conversationId).status, 'blocked');
    const recovery = data.messages.filter(m => m.id === conversationId && m.author === 'assistant');
    assert.equal(recovery.length, 1); assert.match(recovery[0].text, /No se ha vuelto a ejecutar/);
    assert.equal(inspect(directory, db => db.prepare('SELECT status FROM entries WHERE id=?').get('imported-active').status), 'dispatching');
    assert.equal(inspect(directory, db => db.prepare('SELECT COUNT(*) n FROM local_metadata').get().n), 1);
    assert.equal(inspect(directory, db => db.prepare("SELECT COUNT(*) n FROM local_admissions WHERE id LIKE 'imported-%'").get().n), 0);
    const duplicate = await post('/api/chat', { id: captureId, text: 'Guarda idea: Synthetic isolated capture.' });
    assert.equal(duplicate.data.duplicate, true);
  });
} catch (error) {
  if (!checks.some(c => !c.passed)) checks.push({ name: 'runtime harness', passed: false, error: error.message });
  process.exitCode = 1;
} finally {
  await Promise.all([...processes].map(child => stop({ child })));
  const summary = {
    serverSha256: fs.existsSync(serverFile) ? createHash('sha256').update(fs.readFileSync(serverFile)).digest('hex') : null,
    passed: checks.filter(c => c.passed).length,
    failed: checks.filter(c => !c.passed).length,
    checks,
  };
  fs.writeFileSync(path.join(runRoot, 'results.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(`RESULT ${summary.passed} passed, ${summary.failed} failed. Fixtures and results: ${runRoot}`);
}
