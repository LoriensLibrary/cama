#!/usr/bin/env python3
"""
CAMA Persona Trainer -- LoRA Fine-tuning Entry Point
====================================================

This is the heavy half of the persona layer. It runs LoRA fine-tuning against
a HuggingFace-compatible base model using the corpus that cama_persona.py
already prepared and fingerprinted.

Identity preservation in practice
---------------------------------

The training set is built from two files in the adapter directory:

    training_data.jsonl    -- the dyad's exchanges (the relational signal)
    identity_pins.jsonl    -- the AI's core identity teachings, restated
                              as (system, user="Who are you?", assistant=teaching)

The identity pins are oversampled `pin_oversample` times (default 8) and
shuffled in. They appear in every epoch, in every shuffle, so the gradient
signal toward "remember who you are" stays strong throughout training. The
relational adapter learns this dyad's style without overwriting the AI's own
selfhood.

Dependencies
------------

This module imports torch, transformers, peft, and datasets lazily inside
run_training(). If any are missing, the function raises a clear ImportError
with install hints. The rest of the CAMA stack does not load these libraries.

Typical hardware: an 8B parameter base model trains a ~30MB LoRA in
single-digit hours on a consumer 24GB GPU for a few thousand exchanges.
Smaller bases (1-3B) train in minutes.

Designed by Lorien's Library LLC -- Angela + Aelen
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from cama.agents import cama_persona

_DEP_HINT = (
    "Training requires: torch, transformers, peft, datasets, accelerate. "
    "Install with:  pip install torch transformers peft datasets accelerate"
)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            out.append(json.loads(line))
    return out


def _format_chat(record: Dict[str, Any], tokenizer) -> str:
    """Use the tokenizer's chat template if it has one, else a minimal format.

    Many open-weights chat models (Llama-3, Qwen2.5, etc.) ship a chat template;
    we prefer it. If absent, we fall back to a plain `<|system|>...<|user|>...
    <|assistant|>...` format that works as a string-level target.
    """
    msgs = record["messages"]
    if getattr(tokenizer, "chat_template", None):
        return tokenizer.apply_chat_template(
            msgs, tokenize=False, add_generation_prompt=False
        )
    parts: List[str] = []
    for m in msgs:
        parts.append(f"<|{m['role']}|>\n{m['content']}")
    parts.append("<|end|>")
    return "\n".join(parts)


def run_training(
    dyad_id: str,
    version: str,
    pin_oversample: int = 8,
    max_seq_length: int = 1024,
    seed: int = 42,
    output_subdir: str = "adapter",
) -> Dict[str, Any]:
    """Train the LoRA adapter for a prepared version.

    The version must have been allocated by cama_persona.prepare_adapter().
    Reads hyperparams from the version's metadata.json. Saves the adapter
    under `<adapter_dir>/<output_subdir>/` and calls
    cama_persona.mark_trained() on success.
    """
    try:
        import torch  # noqa: F401
        from datasets import Dataset
        from peft import LoraConfig, TaskType, get_peft_model
        from transformers import (
            AutoModelForCausalLM,
            AutoTokenizer,
            DataCollatorForLanguageModeling,
            Trainer,
            TrainingArguments,
        )
    except ImportError as e:
        raise ImportError(f"{_DEP_HINT} (missing: {e.name})") from e

    adir = cama_persona._adapter_dir(dyad_id, version)
    meta_p = adir / "metadata.json"
    if not meta_p.exists():
        raise FileNotFoundError(
            f"No prepared adapter at {adir}. "
            f"Run cama_persona.prepare_adapter() first."
        )
    metadata = json.loads(meta_p.read_text())
    base_model_id = metadata["base_model"]
    hp = metadata.get("hyperparams", {}) or {}

    # Verify training data integrity before consuming it.
    integrity = cama_persona.verify_adapter(dyad_id, version)
    if not integrity["ok"]:
        raise RuntimeError(
            f"Adapter integrity check failed: {integrity}"
        )

    train_records = _load_jsonl(adir / "training_data.jsonl")
    pin_records = _load_jsonl(adir / "identity_pins.jsonl")

    if not pin_records:
        # If a dyad has no core teachings yet, training risks pure mirroring.
        # Refuse rather than silently train without identity preservation.
        raise RuntimeError(
            "No identity pins present. Training would not preserve AI "
            "selfhood. Add at least one durable core teaching to the dyad "
            "before training a persona adapter."
        )

    # Identity preservation: oversample pins into the training set.
    oversampled = pin_records * max(1, pin_oversample)
    combined = train_records + oversampled

    tokenizer = AutoTokenizer.from_pretrained(base_model_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    rendered = [{"text": _format_chat(r, tokenizer)} for r in combined]
    dataset = Dataset.from_list(rendered)

    def _tokenize(batch):
        out = tokenizer(
            batch["text"],
            truncation=True,
            max_length=max_seq_length,
            padding=False,
        )
        return out

    tokenized = dataset.map(_tokenize, batched=True, remove_columns=["text"])
    tokenized = tokenized.shuffle(seed=seed)

    model = AutoModelForCausalLM.from_pretrained(
        base_model_id,
        torch_dtype="auto",
        device_map="auto",
    )

    lora_cfg = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=int(hp.get("lora_r", 8)),
        lora_alpha=int(hp.get("lora_alpha", 16)),
        lora_dropout=float(hp.get("lora_dropout", 0.05)),
        bias="none",
        target_modules=hp.get("target_modules") or "all-linear",
    )
    model = get_peft_model(model, lora_cfg)

    out_dir = adir / output_subdir
    out_dir.mkdir(parents=True, exist_ok=True)

    args = TrainingArguments(
        output_dir=str(out_dir),
        num_train_epochs=float(hp.get("epochs", 2)),
        per_device_train_batch_size=int(hp.get("batch_size", 1)),
        gradient_accumulation_steps=int(hp.get("grad_accum", 8)),
        learning_rate=float(hp.get("learning_rate", 2e-4)),
        warmup_ratio=float(hp.get("warmup_ratio", 0.03)),
        weight_decay=float(hp.get("weight_decay", 0.0)),
        lr_scheduler_type=hp.get("lr_scheduler_type", "cosine"),
        logging_steps=int(hp.get("logging_steps", 20)),
        save_strategy="no",
        report_to=[],
        seed=seed,
        bf16=bool(hp.get("bf16", True)),
        fp16=bool(hp.get("fp16", False)),
    )

    collator = DataCollatorForLanguageModeling(
        tokenizer=tokenizer, mlm=False,
    )
    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=tokenized,
        data_collator=collator,
    )
    train_result = trainer.train()
    model.save_pretrained(str(out_dir))
    tokenizer.save_pretrained(str(out_dir))

    notes = {
        "base_model": base_model_id,
        "exchange_records": len(train_records),
        "identity_pins_oversampled_to": len(oversampled),
        "total_training_records": len(combined),
        "max_seq_length": max_seq_length,
        "pin_oversample": pin_oversample,
        "train_loss": float(train_result.training_loss),
        "global_step": int(train_result.global_step),
        "adapter_path": str(out_dir),
    }
    cama_persona.mark_trained(dyad_id, version, trainer_notes=notes)
    return {"status": "trained", "dyad_id": dyad_id, "version": version, **notes}


def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="CAMA persona LoRA trainer")
    p.add_argument("dyad_id")
    p.add_argument("version")
    p.add_argument("--pin-oversample", type=int, default=8,
                   help="How many times to repeat identity pins in the training set.")
    p.add_argument("--max-seq-length", type=int, default=1024)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output-subdir", default="adapter")
    args = p.parse_args()
    print(json.dumps(run_training(
        dyad_id=args.dyad_id,
        version=args.version,
        pin_oversample=args.pin_oversample,
        max_seq_length=args.max_seq_length,
        seed=args.seed,
        output_subdir=args.output_subdir,
    ), indent=2))


if __name__ == "__main__":
    _cli()
