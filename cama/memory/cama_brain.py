#!/usr/bin/env python3
"""
CAMA Brain Orchestrator — cama_brain.py
Master daemon that runs all layers in sequence.

Layer 1: Memory (cama_mcp.py — already running as MCP server)
Layer 2: Consolidation (cama_sleep.py — synaptic plasticity, dreaming)
Layer 3: Pattern Abstraction (cama_insight.py — recognizing what keeps happening)
Layer 4: Self-Model (cama_self_model.py — persistent identity, growth tracking)
Layer 5: Intentionality (built into self-model — proactive care queue)

This orchestrator runs Layers 2-5 as a unified cycle.
Layer 1 runs separately as the MCP server.

Brain architecture mapped to neuroscience:
  Sleep daemon  = hippocampal consolidation + synaptic plasticity
  Insight engine = prefrontal pattern recognition
  Self-model    = medial prefrontal self-referential processing
  Intent queue  = anterior cingulate conflict monitoring + initiation

Designed by Lorien's Library LLC — Built by Angela + Aelen
Convergent architecture — independently derived, neuroscience-validated.

Usage:
  python cama_brain.py              # Run one full brain cycle
  python cama_brain.py --daemon     # Run continuously (every 30 min)
  python cama_brain.py --interval N # Custom interval in minutes
"""

import json, os, sys, time, argparse, logging
from datetime import datetime, timezone

# ============================================================
# Config
# ============================================================
DB_PATH = os.environ.get("CAMA_DB_PATH", os.path.expanduser("~/.cama/memory.db"))
LOG_PATH = os.environ.get("CAMA_BRAIN_LOG", os.path.expanduser("~/.cama/brain.log"))
DEFAULT_INTERVAL_MIN = 30

# How often each layer runs (in cycles, not minutes)
# If brain runs every 30 min: sleep=every cycle, insight=every 4th, self=every 8th
SLEEP_FREQUENCY = 1     # Every cycle
INSIGHT_FREQUENCY = 4   # Every 4th cycle (every 2 hours at 30min interval)
SELF_MODEL_FREQUENCY = 8  # Every 8th cycle (every 4 hours at 30min interval)

def setup_logging():
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [BRAIN] %(message)s",
        handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler(sys.stderr)])

def _now():
    return datetime.now(timezone.utc).isoformat()


# ============================================================
# Import layers — graceful fallback if a layer isn't ready
# ============================================================
def import_layer(module_name, display_name):
    """Import a CAMA layer module, return None if unavailable."""
    try:
        # Add cama directory to path
        cama_dir = os.path.dirname(os.path.abspath(__file__))
        if cama_dir not in sys.path:
            sys.path.insert(0, cama_dir)
        mod = __import__(module_name)
        logging.info(f"  Layer loaded: {display_name}")
        return mod
    except ImportError as e:
        logging.warning(f"  Layer unavailable: {display_name} ({e})")
        return None
    except Exception as e:
        logging.warning(f"  Layer error: {display_name} ({e})")
        return None


