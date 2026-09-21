# v0.1.0 验收记录

2026-09-21，Windows x64、Mac Apple Silicon 和 Mac Intel 三种程序包均已完成本地技术验收。
每种程序都从最终 ZIP 重新解压，完成“打开合成示例—标注—保存—退出—恢复—导出”。
Mac 成品通过 OneDrive 不可变文件清单传回，并在接收端重新核对 SHA-256；Windows 端没有解包或重打包 Mac 应用。

| 验收项目 | 结果 |
| --- | --- |
| Windows 自动化测试 | 201 项中 200 项通过，1 项仅适用于 macOS 的 `ditto` 检查明确跳过 |
| Mac arm64 / x86_64 自动化测试 | 每个架构各 201 项通过，无失败或跳过 |
| 三种最终 ZIP 的独立进程工作流 | 创建阶段各 25 项、重启恢复与导出阶段各 33 项通过 |
| 英文与中文布局 | Windows 219 项、Mac 每个架构 237 项检查；实际截图已查看 |
| Mac 架构、依赖及 ad-hoc 签名 | 每个程序各 131 个 Mach-O 文件通过检查 |
| 真实 Slicer 非线性映射 | 独立 6,000 个采样点，最大误差 0.009696 voxel，小于预设 0.1 voxel |
| 最终程序恢复后的原始 CT 定位 | 最大已保存锚点误差 0.0 voxel |
| 源码与分发材料 | 默认英文、运行时切换中文；源码、许可证、依赖与对应源码材料、合成示例及校验清单齐全 |

实际运行测试系统为 Windows 11 Pro x64 和 Mac mini 上的 macOS 26.2。
Intel 包在 Apple Silicon 上通过 Rosetta 运行，尚未在 Intel 物理设备上验证。
Mac 的最低部署版本为 arm64 12.3、x86_64 12.0，较旧系统尚未做运行测试。
Mac 使用 ad-hoc 签名，未做 Developer ID 公证；Windows 程序未作 Authenticode 签名。
这些结果是技术验收，不代表临床或医生验收。

完整哈希及结构化记录见[验收 JSON](../release-acceptance.json)，详细说明见[英文验收记录](../acceptance.md)。
[GitHub 托管 CI](https://github.com/RvMe/coronary-annotation-studio/actions/runs/35636962867)
已在 Windows、Mac arm64 和 Mac Intel runner 上通过，每组运行 201 项测试（Windows 跳过 1 项 macOS 专属测试）。
双语文档已经部署到 [GitHub Pages](https://rvme.github.io/coronary-annotation-studio/)。
公开下载文件的核对结果记录在[发布附件中的最新验收 JSON](https://github.com/RvMe/coronary-annotation-studio/releases/download/v0.1.0/RELEASE-ACCEPTANCE.json)。
托管 runner 的源码测试不替代上述最终程序 ZIP 的本地窗口验收。
