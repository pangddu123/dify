# ModelNet 开发任务清单

> **配套文档**：`DEVELOPMENT_PLAN.md` v2.4 + `EXTENSIBILITY_SPEC.md` v0.2.2（三轴 SPI 详细契约）。本文档是把这两份计划落到可勾选动作的执行清单。
> **路径决定**（2026-04-18）：ModelSpec / LlamaCppClient 放 `api/core/workflow/nodes/parallel_ensemble/llama_cpp/`（和 Phase 2 节点同包），不放 v2 计划早期写的 `api/core/model_runtime/local_models/` —— 因为 `api/core/model_runtime/` 在当前 fork 已不存在。
> **v2.4 SPI 融入**（2026-04-27）：原伴生文档 EXTENSIBILITY_SPEC v0.2.2 的 §11.1 重排表 + §11.3 v0.3 backend pack 已融入主计划；Phase 2 子任务 15 → 18（新增 P2.1.5 SPI 冻结 / P2.2.4 BACKEND_CAPABILITIES / P2.6.5 ResponseLevelRunner），新增 Phase 4 (5 个子任务)。
> **总任务数**：36（Phase 0: 2 / Phase 1: 8 / Phase 2: 18 / Phase 3: 3 / Phase 4: 5）；**已完整完成 14**（Phase 0: 2/2、Phase 1: 7/8、Phase 2: 5/18 — P2.1 + P2.1.5 + P2.2 + P2.2.4 + P2.3），**部分完成 1**（Phase 1 P1.8 仅静态部分，dev server 联调延后）

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

**v2.4 三轴 SPI 化**（2026-04-27）：Phase 2 不再只是"实现 PN.py"，而是落地三轴可扩展框架（`ModelBackend` / `EnsembleRunner` / `Aggregator`），其中第三轴聚合器复用 P1 的 `@register` 模式。v0.2 仅 `llama_cpp` 一个 backend 落地，`vllm` / `openai_compat` / `anthropic` 留 Phase 4。

子阶段分工：

- **P2.1–2.3 注册表 + SPI 冻结**：回答"模型 URL 放哪"（答：服务端 yaml，节点只引用别名）+ 冻结三轴 SPI 接口（P2.1.5，引 EXTENSIBILITY_SPEC §3-7）+ `BACKEND_CAPABILITIES.md` 钉死语义坑（P2.2.4）
- **P2.4 控制台 API**：前端 `models` / `runners` / `aggregators` 三轴下拉选项从哪里来（不返回 URL，ADR-3 隔离；返回 `BackendInfo` + `ui_schema`）
- **P2.5–2.7 runners + aggregators**：算法核心，**完全不依赖 graphon**。`TokenStepRunner`（PN.py 主循环）+ `ResponseLevelRunner`（包 P1 ensemble-aggregator）双 runner 落地；P1 已落地的 majority_vote / concat 平滑迁移为 `ResponseAggregator`
- **P2.8–2.10 节点 + 注入**：把算法套进 graphon 事件协议（selector、封口块、`node_run_result` 等坑都在这里）+ `DiagnosticsConfig` 接 `TraceCollector` 写 outputs/metadata + DSL 拒 url 类敏感字段
- **P2.11–2.12 前端**：runner / aggregator / model 三轴下拉 + 按 `runner.required_caps` 过滤模型 + 按 `runner_cls.requirements(config)` 实时调后端 validate + 按 `ui_schema` 渲染配置表单 + DiagnosticsConfig 面板 + 9 处注册
- **P2.13–2.14 联调**：workflow 模式（→End）和 chat 模式（→Answer）两套跑一遍
- **P2.15 硬化**：性能 vs PN.py、异常路径、SSRF 回归、Trace 大小 boundary

产出：`parallel-ensemble` 节点 + 三轴 SPI 框架（v0.2 范围 1 backend / 2 runner）+ Trace 入 metadata。**不产出 KV-cache 复用**（§1.2 非目标）。**不产出 vllm/openai/anthropic backend**（Phase 4）。

### Phase 4 — v0.3 backend pack（vllm / openai / anthropic + artifact storage）

**目的**：在 Phase 2 SPI 冻结后落地三个新 backend 适配器 + 跨 backend logprob 一致性 fixture + Trace `storage="artifact"`，让框架能跨闭源 / 自托管 / 量化推理三种 backend 跑同一份 ensemble。

为什么独立成阶段而不是塞 Phase 2：

- **EXTENSIBILITY_SPEC §11.2 评审结论**：v0.1 想一次做 4 个 backend 是过度乐观，v0.2 缩到 1 个先把"加 backend 不改 framework 代码"这件事跑通，避免 SPI 接口 + 4 个适配器同时调试时哪头崩都不知道是哪头的事
- **可拆**：研究侧用户如果只用 llama.cpp，Phase 4 可延期或剪除，不阻塞 Phase 3 收尾
- **语义坑集中爆发**：vLLM logprobs 是 log-softmax、OpenAI top_logprobs ≤ 20、Anthropic 不暴露 logprobs ——这三个坑在同一阶段统一对齐 `BACKEND_CAPABILITIES.md` 钉死的语义（P2.2.4 已先落地这份合约）

产出：3 个新 backend adapter + 跨 backend logprob 一致性 fixture + `DiagnosticsConfig.storage` 加 `"artifact"` Literal。详见 EXTENSIBILITY_SPEC §11.3 / §12.1。

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
Phase 2 token 级 ──► 引入模型注册表 + SSRF 防护 + 三轴 SPI + 流式封口块 + Trace
                   │
                   ├─►  Phase 3 测试文档  (收尾，不阻塞 Phase 4)
                   │
                   ▼
                   Phase 4 v0.3 backend pack (vllm/openai/anthropic + artifact)
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

### P1.7 前端质量门：pnpm type-check:tsgo + lint:fix 全绿 ✅

在 `web/` 跑 `pnpm type-check:tsgo` 和 `pnpm lint:fix`。重点核对：

- ⑥ `singleRunFormParamsHooks` 的 `Record<BlockEnum, any>` 必须覆盖新枚举值，否则 TS strict 直接报错
- 新增 i18n key 两套（en-US / zh-Hans）必须对齐

**落地结果（2026-04-22）**：`pnpm install` 成功；`tsgo` 0 error；`eslint --fix` 0 error（27 warning 全部为仓库历史遗留或 P1.5 scaffolding 层面，计入后续 polish）；`pnpm test ...use-available-nodes-meta-data.spec.ts` 4/4 通过；en-US / zh-Hans `nodes.ensembleAggregator.*` + `blocks(.About).ensemble-aggregator` 键集 27 个完全对齐。唯一实质代码改动：`ensemble-aggregator/default.ts:22` 把 `t: any` 换成 i18next 风格 `(key, options?) => string`（兄弟节点的 `t: any` 在 `eslint-suppressions.json` 里被抑制，我方新文件没进抑制表，正面修更干净）。另外两个文件的改动是 eslint 自动排序 import。详见 `docs/ModelNet/P1.7_LANDING.md`。

