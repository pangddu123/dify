# ModelNet 开发任务清单

> **配套文档**：`DEVELOPMENT_PLAN.md` v2。本文档是把 v2 计划落到可勾选动作的执行清单。
> **路径决定**（2026-04-18）：ModelSpec / LlamaCppClient 放 `api/core/workflow/nodes/parallel_ensemble/llama_cpp/`（和 Phase 2 节点同包），不放 v2 计划写的 `api/core/model_runtime/local_models/` —— 因为 `api/core/model_runtime/` 在当前 fork 已不存在。
> **总任务数**：28（Phase 0: 2 / Phase 1: 8 / Phase 2: 15 / Phase 3: 3）

---

## 背景说明：每个阶段为什么存在

### Phase 0 — Spike（摸清未知）

**目的**：买架构确定性。

v2 计划里还有 4 个未闭环问题（NodeType 注册机制、基类自动注册、DifyNodeFactory 注入、前后端 schema 对齐）。graphon 是外部包（PyPI 0.1.2），源码不在仓库里。如果跳过 spike 直接写代码，万一 NodeType 不接受字符串字面量，Phase 1/2 的 `entities.py` 就得推翻重写。**花 0.5 天换掉这 4 个不确定，避免后面 10 天白干。**

产出只有一份 `SPIKE_GRAPHON.md`，不产出任何产品代码。

### Phase 1 — 响应级聚合节点（最小可用原型）

**目的**：用最简单的方式先出一个可用的"多模型并联"节点，顺便把 Dify 的前端注册流程跑一遍。

Dify 的 GraphEngine 本身就支持"一条边拉到 N 个下游"并发执行 —— 从 Start 拉 3 条边到 3 个 LLM，它们就已经并发跑了。**Phase 1 只是在下游加一个节点把 N 份输出合一**，本质是"N-入 1-出"的聚合器。

为什么把它放在 Phase 2 前面：

- **不改 llama.cpp 协议**：上游直接用 Dify 现成的 LLM 节点
- **不要流式**：等上游 LLM 都跑完了再聚合，只有一次 output
- **不要模型注册表**：模型 URL 走 Dify 原生的模型供应商抽象
- **验证 9 处前端注册路径**：这条路是 Phase 2 必经的，先在简单场景走一遍，排雷更便宜

产出：`ensemble-aggregator` 节点 + `majority_vote` + `concat` 两个策略 + 2 份 DSL。**不产出 logit 级融合**（那是 Phase 2 的事）。

### Phase 2 — Token 级并联节点（把 PN.py 产品化）

**目的**：把研究核心算法 —— **每步所有模型只前向 1 个 token，按 top-k 概率加权投票** —— 做成画布上能拖的节点。

为什么 Phase 2 独立存在，而且工时最大：

| 维度 | Phase 1 够不够 | Phase 2 必须解决 |
|---|---|---|
| 拿 logit | 不需要 | 必须直打 llama.cpp `/completion`，Dify 标准接口不暴露 top_probs |
| 流式 | 不需要 | 必须流式，每秒 5-15 token，非流式用户等 30s+ 空白屏 |
| SSRF 防护 | 无风险 | 有风险：URL 不能让工作流作者随便填（否则是内网扫描工具），必须收到服务端 yaml |
| 并发 | Dify 线程池管 | 节点内部自己管 ThreadPoolExecutor，锁步推进每个 token |

七个子阶段各有分工：

- **P2.1–2.3 注册表**：回答"模型 URL 放哪"（答：服务端 yaml，节点只引用别名）
- **P2.4 控制台 API**：前端模型下拉选项从哪里来（不返回 URL，ADR-3 隔离）
- **P2.5–2.7 engine + 聚合器**：算法核心，**完全不依赖 graphon**，可以独立 mock 测试
- **P2.8–2.10 节点 + 注入**：把算法套进 graphon 事件协议（selector、封口块、`node_run_result` 等坑都在这里）
- **P2.11–2.12 前端**：多选下拉 + 导入 model_info.json 按钮 + 9 处注册
- **P2.13–2.14 联调**：workflow 模式（→End）和 chat 模式（→Answer）两套跑一遍
- **P2.15 硬化**：性能 vs PN.py、异常路径、SSRF 回归

产出：`parallel-ensemble` 节点，在画布上拖 + 配置 2-3 个模型别名 + 流式输出到 End/Answer。**不产出 KV-cache 复用**（§1.2 非目标）。

### Phase 3 — 测试 / 文档 / 示例（让别人接得住）

**目的**：让这个 fork 能被你之外的人理解、使用、修改；让代码能进主干而不是一堆 WIP。

为什么放最后：

