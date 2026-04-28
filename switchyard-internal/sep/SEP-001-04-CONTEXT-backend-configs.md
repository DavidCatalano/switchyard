# Context Document

**Title**: Backend Runtime Configuration Research
**ID**: SEP-001-04-CONTEXT-backend-configs
**Date**: 2026-04-27
**Author**: AI Coding Agent
**PRD**: SEP-001-01-PRD-mvp-control-plane.md

---

## vLLM Configuration Parameters

### Container Image
- Primary: `vllm/vllm-openai:latest`
- Variants: GPU-specific images for different architectures

### Docker Environment Variables
```bash
# Core model settings
VLLM_MODEL="<model-path-or-hf-repo>"  # Required
VLLM_MODEL_NAME="<logical-name>"      # Optional, for naming
VLLM_TOKENIZER="<tokenizer-path>"     # Optional

# GPU/Resource Management
VLLM_GPU_MEMORY_UTILIZATION=<0.0-1.0>  # Default: 0.9
VLLM_TENSOR_PARALLEL_SIZE=<N>          # Number of GPUs
VLLM_MAX_MODEL_LEN=<tokens>            # Context length

# Scheduler/Performance
VLLM_MAX_NUM_SEQS=<N>                  # Max concurrent sequences
VLLM_MAX_NUM_BATCHED_TOKENS=<N>        # Max tokens per batch
VLLM_MAX_LOGPROBS=<N>                  # For logprobs API

# Quantization
VLLM_QUANTIZATION=<none|awq|gptq|squeezellm|fp8>
VLLM_LOAD_FORMAT="<full|tensorize|bitsandbytes>"

# Additional
VLLM_HOST="<host>"                     # Default: 0.0.0.0
VLLM_PORT="<port>"                     # Default: 8000
VLLM_API_KEY="<key>"                   # Optional API key
VLLM_ENABLE_LOG_STATS=true/false       # Request statistics
VLLM_LOGGING_LEVEL="<level>"           # Logging verbosity
```

### Common Patterns
- Usually runs with `--model` flag or `VLLM_MODEL` env var
- GPU memory fraction is critical for multi-model setups
- Tensor parallelism requires explicit GPU count specification
- Quantization options depend on model format

## koboldcpp Configuration Parameters

### Container Image
- Primary: `something/mistral-kobold:latest`
- Alternative: `coboldai/koboldcpp`

### Docker Environment Variables
```bash
# Core model settings
MODEL="<path-to-model-file>"           # Required, path to GGUF file
MODEL_NAME="<name>"                    # Optional display name

# Resource Management
CONTEXT_SIZE=<tokens>                  # Default: varies by model
GPU_LAYERS=<N>                         # Layers on GPU (0=CPU only)
GPU_SPLIT="<list>"                     # Manual GPU split
VRAM_GB=<N>                            # VRAM limit in GB

# Performance
BACKEND="<cuda|vulkan|sycl>"          # GPU backend
THREADS=<N>                            # CPU threads
MAIN_THREAD=true/false                 # Use main thread

# API Settings
HOST="<host>"                          # Default: 0.0.0.0
PORT="<port>"                          # Default: 5001
API_KEY="<key>"                        # Optional API key
CORS_DOMAIN="*"                        # CORS settings
DISABLE_CACHE=true/false               # Disable KV cache

# Additional
LOG_LEVEL="<level>"                    # Logging verbosity
```

### Common Patterns
- Primarily GGUF format models
- GPU layer offloading is the primary performance knob
- CPU/GPU split is important for mixed hardware
- Context size is a critical parameter

## exllamav2 Configuration Parameters

### Container Image
- Various implementations, often custom builds
- Common base: Ubuntu with CUDA toolkit

