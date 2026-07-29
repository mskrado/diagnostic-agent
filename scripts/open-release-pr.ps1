<#
.SYNOPSIS
  Open a Release Checklist issue and a devel→main PR with a generated inventory.

.DESCRIPTION
  Builds release notes from PRs merged into devel since the previous tag, opens
  (or reuses) a Release Checklist issue, and creates a draft PR base=main
  head=devel whose body is that inventory. Never uses the bare title "Devel".

.PARAMETER SinceTag
  Previous release tag. Default: latest GitHub release tag.

.PARAMETER VersionHint
  Optional version string for titles (e.g. 1.1.3). If omitted, uses today's date.

.PARAMETER Draft
  Create the PR as a draft (default: true).
#>
[CmdletBinding()]
param(
  [string]$SinceTag = "",
  [string]$VersionHint = "",
  [bool]$Draft = $true
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
$NotesScript = Join-Path $ScriptDir "generate-release-notes.ps1"

Push-Location $RepoRoot
try {
  if (-not $SinceTag) {
    $SinceTag = gh release list --limit 1 --json tagName -q ".[0].tagName"
    if (-not $SinceTag) { throw "No previous release tag; pass -SinceTag vX.Y.Z" }
  }

  $label = if ($VersionHint) { "v$($VersionHint.TrimStart('v'))" } else { (Get-Date -Format "yyyy-MM-dd") }
  $notesFile = Join-Path $env:TEMP "diagnostic-agent-release-notes-$label.md"

  & $NotesScript -SinceTag $SinceTag -OutFile $notesFile
  $inventory = Get-Content -Raw -Path $notesFile

  $issueBody = @"
**Target version**: $label
**Release manager**: @$((gh api user -q .login))
**Release PR**: (creating…)

## Scope

Inventory of PRs merged into ``devel`` since ``$SinceTag``:

$inventory

## Pre-merge checklist
- [ ] Release notes inventory generated and pasted above
- [ ] PR title is ``Release: $label (devel → main)`` — **not** bare ``Devel``
- [ ] All in-scope child issues closed or explicitly deferred
- [ ] Breaking workspace / CLI / profile contract changes documented in ``docs/``
- [ ] ``.env.example`` updated if new ``AGENT_*`` settings
- [ ] Host pin bump noted (image tag / ``agent_version``) if hosts must upgrade
- [ ] CI green on ``devel``

## Publish
- [ ] PR merged to ``main``
- [ ] ``release.yml`` run from ``main`` (``-f bump=patch|minor|major``) **or** tag ``$label`` pushed
- [ ] ``release.yml`` succeeded (GHCR push, wheel, git tag, GitHub Release)
- [ ] ``ghcr.io/mskrado/diagnostic-agent`` tag pullable

## Follow-up
- [ ] Host repos (if any) opened issues/PRs to bump the pin
- [ ] Tech-debt / bug issues opened for anything found
- [ ] Release issue closed
"@

  $issueUrl = gh issue create `
    --title "[RELEASE] $label (devel → main)" `
    --label "release" `
    --body $issueBody
  $issueNumber = ($issueUrl -split '/')[-1]
  Write-Host "Release checklist: $issueUrl"

  $prBody = @"
## Release checklist
Closes #$issueNumber

## Summary
Production integration PR: merge ``devel`` → ``main``. Does **not** publish; run ``release.yml`` after merge.

## What's included (from devel since $SinceTag)

$inventory

## Type of change
- [x] Infra / CI (release integration)

## Spec & docs
- [x] N/A (release integration)

## Test plan
- [ ] CI green on this PR (merge gate)
- [ ] After merge: ``gh workflow run release.yml --ref main -f bump=patch``
- [ ] Confirm GHCR image / GitHub Release notes list feature PRs (not bare Devel)

## Deployment impact
- [x] Publish via subsequent ``release.yml`` (not this merge alone)
- [ ] Host repos may need image / ``agent_version`` pin bump after publish
"@

  $prBodyFile = Join-Path $env:TEMP "diagnostic-agent-release-pr-$label.md"
  [System.IO.File]::WriteAllText($prBodyFile, $prBody)

  $title = "Release: $label (devel → main)"
  $draftArgs = @()
  if ($Draft) { $draftArgs += "--draft" }

  $prUrl = gh pr create --base main --head devel `
    --title $title `
    --body-file $prBodyFile `
    @draftArgs

  Write-Host ""
  Write-Host "Draft PR: $prUrl"
  Write-Host "Issue:    $issueUrl"
  Write-Host "Notes:    $notesFile"
} finally {
  Pop-Location
}
