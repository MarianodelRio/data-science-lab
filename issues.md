# Issues detectados durante la migración dev-team v1.0.0 → v1.4.0

**Fecha:** 2026-08-18 · **Repo:** `data-science-lab` (rama `main`, sin commitear)
**Referencia upstream:** `MarianodelRio/dev-team` @ `main` (v1.4.0), clonado en `/tmp/dev-team-ref`

Todo lo de abajo se encontró **verificando** la migración descrita en `migration.md`, no
ejecutándola a ciegas. Cada issue lleva reproducción, evidencia observada y fix propuesto.

Clasificación:

| Bloque | Dónde se arregla | Estado |
|---|---|---|
| **A. Bugs upstream** | Issues/PR en `MarianodelRio/dev-team` — afectan a todo proyecto que use v1.4 | ⏳ **PENDIENTE** — se aplicarán vía update cuando dev-team esté arreglado |
| **B. Errores en `migration.md`** | El propio documento de migración, si se reutiliza en otro repo | ✅ **CORREGIDO** (B-1, B-3) · B-2 depende de A-3 |
| **C. Deuda local** | Este repo, tras la migración | ✅ **CORREGIDO** (C-1, C-2) · C-3/C-4 requieren decisión humana |

> **Regla de reparto acordada (2026-08-18):** lo que sea de dev-team se deja intacto en
> este repo y se arregla upstream; lo demás se corrige aquí. `scripts/` y
> `.claude/{agents,commands,steering}/` de framework **no se tocan localmente** para que
> el próximo update de dev-team aplique limpio.

---

## A. Bugs upstream en dev-team v1.4.0

### A-1 · `dt-board.sh` solo indexa `tasks/available/` — SEVERIDAD ALTA

> **Estado: PENDIENTE UPSTREAM.** No parcheado localmente a propósito.

**Fichero:** `scripts/dt-board.sh:53`

En v1.4 `dt-common.sh:80` declara `TASK_FOLDERS` como **array bash**:

```bash
TASK_FOLDERS=(
  "available" "in-progress" "ready-for-pr" "pr-open" "done" "blocked" "cancelled"
)
```

`dt-common.sh:94` (`find_task_file`) lo expande correctamente como `"${TASK_FOLDERS[@]}"`,
pero `dt-board.sh:53` sigue usando la forma escalar de v1.0:

```bash
for folder in $TASK_FOLDERS; do        # ← devuelve SOLO "available"
```

En bash, `$ARRAY` sin subíndice devuelve únicamente el primer elemento. El bucle de la
Pass 1 nunca entra en `done/`, `blocked/`, `in-progress/`, etc.

**Reproducción**

```bash
source scripts/dt-common.sh
echo "$TASK_FOLDERS"          # available
echo "${TASK_FOLDERS[@]}"     # available in-progress ready-for-pr pr-open done blocked cancelled
./scripts/dt-board.sh --print --no-fetch
```

**Observado** (este repo tiene 48 tareas: 9 available, 6 blocked, 33 done):

```json
"tareas en el índice": 9
"summary": {"available": 9, "in_progress": 0, "ready_for_pr": 0,
            "pr_open": 0, "done": 0, "blocked": 0, "cancelled": 0}
```

**Esperado:** 48 tareas, `done: 33`, `blocked: 6`.

**Impacto en cascada.** `.dt-index.json` es el cache que leen `/orchestrate` y `/status`:

- el board reporta **0 tareas completadas** en un proyecto con 33 mergeadas
- la Pass 2 (`depends_on` → `unblocks`) queda **totalmente vacía**: las dependencias de
  las tareas available apuntan a tareas `done` que ya no están en `ALL_IDS`, así que
  ningún `unblocks` se puebla (`unblocks total: 0`)
- con todos los `unblocks` a 0, la elección de `critical_path_next` degenera a un
  desempate puro por ID — que a su vez está roto por **A-2**

**Fix**

```bash
for folder in "${TASK_FOLDERS[@]}"; do
```

Comprobado: es el **único** uso incorrecto. `$ALL_IDS` en las líneas 73, 107, 124, 141 y
160 es una cadena separada por espacios, no un array, y su expansión sin comillas es
correcta e intencionada.

---

### A-2 · `id_num()` interpreta los IDs con cero a la izquierda como octal — SEVERIDAD MEDIA

