# ai_toolchain

A Python CLI for sharing one model warehouse across Stable Diffusion WebUI (A1111) and multiple ComfyUI installations. Replace application model folders with directory symlinks, or preserve local files and add a nested `WLink`.

Model operations preview their work unless `--apply` is supplied. `init-config` creates editable configuration files. Model data, machine paths, and recovery records belong in private local storage.

## Requirements

- Python 3.11 or newer.
- Existing application installations and warehouse category folders.
- Whole-folder migration and consolidation require applications, warehouse models, and recovery state on the same filesystem volume.
- On Windows, directory symlink creation needs Developer Mode or the appropriate account privilege.

Windows is the primary development platform. The configuration also accepts absolute POSIX paths; verify filesystem and symlink support on your own platform before applying changes.

## Quick start

From a source checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
ai-toolchain --config-dir config/local init-config
```

Edit the generated `paths.yaml` and `mapping.yaml` in `config/local/`. That directory is ignored by Git and excluded from source distributions. The templates contain placeholders, so commands refuse to use them until you enter your paths.

For an installed package, `ai-toolchain init-config` uses your operating system's user configuration directory and prints its location. To keep settings elsewhere:

```powershell
ai-toolchain --config-dir "E:/PrivateSettings/ai-toolchain" init-config
ai-toolchain --config-dir "E:/PrivateSettings/ai-toolchain" comfyui
```

The drive and folder names in documentation examples are fictional. Replace them with paths on your own system. See [configuration examples and field explanations](docs/configuration.md).

## Configuration

The repository and package contain portable examples:

```text
config/
  paths.example.yaml
  mapping.example.yaml
  local/                  # ignored private settings created on your machine
src/ai_toolchain/
  default_config/         # packaged copies of the examples
  config.py               # local configuration discovery and validation
  config_setup.py         # template initialization with injected filesystem access
  cli.py                  # dependency composition root and commands
  storage/                # planning, migration, consolidation, recovery, and adapters
tests/                    # temporary installations; no personal configuration required
```

Configuration is selected in this order:

1. Global `--config-dir DIRECTORY`.
2. The `AI_TOOLCHAIN_CONFIG_DIR` environment variable.
3. `config/local/` in a source checkout.
4. The operating system's user configuration directory for an installed package.

Each private directory contains `paths.yaml` and `mapping.yaml`. Packaged examples are never used as active settings. Absolute roots belong in `paths.yaml`; mapping keys and values are relative paths. A1111 and ComfyUI can be configured independently.

## Commands

After filling in your local configuration:

```powershell
ai-toolchain a1111                         # preview whole-folder links
ai-toolchain comfyui --version primary     # preview one user-named install
ai-toolchain comfyui --mode nested         # preview WLink links
ai-toolchain check all                     # read-only inventory and link checks
ai-toolchain migrations                    # list local journals
ai-toolchain consolidate                   # preview legacy backup consolidation
ai-toolchain resume MIGRATION_ID           # preview resuming interrupted work
ai-toolchain rollback MIGRATION_ID         # preview restoring original folders
```

Add `--apply` to a model operation to make changes. Close affected applications first; the tool checks for running processes in the selected installs. `--mode replace|nested` overrides the configured mode. Omitting `--version` selects all configured ComfyUI installs.

`--config-dir`, `--state-dir`, and `--no-progress` are global options and go before the command. Progress shows bytes in a terminal and concise stage summaries when redirected. Use `--no-progress` to suppress it.

Missing folders and unmapped application directories are reported. The tool does not guess destinations or create a missing application installation. `check` returns 0 when every selected mapping is linked, 1 for missing or pending mappings, and 2 for configuration errors.

## Storage and recovery

Whole-folder migration preflights selected mappings, checks collisions using SHA-256, stages original folders inside the warehouse, and creates digest-addressed hardlink snapshots. Verified files are published into their mapped categories without overwriting warehouse files. The original application folders become directory symlinks; no permanent adjacent backup folders remain.

By default, state lives under `<warehouse>/.ai_toolchain/`:

```text
journals/    Versioned manifests and operation phases
objects/     Digest-addressed hardlink snapshots
staging/     Temporary migration and rollback folders
legacy/      Archived journals from the earlier backup format
```

The state directory receives the Windows Hidden attribute when supported. `--state-dir DIRECTORY` overrides its location and must keep state on the same volume.

Interrupted operations can be resumed by journal ID. Rollback verifies recorded hashes and free space, then **copies** snapshot files back and reconstructs recorded links and directories. Warehouse models and snapshots are retained. Hardlinks share an inode: an in-place model edit also changes its snapshot, and digest checks then block rollback.

`consolidate` converts applied legacy ComfyUI journals and adjacent backup folders into warehouse state. It verifies active links, recorded files, hashes, and backup inventory before cleanup. Use `--migration ID` to select one journal. Application links stay in place.

## Development

Tests create temporary warehouses and applications. They do not depend on a developer's local configuration or model collection.

```powershell
python -m pip install -e ".[dev]"
python -m pytest -q --basetemp=.pytest_cache/tmp
python -m ruff check src tests
python -m build --wheel --sdist
```

An existing Conda environment also works; activate your chosen environment before running these commands.

## Preparing a public repository

Keep real configuration in `config/local/` or outside the checkout. Recovery journals and CLI output contain absolute paths and model inventory details; replace them with synthetic examples before sharing them in issues.

[The publication guide](docs/publishing.md) covers reviewing staged files, checking release archives, and publishing a clean source history. Removing a path from the current files does not remove it from earlier Git commits.
