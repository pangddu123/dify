# ModelNet 二次开发指南（Extension Guide）

> **目标读者**：想在 `parallel-ensemble` 节点上加自定义协作策略 / 聚合算法 /
> 模型后端的研究者或工程师。
>
> **本文与 `EXTENSIBILITY_SPEC.md` 的分工**：spec 是设计契约（接口与边界，
> 1500 行）；本文是操作手册（5 种场景的最小可工作代码）。如果你只是要"加
> 一个聚合策略让节点能用"，**只读本文**就够。
>
> **版本对应**：本指南配合 EXTENSIBILITY_SPEC v0.2.2（含 H1 动态 backend
> 分发 + 评审第三轮 10 项修订：公开 `b.id` / dict-keyed backends /
> `validate_selection` / 完整可编译示例 / 删除 v0.2 `extra_*_modules` 承诺 /
> trace storage 二选一 / 扁平 i18n key）。
>
> ⚠️ **当前状态：未落地 cookbook 草稿**。本文所有 `from ...spi.aggregator`、
> `from ...spi.runner`、`@register_aggregator` / `@register_runner` /
> `@register_backend`、`testing.fakes` 等 import 路径**目前都不存在**——
> 仓库内 `api/core/workflow/nodes/parallel_ensemble/` 现在只有
> `llama_cpp/{registry.py, exceptions.py}`（P2.1 落地物）。`spi/`、
> `registry/`、`aggregators/`、`runners/`、`backends/`、`testing/` 这些
> 目录由计划中的 P2.1.5（SPI 接口冻结）+ P2.5–P2.7（聚合器 / engine）+
> P2.2（backend 客户端）落地后才会出现。
>
> 在那之前**直接 copy 本文示例会全员 `ModuleNotFoundError`**。请把本文当
> "接口冻结后即将可用的二开手册预览"看，不要当"今天能跑的 quickstart"。

---

## 0. 选你的场景

| 我想… | 看哪节 | 难度 | 是否需要重启 api |
|---|---|---|---|
| 加一种**响应级**聚合算法（如语义投票，纯函数式 N→1） | §A | 易（30 行 Python + 2 份 i18n）| ✅ |
| 加一种 **token 级**聚合算法（如带阻尼的概率求和） | §B | 易（同 §A） | ✅ |
| 加一种**协作模式**（如 LLM judge、draft+verify、early-stop voting；需要 orchestrate 多个 backend） | §C | 中（80 行 Python，含 Trace 写入）| ✅ |
| 接入**新模型后端**（vLLM / OpenAI / 自家 ZMQ）| §D | 中（120 行 Python + ssrf_proxy 集成）| ✅ |
| 只想**读 token 候选 / 中间数据**，不写算法 | §E | 零代码（节点 panel 配置开关） | ❌ |
| 想**单测**我的扩展 | §F | — | — |

> **aggregator vs runner 的判别**：纯 N→1 函数（输入 N 份信号 → 输出 1 份结果）
> 是 aggregator；需要**调用额外模型**或**自己 orchestrate 调用顺序**的是 runner。
> LLM judge 要再调一个模型当评委，所以是 runner（§C），不是 aggregator。

---

## 1. 一次性环境设置

### 1.1 目录约定

```
api/core/workflow/nodes/parallel_ensemble/
├── aggregators/
│   ├── response/    ← §A 在这里加文件
│   └── token/       ← §B 在这里加文件
├── runners/         ← §C 在这里加文件
└── backends/        ← §D 在这里加文件
```

`pkgutil.walk_packages` 在 api 启动时递归 import 这些目录下的所有 `.py`，
触发 `@register_*` 装饰器自动登记。**新文件不需要手动 import 任何地方**。

### 1.2 i18n 文件位置

```
web/i18n/en-US/workflow.json    ← 英文 key
web/i18n/zh-Hans/workflow.json  ← 中文 key
```

每个新增的 runner / aggregator / backend 都需要在两个文件里加 key。
key 命名规则见 §G。

### 1.3 重启 + 刷新

```bash
# 后端: 改 Python 文件后
uv run --project api flask run             # 或 docker compose restart api

# 前端: 改 i18n 后
# 浏览器硬刷新 (Cmd+Shift+R / Ctrl+F5)；i18n 走 Next.js 静态资源, 不需重启 web
```

---

## 2. 五个场景的最小可工作示例

### §A. 加响应级聚合策略

**场景**：用 sentence-bert 算 N 个模型回答的嵌入，挑一个离 centroid 最近
的回答（"语义中心投票"）。

**步骤 1：写 Python（一个文件）**

