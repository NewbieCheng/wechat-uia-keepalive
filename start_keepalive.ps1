# Start lightweight UIA keepalive for WeChat (no Narrator UI).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found in PATH."
}

python -m pip install -q -r requirements.txt
python keepalive.py @args