### 🚧 P1.8 联调：dev server 跑通 workflow + chat 模式；写 2 份 DSL — **静态完成 / 浏览器回归待执行**（2026-04-24）

> **状态说明**：P1.8 的核心动作是"联调"（起 dev server、画布拖节点、浏览器看流式），这部分尚未由本轮 Agent 执行；本轮仅完成 2 份 DSL 落位 + 静态契约验证 + graphon 注册链路验证。浏览器 E2E 需要用户在本机 dev 环境手动跑一遍后，才能把本节改为 ✅ 完整完成。静态部分见下列证据；浏览器回归命令见 `docs/ModelNet/P1.8_LANDING.md` §4.1。

落地范围（静态部分）：2 份响应级 DSL 编写 + 静态契约验证 + 注册链路校验；浏览器 E2E 回归由用户在本机 dev server 完成（本环境 :3000 被 open-webui 占用、无独立 Dify 栈，起 docker-compose 有污染共享容器风险，按 CLAUDE.md "Executing actions with care" 规则不自启）。

- **workflow DSL**：`docs/ModelNet/examples/workflow_mode/response_level_ensemble.yml` — `start → [llm_a, llm_b, llm_c] → aggregator(majority_vote) → end`；三路情感分类器（temp=0）输出 positive/negative/neutral 单词，End 导出 `label` + `metadata`；切 `concat` 只需改 `strategy_name` + `strategy_config` 两字段
- **chat DSL**：`docs/ModelNet/examples/chat_mode/response_level_ensemble.yml` — `start → [llm_a, llm_b, llm_c] → aggregator(concat + include_source_label=true) → answer`；三风格答复（concise/creative/steps）加源标签拼接流给 Answer；opening_statement 已设
- **静态验证全绿**：(1) 两份 YAML `EnsembleAggregatorNodeData.model_validate` 通过（source_id 唯一、selector 段非空、inputs ≥2、strategy_config extra="forbid" 不违反）；(2) 前端等价 `validateDSLContent` 模式检查：workflow DSL 无 answer、chat DSL 无 end/trigger-*；(3) 策略实际执行预演（majority_vote 三票→positive；concat include_source_label 输出 `[concise]...[creative]...[steps]...` + 默认分隔符 `\n\n---\n\n`）；(4) `register_nodes()` 后 `Node._registry["ensemble-aggregator"]["1"] is EnsembleAggregatorNode`
- **无回归**：`uv run --project api pytest api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -v -o addopts=""` → 45/45 绿
- **两份 DSL 各用一种策略而非都跑两种**：majority_vote 本质是"完全相同字符串投票"，chat 模式三路长文几乎不可能字面相同 → 永远退化成字典序 tie-break，示例效果反向误导；concat 两模式都能跑，但放 chat + include_source_label 肉眼最能看出"多模型融合"效果。两份 DSL 顶部注释都写了如何切到另一种策略（单字段替换）
- **用户本地浏览器回归命令**：`pnpm dev` (web) + `uv run --project api flask run` (api) + `docker compose -f docker-compose.middleware.yaml up -d` (数据库/Redis) → 创建 workflow / advanced-chat 应用 → Import DSL → 跑一遍
- 详见 `docs/ModelNet/P1.8_LANDING.md`

---

## Phase 2 — Token 级并联节点（11–14 天）

### ✅ P2.1 模型注册表：ModelSpec + LocalModelRegistry 单例 (2026-04-27)

新建 `api/core/workflow/nodes/parallel_ensemble/llama_cpp/{__init__.py, exceptions.py, registry.py}`，外加 `parallel_ensemble/__init__.py` 上挂 `PARALLEL_ENSEMBLE_NODE_TYPE = "parallel-ensemble"` 常量为 P2.8 预留。

- `ModelSpec(BaseModel, extra="forbid", frozen=True)`：字段名严格对齐 `docs/ModelNet/model_info.json`
  - `id` (min_length=1), `model_name` (min_length=1), `model_arch="llama"`, `model_url: AnyUrl`, `EOS` (min_length=1), `type: Literal["normal","think"]="normal"`, `stop_think: str|None=None`, `weight=1.0` (gt=0), `request_timeout_ms=30000` (gt=0)
  - `extra="forbid"` 是 **yaml 加载层** 防 typo / rogue 字段的硬约束（运维侧 yaml 写错或多余字段直接 boot 拒）；`frozen=True` 让跨线程引用安全。⚠️ 它**不**防 DSL 偷塞 `model_url`——ModelSpec 从不由 DSL 实例化，DSL 走 alias 反查 spec。DSL 侧防护见 P2.8 / P2.10 的 NodeData 层处理。SSRF 第一道闸是 `list_aliases()` 不返回 url（ADR-3）。
- `LocalModelRegistry` 单例：`instance()` 双检锁 + `for_testing(path)` / `reset_for_testing()` 单测 hooks + `_load(path_override=None)` + `get(alias)` / `list_aliases()` (TypedDict `AliasInfo{id,model_name,type}`，**不含 url**) + `__contains__` / `__len__` / `__repr__`
- **R9 落地**：文件不存在 → 空字典 + `logger.warning("Model registry yaml not found at '%s'; ...")`（不抛异常，不炸 boot）
- **路径解析 forward-compat**：`DEFAULT_REGISTRY_PATH` 基于 `registry.py` 反推 API root，cwd-independent 地指向 `api/configs/model_net.yaml`；`_resolve_path` 用 `getattr(dify_config, "MODEL_NET_REGISTRY_PATH", DEFAULT_REGISTRY_PATH)`，P2.2 在 `dify_config.feature` 注册 Field 后零修改接入
- **加载阶段守卫**：duplicate `id` / 顶层非 mapping / `models` 非 list / entry 非 mapping / `OSError|YAMLError` / Pydantic `ValidationError` 全部转 `RegistryFileError(path, reason)`，带 index 上下文
- **Exception 树**：`LlamaCppNodeError` ⊃ `ModelRegistryError` ⊃ {`RegistryFileError(path, reason)`, `UnknownModelAliasError(alias)`}，节点层可单 except 整族
- **smoke 10/10 绿**（inline 等价 P2.3 验收的 4/5 条）：(1) ModelSpec 吃下 model_info.json 全部 7 条 (2) extra-forbid 拒 unknown key (3) list_aliases 不含 url (4) get unknown → UnknownModelAliasError (5) missing file → empty + WARNING (R9) (6) duplicate id → RegistryFileError (7) rogue entry field → RegistryFileError (8) malformed yaml → RegistryFileError (9) 空 yaml → 空 registry 不报错 (10) instance() 单例身份
- **无回归**：`uv run --project api pytest api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -q -o addopts=""` → 47/47 绿
- **延后到 P2.2**：dify_config 注册 `MODEL_NET_REGISTRY_PATH`、sample yaml + `.gitignore`、`LlamaCppClient` 升级为 `LlamaCppBackend`；**延后到 P2.3**：把 inline smoke 移成正式 pytest 单测文件 + ssrf_proxy mock 用例
- **v2.4 SPI 升级 forward-compat note**（2026-04-27）：P2.1.5 SPI 冻结后，`LocalModelRegistry` → `ModelRegistry`（重命名，保留旧名 alias 一个版本）；`ModelSpec` 拆为 `BaseSpec` + `LlamaCppSpec`（字段不变，加 `backend: Literal["llama_cpp"]` discriminator）；`_load()` 由 `BackendRegistry.get_spec_class(backend_str).model_validate(entry)` 动态分发。**已落地代码不重写**，是+1 文件 + 改 1-2 行式升级。详见 EXTENSIBILITY_SPEC §4.3
- 详见 `docs/ModelNet/P2.1_LANDING.md`

