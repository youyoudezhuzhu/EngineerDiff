<#
.SYNOPSIS
    EngineerDiff Windows 构建脚本：PyInstaller one-dir -> 自检 -> 便携 zip -> Inno Setup 安装包 -> SHA-256。

.DESCRIPTION
    在项目根目录执行（也可在 CI 中调用）：
        powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1

    产物：
        dist\EngineerDiff\EngineerDiff.exe               PyInstaller one-dir
        release\EngineerDiff-1.2.6-Setup-x64.exe         Inno Setup 安装包
        release\EngineerDiff-1.2.6-portable-win64.zip    便携版 zip
        release\sha256sums.txt                           以上文件的 SHA-256
        build\windows-build-summary.json                  构建信息（版本/大小/哈希）

    参数：
        -SkipTests        跳过 pytest（CI 已单独跑过测试时使用）
        -SkipInstaller    只构建 dist + 便携 zip，不生成安装包
        -IsccPath <path>  指定 ISCC.exe（默认自动探测，必要时用 Chocolatey 安装）
        -Python <path>    指定 Python 解释器（默认优先 .venv\Scripts\python.exe）

.NOTES
    构建日志与产物不要提交 git（dist/ build/ release/ 已在 .gitignore 中）。
    真实 Windows 构建结果以 GitHub Actions windows-latest 为准。
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [string]$IsccPath = '',
    [string]$Python = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $PSScriptRoot
$specPath = Join-Path $root 'packaging\engineerdiff.spec'
$issPath = Join-Path $root 'packaging\engineerdiff.iss'
$distRoot = Join-Path $root 'dist'
$distDir = Join-Path $distRoot 'EngineerDiff'
$releaseDir = Join-Path $root 'release'
$buildDir = Join-Path $root 'build'

