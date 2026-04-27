# Dify 多模型串并联开发计划

我现在要针对工作流模式进行修改。现在的工作流就是全回复级别的串联和并联，我现在要在工作流中增加token级别的并联操作，请你给出修改计划 还要考虑API的问题，要求并联过程中可以提供实时的logit值。

> **目标读者**：在 `xianghe/temp/dify` 这个 fork 上做二次开发的工程师。
> **基线代码**：`docs/ModelNet/PN.py`（参考算法）、`docs/ModelNet/model_info.json`（模型清单格式）。
> **修改版后端**：本地 llama.cpp 服务，`/completion` 端点已暴露 `completion_probabilities[0].top_probs`。
> **版本**：v2.4（v2 架构 review 修订 + v2.1 Phase 0 spike 闭环 + v2.2 P1.1 landing 前 schema 钉死 + v2.3 P1.1 schema 兜底加固 + **v2.4 把 EXTENSIBILITY_SPEC v0.2.2 的三轴 SPI 融入 Phase 2，新增 Phase 4 v0.3 backend pack**；review 见底部"修订历史"）。
>
> **架构合约（Phase 2 起）**：`docs/ModelNet/EXTENSIBILITY_SPEC.md` v0.2.2 是 Phase 2
> 三轴 SPI（ModelBackend / EnsembleRunner / Aggregator）+ Capability/Requirements
> 双层校验 + Trace 一等公民的**详细契约**。本文 §6 描述 Phase 2 的实施计划，
> 字段级 / 接口级签名以 EXTENSIBILITY_SPEC 为准。Phase 4 是 v0.3 增量的 backend
> 适配器包（vLLM / OpenAI / Anthropic + artifact storage），见 §7.5。

---

## 1. 目标与非目标

### 1.1 目标
1. 在 Dify 工作流画布上支持多模型**串并联组合**。
2. 新增**两类并联节点**：
   - **响应级并联**（Phase 1）：N 个 LLM 各自完整生成，再聚合（多数投票 / 拼接）。
   - **Token 级并联**（Phase 2）：N 个模型每步只前向一个 token，按 top-k logits 加和投票，等价于 PN.py。
3. Token 级节点支持**流式输出**（每选一个 token 立即推送）。
4. **三轴 SPI 易扩展**（Phase 2 起）：协作模式（runner）、模型后端（backend）、聚合策略（aggregator）三个独立可注册维度，加一种玩法 = 写一个类 + 注册一行；详见 `EXTENSIBILITY_SPEC.md` §2-7。
5. **诊断数据是一等公民**：每条诊断（token 候选、per-model 输出、think 痕迹、错误、耗时）通过 `diagnostics_config` 显式开关 + 标准化 `EnsembleTrace` schema 暴露；详见 `EXTENSIBILITY_SPEC.md` §7。
6. **同时支持 workflow 模式与 advanced-chat 模式**（节点本身模式无关，但终端节点不同）。

### 1.2 非目标（本期不做）
- 不打包成 Dify 插件（plugin 机制不支持新增 workflow 节点类型）。
- 不接入 Dify 的标准模型供应商抽象（直接打 llama.cpp HTTP）。
- 不做跨节点的 token 级流水线（token 循环必须封装在单一节点内）。
- 不做 KV-cache 复用优化（PN.py 的 `clear_slot_kv_cache` 暂不引入）。
- 不做模型注册表的图形化管理 UI（v1 用配置文件 + reload 即可）。

---

## 2. 架构决策记录（ADR）

| # | 决策 | 理由 |
|---|---|---|
| ADR-1 | 走「内置节点 + fork」，不走插件 | `node_factory.py:104-108` 的 `register_nodes()` 只从 `graphon.nodes` 与 `core.workflow.nodes` 两个包发现新节点；Dify 插件系统不支持新增 workflow 节点类型 |
| ADR-2 | 节点代码放在 `api/core/workflow/nodes/<node_pkg>/`，沿用 `agent/` 与 `knowledge_retrieval/` 的目录结构 | `_import_node_package("core.workflow.nodes")` 会自动扫描 |
| ADR-3 | 模型 URL **不暴露**到工作流节点配置；节点只引用「模型别名」，URL 在服务端注册表维护 | `api/AGENTS.md:140` 强制出站 HTTP 走 `core.helper.ssrf_proxy`；`node_factory.py:300/383` HTTP_REQUEST 节点已经是这套模式；让工作流作者填任意 URL 等于把内网访问交出去 |
| ADR-4 | Token 级循环封装在**单一节点内**，不展开成图层级的循环 | 每 token 一轮，跨节点调度开销过大 |
| ADR-5 | 模型注册表 v1 用 **YAML 配置文件 + 启动加载**，未来再升级到 DB+UI | 研究 fork 阶段，单租户、几个模型，YAML 完全够用 |
| ADR-6 | 聚合策略用 **Strategy Pattern + 注册表** | "易扩展"硬要求 |
| ADR-7 | Phase 2 必须做流式 | Token 投票每秒 5-15 token，非流式下用户面对 30s+ 空白屏 UX 崩 |
| ADR-8 | HTTP 客户端**必须**用 `core.helper.ssrf_proxy` | 既定项目规范 + 阻止 SSRF |
| ADR-9 | 同一节点同时通过 **workflow 模式（→End）和 advanced-chat 模式（→Answer）** 验收 | `update-dsl-modal.helpers.ts:46-56` 的 `getInvalidNodeTypes` 把 End 在 chat 模式判为无效 |
| ADR-10 | Phase 2 节点采用**三轴 SPI**（ModelBackend / EnsembleRunner / Aggregator） + 单一对外节点 `parallel-ensemble`；"协作模式"是节点配置项不是节点类型 | 评审正确指出 v1 把"加一种 runner = 加一个节点"会让每种玩法都跑一遍 9 处前端注册；改成 SPI 后写一个 Python 类 + 注册一行即可，与 P1 的 `@register("majority_vote")` 同级。详见 `EXTENSIBILITY_SPEC.md` §2 / EP-3 |
| ADR-11 | Capability **粗过滤** + Requirements **精校验** 双层；启动期跑完，绝不让不兼容组合活到运行期 | Capability 表达不了"top_k≤20"/"gpt-3.5-turbo-0301 不支持 logprobs"这类约束；引入 `runner.requirements(config)` + `backend.validate_requirements(spec, requirements)` 结构化校验。详见 `EXTENSIBILITY_SPEC.md` §3 / §9 |
| ADR-12 | Trace / Diagnostics 是一等数据面，不是附属物；规模通过 `storage` 策略（v0.2: inline / metadata；v0.3 加 artifact）控制 | 用户原始诉求"轻松获取 logit / 中间数据"决定了 trace 必须 schema 化（`EnsembleTrace`）+ 显式开关（`DiagnosticsConfig`），避免 `outputs.text` 爆炸。详见 `EXTENSIBILITY_SPEC.md` §7 |
| ADR-13 | 安全模型 = 「DSL/前端用户不可信，第三方 Python 扩展可信」；唯一硬边界是 DSL → 服务端 | Python 同进程拦不住反射 / `import requests`；要防恶意第三方扩展须走进程隔离 / wasm / RPC，超出 v0.1 范围。本规范的 SSRF 防护**仅**作用在 DSL 层（`extra="forbid"` 嵌套子模型 + ADR-3 `list_aliases()` 不返 url + ADR-8 `ssrf_proxy` 强制）；恶意扩展防护明确认账不做。详见 `EXTENSIBILITY_SPEC.md` §4.4 |

---

## 3. 阶段总览

| Phase | 内容 | 估时 | 关键交付 |
|---|---|---|---|
| Phase 0 | Spike：摸清 graphon 节点协议剩余未知项 | 0.5 天 | spike 报告 |
| Phase 1 | 响应级聚合节点 `ensemble-aggregator` | 5–7 天 | 节点能在画布上拖出、运行、出聚合结果（workflow + chat 两套验收） |
| Phase 2 | `parallel-ensemble` 节点 + 三轴 SPI 框架（v0.2 范围：仅 llama.cpp backend） | 16–19 天 | 节点能拖出、流式输出、SPI 三轴可注册扩展、Trace 入 metadata；详见 `EXTENSIBILITY_SPEC.md` §11.1 |
| Phase 3 | 测试 / 文档 / 示例工作流 / i18n | 3–4 天 | 单测 + 集成测试 + 4 份示例 DSL + README + `BACKEND_CAPABILITIES.md` |
| Phase 4 | v0.3 backend pack：vLLM / OpenAI-compat / Anthropic + artifact storage | 4–7 天 | 三个新 backend adapter + 跨 backend logprob 一致性 fixture + Trace `storage="artifact"` |