### ✅ P2.1.5 SPI 接口冻结（v2.4 新任务）(2026-04-27)

> 评审：v0.2 核心。后续 P2.2-P2.10 全部以此为契约；接口冻结后**禁止破坏性变更**，扩展走子类化或新注册项。

新建 `api/core/workflow/nodes/parallel_ensemble/spi/` 子包，落地 6 个文件：

- `capability.py`: `Capability` 枚举（`STREAMING / TOKEN_STEP / TOP_PROBS / POST_SAMPLING_PROBS / LOGITS_RAW / CHAT_TEMPLATE / FUNCTION_CALLING / KV_CACHE_REUSE`，详见 EXTENSIBILITY_SPEC §3.1）
- `requirements.py`: `Requirement{kind, value, rationale}` + `ValidationIssue{severity, requirement, message, i18n_key}` TypedDict（EXTENSIBILITY_SPEC §3.4）
- `backend.py`: `BaseSpec(BaseModel, extra="forbid", frozen=True)` + `ModelBackend` ABC + `ChatMessage` / `GenerationParams` / `TokenCandidate` / `GenerationResult` / `StreamChunk` TypedDict + `BackendInfo` 投影；公开 `id` / `model_name` / `weight` / `instance_capabilities` property（EXTENSIBILITY_SPEC §4）
- `runner.py`: `EnsembleRunner` ABC + `TokenEvent` / `FullResponseEvent` / `DoneEvent` / `RunnerEvent` Union + `i18n_key_prefix` / `ui_schema` ClassVar（控件白名单：`number_input / text_input / textarea / switch / select / multi_select / model_alias_select`）（EXTENSIBILITY_SPEC §5.1）
- `aggregator.py`: `Aggregator[ConfigT, SignalT, ResultT]` 通用基类 + `ResponseAggregator` / `TokenAggregator` typed bases + `AggregationContext`（注 weights / capabilities / step_index / trace 句柄）+ `ResponseSignal` / `TokenSignals` / `TokenPick` TypedDict（EXTENSIBILITY_SPEC §6）
- `trace.py`: `DiagnosticsConfig`（`storage: Literal["inline","metadata"]`，artifact 留 Phase 4） + `EnsembleTrace` schema + `TraceCollector` 门面（`record_response` / `record_token_step` / `record_think` / `record_summary` / `finalize`）（EXTENSIBILITY_SPEC §7）

**注册表子包**`api/core/workflow/nodes/parallel_ensemble/registry/`：

- `model_registry.py`: 升级 P2.1 `LocalModelRegistry` → `ModelRegistry`（旧名保留 alias 一版本），按 `BackendRegistry.get_spec_class(backend_str)` 动态分发（EXTENSIBILITY_SPEC §4.3.3）
- `backend_registry.py`: `@register_backend("name")` 装饰器 + `BackendRegistry.{register,get,get_spec_class,known_backends}`
- `runner_registry.py`: `@register_runner("name")` 装饰器
- `aggregator_registry.py`: `@register_aggregator("name", scope="...")` 装饰器

**验收**：6 个 SPI 文件 `py_compile` 通过；ABC 子类化测试（写一个 `Echo` 假 backend / `Noop` 假 runner / `First` 假 aggregator，能注册能 lookup 能 type-check 通过）；`ModelRegistry._load` 用 `LlamaCppSpec` 反查 spec_class 加载 P2.1 现有 yaml 不退化（10/10 smoke 仍绿）

**落地结果（2026-04-27）**：
- **SPI 子包**`api/core/workflow/nodes/parallel_ensemble/spi/{capability,requirements,backend,runner,aggregator,trace,__init__}.py` 全量落位；ui_schema 控件白名单 v0.2 冻结为 `frozenset({number_input, text_input, textarea, switch, select, multi_select, model_alias_select})`；`Capability` 枚举 8 个值与 EXTENSIBILITY_SPEC §3.1 一致
- **注册表子包**`registry/{backend_registry,runner_registry,aggregator_registry,model_registry}.py` + `@register_*` 装饰器全部落位；`ModelRegistry._load` 走 `BackendRegistry.get_spec_class(backend_str).model_validate(entry)` 动态分发
- **legacy 兼容**：`LocalModelRegistry = ModelRegistry`、`llama_cpp/registry.py` 改成 shim 重导 `LlamaCppSpec as ModelSpec` + `ModelRegistry as LocalModelRegistry`；`exceptions.py` 上提到 parallel_ensemble 包根破环（`LlamaCppNodeError` 树原位可用）；`CapabilityNotSupported` → `CapabilityNotSupportedError`（N818）保留旧名 alias 一版本
- **side-effect import**：`parallel_ensemble/__init__.py` 真正 `from . import backends as backends` 触发 `@register_backend("llama_cpp")`；boot 后 `BackendRegistry.known_backends() == ["llama_cpp"]`，yaml `backend: llama_cpp` 加载即生效
- **静态全绿**：ruff 0、basedpyright 0 errors / 0 warnings / 0 notes、mypy `Success: no issues found in 19 source files`；pyright `reportIncompatibleVariableOverride` 在 `LlamaCppSpec.backend: Literal[...]` 处用 ignore 注明 pydantic discriminator 模式
- **测试 81/81 绿**：`tests/.../parallel_ensemble/test_spi_freeze.py` 覆盖 Echo/Noop/First 三轴注册 + duplicate / unknown / ui_schema 白名单 / Capability frozen set / DiagnosticsConfig extra-forbid / TraceCollector last-N 截断 / EchoBackend 实例 + 默认 step_token 抛 CapabilityNotSupportedError；`test_model_registry.py` 覆盖 P2.1 全部 10 条 smoke + BackendRegistry 分发（unknown_backend / missing_backend / llama_cpp spec class lookup / LocalModelRegistry alias 仍可用）
- **无回归**：`uv run --project api pytest api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -q -o addopts=""` → 47/47 绿

