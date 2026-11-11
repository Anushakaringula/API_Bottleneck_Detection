# 2. Development setup

The reference machine is the project laptop: **Ubuntu 22.04, Node.js 20,
Python 3.10**. Experiments for the report run there. Team members on Windows can
develop and run the tests in WSL2 (recommended) or Git Bash.

No software is installed without the team reviewing the exact command first.
The commands below are for review, not to be pasted blindly.

## Requirements

| Tool | Version | Used for |
|---|---|---|
| Node.js | 20 (CI uses 20; newer versions work for development) | `target-api/` |
| npm | comes with Node | installs `target-api` dependencies from `package-lock.json` |
| Python | 3.12 (3.10+ for everything except diagnosis); `pip install -r requirements.txt` for diagnosis (scikit-learn, XGBoost) | `experiments/`, `pipeline/`, scripts |
| bash, `sha256sum` | any | `scripts/*.sh` (Git Bash includes both on Windows) |

## Ubuntu (project laptop)

```sh
node --version      # v20.x
python3 --version   # 3.10.x
cd ~/api-bottleneck-research
(cd target-api && npm ci)
source scripts/env.sh
scripts/test-all.sh
scripts/verify-legacy.sh    # needs the legacy data (see below)
```

## Windows

### Option A: WSL2 (recommended, same as the laptop)

```powershell
wsl --install -d Ubuntu-22.04     # one time, needs admin; reboot when asked
```

Then inside Ubuntu, install Node 20 and Python 3.10 the same way as on the laptop.
Clone the repository **inside the Linux file system** (for example
`~/API_Bottleneck_Detection`), not under `/mnt/c`, and follow the Ubuntu steps.

### Option B: Git Bash with native Windows Node and Python

1. Install Python 3.10+ from python.org and tick "Add python.exe to PATH". The
   `python`/`python3` entries under `WindowsApps` are Store shortcuts that do not
   run; turn them off in *Settings → Apps → Advanced app settings → App execution
   aliases*.
2. Install Node.js 20 LTS.
3. In Git Bash:

   ```sh
   cd "/c/Users/<you>/Desktop/Major Project/API_Bottleneck_Detection"
   (cd target-api && npm ci)
   source scripts/env.sh       # sets PYTHONPATH with Windows paths and ';', and ABR_PYTHON
   echo "$ABR_PYTHON"          # python on Windows, python3 on Linux
   scripts/test-all.sh
   ```

   In the docs, `python3 -m ...` means `"$ABR_PYTHON" -m ...` (that is,
   `python -m ...`) in Git Bash.

Windows notes:

- A child process cannot catch `SIGTERM` on Windows. The target API therefore
  also shuts down gracefully on an IPC `"shutdown"` message, which is what the
  smoke test uses. Ctrl+C in a terminal works on every platform.
- `.gitattributes` keeps `*.sh`, `SHA256SUMS` and `*.tsv` at LF line endings.
  A clone made before `.gitattributes` existed may still have CRLF copies; see
  "Refreshing line endings" below.

## Experiment lab: Docker (PostgreSQL + Redis)

The whole project uses a **local lab** for every measured experiment (decision of
10 Nov 2026). Cloud round trips (Neon 113–203 ms, Redis Cloud 317 ms p50) are larger than
the effects being studied. The project PC is the experiment machine.

One-time install on Windows (PowerShell as Administrator, then restart Windows):

```powershell
wsl --install --no-distribution
winget install -e --id Docker.DockerDesktop
```

After the restart, start Docker Desktop once and accept its terms. WSL2 resources are set
in `%USERPROFILE%\.wslconfig`: 6 CPUs, 8 GB, 2 GB swap. On Ubuntu, install Docker Engine
and the compose plugin instead.

```sh
docker compose version
cp .env.lab.example .env.lab
scripts/lab-up.sh && scripts/lab-reset.sh
scripts/lab-probe.sh                    # must print: gate ... PASS
```

Details: [infrastructure/README.md](../infrastructure/README.md).

### Environment profiles

| Profile | File | Database / cache | Start the API | Shell |
|---|---|---|---|---|
| `lab` | `.env.lab` (from `.env.lab.example`) | local lab, `127.0.0.1` | `npm run start:lab` | `ABR_ENV_FILE=.env.lab source scripts/env.sh` |
| `cloud` | `.env` | Neon + Redis Cloud | `npm run start:env` | `source scripts/env.sh` |

Both files are git-ignored. The lab probe refuses to run against a non-local host.

## Environment

`cp .env.example .env` is optional; every value has a default. Load it, set
`PYTHONPATH` and pick the Python interpreter with:

```sh
source scripts/env.sh
```

## Legacy data

The frozen Phase 1–3 data is not in git (`data/**` is ignored except the
integrity files). Copy it from the project laptop backup
(`~/abr-backups/2026-11-10/`) into `data/raw/legacy-2026-11-09/` and
`data/processed/legacy/`, then check it:

```sh
(cd data/raw/legacy-2026-11-09 && sha256sum -c SHA256SUMS)
(cd data/processed/legacy && sha256sum -c SHA256SUMS)
scripts/verify-legacy.sh
```

## Refreshing line endings

After `.gitattributes` is committed, an existing clone keeps its old working-tree
files until they are checked out again. With a clean working tree:

```sh
git rm --cached -r -q .
git reset --hard
```

## Continuous integration

`.github/workflows/test.yml` runs `scripts/test-all.sh` on Ubuntu 22.04 with
Node 20 and Python 3.10 on every push and pull request. `verify-legacy.sh` is not
run in CI because the legacy data is not in git.
