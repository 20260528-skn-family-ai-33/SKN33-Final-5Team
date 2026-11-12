"""Per-clause file timing shared by the upload precheck and runtime-input planning.

A request can mention several files with different timings: "방금 첨부한 계약서 PDF를
분석해줘. 비교용 견적서는 실행할 때 첨부할게요." needs the contract now and records the
quote as an execution input. Timing is therefore judged per clause and per file
mention, never from a cue found anywhere in the whole message.

The vocabulary below describes time anchors (execution time, now), delivery verbs and
file nouns. It is not a list of example sentences; paraphrases that use the same
anchors ("작업을 돌리는 시점에 자료를 넘기겠습니다") reach the same decision.
Quoted text is data the user wants to process, not a timing statement.
"""
import re

_QUOTED = re.compile(r'"[^"\n]*"|“[^”\n]*”|\'[^\'\n]*\'|‘[^’\n]*’|「[^」\n]*」|『[^』\n]*』')
EMAIL_ATTACHMENT = re.compile(r'(?:Gmail|메일)(?:의|에|에서)\s*(?:본문\s*(?:과|및)\s*)?첨부(?:한|된)?\s*파일', re.IGNORECASE)

# Past-tense reference to something the user believes is already attached.
UPLOADED = re.compile(r'(올려\s*준|올려\s*드린|올린|첨부(한|된|해\s*준)|첨부\s*파일|첨부파일|업로드(한|된|해\s*준)|보내\s*준|보낸(?!\s*사람)|붙여\s*준|넣어\s*준|넣은)')
OBJECT = re.compile(r'(파일|이미지|사진|영수증|문서|자료|스캔|엑셀|pdf|csv|xlsx|표)', re.IGNORECASE)
# Document nouns that name a file without a format word ("비교용 견적서는 실행할 때 ...").
_DOCUMENT = re.compile(
    r'(계약서|견적서|보고서|신청서|명세서|청구서|송장|인보이스|이력서|공문|회의록|문서|파일|자료|영수증|이미지|사진|스캔|'
    r'엑셀|표|pdf|csv|xlsx|docx|hwp|txt)', re.IGNORECASE)
_FORMAT_WORDS = ('pdf', 'csv', 'xlsx', 'docx', 'hwp', 'txt', '엑셀', '이미지', '사진', '표', '파일', '문서', '자료')
GENERIC_SUBJECTS = ('파일', '자료', '문서')
# A file noun used as the name of a tool or node, e.g. "업로드된 파일 도구".
# "첨부파일 없이", "첨부한 파일은 없어요": the user says there is no file.
_ABSENT = re.compile(r'^\s*(?:은|는|이|가|도)?\s*(?:아직\s*)?(?:없이|없어|없는|없고|없습니다|없음|없으|없다|안\s*(?:올렸|넣었|첨부))')
_TOOL_NAME = re.compile(r'^\s*(?:읽기\s*)?(?:도구|툴|노드|기능|tool)', re.IGNORECASE)

# Runtime cues that make a past-tense upload marker describe a future run.
_LATER = re.compile(r'(실행\s*(할\s*때|시(?![간작각]))|나중에|매일|매주|매월|예약|올리면|올릴|업로드하면|넣으면|넣을|받으면|들어오면)')
# Time anchors for "when the workflow runs".
EXECUTION_TIME = re.compile(
    r'실행\s*(?:할|하는|될|되는)?\s*(?:때|시점|순간)|실행\s*시(?![간작각])|'
    r'(?:돌리|구동하|가동하|수행하|작동하|실행하)(?:는|던)\s*(?:때|시점|순간)|'
    r'(?:돌릴|구동할|가동할|수행할|작동할)\s*(?:때|시점|순간)|'
    r'나중에|추후에?|매번\s*실행')
# "워크플로우 입력은 계약서 PDF 1개로 설정" declares a run input without a delivery verb.
INPUT_DECLARATION = re.compile(r'(?:워크플로우|실행|작업|에이전트|자동화)\s*(?:의\s*)?입력\s*(?:은|는|으로|을|를|값은|값으로|파일은)')
# A future/intent form of handing over a file.
DELIVERY = re.compile(
    r'(?:넣을|넣겠|넣어\s*(?:줄|드릴)|올릴|올리겠|올려\s*(?:줄|드릴)|첨부할|첨부하겠|첨부해\s*(?:줄|드릴)|'
    r'업로드할|업로드하겠|업로드해\s*(?:줄|드릴)|제공할|제공하겠|제공해\s*(?:줄|드릴)|넘길|넘기겠|넘겨\s*(?:줄|드릴)|'
    r'보낼|보내겠|보내\s*(?:줄|드릴)|전달할|전달하겠|전달해\s*(?:줄|드릴)|입력할|입력하겠|입력해\s*(?:줄|드릴)|'
    r'주겠|줄게|드릴게|드리겠|받을|받겠|받도록|입력받)')
