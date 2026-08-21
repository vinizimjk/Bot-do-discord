@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ==========================================
echo   RESENHA MAXIMA - SINCRONIZAR AUDIOS
echo ==========================================
echo.

if not exist "audios_call" (
    echo [ERRO] A pasta audios_call nao foi encontrada.
    echo Coloque este arquivo na mesma pasta do bot.py.
    pause
    exit /b 1
)

echo Verificando alteracoes na pasta audios_call...
git status --porcelain -- audios_call > "%TEMP%\resenha_audios_status.txt"

for %%A in ("%TEMP%\resenha_audios_status.txt") do set tamanho=%%~zA

if "%tamanho%"=="0" (
    echo.
    echo Nenhuma alteracao de audio encontrada.
    echo Nada para enviar.
    del "%TEMP%\resenha_audios_status.txt" >nul 2>&1
    pause
    exit /b 0
)

echo.
echo Alteracoes encontradas:
type "%TEMP%\resenha_audios_status.txt"
echo.

git add audios_call
if errorlevel 1 goto :erro

git diff --cached --quiet -- audios_call
if not errorlevel 1 (
    echo Nenhuma alteracao nova ficou preparada para commit.
    del "%TEMP%\resenha_audios_status.txt" >nul 2>&1
    pause
    exit /b 0
)

git commit -m "Sincronizar audios da zoeira"
if errorlevel 1 goto :erro

echo.
echo Enviando para o GitHub...
git push
if errorlevel 1 goto :erro

echo.
echo ==========================================
echo  AUDIOS SINCRONIZADOS COM SUCESSO!
echo  A Railway fara o deploy automaticamente.
echo ==========================================
del "%TEMP%\resenha_audios_status.txt" >nul 2>&1
pause
exit /b 0

:erro
echo.
echo ==========================================
echo  ERRO AO SINCRONIZAR OS AUDIOS
echo ==========================================
echo Confira a mensagem acima.
del "%TEMP%\resenha_audios_status.txt" >nul 2>&1
pause
exit /b 1
