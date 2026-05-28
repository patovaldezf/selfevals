"""ArtifactCompletenessGrader: score structured artifacts for completeness.

This grader targets the generic category of agents that PRODUCE ARTIFACTS:
structured documents or reports (a research brief, an incident report, a
spec, a comparison table, ...). "Researcher" is one such category, not a
product. The contract is fully agnostic: the grader reads only the case's
declared expectations and the adapter response, never any external service.

What it checks (deterministic verdict):
- The artifact is `AdapterResponse.structured_output`. Empty / missing
  artifact is a FAIL.
- `expected.output_schema` (when set) is validated by a self-contained
  JSON-Schema SUBSET validator (see `_validate_schema`). An artifact that
  does not satisfy the schema is a FAIL. The subset deliberately covers only
  `type: "object"`, `properties` (recursive), `required`, and primitive type
  checks. Full JSON Schema (`anyOf`, `$ref`, `format`, ...) is out of scope
  and documented as a non-goal: this base install stays dependency-free, so
  we do not pull in `jsonschema`.
- `expected.required_sections` lists top-level keys the artifact must carry,
  each mapping to a NON-EMPTY value. A "section" here is a structured-output
  key; detecting a heading inside free-form prose is an explicit non-goal.

Label policy:
- FAIL  -> artifact empty, or schema invalid.
- PASS  -> schema valid AND every required section present (or no sections
           declared, schema valid, artifact non-empty).
- PARTIAL -> schema valid but one or more required sections missing.

Breakdown: a root node `artifact_completeness` with one child per required
section (present -> score 1.0, absent -> score 0.0).

Optional advisory quality: an injectable `LLMJudgeGrader` can be composed in.
Its result is attached as a weight-0 child node and surfaced in `details`,
but it NEVER flips the deterministic verdict.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from selfevals.graders.base import (
    BreakdownNode,
    GradeLabel,
    Grader,
    GraderContext,
    GradeResult,
)

if TYPE_CHECKING:
    from selfevals.graders.llm_judge import LLMJudgeGrader
    from selfevals.schemas.eval_case import Expected

# JSON-Schema-subset primitive type names mapped to Python types. `integer`
# excludes bool (bool is a subclass of int in Python, but not an integer per
# JSON Schema); `number` accepts int and float but not bool.
_PRIMITIVE_TYPES: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "object": dict,
    "array": list,
    "null": type(None),
}


def _is_empty_artifact(artifact: dict[str, Any] | None) -> bool:
    return not artifact


def _is_non_empty_value(value: Any) -> bool:
    """A section value counts as present when it is not None and not an empty
    container/string. Numbers (including 0) and booleans count as present."""
    if value is None:
        return False
    if isinstance(value, str | list | dict | tuple | set):
        return len(value) > 0
    return True


def _type_matches(value: Any, type_name: str) -> bool:
    if type_name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    expected = _PRIMITIVE_TYPES.get(type_name)
    if expected is None:
        # Unknown / unsupported type keyword: out of subset scope, treat as
        # satisfied so we never FAIL on a keyword we deliberately do not model.
        return True
    return isinstance(value, expected)


def _validate_schema(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    """Validate `value` against a JSON-Schema SUBSET. Returns a list of human
    readable violation strings (empty == valid).

    Supported keywords: `type` (primitive names + object/array), `properties`
    (recursive, for objects), and `required`. Everything else is ignored.
    """
    errors: list[str] = []

    type_name = schema.get("type")
    if isinstance(type_name, str) and not _type_matches(value, type_name):
        actual = "null" if value is None else type(value).__name__
        errors.append(f"{path or '<root>'}: expected type {type_name!r}, got {actual}")
        # If the type is wrong there is no point recursing into properties.
        return errors

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if key not in value:
                    errors.append(f"{path or '<root>'}: missing required property {key!r}")

        properties = schema.get("properties")
        if isinstance(properties, dict):
            for key, sub_schema in properties.items():
                if key in value and isinstance(sub_schema, dict):
                    child_path = f"{path}.{key}" if path else key
                    errors.extend(_validate_schema(value[key], sub_schema, child_path))

    return errors


class ArtifactCompletenessGrader(Grader):
    """Score an artifact-producing agent on completeness of its output.

    Deterministic verdict from schema validity + required-section presence;
    an optional injected judge contributes advisory-only quality signal.
    """

    def __init__(
        self,
        name: str = "artifact_completeness",
        *,
        quality_judge: LLMJudgeGrader | None = None,
    ) -> None:
        if not name:
            raise ValueError("grader name must be non-empty")
        self.name = name
        self._quality_judge = quality_judge

    async def grade(self, context: GraderContext) -> GradeResult:
        expected: Expected = context.case.expected
        artifact = context.response.structured_output if context.response else None

        # Advisory quality signal is computed up front so it can ride along on
        # every return path (including FAIL) as a weight-0 child / detail.
        quality_child, quality_detail = await self._run_quality_judge(context)

        if _is_empty_artifact(artifact):
            return self._result(
                label=GradeLabel.FAIL,
                reason="artifact is empty: structured_output missing or has no keys",
                score=0.0,
                failure_modes=["empty_artifact"],
                section_children=[],
                quality_child=quality_child,
                quality_detail=quality_detail,
            )
        assert artifact is not None  # narrowed by _is_empty_artifact above

        schema_errors: list[str] = []
        if expected.output_schema is not None:
            schema_errors = _validate_schema(artifact, expected.output_schema, "")
        if schema_errors:
            return self._result(
                label=GradeLabel.FAIL,
                reason="artifact does not satisfy output_schema: " + "; ".join(schema_errors),
                score=0.0,
                failure_modes=["schema_invalid"],
                section_children=[],
                quality_child=quality_child,
                quality_detail=quality_detail,
            )

        section_children: list[BreakdownNode] = []
        missing: list[str] = []
        for section in expected.required_sections:
            present = _is_non_empty_value(artifact.get(section))
            if not present:
                missing.append(section)
            section_children.append(
                BreakdownNode(
                    key=f"section:{section}",
                    label=GradeLabel.PASS if present else GradeLabel.FAIL,
                    score=1.0 if present else 0.0,
                    reason=("present" if present else "missing or empty"),
                    failure_modes=[] if present else ["missing_section"],
                )
            )

        total = len(expected.required_sections)
        if missing:
            present_count = total - len(missing)
            score = present_count / total if total else 1.0
            return self._result(
                label=GradeLabel.PARTIAL,
                reason="schema valid but missing required sections: " + ", ".join(missing),
                score=score,
                failure_modes=["missing_section"],
                section_children=section_children,
                quality_child=quality_child,
                quality_detail=quality_detail,
            )

        reason = (
            "artifact complete: schema valid and all required sections present"
            if total
            else "artifact non-empty and schema valid (no required sections declared)"
        )
        return self._result(
            label=GradeLabel.PASS,
            reason=reason,
            score=1.0,
            failure_modes=[],
            section_children=section_children,
            quality_child=quality_child,
            quality_detail=quality_detail,
        )

    async def _run_quality_judge(
        self, context: GraderContext
    ) -> tuple[BreakdownNode | None, dict[str, Any] | None]:
        if self._quality_judge is None:
            return None, None
        judged = await self._quality_judge.grade(context)
        detail = {
            "advisory": True,
            "grader": judged.grader,
            "label": judged.label.value,
            "reason": judged.reason,
            "score": judged.score,
            "confidence": judged.confidence,
        }
        node = BreakdownNode(
            key=f"quality:{judged.grader}",
            label=judged.label,
            score=judged.score,
            weight=0.0,
            reason=judged.reason,
            failure_modes=list(judged.failure_modes),
        )
        return node, detail

    def _result(
        self,
        *,
        label: GradeLabel,
        reason: str,
        score: float,
        failure_modes: list[str],
        section_children: list[BreakdownNode],
        quality_child: BreakdownNode | None,
        quality_detail: dict[str, Any] | None,
    ) -> GradeResult:
        children = list(section_children)
        if quality_child is not None:
            children.append(quality_child)
        breakdown = BreakdownNode(
            key="artifact_completeness",
            label=label,
            score=score,
            reason=reason,
            failure_modes=list(failure_modes),
            children=children,
        )
        details: dict[str, Any] = {
            "required_sections": [c.key.removeprefix("section:") for c in section_children],
            "missing_sections": [
                c.key.removeprefix("section:")
                for c in section_children
                if c.label == GradeLabel.FAIL
            ],
        }
        if quality_detail is not None:
            details["quality"] = quality_detail
        return GradeResult(
            grader=self.name,
            label=label,
            reason=reason,
            score=score,
            failure_modes=list(failure_modes),
            details=details,
            breakdown=breakdown,
        )
