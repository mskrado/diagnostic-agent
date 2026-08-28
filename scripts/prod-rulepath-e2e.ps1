# PROD rule-path E2E for a remote diagnostic-agent deployment.
# Golden copy lives in diagnostic-agent (issue #72). See docs/TESTING.md.
#
# SSH port tunnels alone cannot drive this check: rule-path uses `docker run` /
# `docker exec` against the Docker daemon on the target host. This wrapper SSHs
# there, emits a smoke marker log line, and asserts a DiagnosticAgentSmokeMarker
# audit record on the remote agent. Optionally opens local tunnels so you can
# review Grafana / Mailpit / the agent afterward.
#
# Usage (from this repo root in PowerShell):
#   .\scripts\prod-rulepath-e2e.ps1 -SshTarget ec2-user@HOST -IdentityFile $HOME\.ssh\key.pem -ContainerPrefix <your-compose-prefix>
#   .\scripts\prod-rulepath-e2e.ps1 -SshTarget ec2-user@HOST -IdentityFile $HOME\.ssh\key.pem -ContainerPrefix <your-compose-prefix> -OpenTunnels
#   # Or via env: AGENT_E2E_SSH_TARGET / AGENT_E2E_SSH_IDENTITY / AGENT_E2E_CONTAINER_PREFIX
#
# The container prefix is required: container names are host-specific, so there
# is no safe default to guess.
#
# Exit codes: 0 = PASS, 1 = FAIL / prerequisites.

param(
    [string]$SshTarget = $(if ($env:AGENT_E2E_SSH_TARGET) { $env:AGENT_E2E_SSH_TARGET } else { "" }),
    [string]$IdentityFile = $(if ($env:AGENT_E2E_SSH_IDENTITY) { $env:AGENT_E2E_SSH_IDENTITY } else { (Join-Path $HOME ".ssh\id_rsa") }),
    [string]$ContainerPrefix = $env:AGENT_E2E_CONTAINER_PREFIX,
    [string]$SmokeMarker = $(if ($env:AGENT_E2E_SMOKE_MARKER) { $env:AGENT_E2E_SMOKE_MARKER } else { "DIAGNOSTIC_AGENT_SMOKE_MARKER" }),
    [string]$AgentContainer = "",
    [string]$PromtailContainer = "",
    [string]$LokiContainer = "",
    [string]$AlertmanagerContainer = "",
    [string]$EmitterName = "",
    # Cover Alertmanager group_interval (5m) on repeat runs + ruler eval.
    [int]$AlertTimeoutSec = 420,
    [int]$PollIntervalSec = 5,
    # Open -L tunnels in a background ssh session for post-run review.
    [switch]$OpenTunnels,
    [int]$LocalAgentPort = 8001,
    [int]$LocalGrafanaPort = 3000,
    [int]$LocalMailpitPort = 8025,
    [int]$LocalAlertmanagerPort = 9093,
    [int]$RemoteAgentPort = 8001,
    [int]$RemoteGrafanaPort = 3000,
    [int]$RemoteMailpitPort = 8025,
    [int]$RemoteAlertmanagerPort = 9093
)

if (-not $ContainerPrefix) {
    throw "-ContainerPrefix is required (or set AGENT_E2E_CONTAINER_PREFIX). It is your compose project prefix, e.g. containers named <prefix>-loki, <prefix>-diagnostic-agent."
}

if (-not $AgentContainer) { $AgentContainer = "$ContainerPrefix-diagnostic-agent" }
if (-not $PromtailContainer) { $PromtailContainer = "$ContainerPrefix-promtail" }
if (-not $LokiContainer) { $LokiContainer = "$ContainerPrefix-loki" }
if (-not $AlertmanagerContainer) { $AlertmanagerContainer = "$ContainerPrefix-alertmanager" }
if (-not $EmitterName) { $EmitterName = "$ContainerPrefix-smoke-emitter" }

$ErrorActionPreference = "Stop"
$script:Passed = 0
$script:Failed = 0

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Pass([string]$msg) {
    Write-Host "  PASS: $msg" -ForegroundColor Green
    $script:Passed++
}

