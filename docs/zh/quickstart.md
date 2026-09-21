# 安装与快速入门

## 安装

选择与系统对应的 Windows x64、Mac Apple Silicon arm64 或 Mac Intel x86_64 压缩包，核对发布的 SHA-256 后完整解压。
保持程序文件夹完整，不要单独移动可执行文件。程序和运行中的数据库放在本机非同步目录。

Windows：在解压后的程序目录启动 `CoronaryAnnotationStudio.exe`。

Mac：将 `Coronary Annotation Studio.app` 放到本机目录或 Applications，再打开。
首版采用 ad-hoc 签名；若系统拦截首次打开，可按系统正常的“隐私与安全性 → 仍要打开”流程处理，详见
[Apple 说明](https://support.apple.com/en-us/102445)。不需要关闭系统安全保护。
实际测试过的系统版本以[验收报告](../acceptance.md)为准；Apple Silicon 上的 Rosetta 测试会明确标识，不能替代 Intel 实机验收。

目前实际运行测试覆盖 Windows 11 Pro x64，以及 Mac mini 上的 macOS 26.2（arm64 原生与 x86_64 Rosetta）。
Mac 包的最低部署版本为 arm64 12.3、x86_64 12.0；这些较旧系统尚未做实际运行验收。

从源码启动：

```console
python -m venv .venv
python -m pip install .
python -m annotation_app validate examples/synthetic-v1
python -m annotation_app --package examples/synthetic-v1
```

先激活虚拟环境再执行安装和启动命令。Windows PowerShell 使用 `.venv\Scripts\Activate.ps1`，Mac 使用 `source .venv/bin/activate`。

## 完成一次标注

1. 打开包含 `manifest.json` 的 `examples/synthetic-v1` 文件夹。示例包含单支、多支和明确共享 LM 三种情况。
2. 设置读者编号，选择病例和路径。相同项目中使用相同读者编号表示继续该读者的工作。
3. 沿 CPR 滚动，观察横截面与原始 CT 联动。参考线只用于定位，不表示诊断。
4. 拖动橙色区间边界或输入起止毫米位置，填写病变状态、适用的斑块组成、狭窄等级和置信度，然后应用。
5. 点击已有区间可修改或复核。新应用的重叠区间覆盖同一范围内的旧区间，旧记录保留在审计中。被裁切的阳性摘要可能需要逐段确认。
6. 用“下一处未标/复核”定位缺口。完成检查只针对项目声明的标注范围。
7. 保留一份未应用草稿后切换语言，病例、草稿、选中区间和已保存标签应保持不变。退出再打开可恢复。
8. 导出本例或整批。保留结果文件夹中的 JSON、CSV、审计与校验文件。导出不会清空标签，也不会将草稿变成正式标签。

| 状态 | 含义 |
| --- | --- |
| 未标注 | 尚未明确记录此区域的判断 |
| 阴性 | 读者明确判断该区间无斑块 |
| 阳性 | 读者记录有斑块及其相关字段 |
| 不确定 | 暂时无法作出确定判断 |
| 不可评估 | 该区间无法完成评估 |

## 恢复与离线导出

重新打开同一项目、同一读者，可恢复保存的草稿与工作状态。应用过的标签及撤销/重做历史使用 SQLite 事务保存。
原始影像暂时断开时仍可导出已存标签：

```console
python -m annotation_app export --db /local/project/annotations.sqlite --reader READER-A --output /local/exports
```

批次总清单为 `batch-manifest.json`。缺少有效总清单的中断导出不能视为完整结果，应重新导出到新目录。
