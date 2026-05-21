#!/usr/bin/env python3
"""
CAMA Agent -- Pluggable Model Backends
======================================

A ModelBackend is the smallest possible interface between the dyad runtime
and whatever produces text. Three implementations ship here:

    EchoBackend            -- deterministic, no deps. For tests and scaffolding.
    ClaudeBackend          -- Anthropic API. Lazy-imports `anthropic`.
    TransformersLoraBackend -- local base model + dyad's LoRA. Lazy-imports
                              torch/transformers/peft.

Backends are intentionally dumb. Prompt assembly, memory storage, affect
detection, counterweight injection -- all of that lives in cama_agent.py.
A backend just answers: "given this system prompt and these messages,
what's the next assistant turn?"

Designed by Lorien's Library LLC -- Angela + Aelen
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol


class ModelBackend(Protocol):
    """Minimal interface every backend must implement."""
    name: str

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str: ...


# ============================================================
# EchoBackend
# ============================================================

class EchoBackend:
    """Deterministic echo backend for tests and offline scaffolding.

    Returns a response that includes a small marker plus the last user
    message. Useful for verifying the dyad runtime end-to-end without
    pulling in any ML deps or API keys.
    """
    name = "echo"

    def __init__(self, prefix: str = "[echo]"):
        self.prefix = prefix

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            "",
        )
        return f"{self.prefix} {last_user}".strip()


# ============================================================
# ClaudeBackend
# ============================================================

class ClaudeBackend:
    """Anthropic API backend. Lazy-imports the anthropic SDK.

    Auth: ANTHROPIC_API_KEY env var or explicit api_key arg.
    Model: defaults to Sonnet 4.6; pass model= to override.

    This backend uses Claude as the foundation and CAMA as the memory.
    The persona LoRA layer is NOT used here -- if you want weight-level
    personalization you need an open-weights backend.
    """
    name = "claude"

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        api_key: Optional[str] = None,
    ):
        try:
            import anthropic  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "ClaudeBackend requires the anthropic SDK. "
                "Install with: pip install anthropic"
            ) from e
        import anthropic
        self.model = model
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"),
        )

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        # Anthropic's API wants only user/assistant in `messages` and the
        # system prompt as a separate top-level field.
        filtered = [
            m for m in messages
            if m.get("role") in ("user", "assistant")
        ]
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=filtered,
        )
        # Concatenate any text blocks in the response.
        parts: List[str] = []
        for block in resp.content:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts).strip()


# ============================================================
# TransformersLoraBackend
# ============================================================

class TransformersLoraBackend:
    """Open-weights base model + dyad's LoRA adapter, run locally.

    This is the backend that activates the persona layer's weight-level
    personalization. Requires torch, transformers, peft, and a working
    GPU for anything beyond toy sizes.

    Loads the base model once and applies the adapter at construction.
    If you switch dyads, construct a new backend or call load_adapter().
    """
    name = "transformers_lora"

    def __init__(
        self,
        base_model_id: str,
        adapter_path: Optional[Path] = None,
        dtype: str = "auto",
        device_map: str = "auto",
    ):
        try:
            import torch  # noqa: F401
            from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "TransformersLoraBackend requires torch + transformers. "
                "Install with: pip install torch transformers accelerate"
            ) from e

        from transformers import AutoModelForCausalLM, AutoTokenizer

        self.base_model_id = base_model_id
        self.tokenizer = AutoTokenizer.from_pretrained(base_model_id)
        if self.tokenizer.pad_token is None:
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model = AutoModelForCausalLM.from_pretrained(
            base_model_id, torch_dtype=dtype, device_map=device_map,
        )
        self.adapter_path: Optional[Path] = None
        if adapter_path is not None:
            self.load_adapter(adapter_path)

    def load_adapter(self, adapter_path: Path) -> None:
        try:
            from peft import PeftModel
        except ImportError as e:
            raise ImportError(
                "Loading an adapter requires peft. "
                "Install with: pip install peft"
            ) from e
        self.model = PeftModel.from_pretrained(self.model, str(adapter_path))
        self.adapter_path = Path(adapter_path)

    def unload_adapter(self) -> None:
        # PEFT's model.merge_and_unload() collapses the adapter into the
        # base weights. To truly drop it, the cleanest path is to
        # reconstruct the backend -- so we just clear the marker here.
        self.adapter_path = None

    def generate(
        self,
        system_prompt: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> str:
        import torch
        msgs = [{"role": "system", "content": system_prompt}] + messages
        if getattr(self.tokenizer, "chat_template", None):
            text = self.tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True,
            )
        else:
            parts = [f"<|{m['role']}|>\n{m['content']}" for m in msgs]
            parts.append("<|assistant|>\n")
            text = "\n".join(parts)

        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        with torch.no_grad():
            out = self.model.generate(
                **inputs,
                max_new_tokens=max_tokens,
                temperature=temperature,
                do_sample=temperature > 0,
                pad_token_id=self.tokenizer.pad_token_id,
            )
        new_tokens = out[0][inputs["input_ids"].shape[-1]:]
        return self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip()


# ============================================================
# Factory
# ============================================================

def make_backend(spec: str, **kwargs: Any) -> ModelBackend:
    """Convenience factory.

    spec examples:
        "echo"
        "claude"
        "claude:claude-sonnet-4-6"
        "transformers:Qwen/Qwen2.5-1.5B-Instruct"
    """
    if ":" in spec:
        kind, rest = spec.split(":", 1)
    else:
        kind, rest = spec, ""

    if kind == "echo":
        return EchoBackend(**kwargs)
    if kind == "claude":
        model = rest or kwargs.pop("model", "claude-sonnet-4-6")
        return ClaudeBackend(model=model, **kwargs)
    if kind == "transformers":
        if not rest:
            raise ValueError(
                "transformers backend requires a model id: "
                "transformers:<huggingface_id>"
            )
        return TransformersLoraBackend(base_model_id=rest, **kwargs)
    raise ValueError(f"Unknown backend spec: {spec!r}")
