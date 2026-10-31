# Findings Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reports emit findings as JSON, the CLI remembers declined findings and installs itself, and a bundled skill turns findings into approved fixes.

**Architecture:**
- Each report method builds a `Report` (name, totals, ranked `Finding`s) instead of a markdown string.
- `render.py` turns a `Report` into markdown or JSON.
- `declined.py` filters out findings the user declined. `install.py` copies the bundled `SKILL.md` and registers the Stop hook.
- No clustering change: each report keeps a `normalised → [originals]` map and takes the examples from it.

**Tech Stack:** Python 3.12+, stdlib `json`/`uuid`/`importlib.resources`, existing `fire`, `platformdirs`, `model2vec`, scikit-learn, `typesafe-sdk`. pytest. uv.

**Spec:** `docs/superpowers/specs/2026-10-31-findings-skill-design.md`

## Global Constraints

- Python `>=3.12`. No new runtime dependencies.
- Markdown stays the default output. JSON comes with `--format json` on stdout. Logging goes to stderr.
- Findings hold redacted text only (`redaction.redact`), and nothing is sent anywhere except TypeSafe under `--jev`.
- At most 4 findings per skill batch. Take the top finding of each report first, then the rest by `total`.
- State files live under `platformdirs.user_data_path('repeat-o-scope', appauthor=False)`.
- `install` is idempotent, leaves other hooks alone, replaces a `worklog.ps1` Stop entry, and backs up `settings.json` first.
- Repo rules: keyword-only arguments for two or more parameters, private helpers, `_LOGGER`, negative tests first, tests through the real stack (real-shaped transcripts via `tests/conftest.py`).

## Review Focus

1. **Unparsable `~/.claude/settings.json`** (comments, trailing comma, BOM). `install` must raise and write nothing, never overwrite it. Test in Task 4.
2. **Missing `settings.json`, or no `hooks` key.** `install` creates exactly what it needs. Test in Task 4.
3. **`decline` with an id the last run didn't produce.** Exit non-zero with the id in the message, and store nothing. Test in Task 3.
4. **No transcripts in the window.** `--format json` prints valid JSON with `findings: []`, and the markdown says 0. Test in Task 2.
5. **Non-ASCII text (Finnish `ä`, `…`) on a Windows console with a legacy code page.** The JSON goes out as UTF-8 without crashing. Test in Task 2 (a subprocess with `PYTHONIOENCODING=cp1252`).

---

### Task 1: `Finding` and `Report` model, reports build them