```python
# api/core/workflow/nodes/parallel_ensemble/aggregators/response/semantic_centroid.py
from pydantic import BaseModel, ConfigDict, Field

from ...spi.aggregator import (
    AggregationContext, ResponseAggregator, ResponseSignal,
    ResponseAggregationResult,
)
from ...registry.aggregator_registry import register_aggregator


class SemanticCentroidConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2",
                                  min_length=1)


@register_aggregator("semantic_centroid", scope="response")
class SemanticCentroidAggregator(
    ResponseAggregator[SemanticCentroidConfig]
):
    name = "semantic_centroid"
    config_class = SemanticCentroidConfig
    i18n_key_prefix = "parallelEnsemble.aggregators.semanticCentroid"
    ui_schema = {
        "embedding_model": {"control": "text_input"},
    }

    def aggregate(
        self,
        signals: list[ResponseSignal],
        context: AggregationContext,
        config: SemanticCentroidConfig,
    ) -> ResponseAggregationResult:
        from sentence_transformers import SentenceTransformer  # 懒 import
        import numpy as np

        model = SentenceTransformer(config.embedding_model)
        valid = [s for s in signals if s["text"]]
        if not valid:
            return {"text": "", "metadata": {"reason": "all empty"}}

        vecs = model.encode([s["text"] for s in valid], normalize_embeddings=True)
        centroid = vecs.mean(axis=0)
        sims = vecs @ centroid
        winner_idx = int(np.argmax(sims))
        winner = valid[winner_idx]

        # 把 trace 喂给框架, 是否真存看 diagnostics_config
        context.trace.record_summary(
            "semantic_centroid_similarities",
            {valid[i]["source_id"]: float(sims[i]) for i in range(len(valid))},
        )

        return {
            "text": winner["text"],
            "metadata": {
                "winner_source_id": winner["source_id"],
                "winner_similarity": float(sims[winner_idx]),
                "embedding_model": config.embedding_model,
            },
        }
```

**步骤 2：i18n 两份（扁平 dotted key，与 `web/i18n/{en-US,zh-Hans}/workflow.json` 现有风格一致）**

```json
// web/i18n/en-US/workflow.json 顶层加 4 条
{
  "parallelEnsemble.aggregators.semanticCentroid.name": "Semantic Centroid",
  "parallelEnsemble.aggregators.semanticCentroid.description": "Pick the response closest to the embedding centroid",
  "parallelEnsemble.aggregators.semanticCentroid.fields.embeddingModel.label": "Embedding Model",
  "parallelEnsemble.aggregators.semanticCentroid.fields.embeddingModel.tooltip": "HuggingFace model id for sentence embeddings"
}
```

```json
// web/i18n/zh-Hans/workflow.json 顶层加对称的 4 条
{
  "parallelEnsemble.aggregators.semanticCentroid.name": "语义中心投票",
  "parallelEnsemble.aggregators.semanticCentroid.description": "选出与所有响应嵌入中心距离最近的那条",
  "parallelEnsemble.aggregators.semanticCentroid.fields.embeddingModel.label": "嵌入模型",
  "parallelEnsemble.aggregators.semanticCentroid.fields.embeddingModel.tooltip": "用于生成句嵌入的 HuggingFace 模型 id"
}
```

**步骤 3：重启 + 刷新 → 验证**

panel 选 runner = `response_level`，aggregator 下拉里应出现 "Semantic Centroid"。
选中后渲染 `embedding_model` 文本框。

**完成。** 整个改动：1 个 Python 文件 + 2 份 i18n key。**不动**任何 Dify
节点注册（`BlockEnum` / `NodeComponentMap` / `node_factory.py` 等）。

---

### §B. 加 token 级聚合策略

**场景**：在 PN.py 的 `sum_score` 基础上加阻尼：每个模型对 vote 的贡献按
`score_so_far` 衰减（防一个模型连串 token 高分一直主导）。

```python
# api/core/workflow/nodes/parallel_ensemble/aggregators/token/damped_sum.py
from pydantic import BaseModel, ConfigDict, Field

from ...spi.aggregator import (
    AggregationContext, TokenAggregator, TokenSignals, TokenPick,
)
from ...registry.aggregator_registry import register_aggregator


class DampedSumConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    damping_factor: float = Field(default=0.9, gt=0, le=1.0)


@register_aggregator("damped_sum", scope="token")
class DampedSumAggregator(TokenAggregator[DampedSumConfig]):
    name = "damped_sum"
    config_class = DampedSumConfig
    i18n_key_prefix = "parallelEnsemble.aggregators.dampedSum"
    ui_schema = {
        "damping_factor": {"control": "number_input", "min": 0.01, "max": 1.0, "step": 0.05},
    }

    def __init__(self) -> None:
        self._cumulative_score: dict[str, float] = {}

    def aggregate(
        self, signals: TokenSignals, context: AggregationContext, config: DampedSumConfig,
    ) -> TokenPick:
        scores: dict[str, float] = {}
        per_token_contrib: dict[str, dict[str, float]] = {}

        for alias, candidates in signals["per_model"].items():
            damping = config.damping_factor ** self._cumulative_score.get(alias, 0)
            weight = context.weights.get(alias, 1.0) * damping
            per_token_contrib[alias] = {}
            for c in candidates:
                contrib = c["prob"] * weight
                scores[c["token"]] = scores.get(c["token"], 0.0) + contrib
                per_token_contrib[alias][c["token"]] = contrib

        if not scores:
            return {"token": "<end>", "score": 1.0, "reasoning": {"empty": True}}

        best = max(scores.values())
        winner = sorted([t for t, s in scores.items() if s == best])[0]
        for alias in signals["per_model"]:
            self._cumulative_score[alias] = self._cumulative_score.get(alias, 0) + (
                per_token_contrib[alias].get(winner, 0)
            )

        return {
            "token": winner, "score": best,
            "reasoning": {"per_token_contrib": per_token_contrib,
                          "cumulative": dict(self._cumulative_score)},
        }
```