- **集成测试是 CI-only**（CLAUDE.md 规定），代码要进仓库但本地不跑，所以要等实现稳定
- **README / SECURITY**：要基于已经实现的行为写，提前写是空气文档、容易和实现不一致
- **i18n 全量 review**：要等所有 UI 稳定，才值得扫两套语言的 key 一致性

产出：4 份 DSL（workflow × chat × 响应级 × token 级）+ README（用法）+ SECURITY（URL 隔离机制）+ i18n 覆盖 en-US/zh-Hans。

### 阶段之间的依赖关系

```
Phase 0 spike  ──► 确认 NodeType / 注入路径
                   │
                   ▼
Phase 1 响应级  ──► 验证前端 9 处注册 + graphon 事件契约（非流式版）
                   │
                   ▼
Phase 2 token 级 ──► 引入模型注册表 + SSRF 防护 + 流式封口块
                   │
                   ▼
Phase 3 测试文档  ──► 收尾
```

**关键顺序原因**：Phase 0 若发现 NodeType 不支持字符串 → Phase 1 的 `type: NodeType = "ensemble-aggregator"` 写法要改；Phase 1 若前端注册踩坑 → 在响应级（逻辑简单）发现比在 token 级（流式 + 注入）发现便宜得多；Phase 2 若流式事件契约错 → 影响只到 token 级节点，不会污染已经稳定的响应级节点。

---

## Phase 0 — Spike（0.5 天）✅ **完成（2026-04-18，实耗 ~1.5h）**

> 产出：`docs/ModelNet/SPIKE_GRAPHON.md`；DEVELOPMENT_PLAN.md 同步到 v2.1；R1 closed、R10 mitigated。

### ✅ P0.1 验证 graphon 节点协议剩余未知项 (Q1/Q3/Q4/Q5)

跑 `uv run --project api python -c "import graphon, pathlib; print(pathlib.Path(graphon.__file__).parent)"` 找 graphon 源码；逐项验证：

- **Q1**: NodeType 是枚举还是别名，能否用字符串 `"ensemble-aggregator"` 注册？
- **Q3**: Node 基类自动注册机制（装饰器 / `__init_subclass__`）
- **Q4**: DifyNodeFactory:372-440 注入分支模式（参考 HTTP_REQUEST 注入 ssrf_proxy 的写法）
- **Q5**: 节点 schema 前后端一致性（Pydantic NodeData ↔ panel.tsx 提交的 JSON）

### ✅ P0.2 写 docs/ModelNet/SPIKE_GRAPHON.md 总结发现

把 Q1/Q3/Q4/Q5 的实测结论 + graphon 关键文件路径写进 SPIKE_GRAPHON.md。如果 Q1 发现字符串注册不可行，回 DEVELOPMENT_PLAN.md 改 ADR-1 / 6.1 entities.py 的 `type` 字段。

**实测结论摘要**：Q1 绿灯（`NodeType: TypeAlias = str`）→ ADR-1 不改；Q3 `__init_subclass__` 机制（`graphon/nodes/base/node.py:97`）；Q4 factory 需为 token 节点加分支并新建共享 `ThreadPoolExecutor`（`PARALLEL_ENSEMBLE_MAX_WORKERS` config）；Q5 字符串平凡对接。`self.id == self._node_id` 误述已更正（v2 说 `self.id` 是 execution id 错了）。

---

## Phase 1 — 响应级聚合节点（5–7 天）

### ✅ P1.1 后端：建 ensemble_aggregator 包骨架 + entities/exceptions (2026-04-18 初始；2026-04-19 review round 2 兜底)

新建 `api/core/workflow/nodes/ensemble_aggregator/{__init__.py, entities.py, exceptions.py}`。

- `__init__.py`: 包级常量 `ENSEMBLE_AGGREGATOR_NODE_TYPE = "ensemble-aggregator"`（仿 `knowledge_index/__init__.py:3`）；Node 类 P1.3 再补导出
- `entities.py`: `AggregationInputRef(source_id, variable_selector)` + `EnsembleAggregatorNodeData(BaseNodeData)`，含 `inputs min_length=2`、`strategy_config: dict[str, object]`、`@model_validator(mode="after")` 校验 source_id 唯一性
- `exceptions.py`: `EnsembleAggregatorNodeError` 基类 + `StrategyNotFoundError` / `MissingInputError` / `StrategyConfigError` 三个子类（仿 `agent/exceptions.py` 模式，带语义字段）
- **source_id 语义钉死为 user-defined stable alias**（非上游节点 id），见 DEVELOPMENT_PLAN.md v2.2
- 4 条初始验收命令全绿：正常 model_dump / min_length=2 拒 / 4 异常可导入 / 重复 source_id 拒
- **v2.3 review round 2 兜底**（2026-04-19）：`AggregationInputRef` 加 `ConfigDict(extra="forbid")`；`source_id: Field(min_length=1)` + `field_validator` 禁纯空白；`variable_selector: Field(min_length=2)` + `field_validator` 禁空白段（graphon `SELECTORS_LENGTH=2`，第 3 段起是路径故不设 max）；前置 `tests/.../ensemble_aggregator/test_entities.py` 14 条 schema mini-tests，14/14 绿
- 详见 `docs/ModelNet/P1.1_LANDING.md` §6 "review round 2 修订（v2.3）"

