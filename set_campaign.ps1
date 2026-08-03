<#
.SYNOPSIS
  Retarget the ad's on-screen offer and voiceover in 1.LTX_2.3_Workflow.json.

.EXAMPLE
  .\set_campaign.ps1 -Percent 10 -Product VEGETABLES -Store "grocery store" -Spoken "ten percent off on all vegetables"

.EXAMPLE
  .\set_campaign.ps1 -Percent 20 -Product TOYS
#>
param(
  [int]    $Percent = 20,
  [string] $Product = 'TOYS',
  [string] $Store   = 'toy store',
  [string] $Spoken  = '',
  [string] $Path    = "$PSScriptRoot\1.LTX_2.3_Workflow.json"
)
$ErrorActionPreference = 'Stop'

$words = @{ 5='five'; 10='ten'; 15='fifteen'; 20='twenty'; 25='twenty five'; 30='thirty'; 40='forty'; 50='fifty' }
if (-not $Spoken) {
  $n = if ($words.ContainsKey($Percent)) { $words[$Percent] } else { "$Percent" }
  $Spoken = "$n percent off on all $($Product.ToLower())"
}
$lower = $Product.ToLower()

$pos = @(
  "A vibrant, high-energy 10-second retail advertisement for a $Store, designed for a large in-store digital display. Colorful shelves filled with $lower. Bright, cheerful lighting creates a fun shopping atmosphere. The camera glides smoothly through the store while products gently animate with subtle motion."
  ""
  "A single bold animated promotional graphic is centered in the foreground. It contains exactly two lines of text and nothing else."
  "Line 1 reads: SALE"
  "Line 2 reads: $Percent% OFF ON $($Product.ToUpper())"
  "Line 1 is giant, bright red with a thick golden outline and a sparkling animation. Line 2 sits directly below line 1, smaller, clean white with a red outline. Each line appears exactly once, spelled exactly as written, grouped into one unified design. No other text anywhere in the frame."
  ""
  "Confetti, balloons and colorful ribbons fill the background behind the text."
  ""
  "Style: Modern retail commercial, premium quality, perfectly legible typography, colorful, family-friendly, cinematic lighting, smooth camera movement, vibrant colors, highly detailed, energetic motion graphics, 16:9 landscape."
  ""
  # Quoted, and last in the prompt. Unquoted the model reads the stage direction
  # aloud as well -- "Accompanied by..." was spoken as "acampass".
  "Audio: cheerful, fast-paced background music throughout, with an energetic, upbeat human male announcer reading out loud: `"The big sale is live right now! Get $Spoken!`""
) -join "`n"

$neg = @(
  'duplicated text', 'repeated text', 'second copy of the offer', 'three or more lines of text',
  'extra text', 'text outside the graphic', 'repeated numbers', "multiple ${Percent}s",
  'incorrect spelling', 'misspelled words', 'missing letters', 'gibberish text',
  'unreadable text', 'crowded text', 'cluttered layout', 'distracting backgrounds',
  'blurry visuals', 'dark lighting', 'low resolution', 'watermark', 'distorted products',
  'excessive motion blur', 'flickering', 'robotic voice', 'silent'
) -join ', '

$lines = [System.IO.File]::ReadAllText($Path) -split "`n"
$posIdx = -1; $negIdx = -1
for ($i = 0; $i -lt $lines.Count; $i++) {
  if ($lines[$i] -match '^\s+"A vibrant, high-energy') { $posIdx = $i }
  if ($lines[$i] -match '^\s+"duplicated text')        { $negIdx = $i }
}
if ($posIdx -lt 0) { throw "positive prompt line not found in $Path" }
if ($negIdx -lt 0) { throw "negative prompt line not found in $Path" }

$lines[$posIdx] = '        ' + (ConvertTo-Json $pos -Compress)
$lines[$negIdx] = '        ' + (ConvertTo-Json $neg -Compress)

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($Path, ($lines -join "`n"), $utf8NoBom)

# Fail loudly rather than leave a corrupt workflow behind.
$null = Get-Content $Path -Raw | ConvertFrom-Json
Write-Host "OK  SALE / $Percent% OFF ON $($Product.ToUpper())  |  VO: $Spoken" -ForegroundColor Green
Write-Host "    Reload $([System.IO.Path]::GetFileName($Path)) in ComfyUI." -ForegroundColor DarkGray
