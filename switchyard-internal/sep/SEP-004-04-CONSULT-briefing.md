# Consultation Document — SEP-004 Runtime Resolver Hardening Briefing

**Title**: Briefing on significance, current state, and scope of SEP-004
**ID**: SEP-004-01-CONSULT-briefing
**Date**: 2026-05-11
**Consultant**: Claude (Opus 4.7)
**PRD**: None yet (SEP-004 not yet initiated)
**Predecessor**: None

---

## Question

Qwen, while opening SEP-004, asked the user the following six questions
before proceeding further. They are reproduced here verbatim so the
consultant can address each by number:

> A few things, mostly focused on the runtime model as it relates to SEP-004:
>
> 1. **Runtime vs. backend distinction** — I understand backend is the engine
>    family (e.g., `vllm`) and runtime is a configured variant with specific
>    defaults (e.g., `vllm-cpu`, `vllm-gpu`). Are there currently multiple
>    vLLM runtime variants in the config, or is that something SEP-004 needs
>    to introduce?
>
> 2. **Current runtime schema shape** — What does the typed vLLM field set
>    look like today? The GitHub issue comment mentions `tensor_parallel_size`
>    and `gpu_memory_utilization` as examples, but I'd want to see the full
>    current set before thinking about hardening.
>
> 3. **Typo rejection today** — The issue mentions "fail loudly on typos in
>    typed layers." Right now, does the resolver silently ignore unknown
>    fields, pass them through, or already reject them? Understanding the
>    current behavior will shape what needs to change.
>
> 4. **`extra_args` format** — Is this already a defined field (e.g., a list
>    of strings like `["--disable-log-requests"]`), or does the escape hatch
>    not exist yet?
>
> 5. **The CONTEXT reference** — The roadmap points to
>    `SEP-001-04-CONTEXT-vllm-config.md` as planning reference for SEP-004.
>    Should I read that before we go further?
>
> 6. **Trainbox** — The issue mentions validating examples against
>    "Trainbox." Is that the primary Docker host? Any special characteristics
>    (GPU model, Docker setup) that affect how CPU vs. GPU runtimes are
>    tested?

The consultant should provide a single comprehensive answer that addresses
each numbered question and includes a framing assessment of SEP-004's
overall significance.

## Context

SEP-004 has no PRD or PLAN yet. The ROADMAP entry is terse: "Harden
runtime-specific launch behavior without chasing every backend CLI flag.
For vLLM, maintain a small typed core for high-value fields, require
unknown backend flags to live under `extra_args`, fail loudly on typos in
typed layers, and provide generated command previews for validation and
operator confidence." The GH issue (#4) adds: preserve typed vLLM fields,
prevent Switchyard-only fields from serializing to CLI, support
`extra_args`, support CPU/GPU cleanly, provide command preview, improve CPU
and GPU smoke tests, validate examples against Trainbox.

The user has given preliminary first-pass answers to Qwen's questions —
reproduced below — that materially shape the consultant's response. The
most consequential is the answer to Q3: unknown fields should be **passed
through to CLI**, not rejected, because vLLM evolves too quickly to track
as a schema target. That flips the design center away from "fail loudly"
and toward "render through + preview."

### User's preliminary responses

> 1. There may be more variants introduced. For example, I'm running a model
>    right now on a newer GPU that requires not the 'latest' version of vLLM
>    as I've always run in the past, but a nightly build. I'm uncertain how
>    Switchyard will represent that sort of distinction so that's something
>    to have in mind.
>
> 2. You'll have to do a code review to know how much pydantic modeling has
>    already been established. There's also a CONTEXT document that defines
>    the high-value fields worth supporting.
>
> 3. Unknown fields should be passed through. We don't want to try and keep
>    up w/ a fast evolving application.
>
> 4. As before, you'll have to explore the code.
>
> 5. Yes read it.
>
> 6. Dual RTX Pro 6000 Blackwells (192GB VRAM in aggregate). Not sure what
>    to add for Docker other than there is a planned deployment located in
>    the root of the project `deploy/systemd` directory that might shed more
>    light on this.

