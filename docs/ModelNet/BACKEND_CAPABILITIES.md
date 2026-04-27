# BACKEND_CAPABILITIES.md

> **状态**：v0.2 冻结，跟随 EXTENSIBILITY_SPEC §3.1 / §3.2 同步演进。
> **作用**：把 Capability 矩阵 + 三个最容易踩的语义坑钉死在一个独立文档里，作为 Phase 4 加 backend 时的合约。新 backend 必须在自己的 adapter 测试里附 fixture 验证：声明的 capability 和它实际能干的事一致，跨 backend 的 logprob 语义经 adapter 内部归一化后对齐。

## 1. Capability 矩阵

> 真值表来源：EXTENSIBILITY_SPEC §3.2，附此处以便 PR review 时不用跨文档跳转。

| Capability | llama_cpp | vllm | openai_compat | anthropic |
|---|:---:|:---:|:---:|:---:|
| `STREAMING` | ✅ `/completion?stream=true` (SSE) | ✅ SSE `/v1/completions?stream=true` | ✅ SSE | ✅ SSE |
| `TOKEN_STEP` | ✅ `max_tokens=1, n_probs=k` | ✅ `max_tokens=1, logprobs=k` | ⚠️ `max_tokens=1, logprobs=true, top_logprobs=k` 仅 chat-completions | ❌ |
| `TOP_PROBS` | ✅ `completion_probabilities[0].top_probs` | ✅ `choices[0].logprobs.top_logprobs` | ⚠️ 0–20 上限 | ❌ |
| `POST_SAMPLING_PROBS` | ✅ `post_sampling_probs=true` | ⚠️ 默认是 logprobs (log-softmax)，需 adapter 内换算 | ✅ logprobs 即 post-sampling | — |
| `LOGITS_RAW` | ❌（除非用我们改过的 fork） | ✅ `return_logits` extension | ❌ | ❌ |
| `CHAT_TEMPLATE` | ✅ `/apply-template` | ❌（隐式应用） | ❌（N/A） | ❌（N/A） |
| `FUNCTION_CALLING` | ❌ | ⚠️（部分模型） | ✅ | ✅ |
| `KV_CACHE_REUSE` | ⚠️（slot id 路径，v0.2 未实现） | ❌（vLLM 内部管） | ❌ | ❌ |

**列说明**：
- `llama_cpp` —— 自托管 llama.cpp HTTP server（v0.2 唯一落地的 backend）。
- `vllm` —— 自托管 vLLM；P4.1 落地。
- `openai_compat` —— OpenAI 兼容 API（含 OpenAI 本家、Together、Groq 等）；P4.2 落地。
- `anthropic` —— Anthropic Messages API；P4.3 落地。

## 2. 三个最容易踩的语义坑

### 2.1 `POST_SAMPLING_PROBS` 不是 `LOGITS_RAW`

llama.cpp 的 `post_sampling_probs=true` 返回的是 **采样空间归一化后的概率** —— 即 top-k 候选自身和归一到 1 的概率分布，不是模型 head 输出的 raw logits 也不是 full-vocab softmax。

**影响**：
- `token_step` runner 用这层概率做 token 投票是合法的（PN.py 的语义）。
- 想做 **真正的 logit 加权平均** 的研究 runner（写论文用 `token_step_strict`）必须 require `LOGITS_RAW`，而不是 `POST_SAMPLING_PROBS`，否则结果对不上理论。
- 两个语义算出的 ensemble 结果在大多数模型上会偏移，对 ablation 不是噪声。

**v0.2 的做法**：`token_step` runner 仅 require `TOKEN_STEP + TOP_PROBS`，把 `POST_SAMPLING_PROBS` 列在 `optional_capabilities`。`token_step_strict`（v0.3+）才 require `LOGITS_RAW`。

### 2.2 OpenAI `top_logprobs ≤ 20`

OpenAI / Azure OpenAI / 大多数 openai_compat 提供商的 `top_logprobs` 上限是 **20**。某些模型（`gpt-3.5-turbo-0301`、部分 instruct-tuned 旧版）**完全不支持 logprobs**，且仅 chat-completions 端点暴露 logprobs（completions 老接口已停止维护）。

**影响**：
- 一个 runner 配 `top_k=25` 不能在 OpenAI backend 上跑，必须在启动期 / DSL 导入时就被拒绝，而不是运行时报 `top_logprobs is invalid`。
- 写 `OpenAICompatBackend.validate_requirements` 时必须 override 默认实现，把 `min_top_k > 20` 和 `model_name in [known_no_logprobs_skus]` 两条都翻译成 `ValidationIssue("error", ...)`。

**示例**（P4.2 实现时按此对齐）：

