taskkill /f /im explorer.exe
del /a "%LOCALAPPDATA%\IconCache.db"
del /a "%LOCALAPPDATA%\Microsoft\Windows\Explorer\iconcache*"
start explorer.exe