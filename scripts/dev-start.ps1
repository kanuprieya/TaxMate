# Starts the full TaxMate stack for local dev, natively (no Docker/WSL2
# required — see .venv setup notes in doc-parser/agent-orchestrator/rag-service).
# Each service opens in its own PowerShell window so you can watch its logs
# and close it independently. Run from anywhere; paths are resolved relative
# to this script's location.
#
# Usage:  .\scripts\dev-start.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

function Start-Service($name, $workDir, $command, $envVars) {
    Write-Host "Starting $name..." -ForegroundColor Cyan
    $envLines = ($envVars.GetEnumerator() | ForEach-Object { "`$env:$($_.Key) = '$($_.Value)'" }) -join "; "
    $full = "cd '$workDir'; $envLines; $command"
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $full -WindowStyle Normal
}

# Load .env into a hashtable (shared by every Python/Node service below)
$envFile = Join-Path $root ".env"
$baseEnv = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match "^[^#\s].*=" } | ForEach-Object {
        $k, $v = $_ -split "=", 2
        $baseEnv[$k.Trim()] = $v.Trim()
    }
} else {
    Write-Warning ".env not found at $envFile — copy .env.example to .env and add your LLM API key first."
}

# doc-parser (FastAPI, :8002)
Start-Service "doc-parser" "$root\doc-parser" `
    ".\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8002" `
    $baseEnv

# agent-orchestrator (FastAPI, :8000)
$aoEnv = $baseEnv.Clone()
$aoEnv["RAG_SERVICE_URL"]  = "http://localhost:8001"
$aoEnv["DOC_PARSER_URL"]   = "http://localhost:8002"
Start-Service "agent-orchestrator" "$root\agent-orchestrator" `
    ".\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000" `
    $aoEnv

# rag-service (FastAPI, :8001) — optional; comment out if its torch install
# isn't working on your machine. Everything else degrades gracefully without it.
# Start-Service "rag-service" "$root\rag-service" `
#     ".\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8001" `
#     $baseEnv

# api-gateway (Express, :3001)
$gwEnv = $baseEnv.Clone()
$gwEnv["PORT"]             = "3001"
$gwEnv["DOC_PARSER_URL"]   = "http://localhost:8002"
$gwEnv["RAG_SERVICE_URL"]  = "http://localhost:8001"
$gwEnv["AGENT_ORCH_URL"]   = "http://localhost:8000"
$gwEnv["FRONTEND_URL"]     = "http://localhost:3000"
$gwEnv["SKIP_AUTH"]        = "true"
Start-Service "api-gateway" "$root\api-gateway" "node src/index.js" $gwEnv

# frontend (Next.js, :3000)
Start-Service "frontend" "$root\frontend" "npm run dev" @{}

Write-Host "`nAll services launching in separate windows. Give them ~10s, then check:" -ForegroundColor Green
Write-Host "  http://localhost:8002/health   (doc-parser)"
Write-Host "  http://localhost:8000/health   (agent-orchestrator)"
Write-Host "  http://localhost:3001/api/health (api-gateway)"
Write-Host "  http://localhost:3000          (frontend)"
