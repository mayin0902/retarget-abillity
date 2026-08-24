[CmdletBinding()]
param(
    [ValidateSet('3.11', '3.12', '3.13')]
    [string]$PythonVersion = '3.12',
    [string]$PythonExecutable,
    [switch]$SkipCompanyModels,
    [switch]$WithMovie60Release
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE. See the command output above."
    }
}

function Invoke-NativeCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$HideOutput
    )
    $PreviousPreference = $ErrorActionPreference
    $OutputLines = [System.Collections.Generic.List[string]]::new()
    try {
        # Native stderr is diagnostic output here; the exit code decides success.
        $ErrorActionPreference = 'Continue'
        & $Executable @Arguments 2>&1 | ForEach-Object {
            $Line = $_.ToString()
            $OutputLines.Add($Line)
            if (-not $HideOutput) {
                Write-Host $Line
            }
        }
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
    return [PSCustomObject]@{
        ExitCode = $ExitCode
        Output = $OutputLines.ToArray()
    }
}

function Test-PipSslFailure {
    param([Parameter(Mandatory = $true)][string[]]$Output)
    $CombinedOutput = $Output -join "`n"
    return $CombinedOutput -match (
        '(?i)CERTIFICATE_VERIFY_FAILED|' +
        'SSLCertVerificationError|' +
        'certificate verify failed|' +
        'self[- ]signed certificate'
    )
}

function Get-PipTrustedHosts {
    param([Parameter(Mandatory = $true)][string[]]$Text)
    $Hosts = [System.Collections.Generic.List[string]]::new()
    $CombinedText = $Text -join "`n"
    $Patterns = @(
        '(?i)https?://(?:[^@\s/]+@)?(?<host>\[[^\]]+\]|[^/:\s''"<>)`,;]+)(?::\d+)?',
        '(?i)\bhost\s*=\s*[''"](?<host>[^''"]+)'
    )
    foreach ($Pattern in $Patterns) {
        foreach ($Match in [regex]::Matches($CombinedText, $Pattern)) {
            $HostName = $Match.Groups['host'].Value.Trim('[', ']').Trim().ToLowerInvariant()
            if (
                $HostName -match '^[a-z0-9.-]+$' -and
                $HostName -notmatch '^\.' -and
                $HostName -notmatch '\.$' -and
                $HostName -notmatch '\.\.' -and
                -not $Hosts.Contains($HostName)
            ) {
                $Hosts.Add($HostName)
            }
        }
    }
    return $Hosts.ToArray()
}

function Invoke-PipInstall {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$InstallArguments,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $PipArguments = @('-m', 'pip', 'install') + $InstallArguments
    $FirstAttempt = Invoke-NativeCapture $Executable $PipArguments
    if ($FirstAttempt.ExitCode -eq 0) {
        return
    }
    if (-not (Test-PipSslFailure -Output $FirstAttempt.Output)) {
        throw "$Label failed with exit code $($FirstAttempt.ExitCode). See the command output above."
    }

    $ConfigurationText = [System.Collections.Generic.List[string]]::new()
    foreach ($ConfiguredValue in @(
        $env:PIP_INDEX_URL,
        $env:PIP_EXTRA_INDEX_URL,
        $env:PIP_FIND_LINKS
    )) {
        if (-not [string]::IsNullOrWhiteSpace($ConfiguredValue)) {
            $ConfigurationText.Add($ConfiguredValue)
        }
    }
    $PipConfiguration = Invoke-NativeCapture $Executable @('-m', 'pip', 'config', 'list') -HideOutput
    if ($PipConfiguration.ExitCode -eq 0) {
        foreach ($Line in $PipConfiguration.Output) {
            $ConfigurationText.Add($Line)
        }
    }

    $DetectionInput = @($FirstAttempt.Output) + @($ConfigurationText.ToArray())
    $TrustedHosts = @(Get-PipTrustedHosts -Text $DetectionInput)
    if ($TrustedHosts.Count -eq 0) {
        throw (
            "$Label failed because pip reported an SSL certificate error, but Bootstrap could " +
            'not detect the repository host. Configure PIP_CERT or PIP_TRUSTED_HOST and rerun.'
        )
    }

    Write-Warning (
        "$Label encountered an SSL certificate error. Retrying once for detected host(s): " +
        "$($TrustedHosts -join ', '). TLS certificate verification is disabled for those hosts " +
        'during this retry.'
    )
    $RetryArguments = @($PipArguments)
    foreach ($TrustedHost in $TrustedHosts) {
        $RetryArguments += @('--trusted-host', $TrustedHost)
    }
    $RetryAttempt = Invoke-NativeCapture $Executable $RetryArguments
    if ($RetryAttempt.ExitCode -ne 0) {
        throw (
            "$Label failed with exit code $($RetryAttempt.ExitCode) after the SSL fallback retry. " +
            'See the command output above.'
        )
    }
}

function Test-SupportedPython {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$PrefixArguments = @()
    )
    $PreviousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $Executable @PrefixArguments -c 'import sys; raise SystemExit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 3)' *> $null
        return $LASTEXITCODE -eq 0
    } finally {
        $ErrorActionPreference = $PreviousPreference
    }
}

