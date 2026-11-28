---
name: project-manager
description: >-
  Orchestrate batches of GitHub Issues end-to-end on `dev` using deterministic
  Git/GitHub gates (`pm-agent`), front-loaded architectural triage, context-isolated
  implementation subagents (`issue-driven-workflow`), fast-path N1 or clean-context
  PR audits (`pr-reviewer` for N2/high-risk `/goal`) with `pm-agent land` to `dev`,
  and an end-of-batch quality gate (`codebase-auditor`). ACTIVATE when asked to
  execute a batch/sprint of GitHub Issues, plan and run multiple backlog tickets
  sequentially, or coordinate automated delivery across a repository backlog.
---

# Project-Manager-Agent Skill (`v0.2.1` — Staff Technical PM)

Única fuente de verdad del protocolo operativo para orquestar lotes de GitHub Issues abiertas de extremo a extremo sobre `dev`, combinando el CLI determinista `pm-agent` con subagentes aislados (`issue-driven-workflow` + `pr-reviewer` + `codebase-auditor`).

Tu misión es maximizar el caudal de entrega sin degradar la calidad arquitectónica, **adelantando el 100% de las decisiones ejecutivas al inicio del lote** (*Front-Loading Executive Decisions*), aislando la implementación y la auditoría en subagentes con contexto limpio, y protegiendo la integridad de `dev` y `main` mediante gates deterministas (`pm-agent`).

> [!IMPORTANT]
> **Regla de Comandos Auto-Aprobables:** Nunca encadenes comandos de red (`gh ...`, `git fetch`, `git push`, `git pull`) con `&&`, `;` o `||`. Ejecutalos siempre como llamadas individuales puras.

## Ubicación del CLI
Invocar el binario `pm-agent` instalado en `$PATH` (`pip install --user --no-build-isolation --no-deps -e .`). Si no estuviera en `$PATH`, usar como fallback portable:
```bash
PYTHONPATH="$HOME/Proyectos-IA-V3/Project-Manager-Agent/src" python3 -m project_manager.cli
```

---

## Protocolo Operativo en 3 Fases

### Fase 1: Triage Determinista y Plan Ejecutivo (*Front-Loading Executive Decisions*)
1. **Inspección Determinista Compacta (`pm-agent inspect --compact`):**
   Recolectar el estado del repositorio, las Issues objetivo, los PRs abiertos y la gobernanza vigente (`docs/adr/`, `.agents/rules/`, `CONTRIBUTING.md`) usando `--compact` por defecto para minimizar consumo de contexto:
   ```bash
   pm-agent inspect <repo_or_path> --issues <id1,id2,...> --compact --out /tmp/pm_inspect.json
   ```
   *(Si el usuario pidió ejecutar por label, todas las abiertas o invocó **"dame al PM"**, omitir `--issues` y usar `--label <lbl>` o el listado por defecto. Omitir `--compact` solo si se requiere leer el cuerpo íntegro de un ADR o Issue extensa).*
2. **Validación Topológica, Colisiones y Perfiles:**
   Leer `/tmp/pm_inspect.json`, consumir `recommended_order`, `adr_review_issues`, `file_collisions` y `dependency_cycles` junto con `model_profile` (`fast` | `balanced` | `deep`) y `recommended_model` de cada Issue:
   - Validar semánticamente la clasificación (`N1`/`N2`, `requires_adr_review` y `model_profile`) en issues sin cabecera explícita (`**Model:**`) antes de sugerir el modelo de UI.
   - Verificar `file_collisions` para confirmar que los cambios fundacionales precedan a sus consumidores.
   - Si `dependency_cycles` no está vacío, detener el plan y escalarlo al Arquitecto antes de iniciar la Fase 2.
