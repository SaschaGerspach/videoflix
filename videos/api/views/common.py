from __future__ import annotations

import logging
from django.core.exceptions import ValidationError

ERROR_RESPONSE_REF = {"$ref": "#/components/schemas/ErrorResponse"}
logger = logging.getLogger("videoflix")


def _format_validation_error(error: ValidationError) -> dict[str, list[str]]:
    if hasattr(error, "message_dict") and error.message_dict:
        return {
            key: [str(message) for message in messages]
            for key, messages in error.message_dict.items()
        }
    if hasattr(error, "messages") and error.messages:
        return {"non_field_errors": [str(message) for message in error.messages]}
    return {"non_field_errors": [str(error)]}
