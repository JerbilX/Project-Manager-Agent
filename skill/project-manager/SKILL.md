---
name: project-manager
description: >-
  Orchestrate batches of GitHub Issues end-to-end on `dev` using deterministic
  Git/GitHub gates (`pm-agent`), front-loaded architectural triage, context-isolated
  implementation subagents (`issue-driven-workflow`), mandatory clean-context PR
  audits (`pr-reviewer`) with automatic Squash Merge to `dev` upon `APPROVE`, and an
  end-of-batch quality gate (`codebase-auditor`). ACTIVATE when asked to execute a
  batch/sprint of GitHub Issues, plan and run multiple backlog tickets sequentially,
  or coordinate automated delivery across a repository backlog.
---

# Project-Manager-Agent Skill (`v0.1.0` — Modo Backlog)

Skill reutilizable para orquestar lotes de GitHub Issues abiertas de extremo a extremo sobre la rama `dev`, combinando el CLI determinista `pm-agent` con subagentes aislados (`issue-driven-workflow` + `pr-reviewer` + `codebase-auditor`).

> [!IMPORTANT]
> **Regla de Comandos Auto-Aprobables:** Nunca encadenes comandos de red (`gh ...`, `git fetch`, `git push`, `git pull`) con `&&`, `;` o `||`. Ejecutalos siempre como llamadas individuales puras.

## Ubicación del CLI
Invocar el binario `pm-agent` instalado en `$PATH` (`pip install --user --no-build-isolation --no-deps -e .`). Si no estuviera en `$PATH`, usar como fallback portable:
```bash
PYTHONPATH="$HOME/Proyectos-IA-V3/Project-Manager-Agent/src" python3 -m project_manager.cli
```

---

## Protocolo Operativo en 3 Fases

Aplicar íntegramente la rúbrica y protocolo de [`../../prompts/staff_pm.md`](../../prompts/staff_pm.md):

### Fase 1: Triage Determinista y Plan de Lote (*Front-Loading Executive Decisions*)
1. Recolectar el estado del repositorio, las Issues objetivo, los PRs abiertos y la gobernanza vigente (`docs/adr/`, `.agents/rules/`, `CONTRIBUTING.md`):
   ```bash
   pm-agent inspect <repo_or_path> --issues <id1,id2,...> --out /tmp/pm_inspect.json
   ```
   *(Si el usuario pidió ejecutar por label o todas las abiertas, omitir `--issues` y usar `--label <lbl>` o el listado por defecto).*
2. Leer `/tmp/pm_inspect.json`, consumir `recommended_order`, `adr_review_issues`, `file_collisions` y `dependency_cycles` precalculados por `pm-agent inspect`, validar el nivel `N1`/`N2` de cada Issue e identificar **todas** las decisiones ejecutivas o nuevos ADRs necesarios al inicio (si `dependency_cycles` no está vacío, escalar al Arquitecto antes de ejecutar).
3. Presentar el **Plan de Lote** al Arquitecto y aguardar su aprobación única antes de iniciar la Fase 2.

### Fase 2: Ejecución por Tarea con Subagentes Limpios y Auto-Merge a `dev`
Por cada Issue `#<id>` en el orden aprobado del Plan de Lote:

1. **Pre-Task Gate:**
   ```bash
   pm-agent gate <repo_or_path> --stage pre-task
   ```
   *(Debe retornar exit code `0`: rama `dev`, árbol limpio y sincronizado con `origin/dev`).*
2. **Implementación Aislada (`invoke_subagent`):**
   - Invocar `invoke_subagent` (`TypeName: "self"`, `Role: "Issue #<id> Implementer"`) instruyéndolo a ejecutar `issue-driven-workflow` (Pasos 1 a 6) en `<repo_or_path>` hasta abrir el PR hacia `dev` (sin mergear) y reportar el `<pr_id>`.
3. **Auditoría Pre-Merge Obligatoria (`invoke_subagent` + `pr-reviewer`):**
   - Invocar `invoke_subagent` (`TypeName: "self"`, `Role: "PR #<pr_id> Staff Reviewer"`) instruyéndolo a ejecutar el skill `pr-reviewer` (`pr-review gather` + rúbrica + `pr-review publish`) sobre `<pr_id>`.
   - **Si `REQUEST_CHANGES`:** Re-invocar corrección en la misma rama efímera y re-auditar (máximo **2 ciclos**; si persiste un `BLOCKER`, detener el lote y escalar al Arquitecto).
   - **Si `APPROVE`:** Ejecutar automáticamente la integración a `dev` con comandos separados:
     ```bash
     gh pr merge <pr_id> --squash --delete-branch
     ```
     ```bash
     gh issue close <id> --reason completed
     ```
     ```bash
     git switch dev
     ```
     ```bash
     git pull --prune --ff-only
     ```
     ```bash
     git branch -D <rama-efimera>
     ```
4. **Post-Merge Gate:**
   ```bash
   pm-agent gate <repo_or_path> --stage post-merge --issue <id> --pr <pr_id>
   ```
   *(Debe retornar exit code `0` antes de avanzar a la siguiente Issue del lote).*

### Fase 3: Quality Gate de Fin de Lote y Reporte Ejecutivo
1. Ejecutar `codebase-audit scan <repo_path> --run-checks --out /tmp/pm_batch_audit.json` (skill `codebase-auditor`) para verificar que el lote no introdujo monolitos, deriva arquitectónica ni tests rotos.
2. Generar el reporte consolidado del lote:
   ```bash
   pm-agent batch-summary <repo_or_path> --issues <id1,id2,...> --format markdown
   ```
3. Limpiar archivos temporales en `/tmp/` (`rm -f /tmp/pm_inspect.json /tmp/pm_batch_audit.json`) y presentar el reporte ejecutivo al Arquitecto para que decida si promueve `dev -> main`.

---

## Modo Post-Auditoría (`ingest-audit`)

Cuando el usuario solicite convertir un reporte `audit_report.json` de `codebase-auditor` en un lote de remediación ejecutable en `dev`:

1. **Previsualización segura (`--dry-run` por defecto):**
   ```bash
   pm-agent ingest-audit <repo_or_path> --report /tmp/audit_report.json --min-priority HIGH --out /tmp/pm_audit_preview.json
   ```
   Leer `/tmp/pm_audit_preview.json` y presentar al Arquitecto los hallazgos propuestos (ordenados `HIGH` → `MEDIUM` → `LOW`, archivos/símbolos afectados y ceremonia estimada `N1`/`N2`).
2. **Materialización con `--confirm` y ejecución del lote:**
   Una vez aprobado por el Arquitecto, crear las Issues reales en GitHub y obtener el orden de ejecución recomendado (`recommended_order`):
   ```bash
   pm-agent ingest-audit <repo_or_path> --report /tmp/audit_report.json --min-priority HIGH --confirm --out /tmp/pm_audit_created.json
   ```
   Limpiar `/tmp/pm_audit_preview.json` y `/tmp/pm_audit_created.json`, y ejecutar la **Fase 2** y **Fase 3** sobre las Issues creadas en `recommended_order`.