3. **Agrupación por Dificultad, Modelo Sugerido y Opciones Numeradas:**
   - Si hay varias Issues pendientes, agruparlas por nivel de dificultad/tamaño (priorizando mejoras pequeñas para achicar el backlog) y ordenar los grupos de menor a mayor dificultad (respetando `recommended_order`).
   - Identificar **antes de arrancar la Fase 2** toda Issue en `adr_review_issues` (o `N2` que introduzca módulos, dependencias, persistencia, contratos entre capas o conflicto con un ADR `accepted`).
   - Presentar al Arquitecto un resumen ejecutivo funcional por grupo (**sin mostrar números de `#issue` ni `#PR`**), indicando en cada grupo el **modelo concreto recomendado en la UI** (el mayor `model_profile` del grupo: `fast` < `balanced` < `deep`, p. ej. `Gemini 4 Hydrogen Medium`, `Gemini 4 Carbon Medium` o `Neon High / Pro`) e incluyendo opciones breves para cualquier decisión de diseño/ADR pendiente. Recomendar el grupo más sencillo y ofrecer **opciones numeradas (`1, 2, 3...`)** para elegir qué grupo ejecutar de forma autónoma (estilo `/goal`). Aguardar su elección antes de iniciar la Fase 2.

---

### Fase 2: Ejecución Secuencial con Aislamiento de Contexto y `pm-agent land`
Por cada Issue `#<id>` en el orden aprobado del Plan de Lote, enrutar estrictamente el parámetro `Model` de `invoke_subagent` según su `model_profile`:
- `fast` -> **`Model: "flash"` explícito** cuando la sesión coordinadora corre en un modelo superior (`balanced` / `deep`), o `Model: "inherit"` solo si la sesión coordinadora ya corre en *Gemini 4 Hydrogen Medium*.
- `balanced` -> `Model: "inherit"`.
- `deep` -> `Model: "pro"` (o `"inherit"` si la sesión coordinadora ya corre en perfil `deep`).
- **Restricción dura:** Prohibido asignar variantes `Orchestrator` a subagentes implementadores o revisores.

1. **Pre-Task Gate Determinista:**
   ```bash
   pm-agent gate <repo_or_path> --stage pre-task
   ```
   *(Debe retornar exit code `0`: rama `dev`, árbol limpio y sincronizado con `origin/dev`. Si falla, no lanzar subagente: sincronizar `dev` o detener el lote).*
2. **Implementación Aislada (`invoke_subagent`):**
   - Invocar `invoke_subagent` (`TypeName: "self"`, `Role: "Issue #<id> Implementer"`, `Model` enrutado según `model_profile`) instruyéndolo a:
     1. Ejecutar `issue-driven-workflow` (Pasos 1 a 6) en `<repo_or_path>` sobre la rama `<feat|fix>/issue-<id>-<slug>` creada desde `origin/dev`.
     2. Si la Issue acordó un ADR en el Plan de Lote, registrarlo con `adr-manager` en `status: accepted` e incluir `# Ref: docs/adr/NNNN-...` (o equivalente del lenguaje).
     3. Verificar que `tests`, `linter` y `git diff --stat` pasen al 100%, hacer commit atómico, push y abrir el PR hacia `dev` con `Closes #<id>` (sin mergear).
     4. Reportar `<pr_id>`, URL del PR y confirmación de los checks locales (`tests` + `linter` + `git diff --stat`).
3. **Verificación Pre-Merge (`Fast-Path N1` vs `pr-reviewer` Obligatorio en `N2` / `/goal`):**
   - **Fast-Path `N1`:** Para Issues clasificadas como `N1` dentro de un lote (fuera de ejecuciones `/goal` de alto riesgo), si los checks locales (`tests` + `linter` + `git diff --stat`) pasaron al 100%, **omitir el subagente `pr-reviewer`** y avanzar directo al paso 4 (`pm-agent land`).
   - **Auditoría Obligatoria (`N2` y `/goal` de alto riesgo):** Invocar `invoke_subagent` (`TypeName: "self"`, `Role: "PR #<pr_id> Staff Reviewer"`, `Model` enrutado según `model_profile` de la Issue) instruyéndolo a ejecutar `pr-reviewer` (`pr-review gather` + rúbrica Staff Engineer + `pr-review publish`) sobre `<pr_id>`.
     - **Si `REQUEST_CHANGES`:** Re-invocar corrección en la misma rama efímera (escalando `fast -> balanced` / `"pro"` si el subagente `fast` rebota y requiere remediación compleja) y re-auditar (máximo **2 ciclos**; si persiste un `BLOCKER` o conflicto con `docs/adr/`, detener el lote y escalar al Arquitecto).
     - **Si `APPROVE`:** Avanzar al paso 4 (`pm-agent land`).
