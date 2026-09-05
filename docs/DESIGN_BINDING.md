# 现有工程需求绑定 / Existing design binding

`bind_requirement_review_to_design` 是现有电路进入优化前的只读检查层。它接收一个
`CircuitDesign` 快照和已经通过 `review_design_requirements` 的契约，检查需求中的信号
是否能在快照的节点或元件中找到，并列出可进入有界参数优化的 R/C/L 元件。

## 绑定规则

- `V(node)`：匹配 `CircuitDesign.nets` 中的节点；`V(node,ref)` 同时检查参考节点；
- `I(R1)`：匹配元件参考标号，不要求该元件必须是 R；
- 其他信号文本：状态为 `needs-explicit-alias`，可通过 `signal_aliases` 映射到上述形式；
- 绑定只读，不写回 `.ms14`、源网表、输入输出配置或仿真状态；
- `contract_digest` 会被带入绑定结果，绑定结果自身也有 `binding_digest`。

## 返回状态

- `ready-for-baseline`：所有需求信号均已绑定，可以进入基线实验；
- `needs-signal-binding`：至少一个信号缺失或需要别名，应先补充命名或探针；
- `optimizable_parameters`：仅列出具有标量值且属于 R/C/L 家族的候选，真正的优化仍受
  `optimize_design` / `global_optimize_design` 的变量域、预算和硬约束限制。

## 与 `.ms14` 的边界

当前版本不会直接解析或修改任意 `.ms14` XML。Windows Multisim 连接器或已有工程导入器
应先生成经过校验的 `CircuitDesign` 快照，再调用此绑定工具。这样可以把工程读取、需求
解释和文件写回分开审计；后续将增加 COM 枚举到快照的适配器，并继续保留人工确认门。

## English summary

`bind_requirement_review_to_design` is a read-only pre-optimization binding layer. It matches
`V(node)` and `I(refdes)` requests against a validated `CircuitDesign` snapshot, accepts
explicit signal aliases, and reports bounded R/C/L value candidates. It does not edit `.ms14`,
netlists, simulator state, or source files. Arbitrary `.ms14` parsing remains a separate,
audited importer boundary.
