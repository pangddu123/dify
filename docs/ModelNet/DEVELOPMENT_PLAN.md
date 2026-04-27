# Dify 多模型串并联开发计划

我现在要针对工作流模式进行修改。现在的工作流就是全回复级别的串联和并联，我现在要在工作流中增加token级别的并联操作，请你给出修改计划 还要考虑API的问题，要求并联过程中可以提供实时的logit值。

> **目标读者**：在 `xianghe/temp/dify` 这个 fork 上做二次开发的工程师。
> **基线代码**：`docs/ModelNet/PN.py`（参考算法）、`docs/ModelNet/model_info.json`（模型清单格式）。
> **修改版后端**：本地 llama.cpp 服务，`/completion` 端点已暴露 `completion_probabilities[0].top_probs`。
> **版本**：v2.3（v2 架构 review 修订 + v2.1 Phase 0 spike 闭环 + v2.2 P1.1 landing 前 schema 钉死 + v2.3 P1.1 schema 兜底加固；review 见底部"修订历史"）。

---

## 1. 目标与非目标

### 1.1 目标
1. 在 Dify 工作流画布上支持多模型**串并联组合**。
2. 新增**两类并联节点**：
   - **响应级并联**（Phase 1）：N 个 LLM 各自完整生成，再聚合（多数投票 / 拼接）。
   - **Token 级并联**（Phase 2）：N 个模型每步只前向一个 token，按 top-k logits 加和投票，等价于 PN.py。
3. Token 级节点支持**流式输出**（每选一个 token 立即推送）。
4. 聚合策略**易扩展**：新增策略只需写一个类 + 注册一行。
5. **同时支持 workflow 模式与 advanced-chat 模式**（节点本身模式无关，但终端节点不同）。

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

---

## 3. 阶段总览

| Phase | 内容 | 估时 | 关键交付 |
|---|---|---|---|
| Phase 0 | Spike：摸清 graphon 节点协议剩余未知项 | 0.5 天 | spike 报告 |
| Phase 1 | 响应级聚合节点 `ensemble-aggregator` | 5–7 天 | 节点能在画布上拖出、运行、出聚合结果（workflow + chat 两套验收） |
| Phase 2 | Token 级并联节点 `parallel-ensemble`（流式） + 模型注册表 | 11–14 天 | 节点能拖出、流式输出、对接两种模式的运行环境 |
| Phase 3 | 测试 / 文档 / 示例工作流 / i18n | 3–4 天 | 单测 + 集成测试 + 4 份示例 DSL + README |

总计 **约 26 天**单人工作量（v1 的 22 天 +4 天，主要来自模型注册表 + 多注册点 + chat 模式验收）。

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
> `api/core/model_runtime/`，且和 Phase 2 节点同包更便于注入与测试。下文目录树以已落地结构为准。

```
api/core/workflow/nodes/parallel_ensemble/llama_cpp/
├── __init__.py
├── registry.py           # LocalModelRegistry, ModelSpec     (P2.1 ✅)
├── client.py             # LlamaCppClient（封装 ssrf_proxy） (P2.2 待落地)
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

### 6.2 后端文件结构

```
api/core/workflow/nodes/parallel_ensemble/
├── __init__.py
├── node.py                 # ParallelEnsembleNode(Node) -- 入口、事件协议
├── entities.py             # ParallelEnsembleNodeData
├── exceptions.py
├── engine.py               # TokenVoteEngine: 主循环 (PN.py 移植)
├── think_phase.py          # ThinkPhaseRunner: PN.py.process_think_task
└── aggregators/
    ├── __init__.py
    ├── base.py             # TokenAggregator ABC
    ├── sum_score.py        # PN.py 默认（top-k 概率求和）
    ├── max_score.py        # 取最大单分（不求和）
    └── registry.py
```

> 注：`LlamaCppClient` 和 `ModelSpec` 实际落地在 `api/core/workflow/nodes/parallel_ensemble/llama_cpp/`（见 6.0.3 路径修订），节点通过依赖注入拿到注册表。

### 6.3 关键模块设计

**`entities.py`**
```python
from graphon.entities.base_node_data import BaseNodeData
from graphon.enums import NodeType

class ParallelEnsembleNodeData(BaseNodeData):
    type: NodeType = "parallel-ensemble"           # ✅ Phase 0 Q1 已验证
    question_variable: list[str]                    # selector，例 ["start", "query"]
    model_aliases: list[str]                        # ⚠️ 仅别名，无 URL
    top_k: int = 5
    max_len: int = 1000
    aggregator_name: str = "sum_score"
    aggregator_config: dict = {}
    enable_think: bool = True
```

**`aggregators/base.py`**
```python
from abc import ABC, abstractmethod
from typing import TypedDict

class TokenCandidate(TypedDict):
    token: str
    prob: float

class TokenAggregator(ABC):
    name: str

    @abstractmethod
    def pick(
        self,
        per_model: dict[str, list[TokenCandidate]],
        weights: dict[str, float],
    ) -> tuple[str, float]:
        """返回 (selected_token, score)"""
