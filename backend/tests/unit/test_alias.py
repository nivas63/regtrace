from __future__ import annotations

import uuid

from app.llm.prompts import AliasMap


def test_alias_resolution_variants() -> None:
    a, b = object(), object()
    a_id, b_id = uuid.uuid4(), uuid.uuid4()
    aliases: AliasMap[object] = AliasMap("O")
    assert aliases.add(a, a_id, "6.2") == "O1"
    assert aliases.add(b, b_id, "6.20") == "O2"

    assert aliases.resolve("O1") is a and aliases.resolve("o2") is b
    assert aliases.resolve(str(a_id)) is a  # model copied the real id
    assert aliases.resolve("obligation O2") is b
    assert aliases.resolve("obligation_O2") is b
    assert aliases.resolve("obligation_6.2") is a  # model copied the source label
    assert aliases.resolve("6.20") is b  # "6.2" must not match inside "6.20"
    assert aliases.resolve("O9") is None and aliases.resolve("") is None


def test_ambiguous_hint_is_never_guessed() -> None:
    x, y = object(), object()
    aliases: AliasMap[object] = AliasMap("O")
    aliases.add(x, uuid.uuid4(), "3.1")
    aliases.add(y, uuid.uuid4(), "3.1")  # same paragraph number in two regulations
    assert aliases.resolve("obligation_3.1") is None
    assert aliases.resolve("O2") is y
