param(
    [string]$OutputDir = "images\member3"
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
New-Item -ItemType Directory -Force $OutputDir | Out-Null

function New-Canvas {
    param([int]$Width, [int]$Height)
    $bitmap = New-Object System.Drawing.Bitmap($Width, $Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit
    $graphics.Clear([System.Drawing.Color]::White)
    return @{ Bitmap = $bitmap; Graphics = $graphics }
}

function New-Font {
    param(
        [float]$Size,
        [System.Drawing.FontStyle]$Style = [System.Drawing.FontStyle]::Regular
    )
    foreach ($fontName in @("Microsoft YaHei", "Microsoft YaHei UI", "Segoe UI", "Arial")) {
        try {
            return New-Object System.Drawing.Font($fontName, $Size, $Style, [System.Drawing.GraphicsUnit]::Pixel)
        }
        catch {}
    }
    throw "No usable font was found."
}

function Draw-RoundedRectangle {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Pen]$Pen,
        [System.Drawing.RectangleF]$Rectangle,
        [float]$Radius = 18
    )
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $diameter = $Radius * 2
    $path.AddArc($Rectangle.X, $Rectangle.Y, $diameter, $diameter, 180, 90)
    $path.AddArc($Rectangle.Right - $diameter, $Rectangle.Y, $diameter, $diameter, 270, 90)
    $path.AddArc($Rectangle.Right - $diameter, $Rectangle.Bottom - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($Rectangle.X, $Rectangle.Bottom - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    $Graphics.DrawPath($Pen, $path)
    $path.Dispose()
}

function Draw-CenteredText {
    param(
        [System.Drawing.Graphics]$Graphics,
        [string]$Text,
        [System.Drawing.Font]$Font,
        [System.Drawing.Brush]$Brush,
        [System.Drawing.RectangleF]$Rectangle
    )
    $format = New-Object System.Drawing.StringFormat
    $format.Alignment = [System.Drawing.StringAlignment]::Center
    $format.LineAlignment = [System.Drawing.StringAlignment]::Center
    $format.Trimming = [System.Drawing.StringTrimming]::EllipsisWord
    $Graphics.DrawString($Text, $Font, $Brush, $Rectangle, $format)
    $format.Dispose()
}

function Draw-Arrow {
    param(
        [System.Drawing.Graphics]$Graphics,
        [System.Drawing.Pen]$Pen,
        [float]$X1,
        [float]$Y1,
        [float]$X2,
        [float]$Y2
    )

    # Use a short-lived Pen so no CustomEndCap state needs to be cleared.
    # ArrowAnchor is supported by Windows PowerShell 5.1 / System.Drawing.
    $localPen = [System.Drawing.Pen]::new(
        $Pen.Color,
        [single]$Pen.Width
    )

    try {
        $localPen.StartCap = [System.Drawing.Drawing2D.LineCap]::Flat
        $localPen.EndCap = [System.Drawing.Drawing2D.LineCap]::ArrowAnchor
        $Graphics.DrawLine($localPen, $X1, $Y1, $X2, $Y2)
    }
    finally {
        $localPen.Dispose()
    }
}

function Save-EffectLifecycle {
    param([string]$Path)
    $canvas = New-Canvas -Width 1800 -Height 1000
    $bitmap = $canvas.Bitmap
    $graphics = $canvas.Graphics

    $titleFont = New-Font -Size 42 -Style Bold
    $nodeFont = New-Font -Size 25 -Style Bold
    $bodyFont = New-Font -Size 20
    $smallFont = New-Font -Size 17

    $textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(32, 45, 70))
    $mutedBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(80, 95, 120))
    $accentBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(232, 242, 255))
    $borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 95, 165), 4)
    $arrowPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 95, 165), 4)

    Draw-CenteredText -Graphics $graphics -Text "Unified Effect Lifecycle" -Font $titleFont -Brush $textBrush -Rectangle ([System.Drawing.RectangleF]::new(0, 25, 1800, 80))

    $stagingRect = [System.Drawing.RectangleF]::new(80, 350, 360, 210)
    $pendingRect = [System.Drawing.RectangleF]::new(580, 375, 310, 160)

    foreach ($rect in @($stagingRect, $pendingRect)) {
        $graphics.FillRectangle($accentBrush, $rect)
        Draw-RoundedRectangle -Graphics $graphics -Pen $borderPen -Rectangle $rect
    }

    Draw-CenteredText -Graphics $graphics -Text "Resource Staging`nFile Pending`nMemory Pending`nDownload Quarantine" -Font $bodyFont -Brush $textBrush -Rectangle $stagingRect
    Draw-CenteredText -Graphics $graphics -Text "EffectRecord`nPENDING" -Font $nodeFont -Brush $textBrush -Rectangle $pendingRect
    Draw-Arrow -Graphics $graphics -Pen $arrowPen -X1 440 -Y1 455 -X2 580 -Y2 455
    Draw-CenteredText -Graphics $graphics -Text "register" -Font $smallFont -Brush $mutedBrush -Rectangle ([System.Drawing.RectangleF]::new(445, 410, 130, 35))

    $states = @(
        @{ X = 1120; Y = 135; Text = "COMMITTED`nChecks passed and committed" },
        @{ X = 1120; Y = 335; Text = "REJECTED`nRejected by PostCheck" },
        @{ X = 1120; Y = 535; Text = "CLEANED`nPending side effect cleaned" },
        @{ X = 1120; Y = 735; Text = "ROLLED_BACK`nTarget effect restored" }
    )

    foreach ($state in $states) {
        $rect = [System.Drawing.RectangleF]::new($state.X, $state.Y, 430, 130)
        $graphics.FillRectangle($accentBrush, $rect)
        Draw-RoundedRectangle -Graphics $graphics -Pen $borderPen -Rectangle $rect
        Draw-CenteredText -Graphics $graphics -Text $state.Text -Font $bodyFont -Brush $textBrush -Rectangle $rect
        Draw-Arrow -Graphics $graphics -Pen $arrowPen -X1 890 -Y1 455 -X2 1120 -Y2 ($state.Y + 65)
    }

    Draw-CenteredText -Graphics $graphics -Text "Update EffectStatus only after the real resource operation succeeds." -Font $bodyFont -Brush $mutedBrush -Rectangle ([System.Drawing.RectangleF]::new(200, 900, 1400, 50))
    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)

    foreach ($item in @($titleFont, $nodeFont, $bodyFont, $smallFont, $textBrush, $mutedBrush, $accentBrush, $borderPen, $arrowPen, $graphics, $bitmap)) { $item.Dispose() }
}