### ✅ P1.2 后端：写 strategies (base + registry + majority_vote + concat) (2026-04-19 初始；2026-04-19 review round 2 兜底)

在 `ensemble_aggregator/strategies/` 下：

- `base.py`: `AggregationStrategy` ABC（`name` / `config_schema` ClassVar + `__repr__`） + `AggregationInput` / `AggregationResult` TypedDict，`metadata: dict[str, object]`
- `registry.py`: `register` 装饰器（**幂等 guard**：同名重复注册 → `ValueError`，防开发期手误） + `get_strategy` 未注册 → `StrategyNotFoundError` + `list_strategies` 返回 `[{name, config_schema}]`
- `majority_vote.py`: 完全相同字符串投票；并列时取"tied 文本中最早投票者（按 source_id 字典序）最小"者（`earliest_voter` 字典扫一遍 + `min(tied, key=...)`），保证确定性与输入顺序无关；metadata 带 `votes` / `winner_votes` / `tie_break_applied` / `contributions`
- `concat.py`: 内部 `_ConcatConfig(BaseModel, extra="forbid")` 承载 `separator` (默认 `"\n\n---\n\n"`) + `include_source_label` (默认 `False`)；`ValidationError` 转 `StrategyConfigError("concat", ...)`；metadata 带 `separator` / `include_source_label` / `contributions`
- `config_schema` 采用 JSON Schema（`type: object` + `additionalProperties: false`），供 P1.5 panel UI 直接消费
- `strategies/__init__.py` 显式 re-export 同时触发 `majority_vote` / `concat` 的 `@register` 装饰器生效；`_import_node_package` 的 `pkgutil.walk_packages` 也会递归进来，app 启动一次完成注册
- **smoke 验收（8 条绿）**：注册列表 / `["A","A","B"]→"A"` / 字典序 tie-break (`X` 赢 `Y`) / 默认分隔符拼接 / source_label 拼接 / `StrategyConfigError` extra-forbid / `StrategyNotFoundError` / 幂等 guard `ValueError`
- **无回归**：P1.1 的 14 条 schema 测试 14/14 绿（`tests/unit_tests/core/workflow/nodes/ensemble_aggregator/test_entities.py`）
- **v2 review round 2 兜底**（2026-04-19）：`majority_vote.aggregate` 初始漏写 config 校验，`{"unexpected": 1}` 被静默接受，与 `config_schema.additionalProperties: False` 声明冲突。补入 `_MajorityVoteConfig(BaseModel, extra="forbid")` + `model_validate → StrategyConfigError`，与 `concat` 对称；额外 5 条 review smoke 全绿 + P1.1 14/14 回归无损
- 详见 `docs/ModelNet/P1.2_LANDING.md` §6 "review round 2 修订"

### ✅ P1.3 后端：写 EnsembleAggregatorNode._run (2026-04-19 初始；2026-04-19 review round 2 兜底)

`node.py` 里实现 EnsembleAggregatorNode：

