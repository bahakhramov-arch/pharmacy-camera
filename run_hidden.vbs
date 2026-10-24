' Pharmacy camera: start in background, without a window.
' Double-click to start. To stop: stop.bat. Log: logs\run.log
Set sh = CreateObject("WScript.Shell")
dir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)

' already running? then do nothing (no second copy)
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT * FROM Win32_Process WHERE Name='cmd.exe'")
For Each p In procs
    If Not IsNull(p.CommandLine) Then
        If InStr(1, p.CommandLine, dir & "\start.bat", 1) > 0 Then
            MsgBox "The program is already running.", 64, "Pharmacy camera"
            WScript.Quit
        End If
    End If
Next

sh.CurrentDirectory = dir
sh.Run "cmd /c """ & dir & "\start.bat""", 0, False