NEGATIVE = re.compile(r'(?:넣|올리|첨부하|업로드하|제공하|입력하|넘기|보내|전달하)지\s*(?:않|말|못)|(?:안|못)\s*(?:넣|올|첨부|업로드|제공|입력|넘기|보내|전달)')
NOW_DELIVERY = re.compile(r'(?:지금|먼저|바로|설계\s*(?:할\s*때|시)).{0,30}(?:넣을|올릴|첨부할|업로드할|제공할|넘길|보낼|전달할|넣겠|올리겠|첨부하겠|업로드하겠|제공하겠)')
NEW_OR_IMMEDIATE_UPLOAD = re.compile(
    r'(?:지금|방금)\s*(?:새로\s*)?(?:첨부|업로드|올리|올렸|보내|보냈|넣|제공)|'
    r'실행\s*(?:할\s*때|시)\s*(?:말고|가?\s*아니라).{0,50}지금|'
    r'(?:새로|추가로)\s*(?:첨부|업로드|올린|올려|보낸)|'
    r'(?:새(?:로운)?|다른|추가)\s*(?:파일|자료|문서|PDF|CSV|XLSX|엑셀|영수증|사진|이미지)', re.IGNORECASE)
_IMMINENT = re.compile(r'올려\s*줄\s*(?:게|테)|올릴\s*테|첨부해\s*줄\s*(?:게|테)')
_IMMINENT_LATER = re.compile(r'실행\s*(?:할\s*때|시(?![간작각]))|나중에|예약')
_CLAUSE = re.compile(r'[.!?？。;\n]+|,\s*|(?<=하)고\s+|(?<=해)서\s+|(?<=한)\s*(?:뒤|후)(?:에)?\s+')
_PARTICLE = re.compile(r'(?:은|는|이|가|을|를|도|의|와|과|로|으로|만|에서|에)$')


def strip_quotes(text):
    return _QUOTED.sub(' ', text or '')


def clauses(text):
    text = EMAIL_ATTACHMENT.sub('메일', strip_quotes(text))
    return [c for c in (part.strip() for part in _CLAUSE.split(text)) if c]


def _compact(value):
    return re.sub(r'\s+', '', value).casefold()


def upload_references(text):
    """Subjects named after an upload marker ("첨부한 계약서 PDF" -> 계약서pdf)."""
    text = EMAIL_ATTACHMENT.sub('메일', text)
    result = []
    for marker in UPLOADED.finditer(text):
        # "첨부파일" is itself an upload marker. Its "파일" must not hide
        # the named document that follows, such as 계약서 PDF / 견적서 PDF.
        fragment = text[marker.end():marker.end() + 60]
        if OBJECT.search(marker.group(0)) and _ABSENT.match(fragment):
            continue
        target = OBJECT.search(fragment)
        if target is not None and _ABSENT.match(fragment[target.end():]):
            continue
        if target is None:
            if OBJECT.search(marker.group(0)):
                result.append('파일')
            continue
        if _TOOL_NAME.match(fragment[target.end():]):
            continue  # "업로드된 파일 도구" names a tool, not an attached file
        subject = fragment[:target.end()]
        if OBJECT.search(marker.group(0)):
            subject = re.sub(r'^(?:인|은|는|의|을|를)\s+', '', subject)
        subject = _compact(subject) or target.group(0).casefold()
        result.append(subject)
    return result