> **Estado: PENDIENTE UPSTREAM.** No parcheado localmente a propósito.

**Fichero:** `scripts/dt-board.sh:103`

```bash
id_num() { printf '%d' "${1#*-}"; }
```

`${1#*-}` deja `"032"`, `"040"`, `"039"`… y `printf '%d'` en bash trata una cadena con
cero a la izquierda como **octal**.

**Reproducción**

```bash
id_num() { printf '%d' "${1#*-}"; }
id_num T-032   # → 26   (0o32)
id_num T-040   # → 32   (0o40)
id_num T-039   # → 3  + "printf: 039: invalid octal number" en stderr
```

Los IDs con dígito 8 o 9 tras el cero (`T-008`, `T-009`, `T-018`, `T-019`, `T-028`,
`T-029`, `T-038`, `T-039`, …) no son octal válido: emiten error a stderr y devuelven un
valor truncado. Los demás se convierten **silenciosamente** al número equivocado, que es
el caso más peligroso.

**Impacto.** `id_num` solo se usa en el desempate de `critical_path_next`
(`dt-board.sh:110-115`). Con A-1 activo, este repo mostraba:

```
Suggested next: T-039        ← id_num("039") = 3, el "menor" de todos
```

Corrigiendo **ambos** bugs, la sugerencia correcta es **T-034**.

También ensucia la salida: 6 líneas de `printf: 039: invalid octal number` en stderr en
cada ejecución con `--print`.

**Fix** — forzar base 10 explícitamente:

```bash
id_num() { printf '%d' "$((10#${1#*-}))"; }
```

---

### A-3 · No hay forma soportada de generar `spec.md` en un repo brownfield que ya tiene `design.md`

> **Estado: PENDIENTE UPSTREAM.** No parcheado localmente a propósito.

**Fichero:** `.claude/commands/bootstrap.md` — pre-flight guard (mensaje en línea 16) vs. `### MODE 5 — Brownfield` (línea 483)

`spec.md` se introdujo en v1.1.0 y `/bootstrap` Mode 5 (brownfield) es el único mecanismo
documentado para generarlo. Pero los dos se excluyen mutuamente en cualquier proyecto que
haya pasado por un `/bootstrap` previo:

1. **El guard de idempotencia mata el comando antes del Step 0.** Si existe `design.md`,
   `/bootstrap` se detiene y solo continúa si el usuario escribe `RESET`, descrito como
   *"wipe all generated files and start completely over (destructive — cannot be undone)"*.
   Un proyecto con 33 tareas mergeadas no puede pagar ese precio para obtener un fichero.

2. **Aunque se sortee el guard, Mode 5 hace de más.** Su Phase A genera `spec.md` (lo que
   se quiere), pero también **regenera `design.md`** con headings fijos y su Phase B
   genera delta tasks. En un repo con `design.md` curado y un `tasks/` vivo, ambas cosas
   son destructivas.

**Consecuencia práctica:** todo proyecto que migre de v1.0 a v1.4 se queda sin ruta
soportada hacia `spec.md`, que es justo el artefacto del que dependen `/refine` y el
agente `spec-coverage`.

**Propuesta.** Un modo acotado —`/bootstrap --mode=brownfield --spec-only`, o un
`/refine --init-spec`— que ejecute **solo** la arqueología y la escritura de `spec.md`,
sin tocar `design.md` ni `tasks/`, y que sea alcanzable con `design.md` presente. Como
mínimo, que el guard admita una salida hacia Mode 5 que no pase por `RESET`.

**Workaround aplicado aquí:** se ejecutó Mode 5 Phase A a mano (escaneo de `src/`,
`tests/`, `frontend/`, `config/`, `design.md`, `plan.md`, `tasks/done/`) y se escribió
`spec.md` (780 líneas) sin invocar el comando. `design.md` y `tasks/` quedaron intactos.

---

## B. Errores en `migration.md`

### B-1 · El script de división de `decisions.md` pierde la atribución de las entradas sin task-id

> **Estado: CORREGIDO** en `migration.md` — patrón de split, clasificación de entradas sin ID
> hacia `general.md`, y bloque de verificación por conteo de cabeceras. Script revalidado.

