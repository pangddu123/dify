# Graphon Spike 报告（Phase 0）

- **日期**：2026-04-18
- **执行者**：Claude Code（/home/xianghe/temp/dify fork）
- **graphon 版本**：0.1.2（`api/pyproject.toml:47` 声明 `graphon~=0.1.2`）
- **源码路径**：`/home/xianghe/temp/dify/api/.venv/lib/python3.12/site-packages/graphon/`
- **总耗时**：约 1.5 h（含 uv 安装 + 依赖 sync）
- **总体结论**：**四项探针全绿**，DEVELOPMENT_PLAN.md v2 的架构假设在 graphon 0.1.2 上成立，Phase 1 可直接开工。

---

## 结果速览

| 项 | 结论 | 影响 |
|---|---|---|
| Q1 NodeType 字面量注册 | ✅ 绿灯 | `NodeType` 就是 `str` 别名，任意字符串合法；ADR-1 无改动；R1 关闭 |
| Q3 Node 自动注册 | ✅ 绿灯 | `__init_subclass__` 机制，只需声明 `node_type` + `version()` + 泛型参数 |
| Q4 DifyNodeFactory 注入 | ✅ 绿灯 | mapping key 支持新字符串；聚合节点走默认空分支；token 节点需加一支 |
| Q5 前后端 schema 对齐 | ✅ 绿灯 | 平凡的字符串序列化，`BaseNodeData.type: NodeType = str`，`extra="allow"` |

**衍生发现**：memory / DEVELOPMENT_PLAN.md v2 有一处误述需要更正——`self.id` 并非 execution id，实际上 `self.id == self._node_id`（都是 graph 节点 id）；execution id 存在 `self._node_execution_id` / `self.execution_id`。详见 §2.5。

---

## 1. Q1 — NodeType 字面量注册机制

### 1.1 实测

`graphon/enums.py:13`：

```python
NodeType: TypeAlias = str
```

`graphon/enums.py:16-48` 的 `BuiltinNodeTypes` 是**裸类**（不是 `Enum`），字段全是 `ClassVar[NodeType]` 字符串常量。类 docstring 明确写：

> `node_type` values are plain strings throughout the graph runtime. This namespace only exposes the built-in values shipped by `graphon`; **downstream packages can use additional strings without extending this class.**

### 1.2 结论

- **任意字符串都可以作为 `node_type`**，不需要扩展 `BuiltinNodeTypes`、不需要 fork graphon。
- v2 计划里 `EnsembleAggregatorNodeData` 写法：
  ```python
  class EnsembleAggregatorNodeData(BaseNodeData):
      type: NodeType = "ensemble-aggregator"   # 合法
  ```
  类型标注相当于 `type: str = "ensemble-aggregator"`，Pydantic 直接接受。
- 同理 `ParallelEnsembleNodeData.type = "parallel-ensemble"` 合法。

### 1.3 对 DEVELOPMENT_PLAN.md 的影响

| 位置 | 原文 | 是否要改 |
|---|---|---|
| ADR-1 | 走 fork 路线 | 不改（结论一致） |
| §5.3 entities.py 示例 | `type: NodeType = "ensemble-aggregator"   # ⚠️ Phase 0 Q1 验证后再确认` | **可删除 `⚠️ Phase 0 Q1 验证`注释** |
| §6.3 entities.py 示例 | 同上 | 同上 |
| R1 "字符串注册新 node_type 不行" | open | **closed** |

---

## 2. Q3 — Node 基类自动注册

### 2.1 实测

`graphon/nodes/base/node.py:97-203` 的 `Node.__init_subclass__` 是唯一注册点：

```python
class Node(Generic[NodeDataT]):
    node_type: ClassVar[NodeType]
    execution_type: NodeExecutionType = NodeExecutionType.EXECUTABLE
    _node_data_type: ClassVar[type[BaseNodeData]] = BaseNodeData
    _registry: ClassVar[dict[NodeType, dict[str, type[Node]]]] = {}

    def __init_subclass__(cls, **kwargs):
        # 1. 从 Node[T] 泛型参数抽 T 并验证是 BaseNodeData 子类
        node_data_type = cls._extract_node_data_type_from_generic()
        cls._node_data_type = node_data_type

        # 2. 按 module 路径分层注册
        node_type = cls.node_type
        version = cls.version()
        bucket = Node._registry.setdefault(node_type, {})
        if cls.__module__.startswith("graphon.nodes."):
            bucket[version] = cls            # graphon 内部节点：覆盖写
        else:
            bucket.setdefault(version, cls)  # 外部节点：不覆盖 graphon 内置
        # 3. 维护 "latest" 指针（优先数字版本 max）
        bucket["latest"] = bucket[latest_key]
        Node._registry_version += 1
```

### 2.2 关键约束

