// What Alfred and the team send to Owner's group (the same messages and buttons as Telegram) and what his votes mean, without
// WhatsApp itself. A message with buttons becomes a poll: one tap is the same as tapping the Telegram button. Today's to-do
// becomes a multiple-choice poll where ticking a task is ticking it in the list.
//
// The app (local-workspace/whatsapp.ts) and the team's engine (equipo.py) leave each message as a JSON file in
// Workspace/whatsapp/salida: {ref, texto, botones?: [[{text, callback_data}]], responder?}. `ref` is the number Telegram code
// keeps as the message id; a reply to that message comes back with it.
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export const MAX_OPCIONES=12, MAX_NOMBRE=255, MAX_OPCION=100;
const corta=(s,n)=>{s=String(s||'').replace(/\s+/g,' ').trim();return s.length<=n?s:s.slice(0,n-1).trimEnd()+'…';};
const hash=(s)=>crypto.createHash('sha256').update(Buffer.from(s)).digest('hex');

/** A message from the outbox → what to send: the text, and the poll (with each option's button) when it has buttons. */
export function plan(entrada){
 const texto=String(entrada?.texto||'').trim();
 const botones=(Array.isArray(entrada?.botones)?entrada.botones.flat():[])
  .filter(b=>b&&typeof b.callback_data==='string'&&b.callback_data!=='x'&&String(b.text||'').trim());
 if(!botones.length)return {texto,encuesta:null,opciones:[]};
 // The to-do: one option per task, as many as he wants ticked; the ✓ of the Telegram buttons is the poll's own tick.
 const lista=botones.every(b=>/^h:\d{4}-\d{2}-\d{2}:\d{1,2}$/.test(b.callback_data));
 const opciones=[],usados=new Set();
 for(const b of botones.slice(0,MAX_OPCIONES)){
  let nombre=corta(lista?String(b.text).replace(/^✓\s*/,''):b.text,MAX_OPCION),n=2;
  while(usados.has(nombre))nombre=corta(b.text,MAX_OPCION-4)+' ('+(n++)+')';   // WhatsApp needs every option different
  usados.add(nombre);opciones.push({texto:nombre,data:b.callback_data});
 }
 // A short text is the poll's question; a long one (the to-do, a group of decisions) goes first and the poll asks below it.
 const enUna=!lista&&texto.length<=MAX_NOMBRE;
 const nombre=lista?'Marca lo que ya has hecho':enUna?texto:'¿Qué hacemos?';
 return {texto:enUna?'':texto,encuesta:{name:nombre,values:opciones.map(o=>o.texto),selectableCount:lista?opciones.length:1},opciones,lista};
}

/** A vote (the SHA-256 of each option now chosen) → the buttons it presses and the selection to remember. A poll vote carries
 *  the whole current choice, so only what was not chosen before counts; unticking presses nothing (a task stays done). */
export function voto(registro,hashes,antes=[]){
 const elegidas=registro.opciones.filter(o=>hashes.some(h=>Buffer.from(h).toString('hex')===hash(o.texto)||h===hash(o.texto))).map(o=>o.texto);
 const nuevas=elegidas.filter(t=>!antes.includes(t));
 // A decision is taken once, like a Telegram button that disappears after the tap: changing the vote later does nothing.
 if(!registro.lista&&(antes.length||nuevas.length>1))nuevas.splice(antes.length?0:1);
 return {pulsar:registro.opciones.filter(o=>nuevas.includes(o.texto)).map(o=>o.data),seleccion:elegidas};
}

// ---------- Agent Bar: Claude Code alerts that reach the group when Owner isn't at the Mac ----------
// Agent Bar leaves them in the outbox with ref `ab:<file>` and buttons `ab:<file>:c` (continue) / `ab:<file>:p` (stop). That
// conversation's Claude Code hook waits for <file>.decision in Agent Bar's folder while <file>.waiting exists: a vote or a reply
// quoting the message is written there and goes to that exact conversation. No app of Owner OS is involved.
export const AGENT_BAR=path.join(os.homedir(),'Library','Application Support','Agent Deck','hooks');
/** ref or button data → 'enviado' | 'parado' | 'tarde' (the hook stopped waiting), or null if it isn't Agent Bar's. */
export function agentBar(ref,texto='',dir=AGENT_BAR){
 const m=/^ab:(\d+-\d+\.json)(?::([cp]))?$/.exec(String(ref||''));
 if(!m)return null;
 const [,archivo,op]=m;
 if(!fs.existsSync(path.join(dir,archivo+'.waiting')))return 'tarde';
 const t=String(texto||'').trim();
 const d=op==='p'?{action:'release'}:{action:'continue',text:op==='c'||!t?'continúa':t.slice(0,4000)};
 fs.writeFileSync(path.join(dir,archivo+'.decision'),JSON.stringify(d),{mode:0o600});
 return op==='p'?'parado':'enviado';
}
export const agentBarAviso={enviado:'✅ Enviado a esa conversación de Claude Code.',parado:'⏹️ Parada.',tarde:'⌛ Esa conversación ya no esperaba (pasan 10 min): contéstala en el Mac.'};