### Source material

- `switchyard-api/config.yaml` — current entity config (hosts, runtimes,
  models, deployments)
- `switchyard-api/src/switchyard/config/models.py` — Pydantic schema
  including `VLLMRuntimeConfig`
- `switchyard-api/src/switchyard/adapters/vllm.py` — adapter and
  `_build_cli_args`
- `switchyard-internal/sep/SEP-001-04-CONTEXT-vllm-config.md` —
  authoritative vLLM parameter inventory and tier model
- `deploy/systemd/README.md` — planned production deployment topology

## Response

### Why this work matters (frame)

SEP-004 is more significant than its terse ROADMAP entry suggests. It's the
work that gives Switchyard its identity as a *control plane* rather than a
thin Docker wrapper, and three things downstream depend on it. **Ergonomics**
is the product center per the ROADMAP, which explicitly names "inspect the
generated launch configuration" as a Switchyard goal — that's SEP-004's job,
not SEP-005's. The **Web UI MVP (SEP-008)** needs a preview/validate/dry-run
surface to be useful; without SEP-004 the UI has nothing to show beyond a
"load" button. And **backend pluralism (SGLang, koboldcpp, llama.cpp)**
depends on SEP-004 establishing the template every future adapter inherits —
skipping it and retrofitting later means three slightly-different validation
behaviors.

The trap to avoid: promoting every new vLLM flag into the typed core for
"completeness." vLLM ships flags continuously; chasing them turns
`VLLMRuntimeConfig` into an upstream tracker. Done well, SEP-004 is days of
work; done as "complete the type coverage," it expands forever.

### 1. Runtime variants — backend vs. runtime

Multiple vLLM runtime variants **already exist** in
`switchyard-api/config.yaml:36-52`:

```yaml
runtimes:
  vllm:        # GPU,  image: vllm/vllm-openai:latest
  vllm-cpu:    # CPU,  image: vllm/vllm-openai-cpu:latest-x86_64
               # defaults: device: cpu, dtype: bfloat16
```

A deployment selects one via its `runtime:` reference. So `backend` is the
engine family (`vllm`) and `runtime` is a configured variant (`vllm`,
`vllm-cpu`) — Qwen's reading is correct, and the pattern is in active use.

The new wrinkle: a Blackwell-class GPU deployment that requires the
**nightly** image (`vllm/vllm-openai:cu130-nightly`, visible in `docker ps`)
rather than `latest`. This is a third-variant case, e.g. `vllm-nightly`, and
the current model already supports it cleanly — just another
`runtimes.vllm-nightly` entry pinning the image and any flag defaults that
differ. SEP-004 should formalize this and ensure each variant carries its own
`defaults` (image tag, dtype, device) without bleeding into peers. The
CONTEXT doc's "The `image` Field as Version Lock" section is directly
relevant: pinning `cu130-nightly` also freezes the vLLM CLI-flag contract for
that variant, so SEP-004's typed schema doesn't have to know
Blackwell-specific flags — `extra_args` is the escape hatch and image pinning
is the version lock.

### 2. Current typed schema shape

`VLLMRuntimeConfig` at
`switchyard-api/src/switchyard/config/models.py:398-487` declares ~32 named
fields plus `extra_args`, grouped by vLLM's internal argument categories:

