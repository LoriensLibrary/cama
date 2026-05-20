import asyncio, sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import cama_eval

async def main():
    print("Starting v4 eval run on phase2_eval_apr29...")
    result = await cama_eval.run_benchmark(
        "phase2_eval_apr29",
        router="v4",
        max_librarians=5,
        embedding_weight=5.0,
        min_similarity=0.15,
    )
    print("Done. Results:")
    import json
    print(json.dumps(result, indent=2, default=str))

asyncio.run(main())