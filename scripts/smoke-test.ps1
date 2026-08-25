# End-to-end smoke test for a deployed diagnostic-agent (host DEV stack).
# Golden copy lives in diagnostic-agent (issue #72). See docs/TESTING.md.
#
# Usage (from this repo root in PowerShell — do NOT paste into the shell from chat/docs):
#   .\scripts\smoke-test.ps1 -ContainerPrefix publishi
#   .\scripts\smoke-test.ps1 -ContainerPrefix publishi -RealPath
#   .\scripts\smoke-test.ps1 -ContainerPrefix publishi -DirectAgent
#   .\scripts\smoke-test.ps1 -ContainerPrefix publishi -RulePath
#   .\scripts\smoke-test.ps1 -SkipGrafana -SkipMailpit
# Git Bash / WSL: ./scripts/smoke-test.sh (delegates here)
#
# Prerequisites (host observability + agent containers running):
#   - Agent listening at -AgentUrl (default http://localhost:8001)
#   - Prometheus / Loki / Grafana containers named <prefix>-*
#   - For default (non -DirectAgent) path: Alertmanager + Mailpit
#   - Optional: DIAGNOSTIC_AGENT_GRAFANA_TOKEN or AGENT_GRAFANA_TOKEN for annotations
#   - Optional host .env via -EnvFile (defaults: cwd/.env then parent of cwd)

param(
    [string]$ContainerPrefix = $(if ($env:AGENT_E2E_CONTAINER_PREFIX) { $env:AGENT_E2E_CONTAINER_PREFIX } else { "publishi" }),
    [string]$AgentUrl = "http://localhost:8001",
    [string]$AlertmanagerUrl = "http://localhost:9093",
    [string]$MailpitUrl = "http://localhost:8025",
    [string]$AgentContainer = "",
    [string]$GrafanaContainer = "",
    [string]$AlertmanagerContainer = "",
    [string]$MailpitContainer = "",
    [string]$PrometheusContainer = "",
    [string]$FaultService = "platform-service",
    [string]$FaultContainer = "",
    [string]$SmokeAlertName = "DiagnosticAgentSmokeTest",
    [switch]$RealPath,
    [switch]$SkipGrafana,
    [switch]$SkipMailpit,
    # POST /alert directly (diagnostic email only - skips Alertmanager alert email).
    [switch]$DirectAgent,
    # Exercise the rule-evaluation leg: emit a sentinel log line that trips the
    # DiagnosticAgentSmokeMarker Loki rule -> Alertmanager -> agent (no dependency broken).
    [switch]$RulePath,
    [string]$LokiContainer = "",
    [string]$PromtailContainer = "",
    [string]$SmokeMarker = "PUBLISHI_SMOKE_MARKER",
    [string]$EmitterName = "",
    [string]$EnvFile = "",
    [string]$StackHint = "",
    [int]$RealPathTimeoutSec = 180,
    [int]$AlertTimeoutSec = 300
)

if (-not $AgentContainer) { $AgentContainer = "$ContainerPrefix-diagnostic-agent" }
if (-not $GrafanaContainer) { $GrafanaContainer = "$ContainerPrefix-grafana" }
if (-not $AlertmanagerContainer) { $AlertmanagerContainer = "$ContainerPrefix-alertmanager" }
if (-not $MailpitContainer) { $MailpitContainer = "$ContainerPrefix-mailpit" }
if (-not $PrometheusContainer) { $PrometheusContainer = "$ContainerPrefix-prometheus" }
if (-not $FaultContainer) { $FaultContainer = "$ContainerPrefix-$FaultService" }
if (-not $LokiContainer) { $LokiContainer = "$ContainerPrefix-loki" }
if (-not $PromtailContainer) { $PromtailContainer = "$ContainerPrefix-promtail" }
if (-not $EmitterName) { $EmitterName = "$ContainerPrefix-smoke-emitter" }

# Observability containers the agent queries during /alert (must be running).
$RequiredObservabilityContainers = @(
    $PrometheusContainer,
    $LokiContainer,
    $GrafanaContainer
)

# Required for the default Alertmanager path (alert email + agent webhook).
$RequiredAlertPipelineContainers = @(
    $MailpitContainer,
    $AlertmanagerContainer
)

$ErrorActionPreference = "Stop"
$Passed = 0
$Failed = 0

