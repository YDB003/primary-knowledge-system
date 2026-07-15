param(
    [Parameter(Mandatory = $true)]
    [string]$Vault,

    [Parameter(Mandatory = $true)]
    [string]$MathRoot,

    [Parameter(Mandatory = $true)]
    [string]$ChineseRoot,

    [Parameter(Mandatory = $true)]
    [string]$EnglishRoot
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $projectRoot "src"

$repositories = @(
    @{
        Id = "cn-primary-math-taxonomy"
        Root = $MathRoot
        Subject = "math"
        Adapter = "math-compiled-v1"
    },
    @{
        Id = "cn-primary-chinese-taxonomy"
        Root = $ChineseRoot
        Subject = "chinese"
        Adapter = "chinese-compiled-v1"
    },
    @{
        Id = "cn-primary-english-taxonomy"
        Root = $EnglishRoot
        Subject = "english"
        Adapter = "english-runtime-v1"
    }
)

foreach ($repository in $repositories) {
    & python -m pks attach `
        --vault $Vault `
        --repository-id $repository.Id `
        --root $repository.Root `
        --subject $repository.Subject `
        --adapter $repository.Adapter
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & python -m pks scan `
        --vault $Vault `
        --repository-id $repository.Id
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & python -m pks import `
        --vault $Vault `
        --repository-id $repository.Id
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
