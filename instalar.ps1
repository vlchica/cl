#Requires -Version 5.1
<#
  Instalador do assistente de voz local para Windows 10/11.
  Instala o que faltar (WSL2, Docker Desktop, Python) e chama scripts\instalar.py.
  A GPU NVIDIA é usada pelo Docker Desktop via WSL2; basta o driver normal do Windows.

  Uso (PowerShell):  powershell -ExecutionPolicy Bypass -File .\instalar.ps1
  Opções: -ForcarOmni  -ForcarCpu  -SemImagens  -PularTeste  -SemAutostart
#>
param([switch]$ForcarOmni, [switch]$ForcarCpu, [switch]$SemImagens, [switch]$PularTeste, [switch]$SemAutostart)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Reabre como administrador se preciso
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) {
  $args2 = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"") + ($PSBoundParameters.Keys | ForEach-Object { "-$_" })
  Start-Process powershell -Verb RunAs -ArgumentList $args2
  exit
}

Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force -Path logs | Out-Null
Start-Transcript -Path ("logs\instalar-windows-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date)) | Out-Null

function Passo($m) { Write-Host "`n> $m" -ForegroundColor Cyan }
function Aviso($m) { Write-Host "!! $m" -ForegroundColor Yellow }
function Atualizar-Path {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function Continuar-Depois-De-Reiniciar {
  $cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
  New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'AssistenteDeVoz' -Value $cmd -Force | Out-Null
  Aviso 'É preciso reiniciar para ativar o WSL2. O Windows reinicia em 2 minutos e a instalação continua sozinha ao entrar.'
  Aviso 'Para adiar: shutdown /a'
  shutdown /r /t 120 /c "Reinicio para ativar o WSL2 (assistente de voz)"
  Stop-Transcript | Out-Null
  exit
}

$winget = Get-Command winget -ErrorAction SilentlyContinue
if (-not $winget) { throw 'winget não encontrado. Atualize o "Instalador de Aplicativo" pela Microsoft Store e rode de novo.' }

# ------------------------------------------------------------------ GPU
Passo 'Placa de vídeo'
$nvidia = Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match 'NVIDIA' }
if ($nvidia) {
  $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
  if (-not $smi) {
    Aviso 'GPU NVIDIA encontrada, mas sem driver. Instalando o NVIDIA App (que instala o driver)...'
    winget install -e --id Nvidia.NvidiaApp --accept-package-agreements --accept-source-agreements --silent | Out-Null
    Atualizar-Path
  }
  try {
    $drv = (& nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader) -join ' | '
    Write-Host $drv
    $versao = [int]((& nvidia-smi --query-gpu=driver_version --format=csv,noheader | Select-Object -First 1).Split('.')[0])
    if ($versao -lt 535) { Aviso "Driver $versao é antigo: atualize pelo NVIDIA App para 550 ou mais novo (CUDA 12)." }
  } catch { Aviso 'Não consegui ler o driver NVIDIA; o assistente usará a CPU se a GPU não funcionar.' }
} else {
  Aviso 'Nenhuma GPU NVIDIA: tudo vai rodar na CPU (mais lento).'
}

# ------------------------------------------------------------------ WSL2
Passo 'WSL2'
wsl --status *> $null
if ($LASTEXITCODE -ne 0) {
  wsl --install --no-distribution
  Continuar-Depois-De-Reiniciar
}
wsl --update *> $null

# ------------------------------------------------------------------ Docker Desktop
Passo 'Docker Desktop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  winget install -e --id Docker.DockerDesktop --accept-package-agreements --accept-source-agreements --silent `
    --override 'install --quiet --accept-license --backend=wsl-2 --always-run-service'
  Atualizar-Path
}
docker info *> $null
if ($LASTEXITCODE -ne 0) {
  $exe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
  if (Test-Path $exe) { Start-Process $exe }
  Write-Host 'Aguardando o Docker iniciar...'
  $limite = (Get-Date).AddMinutes(6)
  do { Start-Sleep 5; docker info *> $null } until ($LASTEXITCODE -eq 0 -or (Get-Date) -gt $limite)
  if ($LASTEXITCODE -ne 0) { Continuar-Depois-De-Reiniciar }
}
docker compose version

# ------------------------------------------------------------------ Python
Passo 'Python'
$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py -or ((& python --version 2>&1) -notmatch 'Python 3\.(9|1\d)')) {
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent --scope machine
  Atualizar-Path
}

# ------------------------------------------------------------------ resto
Passo 'Instalando e configurando o assistente'
$opcoes = @()
if ($ForcarOmni) { $opcoes += '--forcar-omni' }
if ($ForcarCpu) { $opcoes += '--forcar-cpu' }
if ($SemImagens) { $opcoes += '--sem-imagens' }
if ($PularTeste) { $opcoes += '--pular-teste' }
if ($SemAutostart) { $opcoes += '--sem-autostart' }
$env:PYTHONIOENCODING = 'utf-8'
& python scripts\instalar.py @opcoes
$codigo = $LASTEXITCODE
Stop-Transcript | Out-Null
exit $codigo
