@echo off
echo Installing dependencies...
python -m pip install -r "%~dp0requirements.txt"
echo.
echo Adding this folder to your PATH so "pctrn" works from anywhere...
powershell -NoProfile -Command "$d='%~dp0'.TrimEnd('\'); $p=[Environment]::GetEnvironmentVariable('Path','User'); if(($p -split ';') -notcontains $d){[Environment]::SetEnvironmentVariable('Path',($p.TrimEnd(';')+';'+$d),'User'); Write-Host 'Added.'} else {Write-Host 'Already on PATH.'}"
echo.
echo Done. Close this window, open a NEW terminal, and type: pctrn
pause
