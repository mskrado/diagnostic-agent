<#
.SYNOPSIS
  Check relative markdown links and heading anchors in the docs tree.

.DESCRIPTION
  For every markdown link that is not an external URL, verifies that the target
  file exists and, when the link carries a #fragment, that a heading in the
  target file slugifies to that fragment (GitHub rules: lowercase, drop
  characters that are not word/space/hyphen, spaces to hyphens).

  Docs-only guard: CI does not need it, but it catches the anchor drift that
  silently breaks cross-document navigation.

.EXAMPLE
  pwsh scripts/check-md-links.ps1
  pwsh scripts/check-md-links.ps1 -Path docs/WORKSPACE.md
#>
[CmdletBinding()]
param(
    # Files or directories to check. Defaults to the whole repository.
    [string[]]$Path = @('.')
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

function Get-Slug {
    param([string]$Heading)
    $s = $Heading.Trim().ToLowerInvariant()
    $s = $s -replace '`', ''
    $s = $s -replace '\[([^\]]*)\]\([^)]*\)', '$1'
    $s = $s -replace '[^\p{L}\p{Nd} _-]', ''
    $s = $s -replace ' ', '-'
    return $s
}

$anchorCache = @{}
function Get-Anchors {
    param([string]$File)
    if ($anchorCache.ContainsKey($File)) { return $anchorCache[$File] }
    $anchors = [System.Collections.Generic.HashSet[string]]::new()
    $inFence = $false
    # Explicit UTF-8: Windows PowerShell 5.1 otherwise reads the ANSI codepage
    # and mangles em dashes / math symbols inside headings.
    foreach ($line in Get-Content -LiteralPath $File -Encoding UTF8) {
        if ($line -match '^\s*(```|~~~)') { $inFence = -not $inFence; continue }
        if ($inFence) { continue }
        if ($line -match '^(#{1,6})\s+(.*)$') {
            [void]$anchors.Add((Get-Slug $Matches[2]))
        }
        foreach ($m in [regex]::Matches($line, '<a\s+(?:id|name)="([^"]+)"')) {
            [void]$anchors.Add($m.Groups[1].Value)
        }
    }
    $anchorCache[$File] = $anchors
    return $anchors
}

$targets = foreach ($p in $Path) {
    $full = Resolve-Path -LiteralPath $p
    if (Test-Path -LiteralPath $full -PathType Container) {
        Get-ChildItem -LiteralPath $full -Recurse -Filter *.md -File |
            Where-Object { $_.FullName -notmatch '\\(\.venv|node_modules|\.git)\\' }
    } else {
        Get-Item -LiteralPath $full
    }
}

$problems = New-Object System.Collections.Generic.List[string]
$checked = 0

foreach ($file in $targets) {
    $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8
    # Strip fenced code blocks so example snippets are not linted.
    $text = [regex]::Replace($text, '(?ms)^(```|~~~).*?^\1\s*$', '')
    foreach ($m in [regex]::Matches($text, '(?<!\!)\[[^\]]*\]\(([^)\s]+)\)')) {
        $link = $m.Groups[1].Value
        if ($link -match '^(https?:|mailto:)') { continue }
        $checked++
        $targetPath, $fragment = $link -split '#', 2
        if ([string]::IsNullOrEmpty($targetPath)) {
            $resolved = $file.FullName
        } else {
            $resolved = Join-Path $file.DirectoryName $targetPath
            if (-not (Test-Path -LiteralPath $resolved)) {
                $rel = $file.FullName.Substring($repoRoot.Length + 1)
                $problems.Add("$rel -> $link (missing file)")
                continue
            }
            $resolved = (Resolve-Path -LiteralPath $resolved).Path
        }
        if ($fragment -and $resolved -like '*.md') {
            if (-not (Get-Anchors $resolved).Contains($fragment.ToLowerInvariant())) {
                $rel = $file.FullName.Substring($repoRoot.Length + 1)
                $problems.Add("$rel -> $link (missing anchor)")
            }
        }
    }
}

Write-Host "Checked $checked relative links across $($targets.Count) files."
if ($problems.Count -gt 0) {
    Write-Host "`nProblems:" -ForegroundColor Red
    $problems | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}
Write-Host 'All relative links and anchors resolve.' -ForegroundColor Green