| 约束 | 违反后果 |
|---|---|
| 必须 `class MyNode(Node[MyNodeData])`（泛型参数不可省） | `TypeError: must inherit from Node[T]` |
| `MyNodeData` 必须是 `BaseNodeData` 子类 | `TypeError: must parameterize Node with a BaseNodeData subtype` |
| 必须声明类属性 `node_type: ClassVar[NodeType] = "..."` | `AttributeError` |
| 必须声明 `@classmethod version()` 返回字符串 | `AttributeError` |

### 2.3 外部节点注册策略

- 我们的节点模块路径是 `core.workflow.nodes.ensemble_aggregator.node` / `core.workflow.nodes.parallel_ensemble.node`，**不以 `graphon.nodes.` 开头** → 走 `bucket.setdefault` 分支。
- 由于 `ensemble-aggregator` / `parallel-ensemble` 这两个 node_type **graphon 从未注册过**，`bucket` 为空，`setdefault` 等于直接写入 → 无冲突、无覆盖问题。

### 2.4 新节点最小骨架

```python
# api/core/workflow/nodes/ensemble_aggregator/node.py
from typing import ClassVar
from graphon.nodes.base.node import Node
from graphon.enums import NodeType
from .entities import EnsembleAggregatorNodeData

class EnsembleAggregatorNode(Node[EnsembleAggregatorNodeData]):
    node_type: ClassVar[NodeType] = "ensemble-aggregator"

    @classmethod
    def version(cls) -> str:
        return "1"

    def _run(self):   # Generator[NodeEventBase, None, None]
        ...
```

仅此 + `_run()` 就会在 `DifyNodeFactory.register_nodes()` 调用 `_import_node_package("core.workflow.nodes")` 时自动注册到 `Node._registry["ensemble-aggregator"]["1"]`。

### 2.5 ⚠️ 误述更正：`self.id` vs `self._node_id`

DEVELOPMENT_PLAN.md v2 §6.4 "关键差异点提醒 2" 与 memory `ref_dify_workflow_arch.md` 说：

> selector 是 `[self._node_id, "text"]`，**不是 `[self.id, "text"]`**（`self.id` 是 execution id）

**这是错的**。实测 `graphon/nodes/base/node.py:256-276`：

```python
def __init__(self, id: str, config: NodeConfigDict, ...):
    self.id = id                          # 参数 id
    ...
    node_id = config["id"]
    self._node_id = node_id               # 就是 config["id"]
    self._node_execution_id: str = ""     # 初始为空
```

配合 `node_factory.py:368-444`：

```python
node_id = typed_node_config["id"]         # DSL 里的节点 id
...
return node_class(
    id=node_id,                           # 传给 __init__ 的 id 参数
    config=typed_node_config,
    ...
)
```

→ `self.id == self._node_id`（都是 DSL graph 节点 id）；execution id 是 `self._node_execution_id`（懒加载的 uuid4），通过 property `self.execution_id` 暴露。

graphon 源码内部：
- 第 608/618 行用 `self.id`（在 `NodeRunFailedEvent` / `NodeRunSucceededEvent` 构造）
- 第 637/653/662/679/688 行用 `self._node_id`（在 `StreamChunkEvent` 等 dispatch 里）

两者值相同，但**约定俗成**用 `self._node_id`。保留 v2 计划里 selector 写 `self._node_id` 的建议（一致性而非正确性理由）。

---

## 3. Q4 — DifyNodeFactory 注入

### 3.1 实测

`api/core/workflow/node_factory.py:286-342` 的 `DifyNodeFactory.__init__` 已有的依赖字段（都可直接在 mapping 里引用）：

| 字段 | 值 | 对我们有用吗 |
|---|---|---|
| `self._http_request_http_client` | `ssrf_proxy` | ❌ 不直接用（见下） |
| `self._code_executor` / `_code_limits` | Code 节点专用 | ❌ |
| `self._agent_*` | Agent 节点专用 | ❌ |
| `self._dify_context` | DifyRunContext | ❌ |
| `self._jinja2_template_renderer` | 模板渲染器 | ❌ |

**没有** `ThreadPoolExecutor` / `thread_pool` / `executor` 字段。R10 确认走备选方案。

`node_factory.py:372-444` 的 mapping：

```python
node_init_kwargs_factories: Mapping[NodeType, Callable[[], dict[str, object]]] = {
    BuiltinNodeTypes.CODE: lambda: {...},
    BuiltinNodeTypes.HTTP_REQUEST: lambda: {...},
    ...
}
node_init_kwargs = node_init_kwargs_factories.get(node_type, lambda: {})()
return node_class(id=node_id, config=..., **node_init_kwargs)
```

- key 类型是 `NodeType`（= `str`）→ 新字符串 key 合法
- 未列出的 node_type 走 `lambda: {}` → **聚合节点不改 factory 即可**

