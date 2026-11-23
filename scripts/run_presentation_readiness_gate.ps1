[CmdletBinding()]
param(
    [switch]$FullSuite,
    [string]$AuditRoot
)

$ErrorActionPreference = 'Stop'
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$repo = Split-Path -Parent $PSScriptRoot
$venvWinPython = Join-Path $repo '.venv\Scripts\python.exe'
$venvUnixPython = Join-Path $repo '.venv/bin/python'
$python = if (Test-Path -LiteralPath $venvWinPython) {
    $venvWinPython
} elseif (Test-Path -LiteralPath $venvUnixPython) {
    $venvUnixPython
} else {
    throw "Virtual-environment Python is missing under: $(Join-Path $repo '.venv')"
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$auditRoot = if ($AuditRoot) {
    $AuditRoot
} elseif ($env:TRANSDOC_AUDIT_ROOT) {
    $env:TRANSDOC_AUDIT_ROOT
} else {
    Join-Path (Split-Path -Parent $repo) 'audit'
}
$log = Join-Path $auditRoot "presentation-readiness-$stamp.txt"

if (-not (Test-Path -LiteralPath $auditRoot)) {
    New-Item -ItemType Directory -Path $auditRoot | Out-Null
}

$env:HF_HUB_OFFLINE = '1'
$env:TRANS_DOC_DOMAIN_GLOSSARY = '1'
$env:PYTHONUNBUFFERED = '1'

$focused = @(
    'tests/test_presentation_readiness_gate.py'
    'tests/test_p0a_immutable_integrity.py'
    'tests/test_p0b_region_boundary.py'
    'tests/test_p0cd_table_and_output_audit.py'
    'tests/test_p1ab_pagination_terminology.py'
    'tests/test_protected_integrity_m4.py'
    'tests/test_formula_placeholder_recovery.py'
    'tests/test_b27_formula_layout.py'
    'tests/test_reading_formula_code_figure_p0.py'
    'tests/test_numeric_table_source_positions.py'
    'tests/test_pseudocode_source_positions.py'
    'tests/test_layout_footnotes_reflow_p0.py'
    'tests/test_selected_page_pdf_export.py'
)

Push-Location $repo
try {
    "PRESENTATION READINESS GATE" | Tee-Object -FilePath $log
    "Repository: $repo" | Tee-Object -FilePath $log -Append
    "Commit: $(git rev-parse --short HEAD)" | Tee-Object -FilePath $log -Append
    "Started: $(Get-Date -Format o)" | Tee-Object -FilePath $log -Append
    "Model loading: disabled/offline" | Tee-Object -FilePath $log -Append

    & $python -m pytest -q @focused 2>&1 | Tee-Object -FilePath $log -Append
    $focusedExit = $LASTEXITCODE
    if ($focusedExit -ne 0) {
        throw "Focused presentation gate failed with exit code $focusedExit. Evidence: $log"
    }

    git diff --check 2>&1 | Tee-Object -FilePath $log -Append
    if ($LASTEXITCODE -ne 0) {
        throw "Git diff check failed. Evidence: $log"
    }

    if ($FullSuite) {
        "Running complete repository suite..." | Tee-Object -FilePath $log -Append
        & $python -m pytest -q 2>&1 | Tee-Object -FilePath $log -Append
        $fullExit = $LASTEXITCODE
        if ($fullExit -ne 0) {
            throw "Full suite failed with exit code $fullExit. Evidence: $log"
        }
    }

    "PASS: deterministic presentation-readiness checks succeeded." |
        Tee-Object -FilePath $log -Append
    "Evidence: $log" | Tee-Object -FilePath $log -Append
}
finally {
    Pop-Location
}