function Fail([string]$msg) {
    Write-Host "  FAIL: $msg" -ForegroundColor Red
    $script:Failed++
}

function Invoke-Remote([string]$RemoteBash) {
    $RemoteBash = $RemoteBash.Replace("`r", "")
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($RemoteBash))
    $sshArgs = @(
        "-i", $IdentityFile,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        $SshTarget,
        "echo $b64 | base64 -d | bash"
    )
    # Native ssh stdout is a PowerShell success-stream object. If we leave it on
    # the pipeline, `return $LASTEXITCODE` makes the caller receive every remote
    # log line + the exit code (so `$exitCode -eq 0` fails even on a real PASS).
    # Write-Host for display; return only the integer exit code.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & ssh @sshArgs 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    foreach ($line in @($output)) {
        Write-Host "  $line"
    }
    return [int]$code
}

Write-Host "Diagnostic agent PROD rule-path E2E" -ForegroundColor White
Write-Host "  Target: $(if ($SshTarget) { $SshTarget } else { '(unset)' })  prefix=$ContainerPrefix" -ForegroundColor DarkGray

Write-Step "Prerequisites (ssh + identity file + target)"
if (-not $SshTarget) {
    Fail "SshTarget required (pass -SshTarget or set AGENT_E2E_SSH_TARGET). Example: ec2-user@54.205.176.61"
    Write-Host "Summary: $script:Passed passed, $script:Failed failed"
    exit 1
}
if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Fail "ssh not found on PATH"
    Write-Host "Summary: $script:Passed passed, $script:Failed failed"
    exit 1
}
if (-not (Test-Path -LiteralPath $IdentityFile)) {
    Fail "Identity file not found: $IdentityFile"
    Write-Host "Summary: $script:Passed passed, $script:Failed failed"
    exit 1
}
Pass "ssh OK, identity file present, target set"

