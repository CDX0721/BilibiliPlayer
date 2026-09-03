param([string[]]$Keys = @("{F12}"))
Add-Type -TypeDefinition @"
using System;using System.Runtime.InteropServices;
public class W3{[DllImport("user32.dll")]public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")]public static extern bool ShowWindow(IntPtr h,int c);
[DllImport("user32.dll")]public static extern IntPtr GetForegroundWindow();}
"@
$p = Get-Process msedge -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowHandle -ne 0} | Select-Object -First 1
if (-not $p) { Write-Output "no-window"; exit 1 }
[W3]::ShowWindow($p.MainWindowHandle, 9) | Out-Null
[W3]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 800
$fg = [W3]::GetForegroundWindow()
if ($fg -ne $p.MainWindowHandle) { Write-Output "focus-failed"; exit 2 }
$sh = New-Object -ComObject WScript.Shell
foreach ($k in $Keys) { $sh.SendKeys($k); Start-Sleep -Milliseconds 700 }
Write-Output ("keys-sent pid={0}" -f $p.Id)
