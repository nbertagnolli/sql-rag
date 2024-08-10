import json
from typing import Any, Dict, List

from sentence_transformers import SentenceTransformer


def model_fn(model_dir=None):
    model_name = "thenlper/gte-small"
    return SentenceTransformer(model_name)


def transform_fn(
    model: SentenceTransformer, input_data, content_type, accept
) -> List[List[Dict[str, Any]]]:
    data = json.loads(input_data)
    return model.encode(data["text"]).tolist()