### ✅ P2.2 LlamaCppBackend (强制 ssrf_proxy) + sample yaml + dify_config 配置项 (2026-04-27)

> 重命名：v2.4 起原 P2.2 的 `LlamaCppClient` 改为 `LlamaCppBackend(ModelBackend)`，按 SPI §4 实现 6 方法。

- `parallel_ensemble/backends/llama_cpp.py`：`LlamaCppBackend(ModelBackend)`，`spec_class = LlamaCppSpec`，`@register_backend("llama_cpp")`
  - `capabilities(spec) -> frozenset[Capability]`：`STREAMING + TOKEN_STEP + TOP_PROBS + POST_SAMPLING_PROBS + CHAT_TEMPLATE`（详见 BACKEND_CAPABILITIES.md，P2.2.4）
  - `validate_requirements(spec, requirements) -> list[ValidationIssue]`：`needs_function_calling=True` 拒；`min_top_k` 任意值放行（llama.cpp 无硬上限）；其余 requirement 走默认 capability-bottom 兜底
  - `generate(prompt, params)`：`POST /completion`，body `{prompt, ...params, stream: false}`；`stop_type` → `finish_reason`，`limit` 标准化为 `length`
  - `generate_stream(prompt, params)`：`POST /completion stream:true`；`parse_sse_chunks` 解析 `data: {...}` SSE 行；ssrf_proxy 缓冲响应所以语义正确但非真实时（注释说明，P2.13 dev server 兜底）
  - `step_token(prompt, top_k)`：`POST /completion {max_tokens:1, n_probs:k, post_sampling_probs:true}`；`parse_top_probs` 独立模块函数（R7：固化 `top_probs` schema），EOS / 空字符串 token 重写为 `<end>` 哨兵（PN.py 契约）
  - `apply_template(messages)`：`POST /apply-template`
- **配置**：`api/configs/feature/__init__.py` 新增 `ModelNetConfig(BaseSettings)`，加 `MODEL_NET_REGISTRY_PATH: str = "api/configs/model_net.yaml"` + `PARALLEL_ENSEMBLE_MAX_WORKERS: PositiveInt = 8`，挂进 `FeatureConfig`（alphabet 顺序）；`ModelRegistry._resolve_path` 已是 `getattr(dify_config, "MODEL_NET_REGISTRY_PATH", DEFAULT)`，零修改接入
- **sample yaml + README**：`api/configs/model_net.yaml.example`（含 `backend: llama_cpp` + 字段注释）+ `api/configs/MODEL_NET_README.md`（路径覆盖、SSRF、loading 说明）；`.gitignore` 加 `api/configs/model_net.yaml` 一行
- **smoke 25/25 绿**：`tests/.../parallel_ensemble/test_llama_cpp_backend.py` 覆盖 capabilities 三连（默认集 / LOGITS_RAW 不在 / FUNCTION_CALLING 不在）/ validate_requirements 三连（min_top_k=999 放行 / needs_fc=True 拒 / needs_fc=False 放行）/ parse_top_probs 四连（EOS 重写 / 空 completion_probabilities / 空 top_probs / 非 dict 项跳过）/ parse_sse_chunks 四连（双 chunk + final / 服务器中断兜底 final / 不可解析行跳过 / 非 data 行忽略）/ generate 三连（端点 + body + headers + 30s timeout / `limit` → `length` / 末尾斜杠裁剪）/ step_token 三连（PN.py contract body / EOS 折叠 / payload 异常兜底）/ apply_template 二连（端点 + 透传 / 缺 prompt 字段 fallback "")/ generate_stream 二连（顺序 + final 标志 + stream=True / 端点）/ ssrf_proxy 注入路径（`monkeypatch ssrf_proxy.post` 确认 step_token 走代理）
- **回归全绿**：`uv run --project . pytest tests/unit_tests/core/workflow/nodes/parallel_ensemble/ tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -q -o addopts=""` → 106/106 绿（P1 47 + P2.1.5 34 + P2.2 新增 25）；ruff lint + format 全绿
- **延后到 P2.3**：把 P2.1 的 inline smoke 移成正式 pytest 单测文件（已部分覆盖于本 commit 的 backend 测试）+ 跨 backend ssrf_proxy 注入 e2e 用例
- **延后到 P2.9**：`node_factory.py` 注入分支把 `ssrf_proxy` 真正传给 `LlamaCppBackend(spec, http=...)`；当前测试用 `_FakeHttp` 验证 protocol shape

### ✅ P2.2.4 BACKEND_CAPABILITIES.md（v2.4 新任务）(2026-04-27)

`docs/ModelNet/BACKEND_CAPABILITIES.md` 落地：
- §1 capability 矩阵（4 backend × 8 capability）来源 EXTENSIBILITY_SPEC §3.2，PR review 时跨文档跳转免去
- §2 三个语义坑：POST_SAMPLING_PROBS vs LOGITS_RAW（PN.py 严格语义需 LOGITS_RAW）/ OpenAI top_logprobs ≤ 20 + gpt-3.5-turbo-0301 不支持 logprobs（含 OpenAICompatBackend.validate_requirements 示例代码）/ vLLM logprobs 是 log-softmax 需 exp + top-k 内归一（含换算示例代码）
- §3 加 backend 合约：声明 capability 集合 + fixture 兜底 + override `validate_requirements` 的硬性要求；§4 当前 v0.2 仅 llama_cpp 的实际声明
- 测试侧：`test_llama_cpp_backend.py::TestCapabilities::test_default_set_matches_backend_capabilities_doc` 是这份文档的可执行快照（合约文件 → fixture → adapter 的三层一致性兜底）
- §5 修订指引：capability 增删改属 SPI 演进，需同时改 spi/capability.py + 本文档 + EXTENSIBILITY_SPEC §3.2 + Phase 4 fixture，三处同步

### ✅ P2.3 模型注册表 + LlamaCppBackend 单测（v2.4 扩展）(2026-04-27)

`api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/llama_cpp/`：

