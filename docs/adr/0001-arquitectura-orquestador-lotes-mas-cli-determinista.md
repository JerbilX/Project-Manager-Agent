---
status: accepted
date: 2026-11-22
decision-makers: [JerbilX, Antigravity]
supersedes: []
superseded-by: []
---

# 0001 - Arquitectura Orquestador de Lotes por Subagentes + CLI Determinista en Python Stdlib

## Contexto y Planteamiento del Problema
En repositorios gestionados bajo `issue-driven-workflow`, `pr-reviewer` y `codebase-auditor`, la ejecución manual o secuencial de un lote de GitHub Issues en una única sesión de agente presenta tres riesgos operativos:
1. **Contaminación de contexto y sesgo de autor:** Si el mismo contexto que implementa varias tareas también las audita o encadena cambios, pierde precisión y agota la ventana de contexto.
2. **Interrupciones fragmentadas al Arquitecto:** Consultas arquitectónicas o de ADRs dispersas a mitad de un lote rompen la ejecución desatendida.
3. **Deriva de estado Git entre tareas:** Pasar a la siguiente Issue sin verificar determinísticamente que el PR anterior fue fusionado con Squash Merge a `dev`, la Issue fue cerrada y el árbol local volvió a `dev` limpio y sincronizado con `origin/dev` puede producir ramas huérfanas o conflictos.

Necesitamos `Project-Manager-Agent` (`v0.1.0`) para orquestar lotes de Issues existentes (Modo Backlog) garantizando **Front-Loading Executive Decisions**, aislamiento por subagentes limpios, pre/post gates deterministas, auto-merge a `dev` tras `APPROVE` de `pr-reviewer` y un quality gate de cierre de lote con `codebase-auditor`.

## Impulsores de la Decisión (Decision Drivers)
* **Consistencia con el ecosistema (`PR-Reviewer-Agent` y `Codebase-Auditor-Agent`):** CLI determinista en Python `>=3.11` stdlib, cero dependencias externas de runtime, tests con `unittest` y skill declarativo para Antigravity.
* **Front-Loading Executive Decisions:** Detectar al inicio del lote todas las tareas `N2` que requieran decisiones de diseño o nuevos ADRs para que el Arquitecto apruebe el Plan de Lote una sola vez.
* **Aislamiento de contexto y sesgo cero:** Separar el agente coordinador (PM), el subagente implementador por Issue y el subagente revisor (`pr-reviewer`).
* **Gates deterministas por código:** Garantizar mediante CLI (`pm-agent gate`) que `dev` esté limpia y sincronizada antes de cada tarea (`pre-task`) y que el PR esté fusionado y la Issue cerrada antes de avanzar (`post-merge`).
* **Protección de `main`:** El auto-merge opera exclusivamente hacia `dev` (previo `APPROVE` de `pr-reviewer`); la promoción `dev -> main` permanece siempre bajo orden explícita del Arquitecto.

## Opciones Consideradas
* **Opción 1: Skill de Antigravity (`project-manager`) + CLI determinista en Python stdlib (`pm-agent`) + Orquestación por Subagentes Limpios**
  * *Pro:* Cero dependencias externas, costo \$0 en APIs externas, invariantes de Git/GitHub verificadas por código (`inspect`, `gate`, `batch-summary`), aislamiento real por tarea con `invoke_subagent` y reutilización directa de `issue-driven-workflow`, `pr-reviewer` y `codebase-auditor`.
  * *Contra:* Requiere `git` y `gh` CLI autenticados en el entorno local.
* **Opción 2: Demonio externo o servidor de colas con llamadas directas a APIs de LLM**
  * *Pro:* Ejecución desacoplada del IDE.
  * *Contra:* Introduce dependencias externas, costo de API Keys por repositorio y duplica la infraestructura nativa de subagentes y skills de Antigravity (viola YAGNI).