### 3.2 聚合节点（Phase 1）

`EnsembleAggregatorNode` 无外部依赖（从 `graph_runtime_state.variable_pool` 取上游，调聚合策略 pure function），**不在 factory 加任何分支**，走默认空 kwargs。这与 TASKS.md P1.3 的断言一致。

### 3.3 Token 节点（Phase 2）

`ParallelEnsembleNode` 需要：
- `local_model_registry`：全局单例（`LocalModelRegistry.instance()`），可以在 lambda 里直接调
- `executor`：**R10 备选方案**——在 `DifyNodeFactory.__init__` 新增共享 `ThreadPoolExecutor`
- HTTP 客户端：**不通过 factory 注入**——`LlamaCppClient` 内部静态导入 `from core.helper import ssrf_proxy` 直接使用（更干净，不污染 factory signature）

注入分支样板（Phase 2 P2.9 落地时写进 `node_factory.py`）：

```python
# node_factory.py __init__ 底部新增：
from concurrent.futures import ThreadPoolExecutor
from core.workflow.nodes.parallel_ensemble.llama_cpp.registry import LocalModelRegistry

self._local_model_registry = LocalModelRegistry.instance()
self._parallel_ensemble_executor = ThreadPoolExecutor(
    max_workers=dify_config.PARALLEL_ENSEMBLE_MAX_WORKERS,   # 新 config，默认 8
    thread_name_prefix="parallel-ensemble",
)

# node_init_kwargs_factories mapping 加一支：
"parallel-ensemble": lambda: {
    "local_model_registry": self._local_model_registry,
    "executor": self._parallel_ensemble_executor,
},
```

共享 executor 而非每节点新建的好处：多个 ParallelEnsemble 节点并存时共用线程池，避免线程数爆炸。`max_workers` 应 ≥ 单节点最大 `len(model_aliases)`；8 是粗默认，Phase 2 P2.15 压测后再校准。

### 3.4 对 DEVELOPMENT_PLAN.md 的影响

| 位置 | 原文 | 改动 |
|---|---|---|
| §6.4 `ParallelEnsembleNode.__init__` 签名 | `executor: ThreadPoolExecutor` | 保留，来源明确为"factory 共享池" |
| R10 "拿不到共享线程池" | open | **调整为：** 确认走"factory 新建共享池"方案，状态改为 mitigated |
| 新增配置项 | — | `api/configs/dify_config.py` 加 `PARALLEL_ENSEMBLE_MAX_WORKERS: int = 8` |

---

## 4. Q5 — 前后端 NodeData schema 对齐

### 4.1 后端

`graphon/entities/base_node_data.py:130-148`：

```python
class BaseNodeData(ABC, BaseModel):
    model_config = ConfigDict(extra="allow")   # ⚠️ 默认 permissive

    type: NodeType               # = str
    title: str = ""
    desc: str | None = None
    version: str = "1"
    error_strategy: ErrorStrategy | None = None
    default_value: list[DefaultValue] | None = None
    retry_config: RetryConfig = Field(default_factory=RetryConfig)
```

### 4.2 前端

`web/app/components/workflow/types.ts:28-51`：`BlockEnum` 是 TS 字符串枚举，值如 `Agent = 'agent'`。前端 `default.ts` 写 `type: BlockEnum.Agent`，序列化到 JSON 就是 `"type": "agent"`。

→ 后端 `BaseNodeData.type: str` 直接吞下，无需 validator、无需转换。

### 4.3 我们的节点加 `extra="forbid"` 的可行性

v2 计划 §6.0.4 在 `ModelSpec` 上用 `extra="forbid"` 拦截 DSL 里塞 `model_url` 的 SSRF 攻击（R5 防护）。`ModelSpec` 是注册表内部 DTO，不是 `BaseNodeData` 子类 → **和 `BaseNodeData.extra="allow"` 不冲突**。

对于 `ParallelEnsembleNodeData` 本身要不要 `extra="forbid"`：**可以**，因为它继承自 `BaseNodeData` 后 `model_config` 会被子类覆盖。但要注意 DEVELOPMENT_PLAN.md §2 引用的 base_node_data 注释说"persisted templates/workflows also carry undeclared compatibility keys such as `selected`, `params`, `paramSchemas`, `datasource_label`"——`ParallelEnsembleNodeData` 若开 `forbid`，需要显式声明这些兼容字段或保留 `allow`。

**建议**：节点本身保持 `extra="allow"`（继承默认），仅 `ModelSpec` 用 `extra="forbid"`。

### 4.4 对 DEVELOPMENT_PLAN.md 的影响

无改动。验收项 P2.10 `test_extra_forbid_dsl` 原意是测 `ModelSpec` 层面，而非 NodeData 层面 —— 与上述结论一致。

---

## 5. 关键文件索引（graphon 0.1.2 内）

