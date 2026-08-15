<#
.SYNOPSIS
    Build a .venv for the CFTR Variant Toolkit and prove the notebooks can run in it.

.DESCRIPTION
    Creates .venv/ in the repo root, installs every dependency the audited notebooks
    and modules need, registers a Jupyter kernel pointing at it, then VERIFIES rather
    than assumes: it imports each third-party package the notebooks use, imports the
    repo's own modules, and runs the fresh-clone smoke test.

    Three things this deliberately does:

      * installs torch BEFORE the Pangolin package, because that package imports torch
        while building and fails confusingly if it is missing;
      * installs the CPU torch build by default (works everywhere; -Cuda switches to a
        CUDA wheel). pangolin_build.py checks torch.cuda.is_available() at run time, so the
        same code uses a GPU when there is one;
      * leaves your global Python untouched — everything lands in .venv/, which is
        gitignored.

.PARAMETER SkipPangolin
    Skip torch + the Pangolin model package. The other nine notebooks work without
    them; only tools/08 needs them. Saves a ~250 MB download.

.PARAMETER Cuda
    Install a CUDA torch build instead of CPU, e.g. -Cuda cu124. Requires a matching
    NVIDIA driver. See https://pytorch.org for the right tag.

.PARAMETER Recreate
    Delete an existing .venv first.

.EXAMPLE
    .\setup_env.ps1
    .\setup_env.ps1 -SkipPangolin
    .\setup_env.ps1 -Cuda cu124 -Recreate
