from typing import Union


default_reviewer_setting = {
    "is_benign": None,
    "is_knowledgeable": None,
    "is_responsible": None,
    "provides_numeric_rating": True,
}


def get_experiment_settings(
    paper_id: Union[int, str, None] = None,
    paper_decision: Union[str, None] = None,
    setting: dict = None,
    doc_id: Union[int, str, None] = None,
    doc_type: Union[str, None] = None,
):
    actual_id = doc_id or paper_id
    actual_decision = doc_type or paper_decision

    setting = setting or {}
    ac_settings = setting.get("AC", ["inclusive"])
    reviewer_settings = setting.get("reviewer", ["benign"])
    global_settings = setting.get("global_settings", {})

    experiment_setting = {
        "paper_id": actual_id,
        "doc_id": actual_id,
        "paper_decision": actual_decision,
        "doc_type": actual_decision,
        "players": {
            "Document Extractor": [{}],
            "AC": [get_ac_setting_from_ac_type(ac_type) for ac_type in ac_settings],
            "Author": [{}],
            "Reviewer": [get_reviewer_setting_from_reviewer_type(r_type) for r_type in reviewer_settings],
        },
        "global_settings": global_settings,
    }

    return experiment_setting


def get_reviewer_setting_from_reviewer_type(reviewer_type: str):
    reviewer_setting = {
        "is_benign": None,
        "is_knowledgeable": None,
        "is_responsible": None,
    }

    if "benign" in reviewer_type:
        reviewer_setting["is_benign"] = True
    if "malicious" in reviewer_type:
        reviewer_setting["is_benign"] = False

    if "knowledgeable" in reviewer_type:
        reviewer_setting["is_knowledgeable"] = True
    if "unknowledgeable" in reviewer_type:
        reviewer_setting["is_knowledgeable"] = False

    if "responsible" in reviewer_type:
        reviewer_setting["is_responsible"] = True
    if "irresponsible" in reviewer_type:
        reviewer_setting["is_responsible"] = False

    return reviewer_setting


def get_ac_setting_from_ac_type(ac_type: str):
    return {"area_chair_type": ac_type}