function Write-Step($Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Pass($Message) {
    Write-Host "  PASS: $Message" -ForegroundColor Green
    $script:Passed++
}

function Fail($Message) {
    Write-Host "  FAIL: $Message" -ForegroundColor Red
    $script:Failed++
}

function Write-StackHint {
    Write-Host "  Start the host observability + diagnostic-agent stack, then retry." -ForegroundColor Yellow
    if ($StackHint) {
        Write-Host "  $StackHint" -ForegroundColor Yellow
    }
    Write-Host "  Docs: docs/TESTING.md (this repo) · host INTEGRATING / APPLY.md" -ForegroundColor Yellow
}

function Test-ContainerRunning([string]$Name) {
    $running = $null
    try {
        $running = docker ps --filter "name=$Name" --format "{{.Names}}" 2>$null
    } catch {
        return $false
    }
    if ($running) { return $true }

    $exited = docker ps -a --filter "name=$Name" --filter "status=exited" --format "{{.Names}}" 2>$null
    if ($exited) {
        Write-Host "  $Name exists but exited. Recent logs:" -ForegroundColor Yellow
        docker logs $Name --tail 12 2>&1 | ForEach-Object { Write-Host "    $_" -ForegroundColor Yellow }
    }
    return $false
}

function Invoke-DockerExec([string]$Container, [string]$Command) {
    try {
        return docker exec $Container sh -c $Command 2>$null
    } catch {
        return $null
    }
}

function Test-Prerequisites {
    Write-Step "Prerequisites (Docker + observability stack + diagnostic-agent)"

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Fail "docker not found on PATH"
        return $false
    }

    $dockerInfoOk = $true
    try {
        $null = docker info 2>&1
        if ($LASTEXITCODE -ne 0) { $dockerInfoOk = $false }
    } catch {
        $dockerInfoOk = $false
    }
    if (-not $dockerInfoOk) {
        Fail "Docker daemon is not running (start Docker Desktop, then retry)"
        Write-StackHint
        return $false
    }

    $running = docker ps --filter "name=$AgentContainer" --format "{{.Names}}" 2>$null
    if (-not $running) {
        $exists = docker ps -a --filter "name=$AgentContainer" --format "{{.Names}}" 2>$null
        if ($exists) {
            Fail "$AgentContainer exists but is not running (docker start $AgentContainer or re-up the stack)"
        } else {
            Fail "$AgentContainer is not deployed (enable the diagnostic-agent Compose profile)"
        }
        Write-StackHint
        return $false
    }

    $stoppedDeps = @()
    foreach ($dep in $RequiredObservabilityContainers) {
        if (-not (Test-ContainerRunning $dep)) {
            $stoppedDeps += $dep
        }
    }
    if ($stoppedDeps.Count -gt 0) {
        Fail ("Observability dependencies not running: " + ($stoppedDeps -join ", "))
        Write-Host "  /alert will hang on DNS timeouts when Prometheus/Loki/Grafana are down." -ForegroundColor Yellow
        Write-Host "  Restart the full stack:" -ForegroundColor Yellow
        Write-StackHint
        return $false
    }

    if (-not $DirectAgent) {
        $stoppedPipeline = @()
        foreach ($dep in $RequiredAlertPipelineContainers) {
            if (-not (Test-ContainerRunning $dep)) {
                $stoppedPipeline += $dep
            }
        }
        if ($stoppedPipeline.Count -gt 0) {
            Fail ("Alert pipeline not running: " + ($stoppedPipeline -join ", "))
            Write-Host "  Default smoke test posts to Alertmanager (alert email + agent webhook)." -ForegroundColor Yellow
            Write-Host "  Start mailpit + alertmanager, or pass -DirectAgent for agent-only (diagnostic email only)." -ForegroundColor Yellow
            return $false
        }
    }

    Pass "Docker OK, diagnostic-agent and observability dependencies running"
    return $true
}

function Invoke-AgentHealth {
    Write-Step "Agent /health"
    try {
        $resp = Invoke-RestMethod -Uri "$AgentUrl/health" -TimeoutSec 15
    } catch {
        Fail ("/health unreachable at $AgentUrl")
        Write-Host "  Check: docker logs $AgentContainer --tail 50" -ForegroundColor Yellow
        Write-Host "  Ensure AGENT_CHAT_PROVIDER / AGENT_CHAT_MODEL (and credentials) are set for the agent" -ForegroundColor Yellow
        return $false
    }
    if ($resp.status -ne "ok") {
        Fail "/health status != ok: $($resp | ConvertTo-Json -Compress)"
        return
    }
    if (-not $resp.agent_initialized) {
        Fail "agent_initialized=false (check container logs for LLM/backend errors)"
        return
    }
    Pass "/health ok, agent_initialized=true"
    return $true
}

