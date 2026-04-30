# Dify 多模型协作 — 开发计划 v3

- **日期**：2026-04-29
- **当前版本**：v3.0.2（review 修订：SPI 切分 + backend SPI 扩展 + fail-fast weight）
- **状态**：草案（pre-implementation）。本文不替代 `DEVELOPMENT_PLAN.md` v2.4，
  而是在 v2.4 已落地（Phase 0–P2.12）的基础上**修订 token 模式架构**并**升级
  response 模式现有节点**。
- **基线**：v2.4 全部成果（`ensemble-aggregator` v1 已上线、`parallel-ensemble`
  P2.12 前端质量门已落地）。
- **关系图**：

  ```
  v2.4 (current)                v3 (this plan)
  ─────────────                 ────────────────
  ensemble-aggregator    ───►   ensemble-aggregator (upgraded)
   (response, basic)             (response, weights + dynamic + ext SPI)

  parallel-ensemble      ───►   parallel-ensemble (re-purposed)
   (token + response,             (token only, aggregator-as-executor,
    self-contained                 reads TokenModelSource refs from canvas)
    model_aliases)
                                  + token-model-source (NEW — config holder)
  ```

---

## 1. 为什么要 v3

### 1.1 一个根本性误判（在 v2.4 token 路径里隐含）

v2.4 的 `parallel-ensemble` 把 N 个模型塞在节点配置里、节点内部 `ThreadPoolExecutor`
并发调它们——这是**自包含执行器**。这条路对 token 级 PN.py 是工作的，但有两个研
究/产品层面的硬伤：

| 痛点 | v2.4 现状 | 用户期望 |
|---|---|---|
| 多模型来源混合（本地 + 闭源 API） | model_aliases 全走 `LocalModelRegistry`（yaml）| Claude/GPT/Ollama 走标配 LLM 节点，本地 llama.cpp 走自定义 |
| 模型在画布上不可见 | 全部藏在 node config 里 | 画布要看到"哪些模型在协作" |
| 上游"任意输出 text 的节点"做响应级融合 | 只能是 model_aliases 里的 backend | LLM/HTTP/Code/Agent/Ollama 任何节点 |

### 1.2 graphon 流式语义（v3 必须遵守的硬约束）

> ⚠️ **新发现的事实，v2.4 隐式假设错了**：

`StreamChunkEvent` 在 graphon 协议里只走给 UI（`_dispatch` 转 `NodeRunStreamChunkEvent`），**不写入 variable pool**，**不被下游节点看到**。下游节点只在
上游 `StreamCompletedEvent` 之后从 `NodeRunResult.outputs` 读最终值。

**推论**：
- 不可能让"多个上游 TokenStreamingLLM 节点"分别跑、再让下游聚合器逐 token 融合
  ——因为下游聚合器**根本拿不到中间 token**
- token 级真协作只能由一个**自己当执行器的聚合器节点**完成
- 该节点可以把"用哪些模型 + 用哪些 prompt"外置成 canvas 上的 **TokenModelSource**
  节点（输出结构化 spec），但**调度循环必须留在聚合器内部**

> 这一条已经更新到 `ref_dify_workflow_arch.md`，作为后续节点设计的硬约束。

### 1.3 v3 与 v2.4 的关系

- v2.4 全部已落地的代码（P1.1–P2.12）**完全保留**，不重写
- v3 只做两件事：
  1. **升级** `ensemble_aggregator`（response 模式）→ 加权重（含动态 selector）/
     fail-fast + 显式 fallback_weight / `ResponseAggregator` SPI 切换至
     `SourceAggregationContext`（**不**含 top_k_override，那是 token 模式独有）
  2. **重定位** `parallel_ensemble`（token 模式）→ 上游改成 `token-model-source`
     节点输出 `ModelInvocationSpec`，删除 `question_variable`（prompt 由 source
     渲染），扩展 `ModelBackend.step_token` 至 `TokenStepParams`，per-source
     sampling 真正生效
- v2.4 EP-3"单节点"原则在 v3 里**部分让步**：聚合器仍是聚合器，但 token 模式额外
  引入 `token-model-source` 这个**配置载体节点**（不是新聚合器，不冲突 EP-3 的
  本意）

---

## 2. 决策记录（v3）

