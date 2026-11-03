# Windows Defender / SmartScreen / Smart App Control 说明

EngineerDiff 是**未签名的开源 Windows 工具**。软件本身不包含联网、下载、持久化或
可疑行为；但 PyInstaller 打包的 exe 与 Inno Setup 安装包在**没有代码签名证书**时，
可能被 Microsoft Defender 的启发式规则或 SmartScreen 拦截/提示“未知发布者”。
这是所有未签名打包工具的常见问题，**并不表示文件真的包含恶意代码**。

> 根治办法只有一个：使用受信任的 **代码签名证书（EV/OV Code Signing）** 对
> `EngineerDiff.exe` 与安装包签名。项目本身无法在没有证书的情况下消除误报。

## 1. 先校验文件是否完整

从 GitHub Release 下载后，用 PowerShell 校验：

```powershell
Get-FileHash .\EngineerDiff-1.2.2-Setup-x64.exe -Algorithm SHA256
```

与 Release 页面或 `sha256sums.txt` 中的 SHA-256 对比；一致才继续。

## 2. 向 Microsoft 提交误报（推荐，影响所有用户）

1. 打开 Microsoft 安全 intelligence 文件提交页：
   <https://www.microsoft.com/en-us/wdsi/filesubmission>
2. 选择 “Submit a file for malware analysis” / “个人用户”或“企业用户”。
3. 上传被报的 `EngineerDiff.exe` 或安装包，说明：
   - “False positive: open-source offline diff tool, unsigned PyInstaller/Inno Setup build”；
   - 软件官网/仓库：<https://github.com/youyoudezhuzhu/engineerdiff>；
   - 附上 SHA-256 与 Release 链接。
4. Microsoft 分析后通常会更新签名库；之后其他用户不再被误报。

## 3. 本机临时排除（仅在确认文件哈希无误时）

Windows 安全中心 → 病毒和威胁防护 → 管理设置 → 排除项 → 添加排除项，加入
`EngineerDiff.exe`、安装包，或安装目录：

```text
%LOCALAPPDATA%\Programs\EngineerDiff
```

管理员 PowerShell 等价命令（**请确认路径无误**）：

```powershell
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\Programs\EngineerDiff"
# 撤销：
Remove-MpPreference -ExclusionPath "$env:LOCALAPPDATA\Programs\EngineerDiff"
```

不建议关闭实时保护或整盘排除；排除范围应尽可能小。

## 4. 使用便携版 / 重新下载

- 若安装包被拦，可尝试 `EngineerDiff-1.2.2-portable-win64.zip`；
- 删除隔离区中的旧文件后重新下载并校验 SHA-256，避免使用损坏/被改写的副本；
- 企业环境可通过 Intune/组策略下发签名后的版本，或添加应用白名单。

## 5. 为发布版签名（维护者）

如果拥有 `.pfx` 代码签名证书，可按以下方式签名（需 Windows SDK 的 `signtool.exe`）：

```powershell
$env:ENGINEERDIFF_SIGN_PFX = "C:\path\codesign.pfx"
$env:ENGINEERDIFF_SIGN_PASSWORD = "***"
powershell -ExecutionPolicy Bypass -File scripts\sign_release.ps1
```

脚本会签名 `dist\EngineerDiff\EngineerDiff.exe` 与 `release\EngineerDiff-1.2.2-Setup-x64.exe`
并做时间戳；签名后重新计算 `sha256sums.txt` 并重新上传 Release。

## 6. 已知限制

- 本项目当前**不包含**代码签名证书，因此不能保证 Defender/SmartScreen 永不提示；
- 安装包未签名时，SmartScreen 的“更多信息 → 仍要运行”是正常系统行为；
- 任何声称“完全绕过 Defender”的方案都不应作为软件分发策略。