i18n 同 §A 模式。aggregator 下拉只会在用户选 runner = `token_step` 时出现
（按 scope 过滤）。

---

### §C. 加协作模式（runner）

**场景**：「draft+verify」模式——一个轻量 backend 一次出 N 个 token 草稿，
其余 backend 并发 verify。**草稿被否时不死循环**：调 token aggregator 选一个
fallback token 推进，连续 N 次仍无共识则提前终止。

```python
# api/core/workflow/nodes/parallel_ensemble/runners/draft_verify.py
from collections.abc import Iterator
from pydantic import BaseModel, ConfigDict, Field

from ..spi.capability import Capability
from ..spi.requirements import Requirement, ValidationIssue
from ..spi.runner import EnsembleRunner, RunnerEvent
from ..spi.trace import TraceCollector
from ..spi.backend import ModelBackend
from ..spi.aggregator import Aggregator
from ..registry.runner_registry import register_runner


class DraftVerifyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_alias: str = Field(min_length=1)
    draft_chunk_size: int = Field(default=4, gt=0, le=32)
    verify_quorum: int = Field(default=2, gt=0)
    max_len: int = Field(default=500, gt=0)
    max_consecutive_rejections: int = Field(default=8, gt=0)


@register_runner("draft_verify")
class DraftVerifyRunner(EnsembleRunner[DraftVerifyConfig]):
    name = "draft_verify"
    config_class = DraftVerifyConfig
    aggregator_scope = "token"           # 复用 token aggregator 做 fallback pick
    required_capabilities = frozenset({Capability.TOKEN_STEP, Capability.TOP_PROBS})
    i18n_key_prefix = "parallelEnsemble.runners.draftVerify"
    ui_schema = {
        "draft_alias": {"control": "model_alias_select"},
        "draft_chunk_size": {"control": "number_input", "min": 1, "max": 32, "step": 1},
        "verify_quorum": {"control": "number_input", "min": 1, "step": 1},
        "max_len": {"control": "number_input", "min": 1, "step": 1},
        "max_consecutive_rejections": {"control": "number_input", "min": 1, "step": 1},
    }

    @classmethod
    def requirements(cls, config: DraftVerifyConfig) -> list[Requirement]:
        return [
            {"kind": "needs_logprobs", "value": True,
             "rationale": "verify step needs candidate probabilities"},
        ]

    @classmethod
    def validate_selection(cls, config, model_aliases, registry) -> list[ValidationIssue]:
        # 跨字段校验: requirements() 不能拿 model_aliases, 这里能拿
        issues: list[ValidationIssue] = []
        if config.draft_alias not in model_aliases:
            issues.append({
                "severity": "error",
                "requirement": {"kind": "model_allowlist", "value": [config.draft_alias],
                                 "rationale": "draft_alias must be in selected models"},
                "message": f"draft_alias '{config.draft_alias}' not in model_aliases {model_aliases}",
                "i18n_key": "parallelEnsemble.runners.draftVerify.errors.draftAliasNotSelected",
            })
        verifier_count = len(model_aliases) - 1
        if verifier_count < 1:
            issues.append({
                "severity": "error",
                "requirement": {"kind": "min_top_k", "value": 0,
                                 "rationale": "draft_verify needs ≥ 1 verifier (so ≥ 2 models total)"},
                "message": "draft_verify requires at least 2 model aliases (1 draft + 1 verifier)",
                "i18n_key": "parallelEnsemble.runners.draftVerify.errors.tooFewModels",
            })
        elif config.verify_quorum > verifier_count:
            issues.append({
                "severity": "error",
                "requirement": {"kind": "min_top_k", "value": config.verify_quorum,
                                 "rationale": "verify_quorum cannot exceed verifier count"},
                "message": f"verify_quorum={config.verify_quorum} exceeds verifier count {verifier_count}",
                "i18n_key": "parallelEnsemble.runners.draftVerify.errors.quorumTooHigh",
            })
        return issues

    def run(
        self,
        question: str,
        backends: dict[str, ModelBackend],   # alias → backend (v0.2.2: dict 而非 list)
        aggregator: Aggregator,              # token-scope, 用于 fallback
        config: DraftVerifyConfig,
        trace: TraceCollector,
    ) -> Iterator[RunnerEvent]:
        draft = backends[config.draft_alias]
        verifier_aliases = [a for a in backends if a != config.draft_alias]

        prompt = draft.apply_template([{"role": "user", "content": question}])

        accumulated = ""
        step = 0
        consecutive_rejections = 0
        stopped_by = "max_len"

        while step < config.max_len:
            # 1. draft 出一段
            draft_result = draft.generate(
                prompt + accumulated, {"max_tokens": config.draft_chunk_size}
            )
            draft_tokens = draft_result["text"]
            if not draft_tokens:
                stopped_by = "draft_eos"
                break

            # 2. 逐 token verify
            for ch in draft_tokens:
                if step >= config.max_len:
                    break

                verify_signals = {
                    a: backends[a].step_token(prompt + accumulated, top_k=10)
                    for a in verifier_aliases
                }
                supporters = sum(
                    1 for cands in verify_signals.values()
                    if any(c["token"] == ch and c["prob"] > 0.05 for c in cands)
                )
                accepted = supporters >= config.verify_quorum

                if accepted:
                    chosen, reasoning = ch, {"path": "draft_accepted",
                                              "supporters": supporters,
                                              "quorum": config.verify_quorum}
                    consecutive_rejections = 0
                else:
                    # ⚠️ 不死循环: 拒绝时让 token aggregator 从 verify_signals
                    # 选一个 fallback token, 强制推进一步
                    from ..spi.aggregator import AggregationContext
                    pick = aggregator.aggregate(
                        {"per_model": verify_signals, "per_model_errors": {}},
                        AggregationContext(
                            backends=[],   # 简化: 实际由框架填; 这里只示 runner 怎么调
                            weights={a: backends[a].weight for a in verifier_aliases},
                            capabilities={a: backends[a].instance_capabilities
                                          for a in verifier_aliases},
                            runner_name=self.name, runner_config=config.model_dump(),
                            trace=trace, elapsed_ms_so_far=0, step_index=step,
                        ),
                        {},
                    )
                    chosen = pick["token"]
                    reasoning = {"path": "draft_rejected_fallback",
                                  "draft_token": ch, "supporters": supporters,
                                  "fallback_score": pick["score"]}
                    consecutive_rejections += 1

                trace.record_token_step({
                    "step": step,
                    "selected_token": chosen,
                    "selected_score": (supporters / max(1, len(verifier_aliases))
                                       if accepted else pick["score"]),
                    "elapsed_ms": 0,
                    "per_model": verify_signals,
                    "per_model_errors": {},
                    "aggregator_reasoning": reasoning,
                })

                accumulated += chosen
                yield {"kind": "token", "delta": chosen}
                step += 1

                if consecutive_rejections >= config.max_consecutive_rejections:
                    stopped_by = "no_consensus"
                    break

                if not accepted:
                    # 草稿被否, 退出本 chunk 让 draft 用 fallback 后的 prompt 重出
                    break

            if consecutive_rejections >= config.max_consecutive_rejections:
                break

        trace.record_summary("stopped_by", stopped_by)
        yield {"kind": "done", "text": accumulated,
               "metadata": {"steps": step, "stopped_by": stopped_by}}
```

