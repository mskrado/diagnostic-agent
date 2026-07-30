<#
.SYNOPSIS
  Generate release notes from PRs merged into devel since the previous release tag.

.DESCRIPTION
  Windows-friendly wrapper around scripts/generate-release-notes.sh.
  Prefers Git Bash when available; otherwise runs an equivalent PowerShell
  implementation using gh + ConvertFrom-Json.

.PARAMETER SinceTag
  Previous release tag (e.g. v1.1.2). Default: latest v* tag / latest GitHub release.

.PARAMETER UntilTag
  Optional upper bound tag (inclusive by commit date). Default: now.

.PARAMETER CurrentTag
  Tag used in the Full Changelog compare URL. Default: UntilTag or HEAD.

.PARAMETER Base
  Integration branch PRs are merged into. Default: devel.

.PARAMETER OutFile
  Write markdown to this path instead of stdout.

.PARAMETER AllowEmpty
  Do not fail when the range has zero in-scope PRs.
#>
[CmdletBinding()]
param(
  [string]$SinceTag = "",
  [string]$UntilTag = "",
  [string]$CurrentTag = "",
  [string]$Base = "devel",
  [string]$OutFile = "",
  [switch]$AllowEmpty
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$BashScript = Join-Path $ScriptDir "generate-release-notes.sh"

function Find-Bash {
  $candidates = @(
    "bash",
    "$env:ProgramFiles\Git\bin\bash.exe",
    "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
    "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
  )
  foreach ($c in $candidates) {
    if ($c -eq "bash") {
      $cmd = Get-Command bash -ErrorAction SilentlyContinue
      if ($cmd) { return $cmd.Source }
    } elseif (Test-Path $c) {
      return $c
    }
  }
  return $null
}

$bash = Find-Bash
if ($bash -and (Test-Path $BashScript)) {
  $argsList = @()
  if ($SinceTag) { $argsList += @("--since-tag", $SinceTag) }
  if ($UntilTag) { $argsList += @("--until-tag", $UntilTag) }
  if ($CurrentTag) { $argsList += @("--current-tag", $CurrentTag) }
  if ($Base) { $argsList += @("--base", $Base) }
  if ($OutFile) { $argsList += @("--out", $OutFile) }
  if ($AllowEmpty) { $argsList += "--allow-empty" }

  # Ensure jq exists for the bash path; fall through to native if missing.
  $jq = Get-Command jq -ErrorAction SilentlyContinue
  if ($jq) {
    Push-Location $RepoRoot
    try {
      & $bash $BashScript @argsList
      if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
      return
    } finally {
      Pop-Location
    }
  }
}

# ── Native PowerShell fallback (no bash/jq) ─────────────────────────
# Keep aligned with .github/release.yml / generate-release-notes.sh
$categories = @(
  @{ Title = "Breaking Changes"; Labels = @("breaking-change", "type:breaking") },
  @{ Title = "Features"; Labels = @("type:feature", "feature", "enhancement") },
  @{ Title = "Bug Fixes"; Labels = @("type:bug", "bug", "fix") },
  @{ Title = "Tech Debt & Refactoring"; Labels = @("type:tech-debt", "refactor") },
  @{ Title = "Documentation"; Labels = @("type:docs", "documentation") },
  @{ Title = "Install & CI"; Labels = @("area:ci", "area:install", "ci", "infra") },
  @{ Title = "Other Changes"; Labels = @("*") }
)

$excludeLabels = @("ignore-for-release", "duplicate", "invalid", "wontfix")
$excludeAuthors = @("dependabot", "dependabot[bot]", "github-actions", "github-actions[bot]")

if (-not $SinceTag) {
  $SinceTag = gh release list --limit 1 --json tagName -q ".[0].tagName" 2>$null
  if (-not $SinceTag) { throw "No previous release tag found; pass -SinceTag vX.Y.Z" }
}

function Get-TagDate([string]$tag) {
  $d = git -C $RepoRoot log -1 --format=%cI $tag 2>$null
  if (-not $d) {
    $d = gh release view $tag --json publishedAt -q .publishedAt
  }
  if (-not $d) { throw "Could not resolve date for tag $tag" }
  return [datetimeoffset]::Parse($d)
}

$sinceDate = Get-TagDate $SinceTag
$untilDate = if ($UntilTag) { Get-TagDate $UntilTag } else { [datetimeoffset]::UtcNow }
if (-not $CurrentTag) {
  $CurrentTag = if ($UntilTag) { $UntilTag } else { "HEAD" }
}

$prs = gh pr list --base $Base --state merged --limit 200 `
  --json number,title,url,mergedAt,author,labels | ConvertFrom-Json

$filtered = @()
foreach ($pr in $prs) {
  $merged = [datetimeoffset]::Parse($pr.mergedAt)
  if ($merged -le $sinceDate -or $merged -gt $untilDate) { continue }
  $login = $pr.author.login
  if ($excludeAuthors -contains $login) { continue }
  $names = @($pr.labels | ForEach-Object { $_.name })
  if ($names | Where-Object { $excludeLabels -contains $_ }) { continue }
  if ($pr.title -match '^(?i)Devel$') { continue }
  if ($pr.title -match '^(?i)docs\(changelog\)') { continue }
  if ($pr.title -match '^(?i)Release:') { continue }
  $filtered += $pr
}
$filtered = @($filtered | Sort-Object { [datetimeoffset]::Parse($_.mergedAt) })

if ($filtered.Count -eq 0 -and -not $AllowEmpty) {
  throw "No in-scope PRs merged into $Base between $SinceTag and $untilDate. Pass -AllowEmpty if intentional."
}

$sections = @{}
foreach ($cat in $categories) { $sections[$cat.Title] = New-Object System.Collections.Generic.List[object] }
$claimed = @{}

foreach ($pr in $filtered) {
  if ($claimed.ContainsKey($pr.number)) { continue }
  $names = @($pr.labels | ForEach-Object { $_.name })
  $placed = $false
  foreach ($cat in $categories) {
    $match = ($cat.Labels -contains "*") -or ($names | Where-Object { $cat.Labels -contains $_ })
    if ($match) {
      $sections[$cat.Title].Add($pr) | Out-Null
      $claimed[$pr.number] = $true
      $placed = $true
      break
    }
  }
  if (-not $placed) {
    $sections["Other Changes"].Add($pr) | Out-Null
  }
}

$repo = gh repo view --json nameWithOwner -q .nameWithOwner
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("## What's Changed")
[void]$sb.AppendLine("")
foreach ($cat in $categories) {
  $items = $sections[$cat.Title]
  if ($items.Count -eq 0) { continue }
  [void]$sb.AppendLine("### $($cat.Title)")
  [void]$sb.AppendLine("")
  foreach ($pr in $items) {
    [void]$sb.AppendLine("* $($pr.title) (#$($pr.number)) by @$($pr.author.login) in $($pr.url)")
  }
  [void]$sb.AppendLine("")
}
if ($filtered.Count -eq 0) {
  [void]$sb.AppendLine("_No in-scope pull requests in this range._")
  [void]$sb.AppendLine("")
}
[void]$sb.AppendLine("**Full Changelog**: https://github.com/$repo/compare/$SinceTag...$CurrentTag")

$text = $sb.ToString().TrimEnd() + "`n"
if ($OutFile) {
  $full = if ([System.IO.Path]::IsPathRooted($OutFile)) { $OutFile } else { Join-Path (Get-Location) $OutFile }
  $dir = Split-Path -Parent $full
  if ($dir -and -not (Test-Path $dir)) {
    New-Item -ItemType Directory -Path $dir | Out-Null
  }
  [System.IO.File]::WriteAllText($full, $text)
  Write-Host "Wrote $full ($($filtered.Count) PR(s))"
} else {
  Write-Output $text
}
