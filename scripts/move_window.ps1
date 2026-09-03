param([string]$TitlePart = "BilibiliPlayer", [int]$X = 0, [int]$Y = 0, [int]$W = 2540, [int]$H = 1510)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinMover {
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
"@
$p = Get-Process | Where-Object { $_.MainWindowTitle -like "*$TitlePart*" } | Select-Object -First 1
if ($p) {
  [WinMover]::MoveWindow($p.MainWindowHandle, $X, $Y, $W, $H, $true) | Out-Null
  [WinMover]::SetForegroundWindow($p.MainWindowHandle) | Out-Null
  Write-Output "moved pid=$($p.Id) title=$($p.MainWindowTitle)"
} else {
  Write-Output "not-found"
}