| 用途 | 文件 | 行号 |
|---|---|---|
| NodeType 类型定义 | `graphon/enums.py` | 13 |
| BuiltinNodeTypes 常量 | `graphon/enums.py` | 16–48 |
| BUILT_IN_NODE_TYPES 元组 | `graphon/enums.py` | 51–77 |
| NodeExecutionType | `graphon/enums.py` | 80–90 |
| WorkflowNodeExecutionStatus | `graphon/enums.py` | 232–243 |
| WorkflowNodeExecutionMetadataKey | `graphon/enums.py` | 199–229 |
| BaseNodeData | `graphon/entities/base_node_data.py` | 130–188 |
| Node 基类 | `graphon/nodes/base/node.py` | 82 |
| `__init_subclass__` 注册 | `graphon/nodes/base/node.py` | 97–203 |
| `_registry` 字典 | `graphon/nodes/base/node.py` | 249 |
| `get_node_type_classes_mapping` | `graphon/nodes/base/node.py` | 530–542 |
| `Node.__init__`（id / \_node\_id / \_node\_execution\_id 初始化） | `graphon/nodes/base/node.py` | 256–281 |
| `_dispatch` StreamChunkEvent 转 GraphEvent | `graphon/nodes/base/node.py` | 633–642 |
| `_dispatch` StreamCompletedEvent 转 GraphEvent | `graphon/nodes/base/node.py` | 644+ |
| StreamChunkEvent 定义 | `graphon/node_events/node.py` | 38–49 |
| StreamCompletedEvent 定义 | `graphon/node_events/node.py` | 52–53 |
| NodeEventBase / NodeRunResult | `graphon/node_events/base.py` | 10–42 |

---

## 6. 对 DEVELOPMENT_PLAN.md v2 的回溯动作清单

### 必做（P0.3）

- [ ] §5.3 `EnsembleAggregatorNodeData` 示例移除 `⚠️ Phase 0 Q1 验证后再确认` 注释
- [ ] §6.3 `ParallelEnsembleNodeData` 同上
- [ ] §6.4 §6.7 "关键差异点提醒 2" 改写：`self.id` 与 `self._node_id` 值相等；**约定上**用 `self._node_id` 保持 graphon 源码一致性
- [ ] §8 风险登记：R1 状态 `open → closed`（证据：Q1）
- [ ] §8 风险登记：R10 状态 `open → mitigated`（方案：factory 共享 `ThreadPoolExecutor`，见 §3.3）
- [ ] §4.1 表：Q1/Q3/Q4/Q5 状态 `待验证 → ✅ 已闭环`
- [ ] §6.0 YAML 加一个字段 `PARALLEL_ENSEMBLE_MAX_WORKERS`（归到 dify_config 而不是 yaml）；或在 §6.4 说明 executor 创建逻辑

### 建议做（可留到 Phase 2）

- [ ] §6.4 `__init__` 签名里的 `LocalModelRegistry` import 路径 调整为 TASKS.md 2026-04-18 决定的 `core.workflow.nodes.parallel_ensemble.llama_cpp.registry`

### memory 更正

- [ ] `ref_dify_workflow_arch.md` 第 17 项删除"（self.id 是 execution id）"的说法
- [ ] `project_dify_modelnet.md` "v2 关键修订"第 1 条相同位置同步更正

---

## 7. 未探的未知项（留给后续阶段自然发现）

以下 graphon 内部机制 Phase 0 没深挖，Phase 1/2 对应开发时顺便验证即可：

| 项 | 何时遇到 | 对策 |
|---|---|---|
| `VariablePool.get(selector)` 返回 `Segment` 对象的 API（`.value` / `.text` 等） | Phase 1 P1.3 写 `_run()` 取上游时 | 看 agent 节点既有用法 |
| `NodeRunResult.metadata` 的 `WorkflowNodeExecutionMetadataKey` 强类型约束 | Phase 2 P2.8 | outputs 用普通 dict，metadata 必须枚举 key |
| GraphEngine 线程池配置（`max_workers`、并发上限） | Phase 2 P2.15 压测 | 读 `graph_engine/` 源码或测试观察 |
| `post_init()` hook 的使用场景 | Phase 2 节点需要惰性构造客户端时 | 看 base/node.py:304 |
| `ensure_execution_id()` 的调用时机 | 如果要在 `_run()` 开头拿 uuid | 看 base/node.py:329 |

---

## 8. 结论

- **Phase 0 全部四项探针通过**。没有发现需要 fork graphon 或推翻 v2 架构的情况。
- **可立即进入 Phase 1**：聚合节点骨架按 §2.4 样板起手。
- **回写 DEVELOPMENT_PLAN.md 的 6 项必做**应在进 Phase 1 之前完成（保持文档与实测一致）。
- **memory 更正 2 条**应一同处理。