总计 **约 31 天**单人工作量（v2.3 的 26 天 + Phase 2 SPI 框架 +5 天 + Phase 4 v0.3 backend pack +4–7 天 — Phase 4 可拆出独立交付，不阻塞 Phase 3 收尾）。详细 v0.2 / v0.3 范围切分见 `EXTENSIBILITY_SPEC.md` §11.2-11.3。

---

## 4. Phase 0 — Spike

**目标**：澄清 graphon 内部协议的剩余未知项。Review 已闭环掉事件协议（Issue 2）；剩下的主要是节点类型与注册机制。

**Phase 0 状态（2026-04-18）**：✅ **全部完成**。Spike 报告 `docs/ModelNet/SPIKE_GRAPHON.md`；四项探针全绿；本章节 §4.1 的 Q1/Q3/Q4/Q5 均已闭环；§8 风险登记 R1 → closed、R10 → mitigated。

### 4.1 已验证的接口

| Q | 问题 | 状态 | 结论 |
|---|---|---|---|
| Q1 | 自定义 `node_type` 字面量值能否注册？ | ✅ 已闭环 | `NodeType: TypeAlias = str`（`graphon/enums.py:13`），`BuiltinNodeTypes` 是裸类常量容器而非枚举，docstring 明说"downstream packages can use additional strings without extending this class"。任意字符串合法 |
| Q2 | 流式事件 schema | ✅ 已闭环 | `StreamChunkEvent(selector=[node_id,'text'], chunk, is_final)` + `StreamCompletedEvent(node_run_result=...)`，见 `graphon/node_events/node.py:38-53`；dispatch 参考 `graphon/nodes/base/node.py:633-642` |
| Q3 | 节点自动注册机制 | ✅ 已闭环 | `Node.__init_subclass__`（`graphon/nodes/base/node.py:97-203`）；子类声明 `class X(Node[XData])` + `node_type: ClassVar[NodeType]` + `@classmethod version()` 即自动注册到 `Node._registry` |
| Q4 | `DifyNodeFactory` 是否需要加 init kwargs 分支？ | ✅ 已闭环 | mapping key 类型 `Mapping[NodeType, ...]` 实际就是 `Mapping[str, ...]`，新字符串合法。**聚合节点无外部依赖**→走默认空分支。**Token 节点需加一支**：注入 `local_model_registry` + `executor`，executor 在 `DifyNodeFactory.__init__` 新建共享 `ThreadPoolExecutor`（R10 备选方案，因为 factory 无现成共享池） |
| Q5 | 前后端 schema 一致性 | ✅ 已闭环 | `BaseNodeData.type: NodeType = str`（`graphon/entities/base_node_data.py:142`），前端 `BlockEnum` 是字符串枚举（`types.ts:28`）。序列化到 JSON 是普通字符串，Pydantic 直接接受，无需 validator |

### 4.2 Spike 产出
`docs/ModelNet/SPIKE_GRAPHON.md`（2026-04-18 产出），包含 Q1/Q3/Q4/Q5 证据、graphon 关键文件行号索引、对本计划的回溯动作清单，以及 `self.id == self._node_id` 的误述更正。

### 4.3 重要更正：`self.id` 并非 execution id

v1/v2 曾误述 `self.id` 为 execution id。实测 `graphon/nodes/base/node.py:256-276` 与 `node_factory.py:368-444`：

- `self.id == self._node_id`（都是 DSL graph 节点 id，由 `config["id"]` / `node_factory.py:368` 传入）
- execution id 存在 `self._node_execution_id`（懒加载 uuid4），通过 `self.execution_id` property 访问

selector 仍然用 `self._node_id`，理由是**与 graphon 内部 `_dispatch` 的约定一致**（第 637/653/662/679/688 行均用 `self._node_id`；仅 608/618 行的 NodeRunFailed/Succeeded 事件用 `self.id`）。

---

## 5. Phase 1 — 响应级聚合节点

### 5.1 节点语义
- **画布拓扑**：`Start → [LLM-A, LLM-B, LLM-C] → EnsembleAggregator → End/Answer`
  N 个标准 LLM 节点并连指向同一个 Aggregator，Dify 图引擎自带的线程池让分支并发执行（`GraphEngineConfig.max_workers`）。
- **节点输入**：N 个上游节点的输出（变量引用，类型 string）。
- **节点配置**：聚合策略名 + 策略参数（每个策略自带 schema）。
- **节点输出**：`text`（最终聚合文本）、`metadata.strategy`、`metadata.contributions`（每个上游贡献了什么）。
- **模式**：workflow 与 advanced-chat 都支持（节点本身模式无关）。

### 5.2 后端文件结构

```
api/core/workflow/nodes/ensemble_aggregator/
├── __init__.py
├── node.py                 # EnsembleAggregatorNode(Node)
├── entities.py             # EnsembleAggregatorNodeData (Pydantic)
├── exceptions.py           # 节点专属异常
└── strategies/
    ├── __init__.py
    ├── base.py             # AggregationStrategy ABC + register decorator
    ├── majority_vote.py    # @register("majority_vote")
    ├── concat.py           # @register("concat")
    └── registry.py         # _STRATEGY_REGISTRY: dict[str, type[AggregationStrategy]]
```

### 5.3 关键接口

**`entities.py`** — 注意 `type` 字段必须有
```python
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

class AggregationInputRef(BaseModel):
    # 嵌套 DTO 收紧：前端多传/拼错字段不得静默吞掉（顶层 BaseNodeData 仍保持 permissive）
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(..., min_length=1)          # 用户定义的稳定别名（同一节点内唯一），决定 metadata.contributions 键 + 并列 tie-break 字典序
    variable_selector: list[str] = Field(..., min_length=2)   # 例 ["llm_a", "text"]（graphon 约定 SELECTORS_LENGTH=2，第 3 段起是路径）

    @field_validator("source_id")
    @classmethod
    def _source_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("source_id must not be blank")
        return v

    @field_validator("variable_selector")
    @classmethod
    def _selector_segments_not_blank(cls, v: list[str]) -> list[str]:
        for i, seg in enumerate(v):
            if not seg or not seg.strip():
                raise ValueError(f"variable_selector segment [{i}] must not be blank")
        return v

class EnsembleAggregatorNodeData(BaseNodeData):
    type: NodeType = ENSEMBLE_AGGREGATOR_NODE_TYPE   # 包级常量 "ensemble-aggregator"；✅ Phase 0 Q1 已验证
    inputs: list[AggregationInputRef] = Field(..., min_length=2)
    strategy_name: Literal["majority_vote", "concat"] = "majority_vote"
    strategy_config: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _check_source_id_unique(self) -> "EnsembleAggregatorNodeData":
        seen: set[str] = set()
        for ref in self.inputs:
            if ref.source_id in seen:
                raise ValueError(f"Duplicate source_id '{ref.source_id}' in inputs; source_id must be unique")
            seen.add(ref.source_id)
        return self
```

**`strategies/base.py`**
```python
from abc import ABC, abstractmethod
from typing import TypedDict

class AggregationInput(TypedDict):
    source_id: str
    text: str

class AggregationResult(TypedDict):
    text: str
    metadata: dict          # 策略自定义诊断信息

class AggregationStrategy(ABC):
    name: str               # 注册名（由装饰器赋值）
    config_schema: dict     # JSON Schema for panel UI

    @abstractmethod
    def aggregate(
        self,
        inputs: list[AggregationInput],
        config: dict,
    ) -> AggregationResult: ...
```

**`strategies/registry.py`**
```python
_REGISTRY: dict[str, type[AggregationStrategy]] = {}

def register(name: str):
    def deco(cls: type[AggregationStrategy]) -> type[AggregationStrategy]:
        cls.name = name
        _REGISTRY[name] = cls
        return cls
    return deco

def get_strategy(name: str) -> AggregationStrategy:
    if name not in _REGISTRY:
        raise KeyError(f"Unknown strategy: {name}")
    return _REGISTRY[name]()

def list_strategies() -> list[dict]:
    return [{"name": cls.name, "config_schema": cls.config_schema} for cls in _REGISTRY.values()]
```

**`strategies/majority_vote.py`**
- v1 做"完全相同字符串"投票；并列时取上游 id 字典序最小者（**确定性**，保证测试稳定）。
- 后续可扩展：sentence-bert / Levenshtein / LLM-as-judge 做"语义投票"。
- `config_schema` v1 为空。

**`strategies/concat.py`**
- 配置项：`separator`（默认 `"\n\n---\n\n"`）、`include_source_label`（bool）。

