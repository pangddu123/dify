# ModelNet 扩展性规范（Extensibility Spec v0.2.2）

> **状态**：设计稿（pre-implementation）。本文不替代 `DEVELOPMENT_PLAN.md`，
> 而是把"如何让第三方在不 fork 我们 fork 的前提下扩展协作模式 / 模型后端 /
> 聚合策略"这件事单独钉死，作为 Phase 2 进入 P2.5+ engine 工作前的架构基线。
>
> **目标读者**：(1) 在 `xianghe/temp/dify` 之上写**自定义协作策略**的研究者；
> (2) 给我们的多模型节点接**新模型后端**（vLLM / OpenAI-compat / Anthropic /
> 自家私有 API）的工程师；(3) 维护本 fork 的我自己。
>
> **基线版本**：DEVELOPMENT_PLAN.md v2.3、TASKS.md（P2.1 已落地）。本规范一旦
> 接受，会引发 P2.2–P2.10 的任务重排，详见 §11。

---

## 1. 为什么要这份规范

`DEVELOPMENT_PLAN.md` 假设的世界是「llama.cpp + token 级投票」一种协作。
现实里：

- **后端**不止 llama.cpp：还有 vLLM（带 logprobs）、OpenAI / Anthropic 等
  闭源 API、自家用 ZMQ 的 inference server……研究里经常**混合并联**
  （比如「Claude 出大纲 + 三个本地模型 token 级投票出正文」）。
- **协作模式**不止「响应级聚合」和「token 级投票」：还有按 token 数加权、
  早停投票、speculative decoding 风格的 draft+verify、用 LLM 当评委的
  "judge mode" 等等。每个研究组的玩法都不一样。
- **聚合策略**也远超 `majority_vote` / `concat` / `sum_score`：sentence-bert
  语义投票、Levenshtein 编辑距离、可学习的 mixing head……

把这些都做进**节点本身**，意味着每加一种玩法就要：

1. 新建一个 Dify 节点类
2. 走完 §5.5 的「9 处前端注册」
3. 加一份 `entities.py` schema
4. 加 `node_factory.py` 的注入分支
5. 写两份 i18n
6. ……一周起步

**这是开发者税，不是研究**。本规范的目标是把"加一种协作玩法"压缩到
**写一个 Python 类 + 注册一行**——和现在 `@register("majority_vote")` 一样轻。

### 1.1 设计原则

| # | 原则 | 含义 |
|---|---|---|
| EP-1 | **三轴正交** | "怎么连模型" / "怎么跑协作" / "怎么合信号" 是三件独立的事，不要让任何一轴的扩展强迫其他两轴跟着改。 |
| EP-2 | **Capability 粗过滤 + Requirements 精校验** | Capability 是"能不能做"的布尔位，仅作启动期粗筛（`token_step` 兼容 backend 列表）。具体上限（`top_k≤20`）、版本约束、模型支持矩阵走 `Requirements` 结构化校验，runner 按运行配置生成、backend 按 spec 校验，返回 typed errors。 |
| EP-3 | **单一对外节点 = `parallel-ensemble`** | Dify 画布上只有一个 `parallel-ensemble` 节点（沿用 P2.1 已落地常量；不改名）。"协作模式"是节点配置项，不是节点类型——避免每加一种玩法就跑一遍 9 处前端注册。P1 的 `response-aggregator` 节点保留作为 backwards-compat 着陆点不删，但**不**作为本规范的 fast path。 |
| EP-4 | **安全边界 = DSL/前端 → 服务端，仅此一道** | ⚠️ 不要相信「Python 扩展是受沙箱保护的」。本规范不假设 Python 内进程隔离：第三方 runner / backend 是**受信代码**（与节点同进程，可反射、可 import 任何东西、可读 `__dict__`）。真正能挡住威胁的边界只有「DSL 与前端用户提交的字段」这一道——他们不能塞 url/key、不能在 `runner_config` 里偷渡敏感字段、`extra="forbid"` 在三层 schema 全开。要防恶意的第三方 Python 扩展，必须走进程隔离 / wasm / RPC 沙箱，超出 v0.1 范围。 |
| EP-5 | **现有 P1 / P2.1 不重写，只升级** | `response-aggregator` 节点和 `LocalModelRegistry` 已经落地——本规范是把它们"提升"成参考实现，不是推翻。 |
| EP-6 | **Trace / Diagnostics 是一等数据面** | 用户原始诉求"想轻松获取 logit / 中间数据"决定了 trace 不是附属物。每条数据（token 候选、logits、per-model 输出、think 痕迹、耗时、错误）都通过 `diagnostics_config` 显式开关 + 标准化 `EnsembleTrace` schema 暴露，规模通过 `storage` 策略（v0.2: inline / metadata，v0.3 加 artifact）控制，不让 `outputs.text` 爆炸。 |

---

## 2. 三轴 SPI 总览

```
                    ┌─────────────────────────────────────┐
                    │  parallel-ensemble  (唯一对外节点) │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │                    │                    │
              ▼                    ▼                    ▼
      ╔════════════════╗   ╔════════════════╗   ╔════════════════╗
      ║ ModelBackend   ║   ║ EnsembleRunner ║   ║   Aggregator   ║
      ║   ─ 怎么连     ║   ║   ─ 怎么跑     ║   ║   ─ 怎么合     ║
      ╠════════════════╣   ╠════════════════╣   ╠════════════════╣
      ║ llama_cpp      ║   ║ response_level ║   ║ majority_vote  ║
      ║ vllm           ║   ║ token_step     ║   ║ concat         ║
      ║ openai_compat  ║   ║ judge          ║   ║ sum_score      ║
      ║ anthropic      ║   ║ token_estimate ║   ║ max_score      ║
      ║ <第三方>       ║   ║ <第三方>       ║   ║ <第三方>       ║
      ╚════════════════╝   ╚════════════════╝   ╚════════════════╝
              │                    │                    │
              │  declares          │  declares          │  consumed by
              │  capabilities      │  required_         │  runner
              │                    │  capabilities      │
              └────────► Capability Matrix ◄────────────┘
                       (启动期匹配, UI 自动过滤)
```

### 2.1 一次运行的数据流

```
DSL { runner: "token_step",                  ┐
      aggregator: "sum_score",               │  节点配置 (用户在画布上填)
      models: ["qwen3-4b", "gpt-4o"] }       ┘
                │
                ▼
   ┌──────────────────────────────────┐
   │  Registry: alias → BackendSpec   │  → 启动期匹配:
   └──────────────────────────────────┘    runner.required_caps ⊆
                │                           backend.capabilities
                ▼                           不满足 → ValidationError
   ┌──────────────────────────────────┐
   │  Backend instance per alias       │
   │  (持有 url/key, 实例只在服务端活)  │  EP-4: 不暴露给 runner
   └──────────────────────────────────┘
                │
                ▼
   ┌──────────────────────────────────┐
   │  Runner.run(backends, aggregator) │  → yield Event[Token | Result]
   └──────────────────────────────────┘
                │
                ▼
   node.py 把 Runner Event 翻译成
   graphon StreamChunkEvent / StreamCompletedEvent
```

---

## 3. Capability 矩阵（语义对齐）

> **v3 note (2026-04-29)**：`response_level` runner 在
> `DEVELOPMENT_PLAN_v3.md` 中删除；response 模式统一走
> `response-aggregator` + `ResponseAggregator` SPI。下方 v0.2 的
> `response_level` 相关矩阵保留为历史上下文，token 模式后续按 v3 的
> `token-model-source` + `parallel-ensemble` aggregator-as-executor 路径演进。

> ⚠️ **本节是整份规范最容易出错、影响最大的地方**。Capability 不是布尔值
> 那么简单：OpenAI 的 `logprobs` 不是 llama.cpp 的 `top_probs`，vLLM 的
> `logprobs` 又是另一回事。本节把语义钉死，避免后续 token 级 runner 输出
> 不可信。

### 3.1 Capability 枚举

```python
# api/core/workflow/nodes/parallel_ensemble/spi/capability.py
from enum import Enum

class Capability(str, Enum):
    # 流式：generate_stream() 可用，按 token / chunk yield
    STREAMING = "streaming"

    # 单 token 推进：step_token(prompt, top_k) → list[TokenCandidate]
    # PN.py 的核心要求；不要求是 logits, 但要求每步只前向一个 token
    TOKEN_STEP = "token_step"

    # top-k 候选带概率值（float），不只是排名
    # 如果只声明 TOKEN_STEP 不声明 TOP_PROBS, runner 只能用基于排名的聚合
    TOP_PROBS = "top_probs"

    # 概率是「采样后归一化」(post-softmax + top-k 重归一), 而不是原始 logits
    # llama.cpp 的 post_sampling_probs=true 是这个; OpenAI 也是这个
    # 严格 logit 加和的 runner 不应依赖这个 capability
    POST_SAMPLING_PROBS = "post_sampling_probs"

    # 原始 logits (pre-softmax, 全 vocab)
    # vLLM 通过 return_logits 内部接口可拿到; 闭源 API 没有
    LOGITS_RAW = "logits_raw"

    # 服务端持有 chat template, 客户端可调 apply_template(messages) → prompt
    # llama.cpp /apply-template 有; vLLM 内部隐式应用; OpenAI/Anthropic 不暴露
    CHAT_TEMPLATE = "chat_template"

    # OpenAI 风格 function/tool calling 结构化输出
    FUNCTION_CALLING = "function_calling"

    # PN.py 的 clear_slot_kv_cache 类优化, 跨 token 复用 KV cache
    # 当前明确不做（§1.2 非目标）, 但 SPI 留位
    KV_CACHE_REUSE = "kv_cache_reuse"
```

### 3.2 参考后端的 capability 矩阵

| Capability | llama_cpp | vllm | openai_compat | anthropic |
|---|:---:|:---:|:---:|:---:|
| `STREAMING` | ✅ `/completion?stream=true` | ✅ SSE `/v1/completions?stream=true` | ✅ SSE | ✅ SSE |
| `TOKEN_STEP` | ✅ `max_tokens=1, n_probs=k` | ✅ `max_tokens=1, logprobs=k` | ⚠️ `max_tokens=1, logprobs=true, top_logprobs=k` 仅 chat-completions | ❌ |
| `TOP_PROBS` | ✅ `completion_probabilities[0].top_probs` | ✅ `choices[0].logprobs.top_logprobs` | ⚠️ 0–20 上限 | ❌ |
| `POST_SAMPLING_PROBS` | ✅ `post_sampling_probs=true` | ⚠️ vLLM 默认是 logprobs (log-softmax), 不是 post-sampling 重归一; 语义需 adapter 内换算 | ✅ logprobs 即 post-sampling | — |
| `LOGITS_RAW` | ❌（除非用我们改过的 fork） | ✅ `return_logits` extension | ❌ | ❌ |
| `CHAT_TEMPLATE` | ✅ `/apply-template` | ❌（隐式） | N/A | N/A |
| `FUNCTION_CALLING` | ❌ | ⚠️（部分模型） | ✅ | ✅ |
| `KV_CACHE_REUSE` | ⚠️（slot id 路径，未实现） | ❌（vLLM 内部管） | ❌ | ❌ |