function Save-SelectiveRollback {
    param([string]$Path)
    $canvas = New-Canvas -Width 1900 -Height 1050
    $bitmap = $canvas.Bitmap
    $graphics = $canvas.Graphics

    $titleFont = New-Font -Size 42 -Style Bold
    $sectionFont = New-Font -Size 31 -Style Bold
    $nodeFont = New-Font -Size 22 -Style Bold
    $bodyFont = New-Font -Size 19

    $textBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(32, 45, 70))
    $mutedBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(80, 95, 120))
    $accentBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(232, 242, 255))
    $borderPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 95, 165), 4)
    $arrowPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(45, 95, 165), 4)

    Draw-CenteredText -Graphics $graphics -Text "Selective Rollback: Before and After" -Font $titleFont -Brush $textBrush -Rectangle ([System.Drawing.RectangleF]::new(0, 20, 1900, 80))
    Draw-CenteredText -Graphics $graphics -Text "Before rollback" -Font $sectionFont -Brush $textBrush -Rectangle ([System.Drawing.RectangleF]::new(50, 120, 480, 60))
    Draw-CenteredText -Graphics $graphics -Text "RollbackPlan" -Font $sectionFont -Brush $textBrush -Rectangle ([System.Drawing.RectangleF]::new(690, 120, 520, 60))
    Draw-CenteredText -Graphics $graphics -Text "After rollback" -Font $sectionFont -Brush $textBrush -Rectangle ([System.Drawing.RectangleF]::new(1370, 120, 480, 60))

    $beforeNodes = @(
        @{ Y = 245; Text = "Effect A`nfile:a.txt`nCOMMITTED" },
        @{ Y = 485; Text = "Effect B`nfile:b.txt`nCOMMITTED" },
        @{ Y = 725; Text = "Effect C`nmemory:key`nCOMMITTED" }
    )
    $afterNodes = @(
        @{ Y = 245; Text = "Effect A`nfile:a.txt`nCOMMITTED" },
        @{ Y = 485; Text = "Effect B`nfile:b.txt`nROLLED_BACK" },
        @{ Y = 725; Text = "Effect C`nmemory:key`nCOMMITTED" }
    )

    foreach ($node in $beforeNodes) {
        $rect = [System.Drawing.RectangleF]::new(90, $node.Y, 410, 155)
        $graphics.FillRectangle($accentBrush, $rect)
        Draw-RoundedRectangle -Graphics $graphics -Pen $borderPen -Rectangle $rect
        Draw-CenteredText -Graphics $graphics -Text $node.Text -Font $nodeFont -Brush $textBrush -Rectangle $rect
    }
    foreach ($node in $afterNodes) {
        $rect = [System.Drawing.RectangleF]::new(1400, $node.Y, 410, 155)
        $graphics.FillRectangle($accentBrush, $rect)
        Draw-RoundedRectangle -Graphics $graphics -Pen $borderPen -Rectangle $rect
        Draw-CenteredText -Graphics $graphics -Text $node.Text -Font $nodeFont -Brush $textBrush -Rectangle $rect
    }

    $planRect = [System.Drawing.RectangleF]::new(680, 390, 540, 300)
    $graphics.FillRectangle($accentBrush, $planRect)
    Draw-RoundedRectangle -Graphics $graphics -Pen $borderPen -Rectangle $planRect
    Draw-CenteredText -Graphics $graphics -Text "plan_id: plan-b`ntask_id: task-1`neffect_ids: [Effect B]`nrequest_ids: [request-b]`nreason: PostCheck failure" -Font $bodyFont -Brush $textBrush -Rectangle $planRect

    Draw-Arrow -Graphics $graphics -Pen $arrowPen -X1 500 -Y1 565 -X2 680 -Y2 540
    Draw-Arrow -Graphics $graphics -Pen $arrowPen -X1 1220 -Y1 540 -X2 1400 -Y2 565
    Draw-CenteredText -Graphics $graphics -Text "Explicit scope" -Font $bodyFont -Brush $mutedBrush -Rectangle ([System.Drawing.RectangleF]::new(510, 500, 160, 40))
    Draw-CenteredText -Graphics $graphics -Text "Execute plan" -Font $bodyFont -Brush $mutedBrush -Rectangle ([System.Drawing.RectangleF]::new(1230, 500, 160, 40))
    Draw-CenteredText -Graphics $graphics -Text "Only Effect B is rolled back; independent Effects A and C remain committed." -Font $bodyFont -Brush $mutedBrush -Rectangle ([System.Drawing.RectangleF]::new(210, 950, 1480, 50))

    $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    foreach ($item in @($titleFont, $sectionFont, $nodeFont, $bodyFont, $textBrush, $mutedBrush, $accentBrush, $borderPen, $arrowPen, $graphics, $bitmap)) { $item.Dispose() }
}

$effectPath = Join-Path $OutputDir "effect_lifecycle.png"
$rollbackPath = Join-Path $OutputDir "selective_rollback_before_after.png"
Save-EffectLifecycle -Path $effectPath
Save-SelectiveRollback -Path $rollbackPath
Write-Host "Generated:"
Write-Host "  $effectPath"
Write-Host "  $rollbackPath"
