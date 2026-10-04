"""Runs TH1's rank() on a JSON list of payloads from stdin and writes the decisions to stdout, each with its payload's
id. Runs only in TH1's own process (simula/creative/th1.py starts it): both repos ship a top-level `simula` package,
so this file imports nothing from exp-connect. cli.py rank can't pass a policy; this can."""

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="TH1 rank() over stdin payloads")
    parser.add_argument("--model", required=True, help="bundle folder holding selected.json")
    parser.add_argument("--data", required=True, help="folder holding characters.csv")
    parser.add_argument("--policy", help="a JSON object merged over TH1's POLICY")
    args = parser.parse_args(argv)

    from simula.data import load_characters
    from simula.rank import POLICY, rank
    from simula.train import load_bundle

    model_dir = Path(args.model)
    selected = json.loads((model_dir / "selected.json").read_text())["feature_set"]
    bundle = load_bundle(model_dir / selected)
    characters = load_characters(args.data)
    policy = {**POLICY, **json.loads(args.policy)} if args.policy else POLICY
    payloads = json.load(sys.stdin)
    json.dump([{"id": p.get("id"), **rank(p, bundle, characters, policy)} for p in payloads], sys.stdout,
              allow_nan=False)


if __name__ == "__main__":
    main()