function ConvertTo-JsonArray {
    param(
        [object[]]$Items,
        [int]$Depth = 6
    )
    if ($Items.Count -eq 0) { return '[]' }
    if ($Items.Count -eq 1) {
        return '[' + (ConvertTo-Json $Items[0] -Depth $Depth -Compress) + ']'
    }
    return ConvertTo-Json $Items -Depth $Depth -Compress
}

function Get-AuditLineCount {
    $day = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
    $shCount = 'wc -l < /app/audit/diagnostics-' + $day + '.jsonl 2>/dev/null || echo 0'
    $raw = Invoke-DockerExec $AgentContainer $shCount
    return [int]($raw -replace '\D', '')
}

function New-SmokeAlertPayload {
  param([string]$Format = "agent")

    $tenantUuid = "550e8400-e29b-41d4-a716-446655440000"
    $summary = "Smoke test for tenant-smoke-test user $tenantUuid"
    $labels = @{
        alertname = $SmokeAlertName
        service   = "platform-service"
        severity  = "warning"
        tenantId  = "tenant-smoke-test"
    }
    $annotations = @{ summary = $summary }

    if ($Format -eq "alertmanager") {
        return @(
            @{
                labels      = $labels
                annotations = $annotations
                startsAt    = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
            }
        )
    }

    return @{
        alerts = @(
            @{
                status      = "firing"
                labels      = $labels
                annotations = $annotations
            }
        )
    }
}

function Wait-ForAuditIncrease {
    param(
        [int]$Baseline,
        [int]$TimeoutSec
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $current = Get-AuditLineCount
        if ($current -gt $Baseline) {
            return $true
        }
        $secsLeft = [int](($deadline - (Get-Date)).TotalSeconds)
        Write-Host ('  ... waiting for agent audit record (' + $secsLeft + 's left)')
    }
    return $false
}

function Invoke-SyntheticAlertViaAlertmanager {
    Write-Step "Synthetic alert via Alertmanager (alert email + agent webhook)"
    Write-Host "  Alertmanager group_wait is ~30s before the alert email is sent." -ForegroundColor DarkGray

    $auditBefore = Get-AuditLineCount
    $body = ConvertTo-JsonArray -Items (New-SmokeAlertPayload -Format alertmanager)

    try {
        Invoke-RestMethod -Uri "$AlertmanagerUrl/api/v2/alerts" -Method POST `
            -ContentType "application/json" -Body $body -TimeoutSec 30 | Out-Null
    } catch {
        Fail "POST Alertmanager /api/v2/alerts failed: $($_.Exception.Message)"
        return $false
    }
    Pass "Alert posted to Alertmanager ($SmokeAlertName)"

    if (-not (Wait-ForAuditIncrease -Baseline $auditBefore -TimeoutSec $AlertTimeoutSec)) {
        Fail "No new audit record within ${AlertTimeoutSec}s (check Alertmanager route + agent webhook)"
        Write-Host "  docker logs $AlertmanagerContainer --tail 30" -ForegroundColor Yellow
        Write-Host "  docker logs $AgentContainer --tail 30" -ForegroundColor Yellow
        return $false
    }
    Pass "Agent processed webhook and wrote audit record"
    return $true
}

function Invoke-SyntheticAlertDirect {
    Write-Step "Synthetic POST /alert (direct - diagnostic email only)"
    Write-Host "  Skips Alertmanager; no alert email in Mailpit. Omit -DirectAgent for both emails." -ForegroundColor DarkGray

    $payload = (New-SmokeAlertPayload -Format agent) | ConvertTo-Json -Depth 6

    try {
        $resp = Invoke-RestMethod -Uri "$AgentUrl/alert" -Method POST `
            -ContentType "application/json" -Body $payload -TimeoutSec $AlertTimeoutSec
    } catch {
        $msg = $_.Exception.Message
        Fail "POST /alert failed: $msg"
        if ($msg -match "timed out") {
            Write-Host "  The agent may still finish in the background (check: docker logs $AgentContainer --tail 30)" -ForegroundColor Yellow
            Write-Host "  Common cause: Prometheus/Loki/Grafana stopped while diagnostic-agent kept running." -ForegroundColor Yellow
        }
        return $false
    }

    if ($resp.count -lt 1 -or -not $resp.reports) {
        Fail "POST /alert returned no reports: $($resp | ConvertTo-Json -Compress)"
        return $false
    }

    $report = $resp.reports[0]
    if (-not $report.service) {
        Fail "Report missing 'service' field"
        return $false
    }
    Pass ("Structured report returned (service={0}, alert={1})" -f $report.service, $report.alert_type)
    return $true
}