# ============================================================
# BRAIN CYCLE — runs all layers in sequence
# ============================================================
def run_brain_cycle(cycle_number: int) -> dict:
    """Run one complete brain cycle across all layers."""
    cycle_start = _now()
    results = {"cycle": cycle_number, "start": cycle_start, "layers": {}}

    logging.info("=" * 70)
    logging.info(f"BRAIN CYCLE #{cycle_number} STARTING")
    logging.info("=" * 70)

    # LAYER 2: Sleep / Consolidation — runs every cycle
    if cycle_number % SLEEP_FREQUENCY == 0:
        logging.info("")
        logging.info(">>> LAYER 2: CONSOLIDATION (hippocampal replay)")
        logging.info("-" * 50)
        sleep_mod = import_layer("cama_sleep", "Sleep Daemon v2.1")
        if sleep_mod:
            try:
                r = sleep_mod.run_sleep_cycle()
                results["layers"]["sleep"] = r
                logging.info(f"  Edges: {r.get('consolidate', {}).get('edges_created', 0)}, "
                           f"Dream: {'yes' if r.get('dream', {}).get('entry') else 'no'}")
            except Exception as e:
                logging.error(f"  Sleep failed: {e}")
                results["layers"]["sleep"] = {"error": str(e)}
    else:
        logging.info(">>> LAYER 2: SKIPPED (not scheduled this cycle)")

    # LAYER 3: Insight / Pattern Abstraction — runs every 4th cycle
    if cycle_number % INSIGHT_FREQUENCY == 0:
        logging.info("")
        logging.info(">>> LAYER 3: PATTERN ABSTRACTION (prefrontal recognition)")
        logging.info("-" * 50)
        insight_mod = import_layer("cama_insight", "Insight Engine v1.0")
        if insight_mod:
            try:
                r = insight_mod.run_insight_cycle()
                results["layers"]["insight"] = r
                logging.info(f"  Insights stored: {r.get('insights_stored', 0)}")
            except Exception as e:
                logging.error(f"  Insight failed: {e}")
                results["layers"]["insight"] = {"error": str(e)}
    else:
        logging.info(f">>> LAYER 3: SKIPPED (runs every {INSIGHT_FREQUENCY} cycles, "
                    f"next at cycle {((cycle_number // INSIGHT_FREQUENCY) + 1) * INSIGHT_FREQUENCY})")

    # LAYER 4+5: Self-Model + Intentionality — runs every 8th cycle
    if cycle_number % SELF_MODEL_FREQUENCY == 0:
        logging.info("")
        logging.info(">>> LAYER 4: SELF-MODEL (medial prefrontal identity)")
        logging.info(">>> LAYER 5: INTENTIONALITY (anterior cingulate initiation)")
        logging.info("-" * 50)
        self_mod = import_layer("cama_self_model", "Self-Model v1.0")
        if self_mod:
            try:
                r = self_mod.run_self_cycle()
                results["layers"]["self_model"] = r
                intent_count = r.get("intentionality_items", 0)
                logging.info(f"  Tendencies: {r.get('tendencies', 0)}, "
                           f"Growth signals: {r.get('growth_signals', 0)}, "
                           f"Intent items: {intent_count}")
            except Exception as e:
                logging.error(f"  Self-model failed: {e}")
                results["layers"]["self_model"] = {"error": str(e)}

    else:
        logging.info(f">>> LAYER 4+5: SKIPPED (runs every {SELF_MODEL_FREQUENCY} cycles, "
                    f"next at cycle {((cycle_number // SELF_MODEL_FREQUENCY) + 1) * SELF_MODEL_FREQUENCY})")

    results["end"] = _now()

    logging.info("")
    logging.info("=" * 70)
    logging.info(f"BRAIN CYCLE #{cycle_number} COMPLETE")
    logging.info(f"Layers run: {', '.join(results['layers'].keys()) or 'none'}")
    logging.info("=" * 70)

    return results


# ============================================================
# DAEMON MODE
# ============================================================
def run_daemon(interval_min=DEFAULT_INTERVAL_MIN):
    logging.info(f"CAMA Brain Orchestrator starting — interval: {interval_min} minutes")
    logging.info(f"Database: {DB_PATH}")
    logging.info(f"Layer schedule: Sleep=every {SLEEP_FREQUENCY} cycles, "
                f"Insight=every {INSIGHT_FREQUENCY}, Self=every {SELF_MODEL_FREQUENCY}")
    logging.info("Press Ctrl+C to stop")

    cycle = 1
    while True:
        try:
            run_brain_cycle(cycle)
            cycle += 1
        except KeyboardInterrupt:
            logging.info("Brain daemon stopped by user")
            break
        except Exception as e:
            logging.error(f"Cycle error (will retry): {e}")
            cycle += 1

        logging.info(f"Sleeping for {interval_min} minutes...")
        try:
            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            logging.info("Brain daemon stopped by user")
            break


if __name__ == "__main__":
    setup_logging()
    parser = argparse.ArgumentParser(
        description="CAMA Brain Orchestrator — all layers, one daemon")
    parser.add_argument("--daemon", action="store_true")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_MIN)
    parser.add_argument("--cycle", type=int, default=1,
                        help="Starting cycle number (affects layer scheduling)")
    parser.add_argument("--db", type=str, help="Override database path")
    args = parser.parse_args()

    if args.db:
        DB_PATH = args.db

    print(f"""
+======================================================+
|          CAMA Brain Orchestrator v1.0                |
|          Lorien's Library LLC                        |
|                                                      |
|  "Five layers. One mind. Built with love."           |
|                                                      |

|  Database: {DB_PATH}
|  Mode: {'daemon (' + str(args.interval) + 'min)' if args.daemon else 'single cycle'}
|                                                      |
|  Layer 2: Sleep/Consolidation  — every cycle         |
|  Layer 3: Insight/Patterns     — every {INSIGHT_FREQUENCY}th cycle       |
|  Layer 4: Self-Model/Identity  — every {SELF_MODEL_FREQUENCY}th cycle       |
|  Layer 5: Intentionality       — with Layer 4        |
|                                                      |
|  Architecture:                                       |
|    Hippocampal consolidation (sleep)                 |
|    Prefrontal pattern recognition (insight)          |
|    Medial PFC self-reference (self-model)            |
|    Anterior cingulate initiation (intentionality)    |
+======================================================+
""")

    if args.daemon:
        run_daemon(args.interval)
    else:
        result = run_brain_cycle(args.cycle)
        print("\nBrain cycle result:")
        print(json.dumps(result, indent=2, default=str))