def _named_subjects(clause):
    """File subjects in a clause without an upload marker ("비교용 견적서는" -> 견적서)."""
    words = clause.split()
    result = []
    index = 0
    while index < len(words):
        word = _PARTICLE.sub('', words[index])
        found = _DOCUMENT.search(word)
        if not found or _TOOL_NAME.match(' '.join(words[index + 1:index + 2])):
            index += 1
            continue
        # "PDF 계약서" is the same file as "계약서 PDF": a bare format word before a named
        # document belongs to it, so both orders give one subject (계약서pdf).
        nxt = _PARTICLE.sub('', words[index + 1]) if index + 1 < len(words) else ''
        if (word.casefold() in _FORMAT_WORDS and not _PARTICLE.search(words[index]) and nxt
                and _DOCUMENT.search(nxt) and nxt.casefold() not in _FORMAT_WORDS):
            result.append(_compact(nxt[_DOCUMENT.search(nxt).start():] + word))
            index += 2
            continue
        parts = [word[found.start():]]
        # Continue through attached format words: "계약서 PDF" is one subject.
        while (index + 1 < len(words) and _PARTICLE.sub('', words[index + 1]).casefold() in _FORMAT_WORDS
               and not _PARTICLE.search(words[index])):
            index += 1
            parts.append(_PARTICLE.sub('', words[index]))
        result.append(_compact(''.join(parts)))
        index += 1
    return result


def _deferral(clause):
    if NEGATIVE.search(clause) or re.search(r'(?:말고|아니라).{0,50}지금', clause):
        return False
    if EXECUTION_TIME.search(clause) and DELIVERY.search(clause):
        return True
    return bool(INPUT_DECLARATION.search(clause) and _DOCUMENT.search(clause))


def clause_choices(text):
    """[(choice, subjects)] in order. choice: 'execution' | 'now' | 'negative' | None."""
    result = []
    for clause in clauses(text):
        if _deferral(clause):
            result.append(('execution', _named_subjects(clause)))
        elif NEGATIVE.search(clause) and (DELIVERY.search(clause) or EXECUTION_TIME.search(clause)
                                         or re.search(r'넣|올리|첨부|업로드|제공|입력', clause)):
            result.append(('negative', _named_subjects(clause)))
        elif NOW_DELIVERY.search(clause) or NEW_OR_IMMEDIATE_UPLOAD.search(clause):
            result.append(('now', upload_references(clause) or _named_subjects(clause)))
        else:
            result.append((None, []))
    return result


def delivery_choice(text):
    """The message's last decisive file-delivery choice, or None."""
    choice = None
    for value, _ in clause_choices(text):
        if value:
            choice = value
    return choice


def now_mentions(text):
    """Files the clause says are already attached (or about to be) for this design turn."""
    result = []
    for clause in clauses(text):
        if _deferral(clause):
            continue
        refs = upload_references(clause)
        if refs and not _LATER.search(clause):
            result.extend(refs)
        elif _IMMINENT.search(clause) and not _IMMINENT_LATER.search(clause) and OBJECT.search(clause):
            result.extend(_named_subjects(clause) or ['파일'])
    return result


def refers_to_attached_file(text):
    return bool(now_mentions(text))


def same_subject(subject, deferred):
    """Whether one mention refers to one recorded runtime file, without guessing between several."""
    if subject in deferred:
        return True
    # "계약서" and "계약서 PDF" name the same file when the remainder is only a format word.
    if sum(1 for d in deferred if _format_variant(subject, d)) == 1:
        return True
    if subject in GENERIC_SUBJECTS and len(deferred) == 1:
        return True
    if subject in _FORMAT_WORDS and sum(d.endswith(subject) for d in deferred) == 1:
        return True
    return False


def _format_variant(left, right):
    short, long = sorted((left, right), key=len)
    return bool(short) and long != short and long.startswith(short) and long[len(short):] in _FORMAT_WORDS


def deferred_subjects(users):
    """Runtime files recorded across user turns, oldest to newest."""
    previous_refs = []
    deferred = set()
    for text in users:
        refs = upload_references(text) or [s for c, subjects in clause_choices(text) if c != 'execution' for s in subjects]
        if NEW_OR_IMMEDIATE_UPLOAD.search(strip_quotes(text)) and not any(c == 'execution' for c, _ in clause_choices(text)):
            deferred = set()
            previous_refs = []
        for choice, subjects in clause_choices(text):
            if choice == 'execution':
                # "실행할 때 PDF를 첨부할게요" after "첨부한 계약서 PDF" refers back to that file.
                resolved = set()
                for subject in subjects:
                    if subject in _FORMAT_WORDS and previous_refs:
                        matches = [r for r in previous_refs if same_subject(subject, {r})]
                        resolved |= set(matches if matches else [subject])
                    else:
                        resolved.add(subject)
                deferred |= resolved or set(refs or previous_refs)
            elif choice == 'negative':
                deferred -= set(subjects or previous_refs)
        if refs and deferred and not any(c == 'execution' for c, _ in clause_choices(text)):
            if not all(same_subject(r, deferred) for r in refs):
                deferred = set()
        if refs:
            previous_refs = refs
    return deferred
