@echo off
REM Automatically pushes all local changes to the GitHub repository.
REM Run this ONLY from inside the FootBall-Stats folder (the local clone).

cd /d "%~dp0"

echo ============================================
echo Checking for changes...
echo ============================================
git status

echo.
echo ============================================
echo Adding all changes...
echo ============================================
git add .

echo.
echo ============================================
echo Committing...
echo ============================================
git commit -m "Update files"

echo.
echo ============================================
echo Pushing to GitHub...
echo ============================================
git push

echo.
echo ============================================
echo DONE.
echo ============================================
pause