| ID | 决策 | 上下文 |
|---|---|---|
| ADR-v3-1 | response 模式：升级现有 `ensemble_aggregator`，**不**重写 | EP-5；现有 `AggregationInputRef` 已经是配置行驱动 |
| ADR-v3-2 | response 模式上游 = 任何输出 text 的节点（LLM/HTTP/Code/Agent/Ollama via Dify model_runtime/...） | 用户 4-Z 决策；`segment.text` 已支持多类型自动渲染 |
| ADR-v3-3 | token 模式：聚合器即执行器，禁止"上游 token 流"幻想 | §1.2 graphon 流式语义 |
| ADR-v3-4 | token 模式上游 = `token-model-source` 节点输出的 `ModelInvocationSpec` | 节点边界清晰 + variable pool 可序列化 |
| ADR-v3-5 | per-input 权重双语义：per-run 启动时解析（图变量）+ per-token 策略内部计算（不是图变量） | 用户 Q1；graphon 不支持节点运行中变量更新 |
| ADR-v3-6 | per-source top-K 覆盖**仅在 token 模式**生效，位置：`TokenSourceRef.top_k_override` 与 `token-model-source.sampling_params.top_k`（后者优先级低，被前者覆盖）。**response 模式不存在 top-K candidates 概念**，`AggregationInputRef` 行只保留 `weight`/`alias`/`extra` | 用户 Q2 + 修订评论 4：response 上游已经是 final text，谈 top-K 无落点 |
| ADR-v3-7 | 配置项强类型 + `extra: dict[str, Any]` 扩展位；不裸奔 `dict[str, dict]` | 用户反馈；DSL typo 难查 |
| ADR-v3-8 | 收敛策略 SPI **+ context 分层**：抽出 `SourceAggregationContext`（仅含 `sources`/`weights`/`source_meta`/`strategy_config`）作为**所有上游为 source 的聚合**通用契约；token 模式扩展为 `BackendAggregationContext(SourceAggregationContext)`，再带 `backends`/`capabilities`/`runner_name`/`runner_config`/`trace`/`elapsed_ms_so_far`/`step_index`。`ResponseAggregator` 消费 `SourceAggregationContext`（`ensemble_aggregator` 上游是 HTTP/Code/Agent 任意 text 节点，**不存在 backend/capability**），`TokenAggregator` 消费 `BackendAggregationContext`。两节点共用同一棵 SPI 树，但策略不再被迫感知不属于自己语义的字段。 | 单一 SPI 但语义分层；修订评论 1：原 `AggregationContext.backends/capabilities/runner_name` 是 backend/runner 语义，不该强加给 response 策略 |
| ADR-v3-9 | `parallel_ensemble.runners.response_level` **删除** | 多模型并发由图引擎+多上游做；保留它会与 `ensemble_aggregator` 形成职责冲突 |
| ADR-v3-10 | `token-model-source` 节点 `_run()` 不调模型；输出 `ModelInvocationSpec`（model_alias + prompt + sampling_params + extra）到 variable pool | aggregator-as-executor 模型契约 |
| ADR-v3-11 | v2.4 的 P2.10/P2.11/P2.12 测试**翻译式迁移**，不丢弃；模型并发部分删除，聚合部分保留 | 保护既有投入 |
| ADR-v3-12 | 是否合并 `ensemble-aggregator` 进 `parallel-ensemble` 的决策**延后**到 v3 全部 ship 后 | 现阶段重要的是先把 token 模式跑起来 |
| ADR-v3-13 | v2.4 已存在的 DSL **不**做向上兼容；旧 DSL 加载由 pydantic `extra="forbid"` / 缺字段自然 `ValidationError`；不提供 migration 工具 | 用户决策（2026-04-29）；研究 fork 阶段，无生产 DSL 需要保护——把保留兼容的工程预算让给"更激进的清理 + 更干净的新 schema" |
| ADR-v3-14 | **`ModelBackend.step_token` SPI 扩展**：签名从 `step_token(prompt: str, top_k: int)` 改为 `step_token(prompt: str, params: TokenStepParams)`，其中 `TokenStepParams` 强类型承载 `top_k` / `temperature` / `top_p` / `max_tokens` / `stop` / `seed` 等 sampling 参数。`token_step.py` 调用前从 `TokenModelSource.sampling_params` + `TokenSourceRef.top_k_override` 合并构造 `params`。`backends/llama_cpp.py` 适配新签名（per-call 应用 sampling params 到 llama.cpp 的 sampling chain，不依赖 backend 实例化时的全局值） | 修订评论 3：原"算法不变"低估了改造量——若不扩 SPI，`TokenModelSource.sampling_params.{temperature,top_p,stop,seed}` 等字段会被 backend 静默丢弃，等于研究配置失效 |
| ADR-v3-15 | **动态 weight 解析失败 = fail fast**：`AggregationInputRef.weight` 是 `VariableSelector` 时，解析失败默认抛 `WeightResolutionError`（带 `input_id` + `selector` + 原因），节点直接 `FAILED`。**不**做 silent fallback 到 1.0。如果用户确实要容错，需显式声明 `AggregationInputRef.fallback_weight: float \| None = None`，且只有它非空时解析失败才回退到该值并在 trace 写 warning | 修订评论 5：silent fallback 会悄悄改变研究实验条件（论文里 weight=0.7/0.3 可能跑成 1.0/1.0）；fail fast 是研究 fork 的正确默认 |
| ADR-v3-16 | **`parallel_ensemble.config.question_variable` 删除**：v2.4 的 `question_variable` 用于节点内拼 prompt，v3 把这件事完全交给 `token-model-source.prompt_template`。每个 source 自带渲染好的 prompt，`parallel-ensemble._run` 不再渲染、不再读 question 变量 | 修订评论 2：保留 `question_variable` 与 `TokenModelSource.prompt` 责任重叠；后者已含完整 prompt，前者会让"prompt 在哪里渲染"难定位 |

---

## 3. 架构图（v3）

### 3.1 节点拓扑

```
┌─────────────────────────────────────────────────────────────────┐
│                         Response 模式                           │
│                                                                 │
│  ┌─ Dify LLM ──┐                                                │
│  │ (任意上游) │ ─►   selector: [llm-1, text]                    │
│  └────────────┘            ─┐                                   │
│                              │                                  │
│  ┌─ HTTP Req  ─┐             │       ┌─ ensemble-aggregator ─┐  │
│  │ (任意上游) │ ─► [http-1,  │ ───►  │  (升级版)              │  │
│  └────────────┘   response]  │       │  inputs: 配置行驱动    │  │
│                              │       │  strategy: 任选        │  │
│  ┌─ Code      ─┐             │       │  per-input weight ✓    │  │
│  │ (任意上游) │ ─► [code,    │       │  weight: var/num ✓     │  │
│  └────────────┘   result]   ─┘       │  fallback_weight (opt) │  │
│                                       │  (无 top_k：见 §2 v3-6)│  │
│                                       └────────────────────────┘ │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│                         Token 模式                              │
│                                                                 │
│  ┌─ token-model-source ─┐                                       │
│  │ alias: qwen3-4b      │ ─► [src-1, spec]                      │
│  │ prompt: "{{#q#}}"    │           │                           │
│  │ sampling: {top_k:10} │           │                           │
│  └──────────────────────┘           │                           │
│                                     ├──► ┌─ parallel-ensemble ─┐│
│  ┌─ token-model-source ─┐           │    │  (重定位)            ││
│  │ alias: gpt-oss-20b   │ ─► [src-2,│    │  token_sources: 配行 ││
│  │ prompt: ...          │      spec]│    │  aggregator: token-* ││
│  │ sampling: {top_k:5}  │           │    │  CONTROLS the gen    ││
│  └──────────────────────┘           ─┘   │  loop via spec       ││
│                                          └──────────────────────┘│
└──────────────────────────────────────────────────────────────────┘
```

