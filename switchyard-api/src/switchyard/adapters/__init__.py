"""Backend adapter implementations."""

from switchyard.adapters.vllm import VLLMAdapter, register_vllm

__all__ = ["VLLMAdapter", "register_vllm"]
