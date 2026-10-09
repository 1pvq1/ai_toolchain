# ai_toolchain

A Python CLI for sharing one model warehouse across Stable Diffusion WebUI (A1111) and multiple ComfyUI installations. It can either replace each app's model folder with a directory symlink or keep the folder and add a nested `WLink`. The default is whole-folder replacement. Every command previews its work until `--apply` is supplied.

## Layout

```text
config/                            Machine paths and explicit model mappings
src/ai_toolchain/cli.py             CLI and dependency composition root
src/ai_toolchain/config.py          YAML validation
src/ai_toolchain/targets.py         Application mapping selection
src/ai_toolchain/storage/           Plans, migration, consolidation, recovery, and ports
src/ai_toolchain/storage/adapters/  Local filesystem, SHA-256, journals, process guard, progress
tests/storage/                      Temporary-directory storage integration tests
pyproject.toml                     Package and ai-toolchain entry point
```

Model files and recovery state stay outside this repository. `config/paths.yaml` names the warehouse, A1111 root, and ComfyUI versions. `config/mapping.yaml` maps paths relative to an application root to paths relative to the warehouse. Unmapped application folders are reported; the CLI does not infer a destination. A missing app folder or warehouse directory is reported and skipped.

The checked-in paths match this machine: `D:/AIProj/Models/CV`, `D:/AIProj/launcher/stable-diffusion-webui`, and `D:/AIProj/launcher/ComfyUI_Portable/{v018,v037_cu126}/ComfyUI`. Use `--config-dir` with another pair of YAML files for a different installation.

## Setup in aienv3

```powershell
conda activate aienv3
python -m pip install -e .
python -m pytest -q --basetemp=.pytest_cache/tmp
```

The `--basetemp` argument keeps test fixtures in the repository's ignored pytest cache, which is useful when the normal Windows temporary directory is restricted.

## Commands

```powershell
ai-toolchain a1111                         # preview whole-folder links
ai-toolchain comfyui --version v018         # preview one ComfyUI version
ai-toolchain comfyui --mode nested          # preview WLink links
ai-toolchain check all                      # read-only verification
ai-toolchain migrations                     # list migration journals
ai-toolchain consolidate                    # preview legacy backup consolidation
ai-toolchain resume MIGRATION_ID            # preview resuming interrupted work
ai-toolchain rollback MIGRATION_ID          # preview restoring folders
```

Add `--apply` to a mutating command to make its previewed changes. Close affected applications before applying; the CLI checks for running Python processes in the selected installs. Use `--mode replace|nested` to override `link_mode` from `paths.yaml`. Omitting `--version` handles every configured ComfyUI version. `--config-dir`, `--state-dir`, and `--no-progress` are global options and go before the command.

### Whole-folder migration

The tool checks all selected mappings before making changes. It rejects unexpected symlinks and destination collisions with different content. If a relative filename already exists in the warehouse, SHA-256 establishes whether the files are identical. The original app folder is staged under `<warehouse>/.ai_toolchain/staging`, unique files are hardlinked into their mapped warehouse category, and digest-addressed snapshot hardlinks are created under `.ai_toolchain/objects`. After verification, the application folder becomes a directory symlink. No permanent backup directory remains beside the app. The versioned manifest records files, old `WLink` targets, and empty folders.

Versioned manifests, staging, legacy journal archives, and snapshots live in hidden `<warehouse>/.ai_toolchain` by default. `--state-dir PATH` selects another state location on the same volume. If an operation is interrupted, use `ai-toolchain migrations` and `ai-toolchain resume ID --apply`. A completed migration can be rolled back: after checking snapshot and warehouse hashes and available space, files are **copied** back to reconstruct the original app folder. Warehouse models and snapshot hardlinks remain available. An in-place edit of a hardlinked model also edits its snapshot; digest checks refuse rollback in that case.

Switching a whole-folder link to `--mode nested --apply` reconstructs all folders in its migration from snapshots, then creates or repairs `WLink` inside them. A real folder or unrelated link at that name is a conflict. A mode switch may require substantial free space because local files are copied back.

### Legacy consolidation

`ai-toolchain consolidate` previews applied ComfyUI journals from the prior release. Use `--migration ID` to select one. It verifies every active link, recorded warehouse file, duplicate backup file, and unknown backup entry before writing. `--apply` creates and verifies snapshot hardlinks, moves adjacent backups into warehouse staging, removes verified redundant files, commits new manifests, and archives old journals. Existing application links stay in place. The two October 2026 ComfyUI migrations were consolidated; their adjacent backups held 10,774,052,856 redundant bytes.

On Windows, creating directory symlinks requires an account or Developer Mode with the necessary privilege. If creation fails, the journal retains the stage for `resume`. Hardlink snapshots require the state and warehouse models to be on the same volume.

## Development checks

Run tests and lint with the `aienv3` interpreter:

```powershell
python -m pytest -q --basetemp=.pytest_cache/tmp
python -m ruff check src tests
```

The tests use temporary warehouses and applications. Preview and `check` are read-only on the configured live paths.