**`node.py` 的 `_run()`** — 非流式
```python
from collections.abc import Generator
from graphon.node_events import NodeEventBase, NodeRunResult, StreamCompletedEvent
from graphon.nodes.base.node import Node
from graphon.enums import WorkflowNodeExecutionStatus

class EnsembleAggregatorNode(Node[EnsembleAggregatorNodeData]):
    node_type = "ensemble-aggregator"   # ⚠️ Phase 0 Q1 验证

    @classmethod
    def version(cls) -> str:
        return "1"

    def _run(self) -> Generator[NodeEventBase, None, None]:
        # 1. 从 VariablePool 取上游输出
        inputs = []
        for ref in self.node_data.inputs:
            seg = self.graph_runtime_state.variable_pool.get(ref.variable_selector)
            inputs.append({"source_id": ref.source_id, "text": str(seg.value)})

        # 2. 聚合
        strategy = get_strategy(self.node_data.strategy_name)
        result = strategy.aggregate(inputs, self.node_data.strategy_config)

        # 3. 完成（无中间块；非流式节点直接 emit Completed 即可）
        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(
                status=WorkflowNodeExecutionStatus.SUCCEEDED,
                outputs={
                    "text": result["text"],
                    "metadata": result["metadata"],
                },
                inputs={"source_count": len(inputs), "strategy": self.node_data.strategy_name},
            )
        )
```

### 5.4 前端文件结构

```
web/app/components/workflow/nodes/ensemble-aggregator/
├── default.ts              # 默认 config（strategy_name="majority_vote"）
├── node.tsx                # 画布上的节点缩略 UI
├── panel.tsx               # 右侧配置面板
├── types.ts                # TS 类型
├── use-config.ts           # 状态管理 hook
└── components/
    └── strategy-selector.tsx   # 下拉 + 动态 schema 表单
```

### 5.5 前端注册改动（**9 必填 + 1 选填**）

| # | 文件 | 用途 | 必填 |
|---|---|---|---|
| 1 | `web/app/components/workflow/types.ts` | `BlockEnum` 加 `EnsembleAggregator = 'ensemble-aggregator'` | ✅ |
| 2 | `web/app/components/workflow/block-selector/constants.tsx` | `BLOCKS` 加一项 | ✅ |
| 3 | `web/app/components/workflow/nodes/components.ts` | `NodeComponentMap` + `PanelComponentMap` 各加一行 | ✅ |
| 4 | `web/app/components/workflow/nodes/constants.ts` | 节点元数据（icon、颜色、默认配置） | ✅ |
| 5 | `web/app/components/workflow/constants.ts:111` `SUPPORT_OUTPUT_VARS_NODE` | 不加，下游引用不到我们的 `text` 输出 | ✅ |
| 6 | `web/app/components/workflow/nodes/_base/components/workflow-panel/last-run/use-last-run.ts:43` `singleRunFormParamsHooks` | TS strict 要求 `Record<BlockEnum, any>` 完整覆盖 | ✅ |
| 7 | `web/app/components/workflow/nodes/_base/components/variable/utils.ts:2092` `getNodeOutputVars` | switch-case 加一支：返回 `[[id, "text"]]`、`[[id, "metadata"]]` | ✅ |
| 8 | `web/i18n/en-US/workflow.ts` + `web/i18n/zh-Hans/workflow.ts` | 显示文案 | ✅ |
| 9 | `web/app/components/workflow/utils/workflow.ts:16` `canRunBySingle` | 单节点调试支持（聚合节点强烈建议加；token 级节点也加） | ✅ |
| 10 | `web/app/components/workflow/hooks/use-nodes-interactions.ts:594` | 仅 VariableAggregator/Assigner 用，我们**不需要** | ❌ |

### 5.6 后端注册改动
- 节点放进 `api/core/workflow/nodes/ensemble_aggregator/` 即被 `_import_node_package` 自动发现。
- **无外部依赖**（不调模型、不读文件），`node_factory.py:372-440` 的 `node_init_kwargs_factories` mapping **不需要加分支**（走默认空 kwargs 路径）。

### 5.7 验收标准（按模式拆分）

**Workflow 模式**
1. 画布上能拖出节点，能从 3 个上游 LLM 节点拉变量引用。
2. `majority_vote` + `["A","A","B"]` → `"A"`。
3. `concat` 默认分隔符 → `"A\n\n---\n\nA\n\n---\n\nB"`。
4. 完整图：`Start(query) → 3 LLM → Aggregator → End(text)` 跑通；输出可被 End 收集。
5. 节点单测：策略基类 + 两个策略 + 节点 `_run()`（mock VariablePool）。

**Advanced-chat 模式**
6. 完整图：`Start → 3 LLM → Aggregator → Answer({{aggregator.text}})`，浏览器能看到聚合后的文本。
7. DSL 文件能通过 `validateDSLContent(content, AppModeEnum.ADVANCED_CHAT)`（不能含 End）。

---

## 6. Phase 2 — Token 级并联节点（流式）

### 6.0 模型注册表（前置基础设施，**Phase 2 第一周做**）

#### 6.0.1 设计
- **配置源**：`api/configs/model_net.yaml`（项目级配置文件，仅运维/管理员维护）。
- **加载时机**：app 启动时一次性加载到 `LocalModelRegistry` 单例；提供 `reload()` 给后续热更新（可选）。
- **节点引用**：节点配置中只存 `model_aliases: list[str]`（即 yaml 里的 `id` 字段）。
- **HTTP 客户端**：所有调 llama.cpp 的请求**必须**通过 `core.helper.ssrf_proxy`（ADR-8）。

#### 6.0.2 配置文件 schema（与 `docs/ModelNet/model_info.json` 字段名严格一致）
```yaml
# api/configs/model_net.yaml
models:
  - id: "qwen3-4b"                                         # 别名（节点引用用）
    model_name: "qwen3-4b-bf16"
    model_arch: "llama"
    model_url: "http://219.222.20.79:30763"                # 仅服务端可见
    EOS: "<|im_end|>"                                       # 注意大写
    type: "think"                                           # normal | think
    stop_think: "</think>"                                  # type=think 必填
    weight: 1.0                                             # 默认 1.0
    request_timeout_ms: 30000                               # 默认 30000
  - id: "llama-3.1-8b"
    model_name: "llama-31-8b"
    model_arch: "llama"
    model_url: "http://219.222.20.79:30431"
    EOS: "<|end_of_text|>"
    type: "normal"
    stop_think: null
```

字段命名直接对齐 `model_info.json`（`EOS` 大写、`stop_think` 下划线），**确保现有 PN.py 用户能 1:1 迁移**。

#### 6.0.3 模块结构

> **路径修订（2026-04-18，TASKS.md 顶部决定）**：本节原写 `api/core/model_runtime/local_models/`，
> 实际落地放在 `api/core/workflow/nodes/parallel_ensemble/llama_cpp/`——理由是当前 fork 已删除
> `api/core/model_runtime/`，且和 Phase 2 节点同包更便于注入与测试。
>
> **SPI 升级路径（v2.4）**：P2.1 已落地的 `LocalModelRegistry` + `ModelSpec` 在 P2.1.5 SPI 冻结
> 后会按 EXTENSIBILITY_SPEC §4.3 升级为 `ModelRegistry`（重命名）+ `BaseSpec` / `LlamaCppSpec`
> 拆分（字段不变，加 `backend: Literal["llama_cpp"]` discriminator），并接入 `BackendRegistry`
> 动态分发（不用静态 Annotated Union — 静态 union 第三方 backend 进不来，见
> EXTENSIBILITY_SPEC §4.3 v0.2 修订）。**已落地代码不重写，只升级**。

```
api/core/workflow/nodes/parallel_ensemble/llama_cpp/   # P2.1 已落地，P2.1.5/P2.2 升级
├── __init__.py
├── registry.py           # LocalModelRegistry, ModelSpec     (P2.1 ✅，P2.1.5 升级为 ModelRegistry + BaseSpec)
├── client.py             # LlamaCppBackend(ModelBackend)     (P2.2 待落地，按 SPI §4 实现)
└── exceptions.py         # LlamaCppNodeError 树              (P2.1 ✅)
```

#### 6.0.4 关键接口
```python
# registry.py
from typing import Literal
from pydantic import BaseModel, AnyUrl, ConfigDict
import yaml
from configs import dify_config

class ModelSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    model_name: str
    model_arch: str = "llama"
    model_url: AnyUrl                          # ⚠️ 仅服务端
    EOS: str
    type: Literal["normal", "think"] = "normal"
    stop_think: str | None = None
    weight: float = 1.0
    request_timeout_ms: int = 30000

class LocalModelRegistry:
    _instance: "LocalModelRegistry | None" = None
    _models: dict[str, ModelSpec]

    @classmethod
    def instance(cls) -> "LocalModelRegistry":
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load()
        return cls._instance

    def _load(self) -> None:
        path = dify_config.MODEL_NET_REGISTRY_PATH  # 新加的 config 项，默认 api/configs/model_net.yaml
        with open(path) as f:
            raw = yaml.safe_load(f)
        self._models = {m["id"]: ModelSpec.model_validate(m) for m in raw.get("models", [])}

    def get(self, alias: str) -> ModelSpec:
        if alias not in self._models:
            raise KeyError(f"Unknown model alias: {alias}")
        return self._models[alias]

    def list_aliases(self) -> list[dict]:
        """供前端下拉，仅返回 id + model_name + type，**不返回 URL**"""
        return [
            {"id": m.id, "model_name": m.model_name, "type": m.type}
            for m in self._models.values()
        ]
```

