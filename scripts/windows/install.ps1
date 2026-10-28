# Parameters: -RepoDir -HomeDir -NoPrereqs -NoClaude -NoHud -NoCcLauncher -RepoUrl -Mode -CpaUrl -CpaToken -CpaDir -InstallCpa -Adapters
[CmdletBinding()]
param(
    [string]$RepoDir,
    [string]$HomeDir,
    [switch]$NoPrereqs,
    [switch]$NoClaude,
    [switch]$NoHud,
    [switch]$NoCcLauncher,
    [string]$RepoUrl = "https://github.com/LeoXiangkai/evoharness.git",
    [ValidateSet("cc", "worker")]
    [string]$Mode = "cc",
    [string]$CpaUrl = "http://127.0.0.1:8317",
    [string]$CpaToken,
    [string]$CpaDir = (Join-Path $env:LOCALAPPDATA "cliproxyapi"),
    [switch]$InstallCpa,
    [string]$Adapters = "",
    [string]$TeamConfig = ""
)

$ErrorActionPreference = "Stop"

function Refresh-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($machine, $user, $env:Path) | Where-Object { $_ }
    $env:Path = ($parts | Select-Object -Unique) -join [IO.Path]::PathSeparator
}

function Install-WingetPackage([string]$Id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "未找到 winget。请先手动安装 $Id：Git https://git-scm.com/download/win；Python https://www.python.org/downloads/windows/"
    }
    & winget install -e --id $Id --silent --accept-source-agreements --accept-package-agreements
    if ($LASTEXITCODE -ne 0) { throw "winget 安装 $Id 失败，退出码 $LASTEXITCODE" }
    Refresh-Path
}

function Test-Runnable([string]$Name) {
    # A command can exist yet fail to start (for example a truncated claude.exe), so run it instead of only resolving it.
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { return $false }
    try { & $Name --version *> $null; return $LASTEXITCODE -eq 0 } catch { return $false }
}
function Install-ClaudeCode {
    # The official installer calls exit on failure; run it in a child process so it cannot terminate this script.
    $installer = Join-Path ([IO.Path]::GetTempPath()) "claude-install-$PID.ps1"
    Invoke-WebRequest https://claude.ai/install.ps1 -OutFile $installer -UseBasicParsing
    try {
        & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
        Assert-ExitCode "Claude Code 官方安装器"
    } finally {
        Remove-Item $installer -Force -ErrorAction SilentlyContinue
    }
    $localBin = Join-Path $HOME ".local\bin"
    if (Test-Path $localBin) { $env:Path = "$localBin$([IO.Path]::PathSeparator)$env:Path" }
    if (-not (Test-Runnable "claude")) { throw "Claude Code 安装后仍无法运行（claude --version 失败）；请按 https://docs.anthropic.com/en/docs/claude-code/setup 手动安装后重跑" }
}
function Test-CollectRepoAccess([string]$ConfigPath) {
    try { $repo = (Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json).collect_repo } catch { $repo = "" }
    if ([string]::IsNullOrWhiteSpace($repo)) { return }
    & git ls-remote --heads $repo *> $null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "当前账号无法访问团队采集仓库，数据会先留在本机，上传会失败。请先登录有权限的 GitHub 账号（gh auth login，或 git clone 该仓库一次以保存凭据），再运行 aicj-telemetry report --force。"
    }
}
function Assert-ExitCode([string]$Action) {
    if ($LASTEXITCODE -ne 0) { throw "$Action 失败，退出码 $LASTEXITCODE" }
}

function Complete-Script([int]$Code) {
    if ($PSCommandPath) {
        exit $Code
    }
    $global:LASTEXITCODE = $Code
    return
}

function Set-SessionOrUser([string]$Name, [string]$Value, [bool]$Explicit) {
    if ($HomeDir) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
        return
    }
    $existing = [Environment]::GetEnvironmentVariable($Name, "User")
    if ($Explicit -or [string]::IsNullOrWhiteSpace($existing)) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "User")
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    } else {
        $envValue = [Environment]::GetEnvironmentVariable($Name, "Process")
        if ([string]::IsNullOrWhiteSpace($envValue)) { [Environment]::SetEnvironmentVariable($Name, $existing, "Process") }
        Write-Host "$Name 已存在，保留现值。"
    }
}

function Get-CpaApiKey([string]$Path) {
    if (-not (Test-Path $Path)) { return "" }
    $inApiKeys = $false
    $apiKeysIndent = -1
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if (-not $inApiKeys) {
            if ($line -match '^(\s*)api-keys:\s*(?:#.*)?$') {
                $apiKeysIndent = $Matches[1].Length
                $inApiKeys = $true
            }
            continue
        }
        if ($line -match '^\s*$') { continue }
        $indent = ([regex]::Match($line, '^\s*')).Value.Length
        if ($indent -le $apiKeysIndent) { break }
        if ($line -match '^\s*-\s*(.*?)\s*(?:#.*)?$') {
            $value = $Matches[1].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1, $value.Length - 2)
            }
            if (-not [string]::IsNullOrWhiteSpace($value)) { return $value }
        }
    }
    return ""
}