**关键点**：
- `backends: dict[str, ModelBackend]` —— alias-keyed，二开者直接 `backends["draft_alias"]`，不读私有 `_spec`
- `b.id` / `b.weight` / `b.instance_capabilities` —— 公开 `@property`（spec §4.1 v0.2.2 加）
- `validate_selection` —— DSL 导入时校验 `draft_alias ∈ model_aliases`、`verify_quorum ≤ 验证者数`、`总数 ≥ 2`；这些约束 `requirements(config)` 拿不到 `model_aliases` 表达不了
- **死循环防护**：拒绝时调 `aggregator.aggregate(...)` 选 fallback token 强制推进；`max_consecutive_rejections` 兜底（达到则 `stopped_by="no_consensus"` 退出）
- `trace.record_token_step(...)` —— 永远调，是否真存看 `DiagnosticsConfig`；`reasoning.path` 区分 `draft_accepted` / `draft_rejected_fallback`

i18n（扁平 key，最小集）：

```json
// en-US/workflow.json
{
  "parallelEnsemble.runners.draftVerify.name": "Draft + Verify",
  "parallelEnsemble.runners.draftVerify.description": "Draft model proposes tokens; verifiers vote",
  "parallelEnsemble.runners.draftVerify.fields.draftAlias.label": "Draft Model",
  "parallelEnsemble.runners.draftVerify.fields.draftChunkSize.label": "Draft Chunk Size",
  "parallelEnsemble.runners.draftVerify.fields.verifyQuorum.label": "Verify Quorum",
  "parallelEnsemble.runners.draftVerify.fields.maxLen.label": "Max Tokens",
  "parallelEnsemble.runners.draftVerify.fields.maxConsecutiveRejections.label": "Max Consecutive Rejections",
  "parallelEnsemble.runners.draftVerify.errors.draftAliasNotSelected": "Draft model must be one of the selected models",
  "parallelEnsemble.runners.draftVerify.errors.tooFewModels": "Need at least 2 models (1 draft + 1 verifier)",
  "parallelEnsemble.runners.draftVerify.errors.quorumTooHigh": "Verify quorum cannot exceed verifier count"
}
```

