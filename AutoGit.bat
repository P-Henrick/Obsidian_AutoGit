@echo off
cd /d "%~dp0"
title Obsidian AutoGit

if exist "dist\Obsidian AutoGit.exe" (
    start "Obsidian AutoGit" "dist\Obsidian AutoGit.exe"
    exit /b 0
)

:: Verifica se Python está instalado
where python >nul 2>&1
if errorlevel 1 (
    echo ERRO: Python nao encontrado no PATH.
    echo Instale o Python em https://python.org e tente novamente.
    pause
    exit /b 1
)

:: Abre a interface grafica (sem janela de console extra)
start "Obsidian AutoGit" pythonw autogit_gui.py

:: Se pythonw falhar, tenta com python normal
if errorlevel 1 (
    python autogit_gui.py
)