**⚠️ 三个最容易踩的语义坑**：

1. **`POST_SAMPLING_PROBS` vs `LOGITS_RAW`**：llama.cpp `post_sampling_probs=true`
   返回的是「采样空间归一化后的概率」（top-k 内部重归一到和为 1），**不是
   raw logits**。如果 runner 想做「真正的 logit 加权平均」（PN.py 严格语义），
   需要 `LOGITS_RAW` 而不是 `POST_SAMPLING_PROBS`。两者算出的 ensemble 结果
   会不同。
2. **OpenAI `logprobs` 的 top_k 上限是 20**，且不是所有模型都支持，且仅
   chat-completions 端点。如果 runner 要 `top_k > 20`，OpenAI backend 应该
   在 capability 协商阶段就被排除，而不是运行时报错。
3. **vLLM 的 `logprobs` 是 log-softmax 后的值**，需要 `exp()` 再归一化才能
   和 llama.cpp 的 `top_probs` 语义对齐。Adapter 内部必须做这个换算，
   不要把语义不一致的 float 直接喂给 aggregator。

### 3.3 Runner ↔ Capability 的合法组合

| Runner | Required | Optional | 兼容 backend |
|---|---|---|---|
| `response_level` | `STREAMING`（弱要求，可降级到非流式 `generate`） | `FUNCTION_CALLING` | 全部 4 种 |
| `token_step` | `TOKEN_STEP`, `TOP_PROBS` | `POST_SAMPLING_PROBS`, `CHAT_TEMPLATE` | llama_cpp, vllm |
| `token_step_strict` | `TOKEN_STEP`, `LOGITS_RAW` | — | vllm（+ 改版 llama_cpp） |
| `judge`（LLM 评委）| `STREAMING` | — | 全部 4 种 |
| `token_estimate`（用户提的）| `STREAMING` | — | 全部 4 种 |

UI 端选完 runner 后，模型多选下拉**只显示满足该 runner required_caps 的
alias**，其余灰掉并 tooltip 解释「该模型不支持 token-step，因为它的 backend
是 anthropic」。

### 3.4 Requirements：精校验层（Capability 之上）

> 评审指出：单纯布尔 capability 表达不了「`top_k≤20`」「`gpt-3.5-turbo-0301`
> 不支持 logprobs」「vLLM 0.5 vs 0.6 行为差异」「runner config 决定 backend
> 是否兼容」这类约束。本节定义 capability 之上的结构化约束层。

**两层校验**：

| 层 | 输入 | 时机 | 失败结果 |
|---|---|---|---|
| Capability 粗过滤 | `runner.required_capabilities ⊆ backend.capabilities(spec)` | 启动期 + UI 下拉过滤 | alias 灰掉, tooltip 说原因 |
| Requirements 精校验 | `backend.validate_requirements(spec, runner.requirements(config))` | 启动期（DSL 导入 / 单步预校验） | typed `ValidationIssue[]`, 节点标红, 错误显示在 panel |

**接口签名**（v0.2 新增）：

```python
# api/core/workflow/nodes/parallel_ensemble/spi/requirements.py

class Requirement(TypedDict, total=False):
    """Runner 对 backend 的具体诉求, runner.requirements(config) 产出 list."""
    kind: Literal[
        "min_top_k",          # value: int  - 需要 top_k 候选数 ≥ value
        "needs_logprobs",     # value: bool - 必须返回带 prob 的候选 (TOP_PROBS 之上的精确性)
        "min_context_tokens", # value: int
        "needs_function_calling",
        "needs_chat_template",
        "min_backend_version",  # value: str semver
        "model_allowlist",    # value: list[str] - 仅这些 model_name 被允许 (兼容性已知差时)
    ]
    value: object             # kind 决定 value 类型
    rationale: str            # 给前端 tooltip / 错误信息用


class ValidationIssue(TypedDict):
    """backend.validate_requirements 返回的结构化问题."""
    severity: Literal["error", "warning"]
    requirement: Requirement
    message: str              # 人话, 已经填了模型名 / 上限值 / etc
    i18n_key: str | None      # 前端可用 key 翻译
```

**Backend 侧**（`ModelBackend` 加方法）：

```python
class ModelBackend(ABC):
    @classmethod
    @abstractmethod
    def validate_requirements(
        cls, spec: BaseSpec, requirements: list[Requirement]
    ) -> list[ValidationIssue]:
        """对每条 requirement 检查; 返回空 list 表示全部通过."""
```

**Runner 侧**：

```python
class EnsembleRunner(ABC, Generic[ConfigT]):
    @classmethod
    @abstractmethod
    def requirements(cls, config: ConfigT) -> list[Requirement]:
        """从运行配置生成对 backend 的诉求 list."""
```

**示例：`token_step` runner + OpenAI backend + `top_k=25` 的拒绝路径**

```python
class TokenStepRunner(EnsembleRunner[TokenStepConfig]):
    @classmethod
    def requirements(cls, config: TokenStepConfig) -> list[Requirement]:
        return [
            {"kind": "needs_logprobs", "value": True,
             "rationale": "token-step voting needs candidate probabilities"},
            {"kind": "min_top_k", "value": config.top_k,
             "rationale": f"runner is configured with top_k={config.top_k}"},
        ]

class OpenAICompatBackend(ModelBackend):
    @classmethod
    def validate_requirements(cls, spec, requirements):
        issues: list[ValidationIssue] = []
        for req in requirements:
            if req["kind"] == "min_top_k" and req["value"] > 20:
                issues.append({
                    "severity": "error",
                    "requirement": req,
                    "message": f"OpenAI top_logprobs is capped at 20, "
                               f"runner requested {req['value']}",
                    "i18n_key": "parallelEnsemble.errors.openaiTopKCap",
                })
            if req["kind"] == "min_top_k" and spec.model_name.startswith("gpt-3.5-turbo-0301"):
                issues.append({
                    "severity": "error",
                    "requirement": req,
                    "message": "gpt-3.5-turbo-0301 does not support logprobs",
                    "i18n_key": "parallelEnsemble.errors.modelNoLogprobs",
                })
        return issues
```

**Capability 与 Requirements 的关系**：

- Capability 决定「这种 backend 在这种 runner 下**有可能**工作吗」（粗过滤，
  与 config 无关，UI 一进面板就能算）
- Requirements 决定「这个 spec + 这份 config **实际**能不能工作」（精校验，
  config 变化即重算，DSL 导入 / 保存时必跑）
- Capability 是 Requirements 的 default 实现兜底——如果 backend 没 override
  `validate_requirements`，框架按 `required_capabilities` ⊆ `capabilities`
  做最简单检查；任何想表达细粒度上限的 backend 必须 override。

---

## 4. ModelBackend SPI

### 4.1 接口签名

```python
# api/core/workflow/nodes/parallel_ensemble/spi/backend.py
from __future__ import annotations
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterator
from typing import ClassVar, TypedDict
from pydantic import BaseModel

from .capability import Capability


class ChatMessage(TypedDict):
    role: str         # "system" | "user" | "assistant" | "tool"
    content: str


class GenerationParams(TypedDict, total=False):
    max_tokens: int
    temperature: float
    top_p: float
    top_k: int                # 仅 TOKEN_STEP runner 用
    stop: list[str]
    seed: int | None


class TokenCandidate(TypedDict):
    token: str
    prob: float               # post-sampling-prob OR sampled-logprob.exp()——adapter 须归一化
    logit: float | None       # 仅 LOGITS_RAW capability 时填


class GenerationResult(TypedDict):
    text: str
    finish_reason: str        # "stop" | "length" | "tool_call" | ...
    metadata: dict            # backend-specific 诊断


class StreamChunk(TypedDict):
    delta: str                # 增量文本
    is_final: bool


class ModelBackend(ABC):
    """one instance per (alias × workflow run); 持有 url/key, 不外漏。

    评审 v0.2.2 修订: 给 runner / aggregator 用的公共属性 (id / model_name /
    weight / instance_capabilities) 在基类上明确为 public, 不让二开者读 _spec。
    虽然 §4.4 已说"Python 同进程不防反射", 让公共 API 显式存在是 DX 问题
    而不是安全问题——避免 IDE 看到 `_` 前缀红线、避免重构时破坏二开者代码。
    """

    name: ClassVar[str]                       # registry key, e.g. "llama_cpp"
    spec_class: ClassVar[type[BaseModel]]     # backend-specific spec schema

    def __init__(self, spec: BaseModel, http: object) -> None:
        # http: SsrfProxyHttpClient (自托管) 或 cloud SDK client
        self._spec = spec
        self._http = http

    # —— 公开投影 (基类提供, 二开者只读这些, 别读 _spec) ——
    @property
    def id(self) -> str:
        """spec.id, 即 yaml 里的 alias; runner 用它做 dict key / trace key。"""
        return self._spec.id

    @property
    def model_name(self) -> str:
        return self._spec.model_name

    @property
    def weight(self) -> float:
        return self._spec.weight

    @property
    def instance_capabilities(self) -> frozenset[Capability]:
        """capabilities(spec) 的实例侧 cache; runner / aggregator 用它做 if 分支。"""
        return type(self).capabilities(self._spec)

    @classmethod
    @abstractmethod
    def capabilities(cls, spec: BaseModel) -> frozenset[Capability]:
        """声明该 spec 实例支持的 capability。spec 内可能含 model_name 或
        其他字段决定能力——例如同一 OpenAI backend, gpt-4o 支持 logprobs 但
        gpt-3.5-turbo-0301 不支持。"""

    # —— 响应级 ——
    @abstractmethod
    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult: ...

    def generate_stream(
        self, prompt: str, params: GenerationParams
    ) -> Iterator[StreamChunk]:
        """STREAMING capability 必须 override; 默认报 CapabilityNotSupported。"""
        raise CapabilityNotSupported(self.name, Capability.STREAMING)

    # —— Token 级 ——
    def step_token(
        self, prompt: str, top_k: int
    ) -> list[TokenCandidate]:
        """TOKEN_STEP capability 必须 override; 默认报 CapabilityNotSupported。

        语义契约: 返回的 list 长度 ≤ top_k, prob 之和应在 [0, 1] 之间;
        若声明 TOP_PROBS 则 prob 字段必须可信; 若声明 LOGITS_RAW 则 logit
        字段不为 None。
        """
        raise CapabilityNotSupported(self.name, Capability.TOKEN_STEP)

    # —— 模板 ——
    def apply_template(self, messages: list[ChatMessage]) -> str:
        """CHAT_TEMPLATE capability 必须 override; 默认 fallback 拼字符串
        (system + user + ... 的天真拼接, 仅供没有服务端模板的云端 API)。"""
        return "\n\n".join(f"{m['role']}: {m['content']}" for m in messages)
```