---

### §D. 加模型后端（v0.2.1 H1 修订后真的可走）

> §D 的 import 路径全部正确（backends 在 `backends/<name>.py`，到 `spi/` 是
> `..spi.*`，到 `registry/` 是 `..registry.*`），不需要改。下面只更新 i18n
> 与"第三方发现路径"的措辞与 spec 一致。

**场景**：接入自家用 ZMQ 协议的 inference server。

```python
# api/core/workflow/nodes/parallel_ensemble/backends/my_zmq.py
import os
from typing import ClassVar, Literal
from pydantic import Field

from ..spi.backend import (
    BaseSpec, ModelBackend, GenerationParams, GenerationResult,
    StreamChunk, TokenCandidate, ChatMessage,
)
from ..spi.capability import Capability
from ..spi.requirements import Requirement, ValidationIssue
from ..registry.backend_registry import register_backend


class MyZmqSpec(BaseSpec):
    backend: Literal["my_zmq"]
    zmq_endpoint: str = Field(min_length=1, pattern=r"^tcp://")
    auth_token_env: str | None = None


@register_backend("my_zmq")
class MyZmqBackend(ModelBackend):
    spec_class: ClassVar[type[BaseSpec]] = MyZmqSpec

    def __init__(self, spec: MyZmqSpec, http: object) -> None:
        super().__init__(spec, http)
        # http 不用; ZMQ 走自家 socket
        import zmq
        self._ctx = zmq.Context.instance()
        self._sock = self._ctx.socket(zmq.REQ)
        self._sock.connect(spec.zmq_endpoint)
        self._token = os.environ[spec.auth_token_env] if spec.auth_token_env else None
        self._timeout_s = spec.request_timeout_ms / 1000

    @classmethod
    def capabilities(cls, spec: MyZmqSpec) -> frozenset[Capability]:
        # 假设我们的 ZMQ server 支持 streaming + token-step + top-probs
        return frozenset({
            Capability.STREAMING, Capability.TOKEN_STEP, Capability.TOP_PROBS,
        })

    @classmethod
    def validate_requirements(
        cls, spec: MyZmqSpec, requirements: list[Requirement],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        for req in requirements:
            if req["kind"] == "min_top_k" and req["value"] > 64:
                issues.append({
                    "severity": "error", "requirement": req,
                    "message": f"my_zmq supports top_k up to 64, got {req['value']}",
                    "i18n_key": None,
                })
        return issues

    def generate(self, prompt: str, params: GenerationParams) -> GenerationResult:
        resp = self._call("generate", {"prompt": prompt, **params})
        return {"text": resp["text"], "finish_reason": resp.get("finish_reason", "stop"),
                "metadata": resp.get("meta", {})}

    def step_token(self, prompt: str, top_k: int) -> list[TokenCandidate]:
        resp = self._call("step_token", {"prompt": prompt, "top_k": top_k})
        return [{"token": c["token"], "prob": c["prob"], "logit": None}
                for c in resp["candidates"]]

    def apply_template(self, messages: list[ChatMessage]) -> str:
        # 我们的 server 不持有模板, 用 base 类的天真拼接 fallback
        return super().apply_template(messages)

    def _call(self, op: str, body: dict) -> dict:
        import json
        msg = {"op": op, "body": body, "auth": self._token}
        self._sock.send_json(msg)
        if not self._sock.poll(int(self._timeout_s * 1000)):
            raise TimeoutError(f"my_zmq {op} timeout after {self._timeout_s}s")
        return self._sock.recv_json()
```

**yaml 注册**：

```yaml
# api/configs/model_net.yaml
models:
  - id: my-internal-7b
    backend: my_zmq                     # 新 backend 字符串, 框架按它反查 spec_class
    model_name: my-internal-7b-v3
    zmq_endpoint: tcp://10.0.0.42:5555
    auth_token_env: MY_ZMQ_TOKEN
    weight: 1.5
    request_timeout_ms: 60000
```

**重要**：要保证 `backends/my_zmq.py` 在 `ModelRegistry._load()` 之前被 import。
v0.2 走 `pkgutil.walk_packages` 扫描 `backends/` 目录就自动满足。**v0.2 的
唯一发现路径就是这条**——第三方包（不在 fork 内的）必须先把代码放进 fork。
yaml 顶部的 `extra_backend_modules` 字段在 v0.2 不存在，v0.3 才落地。