- 继承 `Node[EnsembleAggregatorNodeData]`；`node_type: ClassVar[NodeType] = ENSEMBLE_AGGREGATOR_NODE_TYPE`；`version() -> "1"`
- `_run() -> Generator[NodeEventBase, None, None]`：单 yield `StreamCompletedEvent`
- `_collect_inputs()` 从 `graph_runtime_state.variable_pool.get(ref.variable_selector)` 取 `Segment`，`None` → `MissingInputError`；用 **`segment.text`**（graphon 规范文本）归一化，NoneSegment/ObjectSegment/ArrayStringSegment/空数组 都按 graphon 其他节点可见语义渲染
- `_extract_variable_selector_to_variable_mapping()` 覆盖（v2 review round 2 补）：每条 input 映射到 `{node_id}.inputs.{source_id}` → 原始 `variable_selector`，暴露给 `workflow_entry.py:290` / `workflow_app_runner.py:347` 的 `load_into_variable_pool` 预加载链路（单步调试 + draft 预加载必需）
- 调 `get_strategy(strategy_name).aggregate(inputs, strategy_config)`
- SUCCEEDED：`outputs={text, metadata}` + `inputs={source_count, strategy}`
- `EnsembleAggregatorNodeError` 全族（`MissingInputError` / `StrategyNotFoundError` / `StrategyConfigError`）→ FAILED，`error_type=type(e).__name__` 保留语义，未预期异常让 base `Node.run()` 兜底
- `__init__.py` 追加 `from .node import EnsembleAggregatorNode`；放在 `ENSEMBLE_AGGREGATOR_NODE_TYPE` 常量之后规避循环 import（entities.py 会反向 import 常量）
- **smoke 6/6 绿**：注册（`Node._registry["ensemble-aggregator"]["1"]`）/ majority_vote 3-票 happy / concat+source_label / MissingInputError / StrategyConfigError（`{bogus: 42}`）/ StrategyNotFoundError（defense-in-depth，绕 Pydantic Literal）
- **pytest 21/21 绿**（P1.1 schema 14 + P1.3 新增 7）：`test_node.py::TestSegmentTextNormalization`（NoneSegment/ObjectSegment/ArrayStringSegment/空数组 4 条）+ `TestExtractVariableSelectorMapping`（mapping 暴露/非空 guard/多段 selector 保留 3 条）
- `node_factory.py` 的 `_import_node_package("core.workflow.nodes")` + `pkgutil.walk_packages` 自动递归 import 到 `ensemble_aggregator.node`，`__init_subclass__` 注册完成；无需加 factory 注入分支（P1.1 决策验证）
- **v2 review round 2 兜底**（2026-04-19）：初稿两处绕过 graphon 集成契约。(1) `str(segment.value)` 绕过 `Segment.text` 特化（NoneSegment/ObjectSegment/ArrayStringSegment 会得到 Python repr 而非 JSON/空串）→ 改用 `segment.text`；(2) 默认 `_extract_variable_selector_to_variable_mapping` 返回 `{}` 导致 `workflow_entry` / `workflow_app_runner` 预加载路径拿不到依赖，单步调试/draft 预加载会失效（全图跑能过只因上游已写入 pool）→ 补 classmethod 映射 `{node_id}.inputs.{source_id}`；两处均有 pytest 回归护栏

### ✅ P1.4 后端单测：strategies + node._run（mock VariablePool）(2026-04-20)

> schema 层 14 条 mini-tests 已随 P1.1 v2.3 前置（`test_entities.py`）；P1.4 补策略层 + 节点层。

`api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/`：

- **`test_strategies.py` 新建**（17 条 / 208 行）：`TestMajorityVoteStrategy` 7 条覆盖 `['A','A','B']→'A'` / lex tie-break 确定性 / 阶次无关 / 三路并列 / `extra="forbid"` / contributions 键；`TestConcatStrategy` 7 条覆盖默认分隔符 / 自定义 separator / `include_source_label` 前缀 / 输入顺序保留 / 两种无效 config 拒；`TestRegistry` 3 条覆盖 `list_strategies` / `get_strategy` 新实例 / 同名二次注册 `ValueError` 不污染
- **`test_node.py` +7 条**（复用 P1.3 r2 的 `_make_node` 框架）：`TestRunHappyPath` 3 条（majority_vote 3 路 SUCCEEDED + `outputs.text=='A'` + metadata 全字段 + `inputs={source_count:3, strategy:...}` + 事件序列 `len==1` 且类型校验；concat 默认分隔符；concat + source_label + 自定义 separator）；`TestRunFailurePaths` 4 条（MissingInputError / StrategyConfigError / StrategyNotFoundError defense-in-depth 直改 `_node_data.strategy_name` 绕 Pydantic Literal；异常族 issubclass 守卫）
- **合计 45 条全绿**（P1.1 schema 14 + P1.3 r2 回归 7 + P1.4 新增 24）：`uv run --project api pytest api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -v -o addopts=""` → `45 passed in 0.15s`
- 详见 `docs/ModelNet/P1.4_LANDING.md`

跑 `uv run --project api pytest api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -v -o addopts=""`（仓库 pytest.ini 带 `--cov`，本地 venv 未装 pytest-cov，需用 `-o addopts=""` 覆盖）

### ✅ P1.5 前端：建 ensemble-aggregator 包（default/types/node/panel/use-config + strategy-selector）(2026-04-21)

`web/app/components/workflow/nodes/ensemble-aggregator/` 7 文件 / 635 行落位：