### 4.2 公开投影（给前端 / runner 看）

```python
class BackendInfo(TypedDict):
    """list_aliases() 返回的元素; 永远不包含 url / api_key / 任何凭据。"""
    id: str
    backend: str                    # "llama_cpp" | "vllm" | ...
    model_name: str                 # 给用户看的名字
    capabilities: list[str]         # frozenset 平铺
    metadata: dict                  # 可选: max_context, model_arch, ...
```

### 4.3 Registry 升级：discriminated union

```yaml
# api/configs/model_net.yaml
models:
  # ── 自托管 llama.cpp（向后兼容现有 P2.1 ModelSpec）──
  - id: qwen3-4b-local
    backend: llama_cpp                      # ← 新增 discriminator
    model_name: qwen3-4b-bf16
    model_arch: llama
    model_url: http://10.0.0.5:30763        # ← 仅 self-host 才有
    EOS: "<|im_end|>"
    type: think
    stop_think: "</think>"
    weight: 1.0
    request_timeout_ms: 30000

  # ── 自托管 vLLM ──
  - id: qwen3-32b-vllm
    backend: vllm
    model_name: Qwen/Qwen3-32B-Instruct
    base_url: http://10.0.0.6:8000          # /v1/...
    api_key_env: VLLM_KEY                   # 可选, vLLM 默认无鉴权
    weight: 2.0
    request_timeout_ms: 60000

  # ── OpenAI 兼容（含 OpenAI 本身、Together、DeepInfra 等）──
  - id: gpt-4o
    backend: openai_compat
    model_name: gpt-4o-2024-08-06
    base_url: https://api.openai.com         # 可改成 Together 等
    api_key_env: OPENAI_API_KEY              # 必填; 引用 env 名而不是值
    weight: 1.0

  # ── Anthropic ──
  - id: claude-sonnet
    backend: anthropic
    model_name: claude-sonnet-4-6
    api_key_env: ANTHROPIC_API_KEY
    weight: 1.0
```

**实现：动态 spec 分发**（v0.2 评审 H1 修订）

> ⚠️ **v0.1 错误**：v0.1 §4.3 同时说"用 `Annotated[Union[...], Field(discriminator)]`"
> 与"第三方加 backend 不修改 union"——这两件事互斥。Pydantic discriminated union
> 是 parse 时静态决议的，第三方注册的 spec 永远进不来。
>
> v0.2 修法：**抛弃 Annotated Union，registry 加载时按 `backend` 字符串到
> `backend_registry` 动态查 spec_class**。union 类型只用于 `BackendInfo`
> 的纯文档；运行期不依赖。

#### 4.3.1 BaseSpec + 各 backend 的 Spec 子类

```python
# api/core/workflow/nodes/parallel_ensemble/spi/backend.py
from typing import ClassVar, Literal
from pydantic import BaseModel, ConfigDict, Field, AnyUrl

class BaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    backend: str = Field(min_length=1)              # discriminator, 由子类 Literal 收紧
    model_name: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0)
    request_timeout_ms: int = Field(default=30000, gt=0)
```

每个 backend 自带 spec 子类（仅 v0.2 落地的 llama_cpp 演示；vllm / openai /
anthropic 是 v0.3 范例）：

```python
# backends/llama_cpp.py
class LlamaCppSpec(BaseSpec):
    backend: Literal["llama_cpp"]
    model_arch: str = "llama"
    model_url: AnyUrl
    EOS: str = Field(min_length=1)
    type: Literal["normal", "think"] = "normal"
    stop_think: str | None = None

@register_backend("llama_cpp")
class LlamaCppBackend(ModelBackend):
    spec_class: ClassVar[type[BaseSpec]] = LlamaCppSpec
    ...
```

第三方 backend 完全对称，无需改 framework 代码：

```python
# 第三方包内: my_pkg/backends/zmq.py
class MyZmqSpec(BaseSpec):
    backend: Literal["my_zmq"]
    zmq_endpoint: str = Field(min_length=1)
    auth_token_env: str | None = None

@register_backend("my_zmq")
class MyZmqBackend(ModelBackend):
    spec_class = MyZmqSpec
    ...
```

#### 4.3.2 BackendRegistry：spec_class 反查

```python
# registry/backend_registry.py
class BackendRegistry:
    _backends: dict[str, type[ModelBackend]] = {}

    @classmethod
    def register(cls, name: str, backend_cls: type[ModelBackend]) -> None:
        if name in cls._backends:
            raise ValueError(f"backend '{name}' already registered")
        if not issubclass(backend_cls.spec_class, BaseSpec):
            raise TypeError(
                f"backend '{name}' spec_class must extend BaseSpec, "
                f"got {backend_cls.spec_class!r}"
            )
        cls._backends[name] = backend_cls

    @classmethod
    def get(cls, name: str) -> type[ModelBackend]:
        try:
            return cls._backends[name]
        except KeyError as e:
            raise UnknownBackendError(name) from e

    @classmethod
    def get_spec_class(cls, name: str) -> type[BaseSpec]:
        return cls.get(name).spec_class

    @classmethod
    def known_backends(cls) -> list[str]:
        return sorted(cls._backends)


def register_backend(name: str):
    """装饰器形态."""
    def deco(cls: type[ModelBackend]) -> type[ModelBackend]:
        cls.name = name
        BackendRegistry.register(name, cls)
        return cls
    return deco
```

#### 4.3.3 ModelRegistry._load：两阶段解析

**关键约束**：backend registry **必须在** model registry 加载 yaml **之前**
被填充，否则 `backend: my_zmq` 会被判 unknown。具体落地靠 `pkgutil.walk_packages`
的递归 import 顺序——先扫 `backends/`（触发 `@register_backend`），再
`ModelRegistry.instance()._load()`。

```python
# registry/model_registry.py
class ModelRegistry:
    def _load(self, path_override: str | None = None) -> None:
        path_str = path_override or self._resolve_path()
        path = Path(path_str)
        if not path.exists():
            logger.warning("Model registry yaml not found at '%s'; ...", path_str)
            self._models = {}
            return

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise RegistryFileError(path_str, str(exc)) from exc

        if raw is None:
            self._models = {}
            return
        if not isinstance(raw, dict):
            raise RegistryFileError(path_str, "top-level yaml must be a mapping")

        entries = raw.get("models", [])
        if not isinstance(entries, list):
            raise RegistryFileError(path_str, "'models' must be a list")

        models: dict[str, BaseSpec] = {}
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise RegistryFileError(
                    path_str, f"models[{index}] must be a mapping, got {type(entry).__name__}"
                )

            # ── 阶段 1: 抽 backend 字符串 ──
            backend_name = entry.get("backend")
            if not isinstance(backend_name, str) or not backend_name:
                raise RegistryFileError(
                    path_str,
                    f"models[{index}] missing or empty 'backend' field; "
                    f"expected one of {BackendRegistry.known_backends()}"
                )

            # ── 阶段 2: 用 backend_registry 反查 spec_class, 动态校验 ──
            try:
                spec_class = BackendRegistry.get_spec_class(backend_name)
            except UnknownBackendError:
                raise RegistryFileError(
                    path_str,
                    f"models[{index}] backend '{backend_name}' is not registered; "
                    f"known backends: {BackendRegistry.known_backends()}. "
                    f"If this is a third-party backend, ensure the package is "
                    f"importable before registry load."
                ) from None

            try:
                spec = spec_class.model_validate(entry)
            except ValidationError as exc:
                raise RegistryFileError(
                    path_str, f"models[{index}] (backend={backend_name}) invalid: {exc}"
                ) from exc

            if spec.id in models:
                raise RegistryFileError(path_str, f"duplicate model id '{spec.id}'")
            models[spec.id] = spec

        self._models = models
```

**与 P2.1 已落地代码的差异**（升级路径，不重写）：
- `LocalModelRegistry` → `ModelRegistry`（重命名；保留旧名 alias 一个版本）
- `_load()` 里 `ModelSpec.model_validate(entry)` → `spec_class.model_validate(entry)`
  按 backend 反查
- `ModelSpec` 拆成 `BaseSpec` + `LlamaCppSpec`（字段不变，只是分层）
- 新增 `BackendRegistry` + `register_backend` 装饰器，`backends/llama_cpp.py`
  里的 `LlamaCppBackend` 自动注册
- `_models` 从 `dict[str, ModelSpec]` 变为 `dict[str, BaseSpec]`，类型更宽
  （子类协变）；`get(alias)` 返回的是具体子类实例，runner / backend 拿到
  时 `isinstance(spec, LlamaCppSpec)` 收窄

#### 4.3.4 第三方 backend 的发现路径

模块发现两条路（v0.2 仅实现 (a)）：

| # | 路径 | 时机 | 注 |
|---|---|---|---|
| (a) | 放进 `api/core/workflow/nodes/parallel_ensemble/backends/<name>.py` | `_import_node_package` 自动 import | v0.2 落地；要求第三方 fork 仓库 |
| (b) | `model_net.yaml` 顶部 `extra_backend_modules: ["my_pkg.backends.zmq"]` | registry `_load()` 前显式 `importlib.import_module(...)` | v0.3 落地（OQ-OQ 要先评估 import path 安全） |

无论哪条，**都必须保证 backend 模块在 `ModelRegistry._load()` 调用前被 import**，
否则 `BackendRegistry.get(...)` 报 unknown。

#### 4.3.5 静态类型友好的辅助 union（仅文档/IDE 用，不参与运行期校验）

为了让 IDE 在标注 `spec: AnyKnownSpec` 时有类型提示，框架可以维护一个**纯
文档性**的 union（仅含框架内置 spec），但**绝不**在 yaml 校验路径用：

