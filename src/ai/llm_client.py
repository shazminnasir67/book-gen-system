"""
src/ai/llm_client.py
====================
Thin wrapper around the Google Gemini SDK for the Book Generation System.

Responsibilities:
  - Instantiate and hold the Gemini client.
  - Provide a single `complete()` method used by every stage.
  - Implement retry logic via `tenacity` for transient API errors.
  - Log token usage and latency for every call.

No prompt construction happens here — callers pass fully-rendered strings.
"""

import logging
import time
from typing import Optional

from google import genai
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from src.config import Config

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Wrapper around the Google Gemini API.

    Provides a single `complete()` method that handles retries, logging,
    and model configuration. All stage modules interact with the LLM through
    this class.
    """

    def __init__(self, config: Config) -> None:
        """
        Initialise the Gemini client with credentials from config.

        Args:
            config: Application configuration containing the API key,
                    model name, and LLM tuneable parameters.
        """
        self._config = config
        self._client = genai.Client(api_key=config.gemini_api_key)
        logger.info("LLMClient initialised — model=%s", config.gemini_model)

    def complete(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """
        Send a prompt to the Gemini model and return the text response.

        Retries up to `config.llm_max_retries` times on transient API errors
        using exponential back-off.

        Args:
            user_prompt: Fully rendered user-turn message.
            system_prompt: Optional override for the system instruction.
                           Defaults to the value in `src/ai/prompts.SYSTEM_PROMPT`.
            max_tokens: Override the default max_tokens from config.
            temperature: Override the default temperature from config.

        Returns:
            str: The model's text response.

        Raises:
            Exception: If all retries are exhausted.
        """
        from src.ai.prompts import SYSTEM_PROMPT  # local import avoids circular deps

        effective_system = system_prompt or SYSTEM_PROMPT
        effective_max_tokens = max_tokens or self._config.llm_max_tokens
        effective_temperature = temperature if temperature is not None else self._config.llm_temperature

        return self._call_with_retry(
            user_prompt=user_prompt,
            system_prompt=effective_system,
            max_tokens=effective_max_tokens,
            temperature=effective_temperature,
        )

    def _call_with_retry(
        self,
        user_prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Internal method that performs the actual API call with tenacity retry.

        Args:
            user_prompt: Rendered user message.
            system_prompt: System instruction.
            max_tokens: Token budget for completion.
            temperature: Sampling temperature.

        Returns:
            str: Extracted text from the response.
        """
        max_retries = self._config.llm_max_retries
        wait_seconds = self._config.llm_retry_wait_seconds

        @retry(
            stop=stop_after_attempt(max_retries),
            wait=wait_exponential(multiplier=wait_seconds, min=wait_seconds, max=wait_seconds * 4),
            retry=retry_if_exception_type((Exception,)),  # Gemini uses generic exceptions
            before_sleep=before_sleep_log(logger, logging.WARNING),
            reraise=True,
        )
        def _inner() -> str:
            start = time.perf_counter()
            logger.debug("LLM call — model=%s max_tokens=%s", self._config.gemini_model, max_tokens)

            # Combine system prompt with user prompt for Gemini
            full_prompt = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt

            # Configure generation
            config_dict = {
                "max_output_tokens": max_tokens,
                "temperature": temperature,
            }

            response = self._client.models.generate_content(
                model=self._config.gemini_model,
                contents=full_prompt,
                config=config_dict,
            )

            elapsed = time.perf_counter() - start
            
            # Log token usage if available
            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage = response.usage_metadata
                logger.info(
                    "LLM response received — elapsed=%.2fs input_tokens=%s output_tokens=%s",
                    elapsed,
                    usage.prompt_token_count,
                    usage.candidates_token_count,
                )
            else:
                logger.info("LLM response received — elapsed=%.2fs", elapsed)

            text = response.text
            if not text:
                raise ValueError("LLM returned an empty response")
            return text

        return _inner()
