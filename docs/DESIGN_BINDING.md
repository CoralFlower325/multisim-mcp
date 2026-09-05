# 现有工程需求绑定 / Existing design binding

`bind_requirement_review_to_design` 是现有电路进入优化前的只读检查层。它接收一个
`CircuitDesign` 快照和已经通过 `review_design_requirements` 的契约，检查需求中的信号
是否能在快照的节点或元件中找到，并列出可进入有界参数优化的 R/C/L 元件。
若同时传入 `snapshot_evidence`，工具会强制校验快照中的元件交叉校验和原生身份审查；
仅“连接关系表缺少可执行参数”这一已知边界可进入原生 COM 规划，其余边界都会阻止绑定。

## 绑定规则

- `V(node)`：匹配 `CircuitDesign.nets` 中的节点；`V(node,ref)` 同时检查参考节点；
- `I(R1)`：匹配元件参考标号，不要求该元件必须是 R；
- 其他信号文本：状态为 `needs-explicit-alias`，可通过 `signal_aliases` 映射到上述形式；
- 绑定只读，不写回 `.ms14`、源网表、输入输出配置或仿真状态；
- `contract_digest` 会被带入绑定结果，绑定结果自身也有 `binding_digest`。
- 带有原生快照时，结果还会返回 `native_optimization_readiness`，列出可通过 COM
  `SetRLCValue` 扫描的候选，并要求运行时门禁、显式审批和恢复原值。

## 返回状态

- `ready-for-baseline`：所有需求信号均已绑定，可以进入基线实验；
- `needs-signal-binding`：至少一个信号缺失或需要别名，应先补充命名或探针；
- `optimizable_parameters`：仅列出具有标量值且属于 R/C/L 家族的候选，真正的优化仍受
  `optimize_design` / `global_optimize_design` 的变量域、预算和硬约束限制。

## 与 `.ms14` 的边界

`snapshot_open_circuit` 可在 Windows COM 工作者中对当前已打开的 Multisim 工程执行安全的
`ReportNetlist` 导出，并将解析后的 `CircuitDesign` 与 `circuit_info`、元件/输入/输出枚举
证据写入独立目录。它不会覆盖源 `.ms14`；输出目录必须是新的快照目录。
Multisim 14.x 的默认 `fmt=0` 可能返回连接关系表而非 SPICE；系统会识别并导入其中的
网络/元件/引脚拓扑，但会把缺失的值、模型和隐藏引脚标为人工审查边界，不会把它当作
可直接优化的完整设计。
快照还包含 `cross_validation`：解析网表中的参考标号必须与 COM 元件枚举一致，否则
`next_step` 为 `review_snapshot_mismatch`，不能直接进入需求绑定。
`boundary_review` 会额外标出模型缺失、多端/耦合器件隐藏引脚以及未结构化网表记录；
存在这些发现时，下一步为 `review_snapshot_boundaries`，优化安全标志为 false。
快照同时写入 `snapshot_digest`。后续进程可调用 Python API
`load_existing_design_snapshot(path)` 重新加载；它会限制文件大小、拒绝符号链接，
并在返回 `CircuitDesign` 前校验摘要，服务器追加的输出路径等临时字段不会影响校验。
对连接关系表中的 R/C/L 元件，`snapshot_open_circuit` 还会通过 COM `RLCValue` 补回
实际数值，并在 `parameter_evidence` / `parameter_coverage` 中记录成功和失败；这只补
参数证据，不会自动放宽模型、隐藏引脚或未命名元件的人工审查门禁。
当源文件是本机可读的 `.ms14` 时，工具会在临时目录解码其 XML，使用
`CIRToInfoMapItem` 将内部编号映射到界面参考标号，并提取器件类型、数据库身份、
厂家和端口清单。快照只保存身份字段和模型/模板 SHA-256，不保存模型正文。
只有端口清单与连接报告完全一致时，才会解除对应器件的隐藏引脚告警。
COM 未枚举且只连接一个网络的 `_uc...` 记录会作为 Multisim 报告辅助标记记录，
不会再误计为实际元件。

当前版本仍不会直接解析或修改任意 `.ms14` XML。若 Multisim 导出的网表包含当前解析器不
支持的记录，默认会失败关闭；只有使用者明确设置 `allow_unsupported=true` 才会保留受限
快照，并在设计注释中记录未支持项。这样可以把工程读取、需求解释和文件写回分开审计，
后续再逐步增加 COM 属性到节点/参数的更完整映射。

## English summary

`snapshot_open_circuit` exports the currently open Multisim circuit through the isolated COM
worker into a new directory, parses the reported netlist, and preserves enumeration evidence.
Multisim 14.x may return a connectivity table for the default report format; the importer keeps
its bounded topology while explicitly blocking optimization until values, models, and hidden
pins are confirmed.
`bind_requirement_review_to_design` is a read-only pre-optimization binding layer. It matches
`V(node)` and `I(refdes)` requests against a validated `CircuitDesign` snapshot, accepts
explicit signal aliases, and reports bounded R/C/L value candidates. It does not edit `.ms14`,
netlists, simulator state, or source files. Persisted snapshots carry a digest and can be
reloaded with `load_existing_design_snapshot(path)` for integrity-checked, cross-process
workflows. Arbitrary `.ms14` parsing remains a separate, audited importer boundary.
For R/C/L entries in a connectivity report, `snapshot_open_circuit` also records verified COM
`RLCValue` readings in `parameter_evidence`; this improves parameter visibility without
silently approving missing models or hidden pins.
When a verified native snapshot is supplied, the binding also returns
`native_optimization_readiness` for a future COM `SetRLCValue` sweep. The plan requires a
runtime gate, explicit approval, and restoration of original values; missing native evidence
keeps the state at `manual-review-required`.
For a readable local `.ms14`, the tool decodes a temporary copy and maps internal identifiers
through `CIRToInfoMapItem`. Only component identity, port inventory, and model/template hashes
are retained; licensed model bodies are never embedded in the snapshot.
