param([string]$TitlePart = "BilibiliPlayer", [int]$X = 0, [int]$Y = 0, [int]$W = 1499, [int]$H = 939)
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinTop {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int X, int Y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);
}
"@
$p = Get-Process | Where-Object { $_.MainWindowTitle -like "*$TitlePart*" } | Select-Object -First 1
if (-not $p) { Write-Output "not-found"; exit 1 }
$h = $p.MainWindowHandle
[WinTop]::MoveWindow($h, $X, $Y, $W, $H, $true) | Out-Null
# TOPMOST 再 NOTOPMOST：置顶但不永久最前
[WinTop]::SetWindowPos($h, [IntPtr](-1), 0, 0, 0, 0, 0x0001 -bor 0x0002) | Out-Null
Start-Sleep -Milliseconds 300
[WinTop]::SetWindowPos($h, [IntPtr](-2), 0, 0, 0, 0, 0x0001 -bor 0x0002) | Out-Null
Write-Output "topped pid=$($p.Id)"