function Test-MailpitDualEmail {
    if ($SkipMailpit) {
        Write-Step "Mailpit dual email (skipped)"
        return
    }

    Write-Step "Mailpit alert + diagnostic summary email"
    Write-Host "  View: $MailpitUrl" -ForegroundColor DarkGray

    $deadline = (Get-Date).AddSeconds($AlertTimeoutSec)
    $sawAlert = $false
    $sawDiagnostic = $false

    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-RestMethod -Uri "$MailpitUrl/api/v1/messages" -TimeoutSec 10
        } catch {
            Fail "Mailpit API unreachable at $MailpitUrl"
            return
        }

        foreach ($msg in @($resp.messages)) {
            $subject = [string]$msg.Subject
            $from = [string]$msg.From.Address
            if ($subject -notmatch [regex]::Escape($SmokeAlertName)) { continue }
            if ($from -match 'alertmanager@') { $sawAlert = $true }
            if ($from -match 'diagnostic-agent@') { $sawDiagnostic = $true }
        }

        if ($sawAlert -and $sawDiagnostic) { break }
        $secsLeft = [int](($deadline - (Get-Date)).TotalSeconds)
        $status = @()
        if ($sawAlert) { $status += 'alert' } else { $status += 'waiting alert' }
        if ($sawDiagnostic) { $status += 'diagnostic' } else { $status += 'waiting diagnostic' }
        Write-Host ('  ... ' + ($status -join ', ') + ' (' + $secsLeft + 's left)')
        Start-Sleep -Seconds 5
    }

    if ($sawAlert) {
        Pass "Mailpit alert email from alertmanager@"
    } else {
        Fail "No Alertmanager alert email for $SmokeAlertName (check host Alertmanager email receiver != blackhole)"
    }
    if ($sawDiagnostic) {
        Pass "Mailpit diagnostic summary email from diagnostic-agent@"
    } else {
        Fail "No diagnostic summary email for $SmokeAlertName (check AGENT_EMAIL_ENABLED / host wiring and agent logs)"
    }
}

function Test-AuditRedaction {
    Write-Step "Audit JSONL + tenant redaction"
    $day = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
    $auditPath = "/app/audit/diagnostics-$day.jsonl"

    $line = Invoke-DockerExec $AgentContainer ('tail -n 1 ' + $auditPath + ' 2>/dev/null')
    if (-not $line) {
        Fail "No audit line at $auditPath (is the audit volume mounted?)"
        return
    }

    Pass "Audit record appended ($auditPath)"

    $leaks = @()
    if ($line -match "tenant-smoke-test") { $leaks += "tenant-smoke-test" }
    if ($line -match "550e8400-e29b-41d4-a716-446655440000") { $leaks += "tenant UUID" }
    if ($leaks.Count -gt 0) {
        Fail "Audit line leaks tenant identifiers: $($leaks -join ', ')"
    } else {
        Pass "No tenant identifiers leaked in audit JSONL"
    }
}