# Optional review tunnels (separate background session). The E2E itself always
# runs over a non-tunnel SSH command so Docker executes on the remote host.
if ($OpenTunnels) {
    Write-Step "Opening SSH tunnels for post-run review"
    $tunnelArgs = @(
        "-i", $IdentityFile,
        "-N",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-L", "${LocalAgentPort}:127.0.0.1:${RemoteAgentPort}",
        "-L", "${LocalGrafanaPort}:127.0.0.1:${RemoteGrafanaPort}",
        "-L", "${LocalMailpitPort}:127.0.0.1:${RemoteMailpitPort}",
        "-L", "${LocalAlertmanagerPort}:127.0.0.1:${RemoteAlertmanagerPort}",
        $SshTarget
    )
    $tunnelJob = Start-Process -FilePath "ssh" -ArgumentList $tunnelArgs -WindowStyle Hidden -PassThru
    Start-Sleep -Seconds 2
    if ($tunnelJob.HasExited) {
        Fail "Tunnel ssh exited early (exit $($tunnelJob.ExitCode)). Port already in use, or SSH failed."
    } else {
        Pass ("Tunnels up (pid={0}): agent=:{1} grafana=:{2} mailpit=:{3} alertmanager=:{4}" -f `
            $tunnelJob.Id, $LocalAgentPort, $LocalGrafanaPort, $LocalMailpitPort, $LocalAlertmanagerPort)
        Write-Host "  Review: http://127.0.0.1:$LocalGrafanaPort  http://127.0.0.1:$LocalMailpitPort  http://127.0.0.1:$LocalAgentPort/health" -ForegroundColor DarkGray
        Write-Host "  Stop tunnels later: Stop-Process -Id $($tunnelJob.Id)" -ForegroundColor DarkGray
    }
}

Write-Step "Rule path on remote host: sentinel -> Loki ruler -> Alertmanager -> agent"

# Single-quoted template so PowerShell does not expand bash `$vars`. Placeholders
# are substituted below (same approach as smoke-test.ps1).
$remote = @'
set -euo pipefail
MARKER='__MARKER__'
AGENT='__AGENT__'
EMITTER='__EMITTER__'
PROMTAIL='__PROMTAIL__'
LOKI='__LOKI__'
AM='__AM__'
TIMEOUT=__TIMEOUT__
POLL=__POLL__

for c in "$PROMTAIL" "$LOKI" "$AM" "$AGENT"; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "FAIL: container $c not running on remote host"
    exit 1
  fi
done
echo "PASS: promtail, loki, alertmanager, agent running"

DAY=$(date -u +%F)
AUDIT="/app/audit/diagnostics-$DAY.jsonl"
BEFORE=$(docker exec "$AGENT" sh -c "wc -l < $AUDIT 2>/dev/null || echo 0" | tr -d '[:space:]')
echo "baseline_audit_lines=$BEFORE"

docker rm -f "$EMITTER" >/dev/null 2>&1 || true

# Emitter script is itself base64-encoded so nested quotes stay sane.
EMIT_B64='__EMIT_B64__'
docker run -d --name "$EMITTER" alpine sh -c "echo $EMIT_B64 | base64 -d | sh" >/dev/null
echo "PASS: emitter started ($EMITTER)"

cleanup() { docker rm -f "$EMITTER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

deadline=$(( $(date +%s) + TIMEOUT ))
while [ $(date +%s) -lt $deadline ]; do
  NOW=$(docker exec "$AGENT" sh -c "wc -l < $AUDIT 2>/dev/null || echo 0" | tr -d '[:space:]')
  if [ "$NOW" -gt "$BEFORE" ]; then
    NEW=$((NOW - BEFORE))
    if docker exec "$AGENT" tail -n "$NEW" "$AUDIT" | grep -q DiagnosticAgentSmokeMarker; then
      echo "PASS: DiagnosticAgentSmokeMarker audit record written"
      docker exec "$AGENT" tail -n "$NEW" "$AUDIT" | grep DiagnosticAgentSmokeMarker
      exit 0
    fi
  fi
  left=$(( deadline - $(date +%s) ))
  echo "... waiting for DiagnosticAgentSmokeMarker audit record (${left}s left)"
  sleep "$POLL"
done

echo "FAIL: No DiagnosticAgentSmokeMarker audit record within ${TIMEOUT}s"
echo "Check: docker exec $LOKI wget -qO- http://localhost:3100/loki/api/v1/rules"
echo "Check: docker exec $AM wget -qO- http://localhost:9093/api/v2/alerts"
echo "Check: docker logs $AGENT --tail 40"
exit 1
'@

$emitScript = @"
i=0
while [ `$i -lt 20 ]; do
  echo "{\"@timestamp\":\"`$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"level\":\"WARN\",\"service\":\"platform-service\",\"message\":\"$SmokeMarker diagnostic-agent rule-path smoke\"}"
  i=`$((i+1))
  sleep 2
done
"@
$emitScript = $emitScript.Replace("`r", "")
$emitB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($emitScript))

$remote = $remote.Replace('__MARKER__', $SmokeMarker)
$remote = $remote.Replace('__AGENT__', $AgentContainer)
$remote = $remote.Replace('__EMITTER__', $EmitterName)
$remote = $remote.Replace('__PROMTAIL__', $PromtailContainer)
$remote = $remote.Replace('__LOKI__', $LokiContainer)
$remote = $remote.Replace('__AM__', $AlertmanagerContainer)
$remote = $remote.Replace('__TIMEOUT__', [string]$AlertTimeoutSec)
$remote = $remote.Replace('__POLL__', [string]$PollIntervalSec)
$remote = $remote.Replace('__EMIT_B64__', $emitB64)

Write-Host "  Emitting sentinel '$SmokeMarker' on remote host (timeout ${AlertTimeoutSec}s) ..." -ForegroundColor DarkGray
$exitCode = Invoke-Remote $remote
if ($exitCode -eq 0) {
    Pass "Remote rule-path E2E completed"
} else {
    Fail "Remote rule-path E2E failed (remote exit $exitCode)"
    Write-Host "  Tip: if Loki/Alertmanager configs changed recently, restart them on the host:" -ForegroundColor Yellow
    Write-Host "    ssh -i `"$IdentityFile`" $SshTarget `"docker restart $LokiContainer $AlertmanagerContainer`"" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Summary: $script:Passed passed, $script:Failed failed"
if ($script:Failed -gt 0) { exit 1 }
exit 0