- `test_registry_load.py::test_registry_load`: 临时写 yaml 文件（含 `backend: llama_cpp`），验证 `ModelRegistry.for_testing(path)` 通过 BackendRegistry 反查 `LlamaCppSpec` 加载正确
- `test_registry_load.py::test_extra_forbid`: yaml entry 含未知字段（`rogue`）→ `RegistryFileError`（包装 Pydantic `ValidationError`，reason 含字段名）
- `test_registry_load.py::test_unknown_backend`: yaml `backend: my_zmq` 但未注册 → `RegistryFileError`，reason 含 `"my_zmq"` + `"is not registered"` + 已注册的 `llama_cpp`
- `test_registry_load.py::test_unknown_alias`: `get("nope")` 抛 `UnknownModelAliasError(alias="nope")`
- `test_registry_load.py::test_list_aliases_returns_backend_info`: 返回 `BackendInfo{id, backend, model_name, capabilities, metadata}`；**不含 `model_url` / `api_key` / `api_key_env`**（T2 SSRF/凭证边界）；`capabilities` 是字符串 list（含 `token_step / top_probs`，不含 `logits_raw`）；`metadata` 携带 `type` 区分 think/normal
- `test_backend_ssrf.py::test_capability_declaration`: `LlamaCppBackend.capabilities(spec)` 等于 `{STREAMING, TOKEN_STEP, TOP_PROBS, POST_SAMPLING_PROBS, CHAT_TEMPLATE}`，`LOGITS_RAW` 不在其中（trap 1）
- `test_backend_ssrf.py::test_validate_requirements_default_*` 三连：未知 kind 默认 pass / `min_top_k=999` pass（无硬上限）/ `needs_function_calling=True` 拒、`False` pass
- `test_backend_ssrf.py::test_step_token_uses_ssrf_proxy`: `monkeypatch ssrf_proxy.ssrf_proxy.post`；验证 url + body `{prompt, max_tokens=1, n_probs=k, post_sampling_probs=True}` + headers + `request_timeout_ms=15000` → `timeout=15.0s`
- `test_backend_ssrf.py::test_generate_uses_ssrf_proxy`: 同上，验证 `/completion` 端点 + body 含 `stream=False` + caller 的 sampling params 透传 + 默认 `request_timeout_ms=30000` → `timeout=30.0s` + 返回 `text/finish_reason/metadata`
- `test_backend_ssrf.py::test_apply_template_uses_ssrf_proxy`: `/apply-template` 端点 + messages list 透传

**实现侧改动**：
- `parallel_ensemble/registry/model_registry.py::list_aliases` 返回类型由 `list[AliasInfo]` 升级为 `list[BackendInfo]`（SPI §4 已存在的 TypedDict）：`AliasInfo = BackendInfo` **仅保留 import 名兼容**，运行时 shape 已由 `{id, backend, model_name, type}` 变为 `{id, backend, model_name, capabilities, metadata}`，旧 `info["type"]` 字段需迁移到 `info["metadata"].get("type")`；capabilities 通过 `BackendRegistry.get(spec.backend).capabilities(spec)` 计算后转成排序 string list（前端无需 import `Capability` 枚举）；当前 v0.2 仅 llama.cpp 的 `type` 进入 `metadata`，其余非密钥扩展字段是 P2.4+ 后续任务的接入点。P2.4 控制台 API 直接复用此 dict，零改动接入
- `tests/.../parallel_ensemble/test_model_registry.py::test_list_aliases_omits_url` 同步更新 keys 断言为 `{id, backend, model_name, capabilities, metadata}`

**测试范围说明（不夸大）**：`test_backend_ssrf.py` 是 **adapter-level wiring smoke**（直接构造 `LlamaCppBackend(spec, http=ssrf_module.ssrf_proxy)`），证明 backend 收到 `SSRFProxy` 后确实走 `ssrf_proxy.post` 而非 `httpx.post`。**不**覆盖框架运行时一定注入 proxy 这一更强命题——`node_factory → backend` 的注入路径属 P2.9 范围

**回归**：`uv run --project . pytest tests/unit_tests/core/workflow/nodes/parallel_ensemble/ tests/unit_tests/core/workflow/nodes/ensemble_aggregator/ -q -o addopts=""` → **118/118 绿**（P1 47 + P2.1.5 34 + P2.2 25 + P2.3 新增 12）；`uv run --project . ruff check` / `ruff format --check` 全绿；`uv run --project . basedpyright <changed files>` → 0 errors / 0 warnings / 0 notes

### P2.4 控制台 API：models / runners / aggregators 三路由（v2.4 改）

> v2.4 改：原 P2.4 只挂一个 `/local-models`；现需三个路由对应三轴下拉。

- `api/controllers/console/workspace/local_models.py`：`GET /workspaces/current/local-models` → `ModelRegistry.instance().list_aliases()`，每条返回 `BackendInfo{id, backend, model_name, capabilities, metadata}`，**不含 url/api_key**（关键 T2）
- `api/controllers/console/workspace/runners.py`：`GET /workspaces/current/runners` → `[{name, i18n_key_prefix, ui_schema, config_schema, aggregator_scope, required_capabilities, optional_capabilities}, ...]`
- `api/controllers/console/workspace/aggregators.py`：`GET /workspaces/current/aggregators` → `[{name, i18n_key_prefix, ui_schema, config_schema, scope}, ...]`
- 路由注册（同目录 console blueprint），鉴权装饰器参考同目录其他 controller
- 单测：3 个 API 各返回 200；`body.models` 不含 url；`body.runners[].ui_schema` 控件全在 v0.2 白名单内

### P2.5 后端 aggregators：response + token 双 scope（v2.4 重写）

> v2.4 改：P1 已落地的 majority_vote / concat 平滑迁移为 `ResponseAggregator[ConfigT]`，对 P1 调用点零影响（`AggregationContext` 默认参数兜底）。

`api/core/workflow/nodes/parallel_ensemble/aggregators/`：

**`response/` (scope="response", 平滑迁移 P1)**：

- `majority_vote.py`: 继承 `ResponseAggregator[MajorityVoteConfig]`，`aggregate(signals: list[ResponseSignal], ctx: AggregationContext, config) -> ResponseAggregationResult`；与 P1 行为一致（lex tie-break 确定性）；`@register_aggregator("majority_vote", scope="response")`
- `concat.py`: 同上，`@register_aggregator("concat", scope="response")`

**`token/` (scope="token", 新建)**：

- `sum_score.py`: 继承 `TokenAggregator[SumScoreConfig]`，`aggregate(signals: TokenSignals, ctx, config) -> TokenPick`；等价 PN.py `calculate_scores`，并列取字典序最小（确定性，不用 `random.choice`）；`per_model_errors` 非空时按 `skip_empty_voters` 配置决定跳过该模型还是用上步 fallback
- `max_score.py`: 取最大单分（不求和）

### P2.6 TokenStepRunner + ThinkPhaseRunner（v2.4 重命名）

> v2.4 改：原 `TokenVoteEngine` → `TokenStepRunner(EnsembleRunner[TokenStepConfig])`；逻辑等价 PN.py 主循环。