function Test-GrafanaAnnotation {
    if ($SkipGrafana) {
        Write-Step "Grafana annotation (skipped)"
        return
    }

    Write-Step "Grafana annotation (optional)"
    $token = $env:AGENT_GRAFANA_TOKEN
    if (-not $token) { $token = $env:DIAGNOSTIC_AGENT_GRAFANA_TOKEN }
    if (-not $token) {
        # Try loading from host/agent .env without committing secrets to output
        $candidates = @()
        if ($EnvFile) { $candidates += $EnvFile }
        $candidates += (Join-Path (Get-Location) ".env")
        $candidates += (Join-Path (Split-Path $PSScriptRoot -Parent) ".env")
        foreach ($envPath in $candidates) {
            if (-not (Test-Path -LiteralPath $envPath)) { continue }
            Get-Content $envPath | ForEach-Object {
                if ($_ -match '^\s*(?:AGENT_GRAFANA_TOKEN|DIAGNOSTIC_AGENT_GRAFANA_TOKEN)=(.+)$') {
                    $token = $Matches[1].Trim()
                }
            }
            if ($token) { break }
        }
    }

    if (-not $token) {
        Write-Host '  SKIP: DIAGNOSTIC_AGENT_GRAFANA_TOKEN not set (#187 provisioning optional)' -ForegroundColor Yellow
        return
    }

    if (-not (Test-ContainerRunning $GrafanaContainer)) {
        Write-Host "  SKIP: $GrafanaContainer is not running (start the observability stack)" -ForegroundColor Yellow
        return
    }

    $url = 'http://localhost:3000/api/annotations?tags=diagnostic-agent&limit=5'
    $json = Invoke-DockerExec $GrafanaContainer "wget -qO- $url"
    if (-not $json) {
        Fail "Could not query Grafana annotations API"
        return
    }

    try {
        $annotations = $json | ConvertFrom-Json
    } catch {
        Fail "Grafana annotations response not JSON"
        return
    }

    $match = $annotations | Where-Object {
        $_.tags -contains "diagnostic-agent" -and $_.tags -contains "platform-service"
    } | Select-Object -First 1

    if ($match) {
        Pass ("Grafana annotation found (id={0}, tags={1})" -f $match.id, ($match.tags -join ','))
    } else {
        Fail 'No diagnostic-agent annotation for platform-service (run #187 provisioning?)'
    }
}

function Invoke-RealPathFault {
    Write-Step "Real path: stop $FaultService -> Alertmanager -> agent"
    $running = docker ps --filter "name=$FaultContainer" --format "{{.Names}}" 2>$null
    if (-not $running) {
        Write-Host "  SKIP: $FaultContainer not running (start full app stack for real-path test)" -ForegroundColor Yellow
        return
    }

    $day = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
    $shCount = 'wc -l < /app/audit/diagnostics-' + $day + '.jsonl 2>/dev/null || echo 0'
    $auditBefore = Invoke-DockerExec $AgentContainer $shCount
    $auditBefore = [int]($auditBefore -replace '\D', '')

    Write-Host "  Stopping $FaultContainer ..."
    docker stop $FaultContainer | Out-Null

    try {
        $deadline = (Get-Date).AddSeconds($RealPathTimeoutSec)
        $triggered = $false
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 10
            $auditAfter = Invoke-DockerExec $AgentContainer $shCount
            $auditAfter = [int]($auditAfter -replace '\D', '')
            if ($auditAfter -gt $auditBefore) {
                $triggered = $true
                break
            }
            $secsLeft = [int](($deadline - (Get-Date)).TotalSeconds)
            Write-Host ('  ... waiting for alert pipeline (' + $secsLeft + 's left)')
        }
        if ($triggered) {
            Pass "Real-path alert produced a new audit record"
        } else {
            Fail "No new audit record within ${RealPathTimeoutSec}s (check Prometheus rules + Alertmanager route)"
        }
    } finally {
        Write-Host "  Starting $FaultContainer ..."
        docker start $FaultContainer | Out-Null
    }
}

# Remove a container only if it exists. A bare `docker rm -f` on a missing
# container writes to stderr; under $ErrorActionPreference=Stop, PowerShell 5.1
# turns redirected native stderr into a terminating NativeCommandError.
function Remove-ContainerIfExists([string]$Name) {
    $id = docker ps -aq --filter "name=^/$Name$" 2>$null
    if ($id) { docker rm -f $Name | Out-Null }
}