```python
# llama_cpp_client.py —— 严格按 PN.py 调用，但 HTTP 走 ssrf_proxy
from core.helper import ssrf_proxy

class LlamaCppClient:
    def __init__(self, spec: ModelSpec):
        self._spec = spec
        self._timeout_s = spec.request_timeout_ms / 1000

    def apply_template(self, messages: list[dict]) -> str:
        url = f"{self._spec.model_url}/apply-template"
        resp = ssrf_proxy.post(url, json={"messages": messages}, timeout=self._timeout_s)
        resp.raise_for_status()
        return resp.json().get("prompt", "")

    def completion(
        self,
        prompt: str,
        max_tokens: int = 1,
        n_probs: int = 5,
        post_sampling_probs: bool = True,
        stop: list[str] | None = None,
    ) -> dict:
        url = f"{self._spec.model_url}/completion"
        body = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "n_probs": n_probs,
            "post_sampling_probs": post_sampling_probs,
        }
        if stop:
            body["stop"] = stop
        resp = ssrf_proxy.post(url, json=body, timeout=self._timeout_s)
        resp.raise_for_status()
        return resp.json()
```

#### 6.0.5 控制台 API（前端下拉用）
- 路径：`GET /console/api/workspaces/current/local-models`
- 返回：`{"models": [{"id":"qwen3-4b","model_name":"qwen3-4b-bf16","type":"think"}, ...]}`
- 控制器：`api/controllers/console/workspace/local_models.py`
- **不返回 URL**（按 ADR-3 隔离）。

### 6.1 节点语义与模式兼容
- **画布拓扑**（按模式）：

| 模式 | 拓扑 | 终端节点 |
|---|---|---|
| workflow | `Start → ParallelEnsemble → End(text)` | `End` |
| advanced-chat | `Start → ParallelEnsemble → Answer({{node.text}})` | `Answer`（流式 chunk 自动转发） |

- **节点输入**：`question`（string，从上游变量 selector 取）。
- **节点配置**：
  - `model_aliases: list[str]`（必须，至少 1 个）
  - `top_k`（默认 5）
  - `max_len`（默认 1000）
  - `aggregator_name`（默认 `sum_score`）
  - `aggregator_config`（默认 `{}`）
  - `enable_think`（默认 true，是否走 PN.py 的"思考"前置阶段）
- **节点输出**：`text`、`tokens_count`、`elapsed_ms`、`per_model_contribution`（可选诊断）。
- **流式行为**：每选定一个 token 立即 emit `StreamChunkEvent`；末尾 emit `is_final=True` 的封口块 + `StreamCompletedEvent`。

### 6.2 后端文件结构（v2.4 SPI 化重写，对齐 EXTENSIBILITY_SPEC §10）

```
api/core/workflow/nodes/parallel_ensemble/
├── __init__.py                 # PARALLEL_ENSEMBLE_NODE_TYPE 常量（P2.1 ✅）
├── node.py                     # ParallelEnsembleNode(Node) — 入口、事件协议、Trace finalize
├── entities.py                 # ParallelEnsembleNodeData + DiagnosticsConfig
├── exceptions.py               # LlamaCppNodeError 树（P2.1 ✅）+ CapabilityNotSupported / StructuredValidationError
│
├── spi/                        # ★ 三轴 SPI 接口冻结（P2.1.5）
│   ├── __init__.py
│   ├── capability.py           # Capability enum
│   ├── requirements.py         # Requirement / ValidationIssue TypedDict
│   ├── backend.py              # ModelBackend ABC + BaseSpec + ChatMessage/GenerationParams/...
│   ├── runner.py               # EnsembleRunner ABC + RunnerEvent + ui_schema 白名单
│   ├── aggregator.py           # ResponseAggregator / TokenAggregator typed bases + AggregationContext
│   └── trace.py                # EnsembleTrace + TraceCollector
│
├── registry/                   # 注册表（v2.4 拆分；P2.1 的 LocalModelRegistry 升级为 ModelRegistry）
│   ├── __init__.py
│   ├── model_registry.py       # ModelRegistry（升级 P2.1，按 backend 字符串动态分发 spec_class）
│   ├── backend_registry.py     # @register_backend("name")
│   ├── runner_registry.py      # @register_runner("name")
│   └── aggregator_registry.py  # @register_aggregator("name", scope="...")
│
├── backends/                   # ★ v0.2 仅 llama_cpp；vllm/openai/anthropic 留 Phase 4
│   ├── __init__.py
│   └── llama_cpp.py            # LlamaCppBackend（升级 P2.1 LlamaCppClient → ModelBackend SPI）
│
├── runners/                    # ★ v0.2 两个参考 runner
│   ├── __init__.py
│   ├── response_level.py       # ResponseLevelRunner — 包 P1 ensemble-aggregator 现有逻辑（P2.6.5）
│   └── token_step.py           # TokenStepRunner — PN.py 主循环（P2.6）
│
└── aggregators/
    ├── response/               # scope="response"，平滑迁移 P1 majority_vote / concat
    │   ├── __init__.py
    │   ├── majority_vote.py
    │   └── concat.py
    └── token/                  # scope="token"
        ├── __init__.py
        ├── sum_score.py
        └── max_score.py
```

`pkgutil.walk_packages` 在 `_import_node_package` 里递归扫描，所有 `@register_*` 装饰器自动生效；P1 `ensemble-aggregator` 节点保留为"响应级 fast path"**不删**——它是 backwards-compat 的着陆点。

> 注：`llama_cpp/` 下的 `registry.py` / `client.py` / `exceptions.py` 是 P2.1 已落地路径；P2.1.5 起，`registry.py` 内容拆迁到 `registry/model_registry.py`，`client.py` 升级为 `backends/llama_cpp.py`，`exceptions.py` 保留为节点层异常树。
>
> 接口签名级细节（每个 ABC、TypedDict、装饰器）以 `EXTENSIBILITY_SPEC.md` §3-7 为准。

### 6.3 关键模块设计（v2.4：节点层只描述对外 schema；SPI 内部接口见 §6.7）

**`entities.py`**（v2.4 改：runner_name / aggregator_name / runner_config / aggregator_config / diagnostics 五元组）
```python
from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spi.trace import DiagnosticsConfig    # 见 §6.7 / EXTENSIBILITY_SPEC §7.1


class ParallelEnsembleConfig(BaseModel):
    """嵌套业务配置；用 extra="forbid" 锁死（顶层 NodeData 须保 allow 兼容
    BaseNodeData 的 selected/params/paramSchemas/datasource_label）。
    DSL 偷塞 model_url 等敏感字段 → 在此层 forbid 拒。详见 SPIKE_GRAPHON §4.3。"""
    model_config = ConfigDict(extra="forbid")

    question_variable: list[str] = Field(min_length=2)
    model_aliases: list[str] = Field(min_length=1)         # ⚠️ 仅 alias，无 URL；ADR-3

    runner_name: str = Field(min_length=1)                  # registry key
    runner_config: dict[str, object] = Field(default_factory=dict)

    aggregator_name: str = Field(min_length=1)
    aggregator_config: dict[str, object] = Field(default_factory=dict)

    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)


class ParallelEnsembleNodeData(BaseNodeData):
    """顶层 NodeData：保留 BaseNodeData 的 extra="allow"（兼容 graphon 兼容字段），
    但加 model_validator 显式拒绝已知敏感字段 + 业务配置封到 ensemble 子模型。"""
    type: NodeType = "parallel-ensemble"           # ✅ P2.1 已挂常量
    ensemble: ParallelEnsembleConfig

    @model_validator(mode="before")
    @classmethod
    def _reject_sensitive_top_level(cls, data):
        # T1（EXTENSIBILITY_SPEC §4.4）：DSL 不能在 NodeData 顶层塞 url/api_key 类字段
        if isinstance(data, dict):
            forbidden = {"model_url", "api_key", "api_key_env", "url", "endpoint"}
            stray = forbidden & data.keys()
            if stray:
                raise ValueError(f"forbidden top-level fields: {sorted(stray)}")
        return data
```

**Runner / Aggregator / Backend 接口、Capability 枚举、Trace schema** —— 见 §6.7（SPI 接口冻结）+ `EXTENSIBILITY_SPEC.md` §3-7。本文不再重复粘签名，避免双源真相。

**响应级聚合策略（majority_vote / concat）**：直接复用 P1 已落地实现，P2.5 时改基类
为 `ResponseAggregator[ConfigT]`（`AggregationContext` 默认参数，对 P1 调用点零影响）。