- `runners/token_step.py`: `TokenStepRunner` 全字段（`name="token_step"` / `config_class` / `aggregator_scope="token"` / `required_capabilities={TOKEN_STEP, TOP_PROBS}` / `optional_capabilities={CHAT_TEMPLATE}` / `i18n_key_prefix` / `ui_schema`），`requirements(config)` 派生 `min_top_k` + `needs_logprobs` 两条诉求，`validate_selection(config, aliases, registry)` 校验 ≥ 2 模型 + `enable_think` 与 type=think 模型一致性，`run(question, backends, aggregator, config, trace)` 主循环：每轮 ThreadPoolExecutor 并发 `backend.step_token(prompt, config.top_k)`，`aggregator.aggregate(TokenSignals(...), ctx, config.aggregator_config)`，所有 prompt 同步追加 pick.token，`yield TokenEvent(delta=token)`；终止 `<end>` / `config.max_len`，末尾 `yield DoneEvent(text=accumulated, metadata=...)`；`trace.record_token_step(...)` 由 collector 按 cfg 决定真存
- `think_phase.py`: `ThinkPhaseRunner`（仅对 `type="think"` 模型，调 `backend.generate(stop=[stop_think], max_tokens=8196)` 跑前置思考段，返回 `dict[alias, suffix]`），由 `TokenStepRunner.run` 在 `enable_think=True` 时调用
- 不实现 KV-cache 复用（非目标 §1.2）

### P2.6.5 ResponseLevelRunner（v2.4 新任务，包 P1 ensemble-aggregator 现有逻辑）

新建 `runners/response_level.py`：`ResponseLevelRunner(EnsembleRunner[ResponseLevelConfig])`，`name="response_level"` / `aggregator_scope="response"` / `required_capabilities=frozenset()` / `optional_capabilities={STREAMING}` / `ui_schema={}`（无字段），`requirements()` 返回空 list，`validate_selection` 校验 ≥ 2 模型，`run` 并发调 `backend.generate`，`trace.record_response(...)`，收齐喂 `aggregator.aggregate`，`yield DoneEvent(text=result.text, metadata=result.metadata)`。

### P2.7 runners + aggregators 单测：确定性 + capability/requirements/trace 路径（v2.4 扩展）

`tests/unit_tests/core/workflow/nodes/parallel_ensemble/`：

- `test_sum_score_deterministic`: 并列时取字典序最小，多次跑结果一致
- `test_token_step_runner_eos`: mock `LlamaCppBackend.step_token` 喂假候选直到 `<end>`，断言 yield 顺序（N×TokenEvent + 1×DoneEvent）+ `metadata.stopped_by="eos"`
- `test_token_step_runner_max_len`: 永不返回 `<end>`，断言 `stopped_by="max_len"` 且 TokenEvent 数 == `max_len`
- `test_token_step_prompt_sync`: 每轮所有 backend 的 prompt 都被追加同一 token
- `test_think_phase`: 仅 `type="think"` 模型被调用，suffix 正确
- `test_response_level_runner`: mock backend.generate，断言 yield `DoneEvent(text=...)` 且经过 aggregator
- `test_capability_filter`: `runner.required_capabilities - backend.capabilities(spec)` 不空 → `StructuredValidationError`
- `test_requirements_validation`: `backend.validate_requirements(spec, [{kind:"min_top_k", value:25}])` 返回 ValidationIssue list
- `test_validate_selection_too_few_models`: `model_aliases=[只 1 个]` → ValidationIssue
- `test_validate_selection_judge_alias_not_in_models`: judge runner 跨字段校验
- `test_trace_collector_no_op`: `DiagnosticsConfig.include_token_candidates=False` 时 `record_token_step` 是 no-op，`finalize().token_trace` 不含 candidates
- `test_trace_collector_truncation`: token 步数超过 `max_trace_tokens` 时 last-N 自动丢弃 + `truncated=True`

### P2.8 ParallelEnsembleNode._run：SPI 化 + 流式事件契约（v2.4 重写）

`node.py` 实现 ParallelEnsembleNode（详见 DEVELOPMENT_PLAN §6.4 v2.4 伪代码）：

- `entities.py`：`ParallelEnsembleNodeData` 顶层保 `extra="allow"`（兼容 BaseNodeData 的 selected/params/paramSchemas/datasource_label）+ `model_validator(mode="before")` 显式拒已知敏感字段（`model_url, api_key, api_key_env, url, endpoint`）；嵌套业务配置 `ParallelEnsembleConfig`（含 `runner_name / runner_config / aggregator_name / aggregator_config / diagnostics / question_variable / model_aliases`）挂 `extra="forbid"`
- `__init__` 接 5 个关键字参数：`model_registry / runner_registry / aggregator_registry / backend_registry / executor`
- `_run()` 顺序：
  1. 取 question
  2. 解析 alias → spec → backend 实例（`backend_registry.get(spec.backend)(spec, http=ssrf_http)`）
  3. `runner_registry.get(cfg.runner_name)` + `aggregator_registry.get(cfg.aggregator_name)` 反查；validate `runner_config` / `aggregator_config`
  4. 构建 `TraceCollector(cfg.diagnostics)`
  5. `runner.run(question, backends, aggregator, runner_config, trace)` 迭代事件
  6. `TokenEvent` → yield `StreamChunkEvent(selector=[self._node_id, "text"], chunk=delta, is_final=False)`；`DoneEvent` → 记 final text
  7. 末尾 yield 空 chunk + `is_final=True` 封口块
  8. `trace.finalize()` 按 `cfg.diagnostics.storage` 写 `outputs.trace`（inline）或 `metadata.ensemble_trace`（metadata），`outputs.text` 永远是最终文本字符串
  9. yield `StreamCompletedEvent(node_run_result=NodeRunResult(SUCCEEDED, outputs={text, tokens_count, elapsed_ms[, trace]}, metadata={[ensemble_trace]}, inputs={...}))`
- ⚠️ selector 必须 `self._node_id` 不是 `self.id`；事件参数关键字 `node_run_result=`
- 异常路径：单 backend 超时本轮空票（runner 决定）；全 backend 超时 → `StreamCompletedEvent(status=FAILED)`

### P2.9 node_factory.py:372-440 加 ParallelEnsembleNode 注入分支（v2.4 扩展）

参考 HTTP_REQUEST 节点注入 ssrf_proxy 的写法（`node_factory.py:300, 383`）：

- 在 `node_init_kwargs_factories` mapping 加一支：`node_type == "parallel-ensemble"` 时 `kwargs={"model_registry": ModelRegistry.instance(), "runner_registry": RunnerRegistry, "aggregator_registry": AggregatorRegistry, "backend_registry": BackendRegistry, "executor": <共享 ThreadPoolExecutor>}`
- 共享 executor 来源：优先复用 GraphEngine 已有的（如果暴露），否则节点内部自建（R10 备选方案，`PARALLEL_ENSEMBLE_MAX_WORKERS` config，默认 8）
- 改动后跑 Phase 1 47 条回归确保未破坏

### P2.10 ParallelEnsembleNode 单测：事件序列 + capability/requirements + Trace storage（v2.4 扩展）

