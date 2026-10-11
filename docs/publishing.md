# Publishing without machine-specific information

Publish the portable examples, application code, and synthetic tests. Keep real configuration, model data, recovery records, and local command logs outside the public source snapshot.

## Review the current source

Real settings should be in `config/local/` or outside the checkout. Check the ignore rules:

```powershell
git check-ignore config/local/paths.yaml config/local/mapping.yaml
git status --short --ignored config/local
```

Review the paths and contents you stage, including new untracked files:

```powershell
git diff --cached --name-only
git diff --cached
```

After staging removal of any formerly tracked private settings, this command should show no private configuration or root-level recovery files:

```powershell
git ls-files -- config/local config/paths.yaml config/mapping.yaml .ai_toolchain
```

Public examples should use placeholders or explicitly fictional paths. Review usernames, drive and folder names, personal installation labels, migration IDs, and operational records. Local configuration is preserved by moving it to an ignored directory before sanitizing tracked files.

## Check distributions

```powershell
python -m pytest -q --basetemp=.pytest_cache/tmp
python -m ruff check src tests
python -m build --wheel --sdist
```

Inspect both archives in `dist/`. A wheel should contain only the packaged `.example.yaml` templates; a source distribution may also contain the public examples and documentation. Neither should contain actual `paths.yaml` or `mapping.yaml` settings, `config/local/`, model files, journals, staging folders, or local logs. Packaging exclusions supplement Git ignore rules because the two mechanisms serve different purposes.

## Review history before pushing

Deleting or sanitizing a file in the latest revision does not erase its contents from earlier commits. A later public push can include those commits even when the current working tree is clean. Check the history of the original configuration filenames:

```powershell
git log --all -- config/paths.yaml src/ai_toolchain/default_config/paths.yaml
```

If old commits contain personal information, publish from a fresh source snapshot or coordinate a history rewrite before making that history public. A previously private remote can expose old commits when its visibility changes. Also review the author name and email that you intend to make public.

To prepare a fresh snapshot, first commit the reviewed sanitized source locally, then export that revision:

```powershell
git archive --format=zip --output=../ai-toolchain-public-source.zip HEAD
```

Extract that archive into a new, empty directory. It contains the reviewed tracked source and no original `.git` history or ignored local settings. Inspect it, add the license you have chosen for the project, and initialize a new repository using your intended public Git identity:

```powershell
git init
git add .
git commit -m "Initial public source"
```

Connect this fresh repository to the intended public remote after reviewing the snapshot. Keep the original checkout as your local working repository with its private configuration. These instructions do not push code or rewrite an existing repository's history.