```python
# 仅 IDE/类型检查器用; 第三方 backend 不需要进这个 union
KnownBuiltinSpec = LlamaCppSpec | VllmSpec | OpenAICompatSpec | AnthropicSpec
```

第三方代码自己用 `BaseSpec` 类型标注即可（协变兜底）。

### 4.4 安全边界（v0.2 修订：唯一边界 = DSL/前端 → 服务端）

> ⚠️ **v0.1 错误回滚**：v0.1 §4.4 声称「Runner 拿到 backend 实例后反射读
> `_spec.model_url`」可以由 `_credentials` + `__init_subclass__` 拦住——这在
> 同进程 Python 里根本挡不住。第三方 runner / backend 是**受信代码**，可以
> `vars(obj)` / `obj.__dict__` / 自己 `import httpx`。把这条算进安全保证是
> 误导。v0.2 把这条删除，并明确：**威胁模型只覆盖一道边界——DSL / 前端用户
> → 服务端**。

| # | 威胁（仅限本规范防护范围） | 防护 | 落点 |
|---|---|---|---|
| T1 | 工作流作者在 DSL 里塞任意 URL（→ SSRF / 内网扫描） | DSL 节点配置只允许 `model_aliases: list[str]`；三层 schema 全开 `extra="forbid"`（节点 / runner_config / aggregator_config）拒绝偷渡 url 字段 | `ParallelEnsembleNodeData` schema |
| T2 | 前端用户从控制台 API 拿到 url / api_key | `BackendInfo` TypedDict 显式 allowlist `{id, backend, model_name, capabilities, metadata}`；控制器返回路径用 `BackendInfo(**)` 投影而不是 `dict(spec)` | `Registry.list_aliases()` 单测钉死「response 不含 url / api_key / api_key_env」 |
| T3 | 前端用户在 panel 里输入凭据进 yaml / 日志 | `api_key` 字段不接受字面量；只接受 `api_key_env: str`（环境变量名）；spec 加载时 resolve 成 `SecretStr`，`__repr__` 自动 mask；前端 panel 不渲染 api_key 输入框 | `BaseSpec.api_key_env` resolver + 前端 schema |
| T4 | 自托管 endpoint 由配置以外的路径绕过 `ssrf_proxy` | 框架强制：自托管 backend（llama_cpp / vllm）的 http client 由 framework 在构造器注入 `SsrfProxyHttpClient`，参考 `node_factory.py:300, 383` HTTP_REQUEST 的注入模式（ADR-8） | code review + 注入分支单测 |

**v0.2 不防护的威胁（明确认账）**：

- **T-OUT-1**：恶意第三方 Python 扩展通过反射 `obj.__dict__` 读 url / key。
  Python 同进程没有访问控制；要防这条须走进程隔离 / wasm / RPC 沙箱，
  超出 v0.1。**第三方 runner / backend 代码视为受信代码**，对它们的代码
  审计与对节点本体代码的代码审计同等级。
- **T-OUT-2**：恶意第三方扩展自行 `import requests` 绕开框架 http client。
  v0.2 通过「pkgutil 自动 import 限定在 `runners/` `backends/` 包内 +
  CI lint 规则禁止这两个目录直接 import 网络库」做软约束，但**不算硬安全
  保证**——同 T-OUT-1。
- **T-OUT-3**：扩展把 trace 落盘到非预期位置造成信息泄漏。trace 写入路径
  由框架统一管（§7），扩展只能 yield 标准 `TraceEntry`；但扩展仍可绕开
  trace 自己写文件，同 T-OUT-1 兜底原则。

> 一句话：本规范的安全模型 = 「Dify 用户不可信，第三方 Python 扩展可信」。
> 改变后者需要新建一份 `SANDBOX_SPEC.md`，不在 v0.2 范围。

---

## 5. EnsembleRunner SPI

### 5.1 接口签名

```python
# api/core/workflow/nodes/parallel_ensemble/spi/runner.py
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import ClassVar, Generic, TypeVar
from pydantic import BaseModel

from .backend import ModelBackend
from .capability import Capability
from .aggregator import Aggregator

ConfigT = TypeVar("ConfigT", bound=BaseModel)


class TokenEvent(TypedDict):
    kind: Literal["token"]
    delta: str

class FullResponseEvent(TypedDict):
    kind: Literal["full_response"]
    source_id: str
    text: str

class DoneEvent(TypedDict):
    kind: Literal["done"]
    text: str                     # 最终 text(被 node.py 写到 outputs.text)
    metadata: dict


RunnerEvent = TokenEvent | FullResponseEvent | DoneEvent


class EnsembleRunner(ABC, Generic[ConfigT]):
    name: ClassVar[str]
    config_class: ClassVar[type[BaseModel]]   # runner 自己的 config schema
    aggregator_scope: ClassVar[str]           # "response" | "token" | 自定义
    required_capabilities: ClassVar[frozenset[Capability]]
    optional_capabilities: ClassVar[frozenset[Capability]] = frozenset()

    # ── v0.2 新增：UI 元数据 ──
    # 评审指出 Pydantic schema 不足以支撑 Dify 前端表单 (i18n + 控件类型 +
    # tooltip + 校验文案); 补三件套
    i18n_key_prefix: ClassVar[str]
    """e.g. 'parallelEnsemble.runners.tokenStep' — 前端拼出
    `<prefix>.name` / `<prefix>.description` / `<prefix>.fields.<fieldName>.label`
    / `<prefix>.fields.<fieldName>.tooltip`; 必须在 en-US + zh-Hans 两套
    workflow.json 都注册"""

    ui_schema: ClassVar[dict]
    """每字段的控件类型和约束, JSON Schema 风格但仅支持 v0.2 白名单控件:

        {
          "top_k":   {"control": "number_input", "min": 1, "max": 20, "step": 1},
          "max_len": {"control": "number_input", "min": 1},
          "enable_think": {"control": "switch"},
          "judge_alias": {"control": "model_alias_select"},  # 特殊: 取 list_aliases
        }

    控件白名单 v0.2: number_input / text_input / textarea / switch / select /
    multi_select / model_alias_select. 不在白名单的字段前端拒渲染, 报错。
    """

    @classmethod
    def config_schema_json(cls) -> dict:
        """从 config_class 导出 Pydantic JSON Schema, 给前端兜底校验"""
        return cls.config_class.model_json_schema()

    @classmethod
    @abstractmethod
    def requirements(cls, config: ConfigT) -> list[Requirement]:
        """v0.2 新增: 从 config 派生对 backend 的诉求列表; 见 §3.4"""

    @classmethod
    def validate_selection(
        cls,
        config: ConfigT,
        model_aliases: list[str],
        registry: "ModelRegistry",
    ) -> list[ValidationIssue]:
        """v0.2.2 新增: 跨字段校验 (config 与所选模型的关系)。

        例: draft_verify runner 要求 config.draft_alias ∈ model_aliases;
        token_step 要求 len(model_aliases) ≥ 2; judge runner 要求 config.judge_alias
        ∈ model_aliases。这些约束 requirements(config) 表达不了 (那个只看
        config 不看选了哪些 alias)。

        默认实现返回空 list (无跨字段约束); 有约束的 runner override。

        启动期 §9 流水线的第 5 步会调用此方法。
        """
        return []

    @abstractmethod
    def run(
        self,
        question: str,
        backends: dict[str, ModelBackend],    # alias → backend; 已校验过 capability + requirements
        aggregator: Aggregator,               # scope 已对齐, 见 §6
        config: ConfigT,
        trace: TraceCollector,                # v0.2 新增, 见 §7
    ) -> Iterator[RunnerEvent]:
        """Yield runner events. node.py 翻译成 graphon stream events.

        - backends 是 dict 不是 list (v0.2.2): 让 runner 用 alias 做 dict key
          顺手; 也避免 runner 反射读 backend._spec.id
        - 流式 runner 应交错 yield TokenEvent, 末尾一个 DoneEvent
        - 非流式 runner 可只 yield 一个 DoneEvent (text 直接写)
        - judge 类 runner 可先 yield 多个 FullResponseEvent 再 yield DoneEvent
        - runner 调 trace.record_*() 把诊断数据进 EnsembleTrace, 由
          diagnostics_config 决定是否真的记录
        """
```

### 5.2 参考 runner（v0.2.2 更新：示例使用全部抽象方法，可直接编译）

> 评审指出 v0.2 这一节示例还是旧签名（缺 name / config_class / i18n_key_prefix /
> ui_schema / requirements / validate_selection / `trace` 参数 / dict-keyed
> backends），二开者照抄会撞抽象方法未实现。v0.2.2 全部改成 v0.2 完整契约。

#### 5.2.1 `response_level` —— 包装 P1 response-aggregator

```python
class ResponseLevelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

@register_runner("response_level")
class ResponseLevelRunner(EnsembleRunner[ResponseLevelConfig]):
    name = "response_level"
    config_class = ResponseLevelConfig
    aggregator_scope = "response"
    required_capabilities = frozenset()
    optional_capabilities = frozenset({Capability.STREAMING})
    i18n_key_prefix = "parallelEnsemble.runners.responseLevel"
    ui_schema = {}                                # 无字段, 空 schema

    @classmethod
    def requirements(cls, config):
        return []

    @classmethod
    def validate_selection(cls, config, model_aliases, registry):
        if len(model_aliases) < 2:
            return [{"severity": "error",
                     "requirement": {"kind": "min_top_k", "value": 0,
                                      "rationale": "response_level needs ≥ 2 models"},
                     "message": "response_level runner requires at least 2 model aliases",
                     "i18n_key": "parallelEnsemble.errors.tooFewModels"}]
        return []

    def run(self, question, backends, aggregator, config, trace):
        # backends 是 dict[alias, ModelBackend]; 并发调 generate, 收齐后喂 aggregator
        signals = []
        # ... ThreadPoolExecutor 并发, 每个 backend.generate(prompt, ...) ...
        for alias, b in backends.items():
            res = b.generate(question, {"max_tokens": 1024})
            signals.append({"source_id": alias, "text": res["text"],
                            "finish_reason": res["finish_reason"],
                            "elapsed_ms": 0, "error": None})
            trace.record_response({"source_id": alias, "text": res["text"],
                                    "finish_reason": res["finish_reason"],
                                    "tokens_count": 0, "elapsed_ms": 0, "error": None})
        result = aggregator.aggregate(signals, ctx, config.aggregator_config)
        yield {"kind": "done", "text": result["text"], "metadata": result["metadata"]}
```

