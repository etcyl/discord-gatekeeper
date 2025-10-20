New-Item -ItemType Directory -Force -Path $Logs | Out-Null

@'
$ErrorActionPreference = "Stop"

# Use the script's own folder as working dir (works from System32, Scheduler, etc.)
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

$logs   = Join-Path $Here "logs"
$diag   = Join-Path $logs "launcher_diag.log"
$stdout = Join-Path $logs "bot_stdout.log"
$stderr = Join-Path $logs "bot_stderr.log"

New-Item -ItemType Directory -Force -Path $logs | Out-Null
"diag start $(Get-Date)" | Out-File -FilePath $diag -Encoding UTF8 -Append

# Pick Python: venv → py.exe → python
$py = Join-Path $Here ".venv\Scripts\python.exe"
if (!(Test-Path $py)) { $py = "py.exe" }
if (-not (Get-Command $py -ErrorAction SilentlyContinue)) { $py = "python" }
"using python: $py" | Out-File -FilePath $diag -Encoding UTF8 -Append

# Log python version (also creates stdout/stderr files)
try { & $py --version 1>> $stdout 2>> $stderr; "python --version ok" | Out-File $diag -Append } catch { "python --version FAILED: $($_.Exception.Message)" | Out-File $diag -Append }

# Entry file
$entry = Join-Path $Here "bot.py"
if (!(Test-Path $entry)) { "ERROR: missing entry: $entry" | Out-File $diag -Append; exit 1 }

$env:PYTHONUNBUFFERED = "1"
"starting: $py $entry" | Out-File $diag -Append
try { & $py $entry 1>> $stdout 2>> $stderr; "bot exited with code $LASTEXITCODE" | Out-File $diag -Append; exit $LASTEXITCODE } catch { "bot invoke FAILED: $($_.Exception.Message)" | Out-File $diag -Append; exit 2 }
'@ | Set-Content -Path $Runner -Encoding UTF8
