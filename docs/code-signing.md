# 代码签名与 Windows 11 智能应用控制（Smart App Control）

## 1. 截图中的提示是什么

如果 Windows 11 弹出：

> 智能应用控制已阻止可能不安全的应用
> 我们已阻止 ...，因为无法验证其发布者，无法确认它是否能安全运行。

这是 **Smart App Control（SAC）**，不是 Microsoft Defender 的病毒误报，也不是
SmartScreen 的“仍要运行”提示。SAC 的规则是：

1. 微软云安全服务能明确判断应用安全时放行；
2. 无法判断时，检查应用是否有**有效代码签名**；
3. **未签名或签名无效 → 阻止**。

微软官方明确说明：**无法针对单个应用绕过 SAC**，不能加入“白名单”，SAC 对话框只有
“正常”和“从 Microsoft Store 获得应用”两个按钮。

官方 FAQ：
<https://support.microsoft.com/en-us/windows/security/threat-malware-protection/smart-app-control-frequently-asked-questions>

## 2. 与 1.0.1 的差异说明

项目维护者已在本地检查 1.0.1/1.0.2 便携版中的 `EngineerDiff.exe`：

| 版本 | 文件大小 | Authenticode 证书表 | PE 结构 |
|---|---:|---|---|
| 1.0.1 | 6,796,210 B | 空（未签名） | 相同 |
| 1.0.2 | 6,822,234 B | 空（未签名） | 相同 |

两版**都没有代码签名**；1.0.2 只是新增批量比较模块导致文件哈希变化。SAC 对新哈希
尚无云端信誉，或系统在两次运行之间启用了 SAC，都会造成“上一版能运行、新版被拦”的
现象。这不是 1.0.2 功能代码引入的安全风险，也无法通过改 PyInstaller 参数、改名或
换便携/安装版绕过。

## 3. 正式解决：代码签名

需要对以下文件签名（签名必须在打包 zip 之前完成）：

1. `dist/EngineerDiff/EngineerDiff.exe`
2. 便携版 zip 内的 `EngineerDiff.exe`
3. `release/EngineerDiff-<version>-Setup-x64.exe`

可使用：

- 受信任的 OV/EV 代码签名证书（`.pfx`）；
- Microsoft Azure Trusted Signing；
- 开源项目可申请 SignPath Foundation 等开源签名服务（需审核）。

仓库已有 `scripts/sign_release.ps1`，但当前 CI **没有**签名证书，因此发布包未签名。
如提供证书/PFX 或 Trusted Signing 账号，可把签名接入 GitHub Actions：

```text
CODE_SIGN_PFX_BASE64
CODE_SIGN_PFX_PASSWORD
```

私钥不要提交到仓库或贴在聊天中；应放入 GitHub Actions Secrets，或仅在本机安全路径使用。

## 4. 当前测试机临时处理

如果这是**你信任的测试机**，可以在 Windows 安全中心关闭 SAC：

> Windows 安全中心 → 应用和浏览器控制 → 智能应用控制 → 关闭

注意：

- 这是系统级安全开关，会影响所有未签名/低信誉应用，不只是 EngineerDiff；
- 新版本 Windows 通常可以在 Windows 安全中心重新开启；较旧版本可能需要重置系统，
  请以 Microsoft 官方说明为准；
- 办公/生产/企业设备不建议关闭，应联系 IT 通过签名或 WDAC 策略处理；
- 不要通过修改文件名、解压后“解除锁定”、Defender 排除项或自制“免杀”方式绕过，
  这些对 SAC 无效，也会误导其他用户。

## 5. 替代测试环境

- 未启用 Smart App Control 的 Windows 10/11 测试机；
- 虚拟机；
- 等待签名版发布。

当前 [v1.2.7 Release](https://github.com/youyoudezhuzhu/engineerdiff/releases) 是未签名构建，
请仅在你信任来源的前提下核对 SHA-256 后使用。
