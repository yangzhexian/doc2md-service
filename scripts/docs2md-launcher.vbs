' ─────────────────────────────────────────────────────────────────────────────
' docs2md Silent Launcher (Windows)
' Runs start.bat in a hidden window so no console flashes on login.
' ─────────────────────────────────────────────────────────────────────────────
Set objShell = CreateObject("WScript.Shell")
projectDir = objShell.CurrentDirectory
batPath = projectDir & "\start.bat"
objShell.Run """" & batPath & """", 0, False
Set objShell = Nothing
