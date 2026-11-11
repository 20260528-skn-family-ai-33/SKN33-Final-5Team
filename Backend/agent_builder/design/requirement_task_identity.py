"""Resolve copies of the same requested output or schedule before assigning task IDs."""
import re
from collections import defaultdict

from agent_builder.conditions.condition_readiness import schedule_literals


# A shared format alone is insufficient evidence. A user may ask for a file
# per group, a copy in another destination, or both original and processed data.
_MULTIPLE_TARGETS = re.compile(
    r"각각|각기|별도|따로|마다|복사|사본|두\s*(?:개|곳|파일|폴더)|여러|"
    r"둘\s*다|양쪽|모두에|및.{0,30}(?:저장|보관)|"
    r"(?:와|과|랑)\s*\S+(?:에|로)\s*(?:저장|내보내|보내|전송)|"
    r"\b(?:each|per|both|separately|copies|destinations|respectively)\b|"
    r"\b(?:folders?|directories|buckets?)\b|폴더|디렉터리|버킷",
    re.I,
)
_NAMED_FILE = re.compile(r"[\w.-]+\.(?:json|csv|md|xlsx|pdf)(?![a-z\d_])", re.I)
_OUTPUT_VERB = re.compile(r"저장|내보내|내보낸|보관|출력|다운로드|"
                          r"\b(?:sav(?:e|ing)|writ(?:e|ing)|export(?:ing)?|archiv(?:e|ing))\b", re.I)
_SCHEDULE_REPLY_WORDS = re.compile(
    r"(?:[\s.,!?~]|정각|정시|에|으로|로|를|은|는|실행|예약|설정|해\s*줘|"
    r"해\s*주세요|하세요|해요|할게요|입니다|이요|요|네)+"
)


def _artifact_position(text, file_format):
    if _MULTIPLE_TARGETS.search(text) or _NAMED_FILE.search(text):
        return None
    # One format mention can qualify several output clauses. In particular an
    # original and a filtered copy have different upstream dependencies.
    if len(_OUTPUT_VERB.findall(text)) > 1:
        return None
    token = rf"(?<![a-z0-9_]){re.escape(file_format)}(?![a-z0-9_])"
    if len(re.findall(token, text, re.I)) != 1:
        return None
    # The final-result form also establishes which side of any intermediate
    # transformation to keep when a model proposes a premature export.
    if re.search(
        rf"(?:결과|결과물|행|항목)(?:만|만을)\s*(?:하나의\s*)?{token}|"
        rf"\bonly\s+the\s+(?:final\s+)?result\s+(?:to|as)\s+(?:(?:one|a single)\s+)?{token}",
        text, re.I,
    ):
        return "final"
    if re.fullmatch(
        rf"\s*(?:이\s*|해당\s*|최종\s*|연결한\s*)?(?:결과물?|표)(?:를|을)\s*"
        rf"{token}\s*(?:파일)?(?:로|으로)\s*(?:저장|내보내)\s*(?:해\s*)?(?:줘|주세요|세요)?[.!\s]*|"
        rf"\s*(?:save|export)\s+the\s+(?:result|table)\s+(?:as|to)\s+{token}[.!\s]*",
        text, re.I,
    ):
        return "single"
    if not re.search(
        rf"{token}\s*(?:파일|문서)\s*(?:하나|한\s*개)|"
        rf"(?:하나의|한\s*개의)\s*{token}\s*(?:파일|문서)|"
        rf"\b(?:one|a single)\s+{token}\s+file\b",
        text, re.I,
    ):
        return None
    if re.search(rf"원본(?:을|만)\s*{token}|\b(?:original|raw)\s+data\s+as\s+one\s+{token}", text, re.I):
        return "original"
    return "single"


def _schedule_answer(state, turn, answer):
    # condition_answers.turn indexes the complete dialogue. Candidate
    # source_message indexes user turns only.
    slots = {
        key for event in state.get("condition_answers", [])
        if event.get("turn") == turn and event.get("answer") == answer and event.get("question_ids")
        for key in event.get("candidate_keys", [])
    }
    if not slots or not all(isinstance(key, str) and key.startswith("schedule.") for key in slots):
        return False
    literals = schedule_literals(answer, follow_up=True)
    if not any(key in slots and value != "conflict" for key, value, _ in literals):
        return False
    # A slot answer can introduce a second actual job in the same turn. Only a
    # pure temporal refinement is proof that it describes the original job.
    remaining = answer
    for span in sorted({span for _, _, span in literals}, key=len, reverse=True):
        remaining = remaining.replace(span, "")
    return not remaining or bool(_SCHEDULE_REPLY_WORDS.fullmatch(remaining))


def merge_repeated_tasks(candidates, state):
    """Keep uncertain cases. This operates only on model candidates without graph IDs."""
    users = [(turn, message["content"]) for turn, message in enumerate(state["messages"])
             if message.get("role") == "user"]
    exports = defaultdict(list)
    schedules = []
    removed = set()
    for index, item in enumerate(candidates):
        if not isinstance(item, dict) or set(item) != {"spec", "description", "source_message"}:
            continue
        source = item["source_message"]
        if (type(source) is not int or not 0 <= source < len(users)
                or not isinstance(item["description"], str) or not 1 <= len(item["description"]) <= 300
                or not isinstance(item["spec"], dict)):
            continue
        spec = item["spec"]
        if spec.get("kind") == "export" and set(spec) == {"kind", "format"}:
            fmt = spec["format"]
            if isinstance(fmt, str) and fmt in ("csv", "json", "md", "pdf", "xlsx"):
                exports[source, fmt].append(index)
        elif spec.get("kind") == "schedule":
            turn, answer = users[source]
            prior = [old for old in schedules if old["source_message"] < source]
            if len(prior) == 1 and prior[0]["spec"] == spec and _schedule_answer(state, turn, answer):
                removed.add(index)
            else:
                schedules.append(item)
    for (source, fmt), indices in exports.items():
        if len(indices) < 2:
            continue
        position = _artifact_position(users[source][1], fmt)
        if position == "original":
            removed.update(indices[1:])
        elif position == "final":
            removed.update(indices[:-1])
        elif position == "single":
            between = candidates[indices[0]+1:indices[-1]]
            if all(isinstance(task, dict) and isinstance(task.get("spec"), dict)
                   and task["spec"].get("kind") in ("export", "policy") for task in between):
                removed.update(indices[:-1])
    return [task for index, task in enumerate(candidates) if index not in removed]