### 3.2 数据流（token 模式关键点）

```
P3.B 阶段每个 token-model-source 在 _run() 里做的事：

  upstream var resolve (prompt 模板填变量)
        │
        ▼
  ModelInvocationSpec {
      model_alias: "qwen3-4b",
      prompt: "<已渲染的最终 prompt>",
      sampling_params: {top_k: 10, temperature: 0.7, ...},
      extra: {...}
  }
        │
        ▼
  yield StreamCompletedEvent(outputs={"spec": <spec>})
        │
        ▼
  variable_pool[node_id, "spec"] = <spec>     # 可被下游 selector 引用


parallel-ensemble._run() 在收到 N 个 spec 后：

  解析 N 个 spec → 实例化 N 个 backend (按 spec.model_alias 找 LocalModelRegistry)
        │
        ▼
  解析 per-input weight (per-run 模式)
        │
        ▼
  跑 token-step 协作循环（runner + aggregator + executor，沿用 v2.4 SPI）
        │
        ▼
  yield 流式 chunk + 最终 StreamCompletedEvent
```

---

## 4. 后端改造清单

### 4.1 `ensemble_aggregator/`（response 升级）

| 文件 | 处置 | 改动要点 |
|---|---|---|
| `entities.py` | **扩展** | `AggregationInputRef` 加：`weight: float \| VariableSelector = 1.0`、`fallback_weight: float \| None = None`（仅当 weight 是 selector 且解析失败时使用，None 表示 fail fast，见 ADR-v3-15）、`extra: dict[str, Any] = {}`。**不**加 `top_k_override`（ADR-v3-6） |
| `node.py` | **改 `_collect_inputs`** | 解析 weight 变量引用；构造 `SourceAggregationContext`（来自 `parallel_ensemble.spi.aggregator`，**仅** sources/weights/source_meta/strategy_config，无 backends/capabilities/runner_name）。weight 解析失败默认抛 `WeightResolutionError`；`fallback_weight` 非空时回退到该值并在 trace 写 warning |
| `strategies/base.py` | **替换为 SPI 收敛** | 删除原 `AggregationStrategy`；改为 `re-export ResponseAggregator from parallel_ensemble.spi.aggregator`，本地策略全部继承之；**`ResponseAggregator.aggregate` 第二参数为 `SourceAggregationContext`**（不是 `BackendAggregationContext`） |
| `strategies/registry.py` | **保留** | 注册装饰器协议不变 |
| `strategies/majority_vote.py` | **改造** | 接 `SourceAggregationContext.weights`，实现"权重加权多数"作为新分支（保持原行为为 weights 全 1 时的特化） |
| `strategies/concat.py` | **改造** | 不需要权重，但支持按 weight 排序输出顺序（可选） |
| `strategies/weighted_majority_vote.py` | **新增** | 完全权重驱动；用作 SPI 扩展示例 |
| `exceptions.py` | **保留 + 加** | 新增 `WeightResolutionError(input_id, selector, reason)`；ADR-v3-15 默认抛它，仅在 `fallback_weight` 非空时改为 warning + 回退 |

### 4.2 `parallel_ensemble/`（token 重定位）

| 文件 | 处置 | 改动要点 |
|---|---|---|
| `entities.py` | **重写 `ParallelEnsembleConfig`** | 删除 `model_aliases`；**删除 `question_variable`**（ADR-v3-16，prompt 由 token-model-source 渲染）；新增 `token_sources: list[TokenSourceRef]`（每条含 `source_id` + `spec_selector` + `weight` + `top_k_override: int \| None`（**保留**，token 模式独有，ADR-v3-6） + `fallback_weight: float \| None` + `extra`）；保留 `runner_name`/`aggregator_name`/`runner_config`/`aggregator_config`/`diagnostics` |
| `node.py` | **改 `_instantiate_backends` + `_run`** | 不再从 `cfg.model_aliases` 读；改为从 variable pool 取 N 个 `ModelInvocationSpec`，按 `spec.model_alias` 调 `LocalModelRegistry` 实例化 backend；prompt **不在节点里渲染**——直接用 `spec["prompt"]`；为每个 source 合并 `sampling_params = spec.sampling_params` ⊕ `TokenSourceRef.top_k_override`（后者覆盖前者的 top_k），打包成 `TokenStepParams` 在协作循环里逐 step 传给 backend |
| `node.py` `_validate_at_startup` | **保留+改写** | §9 五步流程保留；step 3-4 capability/requirements 现在按 **spec.sampling_params + TokenSourceRef.top_k_override 合并后的 effective params** 校验；step 5 cross-field 校验"≥2 sources"、`spec_selector` 在 variable_pool schema 能解析到 `ModelInvocationSpec` 形状（Rv3-3） |
| `runners/response_level.py` | **删除** | ADR-v3-9 |
| `runners/token_step.py` | **保留 + 适配新 backend SPI** | 协作算法不变（PN.py 的逐 token 投票循环），但调用 backend 时从 `step_token(prompt, top_k)` 改为 `step_token(prompt, params: TokenStepParams)`（ADR-v3-14）。`params` 由节点 `_run` 在循环外构造好后传入 runner，runner 透传给每个 backend 即可 |
| `runners/think_phase.py` | **保留 + 适配新 backend SPI** | 同上：think 阶段的 backend 调用也走新签名 |
| `spi/aggregator.py` | **重写 + 切分 context** | 新增 `SourceAggregationContext`（sources/weights/source_meta/strategy_config）；`BackendAggregationContext(SourceAggregationContext)` 再带 backends/capabilities/runner_name/runner_config/trace/elapsed_ms_so_far/step_index；`ResponseAggregator` 消费前者，`TokenAggregator` 消费后者（ADR-v3-8） |
| `spi/runner.py` | **保留 + 适配** | runner 接口收到的 backend 调用参数从 `top_k:int` 换成 `params: TokenStepParams`（透传） |
| `spi/backend.py` | **改签名** | `ModelBackend.step_token(prompt, top_k)` → `step_token(prompt, params: TokenStepParams)`；新增 `TokenStepParams` TypedDict/BaseModel（详见 §4.4） |
| `aggregators/response/*` | **删除** | response 路径转给 `ensemble_aggregator`；这里只留 token aggregator |
| `aggregators/token/*` | **保留 + 适配 context** | sum_score / max_score 算法不变；`aggregate(signals, context, config)` 的 `context` 类型从旧 `AggregationContext` 改名为 `BackendAggregationContext`（字段超集，行为兼容） |
| `backends/llama_cpp.py` | **适配新 SPI** | `step_token(prompt, params)` 内部把 `params.{top_k,temperature,top_p,stop,seed,max_tokens}` 应用到 llama.cpp 的 sampling chain（per-call，非全局），保证 per-source sampling 真的生效（ADR-v3-14） |
| `llama_cpp/registry.py` | **保留** | yaml 注册表仍是模型来源 |

