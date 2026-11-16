"""Disabled clients have no transport, credentials or paid/free provider calls."""
from types import SimpleNamespace


class ProviderDisabled(RuntimeError):
    pass


class DisabledOpenAI:
    def __init__(self, *args, **kwargs):
        self.embeddings = SimpleNamespace(create=self.blocked)
        self.responses = SimpleNamespace(create=self.blocked, parse=self.blocked)

    async def blocked(self, *args, **kwargs):
        raise ProviderDisabled("Demo provider is disabled")

    def __getattr__(self, name):
        raise ProviderDisabled("Demo provider is disabled")


def install_disabled_provider():
    # Process-local replacement, before existing ingestion/generation imports.
    import openai
    for name in ("OpenAI", "AsyncOpenAI", "AzureOpenAI", "AsyncAzureOpenAI"):
        setattr(openai, name, DisabledOpenAI)