#### 5.2.2 `token_step` —— PN.py

```python
class TokenStepConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    top_k: int = Field(default=5, gt=0, le=20)
    max_len: int = Field(default=1000, gt=0)
    enable_think: bool = True

@register_runner("token_step")
class TokenStepRunner(EnsembleRunner[TokenStepConfig]):
    name = "token_step"
    config_class = TokenStepConfig
    aggregator_scope = "token"
    required_capabilities = frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})
    optional_capabilities = frozenset({Capability.CHAT_TEMPLATE})
    i18n_key_prefix = "parallelEnsemble.runners.tokenStep"
    ui_schema = {
        "top_k":   {"control": "number_input", "min": 1, "max": 20, "step": 1},
        "max_len": {"control": "number_input", "min": 1, "step": 1},
        "enable_think": {"control": "switch"},
    }

    @classmethod
    def requirements(cls, config):
        return [
            {"kind": "min_top_k", "value": config.top_k,
             "rationale": f"token_step is configured with top_k={config.top_k}"},
            {"kind": "needs_logprobs", "value": True,
             "rationale": "token_step needs candidate probabilities"},
        ]

    @classmethod
    def validate_selection(cls, config, model_aliases, registry):
        issues = []
        if len(model_aliases) < 2:
            issues.append({"severity": "error",
                            "requirement": {"kind": "min_top_k", "value": 0,
                                             "rationale": "token_step needs ≥ 2 models"},
                            "message": "token_step requires at least 2 model aliases",
                            "i18n_key": "parallelEnsemble.errors.tooFewModels"})
        if config.enable_think and not any(
            registry.get(a).type == "think" for a in model_aliases
            if hasattr(registry.get(a), "type")
        ):
            issues.append({"severity": "warning",
                            "requirement": {"kind": "needs_chat_template", "value": False,
                                             "rationale": "enable_think=True but no think-type models"},
                            "message": "enable_think is on but none of the selected models are type=think; think phase will be a no-op",
                            "i18n_key": "parallelEnsemble.errors.thinkNoModels"})
        return issues

    def run(self, question, backends, aggregator, config, trace):
        # 等价 PN.py 主循环: 每 token 并发 step_token, aggregator.aggregate, yield delta
        # 详细实现见 P2.6 落地代码
        ...
```

#### 5.2.3 `judge` —— LLM 评委 runner（先生成 N 份, 再让 judge 选）

```python
class JudgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    judge_alias: str = Field(min_length=1)
    judge_prompt: str = "Pick the best answer."

@register_runner("judge")
class JudgeRunner(EnsembleRunner[JudgeConfig]):
    name = "judge"
    config_class = JudgeConfig
    aggregator_scope = "response"
    required_capabilities = frozenset()
    i18n_key_prefix = "parallelEnsemble.runners.judge"
    ui_schema = {
        "judge_alias": {"control": "model_alias_select"},
        "judge_prompt": {"control": "textarea"},
    }

    @classmethod
    def requirements(cls, config):
        return []

    @classmethod
    def validate_selection(cls, config, model_aliases, registry):
        if config.judge_alias not in model_aliases:
            return [{"severity": "error",
                      "requirement": {"kind": "model_allowlist", "value": [config.judge_alias],
                                       "rationale": "judge_alias must be in selected models"},
                      "message": f"judge_alias '{config.judge_alias}' not in model_aliases",
                      "i18n_key": "parallelEnsemble.errors.judgeAliasNotSelected"}]
        if len(model_aliases) < 2:
            return [{"severity": "error",
                      "requirement": {"kind": "min_top_k", "value": 0,
                                       "rationale": "judge needs ≥ 2 contestants"},
                      "message": "judge runner requires at least 2 model aliases (1 judge + 1 contestant)",
                      "i18n_key": "parallelEnsemble.errors.tooFewModels"}]
        return []

    def run(self, question, backends, aggregator, config, trace):
        contestants = {a: b for a, b in backends.items() if a != config.judge_alias}
        for alias, b in contestants.items():
            res = b.generate(question, {"max_tokens": 1024})
            yield {"kind": "full_response", "source_id": alias, "text": res["text"]}
        # 让 judge 评判
        judge = backends[config.judge_alias]
        # ... 拼 judge_prompt + contestants 答案 -> judge.generate -> 解析 ...
        picked_text = ...
        yield {"kind": "done", "text": picked_text, "metadata": {"judge": config.judge_alias}}
```

#### 5.2.4 `token_estimate` —— 按生成长度加权

```python
class TokenEstimateConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_tokens: int = Field(default=100, gt=0)

@register_runner("token_estimate")
class TokenEstimateRunner(EnsembleRunner[TokenEstimateConfig]):
    name = "token_estimate"
    config_class = TokenEstimateConfig
    aggregator_scope = "response"
    required_capabilities = frozenset({Capability.STREAMING})
    i18n_key_prefix = "parallelEnsemble.runners.tokenEstimate"
    ui_schema = {"sample_tokens": {"control": "number_input", "min": 1, "step": 10}}

    @classmethod
    def requirements(cls, config):
        return []

    @classmethod
    def validate_selection(cls, config, model_aliases, registry):
        if len(model_aliases) < 2:
            return [{"severity": "error",
                      "requirement": {"kind": "min_top_k", "value": 0,
                                       "rationale": "token_estimate needs ≥ 2 models to weigh"},
                      "message": "token_estimate runner requires at least 2 model aliases",
                      "i18n_key": "parallelEnsemble.errors.tooFewModels"}]
        return []

    def run(self, question, backends, aggregator, config, trace):
        # 1. 每 backend 流式抽 sample_tokens 估算速率与终止概率
        # 2. 派生权重传给 aggregator
        # 3. 走 generate 完整再 aggregate
        ...
```

### 5.3 第三方 runner 三步落地（v0.2: 仅 fork 内目录）

```python
# 1) 在 api/core/workflow/nodes/parallel_ensemble/runners/ 下新建 my_runner.py
class MyRunnerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    my_param: float = 0.5

@register_runner("semantic_vote")
class SemanticVoteRunner(EnsembleRunner[MyRunnerConfig]):
    name = "semantic_vote"
    config_class = MyRunnerConfig
    aggregator_scope = "response"
    required_capabilities = frozenset()
    i18n_key_prefix = "parallelEnsemble.runners.semanticVote"
    ui_schema = {"my_param": {"control": "number_input", "min": 0, "max": 1, "step": 0.05}}

    @classmethod
    def requirements(cls, config): return []
    @classmethod
    def validate_selection(cls, config, model_aliases, registry): return []

    def run(self, question, backends, aggregator, config, trace):
        # ... 调 sentence-bert, 自定义聚合 ...
        yield {"kind": "done", "text": picked, "metadata": {...}}

# 2) framework 发现机制 (v0.2: 仅这一种):
#    a. 放进 api/core/workflow/nodes/parallel_ensemble/runners/ 下,
#       pkgutil.walk_packages 自动 import 触发 @register_runner
#
#    ⚠️ v0.1 / v0.2 早期文档提的 yaml `extra_runner_modules` 字段在 v0.2 不实现;
#    v0.3 评估 import path 安全性后再加。第三方包内的 runner 在 v0.2 下必须 fork。

# 3) 重启 api + 浏览器刷新 → parallel-ensemble 节点 → "协作模式" 下拉自动出现
```

**完全不动**：Dify 节点注册（`BlockEnum` / `BLOCKS` / `NodeComponentMap` 等
9 处）、`node_factory.py`、graphon 事件协议。这是 EP-3 的兑现。

---

## 6. Aggregator SPI（v0.2 修订：typed scopes + AggregationContext）

> v0.1 的 `aggregate(signals: object, config: dict) -> object` 评审正确指出
> 太松——二开者无从知道 `signals` 里有什么。v0.2 拆成两个 typed 基类
> （`ResponseAggregator` / `TokenAggregator`），每条 aggregate 调用都接收
> 一个 `AggregationContext` 携带 weights / capabilities / 时间戳 / trace
> 句柄等元数据。第三方想加 `SemanticAggregator` 这种新 scope, 子类化基础
> `Aggregator` 自定义 signal/result types 即可（runner 与 aggregator 通过
> `scope` 字符串配对）。

### 6.1 通用基类

```python
# api/core/workflow/nodes/parallel_ensemble/spi/aggregator.py

class AggregationContext(BaseModel):
    """运行期上下文, 由 framework 构造塞给 aggregator;
    扩展者只读, 不要 mutate."""
    model_config = ConfigDict(frozen=True)

    backends: list[BackendInfo]              # public 投影, 不含 url/key
    weights: dict[str, float]                # alias → weight (来自 spec.weight)
    capabilities: dict[str, frozenset[Capability]]  # alias → caps
    runner_name: str
    runner_config: dict                      # 已校验过的 runner 配置
    trace: TraceCollector                    # 见 §7, aggregator 也可记 trace
    elapsed_ms_so_far: int                   # 节点开始到现在
    step_index: int | None                   # token-level 时是当前 step, 否则 None


ConfigT = TypeVar("ConfigT", bound=BaseModel)
SignalT = TypeVar("SignalT")
ResultT = TypeVar("ResultT")


class Aggregator(ABC, Generic[ConfigT, SignalT, ResultT]):
    name: ClassVar[str]
    scope: ClassVar[str]                     # "response" | "token" | 自定义
    config_class: ClassVar[type[BaseModel]]
    i18n_key_prefix: ClassVar[str]
    ui_schema: ClassVar[dict]                # 同 §5.1 runner 的 ui_schema 规则

    @abstractmethod
    def aggregate(
        self, signals: SignalT, context: AggregationContext, config: ConfigT
    ) -> ResultT: ...
```

### 6.2 `scope == "response"`：兼容 P1

```python
class ResponseSignal(TypedDict):
    """单个 backend 的完整响应 — 一份给 aggregator."""
    source_id: str                  # alias
    text: str
    finish_reason: str
    elapsed_ms: int
    error: str | None               # 单 backend 失败但 runner 继续时填; runner 决定怎么处理 None text


class ResponseAggregationResult(TypedDict):
    text: str
    metadata: dict                  # aggregator 自定义诊断, 进 outputs.metadata


class ResponseAggregator(
    Aggregator[ConfigT, list[ResponseSignal], ResponseAggregationResult]
):
    scope = "response"
```

P1 已落地的 `MajorityVoteStrategy` / `ConcatStrategy` 平滑迁移到这个基类，
入参 `inputs: list[AggregationInput]` 改为 `list[ResponseSignal]`（多了
`finish_reason` / `elapsed_ms` / `error` 字段，可忽略）；context 新接口对
P1 调用点零影响（默认参数）。