### 4.3 `token_model_source/`（新节点，独立目录）

```
api/core/workflow/nodes/token_model_source/
├── __init__.py            # NODE_TYPE = "token-model-source"
├── node.py                # 50 行：渲染 prompt + 构造 spec + 输出
├── entities.py            # TokenModelSourceNodeData + ModelInvocationSpec TypedDict
└── exceptions.py          # PromptRenderError 等
```

`ModelInvocationSpec` shape：

```python
class ModelInvocationSpec(TypedDict):
    model_alias: str                  # → LocalModelRegistry 的 key
    prompt: str                       # 已渲染（变量替换完）
    sampling_params: dict[str, Any]   # top_k / temperature / max_tokens / ...
    extra: dict[str, Any]             # 第三方扩展位
```

`TokenModelSourceNodeData` shape：

```python
class TokenModelSourceNodeData(BaseNodeData):
    type: NodeType = "token-model-source"
    model_alias: str                                   # 必填
    prompt_template: str                               # 支持 {{#xxx#}}
    sampling_params: SamplingParams = ...              # 强类型；下方
    extra: dict[str, Any] = {}                         # 扩展位

class SamplingParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_k: int = 10
    temperature: float = 0.7
    max_tokens: int = 1024
    top_p: float | None = None
    seed: int | None = None
    stop: list[str] = []
```

`token_model_source/node.py` 核心逻辑（伪代码）：

```python
def _run(self):
    rendered = self._render_prompt(self.node_data.prompt_template)  # 解析 {{#var#}}
    spec: ModelInvocationSpec = {
        "model_alias": self.node_data.model_alias,
        "prompt": rendered,
        "sampling_params": self.node_data.sampling_params.model_dump(),
        "extra": self.node_data.extra,
    }
    yield StreamCompletedEvent(
        node_run_result=NodeRunResult(
            status=SUCCEEDED,
            outputs={"spec": spec, "model_alias": spec["model_alias"]},
        )
    )
```

### 4.4 `parallel_ensemble/spi/backend.py` SPI 扩展（ADR-v3-14）

v2.4 backend SPI 是 `step_token(prompt: str, top_k: int) -> list[TokenCandidate]`，
丢弃了 `temperature`/`top_p`/`stop`/`seed` 等关键 sampling 参数。v3 修订评论 3
要求扩展为强类型参数对象：

```python
# parallel_ensemble/spi/backend.py（v3 修订）
class TokenStepParams(BaseModel):
    """Per-call sampling parameters for ModelBackend.step_token."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    top_k: int                           # 必填，>= 1
    temperature: float = 1.0
    top_p: float | None = None
    max_tokens: int | None = None        # 仅 think_phase 关心
    stop: list[str] = []
    seed: int | None = None
    extra: dict[str, Any] = {}           # 第三方 backend 私有字段（vLLM repetition_penalty 等）


class ModelBackend(Protocol):
    @abstractmethod
    def step_token(
        self,
        prompt: str,
        params: TokenStepParams,
    ) -> list[TokenCandidate]: ...

    @abstractmethod
    def think(
        self,
        prompt: str,
        params: TokenStepParams,
    ) -> ThinkResult: ...
```

`token_step.py` 从 spec + TokenSourceRef 合并构造 params：

```python
def _build_params(self, spec: ModelInvocationSpec, ref: TokenSourceRef) -> TokenStepParams:
    sp = dict(spec["sampling_params"])
    if ref.top_k_override is not None:                    # ref 优先
        sp["top_k"] = ref.top_k_override
    return TokenStepParams.model_validate(sp)             # 强类型校验在这里发生
```

**为什么不沿用 v2.4 签名**：v2.4 把 sampling 当成 backend **实例化时的全局值**，
PN.py 风格的研究里每个 source 想用不同的 temperature/stop 就只能造多份 backend
实例。v3 规定 sampling 是 **per-call** 的——同一个 backend 实例可以在循环里被
多次调用、每次带不同 params。这对"同模型不同温度做 self-consistency"这类研究
配置是必须的，而且不增加 backend 实现复杂度（llama.cpp 的 sampling chain 本身
就是 per-call 创建的）。

---

## 5. SPI 扩展点（关键架构承诺）

### 5.1 分层后的 Aggregation context（ADR-v3-8）

```python
# parallel_ensemble/spi/aggregator.py（v3 重写）

class SourceAggregationContext(BaseModel):
    """response 模式 + token 模式共用的 source 视图。
    上游可以是任何输出 text/spec 的节点，因此这里**不**带 backend 语义。
    """
    sources: list[SourceInfo]                # source_id + alias + (opt) origin_node_id
    weights: dict[str, float]                # source_id → effective weight（解析后）
    source_meta: dict[str, dict[str, Any]]   # source_id → 任意上游附带的 metadata
    strategy_config: dict[str, Any]          # 策略私有 config 的反射镜像（ui_schema 同源）


class BackendAggregationContext(SourceAggregationContext):
    """token 模式独有：聚合发生时 backend 还活着，可以问能力 / runner / trace。"""
    backends: list[BackendInfo]
    capabilities: dict[str, frozenset[Capability]]
    runner_name: str
    runner_config: dict
    trace: TraceCollector
    elapsed_ms_so_far: int
    step_index: int | None = None


class ResponseAggregator(Aggregator[ConfigT, list[ResponseSignal], ResponseAggregationResult]):
    scope = "response"

    @abstractmethod
    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,           # ← 仅 source 视图
        config: ConfigT,
    ) -> ResponseAggregationResult: ...


class TokenAggregator(Aggregator[ConfigT, list[TokenSignal], TokenAggregationResult]):
    scope = "token"

    @abstractmethod
    def aggregate(
        self,
        signals: list[TokenSignal],
        context: BackendAggregationContext,          # ← 完整 backend 视图
        config: ConfigT,
    ) -> TokenAggregationResult: ...
```

