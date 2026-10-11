from __future__ import annotations

from app.agents.supervisor import REQUIRED, TEMPLATES, _preconditions_ok


def _plan(situation: str, **facts: int) -> list[str]:
    context = {"situation": situation, **facts}
    return [s for s in TEMPLATES[situation] if _preconditions_ok(s, context)]


def test_first_regulation_maps_when_controls_exist() -> None:
    plan = _plan("NEW_REGULATION", controls=23, obligations=0, regulations=1)
    assert "map_controls" in plan and "cross_regulation" not in plan


def test_control_matrix_after_regulations_triggers_mapping() -> None:
    plan = _plan("CONTROL_MATRIX", controls=0, obligations=41, regulations=3)
    assert plan[0] == "import_controls" and "map_controls" in plan


def test_control_matrix_without_obligations_skips_mapping() -> None:
    assert "map_controls" not in _plan("CONTROL_MATRIX", controls=0, obligations=0, regulations=0)


def test_required_steps_are_in_templates() -> None:
    for situation, required in REQUIRED.items():
        assert required <= set(TEMPLATES[situation]), situation