### 6.3 `scope == "token"`：PN.py 玩法

```python
class TokenCandidate(TypedDict):
    token: str
    prob: float                     # adapter 已归一化 (见 §3.2 坑 3)
    logit: float | None             # 仅 LOGITS_RAW capability


class TokenSignals(TypedDict):
    """单步 (single token step) 各 backend 候选."""
    per_model: dict[str, list[TokenCandidate]]    # alias → top-k
    per_model_errors: dict[str, str]              # alias → error msg (空票)


class TokenPick(TypedDict):
    token: str
    score: float
    reasoning: dict                 # 给 trace 用; 例 {"per_token_score": {...}}


class TokenAggregator(
    Aggregator[ConfigT, TokenSignals, TokenPick]
):
    scope = "token"
```

`AggregationContext.step_index` 在 token scope 下从 0 开始递增；
`per_model_errors` 让 aggregator 决定是「跳过空票模型」还是「用上一步的
candidate fallback」——v0.1 把这层信息丢了。

### 6.4 自定义新 scope：`SemanticAggregator` 示例

```python
class SemanticSignal(TypedDict):
    source_id: str
    embedding: list[float]
    text: str

class SemanticPick(TypedDict):
    text: str
    cluster_metadata: dict

class SemanticAggregator(Aggregator[ConfigT, list[SemanticSignal], SemanticPick]):
    scope = "semantic"             # 自定义 scope 字符串
```

配对的 runner 声明 `aggregator_scope = "semantic"`，框架把两边按字符串
配齐，UI 下拉只列同 scope 的 aggregator。

---

## 7. Trace & Diagnostics（v0.2 新章 — 用户原始诉求的一等公民）

> 评审正确指出: 用户的原始诉求是"想轻松获取 logit / 中间数据", 这不是
> 扩展性的副产物而是核心。本节定义诊断数据面: 哪些数据可以采、由谁开关、
> 走什么 schema、怎么避免 `outputs.text` 爆炸。

### 7.1 节点配置的 `diagnostics_config`

```python
class DiagnosticsConfig(BaseModel):
    """挂在 ParallelEnsembleNodeData 上, 一等配置项."""
    model_config = ConfigDict(extra="forbid")

    # 响应级
    include_model_outputs: bool = False     # 每个 backend 的完整文本
    include_response_timings: bool = True   # 每 backend 耗时 (轻量, 默认开)

    # Token 级
    include_token_candidates: bool = False  # 每步 top-k 候选
    include_logits: bool = False            # 原始 logits, 仅 LOGITS_RAW capability
    include_aggregator_reasoning: bool = False  # aggregator 给的 reasoning dict
    max_trace_tokens: int = 1000            # token-step trace 长度上限, 防爆

    # Think 阶段
    include_think_trace: bool = False       # type=think 模型的 think token

    # 错误
    include_per_backend_errors: bool = True # 单 backend 失败但 runner 继续, 详情默认存

    # 存储策略
    storage: Literal["inline", "metadata"] = "metadata"
    # inline:    塞进 outputs.trace, 工作流变量可下游引用 (注意大小)
    # metadata:  塞进 NodeRunResult.metadata, 不进变量池, 在运行历史可查
    # ⚠️ v0.2.2 修订: 不再接受 "artifact"。v0.3 加 "artifact" (附件存储)
    # 时再放进 Literal。当前接受 "artifact" 会被 Pydantic extra rejection
    # 拦下, 不会走 fallback。
```

### 7.2 标准 `EnsembleTrace` schema

```python
class TokenStepTraceEntry(TypedDict):
    step: int
    selected_token: str
    selected_score: float
    elapsed_ms: int
    per_model: dict[str, list[TokenCandidate]]   # 仅 include_token_candidates=True 时填
    per_model_errors: dict[str, str]              # 仅 include_per_backend_errors=True 时填
    aggregator_reasoning: dict | None             # 仅 include_aggregator_reasoning=True 时填


class ResponseTraceEntry(TypedDict):
    source_id: str
    text: str | None                              # 仅 include_model_outputs=True 时填
    finish_reason: str
    tokens_count: int
    elapsed_ms: int                               # 始终填 (轻量)
    error: str | None                             # 仅 include_per_backend_errors=True 时填


class ThinkTraceEntry(TypedDict):
    source_id: str
    think_text: str                               # type=think 模型的思考段
    elapsed_ms: int


class EnsembleTrace(TypedDict):
    trace_version: int                            # schema version, 当前 1
    runner_name: str
    runner_config: dict
    aggregator_name: str
    aggregator_config: dict
    backends: list[BackendInfo]                   # public 投影, 不含 url/key
    diagnostics_config: dict                      # 哪些项被开了, 给后续工具读
    response_trace: list[ResponseTraceEntry]      # 始终至少 elapsed_ms; 长 list 看配置
    token_trace: list[TokenStepTraceEntry]        # 截断到 max_trace_tokens
    think_trace: list[ThinkTraceEntry]            # 仅 enable_think + include_think_trace
    summary: dict                                 # tokens_count / total_elapsed_ms / stopped_by / etc
```

### 7.3 `TraceCollector` —— Runner / Aggregator 的写入接口

Runner 与 aggregator 拿到的不是裸 `EnsembleTrace`，而是 `TraceCollector`
门面——按配置决定调用是 no-op 还是真记录，这样**runner 代码无需自己判断
配置**：

```python
class TraceCollector:
    def __init__(self, config: DiagnosticsConfig, max_token_steps: int): ...

    def record_response(self, entry: ResponseTraceEntry) -> None: ...
    def record_token_step(self, entry: TokenStepTraceEntry) -> None:
        """超过 max_trace_tokens 自动丢弃 (last-N) 并记 truncated=True"""
    def record_think(self, entry: ThinkTraceEntry) -> None: ...
    def record_summary(self, key: str, value: object) -> None: ...

    def finalize(self) -> EnsembleTrace:
        """节点 _run 结束时由 framework 调用, 写入 outputs / metadata
        按 storage 策略."""
```

**Runner 编写示例**（token_step）：

```python
def run(self, question, backends, aggregator, config, trace):
    for step in range(config.max_len):
        per_model_candidates, per_model_errors = self._step_concurrent(...)

        ctx = AggregationContext(..., step_index=step, trace=trace)
        pick = aggregator.aggregate(
            TokenSignals(per_model=per_model_candidates,
                         per_model_errors=per_model_errors),
            ctx, config.aggregator_config,
        )

        # ← runner 永远调 record_*, 是否真存由 trace 内部按 config 决定
        trace.record_token_step({
            "step": step,
            "selected_token": pick["token"],
            "selected_score": pick["score"],
            "elapsed_ms": ...,
            "per_model": per_model_candidates,        # config 关了就被丢
            "per_model_errors": per_model_errors,
            "aggregator_reasoning": pick["reasoning"],
        })
        yield {"kind": "token", "delta": pick["token"]}
```

### 7.4 输出契约

`ParallelEnsembleNode._run` 在 finalize 后:

```python
trace = trace_collector.finalize()
outputs = {
    "text": accumulated_text,
    "tokens_count": trace["summary"]["tokens_count"],
    "elapsed_ms": trace["summary"]["total_elapsed_ms"],
}
metadata: dict = {}

if config.diagnostics.storage == "inline":
    outputs["trace"] = trace                   # 进变量池, 下游节点可引用
elif config.diagnostics.storage == "metadata":
    metadata["ensemble_trace"] = trace         # 仅运行历史可查
# 注: v0.2 schema 只接受 inline / metadata; "artifact" 由 v0.3 加入。

yield StreamCompletedEvent(node_run_result=NodeRunResult(
    status=SUCCEEDED, outputs=outputs, metadata=metadata, ...
))
```

**关键约束**:
- 默认 `storage="metadata"` —— 工作流输出干净, 调试时能在运行历史查
- 用户显式选 `inline` 才进变量池, 防 token 级 1k 步 trace 把变量池撑爆
- `outputs.text` 永远只是最终文本字符串 —— 下游 LLM / End / Answer 节点
  可零修改消费

### 7.5 第三方 runner 加自定义 trace 字段

`TokenStepTraceEntry` 是 `TypedDict`，不锁死字段；扩展 runner 可塞自定义
键（前缀建议 `x_<runner_name>_`），下游消费方能识别即可。框架不验证额外
键，但 `trace_version` 不变 —— 第三方扩展不能改 schema version。

---

## 8. 节点对外 schema

```python
# api/core/workflow/nodes/parallel_ensemble/entities.py
class ParallelEnsembleNodeData(BaseNodeData):
    type: NodeType = "parallel-ensemble"

    question_variable: list[str] = Field(min_length=2)
    model_aliases: list[str] = Field(min_length=1)

    runner_name: str                         # registry key, free string
    runner_config: dict[str, object] = Field(default_factory=dict)

    aggregator_name: str
    aggregator_config: dict[str, object] = Field(default_factory=dict)

    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)  # v0.2 新增

    # ⚠️ 注意: 没有 model_url, 没有 api_key, 没有 backend 类型, 没有"是否拿 logits" 开关
    # 这些都由 alias → registry 决定; runner 决定能力需求; aggregator 决定信号空间;
    # logits/trace 由 diagnostics 控制
```

`extra="forbid"`（DSL 拒绝塞额外字段）+ `runner_name` / `aggregator_name`
未注册时启动期 ValidationError → 用户看到清晰错误。

---

## 9. 启动期校验流水线（Capability + Requirements + Schema）

```python
def validate_node_data(data: ParallelEnsembleNodeData, registry, runner_reg,
                       agg_reg, backend_reg):
    runner_cls = runner_reg.get(data.runner_name)
    agg_cls = agg_reg.get(data.aggregator_name)

    # 1) aggregator scope 对齐 runner
    if agg_cls.scope != runner_cls.aggregator_scope:
        raise ValidationError(
            f"Aggregator '{agg_cls.name}' (scope={agg_cls.scope}) is not "
            f"compatible with runner '{runner_cls.name}' "
            f"(scope={runner_cls.aggregator_scope})"
        )

    # 2) runner_config / aggregator_config 各自 schema 校验 (extra=forbid)
    runner_config = runner_cls.config_class.model_validate(data.runner_config)
    agg_cls.config_class.model_validate(data.aggregator_config)

    # 3) Capability 粗过滤: 每个 alias 必须满足 runner 的 required_capabilities
    for alias in data.model_aliases:
        spec = registry.get(alias)
        backend_cls = backend_reg.get(spec.backend)
        caps = backend_cls.capabilities(spec)
        missing = runner_cls.required_capabilities - caps
        if missing:
            raise ValidationError(
                f"Model '{alias}' (backend={spec.backend}) lacks required "
                f"capabilities for runner '{runner_cls.name}': {sorted(missing)}"
            )

    # 4) Requirements 精校验 (v0.2 新增, 见 §3.4): 按 runner_config 派生具体诉求
    requirements = runner_cls.requirements(runner_config)
    issues: list[ValidationIssue] = []
    for alias in data.model_aliases:
        spec = registry.get(alias)
        backend_cls = backend_reg.get(spec.backend)
        issues.extend(backend_cls.validate_requirements(spec, requirements))

    errors = [i for i in issues if i["severity"] == "error"]
    if errors:
        raise StructuredValidationError(errors)   # 节点标红, panel 显示 i18n 翻译后的 message
```