```python
class OpenAICompatBackend(ModelBackend):
    @classmethod
    def validate_requirements(cls, spec, requirements):
        issues: list[ValidationIssue] = []
        for req in requirements:
            if req["kind"] == "min_top_k" and isinstance(req["value"], int) and req["value"] > 20:
                issues.append({
                    "severity": "error",
                    "requirement": req,
                    "message": f"OpenAI top_logprobs is capped at 20, runner requested {req['value']}",
                    "i18n_key": "parallelEnsemble.errors.openaiTopKCap",
                })
            if req["kind"] == "needs_logprobs" and bool(req["value"]) and \
               spec.model_name.startswith("gpt-3.5-turbo-0301"):
                issues.append({
                    "severity": "error",
                    "requirement": req,
                    "message": "gpt-3.5-turbo-0301 does not support logprobs",
                    "i18n_key": "parallelEnsemble.errors.modelNoLogprobs",
                })
        return issues
```

### 2.3 vLLM `logprobs` 是 log-softmax 不是概率

vLLM 的 `choices[0].logprobs.top_logprobs` 字段是 **log-softmax 后的对数概率**（`log p_i`），不是已归一化的概率值。如果 adapter 不做换算就直接喂给聚合器，跨 backend 的同一 prompt 会得到语义不对齐的 float —— `0.5` 和 `-0.69` 比大小没意义。

**adapter 内部的最小换算**：

```python
# 单步 top-k 候选
top_logprobs: dict[str, float] = data["choices"][0]["logprobs"]["top_logprobs"][0]
# log-softmax → 概率
unnormalised = {tok: math.exp(lp) for tok, lp in top_logprobs.items()}
# top-k 内部归一到和为 1，对齐 llama.cpp `post_sampling_probs=true` 的语义
total = sum(unnormalised.values())
post_sampling = {tok: p / total for tok, p in unnormalised.items()} if total > 0 else {}
```

**影响**：
- 这一步是 P4.1 vLLM adapter 的硬约束，不能漏。
- P4.4 跨 backend logprob 一致性 fixture 是兜底护栏：同一 prompt 喂三个 mocked backend，归一化后 top-k 候选概率应在公共子集上误差 < 1e-3。

## 3. Backend 加 capability 的合约

新 backend（无论 fork 还是 v0.3 三个新 adapter）落地时必须：

1. **列出 capability 集合**：在自己 adapter 的模块顶层定义一个 `frozenset[Capability]`，并在 `capabilities(spec)` 里返回它（如某些 capability 受 `spec.model_name` 影响则按 spec 分支）。
2. **写 fixture**：在 `tests/unit_tests/.../test_<backend>.py` 里加一个 `test_capability_declaration` 或等价用例，断言声明集合等于本表对应列。fixture 是这份文档的可执行快照。
3. **覆盖语义坑**：
   - 若 backend 同时声明 `TOP_PROBS` + `POST_SAMPLING_PROBS`，写 fixture 喂一份固定 logprobs/probs 输入，断言 adapter 内归一化后误差 < 1e-6。
   - 若 backend 只声明 `TOP_PROBS` 不声明 `POST_SAMPLING_PROBS`，必须能用 `LOGITS_RAW` 数据复现 PN.py 同一份 prompt 的 top-k 顺序（兜底 P4.4 的跨 backend fixture）。
4. **override `validate_requirements` 必要时**：默认实现对 capability 子集兜底，但任何细粒度上限（`top_k ≤ N`、模型黑名单、版本号）必须 override，参考 §2.2 OpenAI 示例。

## 4. 当前 v0.2 的实际声明

**v0.2 只落地 llama_cpp**（`api/core/workflow/nodes/parallel_ensemble/backends/llama_cpp.py`）：

```python
_LLAMA_CPP_CAPABILITIES = frozenset({
    Capability.STREAMING,
    Capability.TOKEN_STEP,
    Capability.TOP_PROBS,
    Capability.POST_SAMPLING_PROBS,
    Capability.CHAT_TEMPLATE,
})
```

`LOGITS_RAW` 不声明 —— 见 §2.1。`FUNCTION_CALLING` / `KV_CACHE_REUSE` 不声明 —— 自托管 llama.cpp 不暴露 OpenAI 风格 tool calling，KV cache 复用见 PN.py `clear_slot_kv_cache` 但当前 framework 不利用。

`validate_requirements` 默认对 `needs_function_calling=true` 拒（capability 不存在），其它 requirement 走默认 capability-bottom 兜底。

## 5. 修订指引

- 新增 capability：先改 `spi/capability.py` + 本文档矩阵 + EXTENSIBILITY_SPEC §3.1，再让 backend 声明。
- 修改语义坑：必须同时改 §2 这里 + EXTENSIBILITY_SPEC §3.2 + P4.4 fixture，三处保持一致。
- 撤销某个 backend 的 capability：算 SPI 破坏性变更，按 EXTENSIBILITY_SPEC §11 流程处理，不能默默删。
