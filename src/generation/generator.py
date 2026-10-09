import os
import re

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel


class GeneratedAnswer(BaseModel):
    answer: str
    answered: bool
    citation_ids: list[int]


load_dotenv()

client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY")
)


ABSTENTION_MESSAGE = (
    "I don't have enough information in the provided sources "
    "to answer this question."
)

MAX_GENERATION_ATTEMPTS = 2


SYSTEM_INSTRUCTIONS = """
You are an enterprise document assistant.

Rules:
- Answer the user's question using only the provided sources.
- Do not use information that is not supported by the sources.
- Sources are labeled [SOURCE 1], [SOURCE 2], etc.
- When citing a source, use [1], [2], etc. in the answer.
- Place citations directly after the claims they support.
- Every factual answer must contain at least one inline citation.
- Do not cite a source that does not support the claim.
- Do not invent citations.
- Include in citation_ids only the source IDs actually cited in the answer.
- Do not include source IDs that are not cited in the answer.
- Set answered to true only if the provided sources contain enough
  information to answer the question.
- If the sources do not contain enough information, set answered to false,
  set citation_ids to an empty list, and use this exact answer:
  "I don't have enough information in the provided sources to answer this question."
""".strip()


def build_user_input(query, context):
    """
    Build the user input containing the question and retrieved sources.
    """
    if not query or not query.strip():
        raise ValueError("query cannot be empty")

    if not context or not context.strip():
        raise ValueError("context cannot be empty")

    return f"""
Question:
{query}

Sources:
{context}
""".strip()


def extract_citation_ids(answer):
    """
    Extract inline citation IDs from the generated answer.

    Example:
        "Employees receive 500 USD [1]."
        -> {1}
    """
    return {
        int(value)
        for value in re.findall(r"\[(\d+)\]", answer)
    }


def extract_available_source_ids(context):
    """
    Extract source IDs from formatted RAG context.

    Example:
        "[SOURCE 1]\\nProvenance:..."
        -> {1}
    """
    return {
        int(value)
        for value in re.findall(
            r"(?m)^\[SOURCE[ \t]+(\d+)\][ \t]*$",
            context,
        )
    }


def get_citation_validation_errors(
    generation,
    available_source_ids,
):
    """
    Return detailed citation consistency validation errors.
    """
    errors = []

    answer = generation.answer.strip()
    inline_ids = extract_citation_ids(answer)
    declared_ids = set(generation.citation_ids)

    if not generation.answered:
        if answer != ABSTENTION_MESSAGE:
            errors.append(
                "The abstention answer must match the exact "
                "required abstention message."
            )

        if inline_ids or declared_ids:
            errors.append(
                "An abstention response must not contain citations."
            )

        return errors

    if not inline_ids:
        errors.append(
            "The factual answer contains no inline citations. "
            "Add citations such as [1] immediately after "
            "the claims they support."
        )

    if inline_ids != declared_ids:
        errors.append(
            f"Inline citation IDs {sorted(inline_ids)} "
            f"do not match declared citation_ids "
            f"{sorted(declared_ids)}."
        )

    unknown_ids = inline_ids - available_source_ids

    if unknown_ids:
        errors.append(
            f"Unknown source IDs: {sorted(unknown_ids)}. "
            f"Available IDs: {sorted(available_source_ids)}."
        )

    return errors


def validate_generated_answer(
    generation,
    available_source_ids,
):
    """
    Validate citation consistency and abstention behavior.
    """
    return not get_citation_validation_errors(
        generation,
        available_source_ids,
    )


def print_validation_debug(
    generation,
    available_source_ids,
    attempt,
    validation_errors,
):
    """
    Print diagnostic information when citation validation fails.

    Intended for local development, not production logging.
    """
    answer = generation.answer.strip()

    print(
        f"\nCitation validation failed "
        f"(attempt {attempt}/{MAX_GENERATION_ATTEMPTS})"
    )

    print("Generated answer:", repr(generation.answer))
    print("Answered:", generation.answered)
    print("Declared citation IDs:", generation.citation_ids)
    print(
        "Inline citation IDs:",
        sorted(extract_citation_ids(answer)),
    )
    print(
        "Available source IDs:",
        sorted(available_source_ids),
    )

    for error in validation_errors:
        print(f"Validation reason: {error}")


async def generate_answer(query, context, model):
    """
    Generate a grounded answer and validate citation consistency.

    Retry once with targeted validation feedback if needed.
    """
    base_user_input = build_user_input(
        query=query,
        context=context,
    )

    available_source_ids = extract_available_source_ids(context)

    if not available_source_ids:
        raise ValueError(
            "No valid [SOURCE n] headers found in context"
        )

    user_input = base_user_input

    for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
        response = await client.responses.parse(
            model=model,
            instructions=SYSTEM_INSTRUCTIONS,
            input=user_input,
            text_format=GeneratedAnswer,
        )

        generation = response.output_parsed

        if generation is None:
            raise ValueError(
                "Model returned no parsed generation"
            )

        validation_errors = get_citation_validation_errors(
            generation,
            available_source_ids,
        )

        if not validation_errors:
            return generation

        print_validation_debug(
            generation=generation,
            available_source_ids=available_source_ids,
            attempt=attempt,
            validation_errors=validation_errors,
        )

        if attempt < MAX_GENERATION_ATTEMPTS:
            error_details = "\n".join(
                f"- {error}"
                for error in validation_errors
            )

            user_input = base_user_input + f"""

Your previous response failed citation validation.

Previous answer:
{generation.answer}

Previous answered value:
{generation.answered}

Previous citation_ids:
{generation.citation_ids}

Validation errors:
{error_details}

Regenerate the response and correct these specific errors.

Requirements:
- Every factual answer must contain inline citations.
- Place each citation directly after the claim it supports.
- The citation_ids field must exactly match the IDs
  appearing in the answer text.
- Only cite source IDs present in the provided context.
- Only cite sources that actually support the claims.
- Do not invent source IDs or unsupported information.
- If the sources do not support an answer, abstain
  using the required exact message.
"""

    raise ValueError(
        "Generation failed citation consistency validation "
        f"after {MAX_GENERATION_ATTEMPTS} attempts"
    )
