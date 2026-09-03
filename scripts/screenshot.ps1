param([string]$Out = "F:\Projects\BilibiliPlayer\data\screen.png", [int]$X = -1, [int]$Y = -1, [string]$Click = "", [string]$Key = "")
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;using System.Runtime.InteropServices;
public class M {
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
# 可选动作: click / rightclick / dblclick 在 (X,Y)
if ($Click -ne "" -and $X -ge 0) {
  [M]::SetCursorPos($X, $Y) | Out-Null
  Start-Sleep -Milliseconds 150
  switch ($Click) {
    "click" { [M]::mouse_event(2,0,0,0,[UIntPtr]::Zero); [M]::mouse_event(4,0,0,0,[UIntPtr]::Zero) }
    "rightclick" { [M]::mouse_event(8,0,0,0,[UIntPtr]::Zero); [M]::mouse_event(16,0,0,0,[UIntPtr]::Zero) }
    "dblclick" { [M]::mouse_event(2,0,0,0,[UIntPtr]::Zero); [M]::mouse_event(4,0,0,0,[UIntPtr]::Zero); Start-Sleep -Milliseconds 80; [M]::mouse_event(2,0,0,0,[UIntPtr]::Zero); [M]::mouse_event(4,0,0,0,[UIntPtr]::Zero) }
  }
  Start-Sleep -Milliseconds 500
}
if ($Key -ne "") {
  $sh = New-Object -ComObject WScript.Shell
  $sh.SendKeys($Key)
  Start-Sleep -Milliseconds 600
}
$b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen(0, 0, 0, 0, $bmp.Size)
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Output "saved $Out ($($b.Width)x$($b.Height))"