* **Opción 3: Ejecución monolítica de todas las Issues dentro del mismo contexto sin CLI de gates**
  * *Pro:* Sin código CLI adicional.
  * *Contra:* Alto riesgo de degradación de contexto tras 2-3 tareas, sesgo de autor en revisiones y ausencia de verificación determinista del estado de `dev` entre merges.

## Decisión Adoptada
Opción elegida: **"Opción 1: Skill de Antigravity (`project-manager`) + CLI determinista en Python stdlib (`pm-agent`) + Orquestación por Subagentes Limpios"**, estructurada en:

1. **CLI Determinista (`src/project_manager/`, binario `pm-agent`):**
   - `pm-agent inspect <repo_or_path> [--issues 1,2,3] [--label <lbl>]`: Recolecta estado Git (`branch`, `is_clean`, `synced_with_origin_dev`), especificación de Issues del lote, clasificación preliminar `N1`/`N2`, archivos mencionados, PRs abiertos y gobernanza activa (`docs/adr/` aceptados, `.agents/rules/`, `CONTRIBUTING.md`).
   - `pm-agent gate <repo_or_path> --stage <pre-task|post-merge> [--issue <id>] [--pr <pr_id>]`: Valida por código las invariantes antes de iniciar cada Issue y después del Squash Merge a `dev`.
   - `pm-agent batch-summary <repo_or_path> --issues 1,2,3`: Verifica el estado final de cada Issue/PR del lote y genera el resumen ejecutivo en JSON y Markdown.
2. **Protocolo de Technical PM (`prompts/staff_pm.md`) y Skill (`skill/project-manager/SKILL.md`):**
   - **Fase 1 (Triage y Plan de Lote):** Clasificación `N1`/`N2`, orden topológico por dependencias/archivos y levantamiento anticipado de decisiones ejecutivas/ADRs para aprobación única del Arquitecto.
   - **Fase 2 (Ejecución por Tarea y Auto-Merge a `dev`):** Por cada Issue: `pre-task` gate -> subagente implementador (`issue-driven-workflow` Pasos 1-6) -> subagente revisor (`pr-reviewer`, con hasta 2 ciclos de remediación si devuelve `REQUEST_CHANGES`) -> Squash Merge automático a `dev`, cierre garantizado de la Issue, higiene local y `post-merge` gate.
   - **Fase 3 (Quality Gate Final y Reporte):** Ejecución de `codebase-auditor` (`codebase-audit scan --run-checks`) y generación de `pm-agent batch-summary` para que el Arquitecto evalúe la promoción `dev -> main`.
3. **Alcance `v0.1.0` y Roadmap Evolutivo (`v0.2.0+`):**
   - **`v0.1.0` (Modo Backlog — Implementado):** Orquestación de lotes de GitHub Issues abiertas existentes (por IDs `--issues`, por `--label` o todas las abiertas).
   - **`v0.2.0+` (Modo Épica / Idea — Roadmap):** Desglose asistido de un requerimiento de alto nivel en múltiples Issues atómicas con criterios de aceptación y orden de dependencias antes de ejecutar el lote.
   - **`v0.2.0+` (Modo Post-Auditoría — Roadmap):** Ingesta directa de `audit_report.json` generado por `Codebase-Auditor-Agent`, creación confirmada de Issues de remediación (`create-issues --confirm`) y ejecución orquestada del lote resultante.

### Consecuencias
* **Positivas:**
  * Ejecución desatendida y predecible de lotes en `dev` una vez aprobado el Plan de Lote inicial.
  * Trazabilidad e higiene Git garantizadas entre tareas mediante `pm-agent gate`.
  * Contexto limpio por tarea y auditoría imparcial obligatoria antes de cada auto-merge a `dev`.
* **Negativas / Trade-offs:**
  * Si una tarea del lote falla tras 2 ciclos de `REQUEST_CHANGES` o introduce un conflicto arquitectónico no previsto, el lote se detiene de forma segura y escala al Arquitecto en lugar de forzar un merge defectuoso.