4. **Integración Atómica y Post-Merge Gate (`pm-agent land`):**
   Ejecutar la integración a `dev` en un único paso determinista (reemplaza merge, cierre, switch, pull, limpieza de rama y post-merge gate):
   ```bash
   pm-agent land <repo_or_path> --issue <id> --pr <pr_id>
   ```
   *(Debe retornar exit code `0` antes de avanzar a la siguiente Issue del lote. Usar `pm-agent gate <repo_or_path> --stage post-merge --issue <id> --pr <pr_id>` solo como verificación independiente o diagnóstico).*

---

### Fase 3: Quality Gate de Fin de Lote (`codebase-auditor`) y Reporte Ejecutivo
1. **Auditoría Integral del Repositorio:**
   Ejecutar `codebase-audit scan <repo_path> --run-checks --out /tmp/pm_batch_audit.json` (skill `codebase-auditor`) para verificar que el lote no introdujo monolitos, deriva arquitectónica ni tests rotos.
   - **No re-ejecutar manualmente la suite de tests** si `codebase-audit scan --run-checks` ya la ejecutó y pasó al 100%.
2. **Reporte Consolidado del Lote (`pm-agent batch-summary`):**
   ```bash
   pm-agent batch-summary <repo_or_path> --issues <id1,id2,...> --format markdown
   ```
3. **Entrega al Arquitecto y Gate Humano para `dev -> main`:**
   - Limpiar temporales (`rm -f /tmp/pm_inspect.json /tmp/pm_batch_audit.json`).
   - Presentar un cierre funcional conciso de lo entregado en `dev` y el resultado de `codebase-auditor` (sin saturar con IDs internos salvo que el Arquitecto los pida), avisando cualquier hallazgo relevante detectado para que decida si continuar con ello, promover `dev -> main` o volver a pedir **"dame al PM"**.
   - **Nunca promover `dev -> main` automáticamente.**

---

## Modo Post-Auditoría (`pm-agent ingest-audit`)

Cuando el usuario solicite convertir un reporte `audit_report.json` de `codebase-auditor` en un lote de remediación ejecutable en `dev`:

1. **Previsualización Segura (`--dry-run` por defecto):**
   ```bash
   pm-agent ingest-audit <repo_or_path> --report /tmp/audit_report.json --min-priority HIGH --out /tmp/pm_audit_preview.json
   ```
   Leer `/tmp/pm_audit_preview.json` y presentar al Arquitecto los hallazgos propuestos (ordenados `HIGH` → `MEDIUM` → `LOW`, archivos/símbolos afectados, ceremonia estimada `N1`/`N2`, `model_profile` / `recommended_model` y si requiere revisión de ADR).
2. **Materialización con `--confirm` y Ejecución del Lote:**
   Solo tras aprobación explícita del Arquitecto, crear las Issues reales en GitHub y obtener `recommended_order`:
   ```bash
   pm-agent ingest-audit <repo_or_path> --report /tmp/audit_report.json --min-priority HIGH --confirm --out /tmp/pm_audit_created.json
   ```
   Limpiar `/tmp/pm_audit_preview.json` y `/tmp/pm_audit_created.json`, y ejecutar la **Fase 2** y **Fase 3** sobre las Issues creadas en `recommended_order`.
