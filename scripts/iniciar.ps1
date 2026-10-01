# Executado pela tarefa agendada "Assistente de voz" ao entrar no Windows.
# Espera o Docker Desktop, atualiza o IP da rede local e sobe os contêineres.
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
docker info *> $null
if ($LASTEXITCODE -ne 0 -and (Test-Path $exe)) { Start-Process $exe }
$limite = (Get-Date).AddMinutes(10)
do { Start-Sleep 5; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $limite)
& python scripts\atualizar_ip.py
docker compose up -d --remove-orphans
