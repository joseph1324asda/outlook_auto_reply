$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3 and ensure it is on PATH."
    exit 1
}

if ($args -notcontains "--no-install") {
    python -m pip install -r requirements.txt
}

$env:LLM_MAILER_SSL = "adhoc"
$env:PORT = "7999"

python -m llm_mailer.web_app
