from clinic_sidecar.checklist import TOPICS, evaluate


def covered(lines: list[str]) -> dict[str, tuple[int | None, str | None]]:
    return {i.id: (i.line, i.match) for i in evaluate(lines) if i.covered}


def test_nothing_covered_at_start() -> None:
    items = evaluate([])
    assert [i.id for i in items] == [t.id for t in TOPICS]
    assert not any(i.covered for i in items)


def test_typical_opening() -> None:
    lines = [
        "Guten Tag, was führt Sie heute zu uns?",
        "Ich habe seit drei Tagen starke Brustschmerzen.",
        "Nehmen Sie regelmäßig Medikamente?",
        "Haben Sie Allergien?",
    ]
    got = covered(lines)
    assert got["complaint"][0] == 1
    assert got["onset"] == (2, "seit")
    assert got["medication"][0] == 3
    assert got["allergies"] == (4, "Allergi")
    assert "surgery" not in got and "substances" not in got and "living" not in got


def test_english_lines_count_too() -> None:
    got = covered(["I am allergic to penicillin.", "I smoke, about ten a day."])
    assert {"allergies", "substances"} <= set(got)


def test_op_matches_only_as_a_word() -> None:
    assert "surgery" not in covered(["Das ist ein Problem."])
    assert "surgery" in covered(["Hatten Sie schon eine OP?"])
