@echo off
chcp 65001 >nul
title RESENHA MAXIMA - Deploy do Bot
cd /d "%~dp0"

echo ==========================================
echo   RESENHA MAXIMA - DEPLOY DO BOT
echo ==========================================
echo.

where git >nul 2>nul
if errorlevel 1 (
    echo [ERRO] Git nao foi encontrado neste computador.
    echo Instale o Git e tente novamente.
    pause
    exit /b 1
)

if not exist ".git" (
    echo [ERRO] Esta pasta nao e um repositorio Git.
    echo Coloque este arquivo .bat na raiz do repositorio do bot.
    pause
    exit /b 1
)

for /f "delims=" %%B in ('git branch --show-current') do set "BRANCH=%%B"

if "%BRANCH%"=="" (
    echo [ERRO] Nao foi possivel identificar a branch atual.
    pause
    exit /b 1
)

echo Branch atual: %BRANCH%
echo.
echo Adicionando arquivos...
git add .
if errorlevel 1 goto :erro

echo.
set "MSG=Atualizacao bot RESENHA MAXIMA"
set /p "MSG=Mensagem do deploy [Atualizacao bot RESENHA MAXIMA]: "
if "%MSG%"=="" set "MSG=Atualizacao bot RESENHA MAXIMA"

echo.
echo Criando commit...
git commit -m "%MSG%"
if errorlevel 1 (
    echo.
    echo Nenhuma alteracao nova para commit ou o commit nao foi necessario.
    echo Tentando enviar o que ja estiver pendente...
)

echo.
echo Enviando para o GitHub...
git push origin "%BRANCH%"
if errorlevel 1 goto :erro

echo.
echo ==========================================
echo   DEPLOY ENVIADO COM SUCESSO!
echo ==========================================
echo.
echo O Railway deve iniciar o deploy automaticamente
echo depois que o GitHub receber o push.
echo.
pause
exit /b 0

:erro
echo.
echo ==========================================
echo   O DEPLOY FALHOU
echo ==========================================
echo Veja o erro acima.
echo.
pause
exit /b 1
