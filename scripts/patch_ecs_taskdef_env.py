#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Patch an ECS task-definition JSON: set/replace an environment variable.

Usage:
    python3 scripts/patch_ecs_taskdef_env.py \\
        --input  task_def.json \\
        --output patched.json \\
        --set    SIONNA_FEATURES_VERSION=v2

Reads the full task-definition JSON (as returned by
``aws ecs describe-task-definition --query taskDefinition``),
sets the named env var in every container definition, strips
the read-only fields that ``register-task-definition`` rejects,
and writes the result to --output.
"""
from __future__ import annotations

import argparse
import json
import sys

_READONLY = (
    "taskDefinitionArn", "revision", "status", "requiresAttributes",
    "compatibilities", "registeredAt", "registeredBy",
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input",  required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--set",    required=True, metavar="NAME=VALUE",
                    help="Environment variable assignment to apply")
    args = ap.parse_args()

    if "=" not in args.set:
        print(f"ERROR: --set must be NAME=VALUE, got: {args.set!r}", file=sys.stderr)
        sys.exit(1)

    name, value = args.set.split("=", 1)

    with open(args.input) as fh:
        td = json.load(fh)

    for cd in td.get("containerDefinitions", []):
        env = [e for e in cd.get("environment", []) if e["name"] != name]
        env.append({"name": name, "value": value})
        cd["environment"] = env

    for key in _READONLY:
        td.pop(key, None)

    with open(args.output, "w") as fh:
        json.dump(td, fh)

    print(f"Patched: {name}={value} → {args.output}")


if __name__ == "__main__":
    main()