**Token 级聚合策略（sum_score / max_score）**：新建 `aggregators/token/`，继承
`TokenAggregator`；签名 `aggregate(signals: TokenSignals, ctx: AggregationContext, config) -> TokenPick`，
确定性（并列取字典序最小，禁 `random.choice`）。

**TokenStepRunner**（`runners/token_step.py`，P2.6 落地）：等价 PN.py 主循环
（每轮 ThreadPoolExecutor 并发 `backend.step_token(prompt, top_k)`、`aggregator.aggregate(...)`、
所有 prompt 同步追加 token、`yield TokenEvent(...)`）；终止条件 `<end>` / `max_len`；
`trace.record_token_step({...})` 由 `TraceCollector` 按 `DiagnosticsConfig` 决定真存还是 no-op。

**ResponseLevelRunner**（`runners/response_level.py`，P2.6.5）：包装 P1
`ensemble-aggregator` 现有响应级语义；并发调 `backend.generate`，收齐喂
`ResponseAggregator.aggregate`，`yield DoneEvent`。

### 6.4 节点 `_run()` —— **流式事件契约（按 Issue 2 修订）**

```python
# node.py
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from graphon.node_events import (
    NodeEventBase,
    NodeRunResult,
    StreamChunkEvent,         # ⚠️ 名字是 StreamChunkEvent，不是 NodeRunStreamChunkEvent
    StreamCompletedEvent,
)
from graphon.nodes.base.node import Node
from graphon.enums import WorkflowNodeExecutionStatus

class ParallelEnsembleNode(Node[ParallelEnsembleNodeData]):
    node_type: ClassVar[NodeType] = "parallel-ensemble"   # ✅ Phase 0 Q3 已验证自动注册路径

    def __init__(
        self,
        id: str,
        config,
        graph_init_params,
        graph_runtime_state,
        *,
        # v2.4 SPI 化：5 个依赖由 DifyNodeFactory 注入（P2.9 节，对齐 Phase 0 Q4）
        model_registry: "ModelRegistry",
        runner_registry: "RunnerRegistry",
        aggregator_registry: "AggregatorRegistry",
        backend_registry: "BackendRegistry",
        executor: ThreadPoolExecutor,                # 共用线程池
    ):
        super().__init__(id=id, config=config,
                         graph_init_params=graph_init_params,
                         graph_runtime_state=graph_runtime_state)
        self._model_registry = model_registry
        self._runner_registry = runner_registry
        self._aggregator_registry = aggregator_registry
        self._backend_registry = backend_registry
        self._executor = executor
        self._ssrf_http = ...    # core.helper.ssrf_proxy 句柄，由 backend 实例化时注入

    @classmethod
    def version(cls) -> str:
        return "1"

    def _run(self) -> Generator[NodeEventBase, None, None]:
        cfg = self.node_data.ensemble    # ParallelEnsembleConfig，见 §6.3

        # 1. 取 question
        seg = self.graph_runtime_state.variable_pool.get(cfg.question_variable)
        question = str(seg.value)

        # 2. alias → spec → backend 实例（backend 持 url/key，不外漏；§4.4 EP-4）
        specs = [self._model_registry.get(a) for a in cfg.model_aliases]
        backends: dict[str, ModelBackend] = {
            s.id: self._backend_registry.get(s.backend)(s, http=self._ssrf_http)
            for s in specs
        }

        # 3. runner / aggregator 解析（注册表反查，名字未注册抛 ValidationError）
        runner_cls = self._runner_registry.get(cfg.runner_name)
        aggregator_cls = self._aggregator_registry.get(cfg.aggregator_name)
        runner_config = runner_cls.config_class.model_validate(cfg.runner_config)
        aggregator_config = aggregator_cls.config_class.model_validate(cfg.aggregator_config)

        # 4. Trace 门面（按 cfg.diagnostics 决定真存还是 no-op）
        trace = TraceCollector(cfg.diagnostics, max_token_steps=cfg.diagnostics.max_trace_tokens)

        # 5. 跑 runner — 流式翻译为 graphon 事件
        accumulated = ""
        t0 = time.time()
        runner = runner_cls()
        for event in runner.run(question, backends, aggregator_cls(), runner_config, trace):
            if event["kind"] == "token":
                accumulated += event["delta"]
                yield StreamChunkEvent(selector=[self._node_id, "text"],
                                        chunk=event["delta"], is_final=False)
            elif event["kind"] == "done":
                accumulated = event["text"]    # 非流式 runner 直接给最终 text
                # 不在循环里 break，让 runner 决定是否再 yield 后续

        # 6. 封口块
        yield StreamChunkEvent(selector=[self._node_id, "text"], chunk="", is_final=True)

        # 7. Trace finalize → 按 storage 策略写 outputs / metadata（EXTENSIBILITY_SPEC §7.4）
        trace_data = trace.finalize()
        outputs = {"text": accumulated,
                    "tokens_count": trace_data["summary"]["tokens_count"],
                    "elapsed_ms": int((time.time() - t0) * 1000)}
        metadata: dict = {}
        if cfg.diagnostics.storage == "inline":
            outputs["trace"] = trace_data
        elif cfg.diagnostics.storage == "metadata":
            metadata["ensemble_trace"] = trace_data

        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(
                status=WorkflowNodeExecutionStatus.SUCCEEDED,
                outputs=outputs,
                metadata=metadata,
                inputs={"question": question, "models": list(backends.keys())},
            )
        )
```

> 上文为 v2.4 SPI 化伪代码，省略了 capability + requirements 启动期校验、单 backend 超时聚合、`FullResponseEvent` 处理（judge runner）。完整流程见 `EXTENSIBILITY_SPEC.md` §9 校验流水线 + §5/§7。

**关键差异点提醒**（v1 文档错的地方）：
1. 事件名 `StreamChunkEvent` ≠ `NodeRunStreamChunkEvent`（后者是 graphon 内部 `_dispatch` 转换后的事件，节点自身只 yield `StreamChunkEvent`；参考 `graphon/nodes/base/node.py:633-642`）
2. selector 用 `[self._node_id, "text"]`。⚠️ **Phase 0 更正**：v1/v2 曾误述 `self.id` 是 execution id，实测 `self.id == self._node_id`（都是 graph 节点 id）。约定上仍用 `self._node_id`，因为 graphon 内部 dispatch 里多数用这个，保持一致性
3. `StreamCompletedEvent` 的参数名是 `node_run_result=`，不是位置参数或 `run_result=`
4. 末尾必须发一个 `is_final=True` 的空 chunk 封口（参考 agent 的实现）

### 6.5 前端要点
- `panel.tsx` 顶部用 **下拉多选**（`MultiSelect` 组件）选模型 alias，选项来自 `GET /console/api/workspaces/current/local-models`。
- **去掉**之前设计的 JSON 编辑器（不再让用户编辑模型 URL）。
- 提供 "导入 model_info.json" 按钮：解析后**只取 `id` 字段**自动勾选对应 alias，URL 等敏感字段忽略。
- 配置项分组：模型选择 / 推理参数 / 聚合策略。
- 流式输出在画布运行预览面板按 LLM 节点既有方式渲染（chunks 拼接）；advanced-chat 模式下 Answer 节点会自动转发 chunk 到聊天 UI。

### 6.6 流式实现的两步走
**Step 1：纯 Python 验证逻辑**
- `TokenStepRunner.run()` 写完后，写一个 `pytest` mock `LlamaCppBackend`（实现 `step_token()`），喂假的 top-k 候选序列，断言：
  - 终止条件正确（`<end>` / `max_len`）
  - 聚合策略正确（确定性输出，确保字典序 tie-break）
  - prompt 同步正确（每轮所有模型 prompt 都加同一个 token）
  - `TraceCollector.record_token_step` 按 `DiagnosticsConfig` 真存 / no-op
- 此时**完全不依赖 graphon**（runner SPI 与 graphon 解耦，仅 node.py 套 graphon 事件）。

**Step 2：套上 graphon 事件协议**
- 实现 `_run()`，用上节 6.4 验证过的事件签名。
- 调试方法：先在 dev server 跑 workflow 模式最小图（`Start → ParallelEnsemble → End`），看后端日志是否按节奏 emit chunk；再换 chat 模式（→ Answer）看浏览器是否流式渲染。
- **风险点**：v1 提到的"事件 schema 错了不会报错"已经被消除（schema 已确认）；剩余风险是 selector 写错（用了 `self.id` 而非 `self._node_id`），review 已警示。

### 6.7 验收标准（按模式拆分）

**Workflow 模式**
1. 节点能在画布上拖出，模型选择下拉来自后端 API（不暴露 URL）。
2. 单测：聚合器 + engine 主循环 + think phase（mock client）。
3. 集成（CI-only）：用真实修改版 llama.cpp 后端跑一个 question，2-3 个模型，能完整出答案，行为与 PN.py 一致。
4. DSL：`Start → ParallelEnsemble → End(text)` 能跑，`workflow run` API 返回最终文本。
5. 异常路径：单个模型超时 → 该模型本轮投票视为空，其他继续；全部超时 → emit `StreamCompletedEvent(status=FAILED)`。
6. SSRF：尝试在配置文件以外的地方塞 URL（如直接编辑 DSL 加 `model_url` 字段）→ 应被 Pydantic `extra="forbid"` 拒绝。

