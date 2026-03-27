@echo off
setlocal EnableExtensions

set "RAVEBERRY_ENV=Raveberry"
set "RAVEBERRY_CONFIG=%USERPROFILE%\raveberry.yaml"

set "CONDA_BAT="

if exist "%USERPROFILE%\miniconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "%USERPROFILE%\anaconda3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "%USERPROFILE%\miniforge3\condabin\conda.bat" set "CONDA_BAT=%USERPROFILE%\miniforge3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\miniconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\miniconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\anaconda3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\anaconda3\condabin\conda.bat"
if not defined CONDA_BAT if exist "C:\ProgramData\miniforge3\condabin\conda.bat" set "CONDA_BAT=C:\ProgramData\miniforge3\condabin\conda.bat"

if not defined CONDA_BAT (
    echo.
    echo [ERROR] Could not find conda.bat.
    echo Edit this file and set CONDA_BAT manually if your Conda install is elsewhere.
    pause
    exit /b 1
)

call "%CONDA_BAT%" activate "%RAVEBERRY_ENV%"
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to activate Conda environment "%RAVEBERRY_ENV%".
    echo If you used a different env name in the installer, change RAVEBERRY_ENV in this file.
    pause
    exit /b 1
)

if not defined CONDA_PREFIX (
    echo.
    echo [ERROR] CONDA_PREFIX is missing after activation.
    pause
    exit /b 1
)

set "RAVEBERRY_SCRIPT=%CONDA_PREFIX%\Scripts\raveberry"

if not exist "%RAVEBERRY_SCRIPT%" (
    echo.
    echo [ERROR] Raveberry launcher not found:
    echo %RAVEBERRY_SCRIPT%
    pause
    exit /b 1
)

set "RB_QUEUE_COUNT=0"
set "RB_CURRENT_COUNT=0"

for /f "usebackq tokens=1,2" %%A in (`python -c "import os, sys, pathlib, raveberry; base=pathlib.Path(raveberry.__file__).resolve().parent; os.chdir(base); sys.path.insert(0, str(base)); os.environ['DJANGO_SETTINGS_MODULE']='main.settings'; os.environ['DJANGO_DEBUG']='1'; import django; django.setup(); from core.models import QueuedSong, CurrentSong; print(f'{QueuedSong.objects.count()} {CurrentSong.objects.count()}')"` ) do (
    set "RB_QUEUE_COUNT=%%A"
    set "RB_CURRENT_COUNT=%%B"
)

echo.
if "%RB_QUEUE_COUNT%"=="0" if "%RB_CURRENT_COUNT%"=="0" (
    echo No saved queue or current song was found from the last session.
) else (
    echo Found saved playback state from the last session:
    echo   Queued songs: %RB_QUEUE_COUNT%
    echo   Current song entry: %RB_CURRENT_COUNT%
    echo.
    choice /C YN /N /M "Reset the saved queue/current song before starting? [Y/N]: "
    echo.
    if errorlevel 2 (
        echo Keeping saved queue/current song and resuming previous session state if available.
    ) else (
        python -c "import os, sys, pathlib, raveberry; base=pathlib.Path(raveberry.__file__).resolve().parent; os.chdir(base); sys.path.insert(0, str(base)); os.environ['DJANGO_SETTINGS_MODULE']='main.settings'; os.environ['DJANGO_DEBUG']='1'; import django; django.setup(); from core.models import QueuedSong, CurrentSong; QueuedSong.objects.all().delete(); CurrentSong.objects.all().delete()"
        if errorlevel 1 (
            echo [WARNING] Failed to clear the saved queue/current song. Starting anyway.
        ) else (
            echo Saved queue/current song was cleared.
        )
    )
)

title Raveberry
echo.
echo Starting Raveberry...
echo Env:    %RAVEBERRY_ENV%
echo Config: %RAVEBERRY_CONFIG%
echo URL:    http://127.0.0.1:8080/
echo.

python "%RAVEBERRY_SCRIPT%" run --nomopidy
set "RB_EXIT=%ERRORLEVEL%"

echo.
if not "%RB_EXIT%"=="0" (
    echo Raveberry exited with code %RB_EXIT%.
) else (
    echo Raveberry stopped.
)
pause
exit /b %RB_EXIT%