- `test_event_sequence`: mock runner + mock registries，断言 yield 序列 = `[N×StreamChunkEvent(is_final=False), 1×StreamChunkEvent(is_final=True), 1×StreamCompletedEvent]`，selector 全为 `[node_id, "text"]`
- `test_completed_outputs`: `outputs.text == 累计`、`tokens_count == steps`、`elapsed_ms` 合理
- `test_capability_mismatch_raises`: runner 要 `TOKEN_STEP` 但 backend 不声明 → `StructuredValidationError` 在启动期就抛
- `test_requirements_mismatch_raises`: openai backend + `top_k=25` requirement → `StructuredValidationError("top_logprobs is capped at 20, runner requested 25")`
- `test_validate_selection_propagates`: runner.validate_selection 返回 ValidationIssue → `StructuredValidationError`
- `test_single_model_timeout`: 单 backend raises `TimeoutError`，本轮 vote 不计该模型，最终 SUCCEEDED；trace.per_model_errors 含该 backend
- `test_all_timeout`: 全部 backend 超时 → `StreamCompletedEvent(status=FAILED)`
- `test_storage_inline`: `diagnostics.storage="inline"` → `outputs.trace` 进变量池可被下游引用
- `test_storage_metadata`: `diagnostics.storage="metadata"` → `metadata.ensemble_trace` 在运行历史可查，**不**进变量池（`outputs.text` 干净）
- `test_diagnostics_token_candidates_off`: `include_token_candidates=False` → `trace.token_trace[i].per_model = {}`（no-op，不爆 trace 大小）
- `test_dsl_rejects_model_url`: DSL 在 node `data` 里塞 `model_url` / `api_key` / `endpoint` 等 → `model_validator(mode="before")` 拒（顶层 `allow` 但显式 forbidden 名单）
- `test_dsl_rejects_runner_config_smuggle`: `runner_config` 里塞 `model_url` → 嵌套 `ParallelEnsembleConfig.extra="forbid"` 拒（业务子模型层）
- `test_dsl_compat_keys_allowed`: 顶层 `selected: True` / `params: {...}` / `paramSchemas: [...]` / `datasource_label: "x"` 都通过（BaseNodeData 兼容字段，详见 SPIKE_GRAPHON §4.3 + base_node_data）
- ⚠️ **不在本测试范围**：ModelSpec 层 `extra="forbid"`（yaml 防 typo / rogue 字段）已由 P2.1_LANDING.md smoke 2 覆盖，ModelSpec 不解析 DSL，不要把这两层混测。详见 SPIKE_GRAPHON §4.3–4.4 + EXTENSIBILITY_SPEC §4.4

### P2.11 前端：parallel-ensemble 包 + 三轴下拉 + ui_schema 反射表单 + DiagnosticsConfig 面板 + 9 处注册 + i18n（v2.4 大改）

`web/app/components/workflow/nodes/parallel-ensemble/`：

- `default.ts` / `types.ts` / `node.tsx` / `panel.tsx` / `use-config.ts`
- `components/model-selector.tsx`: MultiSelect，选项来自 `GET /console/api/workspaces/current/local-models`，**按当前选中 runner 的 `required_capabilities` 过滤模型**（不满足的 alias 灰掉 + tooltip 解释 "该模型不支持 token-step"）；选项展示 `BackendInfo{id, backend, model_name, capabilities}`
- `components/runner-selector.tsx`: 选项来自 `GET /console/api/workspaces/current/runners`，按 `i18n_key_prefix` 显示名 + tooltip
- `components/aggregator-selector.tsx`: 选项来自 `GET /console/api/workspaces/current/aggregators`，**按 `aggregator.scope == runner.aggregator_scope` 过滤**
- `components/dynamic-config-form.tsx`: 通用按 `ui_schema` 反射渲染（控件白名单：`number_input / text_input / textarea / switch / select / multi_select / model_alias_select`），不在白名单的字段直接报错；同一组件复用 runner_config + aggregator_config 表单
- `components/diagnostics-config.tsx`: `DiagnosticsConfig` 表单（include_model_outputs / include_token_candidates / include_logits / max_trace_tokens / storage 单选）
- `components/import-model-info-button.tsx`: 解析 `model_info.json`，**仅取 id 字段**自动勾选 alias，URL 等忽略
- 配置项分组：模型选择 / 协作模式 (runner) / 聚合策略 (aggregator) / 诊断输出 (diagnostics)
- runner / config 改变时实时调后端 `validate_requirements` API（防抖 500ms），把 ValidationIssue 渲染到对应字段红框 + i18n 翻译后的 tooltip
- 9 处注册 + i18n（同 P1.6 模式，`BlockEnum` 加 `ParallelEnsemble`）；每个 runner / aggregator 按其 `i18n_key_prefix` 注册 en-US + zh-Hans 两套（OQ-2 fallback：缺 key 显示 raw key + console.warn）

### P2.12 前端质量门：TS + lint + 三轴下拉 mock API 单测（v2.4 扩展）

- 跑 `pnpm type-check:tsgo` + `pnpm lint:fix`
- `web/app/components/workflow/nodes/parallel-ensemble/__tests__/`：
  - `panel.test.tsx`: 渲染所有配置项 + 三轴 selector
  - `model-selector.test.tsx`: mock `/local-models` API + 选 token_step runner，断言 anthropic backend 的模型被过滤掉
  - `runner-selector.test.tsx`: mock `/runners` API，渲染所有 runner + tooltip
  - `aggregator-selector.test.tsx`: 选 token_step runner 后只显示 scope=token 的 aggregator
  - `dynamic-config-form.test.tsx`: 喂 ui_schema fixture（含全部 7 种白名单控件 + 一个非白名单控件），后者应渲染 fallback 错误
  - `requirements-validation.test.tsx`: mock `/validate` API，断言 ValidationIssue 渲染到对应字段红框 + tooltip 文案
  - `diagnostics-config.test.tsx`: 改 storage 单选 → state 变化 → 持久化到 NodeData
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

### P2.15 性能基准 (vs PN.py) + 异常路径 + SSRF 抗压 + Trace 大小 boundary（v2.4 扩展）