启动期发现 → DSL 导入失败、画布上节点标红、清晰错误信息。**绝不**让
不兼容的组合活到运行期。

---

## 10. 模块布局（v0.2 缩量：仅 llama_cpp，其余延后 v0.3）

```
api/core/workflow/nodes/parallel_ensemble/
├── __init__.py                 # PARALLEL_ENSEMBLE_NODE_TYPE = "parallel-ensemble"
├── node.py                     # ParallelEnsembleNode
├── entities.py                 # ParallelEnsembleNodeData + DiagnosticsConfig
├── exceptions.py               # CapabilityNotSupported, StructuredValidationError, ...
│
├── spi/                        # ★ 三轴 SPI 接口 (v0.2 冻结)
│   ├── __init__.py
│   ├── capability.py           # Capability enum
│   ├── requirements.py         # Requirement / ValidationIssue (v0.2)
│   ├── backend.py              # ModelBackend ABC + TypedDicts
│   ├── runner.py               # EnsembleRunner ABC + RunnerEvent + ui_schema
│   ├── aggregator.py           # ResponseAggregator / TokenAggregator typed bases
│   └── trace.py                # EnsembleTrace + TraceCollector (v0.2)
│
├── registry/                   # 注册表 (yaml + 三个 SPI 注册器)
│   ├── __init__.py
│   ├── model_registry.py       # 升级 P2.1 LocalModelRegistry, discriminated union (准备多 backend)
│   ├── backend_registry.py     # @register_backend("name")
│   ├── runner_registry.py      # @register_runner("name")
│   └── aggregator_registry.py  # @register_aggregator("name", scope="...")
│
├── backends/                   # ★ v0.2 仅 llama_cpp; vllm/openai/anthropic 延后 v0.3
│   ├── __init__.py
│   └── llama_cpp.py            # LlamaCppBackend (从 P2.1 LlamaCppClient 升级)
│
├── runners/                    # ★ v0.2 两个参考 runner
│   ├── __init__.py
│   ├── response_level.py       # 包装 P1, scope="response"
│   └── token_step.py           # PN.py, scope="token"
│
└── aggregators/
    ├── response/               # scope="response", 平滑迁移 P1
    │   ├── majority_vote.py
    │   └── concat.py
    └── token/                  # scope="token"
        ├── sum_score.py
        └── max_score.py
```

`pkgutil.walk_packages` 在 `_import_node_package` 里递归扫，所有 `@register_*`
装饰器自动生效；P1 `response-aggregator` 节点保留为「响应级专用 fast path」
**不删**——它是 backwards-compat 的着陆点，研究者可以渐进式迁移。

---

## 11. 对现有 TASKS.md 的影响（v0.2 缩量）

> 评审正确指出 v0.1 「+4 天做完 SPI + 3 个新 backend + 动态前端表单 +
> capability 约束 + trace」过度乐观。v0.2 缩量：先把**最小可扩展框架 +
> llama.cpp** 做实，vllm/openai/anthropic 作为 v0.3 adapter，不阻塞核心。

### 11.1 v0.2 范围内的 TASKS.md 重排

| 现 TASKS | v0.2 动作 | 备注 |
|---|---|---|
| ✅ P2.1 ModelSpec + LocalModelRegistry | 升级为 `BaseSpec + LlamaCppSpec`（discriminated union 准备好但仅注册一种），`LocalModelRegistry` → `ModelRegistry` | 现已落地代码不重写, 改名 + 加 `backend: Literal["llama_cpp"]` 字段 |
| **新 P2.1.5** | SPI 接口冻结：`spi/{capability,requirements,backend,runner,aggregator,trace}.py` 五个 ABC + TypedDict + UI 元数据字段 | v0.2 核心；后续所有任务以此为契约 |
| P2.2 LlamaCppClient | 重命名 `LlamaCppBackend(ModelBackend)`；实现 `capabilities()` / `validate_requirements()` / `step_token()` / `generate()` / `generate_stream()` / `apply_template()` | spec 改 `LlamaCppSpec`；ssrf_proxy 注入由框架管 |
| **新 P2.2.4** | `BACKEND_CAPABILITIES.md` 把 §3.2 矩阵 + 三个语义坑钉死，附 fixture 测试 | 评审强调；v0.3 加 backend 时按本文档对齐语义 |
| P2.3 注册表 / backend 单测 | 扩展为：spec 解析 / capability 声明 / requirements 校验路径 / list_aliases 不含 url / SecretStr mask | |
| P2.4 控制台 API | 返回 `BackendInfo`（含 capabilities）；新增 `GET /workspaces/current/runners` 与 `GET /workspaces/current/aggregators` 各返回 `{name, i18n_key_prefix, ui_schema, config_schema, scope or required_caps}` | UI 三轴下拉来源 |
| P2.5 aggregators | 升级为 `ResponseAggregator` / `TokenAggregator` typed 基类；`AggregationContext` 注入；P1 已落地的 majority_vote / concat 平滑迁移到 response scope | P1 调用点零影响 |
| P2.6 TokenVoteEngine | 重命名 `TokenStepRunner(EnsembleRunner)`；算法逻辑同 PN.py；新增 `requirements()` + `TraceCollector` 调用点 | |
| **新 P2.6.5** | `ResponseLevelRunner` 落地（包 P1 response-aggregator 现有逻辑） | v0.2 第二个参考 runner |
| P2.7 engine 单测 | + Capability 粗过滤 + Requirements 精校验路径 + scope 不匹配测试 + Trace 开关行为测试 | |
| P2.8 ParallelEnsembleNode | 字段重组：`runner_name` / `aggregator_name` / `runner_config` / `aggregator_config` / `diagnostics`；`extra="forbid"` 三层 | |
| P2.9 node_factory 注入 | 注入 `model_registry` / `runner_registry` / `aggregator_registry` / `backend_registry` / `executor` | 5 个依赖 |
| P2.10 单测 | + capability/requirements 不匹配 → `StructuredValidationError`；trace storage 两种策略 (inline/metadata)；inline trace 进变量池后下游可读 | |
| P2.11 前端 | runner / aggregator / model 三轴下拉；按 `runner.required_caps` 过滤模型；按 `runner_cls.requirements(config)` 实时调后端 validate；按 `ui_schema` 渲染配置表单（v0.2 控件白名单：number_input / text_input / textarea / switch / select / multi_select / model_alias_select）；`DiagnosticsConfig` 表单 | 大改；i18n 按 `i18n_key_prefix` 注册 |
| P2.12 前端质量门 | tsgo + lint + 三个新单测（runner 下拉 / requirements 校验 mock / trace 开关持久化） | |
| P2.13 联调 workflow | 同 v0.1, 仅 llama_cpp | |
| P2.14 联调 chat | 同 v0.1, 仅 llama_cpp | |
| P2.15 硬化 | 性能 vs PN.py + 异常路径 + SSRF 回归 + Trace 大小 boundary 测试 | |

### 11.2 v0.2 工时估计（修正）

评审给出的更现实区间：

| 包 | 估时 |
|---|---|
| SPI 接口冻结（capability + requirements + typed aggregator + trace） | +1 天 |
| llama_cpp backend 升级到 SPI（实现 6 方法 + capabilities + validate_requirements） | +1 天 |
| 控制台 API 新增 runners / aggregators 路由 + BackendInfo 投影 | +0.5 天 |
| 前端 ui_schema 反射表单 + 三轴联动 + diagnostics 面板 + i18n | +1.5 天 |
| Trace schema + TraceCollector + 两种 storage (inline/metadata) + 单测 | +1 天 |

**v0.2 框架增量 ≈ +5 天**，落在原 Phase 2 11–14 天上 → **总 16–19 天**。

### 11.3 延后 v0.3 的工作

| 包 | 估时 |
|---|---|
| `VllmBackend` adapter（含 logprobs 语义换算） | +2 天 |
| `OpenAICompatBackend` adapter（含 top_logprobs ≤ 20 的 requirements 拒） | +2 天 |
| `AnthropicBackend` adapter（无 logprobs，仅 response_level） | +1 天 |
| 跨 backend logprob 一致性 fixture 单测 | +1 天 |
| Trace `storage="artifact"` 落地（附件存储路径） | +1 天 |

**v0.3 增量 ≈ +4–7 天**（与评审估计对齐）。

### 11.4 路线图对比

| 路线 | v0.1 (评审前) | v0.2 (评审后) |
|---|---|---|
| Phase 2 范围 | 框架 + 4 backend + 4 runner + 动态表单 | 框架 + 1 backend (llama_cpp) + 2 runner + ui_schema 白名单表单 + Trace |
| 工时增量 | +4 天（被评审判为乐观） | +5 天 |
| Phase 3 (v0.3) | 测试 / 文档（保持） | 测试 / 文档 + 3 个新 backend adapter |
| 总 Phase 2+3 | 26 天 | 20–26 天（取决于 v0.3 中实际接几个 backend）|

---

## 12. 显式非目标（v0.2 不做）

### 12.1 v0.2 不做（v0.3 / v0.4 候选）

- **vLLM / OpenAI-compat / Anthropic backend adapter** —— 仅占位 spec 子类，
  实际 `ModelBackend` 实现留 v0.3。SPI 已为它们准备就绪（§4.3 动态 spec 分发支持新 backend 注册不改 framework 代码）。
- **Trace `storage="artifact"`** —— v0.2 仅 inline / metadata 两种；artifact
  写附件存储留 v0.3。
