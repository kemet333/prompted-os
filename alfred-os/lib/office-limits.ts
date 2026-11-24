// Límites de texto compartidos por el motor (alfred-store, alfred-chat) y los formularios.
export const REVISE_FEEDBACK_MAX=2000;
export const REQUEST_BODY_MAX=12000;
export const ENTRY_TITLE_MAX=180;
export const ENTRY_BODY_MAX=6000;
export const RESULTADO_MIN=10;
export const RESULTADO_MAX=600;
export const POR_LANZAR_TOPE=3;
export const ANALISIS_DIAS_VIGENTE=3;
export const FASE_FALTA_FUSIONAR='lista para lanzar · falta fusionar';
// Id of a task on the team board: same rule as ID_TABLERO in equipo/equipo.py.
export const TAREA_TABLERO=/^t_[0-9a-f]{8}$/;
export const DECISIONES=['seguir','cambiar','parar'] as const;
export type Decision=typeof DECISIONES[number];
