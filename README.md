# Project-Manager-Agent

Orquestador técnico de lotes de GitHub Issues (**Staff Technical PM**) que coordina ejecuciones multi-issue de extremo a extremo sobre `dev`, combinando un CLI determinista en Python stdlib (`pm-agent`) con aislamiento de contexto por subagentes e integración nativa con [`issue-driven-workflow`](https://github.com/JerbilX/Dotfiles-v3), [`PR-Reviewer-Agent`](https://github.com/JerbilX/PR-Reviewer-Agent) y [`Codebase-Auditor-Agent`](https://github.com/JerbilX/Codebase-Auditor-Agent).

## Arquitectura y Flujo de Orquestación (`v0.1.0` — Modo Backlog)

Documentado en [`ADR-0001`](docs/adr/0001-arquitectura-orquestador-lotes-mas-cli-determinista.md):

1. **Fase 1 — Triage Determinista y Plan de Lote (*Front-Loading Executive Decisions*):**
   - `pm-agent inspect <repo_or_path> [--issues 4,5,6]` recolecta estado Git (`branch`, `is_clean`, `synced_with_origin_dev`), especificación completa de las Issues del lote, clasificación preliminar `N1`/`N2`, perfil de modelo `model_profile` (`fast` | `balanced` | `deep`) y modelo concreto sugerido `recommended_model` desde `**Model:** ...` (con fallback Hydrogen-First para issues legadas y validación semántica del PM en issues sin cabecera explícita), dependencias, PRs abiertos y gobernanza local (`docs/adr/` aceptados, `.agents/rules/`, `CONTRIBUTING.md`).
   - En modo ejecutivo (`"dame al PM"`), el agente agrupa las tareas de menor a mayor dificultad (sin exponer IDs técnicos de `#issue`/`#PR`), sugiere en cada opción numerada el **modelo concreto recomendado para la UI** y levanta **al inicio** todas las decisiones ejecutivas o ADRs pendientes para aprobación única del Arquitecto.
2. **Fase 2 — Ejecución por Tarea con Aislamiento de Contexto y Auto-Merge a `dev`:**
   - **Pre-Task Gate (`pm-agent gate --stage pre-task`):** Verifica por código que el workspace esté en `dev`, con árbol limpio y sincronizado con `origin/dev`.
   - **Enrutamiento de `Model` en `invoke_subagent`:** Cada tarea enruta automáticamente el parámetro `Model` según su `model_profile` (`fast` → `"inherit"` en sesión *Gemini 4 Hydrogen Medium* o `"flash"` si corre en un modelo superior, `balanced` → `"inherit"`, `deep` → `"pro"` o `"inherit"` si la sesión ya corre en `deep`, evitando variantes *Orchestrator* en implementadores; con escalado `fast -> balanced` / `"pro"` si rebota en `pr-reviewer` y requiere remediación compleja).
   - **Implementación (`invoke_subagent` limpio):** Ejecuta `issue-driven-workflow` (Pasos 1 a 6) en una rama efímera hasta abrir el PR hacia `dev` usando el `Model` enrutado.
   - **Auditoría Pre-Merge Obligatoria (`invoke_subagent` + `pr-reviewer`):** Audita el PR sin sesgo de autor con el `Model` correspondiente al perfil de la Issue.
     - Si devuelve `REQUEST_CHANGES`: corrige en la misma rama efímera y re-audita (máximo 2 ciclos; si persiste un `BLOCKER`, detiene el lote y escala al Arquitecto).
     - Si devuelve `APPROVE`: ejecuta automáticamente el Squash Merge a `dev` (`gh pr merge --squash --delete-branch`), cierra explícitamente la Issue (`gh issue close --reason completed`), limpia la rama local y verifica el estado con **`pm-agent gate --stage post-merge --issue <id> --pr <pr_id>`**.
3. **Fase 3 — Quality Gate de Fin de Lote (`codebase-auditor`) y Reporte Ejecutivo:**
   - Ejecuta `codebase-audit scan <repo_path> --run-checks` para verificar que el lote no introdujo monolitos, deriva arquitectónica ni tests rotos.
   - Genera el reporte ejecutivo con `pm-agent batch-summary <repo_or_path> --issues 4,5,6` listo para que el Arquitecto evalúe la promoción `dev -> main`.

## Requisitos

- Python >= 3.11 (cero dependencias externas de runtime)
- `git` y `gh` CLI autenticado (`gh auth status`)
- Ecosistema de skills hermanos instalados:
  - [`JerbilX/Dotfiles-v3`](https://github.com/JerbilX/Dotfiles-v3) (`issue-driven-workflow` y `adr-manager`)
  - [`JerbilX/PR-Reviewer-Agent`](https://github.com/JerbilX/PR-Reviewer-Agent) (`pr-reviewer` / CLI `pr-review`)
  - [`JerbilX/Codebase-Auditor-Agent`](https://github.com/JerbilX/Codebase-Auditor-Agent) (`codebase-auditor` / CLI `codebase-audit`)

## Uso Rápido (CLI `pm-agent`)

```bash
# 1. Fase 1: Inspeccionar un lote de Issues y estado de gobernanza/Git
pm-agent inspect /ruta/al/repo --issues 4,5,6 --out /tmp/pm_inspect.json

# 2. Fase 2 (Pre-Task Gate): Verificar que local esté en dev, limpio y sincronizado con origin/dev
pm-agent gate /ruta/al/repo --stage pre-task

# 3. Fase 2 (Post-Merge Gate): Verificar cierre de Issue, merge de PR a dev y sincronización local
pm-agent gate /ruta/al/repo --stage post-merge --issue 4 --pr 10

# 4. Fase 3: Generar resumen consolidado del lote (JSON o Markdown)
pm-agent batch-summary /ruta/al/repo --issues 4,5,6 --format markdown

# 5. Modo Post-Auditoría: Previsualizar (--dry-run) o crear (--confirm) lote desde audit_report.json
pm-agent ingest-audit /ruta/al/repo --report /tmp/audit_report.json --min-priority HIGH
pm-agent ingest-audit /ruta/al/repo --report /tmp/audit_report.json --min-priority HIGH --confirm
```

## Instalación del CLI y Registro Declarativo en Antigravity

Instalar el binario `pm-agent` en `~/.local/bin`:
```bash
pip install --user --no-build-isolation --no-deps -e .
```

Registrar el directorio `skill/` de forma declarativa en `~/.gemini/config/skills.json` (gestionado por [`JerbilX/Dotfiles-v3`](https://github.com/JerbilX/Dotfiles-v3)):
```json
{
  "entries": [
    {
      "path": "~/Proyectos-IA-V3/Project-Manager-Agent/skill"
    }
  ]
}
```

## Roadmap Evolutivo (`v0.2.0+`)

- **`v0.1.0` — Modo Backlog (Implementado):** Ejecución orquestada de lotes de GitHub Issues abiertas existentes en un repositorio objetivo (por lista de IDs `--issues`, por `--label` o todas las abiertas).
- **`v0.2.0+` — Modo Post-Auditoría (Implementado):** Ingerir hallazgos priorizados de `codebase-audit` (`audit_report.json`) con `pm-agent ingest-audit`, previsualizar el lote en `--dry-run` (exponiendo `model_profile` y `recommended_model` e inyectando/propagando `**Model:**`), crear las Issues aprobadas con `--confirm` y obtener el `recommended_order` listo para ejecutar la remediación en `dev`.
- **`v0.2.0+` — Modo Épica / Idea:** Desglosar un requerimiento de alto nivel en múltiples Issues atómicas coherentes (con objetivos técnicos, criterios de aceptación y grafo de dependencias) antes de ejecutar el lote.

## Tests

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```
