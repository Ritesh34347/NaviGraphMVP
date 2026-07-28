#!/usr/bin/env python3
"""Scaffold a new NaviGraph agent from the template in tools/templates/agent_template/.

Copies every file under tools/templates/agent_template/ into
packages/agent_runtime/navigraph_agents/<domain>/<name>/, stripping the
`.tmpl` suffix and substituting the placeholders `{{AgentName}}`,
`{{domain}}`, and `{{name}}` in both file contents and file/directory names.

Usage:
    python tools/scripts/new-agent.py --domain query --name schema_retriever

This creates:
    packages/agent_runtime/navigraph_agents/query/schema_retriever/__init__.py
    packages/agent_runtime/navigraph_agents/query/schema_retriever/agent.py
    packages/agent_runtime/navigraph_agents/query/schema_retriever/contracts.py
    packages/agent_runtime/navigraph_agents/query/schema_retriever/tests/__init__.py
    packages/agent_runtime/navigraph_agents/query/schema_retriever/tests/test_agent.py

After running, you still need to:
  1. Fill in the real payload/result fields in contracts.py.
  2. Replace the placeholder system prompt and parsing logic in agent.py.
  3. Register the agent in navigraph_agents/main.py (construct it in the
     lifespan handler and `register(...)` it), and add an `/agents/<domain>/<name>/invoke`
     route.
  4. Update the fixtures in tests/test_agent.py to match the real contract.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_DIR = REPO_ROOT / "tools" / "templates" / "agent_template"
AGENTS_ROOT = REPO_ROOT / "packages" / "agent_runtime" / "navigraph_agents"


def _to_pascal_case(name: str) -> str:
    """Convert a snake_case (or kebab-case) agent name into PascalCase.

    e.g. "schema_retriever" -> "SchemaRetriever", "sql-generator" -> "SqlGenerator"
    """

    parts = re.split(r"[_\-\s]+", name.strip())
    return "".join(part[:1].upper() + part[1:] for part in parts if part)


def _substitute(text: str, *, agent_name: str, domain: str, name: str) -> str:
    return (
        text.replace("{{AgentName}}", agent_name)
        .replace("{{domain}}", domain)
        .replace("{{name}}", name)
    )


def _validate_identifier(value: str, flag: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]*", value):
        raise SystemExit(
            f"error: --{flag} must be a lowercase snake_case Python identifier "
            f"(letters, digits, underscores; must start with a letter), got {value!r}"
        )


def scaffold_agent(domain: str, name: str, *, force: bool = False) -> Path:
    _validate_identifier(domain, "domain")
    _validate_identifier(name, "name")

    if not TEMPLATE_DIR.exists():
        raise SystemExit(f"error: template directory not found: {TEMPLATE_DIR}")

    agent_name = _to_pascal_case(name)
    target_dir = AGENTS_ROOT / domain / name

    if target_dir.exists() and not force:
        raise SystemExit(
            f"error: {target_dir} already exists. Pass --force to overwrite, "
            "or choose a different --domain/--name."
        )

    domain_dir = AGENTS_ROOT / domain
    domain_init = domain_dir / "__init__.py"
    if not domain_init.exists():
        domain_dir.mkdir(parents=True, exist_ok=True)
        domain_init.write_text(f'"""{domain.title()} domain agents."""\n', encoding="utf-8")
        print(f"created {domain_init.relative_to(REPO_ROOT)}")

    for template_path in sorted(TEMPLATE_DIR.rglob("*")):
        if template_path.is_dir():
            continue

        relative = template_path.relative_to(TEMPLATE_DIR)
        # Strip .tmpl suffix from the final path component if present.
        relative_parts = list(relative.parts)
        relative_parts[-1] = relative_parts[-1].removesuffix(".tmpl")
        substituted_parts = [
            _substitute(part, agent_name=agent_name, domain=domain, name=name)
            for part in relative_parts
        ]
        dest_path = target_dir.joinpath(*substituted_parts)

        dest_path.parent.mkdir(parents=True, exist_ok=True)

        content = template_path.read_text(encoding="utf-8")
        content = _substitute(content, agent_name=agent_name, domain=domain, name=name)
        dest_path.write_text(content, encoding="utf-8")
        print(f"created {dest_path.relative_to(REPO_ROOT)}")

    # __init__.py files for the new agent package and its tests/ subpackage
    # (the template dir doesn't ship these -- they're structural, not content).
    init_file = target_dir / "__init__.py"
    if not init_file.exists():
        init_file.write_text(f'"""{agent_name} agent."""\n', encoding="utf-8")
        print(f"created {init_file.relative_to(REPO_ROOT)}")

    tests_init = target_dir / "tests" / "__init__.py"
    if not tests_init.exists():
        tests_init.parent.mkdir(parents=True, exist_ok=True)
        tests_init.write_text("", encoding="utf-8")
        print(f"created {tests_init.relative_to(REPO_ROOT)}")

    return target_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--domain",
        required=True,
        help="Agent domain, e.g. 'understanding', 'query', 'insight', 'guardrail', 'ops', 'orchestrator'.",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Agent name in snake_case, e.g. 'schema_retriever'.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite the target directory if it already exists.",
    )
    args = parser.parse_args(argv)

    target_dir = scaffold_agent(args.domain, args.name, force=args.force)

    print()
    print(f"Scaffolded new agent at {target_dir.relative_to(REPO_ROOT)}")
    print("Next steps:")
    print("  1. Fill in the real payload/result fields in contracts.py")
    print("  2. Replace the placeholder system prompt + parsing logic in agent.py")
    print("  3. Register the agent and its invoke route in navigraph_agents/main.py")
    print("  4. Update tests/test_agent.py fixtures to match the real contract")

    return 0


if __name__ == "__main__":
    sys.exit(main())
