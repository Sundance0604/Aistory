from gpt_activity.topics import TopicClassificationResult


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