**Advanced-chat 模式**
7. DSL：`Start → ParallelEnsemble → Answer({{node.text}})` 能在 chat 应用中跑。
8. 浏览器看到文字逐字出现（不是一次性出现）；首 token 延迟 ≤ think 阶段时长 + 1 个 token 时间。
9. DSL 文件能通过 `validateDSLContent(content, AppModeEnum.ADVANCED_CHAT)`。

### 6.7 SPI 接口冻结（v2.4 新章 — 引用 `EXTENSIBILITY_SPEC.md` 为详细契约）

> Phase 2 引入三轴 SPI 是为了让"加一种协作玩法"压缩到"写一个 Python 类 +
> 注册一行"。以下接口在 P2.1.5 冻结后**禁止破坏性变更**；扩展走子类化或新注册项。

**Capability 枚举**（`spi/capability.py`）：`STREAMING / TOKEN_STEP / TOP_PROBS / POST_SAMPLING_PROBS / LOGITS_RAW / CHAT_TEMPLATE / FUNCTION_CALLING / KV_CACHE_REUSE`。详见 EXTENSIBILITY_SPEC §3.1-3.2。

**Requirements 精校验**（`spi/requirements.py`，ADR-11）：`Requirement{kind, value, rationale}` + `ValidationIssue{severity, requirement, message, i18n_key}`，`runner.requirements(config)` → `backend.validate_requirements(spec, requirements)`。详见 EXTENSIBILITY_SPEC §3.4。

**ModelBackend ABC**（`spi/backend.py`）：`name` / `spec_class` ClassVar，公开 `id` / `model_name` / `weight` / `instance_capabilities` property，方法 `capabilities(spec)` / `validate_requirements(...)` / `generate(prompt, params)` / `generate_stream` / `step_token` / `apply_template`。详见 EXTENSIBILITY_SPEC §4.1。

**EnsembleRunner ABC**（`spi/runner.py`）：`name` / `config_class` / `aggregator_scope` / `required_capabilities` / `optional_capabilities` / `i18n_key_prefix` / `ui_schema` ClassVar；方法 `requirements(config)` / `validate_selection(config, aliases, registry)` / `run(question, backends, aggregator, config, trace) -> Iterator[RunnerEvent]`。详见 EXTENSIBILITY_SPEC §5.1。

**Aggregator typed bases**（`spi/aggregator.py`，ADR-12 配套）：`Aggregator[ConfigT, SignalT, ResultT]` 通用基类 + `ResponseAggregator[ConfigT]`（scope="response"）+ `TokenAggregator[ConfigT]`（scope="token"），`AggregationContext` 注入 weights / capabilities / step_index / trace 句柄。详见 EXTENSIBILITY_SPEC §6。

**Trace + DiagnosticsConfig**（`spi/trace.py`，ADR-12）：`DiagnosticsConfig`（`storage: Literal["inline","metadata"]`，v0.3 加 `"artifact"`） + `EnsembleTrace` schema + `TraceCollector` 门面。runner / aggregator 调 `trace.record_*()`，是否真存由 collector 按 config 决定。详见 EXTENSIBILITY_SPEC §7。

**校验流水线**（启动期 / DSL 导入时跑完）：(1) aggregator scope 对齐 runner ；(2) runner_config / aggregator_config schema 校验；(3) Capability 粗过滤；(4) Requirements 精校验；(5) `runner.validate_selection` 跨字段校验。任一失败 → `StructuredValidationError`，节点标红。详见 EXTENSIBILITY_SPEC §9。

### 6.8 v0.2 范围 vs Phase 4 (v0.3) 切分

| 项 | v0.2（Phase 2 落地） | v0.3（Phase 4 增量） |
|---|---|---|
| Backend 数 | 1（`llama_cpp`） | +3（`vllm` / `openai_compat` / `anthropic`） |
| Runner 参考实现 | 2（`response_level` / `token_step`） | +0（v0.3 不强制加新 runner，第三方按 SPI 自加） |
| 第三方发现路径 | (a) fork 内目录 `runners/` `backends/` `aggregators/<scope>/` | + (b) `model_net.yaml` 顶层 `extra_backend_modules` / `extra_runner_modules` / `extra_aggregator_modules` 显式 import path |
| Trace storage 选项 | `inline` / `metadata` | + `artifact`（附件存储） |
| 跨 backend logprob 一致性测试 | 不做（只一个 backend） | 加 fixture 单测 |
| 工时估计 | +5 天 | +4–7 天 |

详见 EXTENSIBILITY_SPEC §11.2 / §11.3 / §12.1。

---

## 7. Phase 3 — 测试 / 文档 / 示例工作流

### 7.1 测试
| 层级 | 文件 | 内容 |
|---|---|---|
| 单测 | `api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/` | 策略 + 节点 |
| 单测 | `api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/` | runners / aggregators / node._run / capability 粗过滤 / requirements 精校验 / TraceCollector 开关 / storage(inline\|metadata) 路由（mock backend + mock registry） |
| 单测 | `api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/llama_cpp/` | registry 加载 / Pydantic validation / `extra="forbid"` 拒绝未知 yaml 字段 / `ssrf_proxy` mock + `LlamaCppBackend` 6 方法（capabilities / validate_requirements / generate / generate_stream / step_token / apply_template） |
| 前端单测 | `web/app/components/workflow/nodes/ensemble-aggregator/__tests__/` | panel + use-config |
| 前端单测 | `web/app/components/workflow/nodes/parallel-ensemble/__tests__/` | 同上 + 模型下拉（mock API） |

集成测试是 CI-only（CLAUDE.md 明确说本地不跑），**集成测试用例的提交允许，但本地验证靠 dev server**。

### 7.2 示例 DSL（4 份）
- `docs/ModelNet/examples/workflow_mode/response_level_ensemble.yml`：3 LLM → aggregator → End
- `docs/ModelNet/examples/workflow_mode/token_level_ensemble.yml`：parallel-ensemble + 3 模型 → End
- `docs/ModelNet/examples/chat_mode/response_level_ensemble.yml`：3 LLM → aggregator → Answer
- `docs/ModelNet/examples/chat_mode/token_level_ensemble.yml`：parallel-ensemble → Answer

每份附 `README.md` 说明用什么 alias、需要 `model_net.yaml` 里有哪些模型。

### 7.3 文档
- `docs/ModelNet/README.md`：节点用法、model_net.yaml schema、流式行为说明、模式选择指南。
- `docs/ModelNet/SECURITY.md`：为什么 URL 不暴露给节点配置、谁能改 yaml、reload 流程。

### 7.4 i18n
- `web/i18n/en-US/workflow.ts` 和 `web/i18n/zh-Hans/workflow.ts` 加节点显示名 / 配置项标签 / 错误提示。
- 每个 runner / aggregator 按 `i18n_key_prefix` 注册：`<prefix>.name` / `<prefix>.description` / `<prefix>.fields.<field>.{label,tooltip}` 必须 en-US + zh-Hans 两套都有；CI lint 检查注册项 vs i18n key 集（OQ-2）。

### 7.5 `BACKEND_CAPABILITIES.md`（v2.4 新增）
- 把 EXTENSIBILITY_SPEC §3.2 capability 矩阵 + §3.2 三个语义坑（POST_SAMPLING_PROBS vs LOGITS_RAW / OpenAI top_k≤20 / vLLM logprobs 单位换算）钉死成独立文档，附 fixture 测试
- 是 Phase 4 加 backend 时对齐语义的合约文件；P2.2.4 落地（Phase 2 内）

---

## 7bis. Phase 4 — v0.3 backend pack（4–7 天）

> **目的**：在 Phase 2 三轴 SPI 冻结后，按 EXTENSIBILITY_SPEC §11.3 / §12.1 落地三个新
> backend 适配器 + 跨 backend logprob 一致性测试 + Trace `artifact` storage。
> Phase 4 与 Phase 3 平行可拆，**不阻塞 Phase 3 收尾**；研究侧用户如果只用
> llama.cpp，Phase 4 可延期或剪除。

### 7bis.1 范围