function Invoke-RulePathSmoke {
    Write-Step "Rule path: sentinel log -> Loki rule -> Alertmanager -> agent (#357)"

    if (-not (Test-ContainerRunning $PromtailContainer)) {
        Fail "$PromtailContainer not running (start with --profile log-collector); Promtail must scrape the marker"
        return $false
    }
    if (-not (Test-ContainerRunning $LokiContainer)) {
        Fail "$LokiContainer not running (Loki ruler evaluates the DiagnosticAgentSmokeMarker rule)"
        return $false
    }
    if (-not (Test-ContainerRunning $AlertmanagerContainer)) {
        Fail "$AlertmanagerContainer not running (routes severity=warning to the agent webhook)"
        return $false
    }

    $auditBefore = Get-AuditLineCount

    # Emit the sentinel as a short-lived container. The `service` label is derived
    # by Promtail from the JSON `service` field (not the container name), so this
    # lands in Loki as {service="platform-service"} and trips the marker rule.
    # It lives ~40s so Promtail's 5s docker service discovery reliably scrapes it.
    # The emitter script is base64-encoded to avoid PowerShell/docker/sh quoting issues.
    $emitter = $EmitterName
    Remove-ContainerIfExists $emitter

    $emitScript = @'
i=0
while [ $i -lt 20 ]; do
  echo "{\"@timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%SZ)\",\"level\":\"WARN\",\"service\":\"platform-service\",\"message\":\"__MARKER__ diagnostic-agent rule-path smoke\"}"
  i=$((i+1))
  sleep 2
done
'@
    $emitScript = $emitScript.Replace('__MARKER__', $SmokeMarker)
    # The here-string carries CRLF on Windows; busybox sh chokes on \r ("unexpected done").
    $emitScript = $emitScript.Replace("`r", "")
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($emitScript))

    Write-Host "  Emitting sentinel '$SmokeMarker' (service=platform-service) for ~40s ..." -ForegroundColor DarkGray
    # docker writes image-pull progress to stderr; under EAP=Stop, PS 5.1 turns
    # redirected native stderr into a terminating error, so relax it briefly.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $runOut = docker run -d --name $emitter alpine sh -c "echo $b64 | base64 -d | sh" 2>&1
    $ErrorActionPreference = $prevEap
    if ($LASTEXITCODE -ne 0) {
        Fail ("Could not start marker emitter container: " + ($runOut -join ' '))
        return $false
    }

    try {
        Write-Host "  Waiting for Loki ruler eval + Alertmanager group_wait + agent webhook ..." -ForegroundColor DarkGray
        # Assert the NEW audit record is specifically our marker alert. Since the
        # Alertmanager v2 fix lets every firing alert through, a bare count-increase
        # could pass on an unrelated alert; only lines added since $auditBefore are
        # inspected, and they must reference DiagnosticAgentSmokeMarker.
        $day = [DateTime]::UtcNow.ToString("yyyy-MM-dd")
        $auditPath = "/app/audit/diagnostics-$day.jsonl"
        $deadline = (Get-Date).AddSeconds($AlertTimeoutSec)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 5
            $auditNow = Get-AuditLineCount
            if ($auditNow -gt $auditBefore) {
                $newCount = $auditNow - $auditBefore
                $newLines = Invoke-DockerExec $AgentContainer ('tail -n ' + $newCount + ' ' + $auditPath + ' 2>/dev/null')
                if ($newLines -match 'DiagnosticAgentSmokeMarker') {
                    Pass "Marker rule fired end-to-end; agent wrote a DiagnosticAgentSmokeMarker audit record"
                    return $true
                }
            }
            $secsLeft = [int](($deadline - (Get-Date)).TotalSeconds)
            Write-Host ('  ... waiting for DiagnosticAgentSmokeMarker audit record (' + $secsLeft + 's left)')
        }
        Fail "No DiagnosticAgentSmokeMarker audit record within ${AlertTimeoutSec}s"
        Write-Host "  Check: docker exec $LokiContainer wget -qO- http://localhost:3100/loki/api/v1/rules" -ForegroundColor Yellow
        Write-Host "  Check: docker exec $AlertmanagerContainer wget -qO- http://localhost:9093/api/v2/alerts" -ForegroundColor Yellow
        Write-Host "  Check: docker logs $AgentContainer --tail 30" -ForegroundColor Yellow
        return $false
    } finally {
        Remove-ContainerIfExists $emitter
    }
}

# --- main ---
Write-Host "Diagnostic agent smoke test (prefix=$ContainerPrefix)" -ForegroundColor White

if (-not (Test-Prerequisites)) {
    Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
    exit 1
}

if (-not (Invoke-AgentHealth)) {
    Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
    exit 1
}

if ($RulePath) {
    if (-not (Invoke-RulePathSmoke)) {
        Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
        exit 1
    }
    Test-GrafanaAnnotation
    Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
    if ($Failed -gt 0) { exit 1 }
    exit 0
}

if ($DirectAgent) {
    if (-not (Invoke-SyntheticAlertDirect)) {
        Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
        exit 1
    }
} else {
    if (-not (Invoke-SyntheticAlertViaAlertmanager)) {
        Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
        exit 1
    }
    Test-MailpitDualEmail
}

Test-AuditRedaction
Test-GrafanaAnnotation

if ($RealPath) {
    Invoke-RealPathFault
} else {
    Write-Step "Real path (skipped)"
    Write-Host "  Re-run with -RealPath when platform-service is running in the stack."
}

Write-Host ('Summary: ' + $Passed + ' passed, ' + $Failed + ' failed') -ForegroundColor White
if ($Failed -gt 0) { exit 1 }
exit 0