i18n 给 backend 用得少（用户主要看 `model_name`），但若想在前端显示 backend
类型 tooltip，可以加（扁平 key）：

```json
{
  "parallelEnsemble.backends.myZmq.label": "My ZMQ",
  "parallelEnsemble.backends.myZmq.description": "Internal ZMQ inference server"
}
```

---

### §E. 仅消费 trace 数据（零 Python 代码）

**场景**：研究者想分析每步 token 的候选概率分布，不写算法、不改框架。

#### v0.2 可走的最小例子（仅 llama_cpp）

**步骤 1**：节点 panel 配置

```yaml
# DSL 片段 — v0.2 可执行 (仅 llama_cpp backend)
- id: parallel_ensemble_1
  type: parallel-ensemble
  data:
    runner_name: token_step
    runner_config: { top_k: 10, max_len: 200, enable_think: false }
    aggregator_name: sum_score
    model_aliases: [qwen3-4b-local, llama-31-8b-local]   # 都是 llama_cpp
    diagnostics:
      include_token_candidates: true                      # ★ 主用这个: 候选 + post-sampling prob
      include_logits: false                               # llama_cpp 标准版无 raw logits
      include_aggregator_reasoning: true
      max_trace_tokens: 200
      storage: inline                                     # 进变量池, 下游能引用
      # ⚠️ v0.2 只接受 "inline" | "metadata"; "artifact" 是 v0.3
```

**步骤 2**：下游 Code 节点直接读

```python
# Code 节点 inputs:
#   trace = {{parallel_ensemble_1.trace}}

def main(trace: dict) -> dict:
    rows = []
    for step in trace["token_trace"]:
        for alias, candidates in step["per_model"].items():
            for c in candidates:
                rows.append({
                    "step": step["step"],
                    "selected": step["selected_token"],
                    "alias": alias,
                    "token": c["token"],
                    "prob": c["prob"],
                    "logit": c["logit"],          # 标准 llama_cpp 下为 null
                })
    return {"rows": rows, "row_count": len(rows)}
```

**步骤 3**：把 Code 节点输出接 End / Answer / 写文件节点。

**注意**：
- `storage="inline"`：trace 进变量池；token 级 200 步 × 2 模型 × 10 候选 ≈
  200 KB JSON。**超过 1k 步建议改 `storage="metadata"`**（仅运行历史可查，
  下游 Code 节点拿不到）。
- `logit` 字段仅在 backend 声明 `LOGITS_RAW` capability 时非 null。**v0.2
  内置的 llama_cpp adapter 不包**（除非你跑的是改过 `/completion` 暴露
  raw logits 的 fork）。`prob` 是 post-sampling probability（top-k 内归一），
  对绝大多数研究够用。

#### v0.3 才能跑的扩展例子（vLLM + 真 logits）

```yaml
# ⚠️ 需要 v0.3: VllmBackend adapter 落地后才有效
- id: parallel_ensemble_1
  type: parallel-ensemble
  data:
    runner_name: token_step
    runner_config: { top_k: 10 }
    aggregator_name: sum_score
    model_aliases: [qwen3-4b-vllm, llama-31-8b-vllm]    # 需 vllm backend
    diagnostics:
      include_logits: true                               # vLLM 的 LOGITS_RAW
      storage: inline
```

---

### §F. 单测脚手架

**场景**：你写完 §A 的 `SemanticCentroidAggregator`，想本地单测，不想起真
backend。

```python
# api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/test_my_aggregator.py
from core.workflow.nodes.parallel_ensemble.aggregators.response.semantic_centroid import (
    SemanticCentroidAggregator, SemanticCentroidConfig,
)
from core.workflow.nodes.parallel_ensemble.testing.fakes import (
    FakeAggregationContext, FakeTraceCollector,
)


def test_semantic_centroid_picks_closest_to_centroid():
    agg = SemanticCentroidAggregator()
    config = SemanticCentroidConfig()
    signals = [
        {"source_id": "A", "text": "Paris is the capital of France",
         "finish_reason": "stop", "elapsed_ms": 100, "error": None},
        {"source_id": "B", "text": "Paris, capital of France",
         "finish_reason": "stop", "elapsed_ms": 110, "error": None},
        {"source_id": "C", "text": "I like cats",   # 离 centroid 远
         "finish_reason": "stop", "elapsed_ms": 90, "error": None},
    ]
    ctx = FakeAggregationContext(
        weights={"A": 1.0, "B": 1.0, "C": 1.0},
        trace=FakeTraceCollector(),
    )
    result = agg.aggregate(signals, ctx, config)
    assert result["text"] in {signals[0]["text"], signals[1]["text"]}
    assert result["metadata"]["winner_source_id"] in {"A", "B"}


def test_semantic_centroid_handles_all_empty():
    agg = SemanticCentroidAggregator()
    signals = [
        {"source_id": "A", "text": "", "finish_reason": "stop",
         "elapsed_ms": 0, "error": None},
    ]
    result = agg.aggregate(signals, FakeAggregationContext(), SemanticCentroidConfig())
    assert result["text"] == ""
    assert "reason" in result["metadata"]
```

