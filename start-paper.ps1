$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $projectRoot

Write-Host "Starting paper strategy loop..."
Start-Process -FilePath "python" -ArgumentList "run.py", "paper", "--loop" -WorkingDirectory $projectRoot -WindowStyle Hidden
Write-Host "Starting dashboard at http://127.0.0.1:8765"
python run.py dashboard
