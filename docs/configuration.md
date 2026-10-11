# Local configuration

Public examples describe the file format. Your actual installations and model warehouse belong in private `paths.yaml` and `mapping.yaml` files.

## Create your private files

From a source checkout:

```powershell
ai-toolchain --config-dir config/local init-config
```

For an installed package, run `ai-toolchain init-config` to use the operating system's user configuration directory. The command prints the generated file locations. You can instead supply `--config-dir` to choose any private directory.

The initializer copies the packaged examples without overwriting existing settings. Edit the generated files in a text editor. A template placeholder produces a configuration error until you replace it; templates do not point to a developer's machine.

Configuration selection uses `--config-dir` first, then `AI_TOOLCHAIN_CONFIG_DIR`, then the checkout's `config/local/`, then the user configuration directory for an installed package. To set an environment override in PowerShell:

```powershell
$env:AI_TOOLCHAIN_CONFIG_DIR = "E:/PrivateSettings/ai-toolchain"
ai-toolchain comfyui
```

The following drive names, folders, and installation labels are fictional examples.

## Fill in paths.yaml

```yaml
warehouse_root: "E:/ModelWarehouse"
a1111_root: "E:/Apps/stable-diffusion-webui"
link_mode: replace

comfyui_versions:
  - name: primary
    path: "E:/Apps/ComfyUI"
  - name: portable
    path: "E:/Apps/ComfyUI_Portable/ComfyUI"
```

| Field | What to enter |
|---|---|
| `warehouse_root` | The existing model warehouse directory containing categories such as `checkpoints`, `Lora`, and `VAEs`. This is independent of the tool's Git checkout. |
| `a1111_root` | The A1111 directory containing `launch.py`, `models/`, and the root-level `embeddings/` directory. Use `null` to disable A1111. |
| `link_mode` | `replace` links the entire application model folder after migration. `nested` keeps the folder and its local files and adds `WLink`. Both preview unless `--apply` is supplied. |
| `comfyui_versions[].name` | Your unique label for an installation, used by `--version`. For example, `primary` or `portable`; it need not match a release number. |
| `comfyui_versions[].path` | The directory containing ComfyUI's `main.py` and `models/`. In a portable distribution, select the inner `ComfyUI` folder. |

At least one application must be configured. For a ComfyUI-only setup, use `a1111_root: null`. For an A1111-only setup, use `comfyui_versions: []`.

For example, a ComfyUI-only configuration on a POSIX system is:

```yaml
warehouse_root: "/srv/ai-models"
a1111_root: null
link_mode: nested
comfyui_versions:
  - name: primary
    path: "/srv/apps/ComfyUI"
```

Use paths valid on the system running the tool. All roots must be absolute; `~` is expanded to your home directory. Environment-variable references inside YAML paths are not expanded. The older `repo_root` field is accepted as an alias for `warehouse_root`; new configurations should use `warehouse_root` and avoid specifying both.

On Windows, quoted forward slashes avoid YAML backslash escape problems: `"E:/Apps/ComfyUI"`. If you use backslashes, use YAML single quotes, for example `'E:\Apps\ComfyUI'`. Quoted paths can contain spaces.

Whole-folder migration uses hardlinks, so the application roots, warehouse, and optional `--state-dir` must share a filesystem volume. Two folders on the same drive letter can still cross a mounted volume; the tool checks the actual filesystem device.

## Fill in mapping.yaml

The left side is a path relative to the application root. The right side is a path relative to `warehouse_root`.

```yaml
a1111:
  models/Stable-diffusion: checkpoints
  models/Lora: Lora
  models/VAE: VAEs
  embeddings: Embeddings

comfyui:
  models/checkpoints: checkpoints
  models/loras: Lora
  models/vae: VAEs
  models/text_encoders: TextEncoders
```

With the Windows example above, `models/vae: VAEs` means:

```text
Application folder: E:/Apps/ComfyUI/models/vae
Warehouse target:   E:/ModelWarehouse/VAEs
```

The included mapping example lists additional model categories. Review it and change the values to match your own warehouse; category spelling is a convention, not a required layout. Use forward slashes for relative paths. Absolute paths, backslashes, drive letters, `.` and `..` components, and duplicate source keys ignoring case are rejected.

Existing unmapped application folders are reported. Missing application folders or warehouse targets are reported and skipped; the tool does not guess a target. Prepare the categories you intend to use before applying changes. If an application is disabled, its mapping section can be omitted.

## Preview before applying

```powershell
ai-toolchain --config-dir config/local comfyui --version primary
ai-toolchain --config-dir config/local a1111 --mode nested
ai-toolchain --config-dir config/local check all
```

`--version` must match a name from your private configuration. `check` reports missing and pending mappings with exit code 1. Invalid or unedited configuration produces exit code 2.

When you are ready to apply a preview, close affected applications and add `--apply` to that operation. Use `--no-progress` before the command to suppress progress output.

## Keep local information private

`config/local/` is ignored by Git and excluded from source distributions. The legacy filenames `config/paths.yaml` and `config/mapping.yaml` are also ignored; pass `--config-dir config` explicitly if you choose to keep local settings there. Packaged templates remain under different `.example.yaml` filenames.

Recovery state defaults to `<warehouse>/.ai_toolchain/`. Journals, archives, and CLI output may contain absolute paths, file names, and hashes. Keep them local, and reproduce problems with temporary folders or redact that information before sharing reports. Ignoring a file does not remove it from a previous commit; see the [publication guide](publishing.md).