**为什么这样切分**（修订评论 1 的核心）：

- `ensemble_aggregator` 的上游是 HTTP / Code / Agent / 任意 LLM 节点，**不**经过
  `LocalModelRegistry`，**没有** backend 实例、**没有** capability 矩阵、**没有**
  runner。强迫 response 策略接收 `backends`/`capabilities`/`runner_name` 等于
  让策略撒谎或忽略字段——前者污染 SPI 语义，后者让"加新 response 策略"的扩展
  者疑惑这些字段该填什么
- token 模式天然有 backend / capability / trace，并且 `TokenAggregator` 的算法
  确实会用到（PN.py 的 `score_history` 来自 trace），所以 `BackendAggregationContext`
  以**继承**关系挂在 `SourceAggregationContext` 之下：token aggregator 想看 source
  视图也能看（同一份 weights/sources）

`ensemble_aggregator/strategies/weighted_majority_vote.py`（response 策略示例）：

```python
@register("weighted_majority_vote")
class WeightedMajorityVote(ResponseAggregator[_Config]):
    config_class = _Config

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: SourceAggregationContext,            # ← 仅 source
        config: _Config,
    ) -> ResponseAggregationResult:
        votes: Counter[str] = Counter()
        for s in signals:
            w = context.weights.get(s["source_id"], 1.0)
            votes[s["text"]] += w
        winner = votes.most_common(1)[0][0]
        return {
            "text": winner,
            "metadata": {"votes": dict(votes), "weights": context.weights},
        }
```

### 5.2 per-token 动态权重（策略内部，非图变量）

```python
# 策略可以自己持续维护 weight，例如基于 history 调整
class AdaptiveWeightedToken(TokenAggregator[_Config]):
    def aggregate(
        self,
        signals: list[TokenSignal],
        context: BackendAggregationContext,           # ← 完整 backend 视图
        config: _Config,
    ) -> TokenAggregationResult:
        # 每个 step 重算 weight：history 越长，越偏向准确率高的源
        recalc = self._dynamic_weight(context.weights, context.trace.history)
        return self._weighted_top_k(signals, recalc)
```

> **per-token 动态权重不能从图上其他变量来**，因为 graphon variable pool 在节点运行
> 中不更新。能用的输入只有：(a) per-run 解析过的初始权重；(b) trace 里到目前为止
> 的协作历史；(c) signals 自身（top-K 候选 + logprobs）；(d) `step_index` /
> `elapsed_ms_so_far`（如想做时间衰减）。所有这些都在 `BackendAggregationContext` 里。

### 5.3 第三方扩展面（v3 总账）

| 想做的事 | 改的文件 | 工作量 |
|---|---|---|
| 加新 response 策略（如 RRF） | `ensemble_aggregator/strategies/rrf.py` + `@register`（继承 `ResponseAggregator`，签名只看到 `SourceAggregationContext`） | 1 文件 |
| 加新 token 策略 | `parallel_ensemble/aggregators/token/<name>.py` + `@register`（继承 `TokenAggregator`，签名看到 `BackendAggregationContext`） | 1 文件 |
| 加新模型后端（vLLM logprobs / OpenAI logprobs） | `parallel_ensemble/backends/<name>.py` + `BackendSpec` 子类 + `@register`，实现 `step_token(prompt, params: TokenStepParams)` 即可 | 2 文件 |
| 加新 token runner（speculative decoding 等） | `parallel_ensemble/runners/<name>.py` + `@register`，循环里透传 `TokenStepParams` 给 backend | 1 文件 |
| 加新 sampling 维度（如 vLLM 独有的 repetition_penalty） | `TokenStepParams.extra` 自由加 key，backend 实现自己读 | **0 文件改框架** |
| 加新动态权重维度（per-source temperature 调节系数） | 在 `inputs[i].extra` / `token_sources[i].extra` 自由加 key，策略自己读 | **0 文件改框架** |
| 改聚合节点本身行为 | fork `node.py` | （非公开扩展点，所有 Dify 节点都这样） |

---

## 6. 前端改造清单

### 6.1 `ensemble-aggregator` 前端升级

| 文件 | 改动 |
|---|---|
| `types.ts` | `AggregationInputRef` 加 `weight`（`number \| VariableSelector`）、`fallback_weight: number \| null`、`extra: Record<string, unknown>` 三个字段；**不**加 `top_k_override`（ADR-v3-6）；strategy 名字字面量从 2 个扩到 3+ 个 |
| `components/input-list.tsx` | 行内加 weight 输入框（接受数字 OR 变量引用） + fallback_weight 数字输入框（默认空 = fail fast，填值才回退；行内 tooltip 说明 ADR-v3-15） |
| `components/strategy-selector.tsx` | 新增 weighted_majority_vote 选项；ui_schema 反射策略私有 config |
| `use-config.ts` | 新增 `handleWeightChange` / `handleFallbackWeightChange` |
| `panel.tsx` | 不变（已是 Field 组合形态，子组件自洽） |

### 6.2 `token-model-source` 前端（新增）

```
web/app/components/workflow/nodes/token-model-source/
├── default.ts             # 新节点 default config
├── node.tsx               # canvas 节点视觉
├── panel.tsx              # 配置面板：model_alias 选择 + prompt textarea + sampling_params 表单
├── types.ts               # 镜像后端 TokenModelSourceNodeData
├── use-config.ts          # 表单状态
└── components/
    ├── model-alias-select.tsx     # 复用 parallel-ensemble 已有组件
    └── sampling-params-form.tsx   # ui_schema 反射
```

