$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "Installing dependencies..."
py -3 -m pip install --upgrade pip
py -3 -m pip install -r requirements.txt

Write-Host "Building L4D2ModManager.exe ..."
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed `
  --name L4D2ModManager --paths src --add-data "page.html;." `
  --collect-all webview --collect-all clr_loader `
  --hidden-import clr --hidden-import pythonnet `
  --hidden-import tkinter --hidden-import tkinter.filedialog `
  --hidden-import tkinter.messagebox `
  src/l4d2_webapp.py

Write-Host "Built: dist/L4D2ModManager.exe"