`testing.fakes` 提供（v0.2 落地）：

| Fake | 用途 |
|---|---|
| `FakeBackend(capabilities, scripted_responses)` | 给 runner 单测；`generate / step_token / generate_stream` 走脚本 |
| `FakeAggregationContext(weights, trace, ...)` | 给 aggregator 单测，所有字段有合理 default |
| `FakeTraceCollector(record_all=True)` | 收集所有 `record_*` 调用，单测断言 |
| `FakeAggregator(scripted_picks)` | 给 runner 单测；按调用次数返回脚本 pick |

跑：
```bash
uv run --project api pytest api/tests/unit_tests/core/workflow/nodes/parallel_ensemble/ -v -o addopts=""
```

---

## §G. i18n key 命名规则与 checklist

每个新增类型必须在 **en-US + zh-Hans 两套** workflow.json 都补 key。

**命名模板**：

| 类型 | i18n_key_prefix | 必备子 key |
|---|---|---|
| Aggregator | `parallelEnsemble.aggregators.<camelName>` | `name`, `description`, `fields.<fieldCamel>.{label,tooltip}` |
| Runner | `parallelEnsemble.runners.<camelName>` | 同上 |
| Backend | `parallelEnsemble.backends.<camelName>` | `label`, `description`（可选） |

**JSON 文件格式**：`web/i18n/{en-US,zh-Hans}/workflow.json` 是**扁平 dotted-key**
对象（与 `blocks.agent`, `nodes.responseAggregator.*` 同风格），不是嵌套对象。
所有 key 在 JSON 顶层并列：

```json
{
  "blocks.agent": "Agent",
  "parallelEnsemble.aggregators.semanticCentroid.name": "Semantic Centroid",
  "parallelEnsemble.aggregators.semanticCentroid.fields.embeddingModel.label": "Embedding Model",
  "parallelEnsemble.runners.draftVerify.name": "Draft + Verify",
  ...
}
```

⚠️ **不要写嵌套对象**：`{"parallelEnsemble": {"aggregators": {...}}}` 会被
Dify 前端的扁平 lookup 完全忽略。

**Checklist**（提交前自查）：

```
[ ] Python 类的 i18n_key_prefix 与 i18n JSON 顶层 key 前缀完全一致
[ ] config_class 每个字段都有对应 <prefix>.fields.<fieldName>.label 顶层 key
[ ] 支持 tooltip 的控件 (number_input / text_input / select / switch / textarea / multi_select)
   都补了 .fields.<fieldName>.tooltip
[ ] en-US + zh-Hans 两套 key 集**完全相同** (CI lint 会跑这条; 缺则前端 fallback 原 key)
[ ] camelName 与 register 名的 snake_case ↔ camelCase 转换一致
    semantic_centroid → semanticCentroid
    draft_verify     → draftVerify
[ ] 所有新增 key 在 JSON **顶层**, 不要写成嵌套对象
[ ] JSON 解析通过: python3 -c "import json; json.load(open('web/i18n/en-US/workflow.json'))"
```

---

## §H. 常见坑