**9 处硬编码注册** 全做：
1. `web/app/components/workflow/types.ts` 的 `BlockEnum`
2. `web/app/components/workflow/block-selector/constants.tsx` 的 `BLOCKS`
3. `web/app/components/workflow/nodes/components.ts` 的 `NodeComponentMap` + `PanelComponentMap`
4–9. `default.ts` / `i18n` / `SUPPORT_OUTPUT_VARS_NODE` / `singleRunFormParamsHooks` / `getNodeOutputVars` / 等等（参考 P2.11 落地清单）

### 6.3 `parallel-ensemble` 前端重构

| 文件 | 改动 |
|---|---|
| `types.ts` | 删 `model_aliases`；**删 `question_variable`**（ADR-v3-16）；加 `token_sources: TokenSourceRef[]`（每条 `source_id` + `spec_selector` + `weight` + `top_k_override: number \| null`（**保留**，token 模式独有） + `fallback_weight: number \| null`） |
| `components/model-selector.tsx` | **删除** —— 不再节点内选模型 |
| `components/import-model-info-button.tsx` | **删除** —— 模型 info 现在通过 token-model-source 节点维护 |
| `components/question-variable-select.tsx`（若 v2.4 单独存在） | **删除** —— ADR-v3-16，prompt 由 token-model-source 节点渲染，节点级问题变量已无意义 |
| `components/token-source-list.tsx` | **新增** —— 类似 ensemble_aggregator 的 InputList，但 selector 限定为 `outputs.spec` 形态；行内含 weight / top_k_override / fallback_weight 三个输入框 |
| `components/runner-selector.tsx` | 保留（runner 选择不变） |
| `components/aggregator-selector.tsx` | 保留 |
| `components/diagnostics-config.tsx` | 保留 |
| `components/dynamic-config-form.tsx` | 保留（ui_schema 反射本来就是为 SPI 通用而做） |

### 6.4 i18n 增量

`web/i18n/{en-US,zh-Hans}/workflow.ts` 增加：
- `nodes.tokenModelSource.*`（新节点）
- `nodes.ensembleAggregator.*` 加：`weight` / `fallbackWeight` / `weightedMajorityVote.*`（**不**加 `topKOverride`，ADR-v3-6）
- `nodes.parallelEnsemble.*` 改：`tokenSources` 替代 `modelAliases`；新增 `tokenSources.topKOverride` / `tokenSources.fallbackWeight`；删除 `questionVariable`

---

## 7. 阶段拆分（v3）

| Phase | 内容 | 依赖 | 工时 |
|---|---|---|---|
| **P3.0** | 本文件 review + memory 更新 + v2.4 文档钩子（"v3 supersedes §6"标注） | — | 0.5d |
| **P3.A.1** | `ensemble_aggregator` 后端：扩展 `AggregationInputRef`（`weight`/`fallback_weight`/`extra`，**无** `top_k_override`）、SPI 切换至 `ResponseAggregator`（消费 `SourceAggregationContext`）、新增 `weighted_majority_vote` 策略、动态 weight 解析（fail-fast 默认 + 显式 `fallback_weight` 容错） | P3.0 | 1.5d |
| **P3.A.2** | `ensemble_aggregator` 前端：行内 weight + fallback_weight 输入框（**无** top_k_override）、strategy ui_schema 反射、i18n | P3.A.1 | 1.5d |
| **P3.A.3** | `ensemble_aggregator` 测试翻译 + 新增（dynamic weight 成功/失败/fallback 三分支 / weighted_majority_vote） | P3.A.1+P3.A.2 | 1d |
| **🟢 ship A** | response 模式完整可用；外部贡献者可基于这个写自定义策略 | A.1–A.3 | — |
| **P3.B.0** | **backend SPI 扩展（ADR-v3-14）+ context 切分（ADR-v3-8）**：`spi/aggregator.py` 拆 `SourceAggregationContext` / `BackendAggregationContext`；`spi/backend.py` 新增 `TokenStepParams`，`step_token` 改签名为 `(prompt, params)`；`backends/llama_cpp.py` 适配 per-call sampling chain；`runners/{token_step,think_phase}.py` 透传 params；`aggregators/token/*` 改 context 名 | A ship | 1.5d |
| **P3.B.1** | `token-model-source` 后端：node.py + entities.py（含强类型 `SamplingParams`）+ 注册 + 单测（prompt 模板渲染、spec 输出） | B.0 | 1d |
| **P3.B.2** | `token-model-source` 前端：5 文件 + 9 处注册 + i18n | B.1 | 2d |
| **P3.B.3** | `parallel_ensemble` 重定位：删 `model_aliases`、**删 `question_variable`**（ADR-v3-16）、加 `token_sources`（含 `top_k_override`/`fallback_weight`）、`_run` 改读 spec + 合并 `TokenStepParams`、删 response_level runner、§9 校验改写 | B.0 | 2d |
| **P3.B.4** | `parallel_ensemble` 前端：删 model_selector / import 按钮 / question-variable-select、加 token-source-list（含 top_k_override + fallback_weight 输入）、i18n | B.3 | 1.5d |
| **P3.B.5** | `parallel_ensemble` 测试翻译 + 新增（含 `TokenStepParams` 合并优先级、weight fail-fast、source spec 解析失败、per-source sampling 真的传到 backend） | B.3 + B.4 | 1.5d |
| **🟢 ship B** | token 模式完整可用；PN.py 算法首次端到端在 Dify 画布上跑通，per-source sampling 真正生效 | B.0–B.5 | — |
| **P3.C.1** | 示例 DSL（4 份）+ EXTENSION_GUIDE 更新（含 `TokenStepParams.extra` 第三方扩展示例 + `SourceAggregationContext` vs `BackendAggregationContext` 选用指南） | A + B | 1d |
| **P3.C.2** | DEVELOPMENT_PLAN v2.4 主文档钩子；本文件升级为 v3.1（含已落地章节） | C.1 | 0.5d |
| **合计** | | | **~15d**（v3.0.1 是 13.5d，本次 +1.5d 来自 P3.B.0 backend SPI 扩展 + context 切分） |