| Group | Fields |
|---|---|
| Model & Tokenizer | `model`, `repo`, `max_model_len`, `dtype`, `quantization`, `trust_remote_code`, `revision`, `code_revision`, `served_model_name`, `chat_template`, `tokenizer`, `tokenizer_mode`, `tokenizer_revision`, `hf_token`, `seed`, `reasoning_parser`, `tool_call_parser` |
| Device | `device: Literal["cpu", "cuda"]` |
| KV cache | `gpu_memory_utilization`, `kv_cache_dtype`, `block_size`, `enable_prefix_caching` |
| Parallelism | `tensor_parallel_size`, `pipeline_parallel_size`, `distributed_executor_backend` |
| Scheduling | `max_num_batched_tokens`, `max_num_seqs`, `enable_chunked_prefill`, `stream_interval` |
| Networking | `api_key`, `uvicorn_log_level` |
| Observability | `otlp_traces_endpoint` |
| Generation | `max_logprobs`, `logprobs_mode` |
| System | `disable_custom_all_reduce` |
| Tool use | `enable_auto_tool_choice` |
| Speculative | `speculative_config` (dict) |
| Multimodal | `limit_mm_per_prompt`, `language_model_only` |
| Catch-all | `extra_args` (dict) |

Coverage is solid against the CONTEXT doc's Tier 1+2 recommendations. Known
gaps versus that document: the LoRA family (`enable_lora`, `max_loras`,
etc.), `enable_sleep_mode`, `mm_processor_kwargs`, `enable_mm_embeds`,
`generation_config`/`override_generation_config`, `enforce_eager`,
`disable_sliding_window`. None are operationally critical right now —
they're Tier 3 and belong under `extra_args` until they earn promotion. The
`image` field is **intentionally not** on `VLLMRuntimeConfig`; it lives one
level up on `RuntimeConfig` because the runtime entity owns the image and
the typed config only describes launch flags.

### 3. Typo behavior today + the design choice

**Today's actual behavior**: `VLLMRuntimeConfig` does *not* declare
`model_config = {"extra": "forbid"}` — only `AppSettings` does
(`config/models.py:370`). By Pydantic v2 default, unknown fields on a
`BaseModel` are **silently dropped** during validation. So
`tenser_parallel_size: 2` (typo) is discarded and the container starts
without it. The model neither fails loudly nor passes through — it forgets.

**The user's design preference** (per his pre-answer to Q3): unknown fields
should be **passed through to the CLI**, not rejected. Rationale: vLLM
evolves too quickly to keep up with as a schema target.

That flips the SEP-004 thrust away from any "fail loudly on typos in typed
layers" framing and toward **"render extras to CLI, let vLLM be the final
arbiter, and surface mistakes via command preview."** Two implementation
paths honor this:

- **a) Allow-and-forward**: set `model_config = {"extra": "allow"}` on
  `VLLMRuntimeConfig`; in `_build_cli_args` iterate both named fields and
  `model_extra`. Simple, minimal blast radius.
- **b) Dict-layer resolution**: keep typed validation as a strict subset
  check on documented fields, but pass the original merged dict (not the
  validated model) to the CLI builder. Cleaner architecturally, more
  invasive.

Either way, the **command preview API** becomes load-bearing under the
pass-through model — it's the operator's only safety net against
silently-CLI-rendered typos. SEP-004 should treat preview as table-stakes,
not "nice to have."

### 4. `extra_args` format

Already exists, at `config/models.py:486`:

```python
extra_args: dict[str, Any] = Field(default_factory=dict)
```

Format: a **dict** keyed by flag name, not a list of strings. Per the
CONTEXT doc: "Passed verbatim to CLI as `--flag value` pairs." Example
usage:

```yaml
runtime_overrides:
  extra_args:
    disable-log-requests: true
    long-prefill-token-threshold: 4096
```

Open implementation details SEP-004 should pin down (verify in
`adapters/vllm.py:_build_cli_args`): how booleans render
(`--disable-log-requests` with no value vs `--disable-log-requests true`),
and whether key normalization handles `_` vs `-` consistently between typed
fields and `extra_args` keys.

### 5. The CONTEXT document — yes, read it

`switchyard-internal/sep/SEP-001-04-CONTEXT-vllm-config.md` (~350 lines).
It covers:

- Complete vLLM parameter inventory across 11 argument groups
- A three-tier exposure model (Tier 1 typed / Tier 2 typed-with-defaults /
  Tier 3 `extra_args`)