```

**`aggregators/sum_score.py`**（等价 PN.py.calculate_scores，确定性版本）
```python
@register("sum_score")
class SumScoreAggregator(TokenAggregator):
    def pick(self, per_model, weights):
        scores: dict[str, float] = {}
        for model_id, candidates in per_model.items():
            w = weights.get(model_id, 1.0)
            for cand in candidates:
                scores[cand["token"]] = scores.get(cand["token"], 0.0) + cand["prob"] * w
        if not scores:
            return "<end>", 1.0
        best_score = max(scores.values())
        best_tokens = sorted([t for t, s in scores.items() if s == best_score])
        return best_tokens[0], best_score   # 字典序最小（确定性，避免 random.choice 影响测试）
```

**`engine.py`**
```python
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor

class TokenVoteEngine:
    def __init__(
        self,
        specs: list[ModelSpec],
        clients: dict[str, LlamaCppClient],
        aggregator: TokenAggregator,
        top_k: int,
        max_len: int,
        executor: ThreadPoolExecutor,
    ): ...

    def run_think_phase(self, prompts: dict[str, str]) -> dict[str, str]:
        """对 type=think 的模型，先跑一段 chain-of-thought，append 到 prompt 上"""
        # 并发调 client.completion(prompt, stop=[stop_think], max_tokens=8196)

    def stream(self, prompts: dict[str, str]) -> Generator[str, None, dict]:
        """主循环：每轮 yield 一个 token；返回最终诊断信息"""
        for step in range(self.max_len):
            futures = {
                mid: self._executor.submit(self._one_step, mid, prompts[mid])
                for mid in self._clients
            }
            per_model = {mid: f.result() for mid, f in futures.items()}

            token, score = self._aggregator.pick(per_model, self._weights)
            if token == "<end>":
                return {"steps": step, "stopped_by": "eos"}

            for mid in prompts:
                prompts[mid] += token
            yield token

        return {"steps": self.max_len, "stopped_by": "max_len"}
```

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
        local_model_registry: LocalModelRegistry,   # ⚠️ 由 DifyNodeFactory 注入（Phase 0 Q4 确认注入路径）
        executor: ThreadPoolExecutor,                # 共用线程池
    ):
        super().__init__(id=id, config=config,
                         graph_init_params=graph_init_params,
                         graph_runtime_state=graph_runtime_state)
        self._registry = local_model_registry
        self._executor = executor

    @classmethod
    def version(cls) -> str:
        return "1"

    def _run(self) -> Generator[NodeEventBase, None, None]:
        # 1. 取 question
        seg = self.graph_runtime_state.variable_pool.get(self.node_data.question_variable)
        question = str(seg.value)

        # 2. 解析 alias → ModelSpec → LlamaCppClient
        specs = [self._registry.get(a) for a in self.node_data.model_aliases]
        clients = {s.id: LlamaCppClient(s) for s in specs}

        # 3. apply_template
        prompts = {
            s.id: clients[s.id].apply_template([
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": question},
            ])
            for s in specs
        }

        # 4. think 前置（如开启）
        if self.node_data.enable_think:
            think_runner = ThinkPhaseRunner(specs, clients, self._executor)
            think_appendix = think_runner.run(prompts)
            for mid, suffix in think_appendix.items():
                prompts[mid] += suffix

        # 5. 主循环 + 流式 emit —— ⚠️ 用 self._node_id，不是 self.id
        engine = TokenVoteEngine(
            specs=specs,
            clients=clients,
            aggregator=get_token_aggregator(self.node_data.aggregator_name),
            top_k=self.node_data.top_k,
            max_len=self.node_data.max_len,
            executor=self._executor,
        )
        accumulated = ""
        t0 = time.time()
        stats: dict = {}
        gen = engine.stream(prompts)
        try:
            while True:
                token = next(gen)
                accumulated += token
                yield StreamChunkEvent(
                    selector=[self._node_id, "text"],   # ⚠️ graph node id
                    chunk=token,
                    is_final=False,
                )
        except StopIteration as stop:
            stats = stop.value or {}

        # 6. 封口块（参考 agent message_transformer.py:271-275）
        yield StreamChunkEvent(
            selector=[self._node_id, "text"],
            chunk="",
            is_final=True,
        )

        # 7. 完成
        yield StreamCompletedEvent(
            node_run_result=NodeRunResult(            # ⚠️ 关键字 node_run_result=
                status=WorkflowNodeExecutionStatus.SUCCEEDED,
                outputs={
                    "text": accumulated,
                    "tokens_count": stats.get("steps", 0),
                    "elapsed_ms": int((time.time() - t0) * 1000),
                },
                inputs={"question": question, "models": list(clients.keys())},
            )
        )
```

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
- `TokenVoteEngine.stream()` 写完后，写一个 `pytest` mock `LlamaCppClient`，喂假的 top_probs 序列，断言：
  - 终止条件正确（`<end>` / `max_len`）
  - 聚合策略正确（确定性输出）
  - prompt 同步正确（每轮所有模型 prompt 都加同一个 token）
- 此时**完全不依赖 graphon**。

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

---

## 7. Phase 3 — 测试 / 文档 / 示例工作流

### 7.1 测试
| 层级 | 文件 | 内容 |
|---|---|---|
| 单测 | `api/tests/unit_tests/core/workflow/nodes/ensemble_aggregator/` | 策略 + 节点 |
| 单测 | `api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/` | 聚合器 + engine + think + node._run（mock client + mock registry） |
| 单测 | `api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/llama_cpp/` | registry 加载 / Pydantic validation / `extra="forbid"` 拒绝未知 yaml 字段 / ssrf_proxy mock（路径同 6.0.3 修订） |
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