| 现象 | 原因 | 修法 |
|---|---|---|
| 重启 api 后下拉里**没有**我的 aggregator/runner | 文件不在 fork 目录下（必须在 `aggregators/<scope>/`、`runners/`、`backends/`）；或忘了 `@register_*` 装饰器；或 i18n key 不匹配 → 前端按 fallback 隐藏 | 检查路径 / 装饰器；浏览器 console 看 i18n warning |
| `ImportError: cannot import name 'register_aggregator' from '...registry'` | aggregator 在 `aggregators/<scope>/<file>.py`，需 3 个点：`from ...registry.aggregator_registry import register_aggregator`（不是 `..registry`） | 改 import 路径；runner 在 `runners/<file>.py` 用 2 个点 `from ..registry.runner_registry import register_runner` |
| `RegistryFileError: backend 'xxx' is not registered` | backend 模块没在 `ModelRegistry._load()` 之前 import | 文件放进 `backends/` 目录（v0.2 唯一发现路径）；v0.3 才加 `extra_backend_modules` yaml 字段 |
| `ValidationError: storage Input should be 'inline' or 'metadata'` | DSL 写了 `storage: artifact` | v0.2 schema 只接受 `inline` / `metadata`；`artifact` 是 v0.3 |
| 前端 panel 字段名显示原始 key `parallelEnsemble.aggregators.xxx.fields.yyy.label` | i18n key 缺失或写成嵌套对象 | 补扁平顶层 key（en-US + zh-Hans 两份）；不要写嵌套对象 |
| `StructuredValidationError: model 'gpt-4o' lacks needs_logprobs` | runner 声明的 capability/requirements backend 不满足 | UI 应该在选 runner 后已经把 gpt-4o 灰掉；走 DSL 直接导入会被这里拦下；改 runner_config 或换 backend |
| `StructuredValidationError: draft_alias 'X' not in model_aliases` | runner `validate_selection()` 跨字段校验失败 | 把 `X` 加进 `model_aliases`，或换 `draft_alias` 为已选模型 |
| Runner `run()` 里 `b._spec.id` 访问私有 | 用了 v0.1 接口；v0.2.2 改成 `dict[str, ModelBackend]` | `for alias, b in backends.items()`；用 `b.id` / `b.weight` / `b.instance_capabilities` 公开属性 |
| `outputs.trace` 在下游 Code 节点拿不到 | `diagnostics.storage` 是 `metadata`（默认），不进变量池 | 改成 `inline` |
| 改了 i18n 但浏览器不更新 | Next.js i18n 资源 cache | 浏览器硬刷新（Cmd+Shift+R）或 `pnpm dev` 重启 |
| 加 backend 后 yaml 报 `extra_forbidden` | spec 子类没把字段加进去 | 子类 `MyXxxSpec` 必须列出所有 yaml 里出现的字段；`extra="forbid"` 在 BaseSpec 上是硬约束（SSRF 防护，不能关）|
| 测试时 `from ...spi.aggregator import` 报 ImportError | SPI 还没落地（P2.1.5 未完成）| 等待 SPI 接口冻结；本指南目前是预览，不是 quickstart |
| 单测 `from api.core...` 报 `No module named 'api'` | 本仓 pytest 工作目录是 `api/`，import root 是 `core`，不是 `api.core` | 改为 `from core.workflow.nodes.parallel_ensemble...`；与现有 `tests/unit_tests/core/workflow/nodes/response_aggregator/test_*.py` 一致 |

---

## §I. 我的扩展什么时候应该升级到 v0.3？

| 你的扩展 | v0.2.2 内可工作 | 需要 v0.3 才能工作 |
|---|---|---|
| 响应级 / token 级 aggregator | ✅ | — |
| Runner（任何 scope） | ✅ | — |
| llama.cpp 兼容的 backend | ✅ | — |
| vLLM / OpenAI / Anthropic backend | ⚠️ 要自己写 adapter（参考 §D） | ✅ 框架会自带 |
| 第三方包内 runner / aggregator / backend（不在 fork 目录里） | ❌ 必须 fork | ✅ `extra_*_modules` yaml 字段 |
| 自定义 trace storage（写到 S3 / DB） | ❌ schema 只接受 `inline` / `metadata` | ✅ `storage="artifact"` |
| 自定义 capability（如 `MULTIMODAL_VISION`） | ❌（capability 是封闭枚举） | ❓ 看 v0.3 是否开放 |

**v0.2 范围内的发现路径只有一种**：`pkgutil.walk_packages` 自动 import
`aggregators/<scope>/`、`runners/`、`backends/` 三个目录下的所有 `.py`。
不在 fork 内的代码 v0.2 跑不起来——这是有意的取舍（v0.3 评估 import path
安全后再开 `extra_*_modules`）。

---

## §J. 提交清单（PR 前自查）

```
[ ] 单测覆盖核心路径（happy path + 至少 1 个边界 / 1 个失败）
[ ] i18n en-US + zh-Hans 两套 key 完整
[ ] `pnpm type-check:tsgo` + `pnpm lint:fix` 绿
[ ] 没改任何 Dify 节点注册点（BlockEnum / NodeComponentMap / node_factory.py 等）
   —— 改了说明你做的不是"扩展", 而是"破规", 走单独 PR 讨论
[ ] config_class 全字段有 default 或文档说明必填
[ ] 如果是 backend: capabilities + validate_requirements 都实现; 没实现的方法继承 base 自动报 CapabilityNotSupported
[ ] 如果是 runner: 在 trace.record_* 写诊断, 不要自己缓存 print
[ ] 如果是 aggregator: 用 context.trace.record_summary 记关键中间值
[ ] 重启 api + 浏览器刷新, 在 panel 实际拖一遍, 跑通最小图
```

---

## 参考

- 设计契约：`docs/ModelNet/EXTENSIBILITY_SPEC.md`（v0.2.2）
- Capability 语义对齐：`docs/ModelNet/BACKEND_CAPABILITIES.md`（P2.2.4 落地）
- 算法参考：`docs/ModelNet/PN.py`
- yaml schema 示例：`api/configs/model_net.yaml.example`（P2.2 落地）
- 现有内置参考实现：
  - Aggregator: `aggregators/response/majority_vote.py`、`aggregators/token/sum_score.py`
  - Runner: `runners/response_level.py`、`runners/token_step.py`
  - Backend: `backends/llama_cpp.py`