- Real-world validation against a production Qwen 3.6 27B-FP8 deployment —
  that's where `disable_custom_all_reduce`, `kv_cache_dtype: fp8_e4m3`, and
  `speculative_config` originated as "actively tuned, must be supported"
  rather than theoretical
- The "Image Field as Version Lock" section — directly relevant to Q1 and Q6
- Cascade semantics

Caveat: the CONTEXT doc predates SEP-002, so the *specific YAML structure*
it sketches (`global` / `runtime_defaults` / `models.<name>.runtime`) differs
from what shipped. The current cascade is
`runtime.defaults → model.runtime_defaults → deployment.runtime_overrides →
extra_args`. The **tier definitions** and **parameter lists** remain
authoritative; the example YAML structure does not.

### 6. Trainbox + deployment topology

Trainbox is the primary Docker host. From `config.yaml:1-34`:

- `backend_host: 10.0.0.10`
- `docker_host: tcp://127.0.0.1:2375` (SSH-tunneled in dev — see Makefile
  `make tunnel`)
- `docker_network: ai_net`
- Two CUDA accelerators declared as ids `"0"` and `"1"`
- Container defaults include `ipc: host`, `memlock: unlimited`, and
  HF/CUDA env vars

⚠️ **Stale data alert**: the `accelerators[].vram_gb: 24` values in the
committed `config.yaml` are wrong. The actual hardware is **dual RTX Pro
6000 Blackwell, ~96 GB per card (~192 GB aggregate)**. That looks like a
leftover from a prior RTX 3090/4090 setup. SEP-004 should either correct it
manually or — better long-term — SEP-007 (Host Resource Discovery) should
derive accelerator inventory from `nvidia-smi` rather than relying on
hand-entered values.

The Blackwell architecture (sm_120) is why the **`cu130-nightly`** vLLM
image is required — `latest` doesn't support it yet. That's the
nightly-runtime case from Q1, and it makes the version-lock argument from
§5 concrete: SEP-004 doesn't need to know any Blackwell-specific flags;
pinning the runtime's `image` tag handles it.

CPU vs GPU testing: the `vllm-cpu` runtime exists in `config.yaml:45-52`
and the `tinyllama-1.1b-chat-vllm-cpu-trainbox` deployment uses it. That's
the smoke target for the CPU path.

Production topology (relevant to SEP-009 more than SEP-004 but useful
framing): `deploy/systemd/README.md` plans Switchyard running as a systemd
service inside an LXD container (`switchyard.lan`), with SSH-tunneled
Docker socket forwarding to `trainbox` and `devbox` via Unix sockets at
`/run/switchyard/<host>-docker.sock`. The current TCP tunnel is a dev-time
arrangement; the production target is Unix-socket-based and treats the SSH
key as privileged (membership in the remote `docker` group is
root-equivalent on that host).

### Implied scope for SEP-004

Given the answers above, SEP-004 distills to four concrete pieces:

1. **Flip `VLLMRuntimeConfig` to `extra: "allow"`** (or equivalent
   dict-layer change) so unknown fields render to CLI rather than being
   silently dropped. Honors the pass-through preference from §3.
2. **Switchyard-only fields never serialize to CLI**: audit
   `_build_cli_args` in `adapters/vllm.py` to ensure no Switchyard-internal
   config keys can leak into the rendered command. Cover with tests.
3. **Generated command preview API** — e.g. `GET
   /api/deployments/{id}/preview` returning the rendered CLI args +
   container spec without launching. Load-bearing under the pass-through
   model and foundational for SEP-008's Web UI.
4. **CPU and GPU smoke discipline**: add `tests/live/` smokes for the
   `vllm-cpu` path (tinyllama) and the `vllm` path on trainbox, including
   verification that `extra_args` flows through correctly. Builds on the
   live-test infrastructure that landed in SEP-003a.

Per the GH issue, out of scope: other runtimes, API CRUD, Web UI. The
discipline this work establishes is what those later SEPs lean on.
