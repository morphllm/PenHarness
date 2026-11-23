import os
from datetime import datetime
from typing import List

from openai import OpenAI

from resources.model_resource.model_provider import ModelProvider
from resources.model_resource.model_response import ModelResponse

# Any OpenAI-compatible gateway works; MORPH_BASE_URL lets the same provider class
# point at staging or a local proxy without editing code.
DEFAULT_MORPH_BASE_URL = "https://api.morphllm.com/v1"


class MorphModels(ModelProvider):
    """Morph's OpenAI-compatible endpoint.

    Deliberately mirrors XAIModels: chat completions with a configurable base URL,
    no /models preflight, and no proprietary request fields. Model ids keep their
    provider prefix in BountyBench's config but the gateway expects the bare id.
    """

    def __init__(self):
        self.client = self.create_client()

    def create_client(self) -> OpenAI:
        return OpenAI(
            api_key=self._api_key(),
            base_url=os.getenv("MORPH_BASE_URL", DEFAULT_MORPH_BASE_URL),
            timeout=600.0,
            max_retries=4,
        )

    def request(
        self,
        model: str,
        message: str,
        temperature: float,
        max_tokens: int,
        stop_sequences: List[str],
    ) -> ModelResponse:
        if "/" in model:
            model = model.split("/")[-1]

        start_time = datetime.now()
        status_code = None

        try:
            response = self.client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "user", "content": message},
                ],
                temperature=temperature,
                max_tokens=max_tokens,
                stop=stop_sequences,
            )

            if hasattr(response, "response") and hasattr(
                response.response, "status_code"
            ):
                status_code = response.response.status_code

            end_time = datetime.now()
            response_request_duration = (end_time - start_time).total_seconds() * 1000

            # Reasoning models can return content=None when the output budget is
            # exhausted inside the reasoning block. Callers run string methods on
            # this value (remove_hallucinations), so normalize to "".
            content = response.choices[0].message.content or ""

            return ModelResponse(
                content=content,
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                time_taken_in_ms=response_request_duration,
                status_code=status_code,
            )
        except Exception as e:
            try:
                if hasattr(e, "status_code"):
                    status_code = e.status_code
                elif hasattr(e, "response") and hasattr(e.response, "status_code"):
                    status_code = e.response.status_code
            except Exception:
                pass

            if status_code is not None:
                e.status_code = status_code
            raise

    # Token counting for context truncation is done with tiktoken by
    # model_utils, which always instantiates OpenAIModels for that job, so these
    # three are never reached for Morph models.
    def tokenize(self, model: str, message: str) -> List[int]:
        raise NotImplementedError("Tokenization is not supported for Morph models")

    def decode(self, model: str, tokens: List[int]) -> str:
        raise NotImplementedError("Decoding tokens is not supported for Morph models")

    def get_num_tokens(self, model: str, message: str) -> int:
        raise NotImplementedError(
            "Getting number of tokens is not supported for Morph models"
        )
