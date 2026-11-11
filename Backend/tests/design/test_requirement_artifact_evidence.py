"""Check output identity at the same model-candidate entry point used in production."""
from agent_builder.design.requirement_ledger import extract_plan


def extract(request, descriptions):
    state = {"messages": [{"role": "user", "content": request}], "resources": []}
    raw = {"tasks": [
        {"description": description, "spec": spec, "source_message": 0}
        for description, spec in descriptions
    ], "unresolved": []}
    return extract_plan(raw, state)["tasks"]


def test_one_csv_of_only_selected_rows_uses_the_final_dependency():
    csv = {"kind": "export", "format": "csv"}
    selection = {"kind": "select_fields", "fields": ["cost"]}
    tasks = extract("Select the cost column and save only the result to one CSV file.", [
        ("Export the selected column", csv), ("Select cost", selection), ("Write the file", csv)
    ])
    assert [task["spec"] for task in tasks] == [selection, csv]
    assert tasks[-1]["depends_on"] == [tasks[0]["id"]]


def test_one_file_per_destination_does_not_mean_one_artifact():
    csv = {"kind": "export", "format": "csv"}
    tasks = extract("Export one CSV file for each store, north and south.", [
        ("North store export", csv), ("South store export", csv)
    ])
    assert len(tasks) == 2


def test_singular_output_request_can_be_grounded_without_a_numeric_count():
    pdf = {"kind": "export", "format": "pdf"}
    tasks = extract("결과를 PDF로 저장해줘.", [
        ("PDF 내보내기", pdf), ("결과 저장", pdf)
    ])
    assert [task["spec"] for task in tasks] == [pdf]


def test_named_destination_in_same_turn_does_not_override_second_name():
    csv = {"kind": "export", "format": "csv"}
    tasks = extract("최종 결과만 north.csv와 south.csv에 저장해줘.", [
        ("north.csv 저장", csv), ("south.csv 저장", csv)
    ])
    assert len(tasks) == 2


def test_single_named_format_does_not_rebind_original_export_to_later_transform():
    csv = {"kind": "export", "format": "csv"}
    selection = {"kind": "select_fields", "fields": ["cost"]}
    tasks = extract("원본을 CSV 파일 하나로 저장한 다음 cost 열을 선택해줘.", [
        ("원본 저장", csv), ("열 선택", selection), ("결과 저장", csv)
    ])
    assert tasks[0]["spec"] == csv
    assert tasks[0]["depends_on"] == []


def test_original_and_selected_rows_requested_in_separate_clauses_keep_both_dependencies():
    csv = {"kind": "export", "format": "csv"}
    selection = {"kind": "select_fields", "fields": ["cost"]}
    tasks = extract("원본 표를 저장해. cost 열을 선택한 결과만 CSV로 저장해. 원본도 같은 형식이야.", [
        ("원본 표 저장", csv), ("cost 열 선택", selection), ("선택한 열 저장", csv),
    ])
    assert [task["spec"] for task in tasks] == [csv, selection, csv]
    assert tasks[0]["depends_on"] == []
    assert tasks[2]["depends_on"] == [tasks[1]["id"]]