- **性能**：相同 question / 模型 / `top_k` 下，节点 token/s 对比 PN.py（R4：差距 <3 token/s 视为通过；明显劣化先 profile 找瓶颈）
- **异常**：手动 kill 一个 backend → 该模型本轮空票，其他继续；kill 全部 → SUCCEEDED→FAILED 转换；capability 不匹配在启动期被捕（不让其活到运行期）
- **SSRF**：(1) 手动改 DSL JSON 在顶层塞 `model_url` 字段，导入应被 `model_validator(mode="before")` 拒；(2) 在 `runner_config` 嵌套塞 `model_url` 应被 `ParallelEnsembleConfig.extra="forbid"` 拒；(3) 节点配置写 `192.168.x.x` alias 但 yaml 没该 alias → `UnknownModelAliasError` 启动期就抛；(4) 真有 `192.168.x.x` alias 在 yaml 时，HTTP 强制走 `core.helper.ssrf_proxy` 拦截
- **Trace 大小 boundary**：`max_trace_tokens=1000`，跑 1500 步 token-level → 验 trace `truncated=True` + `len(token_trace)==1000` + 变量池/metadata 大小不爆（OQ-4：`storage="inline"` 时若大小超限自动降级到 metadata + warning）

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
- 每个 runner / aggregator 按其 `i18n_key_prefix` 注册 `<prefix>.{name,description,fields.<field>.{label,tooltip}}` 两套（OQ-2）
- CI: 跑 i18n key 一致性脚本（如有）；缺 key 时前端 fallback 显示 raw key + `console.warn`

---

## Phase 4 — v0.3 backend pack（4–7 天）

> 详见 EXTENSIBILITY_SPEC §11.3 / §12.1 + DEVELOPMENT_PLAN §7bis。Phase 4 与 Phase 3 平行可拆，**不阻塞 Phase 3 收尾**；研究侧用户如果只用 llama.cpp，Phase 4 可延期或剪除。前置依赖：P2.1.5 SPI 冻结 + P2.2.4 BACKEND_CAPABILITIES.md 已落地。

### P4.1 VllmBackend adapter（+2 天）

- 新建 `parallel_ensemble/backends/vllm.py`：`VllmSpec(BaseSpec)` 子类（`backend: Literal["vllm"]`、`base_url: AnyUrl`、`api_key_env: str | None = None`）+ `VllmBackend(ModelBackend)` `@register_backend("vllm")`
- `capabilities(spec)`: `STREAMING + TOKEN_STEP + TOP_PROBS + LOGITS_RAW`（vLLM `return_logits` 内部接口）；**不**含 `POST_SAMPLING_PROBS`（vLLM logprobs 是 log-softmax 不是 post-sampling 重归一）
- **关键坑（语义换算）**：`step_token` 内部把 `choices[0].logprobs.top_logprobs` 的 log-softmax 值 `exp()` 后 top-k 内部归一到和为 1，对齐 llama.cpp `top_probs` 的 post-sampling-prob 语义；adapter 内做，不让 aggregator 看到不一致的 float
- `validate_requirements(spec, requirements)`：默认实现兜底
- HTTP 走 `core.helper.ssrf_proxy`；`api_key_env` resolver 转 `SecretStr`，`__repr__` 自动 mask
- 单测：(1) capabilities 声明正确；(2) logprobs 换算 fixture（log-softmax → exp → 归一 == post-sampling-prob，误差 < 1e-6）；(3) `step_token` 调用 `ssrf_proxy.post`；(4) `LOGITS_RAW` runner（如 `token_step_strict`）能拿到 logits

### P4.2 OpenAICompatBackend adapter（+2 天）

- 新建 `parallel_ensemble/backends/openai_compat.py`：`OpenAICompatSpec(BaseSpec)`（`backend: Literal["openai_compat"]`、`base_url: AnyUrl`、`api_key_env: str`）+ `OpenAICompatBackend` `@register_backend("openai_compat")`
- `capabilities(spec)`: `STREAMING + TOKEN_STEP + TOP_PROBS + POST_SAMPLING_PROBS + FUNCTION_CALLING`；TOKEN_STEP 限 chat-completions 端点；某些模型（gpt-3.5-turbo-0301 等）spec 内置不支持 logprobs 时该 capability 不出
- **关键坑（精校验）**：`validate_requirements(spec, requirements)` 必须 override：
  - `min_top_k > 20` → `ValidationIssue("error", "OpenAI top_logprobs is capped at 20, runner requested {N}", "parallelEnsemble.errors.openaiTopKCap")`
  - `spec.model_name.startswith("gpt-3.5-turbo-0301")` 且 `needs_logprobs` → 拒
- `step_token` 用 chat-completions `max_tokens=1, logprobs=true, top_logprobs=k`
- HTTP 走 `core.helper.ssrf_proxy`；`api_key_env` resolver
- 单测：top_k=25 拒路径；gpt-3.5-turbo-0301 + logprobs 拒路径；正常 gpt-4o + top_k=10 通过；`step_token` 响应解析正确

### P4.3 AnthropicBackend adapter（+1 天）

- 新建 `parallel_ensemble/backends/anthropic.py`：`AnthropicSpec(BaseSpec)`（`backend: Literal["anthropic"]`、`api_key_env: str`）+ `AnthropicBackend` `@register_backend("anthropic")`
- `capabilities(spec)`: `STREAMING + FUNCTION_CALLING`（**不**含 `TOKEN_STEP / TOP_PROBS / LOGITS_RAW`，Anthropic 不暴露 logprobs）
- 仅实现 `generate / generate_stream`，`step_token` 走默认 `raise CapabilityNotSupported`；进 `token_step` runner 在 capability 粗过滤阶段就被排除（UI 灰掉 + 启动期 ValidationError）
- HTTP 走 `core.helper.ssrf_proxy`；`api_key_env` resolver
- 单测：capability 不含 TOKEN_STEP；进 token_step runner 被启动期拒；进 response_level runner 正常跑

### P4.4 跨 backend logprob 一致性 fixture（+1 天）

- `tests/unit_tests/core/workflow/nodes/parallel_ensemble/test_cross_backend_logprob.py`
- 喂三个 backend（llama_cpp 走 mock /completion 假 top_probs；vllm 走 mock /v1/completions 假 log-softmax；openai_compat 走 mock /v1/chat/completions 假 top_logprobs）同一 prompt + 同一公共 vocab 子集
- 各自 adapter 归一化后 → 断言 top-k 候选概率分布在公共 vocab 子集上误差 < 1e-3
- 防 adapter 内部归一化漏写；本测试是"加新 backend 时是否对齐 BACKEND_CAPABILITIES.md 语义"的最后兜底

### P4.5 Trace storage="artifact"（+1 天）

- `DiagnosticsConfig.storage` Literal 加 `"artifact"`：`Literal["inline", "metadata", "artifact"]`
- `TraceCollector.finalize` 当 `storage="artifact"` 时把 trace 写到附件存储（路径由 framework 统一，参考现有 file storage 抽象），`outputs.text` 干净，`metadata.ensemble_trace_artifact_id` 含 artifact 引用
- 解决 token 级 1k 步 trace 在 `metadata` 也偏大的场景
- 单测：1k 步 token-level trace 写入附件存储 → outputs.text 不含 trace；metadata 含 artifact id；通过 artifact id 能反取完整 EnsembleTrace；inline / metadata 两种旧 storage 不退化

---

## 进度跟踪

执行时用 `TaskList` / `TaskUpdate` 在 Claude Code 会话里操作；也可以手动在本文档标 ✅/🚧/⏭️。