function Invoke-Native {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments
    )
    Write-Host "> `"$FilePath`" $($Arguments -join ' ')" -ForegroundColor DarkGray
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "命令失败（exit=$LASTEXITCODE）：$FilePath $($Arguments -join ' ')"
    }
}

function Get-Sha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

Push-Location $root
$summary = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    project = 'EngineerDiff'
    host = $env:COMPUTERNAME
    python = ''
    version = ''
    pyinstaller = ''
    iscc = ''
    dist_exe = ''
    installer = ''
    portable_zip = ''
    sha256 = @{}
}
try {
    # ------------------------------------------------------------------
    # 0. Python 解释器与依赖断言
    # ------------------------------------------------------------------
    if (-not $Python) {
        $venvPython = Join-Path $root '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $venvPython) {
            $Python = $venvPython
        }
        else {
            $Python = (Get-Command python -ErrorAction Stop).Source
        }
    }
    Write-Host "Python: $Python"
    Invoke-Native $Python @('--version')
    $summary.python = (& $Python -c "import sys; print(sys.version.split()[0])").Trim()

    Invoke-Native $Python @((Join-Path $root 'scripts\check_spec.py'))
    Invoke-Native $Python @((Join-Path $root 'scripts\check_deps.py'))

    $version = (& $Python -c "import sys; sys.path.insert(0, r'$($root -replace '\\','/')/src'); from engineerdiff import version; print(version.__version__)").Trim()
    $summary.version = $version
    $summary.pyinstaller = (& $Python -m PyInstaller --version).Trim()
    Write-Host "EngineerDiff 版本：$version（PyInstaller $($summary.pyinstaller)）" -ForegroundColor Cyan

    # ------------------------------------------------------------------
    # 1. 测试（可选）
    # ------------------------------------------------------------------
    if (-not $SkipTests) {
        $env:QT_QPA_PLATFORM = 'offscreen'
        Invoke-Native $Python @('-m', 'pytest', '-q')
    }

    # ------------------------------------------------------------------
    # 2. PyInstaller one-dir 构建
    # ------------------------------------------------------------------
    if (Test-Path -LiteralPath $distDir) {
        Remove-Item -LiteralPath $distDir -Recurse -Force
    }
    Invoke-Native $Python @('-m', 'PyInstaller', '--noconfirm', '--clean', '--distpath', $distRoot, '--workpath', $buildDir, $specPath)

    $exePath = Join-Path $distDir 'EngineerDiff.exe'
    if (-not (Test-Path -LiteralPath $exePath)) {
        throw "PyInstaller 构建后未找到 $exePath"
    }
    $summary.dist_exe = $exePath

    # VC++ 运行库再分发 DLL：spec 已收集到 _internal；这里再复制到 dist 根目录，
    # 确保 Windows 加载器优先从 exe 同目录解析 msvcp140/vcruntime140 等依赖。
    $system32 = Join-Path $env:SystemRoot 'System32'
    # 显式白名单：仅 release 版 VC++ 运行库（不要 msvcp140d* / CLR 专用 DLL）。
    $vcNames = @(
        'msvcp140.dll', 'msvcp140_1.dll', 'msvcp140_2.dll',
        'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll',
        'vcruntime140.dll', 'vcruntime140_1.dll',
        'concrt140.dll', 'vcomp140.dll'
    )
    $vcCopied = @()
    foreach ($name in $vcNames) {
        $dll = Join-Path $system32 $name
        if (Test-Path -LiteralPath $dll) {
            Copy-Item -LiteralPath $dll -Destination (Join-Path $distDir $name) -Force
            $vcCopied += $name
        }
    }
    foreach ($required in @('msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')) {
        if (-not (Test-Path -LiteralPath (Join-Path $distDir $required))) {
            throw "缺少 VC++ 运行库 DLL：$required（请安装 Microsoft Visual C++ 2015-2022 Redistributable x64）"
        }
    }
    Write-Host "已复制 VC++ 运行库 DLL 到 dist 根目录：$($vcCopied -join ', ')" -ForegroundColor DarkGray

    # ------------------------------------------------------------------
    # 3. 许可证 + 交付文档（放到 dist 根目录，便携版可直接阅读）
    # ------------------------------------------------------------------
    Invoke-Native $Python @((Join-Path $root 'scripts\collect_licenses.py'), '--dist', $distDir, '--strict')
    $docsDir = Join-Path $distDir 'docs'
    New-Item -ItemType Directory -Path $docsDir -Force | Out-Null
    foreach ($doc in @('user-guide.md', 'known-limitations.md', 'third-party-licenses.md', 'technical-selection.md', 'windows-defender.md', 'code-signing.md', 'ocr-performance.md', 'architecture.md')) {
        $src = Join-Path $root "docs\$doc"
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $docsDir $doc) -Force
        }
    }
    $shotsSrc = Join-Path $root 'docs\screenshots'
    if (Test-Path -LiteralPath $shotsSrc) {
        Copy-Item -LiteralPath $shotsSrc -Destination (Join-Path $docsDir 'screenshots') -Recurse -Force
    }
    Copy-Item -LiteralPath (Join-Path $root 'LICENSE') -Destination (Join-Path $distDir 'LICENSE') -Force
    Copy-Item -LiteralPath (Join-Path $root 'README.md') -Destination (Join-Path $distDir 'README.md') -Force

    # ------------------------------------------------------------------
    # 4. 校验产物 + 运行打包后的 --self-test
    # ------------------------------------------------------------------
    $env:QT_QPA_PLATFORM = 'offscreen'
    Invoke-Native $Python @(
        (Join-Path $root 'scripts\verify_bundle.py'),
        '--dist', $distDir,
        '--exe', $exePath,
        '--require-frozen',
        '--json-output', (Join-Path $buildDir 'verify-bundle.json'),
        '--self-test-report-output', (Join-Path $buildDir 'self-test-report.json')
    )

    # ------------------------------------------------------------------
    # 5. 便携版 zip + SHA-256
    # ------------------------------------------------------------------
    New-Item -ItemType Directory -Path $releaseDir -Force | Out-Null
    $portableZip = Join-Path $releaseDir "EngineerDiff-$version-portable-win64.zip"
    if (Test-Path -LiteralPath $portableZip) { Remove-Item -LiteralPath $portableZip -Force }
    Write-Host "打包便携版：$portableZip" -ForegroundColor Cyan
    Compress-Archive -Path (Join-Path $distDir '*') -DestinationPath $portableZip -CompressionLevel Optimal
    $summary.portable_zip = $portableZip
    $summary.sha256[[System.IO.Path]::GetFileName($portableZip)] = Get-Sha256 $portableZip

    # ------------------------------------------------------------------
    # 6. Inno Setup 安装包
    # ------------------------------------------------------------------
    if (-not $SkipInstaller) {
        if (-not $IsccPath) {
            $IsccPath = & (Join-Path $root 'scripts\install_inno.ps1')
            if ($LASTEXITCODE -ne 0 -or -not $IsccPath) {
                throw '未找到可用的 Inno Setup（ISCC.exe）'
            }
        }
        if (-not (Test-Path -LiteralPath $IsccPath)) {
            throw "ISCC 路径不存在：$IsccPath"
        }
        # ISCC.exe 的版本资源为 0.0.0.0（Inno Setup 未写入 FileVersion），
        # 不能用 FileVersion 判断版本；兼容性由实际编译验证。
        $summary.iscc = "ISCC: $IsccPath"
        Write-Host "ISCC: $IsccPath" -ForegroundColor Cyan
        # 在 packaging 目录下运行 ISCC：Inno 的自定义语言文件使用
        # "languages\ChineseSimplified.isl"（相对当前源目录），同时 .iss 中的
        # ..\dist、..\release 等相对路径仍然正确。
        Push-Location (Join-Path $root 'packaging')
        try {
            Invoke-Native $IsccPath @('/Qp', $issPath)
        }
        finally {
            Pop-Location
        }

        $installerPath = Join-Path $releaseDir "EngineerDiff-$version-Setup-x64.exe"
        if (-not (Test-Path -LiteralPath $installerPath)) {
            throw "ISCC 未生成预期安装包：$installerPath"
        }
        $summary.installer = $installerPath
        $summary.sha256[[System.IO.Path]::GetFileName($installerPath)] = Get-Sha256 $installerPath
    }

    # ------------------------------------------------------------------
    # 7. sha256sums.txt + 构建摘要
    # ------------------------------------------------------------------
    $hashLines = @()
    foreach ($name in ($summary.sha256.Keys | Sort-Object)) {
        $hashLines += "$($summary.sha256[$name])  $name"
    }
    $hashFile = Join-Path $releaseDir 'sha256sums.txt'
    $hashLines | Set-Content -LiteralPath $hashFile -Encoding ascii
    Write-Host "SHA-256: $hashFile" -ForegroundColor Green

    $summaryFile = Join-Path $buildDir 'windows-build-summary.json'
    $summary | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryFile -Encoding utf8

    Write-Host ''
    Write-Host '========== 构建完成 ==========' -ForegroundColor Green
    Write-Host "  one-dir  : $distDir"
    if ($summary.portable_zip) { Write-Host "  portable : $($summary.portable_zip)" }
    if ($summary.installer) { Write-Host "  installer: $($summary.installer)" }
    Write-Host "  sha256   : $hashFile"
    Write-Host "  summary  : $summaryFile"
    Get-Content -LiteralPath $hashFile | ForEach-Object { Write-Host "    $_" }
}
finally {
    Pop-Location
}
