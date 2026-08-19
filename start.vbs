' docs2md Launcher -- runs the service with absolutely no windows.
' Usage:
'   wscript.exe start.vbs            default port 8000
'   wscript.exe start.vbs 9090       custom port
'   start.vbs                        (if .vbs is associated with wscript.exe)
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
dir = fs.GetParentFolderName(WScript.ScriptFullName)
venvPythonw = dir & "\venv\Scripts\pythonw.exe"
If fs.FileExists(venvPythonw) Then
  pythonw = venvPythonw
Else
  pythonw = "pythonw.exe"
End If
port = "8000"
If WScript.Arguments.Count > 0 Then port = WScript.Arguments(0)
sh.Run Chr(34) & pythonw & Chr(34) & " " & Chr(34) & dir & "\src\launcher.py" & Chr(34) & " " & port, 0, False
Set sh = Nothing
