"""ExecutorAgent — Executa passos do plano chamando tools."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ExecutorAgent:
    """Executa sequência de ferramentas respeitando dependências."""

    def __init__(self, tool_runner: Callable[[str, dict[str, Any]], dict[str, Any]]):
        self.tool_runner = tool_runner

    def execute(
        self,
        steps: list[dict[str, Any]],
        max_iterations: int = 8,
    ) -> list[dict[str, Any]]:
        """Executa steps em ordem, preenchendo resultados."""
        executed: set[int] = set()
        results: list[dict[str, Any]] = list(steps)

        for _ in range(max_iterations):
            progress = False
            for step in results:
                step_number = step.get("step_number", 0)
                if step_number in executed:
                    continue

                deps = step.get("depends_on", []) or []
                if not all(d in executed for d in deps):
                    continue

                # Resolve placeholders de resultados anteriores
                args = self._resolve_args(step.get("args", {}), results)
                tool = step.get("tool", "direct_response")

                try:
                    tool_result = self.tool_runner(tool, args)
                    step["result"] = tool_result
                    step["status"] = "done" if tool_result.get("success") else "failed"
                except Exception as exc:
                    step["result"] = {"success": False, "error": str(exc)}
                    step["status"] = "failed"

                executed.add(step_number)
                progress = True

            if len(executed) == len(results):
                break
            if not progress:
                # Deadlock: marca steps pendentes como falhos
                for step in results:
                    if step.get("step_number", 0) not in executed:
                        step["status"] = "failed"
                        step["result"] = {"success": False, "error": "Dependência não resolvida"}
                break

        return results

    def _resolve_args(
        self,
        args: dict[str, Any],
        all_steps: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Substitui placeholders como {{step_N.field}} por valores reais."""
        import re

        resolved: dict[str, Any] = {}
        for key, value in args.items():
            if isinstance(value, str):
                matches = re.findall(r"\{\{step_(\d+)\.([^}]+)\}\}", value)
                if not matches:
                    resolved[key] = value
                    continue

                new_value = value
                for step_num, field_path in matches:
                    step_result = self._get_step_result(all_steps, int(step_num))
                    replacement = self._get_nested_value(step_result, field_path)
                    if replacement is not None:
                        placeholder = f"{{{{step_{step_num}.{field_path}}}}}"
                        new_value = new_value.replace(placeholder, str(replacement))
                resolved[key] = new_value
            else:
                resolved[key] = value
        return resolved

    def _get_step_result(
        self,
        all_steps: list[dict[str, Any]],
        step_number: int,
    ) -> dict[str, Any]:
        for step in all_steps:
            if step.get("step_number") == step_number:
                return step.get("result", {}) or {}
        return {}

    def _get_nested_value(self, data: dict[str, Any], path: str) -> Any:
        parts = path.split(".")
        current: Any = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return None
        return current