**Sección:** PASO 5 → *Migración de decisions.md → context/decisions/*

El patrón de división solo reconoce cabeceras con ID de tarea:

```python
pattern = r'(?=^## \d{4}-\d{2}-\d{2} — [TB]-\d+)'
```

`context/decisions.md` contenía 93 cabeceras `## `, de las cuales **90** encajan. Las tres
restantes son `## Format`, la plantilla `## YYYY-MM-DD — T-XXX [Agent name]` (ambas dentro
del preámbulo, correctamente capturadas) y una entrada real sin ID:

```
## 2026-08-06 — [Orchestrator, /explore]
Decided: all 3 human checkpoints (phase1_understanding, phase4_design,
phase6_evaluation) are forward-only...
```

Como `re.split` no corta ahí, esa entrada se **concatena silenciosamente** al final del
fichero de la tarea anterior — quedando atribuida a una tarea que no la tomó. No hay
pérdida de bytes, pero sí de trazabilidad, y es invisible en la verificación que propone
el documento (`ls context/decisions/ | head -20`).

**Fix aplicado:** cortar por toda cabecera datada y clasificar después, enrutando las
entradas sin ID a `context/decisions/general.md`.

```python
pattern = r'(?=^## \d{4}-\d{2}-\d{2} — )'   # sin exigir [TB]-\d+
# ... y luego: si re.match(r'^## \d{4}-\d{2}-\d{2} — ([TB]-\d+)') → task_map, si no → untagged
```

**Verificación de integridad** (la que el documento no incluye y conviene añadir):

```bash
# antes / después deben coincidir
grep -c '^## ' context/decisions.md
cat context/decisions/*.md | grep -c '^## '        # 93 = 93
grep -c '^Decided:' context/decisions.md
cat context/decisions/*.md | grep -c '^Decided:'   # 83 = 83
```

---

### B-2 · PASO 8 ordena ejecutar `/bootstrap`, que no puede ejecutarse

> **Estado: BLOQUEADO POR A-3.** El texto del PASO 8 no se reescribe hasta saber qué forma
> toma el modo acotado upstream.

Consecuencia directa de **A-3**. El PASO 8 dice *"Ejecuta el comando `/bootstrap` y cuando
pregunte el modo, selecciona Mode 5"*, pero con `design.md` presente el comando se detiene
en el guard antes de llegar a preguntar el modo. Además el PASO 8 afirma *"NO generará
código nuevo ni modificará tareas existentes"*, lo cual contradice la Phase B de Mode 5,
que genera delta tasks, y su generación de `design.md`.

El documento debería o bien describir el procedimiento manual, o bien esperar al modo
acotado propuesto en A-3.

---

### B-3 · La verificación esperada del PASO 5 no coincide con los datos reales

> **Estado: CORREGIDO** en `migration.md` — la verificación ya no promete un rango de IDs.

El documento anticipa:

```
# context/decisions/T-001.md ... T-031.md (y B-001.md)
```

El resultado real son **26 ficheros con ID** (25 `T-*` + `B-001.md`), más `general.md`
(ver B-1) y `legacy-header.md` = 28 ficheros. Los IDs presentes tienen huecos dentro del
rango anunciado — no hay entradas para T-007, T-010, T-011, T-013, T-014, T-015 ni T-029 —
e incluyen uno fuera de él, **T-038**.

No es un fallo de la migración: el fichero plano simplemente no tenía una entrada por cada
tarea, porque no toda tarea genera decisiones registrables. Pero la verificación tal como
está redactada (`T-001.md ... T-031.md`) induce a pensar que se han perdido datos. Mejor
comprobar integridad por conteo de cabeceras (ver B-1) que por rango de IDs.

---

## C. Deuda local en `data-science-lab` tras la migración

### C-1 · `CLAUDE.md` instruye a escribir en ficheros que la migración ha borrado — SEVERIDAD ALTA

> **Estado: CORREGIDO.** Ver *Cambios aplicados* al final.

`CLAUDE.md` se carga como project instructions **en cada sesión** y tiene precedencia sobre
el comportamiento por defecto. Tras el PASO 5 sigue apuntando a los ficheros planos que ya
no existen:

| Línea | Texto actual | Estado real |
|---|---|---|
| 159 | `` `context/decisions.md` — log non-obvious technical decisions `` | borrado → `context/decisions/T-XXX.md` |
| 160 | `` `context/discoveries.md` — cross-agent alerts `` | borrado → `context/discoveries/T-XXX.md` |
| 186 | *"Always log non-obvious choices in `context/decisions.md`"* | ídem |
| 187 | *"Always check `context/discoveries.md` before implementing"* | ídem |

El formato correcto por-tarea está definido en `.claude/steering/context-formats.md`, que
el orchestrator inyecta a architect, coder y planner. Mientras `CLAUDE.md` diga lo
contrario, hay dos fuentes de verdad en conflicto y la de mayor precedencia es la obsoleta.

**Nota adicional:** `context/decisions/` y `context/discoveries/` reciben en v1.4 el nombre
del ID de tarea. Las entradas históricas migradas conservan la cabecera datada original,
así que ambos formatos conviven sin colisión.

---

### C-2 · `CLAUDE.md` no refleja la superficie de v1.4 — SEVERIDAD MEDIA

> **Estado: CORREGIDO.** Ver *Cambios aplicados* al final.

- La tabla de **Commands** (líneas ~166-178) no incluye `/refine` ni `/reopen`.
  `/refine` es especialmente relevante: es la **única** vía soportada para editar
  `spec.md` — nunca debe editarse a mano.
- No se menciona `spec.md` en ninguna parte, pese a ser ya un artefacto del repo.
- No se menciona `.claude/steering/` ni cómo se inyectan las reglas por scope.
- Línea 93: lista `dt-ready` entre los scripts de transición de estado. Sigue existiendo y
  funcionando (`in-progress → ready-for-pr`), pero en v1.4 **ningún comando lo invoca**:
  la ruta normal es `dt-pr.sh`, que acepta tanto `in-progress` como `ready-for-pr`.
  `dt-ready.sh` es hoy la vía de escape manual. Conviene decirlo o dejará de usarse por
  desconocimiento. Faltan además `dt-pr` y `dt-verify` en esa lista.

---

### C-3 · `spec_coverage_enabled` sigue en `false` — pendiente de decisión humana

> **Estado: ABIERTO — requiere tu decisión.** No es un fallo que yo pueda cerrar.

`devteam.config.yml` → `quality.spec_coverage_enabled: false`, según el PASO 9, que lo
condiciona a haber revisado `spec.md`. El checkpoint humano obligatorio de Mode 5
(*"si documento un bug como comportamiento intencionado, los agentes lo replicarán"*)
**no se ha cumplido**. Activarlo antes de esa revisión propaga a cada review lo que hoy
son inferencias mías.

Para activarlo tras revisar:

```yaml
quality:
  spec_coverage_enabled: true
```

---

### C-4 · Puntos de `spec.md` que requieren validación humana

> **Estado: ABIERTO — requiere tu validación.** No es un fallo que yo pueda cerrar.

Generados leyendo el código, no verificados contra intención. Son los sitios donde un
error mío se convertiría en comportamiento replicado por los agentes:

1. **Polaridad de métrica.** `LabState` no tiene campo de polaridad; `score_evaluator` la
   resuelve por matching contra una lista curada de métricas a minimizar. Lo documenté
   como diseño deliberado (así lo dice su docstring, y resuelve el discovery
   `infra-agent (T-002) → pipeline-agent (T-031)`). **Si en realidad es deuda pendiente,
   hay que marcarlo como tal.**
2. **`feature_importance_extractor` lee un payload que nadie escribe.** Depende del
   `feature_importance` de `results.json`, que produciría `coder` (T-029, blocked). El
   contrato no está ejercitado de punta a punta.
3. **`design.md` está obsoleto en dos puntos**, y `spec.md` sigue al código:
   - dice *"Python + SHAP"* para `feature_importance_extractor`, que **no importa `shap`**
     (deliberado: un import transitivo ausente en un módulo aterrizado rompe el build del
     grafo entero, no solo ese nodo)
   - documenta el contrato de endpoints de la API pero **ningún esquema JSON de respuesta**,
     razón por la que `frontend/src/api/types.ts` se marca `PROVISIONAL` en el propio fuente
4. **`_summarize_output` sin redacción** (`src/observability/jsonl_callback.py`): recogido
   como gap conocido citando `context/decisions/T-012.md`, no como hallazgo nuevo.

Secciones `## Untested behavior` añadidas en: `src/memory/store.py` (sin módulo de test
propio), backend (`src/api/` vacío) y frontend (solo `client.ts` y `Layout.tsx` testeados).

---

## Estado del working tree

Nada commiteado, por indicación explícita.

- `scripts/dt-board.sh` y `scripts/dt-common.sh` están **byte-idénticos a upstream v1.4**
  (verificado con `diff -q` contra `/tmp/dev-team-ref`). A-1 y A-2 **no** están parcheados
  en el repo.
- `.dt-index.json` está **correcto ahora mismo** (48 tareas, 33 done, 6 blocked,
  `critical_path_next: T-034`) porque se regeneró con una copia parcheada ejecutada desde
  el scratchpad. Es un fichero derivado y git-ignored (`.gitignore:19`).
- **Se volverá a corromper en la próxima ejecución de `scripts/dt-board.sh`**, que es lo
  que hacen `/orchestrate` y `/status`. Hasta que A-1 se arregle, el cache no es fiable.

---

## Cambios aplicados en este repo (2026-08-18)

Todo sin commitear. **Ningún fichero de framework de dev-team fue modificado** — verificado
con `diff -q` contra `/tmp/dev-team-ref` para `scripts/` y `.claude/{agents,commands,steering}/`
de framework.

| Fichero | Cambio | Issue |
|---|---|---|
| `CLAUDE.md` | Sección *Context files* reescrita: carpetas por tarea, `retrospectives/`, puntero a `.claude/steering/context-formats.md` como fuente de verdad, nota de dónde quedó el histórico | C-1 |
| `CLAUDE.md` | Reglas 4 y 5 → `context/decisions/T-XXX.md` y entradas abiertas de `context/discoveries/` | C-1 |
| `CLAUDE.md` | *Task lifecycle*: tabla de scripts con `dt-pr`, `dt-verify` y `dt-ready` marcado como escape hatch | C-2 |
| `CLAUDE.md` | Tabla de comandos: `/refine` (única vía de editar `spec.md`) y `/reopen` | C-2 |
| `CLAUDE.md` | `spec.md` añadido al mapa de documentos de referencia | C-2 |
| `CLAUDE.md` | `.claude/steering/` explicado en *Module ownership*; `spec-coverage` en *Quality gates* | C-2 |
| `.claude/agents/{api,frontend,infra,pipeline}-agent.md` | 4 reglas → `context/discoveries/T-XXX.md` (son project agents nuestros, no de dev-team) | C-1 |
| `tasks/available/T-047-…md` | Puntero muerto `context/decisions.md:749-780` → `context/decisions/T-022.md`, identificando las dos entradas por cabecera | C-1 |
| `migration.md` | Script del PASO 5 corregido + verificación por conteo | B-1, B-3 |

### Deliberadamente NO tocado

- **`tasks/done/*.md`** (~60 referencias) — registro histórico de lo que ocurrió en cada
  tarea. Reescribirlo sería falsificar el log.
- **`context/decisions/*.md` y `context/discoveries/legacy.md`** (~20) — contenido
  histórico ya migrado; se citan entre sí con el nombre que tenían entonces.
- **`src/`, `tests/`, `frontend/`, `docs/pipeline.md`** (~30 citas en docstrings, del tipo
  *"see `context/decisions.md`'s T-019 entry"*) — son punteros degradados, no rotos: el
  mapeo al nuevo nombre es mecánico. Además esos ficheros pertenecen a `infra-agent`,
  `pipeline-agent` y `frontend-agent` según *Module ownership*, así que un barrido
  transversal debería ir en su propia tarea, no colarse en la migración.
  **Propuesta:** una tarea `S` de documentación que actualice esas citas de golpe.

---

### Orden de resolución sugerido

1. ~~**C-1**~~ ✅ hecho — era lo más urgente: cada sesión arrancaba con instrucciones falsas
2. ~~**C-2**~~ ✅ hecho · ~~**B-1**, **B-3**~~ ✅ hechos
3. **A-1 + A-2** ⏳ — 2 líneas, pero upstream. Hasta entonces `.dt-index.json` no es fiable
   tras cualquier ejecución de `scripts/dt-board.sh`
4. **A-3** ⏳ upstream → desbloquea **B-2**
5. **C-3 / C-4** — tu decisión, tras revisar `spec.md`
6. Opcional: tarea de documentación para el barrido de citas en `src/`/`docs/`