# Dot-sourcing exposes the helpers for deterministic regression tests without running Bootstrap.
if ($MyInvocation.InvocationName -eq '.') {
    return
}

$Required = @(
    'pyproject.toml',
    'requirements\constraints-py311-313.txt',
    'strategies\registry.yaml',
    'datasets\analyzer_models_company_cpu_v2\download_manifest.csv'
)
$Missing = $Required | Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }
if ($Missing) {
    throw "Repository is incomplete: $($Missing -join ', ')"
}

$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$Cli = Join-Path $RepoRoot '.venv\Scripts\retarget-engine.exe'

if (Test-Path -LiteralPath '.venv') {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw '.venv exists but has no Windows Python. Move it aside, then rerun Bootstrap.'
    }
    if (-not (Test-SupportedPython -Executable $Python)) {
        throw (
            '.venv uses an unsupported Python version. This project requires Python 3.11-3.13. ' +
            'Move the existing .venv aside, install Python 3.12, then rerun Bootstrap.'
        )
    }
    Write-Host 'Reusing the existing repository-local .venv.' -ForegroundColor Cyan
} elseif ($PythonExecutable) {
    if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
        throw "Python executable does not exist: $PythonExecutable"
    }
    if (-not (Test-SupportedPython -Executable $PythonExecutable)) {
        throw (
            "Python executable must be version 3.11-3.13: $PythonExecutable. " +
            'Install Python 3.12 or pass another -PythonExecutable path.'
        )
    }
    Invoke-Checked $PythonExecutable @('-m', 'venv', '.venv') 'Virtual environment creation'
} else {
    $LauncherReady = $false
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $LauncherReady = Test-SupportedPython -Executable 'py' -PrefixArguments @("-$PythonVersion")
    }
    if ($LauncherReady) {
        Invoke-Checked 'py' @("-$PythonVersion", '-m', 'venv', '.venv') 'Virtual environment creation'
    } else {
        $FallbackPython = $null
        if (Get-Command python -ErrorAction SilentlyContinue) {
            if (Test-SupportedPython -Executable 'python') {
                $FallbackPython = (Get-Command python).Source
            }
        }
        if ($null -eq $FallbackPython -and (Get-Command conda -ErrorAction SilentlyContinue)) {
            $CondaScripts = Split-Path -Parent (Get-Command conda).Source
            $CondaPython = Join-Path (Split-Path -Parent $CondaScripts) 'python.exe'
            if (Test-Path -LiteralPath $CondaPython -PathType Leaf) {
                if (Test-SupportedPython -Executable $CondaPython) {
                    $FallbackPython = $CondaPython
                }
            }
        }
        if ($null -eq $FallbackPython) {
            Write-Host "No supported Python 3.11-3.13 interpreter was found." -ForegroundColor Red
            Write-Host "Install Python $PythonVersion, reopen PowerShell, then rerun this script."
            Write-Host (
                "Suggested command (run manually): winget install --exact --id " +
                "Python.Python.$PythonVersion"
            )
            exit 2
        }
        $ActiveVersion = & $FallbackPython -c 'import sys; print(str(sys.version_info.major)+chr(46)+str(sys.version_info.minor))'
        Write-Warning (
            "py -$PythonVersion is unavailable; using supported Python $ActiveVersion at " +
            "$FallbackPython. Pass -PythonExecutable to select an exact interpreter."
        )
        Invoke-Checked $FallbackPython @('-m', 'venv', '.venv') 'Virtual environment creation'
    }
}

Invoke-PipInstall $Python @('--upgrade', 'pip==25.2', 'setuptools==80.9.0', 'wheel==0.45.1') 'Build-tool installation'
Invoke-PipInstall $Python @('-c', 'requirements\constraints-py311-313.txt', '-e', '.[dev]') 'Project installation'
if (-not $SkipCompanyModels) {
    Invoke-PipInstall $Python @('-r', 'requirements\company-models-windows.txt') 'Company-model runtime installation'
    # The current profile needs only the pinned YuNet asset from this downloader.
    # Legacy PPOCRv3/CRNN/YOLOX assets remain available for explicit historical replay.
    Invoke-Checked $Python @(
        'scripts\materialize_analyzer_models.py',
        '--manifest',
        'datasets\analyzer_models_company_cpu_v2\download_manifest.csv'
    ) 'Current analyzer model materialization'
    Invoke-Checked $Python @('scripts\materialize_company_models.py') 'Company model materialization'
}
Invoke-Checked $Cli @('strategy', 'validate') 'Immutable strategy registry validation'
Invoke-Checked $Cli @('strategy', 'show') 'Current strategy validation'
Invoke-Checked $Python @('-m', 'pytest', '-q', 'tests\test_strategy.py', 'tests\test_single_image_workflow_tools.py') 'Bootstrap smoke tests'
Invoke-Checked $Cli @('doctor') 'Environment readiness check'

if ($WithMovie60Release) {
    Invoke-Checked $Python @('scripts\materialize_movie60_release.py') 'Movie60 Release materialization'
}

Write-Host ''
Write-Host 'Bootstrap completed.' -ForegroundColor Green
Write-Host "Python: $Python"
Write-Host 'Next: double-click START_REVIEW.bat, or read docs\QUICKSTART.md.'