- `types.ts`（39）：`AggregationInputRef` / `EnsembleAggregatorNodeType` 1:1 镜像后端；`strategy_config: Record<string, unknown>` 对齐后端 `dict[str, object]`；暴露 `ENSEMBLE_AGGREGATOR_NODE_TYPE` / `ENSEMBLE_STRATEGY_NAMES` / `ConcatConfig` / `DEFAULT_CONCAT_SEPARATOR`
- `default.ts`：默认 `strategy_name="majority_vote"` + 空 `strategy_config`；`checkValid` 前端早抛后端 Pydantic 校验（≥2 输入 / source_id 非空 + 唯一 / variable_selector ≥2 段 / `strategy_config` 按策略白名单 + 已知字段类型守卫）；`type` 用 `as unknown as BlockEnum` cast 兜底（P1.6 ① 加完 enum 后删 cast，避免 Record<BlockEnum> 三连 cascade）
- `use-config.ts`：`useNodeCrud` + `useAvailableVarList`；9 个 handler（add/remove/source_id/selector/strategy/strategyConfig）；策略切换时 reset config 避开后端 `extra="forbid"`；`handleStrategyConfigChange` 把 patch 里 `undefined` 当"删除 key"处理；`filterStringVar` 放行所有 segment.text 可渲染类型 + 禁 file var
- `components/input-list.tsx`：`source_id` `Input` + `VarReferencePicker` + `RemoveButton` 单行；默认命名 `model_{N}`
- `components/strategy-selector.tsx`：`DropdownMenu` 选策略（**重选当前策略不触发 reset**，避免误清 config）；`majority_vote` 展示 hint；`concat` 展示 `separator` `Input`（**清空 = 删除 key 让后端用默认分隔符**）+ `include_source_label` `Switch`
- `panel.tsx`（91）：Field(inputs) + Field(strategy) + `OutputVars(text, metadata)`
- `node.tsx`（30）：画布缩略显示策略名 + 输入条数；无 input 时不渲染
- **不动**：`BlockEnum` / `BLOCKS` / `NodeComponentMap` / `PanelComponentMap` / `DEFAULT_ICON_MAP` / `SUPPORT_OUTPUT_VARS_NODE` / `singleRunFormParamsHooks` / `getNodeOutputVars` / `canRunBySingle` / i18n — 全留 P1.6
- **验收**：包骨架落地；`pnpm type-check:tsgo` 延 P1.7（本地 `web/node_modules` 缺失，P1.6 完成后整体跑更有效）
- 详见 `docs/ModelNet/P1.5_LANDING.md`

### ✅ P1.6 前端：完成 9 处注册改动 + i18n（ensemble-aggregator）(2026-04-21)

按 DEVELOPMENT_PLAN.md §5.5 的 9 必填注册点落地：

| # | 文件 | 改动 |
|---|---|---|
| ① | `web/app/components/workflow/types.ts` | `BlockEnum.EnsembleAggregator = 'ensemble-aggregator'`（枚举末尾）|
| ② | `web/app/components/workflow/block-selector/constants.tsx` | `BLOCKS` 加一项，`classification: Transform`，挂在 `VariableAggregator` 后 |
| ③ | `web/app/components/workflow/nodes/components.ts` | `NodeComponentMap` + `PanelComponentMap` 各 +1 行；顶部 import `EnsembleAggregatorNode` / `EnsembleAggregatorPanel` |
| ④ | `web/app/components/workflow/block-icon.tsx`（实测位置；TASKS.md v2 的 `nodes/constants.ts` 记载实际应为本文件：`DEFAULT_ICON_MAP`（Record<BlockEnum,..>，强制全量）+ `ICON_CONTAINER_BG_COLOR_MAP`）| 图标 `VariableX`（沿用聚合类外观），颜色 `indigo-500`（区分原 VariableAggregator 的 blue） |
| ⑤ | `web/app/components/workflow/constants.ts:111-132` | `SUPPORT_OUTPUT_VARS_NODE` 加 `BlockEnum.EnsembleAggregator`，不加下游引用不到 `text/metadata` |
| ⑥ | `web/app/components/workflow/nodes/_base/components/workflow-panel/last-run/use-last-run.ts:43,82` | `singleRunFormParamsHooks` 与 `getDataForCheckMoreHooks` 均为 `Record<BlockEnum, any>` — 两张表都补 `EnsembleAggregator: undefined`（P1.8 单节点 run 能力由 `canRunBySingle` 提供即可，不接 form-params hook） |
| ⑦ | `web/app/components/workflow/nodes/_base/components/variable/utils.ts:2201-2207` | `getNodeOutputVars` switch 补 `case EnsembleAggregator: push [[id,"text"],[id,"metadata"]]`（与 panel 的 OutputVars 一致） |
| ⑧ | `web/app/components/workflow/utils/workflow.ts:16-41` | `canRunBySingle` 末尾加 `|| === EnsembleAggregator` |
| ⑨ | `web/i18n/{en-US,zh-Hans}/workflow.json` | 2 × `blocks.ensemble-aggregator` / 2 × `blocksAbout.ensemble-aggregator` + 26 条 `nodes.ensembleAggregator.*`（涵盖 panel/node/input-list/strategy-selector 所有 i18n 引用 + checkValid 的 4 条 errorMsg + pluralized `inputCount_one/_other`）|

