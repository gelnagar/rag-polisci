@echo off
title PoliSci RAG - install / repair / update
echo Installing or repairing PoliSci RAG. Your books, models and settings are kept.
powershell -NoProfile -ExecutionPolicy Bypass -Command "[Net.ServicePointManager]::SecurityProtocol='Tls12'; iex ((New-Object Net.WebClient).DownloadString('https://github.com/gelnagar/rag-polisci/releases/latest/download/install.ps1'))"
pause