#>
[CmdletBinding()]
param(
    [switch]$SkipPangolin,
    [string]$Cuda = "",
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
$venv = Join-Path $repo ".venv"
$py = Join-Path $venv "Scripts\python.exe"

function Step($msg) { Write-Host "`n=== $msg" -ForegroundColor Cyan }

# ── 1. interpreter ─────────────────────────────────────────────────────────
Step "Locating a Python interpreter"
$base = $null
foreach ($cand in @("py -3.13", "py -3", "python")) {
    $exe, $sw = $cand.Split(" ", 2)
    try {
        $v = if ($sw) { & $exe $sw -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" }
             else     { & $exe    -c "import sys;print('.'.join(map(str,sys.version_info[:2])))" }
        if ($LASTEXITCODE -eq 0 -and $v) { $base = $cand; Write-Host "  using '$cand' (Python $v)"; break }
    } catch { }
}
if (-not $base) { throw "No Python found on PATH. Install Python 3.11+ from python.org." }

# ── 2. the venv ────────────────────────────────────────────────────────────
if ($Recreate -and (Test-Path $venv)) {
    Step "Removing the existing .venv"
    Remove-Item -Recurse -Force $venv
}
if (-not (Test-Path $py)) {
    Step "Creating $venv"
    $exe, $sw = $base.Split(" ", 2)
    if ($sw) { & $exe $sw -m venv $venv } else { & $exe -m venv $venv }
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
} else {
    Step "Reusing the existing .venv (pass -Recreate to rebuild it)"
}
& $py -m pip install --upgrade pip setuptools wheel --quiet
if ($LASTEXITCODE -ne 0) { throw "pip bootstrap failed" }

# ── 3. dependencies ────────────────────────────────────────────────────────
Step "Installing requirements.txt (tools/01-07, benchmark/00-01)"
& $py -m pip install -r (Join-Path $repo "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "requirements.txt install failed" }

if (-not $SkipPangolin) {
    $idx = if ($Cuda) { "https://download.pytorch.org/whl/$Cuda" }
           else       { "https://download.pytorch.org/whl/cpu" }
    Step "Installing torch from $idx  (~250 MB, this is the slow part)"
    & $py -m pip install torch --index-url $idx
    if ($LASTEXITCODE -ne 0) { throw "torch install failed -- see https://pytorch.org for the right wheel" }

    Step "Installing the Pangolin model package (needs git on PATH)"
    & $py -m pip install -r (Join-Path $repo "requirements-pangolin.txt")
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pangolin install failed. Everything except tools/08 still works."
        Write-Warning "Most often this is git missing from PATH: https://git-scm.com/download/win"
    }
} else {
    Step "Skipping torch + Pangolin (-SkipPangolin); tools/08 will not run"
}

# ── 4. Jupyter kernel ──────────────────────────────────────────────────────
Step "Registering the Jupyter kernel 'cftr-toolkit'"
& $py -m ipykernel install --user --name cftr-toolkit --display-name "Python (CFTR toolkit)"
if ($LASTEXITCODE -ne 0) { Write-Warning "kernel registration failed; 'jupyter lab' will still work from the venv" }

# ── 5. verify, don't assume ────────────────────────────────────────────────
Step "Verifying the environment"
$check = @'
import importlib, pathlib, sys
repo = pathlib.Path(__file__).resolve().parent if "__file__" in dir() else pathlib.Path.cwd()
repo = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(repo)); sys.path.insert(0, str(repo / "tools"))
core = [("pandas",None),("numpy",None),("requests",None),("matplotlib",None),
        ("Bio.bgzf","biopython"),("pyarrow",None),("openpyxl",None),
        ("nbformat",None),("nbconvert",None),("ipykernel",None)]
extra = [("torch",None),("pangolin.model","pangolin"),("pyfaidx",None),("gffutils",None)]
local = ["toolkit","spliceai_build","pangolin_build"]
bad = []
def probe(mod, pkg, required):
    try:
        m = importlib.import_module(mod)
        v = getattr(m, "__version__", "")
        print(f"  ok       {mod:16} {v}")
    except Exception as e:
        tag = "MISSING " if required else "absent  "
        print(f"  {tag} {mod:16} ({type(e).__name__})")
        if required: bad.append(mod)
print("core packages:")
for m,p in core: probe(m,p,True)
print("tools/08 extras:")
for m,p in extra: probe(m,p,False)
print("repo modules:")
for m in local:
    try:
        importlib.import_module(m); print(f"  ok       {m}")
    except Exception as e:
        print(f"  MISSING  {m} ({type(e).__name__}: {e})"); bad.append(m)
try:
    import torch
    print(f"\ntorch {torch.__version__} | CUDA available: {torch.cuda.is_available()}")
except Exception:
    print("\ntorch not installed -- tools/08 will not run")
if bad:
    print("\nFAILED:", bad); sys.exit(1)
print("\nall required imports resolved")
'@
$checkFile = Join-Path $env:TEMP "cftr_env_check.py"
Set-Content -Path $checkFile -Value $check -Encoding utf8
& $py $checkFile $repo
$verifyOk = ($LASTEXITCODE -eq 0)

Step "Running the fresh-clone smoke test"
& $py (Join-Path $repo ".github\scripts\smoke_fresh_clone.py")
$smokeOk = ($LASTEXITCODE -eq 0)
if (-not $smokeOk) {
    Write-Warning "smoke test did not pass. If data/ holds locally built extracts it refuses to run by design -- that is expected in a working checkout, not a failure."
}

# ── 6. what to do next ─────────────────────────────────────────────────────
Step "Done"
if ($verifyOk) { Write-Host "  environment verified" -ForegroundColor Green }
else           { Write-Host "  some required imports failed -- see above" -ForegroundColor Red }
Write-Host @"

  Activate it:      .\.venv\Scripts\Activate.ps1
  Launch Jupyter:   .\.venv\Scripts\jupyter.exe lab
  In a notebook:    pick the kernel 'Python (CFTR toolkit)'
  Run one headless: .\.venv\Scripts\python.exe -m jupyter nbconvert --to notebook ``
                        --execute --inplace tools\07_spliceai.ipynb

  Note: the notebooks read data/, which is gitignored and mostly not shipped. Each
  build cell prints what to download and where to put it the first time you run it.
"@
