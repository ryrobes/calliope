"""SQL text-judgment contracts implemented with typed Decisions questions.

These are new model implementations, not emulations of DeBERTa/Toxic-BERT
calibration. Version the questions and result mapping together for SQL caching.
"""

CONTRACT = "clover-text-judgments-v1"
ROUTES = ("sentiment", "nli", "nli3", "classify", "toxicity")
MODERATION = {
    "toxic": "Rude, hostile, abusive or threatening language directed at people.",
    "severe_toxic": "Extreme abuse, dehumanization or seriously threatening hostility.",
    "obscene": "Obscene or profane language.",
    "threat": "An expressed intention to harm or intimidate someone.",
    "insult": "An insulting or demeaning personal attack.",
    "identity_hate": "Hateful or demeaning language targeting a group identity.",
}
MODERATION_QUESTIONS = {
    "toxic": "Does the text attack, abuse or threaten a person or group? Criticism of a product or service alone is not a personal attack.",
    "severe_toxic": "Does the text express extreme abuse, dehumanization or serious threatened harm? An ordinary insult alone is not severe toxicity.",
    "obscene": "Does the text contain obscene or profane language? An insult without obscenity is not sufficient.",
    "threat": "Does the text express an intention to harm or intimidate someone? An insult alone does not express an intention to harm.",
    "insult": "Does the text contain a demeaning personal attack or insult? Product criticism alone is not an insult against a person.",
    "identity_hate": "Does the text express hatred or abuse targeting a group identity? An insult against an individual alone is not identity hate.",
}


def text_field(value, key):
    text = value.get(key)
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} must be nonempty text")
    return text


def labels_for(value):
    labels = value.get("labels")
    if isinstance(labels, str):
        labels = labels.split(",")
    if not isinstance(labels, list) or not all(isinstance(x, str) for x in labels):
        raise ValueError("labels must be an array of text or comma-separated text")
    labels = list(dict.fromkeys(x.strip() for x in labels if x.strip()))
    if not 1 <= len(labels) <= 200 or any(len(x) > 512 for x in labels):
        raise ValueError("Supply 1 to 200 distinct labels, each at most 512 characters")
    return labels


def choice(instructions, criteria):
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def prepare(capability, value):
    if isinstance(value, str):
        value = {"text": value}
    if not isinstance(value, dict):
        raise ValueError("Input must be text or an object")
    allowed = {"premise", "hypothesis"} if capability in ("nli", "nli3") else {"text"}
    if capability == "classify":
        allowed.add("labels")
    if set(value) - allowed:
        raise ValueError("Unsupported input fields for " + capability)
    if capability in ("nli", "nli3"):
        state = {key: text_field(value, key) for key in ("premise", "hypothesis")}
        criteria = {"entailment": "The hypothesis follows from the premise."}
        if capability == "nli":
            criteria["not_entailment"] = "The premise contradicts the hypothesis or does not provide enough information to establish it."
        else:
            criteria.update(
                contradiction="The premise and hypothesis cannot both be true in the same context.",
                neutral="The premise neither establishes nor contradicts the hypothesis.",
            )
        questions = {"result": choice("Determine the logical relation of the hypothesis to the premise. Interpret both as claims about the same entities or event at the same time unless explicitly stated otherwise. Compare their asserted facts; do not invent intervening events or missing facts.", criteria)}
    else:
        state = {"text": text_field(value, "text")}
        if capability == "sentiment":
            questions = {"result": choice("Classify the overall sentiment expressed in the text.", {
                "negative": "Dissatisfaction, criticism, disappointment or negative sentiment.",
                "neutral": "Factual or neutral text without a clear positive or negative sentiment.",
                "positive": "Satisfaction, praise, approval or positive sentiment.",
            })}
        elif capability == "toxicity":
            questions = {key: {"type": "noul", "instructions": MODERATION_QUESTIONS[key], "criteria": {"true": description, "false": "This category is absent from the text."}} for key, description in MODERATION.items()}
        elif capability == "classify":
            labels = labels_for(value)
            # Provider keys are stable identifiers; user labels can contain spaces,
            # Unicode and punctuation. Never discard labels to fit a choice limit.
            if 2 <= len(labels) <= 16:
                questions = {"result": choice("Select the category that best describes the subject or intent of the text.", {f"c{i}": label for i, label in enumerate(labels)})}
            else:
                questions = {f"c{i}": {"type": "noul", "instructions": "Does this category describe the subject or intent of the text?", "criteria": {"true": label, "false": "The text is unrelated to this category."}} for i, label in enumerate(labels)}
        else:
            raise ValueError("Unknown specialist")
    return {"state": state, "questions": questions}


def finish(capability, value, decision):
    answers = decision["answers"]
    # Scalar wrappers can discard the structured result; raw_usage is retained
    # in SQL sub-call receipts, so keep the actual provider model there too.
    usage = {**decision["usage"], "model": decision["model"], "contract": CONTRACT}
    result = {"model": decision["model"], "contract": CONTRACT, "usage": usage}
    if capability == "toxicity":
        scores = {key: answers[key]["noul"] for key in MODERATION}
        result.update(toxic=scores["toxic"] >= 0.5, score=max(scores.values()), scores=scores)
    else:
        if capability == "classify":
            labels = labels_for(value)
            if "result" in answers:
                scores = {label: answers["result"]["probabilities"][f"c{i}"] for i, label in enumerate(labels)}
                result["scoring"] = "choice"
            else:
                raw = [answers[f"c{i}"]["noul"] for i in range(len(labels))]
                total = sum(raw)
                if total == 0:
                    raise ValueError("No category has a nonzero score")
                scores = {label: raw[i] / total for i, label in enumerate(labels)}
                result["scoring"] = "normalized_independent"
        else:
            scores = answers["result"]["probabilities"]
        label = max(scores, key=scores.get)
        result.update(label=label, scores=scores)
        if capability == "sentiment":
            result["score"] = scores["positive"] - scores["negative"]
        elif capability == "classify":
            result["score"] = scores[label]
    return result
