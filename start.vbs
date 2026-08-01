' 代码实时上传器 - 后台无窗口启动
' 双击运行即可，不弹出控制台窗口
' 请将下面的路径改为你的实际安装路径
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.Run "pythonw.exe """ & scriptDir & "\autosync.py""", 0, False