**Files:**
- Create: `src/repeat_o_scope/findings.py`
- Modify: `src/repeat_o_scope/cli.py` (the report methods return `Report` through a renderer; behaviour unchanged)
- Test: `tests/test_findings.py`; the existing `tests/test_cli.py` must keep passing unchanged

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Variant: text: str; count: int`
  - `@dataclass(frozen=True) class Finding: id: str; kind: str; total: int; variants: list[Variant]; examples: list[str]; label: str | None`. `kind` is one of `prompt`, `command`, `ask`, `failure`, `denial`.
  - `@dataclass(frozen=True) class Report: name: str; days: int; jev: bool; totals: dict[str, int]; findings: list[Finding]; note: str = ''`
  - `class RepeatOScopeError(ValueError)`: this repo's domain error (it has no `vainu.data.exceptions`). Later tasks raise it, with the offending value in the message.
  - `def finding_id(*, report: str, top_variant: str) -> str`: `str(uuid.uuid5(_NAMESPACE, f'{report}:{top_variant}'))`, where `_NAMESPACE` is a fixed `uuid.UUID` constant in `findings.py`.
  - `def from_cluster(*, report: str, kind: str, cluster: cluster.Cluster, originals: Mapping[str, list[str]], label: str | None = None) -> Finding`. `examples` holds up to 3 distinct `originals[top variant]` entries.
  - `cli.Scope._report_<name>() -> Report` for `prompts`, `commands`, `asks`, `fails`.
    - `totals` keys: `items`, `groups`, `one_offs`. `fails` also has `denials`. `asks` uses `turn_endings` and `wasted`.
- [ ] **Step 1: Write failing tests** in `tests/test_findings.py`:
  - `test_finding_id_is_stable_and_report_scoped`: equal for the same inputs. It differs when `report` differs and when `top_variant` differs.
  - `test_from_cluster_takes_examples_from_top_variant`: `Cluster(total=5, variants=[('ship', 4), ('ship it', 1)])` with `originals={'ship': ['Ship', 'ship', 'ship'], 'ship it': ['Ship it']}` gives `examples == ['Ship', 'ship']` (distinct, in order) and `variants[0] == Variant('ship', 4)`.
  - `test_fails_report_puts_denials_in_same_list` (via `Scope(root=...)._report_fails()`, using the fixture from `test_cli.py::test_fails_report_separates_denials`): the kinds are `{'failure', 'denial'}` and `totals['denials'] == 1`.
- [ ] **Step 2: Run** `uv run pytest tests/test_findings.py -q`. Expect a failure with `ModuleNotFoundError: repeat_o_scope.findings`.
- [ ] **Step 3: Implement** `findings.py`, then refactor `cli.py`:
  - Each `_report_*` collects `originals` as `dict[str, list[str]]`, keyed by normalised text, from the redacted raw texts.
  - For `fails`, an example is `f'{tool}: {attempt} → {error}'`.
  - The public `prompts()`/`commands()`/`asks()`/`fails()` return `render.markdown(report)`. In this task, `render.markdown` can be the current `_render` logic moved over unchanged.
  - `asks` without Jev: one finding, `kind='ask'`, variants = closing lines, `id` from `top_variant='*'`. With Jev: one finding per kind, `label=kind`.
  - `fails` with Jev: the kept failure groups get `label='real'`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expect everything to pass, including the unchanged `test_cli.py`.
- [ ] **Step 5: Commit** `Model reports as findings`.

### Task 2: `--format json`

**Files:**
- Create: `src/repeat_o_scope/render.py` (`markdown(report)`, `json_document(report)`)
- Modify: `src/repeat_o_scope/cli.py` (`Scope.__init__` gains `format: str = 'markdown'`; `main()` reconfigures stdout to UTF-8)
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Report`, `Finding`, `Variant` (Task 1)
- Produces:
  - `render.markdown(report: Report) -> str`
  - `render.json_document(report: Report) -> str`: `json.dumps(..., ensure_ascii=False, indent=2)` with the top-level keys `report`, `days`, `generated_at` (ISO 8601, UTC), `jev`, `totals`, `findings`. Each finding is `{id, kind, total, variants: [{text, count}], examples, label}`.
  - An unknown `format` raises `RepeatOScopeError` (Task 1): ``f'Unknown format `{format}`, expected markdown or json!'``.
- [ ] **Step 1: Write failing tests:**
  - `test_unknown_format_raises`: `Scope(format='xml').prompts()` raises `RepeatOScopeError` matching `xml`.
  - `test_json_no_secret_anywhere`: a transcript with `glpat-abcdefghij1234567890` in 4 prompts, 4 commands and 4 tool errors. For each report, the JSON text doesn't contain `glpat-abcdefghij1234567890`.
  - `test_json_empty_window`: no transcripts gives `json.loads(out)['findings'] == []` and `totals['items'] == 0`.
  - `test_json_whole_document`: 5× `ship` prompts. `json.loads` minus `generated_at` equals the full expected dict, with the `id` from `finding_id(report='prompts', top_variant='ship')`.
  - `test_json_utf8_on_legacy_console`: a subprocess running `main()` with `prompts --format=json --root=...` and env `PYTHONIOENCODING=cp1252`, over 4× `tämä…`. Return code 0, and the stdout bytes decode as UTF-8 and contain `tämä…`.
- [ ] **Step 2: Run** `uv run pytest tests/test_render.py -q`. Expect a failure: `render` is missing.
- [ ] **Step 3: Implement** `render.py`. In `main()`, call `sys.stdout.reconfigure(encoding='utf-8')` before `fire.Fire`. In `Scope`, dispatch on `format` with a dict `{'markdown': render.markdown, 'json': render.json_document}`, raising for an unknown key.
- [ ] **Step 4: Run** `uv run pytest -q`. Expect everything to pass.
- [ ] **Step 5: Commit** `Add --format json to every report`.

### Task 3: Declined findings

**Files:**
- Create: `src/repeat_o_scope/declined.py`
- Modify: `src/repeat_o_scope/cli.py` (`Scope.__init__` gains `all: bool = False` and `data_dir: str | None = None`; new method `Scope.decline(finding_id: str) -> str`; every report run writes the last findings)
- Test: `tests/test_declined.py`