> Phase A（response）和 Phase B（token）顺序执行：A 先 ship 是因为升级面更小、风险更低
> （仅扩展 schema + 收敛 SPI）；B 是更大的重构（新增节点 + 重定位现有节点），A 落地
> 稳定后再启动。两阶段都不投入精力做 v2.4 DSL 向上兼容（ADR-v3-13）。

---

## 8. 测试与文档迁移

> ⚠️ **v2.4 DSL 不向上兼容**（ADR-v3-13）。旧 `parallel-ensemble + model_aliases`
> DSL 在 v3 加载时由 pydantic `extra="forbid"` / 缺字段自然 `ValidationError` 失败，
> 这是**预期行为**，不提供 migration 工具，不写 `MigrationRequiredError`，不维护
> "v2.4 → v3" 转换脚本。研究 fork 阶段无生产 DSL 需要保护，省下的工程预算转移给
> Phase A/B 的激进清理（详见 ADR-v3-13 上下文）。
>
> 副作用提示：旧 `ensemble-aggregator` DSL 因为 v3 仅追加可选字段（pydantic 默认值
> 兜底），加载会**自然成功**——但这是 schema 演进的副作用，不是承诺。如果 Phase A
> 后续需要破坏性改动 `ensemble-aggregator` 字段，依然按 ADR-v3-13 处理（让旧 DSL
> 报错，不打补丁）。

### 8.1 v2.4 测试套件迁移

| 测试模块 | 处置 |
|---|---|
| `parallel_ensemble/__tests__/`（事件序列、§9、storage、DSL 防护）| **翻译式重写**：删除 model_aliases 相关用例；用 `token_sources` fixture 替换；DSL 防护层（`_FORBIDDEN_TOP_LEVEL_KEYS`）只保留 SSRF 相关键（`model_url`/`api_key`/...），不加 `model_aliases` 防护——`extra="forbid"` 会更早拦下 |
| `parallel_ensemble/runners/response_level` 相关测试 | **整体删除**（ADR-v3-9） |
| `ensemble_aggregator/` 已有测试 | **保留 + 扩**：增加 weight / dynamic weight / weighted_majority_vote 用例 |
| 前端 `parallel-ensemble/__tests__/panel.spec.tsx` | **翻译式重写**：基于 token-source-list 而非 model-selector |
| 前端 `parallel-ensemble/components/__tests__/model-selector.spec.tsx` | **删除**（组件被删） |

### 8.2 文档钩子

- `DEVELOPMENT_PLAN.md` v2.4 §6 顶部加状态横幅："superseded by `DEVELOPMENT_PLAN_v3.md` Phase B; this section retained as historical context"
- `EXTENSIBILITY_SPEC.md` §3（Capability 矩阵）加注："response_level runner 已在 v3 删除，response 模式统一走 ensemble-aggregator + ResponseAggregator SPI"
- `BACKEND_CAPABILITIES.md` 加一节："token-model-source 节点贡献的 spec → backend 资格映射"
- 不写 `docs/ModelNet/MIGRATION_v2_to_v3.md`——按 ADR-v3-13 这是非目标

---

## 9. 风险登记

| ID | 风险 | 严重 | 缓解 |
|---|---|---|---|
| Rv3-1 | per-run 动态权重的 selector 解析失败 | 中 | **默认 fail fast**（ADR-v3-15）：抛 `WeightResolutionError(input_id, selector, reason)`，节点 FAILED，错误消息直接定位到 input 行。**理由**：研究 fork 阶段 silent fallback 到 1.0 会悄悄改变实验条件（论文里 weight=0.7/0.3 跑成 1.0/1.0 是 reviewer 噩梦）。容错路径：用户在 `AggregationInputRef.fallback_weight` / `TokenSourceRef.fallback_weight` 显式声明回退值，仅当该字段非空时才回退到 fallback_weight 并在 trace 写 warning |
| Rv3-2 | TokenModelSource 的 prompt 模板和 LLM 节点的 prompt 模板代码重复 | 低 | 抽 `_render_prompt` 到 `core/workflow/utils/prompt_render.py`（如果不存在），两节点共享 |
| Rv3-3 | 用户在画布上漏接一个 token-model-source 但 parallel-ensemble 已配置 N 条 token_sources → 启动报错 | 中 | §9 校验加新 step：检查每个 token_source 的 spec_selector 在 variable_pool schema 里能解析到 ModelInvocationSpec 形状 |
| Rv3-4 | per-source `top_k_override` > backend 实际 supported top-K → 调用失败（**仅 token 模式**，ADR-v3-6） | 中 | §9 capability 校验时构造 `effective_params = TokenStepParams(spec.sampling_params + ref.top_k_override)`，再以 effective `top_k` 走 capability matching；response 模式无此风险（已无 top_k_override） |
| Rv3-5 | token-model-source 输出 spec 后被其他节点（非 parallel-ensemble）误用 | 低 | 不强制阻止；`ModelInvocationSpec` 是 TypedDict，谁都能消费；EXTENSION_GUIDE 文档化合法消费者 |
| Rv3-6 | 收敛 SPI 后 `ensemble_aggregator/strategies/base.py` 与 `parallel_ensemble.spi.aggregator` 双向 import | 低 | `ensemble_aggregator` 单向 import 自 `parallel_ensemble.spi`（依赖方向已确定），不构成 cycle |
| Rv3-7 | 老 v2.4 DSL 用户加载 v3 报错时不知所措 | 低 | ADR-v3-13 决定**不缓解**——pydantic `ValidationError` 自带字段名 + 错误原因，足以指引用户重建工作流；研究 fork 阶段没有需要保护的"老用户"语义 |
| Rv3-8 | `ModelBackend.step_token` 签名扩展（ADR-v3-14）会让 v2.4 写过的 backend / mock backend 全部不能直接复用 | 中 | 这是有意为之——v2.4 mock 把 sampling 当全局值的写法本来就限制了 PN.py 风格的研究表达力。P3.B.0 一次性升级所有内置 backend + 所有测试 mock；EXTENSION_GUIDE 给第三方 backend 写迁移示例（旧 `step_token(prompt, top_k)` → 新 `step_token(prompt, params)` 一处改动） |
| Rv3-9 | `ResponseAggregator` 收到 `SourceAggregationContext` 后丢失 `trace`，导致 response 策略不能写诊断日志 | 低 | `SourceAggregationContext` 不带 trace 是有意设计——response 模式上游不在节点内执行，没有 step-level trace 概念。策略需要写 metadata 走 `ResponseAggregationResult.metadata`（节点会把它合并进 outputs），而不是 trace 流 |

