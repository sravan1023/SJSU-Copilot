<#
.SYNOPSIS
  Replay every migration against a throwaway Postgres container, then assert the
  RLS policies and triggers actually behave.

.DESCRIPTION
  This exists because `supabase start` is currently unusable: CLI 2.117.0
  prepulls an image tag (storage-api:operation-ergonomics) that does not exist in
  any registry, and it prepulls it even when the service is excluded with -x.

  Driving Postgres directly is also a better test for our purposes: stubbing
  nothing and setting request.jwt.claim.sub ourselves lets us exercise the
  policies as a specific user, which `supabase start` alone would not do.

  The supabase/postgres image already ships auth.users, auth.uid(), auth.role()
  and the anon / authenticated / service_role roles, so no shim is needed.

.EXAMPLE
  ./supabase/tests/run-verification.ps1
#>

[CmdletBinding()]
param(
  [string]$ContainerName = 'sjsu-verify',
  [string]$Image = 'public.ecr.aws/supabase/postgres:17.6.1.084',
  [int]$HostPort = 55432,
  [switch]$KeepContainer
)

# NOT 'Stop': Windows PowerShell turns any native-command stderr write into an
# error record, and docker/psql both write ordinary progress and NOTICE output
# there. Failures are detected from exit codes and parsed output instead.
$ErrorActionPreference = 'Continue'

# Docker Desktop installs per-user and may not be on PATH in a fresh shell.
$dockerBin = "$env:LOCALAPPDATA\Programs\DockerDesktop\resources\bin"
if (Test-Path $dockerBin) { $env:Path = "$dockerBin;$env:Path" }

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$migrations = Join-Path $repoRoot 'supabase\migrations'
$testsDir = $PSScriptRoot

Write-Host "Starting $Image ..." -ForegroundColor Cyan
docker rm -f $ContainerName 2>&1 | Out-Null
docker run -d --name $ContainerName -e POSTGRES_PASSWORD=postgres -p "${HostPort}:5432" $Image | Out-Null

# The postgres entrypoint runs initdb against a TEMPORARY server, then shuts it
# down and starts the real one. pg_isready succeeds against that temporary
# server, so polling it alone races the restart and connections get killed
# mid-statement with "terminating connection due to administrator command".
# Wait for the entrypoint to announce init is finished first.
# Done entirely inside the container so no host-side stderr parsing is involved.
# Requiring readiness, then a pause, then readiness again means a restart that
# happens in between is caught rather than raced.
Write-Host 'Waiting for Postgres (init restart included) ...' -ForegroundColor DarkGray
docker exec $ContainerName sh -c '
  i=0
  until pg_isready -U postgres -q; do i=$((i+1)); [ $i -gt 150 ] && exit 1; sleep 2; done
  sleep 5
  i=0
  until pg_isready -U postgres -q; do i=$((i+1)); [ $i -gt 150 ] && exit 1; sleep 2; done
' | Out-Null
if ($LASTEXITCODE -ne 0) {
  Write-Host 'Postgres did not become ready. Last container logs:' -ForegroundColor Red
  docker logs $ContainerName | Select-Object -Last 20
  exit 1
}

docker cp $migrations "${ContainerName}:/tmp/mig" | Out-Null
docker cp (Join-Path $testsDir 'apply_migrations.sh') "${ContainerName}:/tmp/apply.sh" | Out-Null
docker cp (Join-Path $testsDir 'verify_policies.sql') "${ContainerName}:/tmp/verify.sql" | Out-Null

# psql writes NOTICEs to stderr, and Windows PowerShell turns native-command
# stderr into error records. Capture inside the container and read stdout only.
Write-Host "`n=== Migration replay ===" -ForegroundColor Cyan
docker exec $ContainerName sh -c 'sh /tmp/apply.sh > /tmp/apply.out 2>&1; echo "exit=$?" >> /tmp/apply.out' | Out-Null
$replay = docker exec $ContainerName cat /tmp/apply.out
$replay | Where-Object { $_ -notmatch '^exit=' } | ForEach-Object { $_ }
$replayFailed = [bool](@($replay | Where-Object { $_ -match '^exit=[^0]' }).Count)

Write-Host "`n=== Policy verification ===" -ForegroundColor Cyan
docker exec $ContainerName sh -c 'psql -U postgres -f /tmp/verify.sql > /tmp/verify.out 2>&1' | Out-Null
$output = docker exec $ContainerName cat /tmp/verify.out
$output |
  Where-Object { $_ -match '^===|NOTICE:|ERROR:' } |
  ForEach-Object { ($_ -replace '^psql:/tmp/verify\.sql:\d+: ', '') -replace '^NOTICE:  ', '' }

$failures = @($output | Where-Object { $_ -match 'FAIL ' }).Count
$errors = @($output | Where-Object { $_ -match 'ERROR:' }).Count
if ($errors -gt 0) { $failures += $errors }

Write-Host ''
if ($replayFailed) { Write-Host 'MIGRATION REPLAY FAILED' -ForegroundColor Red }
if ($failures -gt 0) {
  Write-Host "$failures verification failure(s)" -ForegroundColor Red
} else {
  Write-Host 'All verification checks passed' -ForegroundColor Green
}

if (-not $KeepContainer) {
  docker rm -f $ContainerName 2>&1 | Out-Null
} else {
  Write-Host "Container '$ContainerName' left running on port $HostPort" -ForegroundColor Yellow
}

if ($replayFailed -or $failures -gt 0) { exit 1 }
