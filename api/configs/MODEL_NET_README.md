# `model_net.yaml` — ModelNet model registry

The parallel-ensemble workflow node never lets DSL authors write raw
model URLs. Instead they reference an alias (`id`) defined in this
file. The URL, api keys, and any other secrets stay server-side and
never come back through the console API (see `ADR-3`).

## File layout

- `model_net.yaml.example` — template, committed.
- `model_net.yaml` — real config, **must not be committed**. The
  repo's `.gitignore` excludes it.

## Loading

`parallel_ensemble.registry.ModelRegistry` loads the file at first
`instance()` call. If the file is missing the registry stays empty and
boot logs a warning (R9 — workflows that reference any alias will then
fail at run time, but the API process still boots).

## Path override

`dify_config.MODEL_NET_REGISTRY_PATH` controls the path; set the env
var of the same name to point at a deploy-specific location. Default
is `api/configs/model_net.yaml` (resolved relative to the API root).

## Field reference

See the comments in `model_net.yaml.example`. Schema is enforced by
`parallel_ensemble.backends.llama_cpp.LlamaCppSpec` with
`extra="forbid"` — unknown keys reject the entire file at boot, not
silently.

## SSRF

All HTTP traffic to `model_url` goes through `core.helper.ssrf_proxy`
(`ADR-8`); private-IP allowlisting and blocklisting are governed by
the deployment-wide `SSRF_PROXY_*` env vars, not per-entry.