| 包 | 内容 | 估时 | 备注 |
|---|---|---|---|
| `VllmBackend` adapter | 实现 `ModelBackend` 6 方法 + capabilities 矩阵 + logprobs (log-softmax → exp 归一) 语义换算 | +2 天 | 关键坑：vLLM `logprobs` 是 log-softmax，必须 adapter 内换算到与 llama.cpp `top_probs` 一致的 post-sampling-prob 语义；`LOGITS_RAW` 通过 `return_logits` 内部接口可拿 |
| `OpenAICompatBackend` adapter | chat-completions 端点 + `top_logprobs ≤ 20` 的 `validate_requirements` 拒；`api_key_env` resolver | +2 天 | 不支持 `LOGITS_RAW`；`top_k > 20` 在 requirements 精校验阶段就拒绝，不留给运行期 |
| `AnthropicBackend` adapter | 仅 `STREAMING` + `FUNCTION_CALLING`，不实现 `step_token`（不暴露 logprobs） | +1 天 | 仅能进 `response_level` runner；进 `token_step` 在 capability 粗过滤阶段就被排除 |
| 跨 backend logprob 一致性 fixture | 喂三个 backend 同一 prompt，断言 top-k 候选概率分布在公共 vocab 子集上误差 < ε | +1 天 | 防 adapter 内部归一化漏写 |
| Trace `storage="artifact"` | `DiagnosticsConfig.storage` 加 `"artifact"` Literal；写到附件存储（路径由 framework 统一） | +1 天 | 解决 token 级 1k 步 trace 在 `metadata` 也偏大的场景 |

### 7bis.2 验收

- 三个新 backend 各跑一个真实 endpoint（`base_url` + `api_key_env` 走 `core.helper.ssrf_proxy`），从画布拖出能跑响应级 + 能跑 token 级（`anthropic` 仅响应级）
- `validate_requirements` 拒绝路径单测：OpenAI top_k=25 → `StructuredValidationError("top_logprobs is capped at 20, runner requested 25")`
- 跨 backend 一致性 fixture 绿（误差 ε < 1e-3 在 post-sampling 归一后）
- `storage="artifact"` 单测：1k 步 token-level trace 写入附件存储，`outputs.text` 干净，`metadata` 含 artifact 引用

---

## 8. 风险登记（Risk Register）

| ID | 风险 | 概率 | 影响 | 缓解 | 状态 |
|---|---|---|---|---|---|
| R1 | graphon 不允许字符串注册新 `node_type`，必须扩展 `BuiltinNodeTypes` | — | — | Phase 0 Q1 实测闭环：`NodeType: TypeAlias = str`，任意字符串合法（`graphon/enums.py:13`） | **closed** |
| R2 | 流式事件 schema 不对 | 低 | 中 | 已通过 review 闭环（6.4 已用实测签名） | **closed** |
| R3 | `DifyNodeFactory` 的 init kwargs 路由要为 ParallelEnsembleNode 改源码 | 高 | 低 | 加一段分支注入 `local_model_registry` + `executor`；工作量小 | open |
| R4 | Token 级并联吞吐太低（< 3 token/s）实用性差 | 中 | 高 | 跟 PN.py 现有性能基线对比；如远低于 PN.py，profile 找瓶颈 | open |
| R5 | 模型 URL 暴露给工作流作者 → SSRF / 内网访问 | — | — | ADR-3 + 6.0 模型注册表已根本解决 | **closed** |
| R6 | 前端 `BlockEnum` / `NodeComponentMap` 后续 Dify upstream 重构破坏注册点 | 中 | 中 | 单测覆盖注册项；rebase upstream 时回归测试 | open |
| R7 | 修改版 llama.cpp 端点 schema 变更（`top_probs` 字段名 / 嵌套结构变了） | 低 | 高 | `LlamaCppClient` 的响应解析独立成函数 + 单测固化 schema | open |
| R8 | DSL 在 chat 模式下含 `End` 节点导致导入失败 | 中 | 低 | 提供两套示例 DSL；CI 添加 mode validation 测试 | open |
| R9 | `model_net.yaml` 未配置 / 路径错 → 启动 crash | 低 | 中 | 文件不存在时 registry 留空 + 控制台日志告警；节点运行时拿不到 alias 友好报错 | open |
| R10 | Phase 0 Q4 注入 `executor` 时拿不到合适的共享线程池 | — | 低 | Phase 0 实测 `DifyNodeFactory` 无现成 ThreadPoolExecutor；方案：在 factory `__init__` 新建 `self._parallel_ensemble_executor = ThreadPoolExecutor(max_workers=dify_config.PARALLEL_ENSEMBLE_MAX_WORKERS)`（默认 8），通过 init kwargs 分支注入 | **mitigated** |

---

## 9. 实施顺序速查表

```
Day 1     Phase 0 spike (Q1/Q3/Q4/Q5)
Day 2-8   Phase 1 (响应级聚合)
            后端节点骨架 + 2 策略 + 单测  (Day 2-3)
            前端节点 + 9 处注册 + i18n      (Day 4-6)
            两种模式联调 + 验收             (Day 7-8)
Day 9-22  Phase 2 (token 级并联 + 流式)
            模型注册表 + LlamaCppClient + 控制台 API  (Day 9-11)
            后端 engine + 聚合器 + 单测              (Day 12-13)
            后端 node._run + node_factory 注入       (Day 14-15)
            前端节点 + 模型下拉 UI + 9 处注册        (Day 16-18)
            workflow 模式联调                        (Day 19)
            chat 模式联调 + 浏览器流式渲染验证        (Day 20-21)
            性能 / 异常路径 / SSRF 测试              (Day 22)
Day 23-26 Phase 3 (测试 + 文档 + 4 份 DSL + i18n)
```

---

## 10. 附录：关键已验证文件路径

| 用途 | 路径 | 用法 |
|---|---|---|
| 节点注册入口 | `api/core/workflow/node_factory.py:104-108` | `register_nodes()` 自动扫描 |
| 节点 init kwargs 路由 | `api/core/workflow/node_factory.py:372-440` | 新节点（ParallelEnsemble）需在此加分支注入依赖 |
| `ssrf_proxy` 注入示例 | `api/core/workflow/node_factory.py:300, 383` | HTTP_REQUEST 节点的现成模板 |
| 已有 in-repo 节点先例（Agent） | `api/core/workflow/nodes/agent/agent_node.py` | 节点基类用法、`_run()` Generator 协议 |
| 已有 in-repo 节点先例（KR） | `api/core/workflow/nodes/knowledge_retrieval/` | 简单节点结构参考 |
| 节点基类 import | `api/core/workflow/nodes/agent/agent_node.py:8-12` | 需要的所有 graphon 类 |
| **流式事件契约（核心）** | `api/core/workflow/nodes/agent/message_transformer.py:129/271/284` | `StreamChunkEvent` + 封口块 + `StreamCompletedEvent` 的实测用法 |
| **node_id vs execution_id 区分** | `api/core/workflow/nodes/agent/agent_node.py:155` | `node_id=self._node_id`, `node_execution_id=self.id` |
| `NodeData.type` 字段先例 | `api/core/workflow/nodes/agent/entities.py:13` | `type: NodeType = BuiltinNodeTypes.AGENT` |
| SSRF 强制规范 | `api/AGENTS.md:140-143` | 出站 HTTP 必须走 `core.helper.ssrf_proxy` |
| 模式 / 节点合法性 | `web/app/components/workflow/update-dsl-modal.helpers.ts:46-56` | `getInvalidNodeTypes`：workflow 排除 Answer，chat 排除 End/Trigger* |
| **前端必填注册点 ① BlockEnum** | `web/app/components/workflow/types.ts` | 节点类型枚举 |
| **前端必填注册点 ② BLOCKS** | `web/app/components/workflow/block-selector/constants.tsx` | 节点选择器列表 |
| **前端必填注册点 ③ 组件映射** | `web/app/components/workflow/nodes/components.ts` | `NodeComponentMap` + `PanelComponentMap` |
| **前端必填注册点 ④ 元数据** | `web/app/components/workflow/nodes/constants.ts` | icon/颜色/默认配置 |
| **前端必填注册点 ⑤ 输出变量节点列表** | `web/app/components/workflow/constants.ts:111` | `SUPPORT_OUTPUT_VARS_NODE` |
| **前端必填注册点 ⑥ 单跑表单 hooks** | `web/app/components/workflow/nodes/_base/components/workflow-panel/last-run/use-last-run.ts:43` | `singleRunFormParamsHooks: Record<BlockEnum, any>` |
| **前端必填注册点 ⑦ 输出变量解析** | `web/app/components/workflow/nodes/_base/components/variable/utils.ts:2092` | `getNodeOutputVars` switch 加 case |
| **前端必填注册点 ⑧ 单跑能力** | `web/app/components/workflow/utils/workflow.ts:16` | `canRunBySingle` |
| **前端必填注册点 ⑨ i18n** | `web/i18n/{en-US,zh-Hans}/workflow.ts` | 显示文案（CLAUDE.md 强制） |
| 算法参考 | `docs/ModelNet/PN.py` | |
| 模型清单 schema 参考 | `docs/ModelNet/model_info.json` | 字段名（`EOS` 大写、`stop_think`、`type`）严格照抄 |
| 项目规范 | `CLAUDE.md`, `api/AGENTS.md`, `web/AGENTS.md` | |