附加：
- 删除 `ensemble-aggregator/default.ts` 里 P1.5 遗留的 `ENSEMBLE_AGGREGATOR_NODE_TYPE as unknown as BlockEnum` cast 与 `ENSEMBLE_AGGREGATOR_NODE_TYPE` 的专项 import，`genNodeMetaData({type: BlockEnum.EnsembleAggregator})` 直接用 enum
- i18n JSON 两套 `python3 -c "import json; json.load(...)"` 均解析通过
- 质量门 `pnpm type-check:tsgo` / `pnpm lint:fix` 延至 P1.7（本地 `web/node_modules` 缺失）；三处 `Record<BlockEnum, ...>` 的 TS strict 覆盖通过"每表都显式加 key"消除
- **review round 1 修订（2026-04-22）**：初稿漏 `web/app/components/workflow/constants/node.ts` 的 `WORKFLOW_COMMON_NODES` 注册 → `useAvailableNodesMetaData().nodesMap` 不包含 `EnsembleAggregator`，画布"添加节点"实际创建不出。补加 `ensembleAggregatorDefault` 并在 `workflow-app/hooks/__tests__/use-available-nodes-meta-data.spec.ts` 加 `it.each([true, false])` 回归护栏
- 详见 `docs/ModelNet/P1.6_LANDING.md`

### P1.7 前端质量门：pnpm type-check:tsgo + lint:fix 全绿

在 `web/` 跑 `pnpm type-check:tsgo` 和 `pnpm lint:fix`。重点核对：

- ⑥ `singleRunFormParamsHooks` 的 `Record<BlockEnum, any>` 必须覆盖新枚举值，否则 TS strict 直接报错
- 新增 i18n key 两套（en-US / zh-Hans）必须对齐

### P1.8 联调：dev server 跑通 workflow + chat 模式；写 2 份 DSL

起 dev server，画布上拖出 ensemble-aggregator 节点。

- **workflow 模式**：`Start(query) → 3 LLM → Aggregator → End(text)`，跑 majority_vote + concat 两策略
- **chat 模式**：`Start → 3 LLM → Aggregator → Answer({{aggregator.text}})`，浏览器看到聚合文本
- 导出 DSL 到 `docs/ModelNet/examples/workflow_mode/response_level_ensemble.yml` 与 `docs/ModelNet/examples/chat_mode/response_level_ensemble.yml`
- chat DSL 通过 `validateDSLContent(content, AppModeEnum.ADVANCED_CHAT)`（不含 End）

---

## Phase 2 — Token 级并联节点（11–14 天）

### P2.1 模型注册表：ModelSpec + LocalModelRegistry 单例

新建 `api/core/workflow/nodes/parallel_ensemble/llama_cpp/{__init__.py, exceptions.py, registry.py}`：

- `ModelSpec(BaseModel, extra="forbid")`：字段名严格对齐 `model_info.json`
  - `id`, `model_name`, `model_arch`, `model_url: AnyUrl`, `EOS`（大写）, `type: Literal["normal","think"]`, `stop_think: str | None`, `weight=1.0`, `request_timeout_ms=30000`
- `LocalModelRegistry` 单例：`instance()` / `_load()` / `get(alias)` / `list_aliases()`（list_aliases 返回 `id+model_name+type`，**不含 url**）
- 文件不存在时 `_load` 留空字典 + 控制台日志告警（风险 R9）

### P2.2 LlamaCppClient (强制 ssrf_proxy) + sample yaml + dify_config 配置项

- `parallel_ensemble/llama_cpp/client.py`：`LlamaCppClient.apply_template` + `completion`，**所有 HTTP 走 `core.helper.ssrf_proxy`**（ADR-8）
- 响应解析独立成函数（R7：固化 `top_probs` schema）
- 在 `api/configs/dify_config.py`（或 sub config）加 `MODEL_NET_REGISTRY_PATH: str = "api/configs/model_net.yaml"`
- 写 sample `api/configs/model_net.yaml.example` 模板 + 同目录 README 说明字段（**真实 yaml 进 `.gitignore`**，避免提交内网 URL）

### P2.3 模型注册表单测：load / extra=forbid / client mock ssrf_proxy

`api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/llama_cpp/`：

- `test_registry_load`: 临时写 yaml 文件，验证 `instance()` 加载正确
- `test_extra_forbid`: yaml 含未知字段 → `ValidationError`
- `test_unknown_alias`: `get("nope")` raises `KeyError`
- `test_list_aliases_no_url`: 返回字典不含 `model_url` 字段
- `test_client_uses_ssrf_proxy`: monkeypatch `ssrf_proxy.post`，验证被调用且 timeout 正确换算

### P2.4 控制台 API：GET /workspaces/current/local-models