- **Plugin 包 / 钩子机制（entry_points / setuptools plugin）** —— v0.2 第三方
  runner / aggregator / backend 唯一发现路径是「放进 fork 内的 `runners/` /
  `aggregators/<scope>/` / `backends/` 目录，由 `pkgutil.walk_packages` 自动
  扫描」。yaml 顶层 `extra_runner_modules` / `extra_aggregator_modules` /
  `extra_backend_modules` **是 v0.3 才落地的字段**，v0.2 的 schema 不接受，
  与 EXTENSION_GUIDE.md §I 表格一致。
- **跨 backend 的 prompt 翻译层** —— OpenAI 的 system 在 Anthropic 是 top-level
  字段——这种语义差异由各 backend adapter 内部处理。
- **capability 自动探测** —— capability 由 spec 声明，错了由 runner 运行时
  `CapabilityNotSupported` 报错。
- **runner 之间的复合 / 嵌套**（runner-of-runners）—— 复杂玩法用多个
  parallel-ensemble 节点串起来。

### 12.2 永远不做（明确出局）

- **Python 进程内沙箱**（防恶意第三方扩展）—— 见 §4.4，须新建
  `SANDBOX_SPEC.md` 走进程隔离 / wasm / RPC，不在本规范范畴。
- **API 请求级动态 capability 协商** —— 启动期协商已经够用；运行时再让
  runner 改主意会爆炸 trace schema。

---

## 13. 待解决问题（Open）

| # | 问题 | 暂定方向 |
|---|---|---|
| OQ-1 | `ui_schema` 控件白名单 v0.2 是否够用？特别是 `model_alias_select` 这种与 backend 互动的控件 | 先按 §5.1 列的 7 个控件落地；前端遇到不在白名单的字段直接报错；扩展走 v0.3 |
| OQ-2 | `i18n_key_prefix` 缺 key 时 fallback 行为？（第三方 runner 没注册 i18n 就装上的话） | 前端 fallback 显示 raw key + console.warn；CI 加一个 lint 检查所有注册的 runner / aggregator 在两套 i18n 都有 key |
| OQ-3 | `Requirement` 的 `kind` 是封闭枚举还是开放字符串？开放允许第三方 backend 加，但 runner / backend 双向不识别就不工作 | v0.2 封闭白名单 7 种（见 §3.4）；第三方先用 i18n_key + rationale 表达自定义诉求；v0.3 评估开放 |
| OQ-4 | Trace `storage="inline"` 时变量池能不能存大 dict？graphon 变量池当前是否有大小限制 | 落地前要 spike `graph_runtime_state.variable_pool` 行为；如果有 size 限制要在 framework 写入前序列化检查 + 自动降级到 metadata + warning |
| OQ-5 | `api_key_env` 在 Dify multi-tenant 场景如何？目前研究 fork 单租户 OK | v0.2 只支持 process-wide env；multi-tenant 留 v0.4 |
| OQ-6 | OpenAI 的 chat-completions vs completions 端点选哪个？（v0.3 时再决定） | 倾向 chat-completions, `apply_template` 走天真拼接 fallback |
| OQ-7 | vLLM 的 logprobs 单位换算是否要在 framework 强制？还是 trust adapter | trust adapter, 但 `BACKEND_CAPABILITIES.md` 钉语义 + 跨 backend 一致性 fixture 单测 |

---

## 修订历史

### v0.2（2026-04-27, 评审修订）

针对评审意见 6 项问题的修正：

1. **安全边界过强（§4.4）** —— 删除「Python 同进程拦截 runner 反射」的承诺，
   明确威胁模型 = 「DSL/前端用户不可信，第三方 Python 扩展可信」；恶意扩展
   防护需新建 `SANDBOX_SPEC.md`，超出本规范。
2. **节点命名冲突** —— 全文 `multi-model-ensemble` → `parallel-ensemble`
   （沿用 P2.1 已落地常量 `api/core/workflow/nodes/parallel_ensemble/__init__.py:3`），
   不引入两套节点名。EP-3 文字明确说明。
3. **Capability 不够表达约束（§3.4 新章）** —— 增加 `Requirement` /
   `ValidationIssue` 结构化精校验层；runner 暴露 `requirements(config)`，
   backend 暴露 `validate_requirements(spec, requirements)`；Capability
   降级为粗过滤。
4. **UI 元数据被低估（§5.1）** —— Runner / Aggregator SPI 加
   `i18n_key_prefix` + `ui_schema`（控件白名单 7 种），不再奢望「Pydantic
   schema 自动反射 = 可用前端表单」。
5. **Aggregator 接口太松（§6 重写）** —— 拆 `ResponseAggregator` /
   `TokenAggregator` typed 基类，注入 `AggregationContext` 携带 weights /
   capabilities / step_index / trace 句柄；自定义 scope 通过子类化基础
   `Aggregator` 实现。
6. **Trace / diagnostics 缺位（§7 新章）** —— `DiagnosticsConfig` 一等
   配置项；标准 `EnsembleTrace` schema；`TraceCollector` 门面让 runner
   不必判断配置；v0.2 两种 storage 策略（inline / metadata）防变量池爆炸；
   `artifact` 延后 v0.3。

**范围缩量**：v0.1 想一次做 4 个 backend adapter；v0.2 缩到 llama_cpp 一个，
vllm / openai_compat / anthropic 延后 v0.3；工时由 v0.1 的「+4 天」修正为
「+5 天 v0.2 框架，+4–7 天 v0.3 backend pack」。

### v0.2.2（2026-04-27, 第三轮评审修订）

针对评审 10 项问题（编号对应原评审）：

1. **Guide import 路径错** —— 改 `from ..registry import ...` → `from ...registry.aggregator_registry import register_aggregator`（aggregators 在 `aggregators/<scope>/` 子目录下，需 3 个点）；runner 的 `from .registry` → `from ..registry.runner_registry`。Spec §10 模块布局没问题；guide §A/§B/§C/§D 全改。
2. **Runner 用了私有 `b._spec.id`** —— §4.1 `ModelBackend` 加 `id` / `model_name` / `weight` / `instance_capabilities` 公开 `@property`；`run(...)` 的 `backends` 参数从 `list[ModelBackend]` 改为 `dict[str, ModelBackend]`（alias-keyed），二开者直接 `for alias, b in backends.items()`，不需要反射。
3. **draft_verify 死循环** —— guide §C 重写：拒绝 token 时调 aggregator 选 fallback token 推进，加 `consecutive_rejections` 上限，永不卡死。
4. **跨字段校验缺接口** —— §5.1 EnsembleRunner 加 `validate_selection(config, model_aliases, registry)` classmethod；§5.2 四个参考 runner 全部填实现（draft_alias 在 model_aliases、判 ≥2 模型、enable_think 与 type=think 模型一致性等）；§9 校验流水线增加第 5 步。
5. **Spec 示例没同步抽象方法** —— §5.2.1–5.2.4 全部改成可编译版本：`name` / `config_class` / `i18n_key_prefix` / `ui_schema` / `requirements()` / `validate_selection()` / `run(..., trace)` 全有；移除"..."占位符，给出可读最小实现。
6. **发现机制矛盾** —— v0.2 范围内**只支持 fork 内目录扫描**；删除所有 `extra_runner_modules` / `extra_backend_modules` 的 v0.2 承诺；它们统一延后 v0.3。§5.3、§4.3.4、§12.1 一致化；guide §I 表格同步。
7. **Guide 把 LLM judge 列在响应级 aggregator** —— 改：语义投票是 aggregator，judge 是 runner（要调额外模型）；guide §0 表格改正。
8. **Trace `artifact` 状态不一致** —— `DiagnosticsConfig.storage: Literal["inline", "metadata"]`（去掉 artifact）；§7.4 输出契约 + EP-6 + §11.2 工时估计同步；§11.3 v0.3 仍保留 artifact 落地任务。
9. **Guide 零代码示例用 vLLM 不在 v0.2 范围** —— 改用 v0.2 的 llama_cpp 模型 + `include_token_candidates=True`（这个真有用）；`include_logits` 在标准 llama.cpp 下会 null，注解说明；带 LOGITS_RAW 的 vLLM 示例移到 v0.3 章节。
10. **i18n 嵌套 vs 扁平** —— guide 全改扁平 dotted key（`"parallelEnsemble.aggregators.semanticCentroid.name": "..."`），与 `web/i18n/{en-US,zh-Hans}/workflow.json` 现有风格一致。

### v0.2.1（2026-04-27, 二次自查 H1 修订）

二次自查发现 v0.2 §4.3 仍有自相矛盾："discriminated union + 第三方动态注册"
互斥（H1）。修正：

- **§4.3 重写**：抛弃 `Annotated[Union[...], Field(discriminator="backend")]` 静态校验，
  改为 `BackendRegistry.get_spec_class(backend_str).model_validate(entry)`
  动态分发；增加 §4.3.1（spec 子类）/ §4.3.2（BackendRegistry 实现）/
  §4.3.3（ModelRegistry._load 两阶段解析）/ §4.3.4（第三方发现路径）/
  §4.3.5（仅文档性 IDE union）五个子节
- **保证条件**：backend 模块必须在 `ModelRegistry._load()` 之前 import
  完成（v0.2 通过 `pkgutil.walk_packages` 顺序保证；v0.3 加 `extra_backend_modules`
  yaml 字段）
- 影响 P2.1 升级路径：现 `LocalModelRegistry._load()` 改一行（`ModelSpec.model_validate`
  → 反查 `spec_class.model_validate`），新增 `BackendRegistry`；不重写代码

**新增伴生文档**：`docs/ModelNet/EXTENSION_GUIDE.md` —— 二开者 cookbook，
覆盖 5 种典型扩展场景的最小可工作示例（响应/token aggregator、runner、
backend、纯 trace 消费）+ 测试脚手架 + i18n checklist。本规范是设计契约，
该 guide 是操作手册，分工不同。

### v0.1（2026-04-27, 初稿）
- 三轴 SPI（ModelBackend / EnsembleRunner / Aggregator）+ Capability 协商
- 4 个参考 backend adapter（llama_cpp / vllm / openai_compat / anthropic）
- 4 个参考 runner（response_level / token_step / judge / token_estimate）
- Registry 升级为 discriminated union（向后兼容 P2.1 已落地的 LlamaCppSpec）
- 对 TASKS.md 的重排建议（+4 天，总 Phase 2 15–18 天）
- 5 个待解决问题登记

**评审意见**（外部 review，2026-04-27 当日）指出 6 项问题：安全过强 / 节点命名
冲突 / capability 太粗 / UI 元数据缺位 / aggregator 接口太松 / trace 设计缺失。
全部成立，v0.2 修订。