---

## 修订历史

### v2.4 (2026-04-27, EXTENSIBILITY_SPEC v0.2.2 融入主计划)

把原来作为伴生文档的 `EXTENSIBILITY_SPEC.md` v0.2.2（三轴 SPI + capability/requirements 双层校验 + Trace 一等公民）融入 Phase 2 主计划，并新增 Phase 4 (v0.3 backend pack)：

- §1.1 目标 #4 改为"三轴 SPI 易扩展"，新增 #5 "诊断数据是一等公民"；原 #5 模式兼容下移为 #6
- §2 新增 ADR-10 (三轴 SPI + 单节点 `parallel-ensemble`)、ADR-11 (Capability 粗过滤 + Requirements 精校验)、ADR-12 (Trace 一等数据面)、ADR-13 (安全边界 = DSL→服务端唯一硬边界，恶意第三方扩展明确不防)
- §3 Phase 总览：Phase 2 估时 11–14 → 16–19 天 (+5 天 SPI 框架)；新增 Phase 4 (v0.3 backend pack, 4–7 天)；总计 26 → 31 天
- §6.0.3 加 SPI 升级路径说明：P2.1 已落地的 `LocalModelRegistry` + `ModelSpec` 在 P2.1.5 升级为 `ModelRegistry` + `BaseSpec` / `LlamaCppSpec`，按 backend 字符串动态分发（不静态 Annotated Union）；已落地代码不重写
- §6.2 后端文件结构按 EXTENSIBILITY_SPEC §10 重写：新增 `spi/` `registry/` `backends/` `runners/` `aggregators/<scope>/` 子包；`engine.py` / `think_phase.py` 撤销，逻辑挪进 `runners/token_step.py`
- §6.3 entities.py 改为 `runner_name` / `aggregator_name` / `runner_config` / `aggregator_config` / `diagnostics` 五元组；嵌套 `ParallelEnsembleConfig` 子模型挂 `extra="forbid"`，顶层 `ParallelEnsembleNodeData` 保 `extra="allow"`（兼容 BaseNodeData 的 selected/params 等兼容字段）+ `model_validator(mode="before")` 显式拒已知敏感字段
- §6.4 `_run` 伪代码改 SPI 化：runner_registry / aggregator_registry / backend_registry 反查，`runner.run(question, backends, aggregator, config, trace)` 翻译事件，`TraceCollector.finalize()` 按 storage 策略写 outputs / metadata
- §6.6 移植步骤：`TokenVoteEngine` → `TokenStepRunner`；mock 单位由 `LlamaCppClient` 改为 `LlamaCppBackend.step_token`
- 新增 §6.7 SPI 接口冻结（capability / requirements / backend / runner / aggregator / trace / 校验流水线）— 字段级合约引 EXTENSIBILITY_SPEC §3-7 / §9
- 新增 §6.8 v0.2 vs v0.3 范围切分表
- 新增 §7.5 `BACKEND_CAPABILITIES.md` 文档（capability 矩阵 + 三个语义坑 + fixture）
- 新增 §7bis Phase 4：VllmBackend / OpenAICompatBackend / AnthropicBackend / 跨 backend logprob 一致性 / Trace `storage="artifact"`
- §7.4 i18n：每个 runner / aggregator 按 `i18n_key_prefix` 注册，CI lint 检查 key 一致性

### v2.3 (2026-04-19, P1.1 schema 兜底加固 — review round 2)

评审指出 `AggregationInputRef` 放过非法 `variable_selector`，运行期才在 `graphon.variable_loader`（要求 `SELECTORS_LENGTH = 2`）/ `api/core/workflow/system_variables.py:201` 炸 `Invalid preload selector`；并建议嵌套 DTO 收紧以避免前端静默吞字段。P1.1 内兜底修复：

- §5.3 `AggregationInputRef`：
  - `model_config = ConfigDict(extra="forbid")`（嵌套 DTO 严格模式；顶层 `BaseNodeData` 的 permissive 是显式 graph 兼容性需求，两层责任分离）
  - `source_id: str = Field(..., min_length=1)` + `@field_validator` 禁纯空白
  - `variable_selector: list[str] = Field(..., min_length=2)` + `@field_validator` 禁任一段为空/纯空白（graphon `SELECTORS_LENGTH = 2`；不设 `max_length`，保留第 3 段起路径语义）
- **测试兜底前置**：P1.4 仅保留策略 / 节点层完整测试；`tests/unit_tests/core/workflow/nodes/ensemble_aggregator/test_entities.py` 本轮补 14 条 schema mini-tests（valid 2/path-segments/short/empty/blank-seg/empty-seg/blank-source/empty-source/extra-field/defaults/too-few/duplicate/concat-config/unknown-strategy），首跑 14/14 绿
- 决定：schema 校验的最小 suite 必须与 schema 同 PR 落地，不能留到策略层测试周期；"schema 层挡坏配置"是本包主要价值，没测试等于没兜底

### v2.2 (2026-04-18, P1.1 landing 前 schema 钉死)
- §5.3 `AggregationInputRef.source_id` 语义钉死：**user-defined stable alias**（同一节点内唯一），非上游节点 id。理由：节点 id 在 Dify 是 UUID 不人类可读；metadata.contributions 键和字典序 tie-break 都需要语义化稳定 key；一个节点的多个输出字段可被分别聚合时，node_id 不够区分。
- §5.3 `EnsembleAggregatorNodeData`：
  - `inputs` 加 `Field(..., min_length=2)`（并联最小 2 路）
  - `strategy_config: dict = {}` → `dict[str, object] = Field(default_factory=dict)`（强类型 + 避开可变默认值陷阱）
  - 新增 `@model_validator(mode="after")` 校验 `source_id` 唯一性；重复直接 Pydantic `ValidationError`（DSL 导入层拦截）
- `ensemble_aggregator/__init__.py` 引入包级常量 `ENSEMBLE_AGGREGATOR_NODE_TYPE = "ensemble-aggregator"`（仿 `knowledge_index/__init__.py:3`），后续 `entities.py` / `node.py` / 注入分支都引常量而非字面量，减手误
- **P1.1 landing 已完成**：`api/core/workflow/nodes/ensemble_aggregator/{__init__.py, entities.py, exceptions.py}` 三文件落地；4 条验收（正常实例化 / min_length 拒 / 异常可导入 / 重复 source_id 拒）全绿

### v2.1 (2026-04-18, Phase 0 spike 闭环)
- §4 全章节重写：Q1/Q3/Q4/Q5 全部 ✅ 闭环，附实测证据
- §5.3 / §6.3 entities.py `type` 字段移除 `⚠️ Phase 0 Q1 验证` 待办注释
- §6.4 "关键差异点提醒 2" 更正：`self.id` 与 `self._node_id` 值相等（都是 graph 节点 id），约定用后者保持 graphon dispatch 一致性；v1/v2 的"execution id"说法错误
- §8 风险登记：R1 `open → closed`（字符串注册合法）；R10 `open → mitigated`（factory 新建共享 executor）
- 新增配置项：`dify_config.PARALLEL_ENSEMBLE_MAX_WORKERS: int = 8`（Phase 2 P2.9 时加入）

### v2 (前版)
基于架构 review 修订 5 个高/中风险问题：
- **[Issue 2 高]** 流式事件契约纠正：`NodeRunStreamChunkEvent` → `StreamChunkEvent(selector, chunk, is_final)` + `StreamCompletedEvent(node_run_result=...)` + 封口块；selector 用 `self._node_id` 而非 `self.id`。详见 6.4。
- **[Issue 3 高]** SSRF / 治理：模型 URL 不再暴露到节点配置；引入 `LocalModelRegistry`（YAML 配置）+ 节点只引用别名 + HTTP 强制走 `ssrf_proxy`。详见 6.0、ADR-3、ADR-8。
- **[Issue 4 中]** workflow 与 advanced-chat 模式拆分：所有示例 / 验收按模式分别给（workflow 用 End、chat 用 Answer）。详见 6.1、6.7、7.2、ADR-9。
- **[Issue 5.1 中]** `NodeData.type: NodeType = ...` 字段补全（参考 `agent/entities.py:13`）。
- **[Issue 5.2 中]** `request_timeout_ms` 字段位置统一到 `ModelSpec`（注册表层），节点不重复定义。
- **[Issue 5.3 中]** `ModelSpec` 字段名严格对齐 `model_info.json`（`EOS` 大写、不用 `eos_token`）。
- **[Issue 5.4 中]** 前端注册点从"3 处"扩展为实测的 9 必填 + 1 选填，并标注每处用途与是否会触发 TS 编译错误。

工时影响：22 天 → 26 天（+模型注册表 +chat 模式验收 +多注册点）。

### v1
首版计划。
