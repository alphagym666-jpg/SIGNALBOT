# Mise a jour automatique de SignalBot (lancee par « Lancer SignalBot.bat »).
# Telecharge la derniere version sur GitHub et la copie par-dessus le dossier du bot,
# SANS toucher a .env (tes cles), data (memoire du bot) ni .venv (modules installes).
# Pour la desactiver : cree un fichier « pas_de_mise_a_jour.txt » dans le dossier du bot.
param([string]$Dest)
$ErrorActionPreference = 'Stop'
$url = 'https://github.com/alphagym666-jpg/SIGNALBOT/archive/refs/heads/claude/dazzling-noether-gtwt5f.zip'
$urlFile = Join-Path $Dest 'outils\adresse_mise_a_jour.txt'
if (Test-Path $urlFile) {
    $custom = (Get-Content $urlFile -Raw).Trim()
    if ($custom) { $url = $custom }
}
$tmp = Join-Path $env:TEMP 'signalbot_maj'
try {
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp 'maj.zip'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip -TimeoutSec 60
    Expand-Archive -Path $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (-not $src -or -not (Test-Path (Join-Path $src.FullName 'signalbot'))) { throw 'archive inattendue' }
    $null = robocopy $src.FullName $Dest /E /XD .venv data .git /XF .env .requirements.installed /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { throw "copie impossible (code $LASTEXITCODE)" }
    Write-Host '  [OK] Le bot est a jour.'
}
catch {
    Write-Host "  [!] Mise a jour impossible ($($_.Exception.Message)) : on garde la version actuelle."
}
finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }
}
exit 0
