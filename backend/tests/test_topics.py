import json
from pathlib import Path

from gpt_activity.config import load_settings
from gpt_activity.importer import import_json
from gpt_activity.topics import TopicClassificationResult, classify_prompts


FIXTURE = Path(__file__).parent / "fixtures" / "branching_conversation.json"


def test_topic_weights_are_normalized():
    result = TopicClassificationResult.model_validate(
        {
            "topics": [
                {"parent_topic": "Math", "topic": "Algebra", "weight": 0.8},
                {"parent_topic": "Philosophy", "topic": "Logic", "weight": 0.4},
            ],
            "confidence": 0.8,
        }
    )
    assert abs(sum(topic.weight for topic in result.topics) - 1) < 1e-9


def test_topic_classification_reports_global_progress(tmp_path, monkeypatch):
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps({"storage": {"database_path": "data/test.db", "raw_conversations_dir": "data/raw"}}), encoding="utf-8")
    settings = load_settings(config)
    import_json(settings, FIXTURE)

    class FakeClassifier:
        classifier_name = "fake"
        classifier_version = "fake:v1"

        def __init__(self, _settings):
            pass

        def classify(self, _request):
            return TopicClassificationResult.model_validate({
                "topics": [{"parent_topic": "Math", "topic": "Algebra", "weight": 1}],
                "confidence": 1,
            })

        def close(self):
            pass

    monkeypatch.setattr("gpt_activity.topics.DeepSeekTopicClassifier", FakeClassifier)
    progress = []
    result = classify_prompts(settings, progress_callback=progress.append)
    assert result["classified"] == result["queued"]
    assert progress[0]["processed"] == 0
    assert progress[-1]["processed"] == result["queued"]
    assert progress[-1]["percent"] == 100
