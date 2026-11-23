# EngineerDiff 网页版

免安装的浏览器版本：TXT / CSV / XLSX / XLS 单文件与批量比较，支持手机查看与选择文件、PWA 离线。

- 在线地址：<https://youyoudezhuzhu.github.io/EngineerDiff/>
- 完整说明：[docs/web-version.md](../docs/web-version.md)

本地运行：

```bash
python -m http.server 8765 --directory web
```

测试：

```bash
node --test web/tests/*.test.mjs
```

目录与实现说明见 [docs/web-version.md](../docs/web-version.md)。
