# setup.ps1 — First-time setup for TRABA Labs on a new Windows desktop
# Run once from the repo root:  powershell -ExecutionPolicy Bypass -File setup.ps1

Set-StrictMode -Off
$ErrorActionPreference = 'Stop'

function Say  { param($m) Write-Host "  $m" -ForegroundColor Cyan }
function OK   { param($m) Write-Host "  OK  $m" -ForegroundColor Green }
function Fail { param($m) Write-Host "  ERR $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "  TRABA Labs — Setup" -ForegroundColor White
Write-Host "  ==================" -ForegroundColor DarkGray
Write-Host ""

# ── Node.js ─────────────────────────────────────────────────────────────────
Say "Checking Node.js..."
try {
    $nodeVer = node --version 2>&1
    $nodeMaj = [int]($nodeVer -replace 'v(\d+).*','$1')
    if ($nodeMaj -lt 18) { Fail "Node.js >= 18 required (found $nodeVer). Download from https://nodejs.org/" }
    OK "Node.js $nodeVer"
} catch {
    Fail "Node.js not found. Download from https://nodejs.org/"
}

# ── npm install ──────────────────────────────────────────────────────────────
Say "Installing Node packages (npm install)..."
npm install --silent
if ($LASTEXITCODE -ne 0) { Fail "npm install failed" }
OK "Node packages installed"

# ── Python ───────────────────────────────────────────────────────────────────
Say "Checking Python..."
$pyCmd = $null
foreach ($cmd in @('python', 'python3')) {
    try {
        $ver = & $cmd --version 2>&1
        if ($ver -match 'Python 3\.(\d+)') {
            $pyCmd = $cmd
            OK "Python: $ver  (command: $cmd)"
            break
        }
    } catch {}
}
if (-not $pyCmd) { Fail "Python 3 not found. Download from https://python.org/" }

# ── pip packages ─────────────────────────────────────────────────────────────
Say "Installing Python packages (pip install -r scripts/db_recon/requirements.txt)..."
& $pyCmd -m pip install -r scripts/db_recon/requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
OK "Python packages installed"

# ── data directory ───────────────────────────────────────────────────────────
Say "Ensuring data/db_recon/ exists..."
New-Item -ItemType Directory -Force -Path "data/db_recon" | Out-Null
OK "data/db_recon/ ready"

# ── Done ─────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host ""
Write-Host "  Next steps:" -ForegroundColor White
Write-Host "    1. Set a login password (first time only):" -ForegroundColor DarkGray
Write-Host "         node scripts/set-password.mjs" -ForegroundColor Yellow
Write-Host "    2. Start the server:" -ForegroundColor DarkGray
Write-Host "         node serve.mjs" -ForegroundColor Yellow
Write-Host "    3. Open in browser:" -ForegroundColor DarkGray
Write-Host "         http://localhost:3000" -ForegroundColor Yellow
Write-Host ""
