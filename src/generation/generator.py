import os

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


SYSTEM_INSTRUCTIONS = """
You are an enterprise document assistant.

Rules:
- Answer the user's question using only the provided sources.
- Do not use information that is not supported by the sources.
- Cite supporting sources using [1], [2], etc.
- Place citations directly after the claims they support.
- Do not cite a source that does not support the claim.
- Do not invent citations.
- Include in citation_ids only the source IDs actually cited in the answer.
- Do not include source IDs that are not cited in the answer.
- Set answered to true only if the provided sources contain enough
  information to answer the question.
- If the sources do not contain enough information, set answered to false,
  set citation_ids to an empty list, and use this answer:
  "I don't have enough information in the provided sources to answer this question."
""".strip()


def build_user_input(query, context):
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


async def generate_answer(query, context, model):
    user_input = build_user_input(
        query=query,
        context=context
    )

    response = await client.responses.parse(
        model=model,
        instructions=SYSTEM_INSTRUCTIONS,
        input=user_input,
        text_format=GeneratedAnswer
    )

    return response.output_parsed