---

## 10. 待定决策（不阻塞 P3.0–P3.B）

| 项 | 决策时机 |
|---|---|
| 是否将 `ensemble-aggregator` 合并进 `parallel-ensemble` 成单节点 | Phase B ship 后，看用户使用反馈 |
| 是否提供 `Anthropic logprobs` / `OpenAI logprobs` backend（v0.3 backend pack）| v3 全部 ship 后 |
| 是否做 sentence-bert 语义投票策略 | 第三方贡献者按需 |
| 是否给 `token-model-source` 加 prompt 模板预览 | P3.B.2 前端落地时再决定 |
| 是否暴露"协作深度（cooperation_token_limit）"在节点级 vs runner config 里 | P3.B.3 落 runner_config 时决定（倾向放 runner_config 更解耦） |

---

## 11. 修订历史

### v3.0.2 (2026-04-29, SPI 切分 + backend 扩展 + fail-fast weight)

针对 v3.0.1 的 5 条 review 评论修订：

1. **context 切分**（评论 1）：原 ADR-v3-8 让 `ensemble_aggregator` 直接吃带
   `backends/capabilities/runner_name/trace` 的 `AggregationContext`，但 response
   模式上游是任意 text 节点（HTTP/Code/Agent），没有 backend/capability 概念。
   - 拆出 `SourceAggregationContext`（sources/weights/source_meta/strategy_config）
     作为 response 模式契约；`BackendAggregationContext` 继承之、再加 backend/
     runner/trace 给 token 模式用
   - `ResponseAggregator.aggregate(signals, context: SourceAggregationContext, config)`
   - `TokenAggregator.aggregate(signals, context: BackendAggregationContext, config)`
   - 改动：ADR-v3-8 重写、§4.1 `_collect_inputs` 描述更新、§4.2 `spi/aggregator.py`
     重写、§5.1 全节重写、§5.2 例子签名加 type、新增 Rv3-9
2. **question_variable vs prompt 职责重叠**（评论 2）：v2.4 `parallel_ensemble`
   保留 `question_variable` 与 `TokenModelSource.prompt` 重复。
   - 新增 ADR-v3-16：删除 `parallel_ensemble.config.question_variable`，prompt
     完全由 `token-model-source.prompt_template` 渲染
   - 改动：§4.2 entities/`_run` 描述更新、§6.3 前端删 question-variable-select、
     §6.4 i18n 删 questionVariable、P3.B.3/B.4 phase 加上删除项
3. **token_step 算法不变低估改造量**（评论 3）：v2.4 `step_token(prompt, top_k)`
   不能消费 `TokenModelSource.sampling_params` 的 temperature/top_p/stop/seed。
   - 新增 ADR-v3-14：`ModelBackend.step_token` 改签名 `(prompt, params: TokenStepParams)`，
     `params` 强类型承载所有 sampling 参数；`backends/llama_cpp.py` 适配 per-call
     sampling chain
   - 新增 §4.4 backend SPI 扩展专节（`TokenStepParams` 定义 + `_build_params` 合并
     逻辑）
   - 新增 P3.B.0 phase（1.5d）专做 SPI 扩展；总工时 13.5d → 15d
   - 新增 Rv3-8（老 backend 不能直接复用）
4. **top_k_override 在 response 模式语义弱**（评论 4）：response 上游已是 final
   text，没有 top-K candidates。
   - ADR-v3-6 改写：`top_k_override` 仅 token 模式生效（`TokenSourceRef.top_k_override`
     + `TokenModelSource.sampling_params.top_k`）
   - `AggregationInputRef` 删除 `top_k_override`，改动 §3.1 架构图、§4.1 entities、
     §6.1 前端、§6.4 i18n、Rv3-4
5. **动态 weight 解析失败 silent fallback 危险**（评论 5）：研究实验里 weight
   悄悄变 1.0 是 reviewer 噩梦。
   - 新增 ADR-v3-15：默认 fail fast 抛 `WeightResolutionError`；用户需显式声明
     `fallback_weight: float | None` 才回退
   - 改动 §4.1 entities/`_collect_inputs`、§6.1 前端 input-list、Rv3-1 立场反转

### v3.0.1 (2026-04-29, drop DSL backward-compat)
- 新增 ADR-v3-13：v2.4 已存在的 DSL **不**做向上兼容
- §8 重命名为"测试与文档迁移"（原"迁移与兼容性"），删除原 §8.1 DSL migration matrix
- 风险登记 Rv3-6（老 DSL 升级路径断裂）改写为"不缓解"立场，原 Rv3-7 升为 Rv3-6
- §7 Phase 说明剔除"A 的升级不会破坏既有 DSL"理由
- 不再投入 migration 工具开发；不写 `MIGRATION_v2_to_v3.md`

### v3 (2026-04-29, 本文件初稿)
- 基于用户对 v2.4 token 模式的根本性纠正：streaming 不流到下游 → token 协作必须聚合器即执行器
- 升级 ensemble_aggregator（response 模式）+ 重定位 parallel_ensemble（token 模式）
- 新增 token-model-source 节点
- 收敛策略 SPI 至单一 `ResponseAggregator`/`TokenAggregator` 契约（沿用 v2.4 已有 SPI）
- 总工时：~13.5d（v3.0.2 修订为 ~15d）
