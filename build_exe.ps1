$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw 'Python da venv nao encontrado em .venv\Scripts\python.exe'
}

Write-Host 'Instalando dependencias de build (PyInstaller e Pillow)...'
& $python -m pip install --upgrade pip pyinstaller pillow

if (Test-Path 'Logo.jpg') {
    $sourceLogo = 'Logo.jpg'
} elseif (Test-Path 'logo6.png') {
    $sourceLogo = 'logo6.png'
} else {
    throw 'Nenhuma imagem base encontrada (Logo.jpg ou logo6.png).'
}

Write-Host "Gerando icon.png e icon.ico circulares a partir de $sourceLogo..."
$iconScript = @"
from PIL import Image, ImageDraw

source = r'$sourceLogo'
img = Image.open(source).convert('RGBA')

# recorte quadrado central
w, h = img.size
side = min(w, h)
left = (w - side) // 2
top = (h - side) // 2
img = img.crop((left, top, left + side, top + side)).resize((1024, 1024), Image.Resampling.LANCZOS)

# máscara circular com transparência fora do círculo
mask = Image.new('L', (1024, 1024), 0)
draw = ImageDraw.Draw(mask)
draw.ellipse((0, 0, 1023, 1023), fill=255)
img.putalpha(mask)

# salva PNG circular para o app
img.save('icon.png', format='PNG')

# salva ICO em múltiplos tamanhos
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save('icon.ico', format='ICO', sizes=sizes)
print('icon.png e icon.ico circulares gerados com sucesso')
"@
& $python -c $iconScript

Write-Host 'Limpando builds anteriores...'
if (Test-Path 'build') { Remove-Item -Recurse -Force 'build' }
if (Test-Path 'dist') { Remove-Item -Recurse -Force 'dist' }
if (Test-Path 'Obsidian AutoGit.spec') { Remove-Item -Force 'Obsidian AutoGit.spec' }

Write-Host 'Gerando executavel...'
& $python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name "Obsidian AutoGit" `
  --icon "icon.ico" `
  --version-file "version_info.txt" `
    --add-data "logo6.png;." `
    --add-data "Logo.jpg;." `
  --add-data "icon.png;." `
  --add-data "repos_aliases.json;." `
  --add-data "repos_extra.json;." `
  "autogit_gui.py"

Write-Host ''
Write-Host 'Build finalizado:'
Write-Host (Join-Path $PSScriptRoot 'dist\Obsidian AutoGit.exe')
