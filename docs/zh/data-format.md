# 数据格式与 Slicer 接入

首版输入必须同时具有已有三维 CPR、原始 CT 和空间映射。原始 CT 与 CPR 支持 NIfTI/NRRD。
截图、MIP、二维投影以及没有几何信息的图像不受支持。
影像须为完整的 `.nii`、`.nii.gz` 或 `.nrrd` 单文件；引用外部像素文件的分离式 NRRD 须先重新导出为完整 `.nrrd`，确保像素内容包含在文件校验中。

## 中立数据包

根目录使用 `cas-package-1.0` 的 `manifest.json`，明确 `project_id` 和病例清单。
病例清单中的 `case_manifest` 指向 `cas-case-1.0` 病例文件。病例必须重复声明一致的项目/病例身份。
不同来源即使病例编号相同，也应采用不同项目编号。

病例文件必须包含：

- `project_id`、`case_id`、`geometry_id`：稳定身份；
- `units: mm`、`coordinate_system: LPS`、`intensity_units: HU`：显式单位；
- `native`、`paths`：原始 CT 和实际提供的路径；
- `annotation_scope`：纳入标注和覆盖率的路径编号；
- `files`：影像、映射等必要文件的相对路径、字节数和 SHA-256。

`metadata` 中的数据集和 split 均可选，不会自动补写或重新划分。路径可为单支或多支，不要求固定三支齐全。
编号和字段枚举不随界面语言改变。完整字段说明见[英文格式文档](../data-format.md)，合成示例提供可运行模板。

```console
python -m annotation_app validate /path/to/package
```

验证失败会指出具体路径和原因。不要只修改校验和来掩盖损坏，应核对来源文件。

## 空间映射

数组轴序固定为 `s,v,u`，对应沿程、横截面行、横截面列。沿程位置使用毫米，可非均匀采样，但必须严格递增并从零开始。

`frames` 保存逐截面的中心、法线、副法线和切线，要求右手正交坐标系与相同的横截面采样间距。
`coordinate_field` 保存 `(N,H,W,3)` 的绝对 native LPS 毫米坐标，可表达非线性变换，并支持不同的 u/v 间距。
坐标场采用三线性插值；超出范围不会外推。CPR 图像的局部 affine 不能替代原始 CT 的空间映射。

折叠或交叉路径可能存在多解，回定位需明确路径和局部位置上下文，不返回伪唯一结果。

共享 LM 只有在显式声明关系、来源证据、范围和 LAD 编辑归属，且 LAD/LCX 几何验证通过后才去重。
不会根据文件名或距离接近推断解剖归属。

## Slicer 导出已有 CPR

在 Slicer 中准备原始 CT、已生成的三维 Straightened CPR 及对应变换。
首版要求 CPR 的 K 轴沿路径，所有父变换已明确处理；适配器发现未处理的父变换会拒绝导出。
适配器本身不生成中心线或 CPR。

在 Slicer Python 控制台运行：

```python
import sys
sys.path.insert(0, "/path/to/coronary-annotation-studio/tools")
from slicer_export import export_package
report = export_package(
    slicer.util.getNode("Native CT"),
    [{"path_id": "coronary-A",
      "cpr_node": slicer.util.getNode("Straightened CPR"),
      "transform_node": slicer.util.getNode("Straightening transform"),
      "mapping_direction": "from_parent"}],
    "/new/local/package-directory",
    project_id="my-project-v1", case_id="case-001", geometry_id="geometry-v1",
    intensity_units="HU"
)
print(report)
```

`from_parent` 对应 SlicerSandbox CurvedPlanarReformat 的变换方向；自行提供的正向 CPR→native 变换需明确选择 `to_parent`。
导出器实际采样非线性变换，并执行 RAS→LPS 转换，不以普通 affine 近似代替。
与 Slicer 变换的亚体素采样比较，native 连续索引空间最大误差上限为 **0.1 voxel**，报告记录实际最大值。
误差超限时不完成数据包，需提供更细的既有 CPR 网格。这项数值检查不代表临床认可。

导入后，仍需在起点、末端、弯曲段和离轴位置目视核对原始 CT 联动。
