# Generates build/icon.ico — a multi-resolution Windows icon for RelicToEpub.
# Output: build/icon.ico (sizes 16, 32, 48, 64, 128, 256 px)

Add-Type -AssemblyName System.Drawing

$out = Join-Path $PSScriptRoot "icon.ico"
$sizes = @(16, 32, 48, 64, 128, 256)

# --- Master image: 256×256 with "R" on slate gradient + accent circle ---
$master = New-Object System.Drawing.Bitmap(256, 256)
$g = [System.Drawing.Graphics]::FromImage($master)
$g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
$g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

$rect = New-Object System.Drawing.Rectangle(0, 0, 256, 256)
$bg = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
    $rect,
    [System.Drawing.Color]::FromArgb(255, 30, 41, 59),    # slate-800
    [System.Drawing.Color]::FromArgb(255, 71, 85, 105),   # slate-600
    45.0
)
$g.FillRectangle($bg, $rect)

$accent = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 56, 189, 248))
$g.FillEllipse($accent, 170, 170, 100, 100)

$font = New-Object System.Drawing.Font('Segoe UI', 130, [System.Drawing.FontStyle]::Bold)
$textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::White)
$format = New-Object System.Drawing.StringFormat
$format.Alignment = [System.Drawing.StringAlignment]::Center
$format.LineAlignment = [System.Drawing.StringAlignment]::Center
$rectF = New-Object System.Drawing.RectangleF(20, 10, 216, 220)
$g.DrawString('R', $font, $textBrush, $rectF, $format)

# --- Per-size images ---
$pngBySize = @{}
foreach ($s in $sizes) {
    $bmp = New-Object System.Drawing.Bitmap($s, $s)
    $g2 = [System.Drawing.Graphics]::FromImage($bmp)
    $g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g2.DrawImage($master, 0, 0, $s, $s)
    $tempPath = [System.IO.Path]::Combine($env:TEMP, "relictoepub_icon_${s}.png")
    $bmp.Save($tempPath, [System.Drawing.Imaging.ImageFormat]::Png)
    $pngBySize[$s] = $tempPath
    $g2.Dispose()
    $bmp.Dispose()
}

# --- Write ICO container ---
$fs = [System.IO.File]::Create($out)
$writer = New-Object System.IO.BinaryWriter($fs)
$writer.Write([UInt16]0)               # reserved
$writer.Write([UInt16]1)               # type = icon
$writer.Write([UInt16]$sizes.Count)   # count

$directorySize = 6 + 16 * $sizes.Count
$offset = $directorySize
$pngInfo = @()
foreach ($s in $sizes) {
    $size = (Get-Item $pngBySize[$s]).Length
    $pngInfo += [pscustomobject]@{ Size = $s; Path = $pngBySize[$s]; Bytes = $size; Offset = $offset }
    $offset += $size
}

foreach ($info in $pngInfo) {
    $bw = if ($info.Size -ge 256) { 0 } else { $info.Size }
    $writer.Write([byte]$bw)
    $writer.Write([byte]$bw)
    $writer.Write([byte]0)
    $writer.Write([byte]0)
    $writer.Write([UInt16]1)
    $writer.Write([UInt16]32)
    $writer.Write([UInt32]$info.Bytes)
    $writer.Write([UInt32]$info.Offset)
}
foreach ($info in $pngInfo) {
    $bytes = [System.IO.File]::ReadAllBytes($info.Path)
    $writer.Write($bytes)
    Remove-Item $info.Path -Force
}

$writer.Flush()
$fs.Close()
$font.Dispose()
$g.Dispose()
$master.Dispose()

Write-Host "Icona creata: $out ($((Get-Item $out).Length) bytes, $($sizes -join ', ') px)"
