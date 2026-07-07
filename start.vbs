' docs2md Launcher -- runs the service with absolutely no windows.
' Usage:
'   wscript.exe start.vbs            default port 8000
'   wscript.exe start.vbs 9090       custom port
'   start.vbs                        (if .vbs is associated with wscript.exe)
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
dir = fs.GetParentFolderName(WScript.ScriptFullName)
port = "8000"
If WScript.Arguments.Count > 0 Then port = WScript.Arguments(0)
sh.Run "pythonw.exe " & Chr(34) & dir & "\src\launcher.py" & Chr(34) & " " & port, 0, False
Set sh = Nothing