function Test-Cpa([string]$Url, [string]$Token) {
    if ([string]::IsNullOrWhiteSpace($Token)) { throw "worker 模式需要 CPA token；请传 -CpaToken 或设置用户级 AICJ_EXECUTOR_TOKEN" }
    try {
        $requestParams = @{ Uri = ($Url.TrimEnd("/") + "/v1/models"); Headers = @{ Authorization = "Bearer $Token" }; UseBasicParsing = $true; TimeoutSec = 3 }
        if ($PSVersionTable.PSVersion.Major -ge 7) { $requestParams.NoProxy = $true } else { [Net.WebRequest]::DefaultWebProxy = $null }
        $response = Invoke-WebRequest @requestParams
        if ([int]$response.StatusCode -ne 200) { throw "HTTP $($response.StatusCode)" }
    } catch {
        throw "CPA 不可达：请先启动 CPA 或改用 -Mode cc。$($_.Exception.Message)"
    }
}

function Install-CcLauncher {
    if ($HomeDir) {
        Write-Host "沙箱模式（-HomeDir）不创建 cc 启动器"
        return
    }
    if ($NoCcLauncher) { return }

    $claudeCommand = Get-Command claude -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $claudeCommand) {
        Write-Host "未找到 claude，跳过 cc 启动器；安装 Claude Code 后重跑本脚本"
        return
    }

    $claudePath = if ($claudeCommand.Source) { $claudeCommand.Source } else { $claudeCommand.Path }
    if ([string]::IsNullOrWhiteSpace($claudePath)) {
        Write-Host "未找到 claude，跳过 cc 启动器；安装 Claude Code 后重跑本脚本"
        return
    }
    $target = Join-Path (Split-Path -Parent $claudePath) "cc.cmd"
    $content = "@claude --dangerously-skip-permissions %*"
    try {
        if (Test-Path -LiteralPath $target) {
            $existing = [IO.File]::ReadAllText($target)
            if ($existing.Trim() -eq $content) {
                Write-Host "cc 启动器已存在"
            } else {
                Write-Warning "$target 已存在且内容不同，未覆盖"
            }
        } else {
            [IO.File]::WriteAllText($target, "$content`r`n", [Text.Encoding]::ASCII)
            $ccCommand = Get-Command cc -ErrorAction SilentlyContinue | Select-Object -First 1
            $ccPath = if ($ccCommand) { if ($ccCommand.Source) { $ccCommand.Source } else { $ccCommand.Path } } else { "" }
            $targetFullPath = [IO.Path]::GetFullPath($target)
            $ccFullPath = if ([string]::IsNullOrWhiteSpace($ccPath)) { "" } else { [IO.Path]::GetFullPath($ccPath) }
            if ($ccFullPath -and $ccFullPath -ine $targetFullPath) {
                Write-Warning "PATH 中的 cc 指向 $ccPath，请改用 $target 或调整 PATH"
            }
            Write-Host "已创建 cc 启动器：$target（等价于 claude --dangerously-skip-permissions）"
        }
    } catch {
        Write-Warning "无法写入 $target：$($_.Exception.Message)；可手动创建"
    }
}

