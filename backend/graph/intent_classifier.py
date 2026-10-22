from typing import get_args

from ..common.constants import PROJECT_ROOT
from .state import Intent

INTENTS = get_args(Intent)
MAX_LENGTH = 128
MODEL_PATH = PROJECT_ROOT / "checkpoint" / "intent_classify" / "bert_context_v3_continued"