- 写 `api/controllers/console/workspace/local_models.py`：返回 `LocalModelRegistry.instance().list_aliases()`
- 在该目录的路由注册文件挂上路径 `/workspaces/current/local-models`
- 单测：API 返回 200，`body.models` 存在，每条**不含 url 字段**（关键 SSRF 防护）
- 用 console blueprint 已有的鉴权装饰器（参考同目录其他 controller）

### P2.5 后端 engine：aggregators (base/registry/sum_score/max_score)

`api/core/workflow/nodes/parallel_ensemble/aggregators/`：

- `base.py`: `TokenAggregator` ABC + `TokenCandidate` TypedDict，`pick(per_model, weights) → (token, score)`
- `registry.py`: `register` / `get_token_aggregator`
- `sum_score.py`: 等价 PN.py `calculate_scores`，并列取字典序最小（确定性，不用 `random.choice`）
- `max_score.py`: 取最大单分（不求和）

### P2.6 后端 engine：TokenVoteEngine + ThinkPhaseRunner

- `engine.py`: `TokenVoteEngine.__init__(specs, clients, aggregator, top_k, max_len, executor)`；`stream()` 主循环：每轮 ThreadPoolExecutor 并发调 `client.completion(max_tokens=1, n_probs=k, post_sampling_probs=True)`，`aggregator.pick`，所有 prompt 同步追加 token，yield token；终止条件 `<end>` / `max_len`，return stats dict
- `think_phase.py`: `ThinkPhaseRunner`（仅对 `type=="think"` 模型，调 `completion(stop=[stop_think], max_tokens=8196)` 跑前置思考段，返回 `dict[mid, suffix]`）
- 不实现 KV-cache 复用（非目标 §1.2）

### P2.7 engine 单测：聚合器确定性 + engine.stream + think mock（不依赖 graphon）

`tests/unit_tests/core/workflow/nodes/parallel_ensemble/`：

- `test_sum_score_deterministic`: 并列时取字典序最小，多次跑结果一致
- `test_engine_stream_eos`: mock client 喂假 `top_probs` 序列直到 `<end>`，断言 yield 顺序 + `stats.stopped_by="eos"`
- `test_engine_stream_max_len`: 永不返回 `<end>`，断言 `stopped_by="max_len"` 且 yield 次数 == `max_len`
- `test_engine_prompt_sync`: 每轮所有模型的 prompt 都被追加同一 token
- `test_think_phase`: 仅 `type="think"` 模型被调用，suffix 正确

### P2.8 ParallelEnsembleNode._run：流式事件契约 (6.4 实测签名)

`node.py` 实现 ParallelEnsembleNode：

- `__init__` 接 `local_model_registry` + `executor` 关键字参数
- `_run()` 顺序：
  1. 取 question
  2. 解析 alias
  3. `apply_template`
  4. optional `think_phase`
  5. `engine.stream`
  6. 每 token yield `StreamChunkEvent(selector=[self._node_id, "text"], chunk=token, is_final=False)`
  7. 末尾 yield 空 chunk + `is_final=True` 封口块
  8. yield `StreamCompletedEvent(node_run_result=NodeRunResult(SUCCEEDED, outputs={text, tokens_count, elapsed_ms}, inputs={...}))`
- ⚠️ selector 必须 `self._node_id` 不是 `self.id`；事件参数关键字 `node_run_result=`
- 异常路径：单模型超时本轮空票，全部超时 → `StreamCompletedEvent(status=FAILED)`

### P2.9 node_factory.py:372-440 加 ParallelEnsembleNode 注入分支

参考 HTTP_REQUEST 节点注入 ssrf_proxy 的写法（`node_factory.py:300, 383`）：

- 在 `node_init_kwargs_factories` mapping 加一支：`node_type == "parallel-ensemble"` 时 `kwargs={"local_model_registry": LocalModelRegistry.instance(), "executor": <共享 ThreadPoolExecutor>}`
- 共享 executor 来源：优先复用 GraphEngine 已有的（如果暴露），否则节点内部自建（R10 备选方案，`max_workers` 取 `max(specs)`）
- 改动后跑 P1.4 之前的回归确保未破坏

### P2.10 ParallelEnsembleNode 单测：事件序列 + 异常路径

- `test_event_sequence`: mock engine + mock registry，断言 yield 序列 = `[N×StreamChunkEvent(is_final=False), 1×StreamChunkEvent(is_final=True), 1×StreamCompletedEvent]`，selector 全为 `[node_id, "text"]`
- `test_completed_outputs`: `outputs.text == 累计`、`tokens_count == steps`、`elapsed_ms` 合理
- `test_single_model_timeout`: 单模型 raises `TimeoutError`，本轮 vote 不计该模型，最终 SUCCEEDED
- `test_all_timeout`: 全部模型超时 → `StreamCompletedEvent(status=FAILED)`
- `test_extra_forbid_dsl`: DSL 塞 `model_url` 字段 → Pydantic 拒绝（SSRF 防护）