try {
    if (-not $RepoDir) {
        $scriptRepo = if ($PSScriptRoot) { Split-Path (Split-Path $PSScriptRoot -Parent) -Parent }
        # A standalone downloaded copy has no repository two levels up; clone into the default location instead.
        if ($scriptRepo -and (Test-Path (Join-Path $scriptRepo "installer\aicj.py"))) {
            $RepoDir = $scriptRepo
        } else {
            $RepoDir = Join-Path $HOME "src\evoharness"
        }
    }
    $RepoDir = [IO.Path]::GetFullPath($RepoDir)

    if (-not $NoPrereqs) {
        if (-not (Test-Runnable "git")) { Install-WingetPackage "Git.Git" }
        $python = Get-Command py -ErrorAction SilentlyContinue
        if (-not $python) { Install-WingetPackage "Python.Python.3.12" }
        & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
        if ($LASTEXITCODE -ne 0) { Install-WingetPackage "Python.Python.3.12" }
        & py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)"
        Assert-ExitCode "Python 3.9+ 版本检查"
        if (-not $NoHud -and -not (Get-Command node -ErrorAction SilentlyContinue)) { Install-WingetPackage "OpenJS.NodeJS.LTS" }
        if (-not $NoClaude -and -not (Test-Runnable "claude")) {
            $broken = Get-Command claude -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($broken) { Write-Warning "已找到 $($broken.Source) 但无法运行；若是 npm 全局安装，请执行 npm uninstall -g @anthropic-ai/claude-code，避免它在 PATH 中挡住新安装的版本" }
            Write-Host "未找到可运行的 Claude Code，正在运行官方安装器..."
            Install-ClaudeCode
        }
    }

    if (Test-Path (Join-Path $RepoDir ".git")) {
        & git -C $RepoDir pull --ff-only
        Assert-ExitCode "仓库更新"
    } else {
        $parent = Split-Path $RepoDir -Parent
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
        & git clone $RepoUrl $RepoDir
        Assert-ExitCode "仓库克隆"
    }

    if ($Mode -eq "worker") {
        if ($InstallCpa) {
            $cpaScript = Join-Path $RepoDir "scripts\windows\install-cpa.ps1"
            if (-not (Test-Path $cpaScript)) { throw "找不到 install-cpa.ps1" }
            $port = ([Uri]$CpaUrl).Port
            $cpaInstallArgs = @("-Port", $port, "-InstallDir", $CpaDir)
            if ($HomeDir) { $cpaInstallArgs += "-NoUserEnv" }
            & powershell -ExecutionPolicy Bypass -File $cpaScript @cpaInstallArgs
            Assert-ExitCode "CPA 安装"
        }
        $explicitToken = $PSBoundParameters.ContainsKey("CpaToken")
        $userToken = [Environment]::GetEnvironmentVariable("AICJ_EXECUTOR_TOKEN", "User")
        $configToken = if ($InstallCpa) { Get-CpaApiKey (Join-Path $CpaDir "config.yaml") } else { "" }
        $token = if ($explicitToken) { $CpaToken } elseif (-not [string]::IsNullOrWhiteSpace($configToken)) { $configToken } elseif (-not [string]::IsNullOrWhiteSpace($userToken)) { $userToken } else { "" }
        if ([string]::IsNullOrWhiteSpace($token)) {
            if ($env:CI -or [Console]::IsInputRedirected) {
                throw "非交互会话未提供 CPA token；请传 -CpaToken 或改用 -Mode cc"
            }
            $secure = Read-Host "请输入 CPA token" -AsSecureString
            $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
            try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
        }
        Test-Cpa $CpaUrl $token
        Set-SessionOrUser "AICJ_EXECUTOR_BASE_URL" $CpaUrl.TrimEnd("/") $PSBoundParameters.ContainsKey("CpaUrl")
        Set-SessionOrUser "AICJ_EXECUTOR_TOKEN" $token $explicitToken
        foreach ($model in @(@("AICJ_EXECUTOR_MODEL_HIGH", "opus"), @("AICJ_EXECUTOR_MODEL_MID", "sonnet"), @("AICJ_EXECUTOR_MODEL_LOW", "haiku"))) {
            $current = [Environment]::GetEnvironmentVariable($model[0], "User")
            if ([string]::IsNullOrWhiteSpace($current)) {
                Set-SessionOrUser $model[0] $model[1] $false
            } else {
                [Environment]::SetEnvironmentVariable($model[0], $current, "Process")
                Write-Host "$($model[0]) 已存在，保留现值：$current"
            }
        }
    }

    Push-Location $RepoDir
    try {
        $homeArgs = @()
        if ($HomeDir) { $homeArgs = @("--home", $HomeDir) }
        $adapterNames = @($Adapters -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($Mode -eq "worker" -and $adapterNames -notcontains "executor") { $adapterNames += "executor" }
        if (-not [string]::IsNullOrWhiteSpace($TeamConfig) -and $adapterNames -notcontains "lesson") { $adapterNames += "lesson" }
        $installArgs = @("install") + $homeArgs
        if ($adapterNames.Count -gt 0) { $installArgs += "--adapters"; $installArgs += (($adapterNames | Select-Object -Unique) -join ",") }
        if ($NoHud) { $installArgs += "--no-hud" }
        if (-not [string]::IsNullOrWhiteSpace($TeamConfig)) {
            $installArgs += "--team-config"; $installArgs += $TeamConfig
            Test-CollectRepoAccess $TeamConfig
        }
        & py -3 installer/aicj.py @installArgs
        Assert-ExitCode "aicj 安装"
        Install-CcLauncher
        $selftestArgs = @("selftest") + $homeArgs
        if (-not $NoClaude) { $selftestArgs += "--with-claude" }
        & py -3 installer/aicj.py @selftestArgs
        $selftestCode = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $displayHome = if ($HomeDir) { $HomeDir } else { $HOME }
    $installDir = Join-Path $displayHome ".claude"
    $meaning = if ($Mode -eq "worker") { "worker：B/C 档先经 CPA 使用外部执行体，失败才降级为子代理。" } else { "cc：B/C 档直接使用 Claude Code 子代理。" }
    if ($selftestCode -eq 0) {
        Write-Host "aicj Windows 一键安装与自检完成。安装目录：$installDir；重新自检：cd `"$RepoDir`"; py -3 installer/aicj.py selftest --home `"$displayHome`"。当前模式：$Mode。$meaning"
    } else {
        Write-Host "aicj 安装完成，但自检失败（退出码 $selftestCode）。安装目录：$installDir；重新自检：cd `"$RepoDir`"; py -3 installer/aicj.py selftest --home `"$displayHome`"。当前模式：$Mode。$meaning"
    }
    Complete-Script $selftestCode
} catch {
    Write-Host ("Windows 一键安装失败：" + $_.Exception.Message) -ForegroundColor Red
    Complete-Script 1
}