**Interfaces:**
- Consumes: `Finding`, `Report` (Task 1)
- Produces:
  - `declined.remember_last(*, data_dir: Path, report: Report) -> None`: merges `{id: [variant texts]}` into `data_dir/last-findings.json`.
  - `declined.decline(*, data_dir: Path, finding_id: str) -> list[str]`: moves that id's variants into `data_dir/declined.json` and returns them. An unknown id raises ``RepeatOScopeError(f'Unknown finding `{finding_id}`: run a report first!')``.
  - `declined.drop_declined(*, data_dir: Path, findings: list[Finding]) -> list[Finding]`: leaves out a finding whose `variants[0].text` is in any declined variant set.
  - `data_dir` defaults to `platformdirs.user_data_path('repeat-o-scope', appauthor=False)`. Tests pass `data_dir=str(tmp_path)`.
- [ ] **Step 1: Write failing tests:**
  - `test_decline_unknown_id_raises_and_stores_nothing`: `declined.json` doesn't exist afterwards.
  - `test_declined_finding_is_left_out_next_run`: run `prompts` (5× `ship`), decline its first finding id, run again. `ship` is absent.
  - `test_declined_survives_top_variant_shift`: decline when `ship` × 5 / `ship it` × 4. On the next data, `ship it` × 6 / `ship` × 5, and the cluster is still left out, because `ship it` was among the declined variants.
  - `test_all_shows_declined_again`: `Scope(all=True, ...)` includes it.
- [ ] **Step 2: Run** `uv run pytest tests/test_declined.py -q`. Expect a failure: `declined` is missing.
- [ ] **Step 3: Implement** `declined.py`, and wire it into `Scope`: filter in `_report_*` unless `all`, then `remember_last`. `Scope.decline` returns ``f'Declined `{finding_id}`: {", ".join(variants)}'``. `fire` turns the raised error into a non-zero exit.
- [ ] **Step 4: Run** `uv run pytest -q`. Expect everything to pass.
- [ ] **Step 5: Commit** `Remember declined findings`.

### Task 4: `install` / `uninstall`

**Files:**
- Create: `src/repeat_o_scope/install.py`, `src/repeat_o_scope/skill/SKILL.md` (placeholder text in this task; Task 5 writes it)
- Modify: `src/repeat_o_scope/cli.py` (`Scope.install() -> str`, `Scope.uninstall() -> str`, and `Scope.__init__` gains `claude_dir: str | None = None`, default `~/.claude`)
- Test: `tests/test_install.py`

**Interfaces:**
- Produces:
  - `install.install(*, claude_dir: Path) -> list[str]` returns what it changed, as human-readable lines.
  - `install.uninstall(*, claude_dir: Path) -> list[str]`
  - The hook entry is `{"type": "command", "command": "repeat-o-scope-worklog"}` inside a `Stop` group `{"hooks": [...]}`.
  - Any existing Stop command containing `worklog.ps1` is removed. A group left empty is dropped.
  - Before the first write, the backup goes to `settings.json.repeat-o-scope.bak`, and `uninstall` restores the hook part from the current file, not from the backup.
  - The skill is copied from `importlib.resources.files('repeat_o_scope') / 'skill' / 'SKILL.md'` to `claude_dir/skills/repeat-o-scope/SKILL.md`.
- [ ] **Step 1: Write failing tests** (all against `tmp_path` as `claude_dir`):
  - `test_unparsable_settings_raises_and_writes_nothing`: `settings.json` = `{ // comment\n }`. It raises `RepeatOScopeError` naming the path, the file bytes are unchanged, and there's no skill dir.
  - `test_install_into_empty_claude_dir`: afterwards `settings.json` equals `{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "repeat-o-scope-worklog"}]}]}}` and the skill file exists.
  - `test_install_twice_is_idempotent`: one Stop entry, and the file is unchanged by the second run.
  - `test_install_replaces_worklog_ps1_and_keeps_other_hooks`: start from a settings file with a `worklog.ps1` Stop hook, a `SessionStart` hook and `"model": "x"`. Assert the whole resulting dict.
  - `test_uninstall_reverses_install`: install, then uninstall, gives the original dict (minus the replaced `worklog.ps1`, which is not restored) and no skill dir.
