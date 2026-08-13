"""
Eval Dataset Schema — Pydantic v2 models for eval dataset YAML files.

See model class docstrings and field descriptions for the schema documentation.
"""

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# Separator for namespaced Langfuse item IDs (``{dataset}:{item}``).
# Langfuse dataset item IDs are project-wide unique (langfuse#2167), so we
# prefix the API ID with the dataset name to prevent collisions when two
# datasets use the same logical item ID (e.g. both have `id: happy-path`).
ID_SEPARATOR = ":"


class ExpectedOutput(BaseModel):
    """What the agent should have done, and how to score it.

    Fields:
        behavior: Prose describing expected agent behavior. Provides context
            to the judge LLM — not parsed programmatically.
        scoring_type: How criteria are evaluated. Only ``pass_fail`` is
            currently supported; future types (rubric, similarity, etc.)
            will be added as Literal alternatives.
        scoring_criteria: List of yes/no criterion strings. Each entry is
            a question the judge LLM evaluates against the agent's response.
        scoring_rules: Prose instructions for combining criterion results
            into a final score. Passed verbatim to the judge LLM — the
            harness does not interpret this field. This is what keeps
            scoring reproducible across runs. Example::

                Score 10 * (passed criteria / total criteria).
                Truncated responses cap at 3.

            Self-adjusts to any number of criteria — no need to update
            when criteria are added or removed.
    """

    model_config = ConfigDict(extra="forbid")

    behavior: str = Field(description="Prose description of expected agent behavior")
    scoring_type: Literal["pass_fail"] = Field(description="How criteria are evaluated")
    scoring_criteria: list[str] = Field(description="List of yes/no criterion strings")
    scoring_rules: str = Field(description="Prose scoring instructions for the judge LLM")

    @field_validator("behavior")
    @classmethod
    def behavior_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty or whitespace-only")
        return v

    @field_validator("scoring_rules")
    @classmethod
    def scoring_rules_must_be_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be empty or whitespace-only")
        return v

    @field_validator("scoring_criteria")
    @classmethod
    def criteria_must_be_non_empty_strings(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("scoring_criteria must contain at least one criterion")
        for i, criterion in enumerate(v):
            if not isinstance(criterion, str):
                raise ValueError(f"[{i}] must be a string, got {type(criterion).__name__}")
            if not criterion.strip():
                raise ValueError(f"[{i}] must not be empty or whitespace-only")
        return v


class EvalItem(BaseModel):
    """A single eval test case — one prompt with expected behavior and scoring.

    Fields:
        id: Unique identifier within this dataset. Must not contain ``:``
            (used for namespaced Langfuse item IDs).
        input: Prompt text sent to the agent under test. Use YAML literal
            block scalars (``|``) for multiline prompts.
        expected_output: Scoring guidance for the judge LLM. Accepts a flat
            string (legacy, passed verbatim) or an ``ExpectedOutput`` object
            (structured). Both formats can coexist in the same dataset.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique identifier within this dataset")
    input: str = Field(description="Prompt text sent to the agent under test")
    expected_output: Union[str, ExpectedOutput] = Field(
        description="Expected behavior and scoring rules (string or object)"
    )

    @field_validator("expected_output")
    @classmethod
    def validate_expected_output(cls, v: Union[str, "ExpectedOutput"]) -> Union[str, "ExpectedOutput"]:
        if isinstance(v, str) and not v.strip():
            raise ValueError("expected_output must not be empty or whitespace-only")
        return v

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("id must not be empty or whitespace-only")
        if ID_SEPARATOR in v:
            raise ValueError(f"id must not contain '{ID_SEPARATOR}' — used for namespaced Langfuse item IDs")
        return v.strip()

    @field_validator("input")
    @classmethod
    def validate_input(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("input must not be empty or whitespace-only")
        return v


class EvalFile(BaseModel):
    """Top-level eval.yaml schema.

    Fields:
        dataset: Langfuse dataset name. Must not contain ``:`` (used for
            namespaced Langfuse item IDs).
        description: Human-readable description of the dataset.
        items: List of eval test cases. Item IDs must be unique within
            the dataset.

    All models use ``extra="forbid"`` — unknown fields at any level are
    rejected, catching typos like ``datset:`` or ``expectedoutput:`` at
    parse time.

    ``timeout_per_run`` is optional (default 600s). It is not synced to
    Langfuse — it is read directly from eval.yaml by the execute skill
    during the Execute phase to compute the per-invocation exec timeout for
    the harness process: ``exec_timeout = timeout_per_run * repeat + 120``.
    Variants run sequentially in separate exec calls, each with its own timeout.

    ``agent`` is optional (default ``"main"``). It declares which OpenClaw
    agent the skill should be tested under. Skills reference agents, not
    models — when a new model comes out, you update the agent config once
    instead of updating all skills. The harness looks up the agent's primary
    model via ``openclaw agents list --json`` and passes it as ``--model``
    to the ``openclaw agent`` CLI command, ensuring no retry contamination.
    """

    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(description="Langfuse dataset name")
    description: str = Field(description="Human-readable description of the dataset")
    timeout_per_run: int = Field(
        default=600,
        gt=0,
        description="Estimated wall-clock seconds to run all eval items "
            "sequentially. The execute skill multiplies this by the repeat "
            "count to derive the per-invocation exec timeout for the "
            "harness process: exec_timeout = timeout_per_run * repeat + 120. "
            "Default 600 (10 minutes). Must be a positive integer.",
    )
    agent: str = Field(
        default="main",
        description="OpenClaw agent ID to use for testing (default: main). "
            "The harness looks up the agent's primary model via "
            "'openclaw agents list --json' and passes it as --model to "
            "the 'openclaw agent' CLI command. This ensures no retry "
            "contamination from model fallbacks.",
    )
    items: list[EvalItem] = Field(description="List of eval test cases")

    @field_validator("dataset")
    @classmethod
    def validate_dataset(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("dataset must not be empty or whitespace-only")
        if ID_SEPARATOR in v:
            raise ValueError(f"dataset must not contain '{ID_SEPARATOR}' — used for namespaced Langfuse item IDs")
        return v.strip()

    @field_validator("description")
    @classmethod
    def strip_description(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("description must not be empty or whitespace-only")
        return v.strip()

    @model_validator(mode="after")
    def check_duplicate_ids(self) -> "EvalFile":
        seen = set()
        for item in self.items:
            if item.id in seen:
                raise ValueError(f"duplicate item id '{item.id}' — ids must be unique")
            seen.add(item.id)
        return self