### Docker Environment Variables
```bash
# Core model settings
MODEL_PATH="<path-to-model>"           # Required
MODEL_TYPE="<type>"                    # Model architecture

# GPU/Resource Management
GPU_MEMORY_UTILIZATION=<0.0-1.0>      # GPU memory fraction
TENSOR_PARALLEL_SIZE=<N>             # For multi-GPU setups

# Quantization
QUANTIZATION="<none|4bit|8bit>"       # Default depends on model
QUANTIZATION_METHOD="<llm.int8|nf4>"  # Specific method

# Performance
CONTEXT_LENGTH=<tokens>               # Max context length
BATCH_SIZE=<N>                        # Request batch size

# API Settings
HOST="<host>"                         # Default: 0.0.0.0
PORT="<port>"                         # Default: 5000
```

### Common Patterns
- Focus on quantization and memory efficiency
- Often used with 4-bit or 8-bit quantization
- Context length management is important
- Less standardized configuration than vLLM

## SGLang Configuration Parameters

### Container Image
- Primary: `lmsysorg/sglang:latest`

### Docker Environment Variables
```bash
# Core model settings
MODEL="<model-path>"                   # Required
MODEL_NAME="<name>"                    # Optional

# Resource Management
GPU_MEMORY_UTILIZATION=<0.0-1.0>      # GPU memory fraction
TENSOR_PARALLEL_SIZE=<N>              # Multi-GPU setup
MAX_TOTAL_TOKENS=<N>                  # Context window

# Scheduler
MAX_NUM_REQS=<N>                      # Concurrent requests
MAX_NUM_SEQS=<N>                      # Sequence limit
DISABLE_LOGGING=true/false            # Disable request logging

# Performance
MAX_CHUNKED_PREFILL_SIZE=<N>          # Prefill chunk size
CUDA_VERSION="<version>"              # CUDA version compatibility

# API Settings
HOST="<host>"                         # Default: 0.0.0.0
PORT="<port>"                         # Default: 30000
API_KEY="<key>"                       # Optional API key
```

### Common Patterns
- Memory utilization is a key parameter
- Request scheduling parameters are important
- Often used for high-throughput scenarios
- Less mature than vLLM but gaining popularity

---

## Common Configuration Patterns Across Runtimes

### Universal Parameters
- Model path/repo (required)
- Host/port (API endpoint)
- API key (optional security)
- Context length/tokens
- GPU memory utilization
- Tensor parallel size
- Log level/verbosity

### Runtime-Specific Parameters
- **vLLM**: Quantization methods, load format, batch tokens
- **koboldcpp**: GGUF-specific, GPU layer offloading, CPU/GPU split
- **exllamav2**: Quantization focus, model type detection
- **SGLang**: Request scheduling, prefill chunking

### Suggested Unified Structure
```yaml
models:
  qwen-32b:
    backend: vllm
    image: vllm/vllm-openai:latest
    
    # Universal parameters
    model_path: "Qwen/Qwen2-32B-Instruct"
    host: "0.0.0.0"
    port: 8000
    api_key: ""  # Optional
    context_length: 4096
    gpu_memory_utilization: 0.9
    tensor_parallel_size: 2
    log_level: "info"
    
    # Backend-specific parameters
    backend_config:
      # vLLM-specific
      quantization: "none"
      max_num_seqs: 256
      max_num_batched_tokens: 2048
      
      # koboldcpp-specific
      # gpu_layers: 35
      # backend: "cuda"
      
      # exllamav2-specific
      # quantization_method: "4bit"
```

---

## Research Notes

### Key Findings
1. **Model path** is universal but format varies (HF repo vs local file)
2. **GPU memory management** is critical across all backends
3. **Context length** is important but parameter names vary
4. **API endpoint** configuration is standardized (host/port)
5. **Quantization** is backend-specific and often format-dependent
6. **Parallelism** concepts are similar but implementation differs

### Open Questions
- Should we abstract GPU memory parameters or keep them backend-specific?
- How to handle model format detection (GGUF vs HuggingFace vs safetensors)?
- Should backend-specific configs be validated against known parameters?
- What's the best way to document supported parameters per backend?

### Next Steps
- Implement config validation that understands backend-specific schemas
- Create backend-specific config templates/examples
- Consider runtime parameter discovery via backend adapters
- Document parameter defaults and constraints