- [ ] **Step 2: Run** `uv run pytest tests/test_install.py -q`. Expect a failure: `install` is missing.
- [ ] **Step 3: Implement** `install.py`. Read with `encoding='utf-8-sig'`, and raise on `json.JSONDecodeError` with `from err`. Write with `indent=2` and `ensure_ascii=False`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expect everything to pass.
- [ ] **Step 5: Commit** `Install the skill and Stop hook from the CLI`.

### Task 5: The skill

**Files:**
- Modify: `src/repeat_o_scope/skill/SKILL.md`
- Test: `tests/test_skill.py`

**Interfaces:**
- Consumes: the CLI surface from Tasks 2–4: `repeat-o-scope <report> --format json [--jev] [--all]`, `repeat-o-scope decline <id>`

The skill text must say:
- **Frontmatter:** `name: repeat-o-scope`. The description triggers on "what do I keep repeating", "automate the boring things", `/repeat-o-scope`.
- **Step 1:** if `repeat-o-scope` isn't on PATH, show `uv tool install repeat-o-scope` and stop.
- **Step 2:** run all four reports with `--format json`. Add `--jev` only when `TYPESAFE_API_KEY` is already set, and never read secrets. Add `--all` only when the user asked to see everything.
- **Step 3:** take the top finding of each report, then the rest by `total`, at most 4.
- **Step 4:** for each finding, read the files the fix would touch (shell profile: `$PROFILE`, `~/.zshrc` or `~/.bashrc`; `~/.claude/settings.json`; `~/.claude/CLAUDE.md`; the skill or script involved), then draft one to three concrete fixes. The fix kinds are those in the spec's decision 4. Exact text only, never generic advice.
- **Step 5:** one `AskUserQuestion` call with one question per finding. Each option label says what "yes" does, and every question also has the option `Leave it`.
- **Step 6:** apply each picked fix. For `Leave it`, run `repeat-o-scope decline <id>`. Report what changed in one short list. Don't end with a question unless there are more findings and at least one was approved, in which case ask once whether to do the next batch.
- **Never:** push, post, file issues, or edit files the picked option didn't name.
- [ ] **Step 1: Write failing tests:** `test_skill_frontmatter` parses the frontmatter and checks `name == 'repeat-o-scope'` and that the description is non-empty. `test_skill_mentions_cli_surface` checks the text contains `--format json`, `repeat-o-scope decline`, `Leave it` and `uv tool install repeat-o-scope`.
- [ ] **Step 2: Run** `uv run pytest tests/test_skill.py -q`. Expect a failure against the placeholder.
- [ ] **Step 3: Write** `SKILL.md`.
- [ ] **Step 4: Run** `uv run pytest -q`. Expect everything to pass.
- [ ] **Step 5: Commit** `Write the repeat-o-scope skill`.

### Task 6: PyPI packaging, release workflow, README

**Files:**
- Modify: `pyproject.toml` (`license = "MIT"`, `authors`, `[project.urls]`, `classifiers`, keywords; the version stays `0.1.0`)
- Create: `.github/workflows/release.yml` (on tag `v*`: `uv build`, then `pypa/gh-action-pypi-publish` with `id-token: write`, no token secret)
- Create: `LICENSE` (MIT, Kimmo Parviainen-Jalanko)
- Modify: `README.md` (install becomes `uv tool install repeat-o-scope` then `repeat-o-scope install`; add the skill and `--format json`/`decline`/`--all`; note that the first publish needs the trusted publisher set up on PyPI)
- Test: `tests/test_package.py`

- [ ] **Step 1: Write failing test:** `test_wheel_contains_skill` runs `uv build --wheel --out-dir <tmp>` (subprocess, `timeout=300`), opens the wheel with `zipfile`, and asserts that `repeat_o_scope/skill/SKILL.md` is in its names.
- [ ] **Step 2: Run** it. Expect a pass or a failure depending on uv_build's defaults. If it already passes, keep it as the guard and move on.
- [ ] **Step 3: Make the pyproject, workflow, LICENSE and README edits.**
- [ ] **Step 4: Run** `uv run pytest -q` and `uv build`. Both succeed.
- [ ] **Step 5: Commit** `Prepare the PyPI release`.

## Left out (outward-facing, Kimmo's steps)

- Setting up the trusted publisher on PyPI, and pushing the first `v0.1.0` tag.
- Making the repo public.