### P2.11 前端：parallel-ensemble 包 + 模型多选下拉 + import 按钮 + 9 处注册 + i18n

`web/app/components/workflow/nodes/parallel-ensemble/`：

- `default.ts` / `types.ts` / `node.tsx` / `panel.tsx` / `use-config.ts`
- `components/model-selector.tsx`: MultiSelect，选项来自 `GET /console/api/workspaces/current/local-models`
- `components/aggregator-selector.tsx`
- `components/import-model-info-button.tsx`: 解析 `model_info.json`，**仅取 id 字段**自动勾选 alias，URL 等忽略
- 配置项分组：模型选择 / 推理参数（`top_k`, `max_len`, `enable_think`）/ 聚合策略
- 9 处注册 + i18n（同 P1.6 模式，`BlockEnum` 加 `ParallelEnsemble`）

### P2.12 前端质量门：TS + lint + 模型下拉 mock API 单测

- 跑 `pnpm type-check:tsgo` + `pnpm lint:fix`
- `web/app/components/workflow/nodes/parallel-ensemble/__tests__/`：
  - `panel.test.tsx`: 渲染所有配置项
  - `model-selector.test.tsx`: mock `/local-models` API，验证下拉选项渲染 + 多选交互
  - `import-button.test.tsx`: 喂 `model_info.json` fixture，仅 id 字段被勾选

### P2.13 联调 workflow 模式：dev server + 后端 chunk 节奏日志验证

- 在 `model_net.yaml` 配 2-3 个真实 llama.cpp 端点（修改版）
- 起 dev server，画布拖出 parallel-ensemble，配置最小图：`Start(question) → ParallelEnsemble → End(text)`
- 跑一个 question，看后端日志确认每秒按节奏 emit `StreamChunkEvent`
- 行为对照 PN.py：相同 question + 相同模型 + 相同 `top_k` 应得相近输出
- 抓首 token 延迟、token/s、总耗时

### P2.14 联调 chat 模式：浏览器流式渲染 + 写 2 份 token 级 DSL

- 切到 advanced-chat 应用，图：`Start → ParallelEnsemble → Answer({{node.text}})`
- 浏览器看到文字逐字出现（不是一次性出现）；首 token 延迟 ≤ think 阶段 + 1 token 时间
- 导出 DSL 到 `docs/ModelNet/examples/workflow_mode/token_level_ensemble.yml` 与 `docs/ModelNet/examples/chat_mode/token_level_ensemble.yml`
- chat DSL 通过 `validateDSLContent(content, AppModeEnum.ADVANCED_CHAT)`
- 4 份 DSL 都附 README.md 说明需要的 alias 和 `model_net.yaml` 配置

### P2.15 性能基准 (vs PN.py) + 异常路径 + SSRF 抗压测试

- **性能**：相同 question / 模型 / `top_k` 下，节点 token/s 对比 PN.py（R4：差距 <3 token/s 视为通过；明显劣化先 profile 找瓶颈）
- **异常**：手动 kill 一个模型 → 该模型本轮空票，其他继续；kill 全部 → SUCCEEDED→FAILED 转换
- **SSRF**：手动改 DSL JSON 加 `model_url` 字段，导入应失败（Pydantic `extra=forbid`）；尝试在节点配置写 `192.168.x.x` 的 alias → 走 `ssrf_proxy` 拦截

---

## Phase 3 — 测试 / 文档 / 示例（3–4 天）

### P3.1 集成测试 (CI-only) + 4 份 DSL mode validation 测试

- 集成测试用例提交（CLAUDE.md 明确本地不跑，但代码要进仓库）：response_level + token_level，跑通后断言 `outputs.text` 非空
- DSL mode validation 单测：4 份 DSL 各跑 `validateDSLContent` 对应 mode，workflow DSL 不应含 Answer，chat DSL 不应含 End

### P3.2 写 docs/ModelNet/{README.md, SECURITY.md}

- `README.md`: 两个节点用法、`model_net.yaml` schema、流式行为说明、workflow vs chat 模式选择指南、聚合策略扩展指引
- `SECURITY.md`: 为何 URL 不暴露给节点配置、谁能改 yaml、reload 流程、`ssrf_proxy` 强制说明

### P3.3 i18n 全量 review (en-US + zh-Hans)

- 通读 `web/i18n/{en-US,zh-Hans}/workflow.ts` 新增 key
- 检查所有节点显示名 / 配置项标签 / 错误提示 / tooltip 都有两套
- CI: 跑 i18n key 一致性脚本（如有）

---

## 进度跟踪

执行时用 `TaskList` / `TaskUpdate` 在 Claude Code 会话里操作；也可以手动在本文档标 ✅/🚧/⏭️。
