import pytest

from lumo.core.confirmation import Answer, classify_answer
from lumo.core.skill_registry import DuplicateSkillError, PreparedCall, SkillRegistry

OK_PKG = "tests.fixtures.skills_ok"


@pytest.fixture
def registry():
    reg = SkillRegistry()
    reg.discover(OK_PKG)
    return reg


def test_discovered_skills_are_exposed_as_tools(registry):
    names = {t["function"]["name"] for t in registry.tools()}
    assert names == {"greet", "boom", "wipe"}
    greet = next(t for t in registry.tools() if t["function"]["name"] == "greet")
    assert greet["function"]["parameters"]["required"] == ["name"]


def test_duplicate_names_fail_with_module_names():
    with pytest.raises(DuplicateSkillError) as exc:
        SkillRegistry().discover("tests.fixtures.skills_dup")
    assert "skills_dup.a" in str(exc.value) and "skills_dup.b" in str(exc.value)


def test_disabled_skill_is_invisible_and_uncallable():
    reg = SkillRegistry(disabled=["greet"])
    reg.discover(OK_PKG)
    assert "greet" not in {t["function"]["name"] for t in reg.tools()}
    assert isinstance(reg.prepare("greet", '{"name": "x"}'), str)


def test_invalid_params_are_not_executed(registry):
    result = registry.prepare("greet", '{"wrong": 1}')
    assert isinstance(result, str) and result.startswith("Ошибка")
    assert isinstance(registry.prepare("greet", "not json"), str)


async def test_run_valid_call(registry):
    call = registry.prepare("greet", '{"name": "Марк"}')
    assert isinstance(call, PreparedCall)
    assert await registry.run(call, ctx=None) == "Привет, Марк"


async def test_failing_skill_is_isolated(registry):
    result = await registry.run(registry.prepare("boom", None), ctx=None)
    assert result.startswith("Ошибка")


async def test_dict_result_serialized(registry):
    assert await registry.run(registry.prepare("wipe", "{}"), ctx=None) == '{"wiped": true}'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("да", Answer.YES),
        ("Да, удали", Answer.YES),
        ("Подтверждаю.", Answer.YES),
        ("давай", Answer.YES),
        ("нет", Answer.NO),
        ("да нет, не надо", Answer.NO),
        ("отмена", Answer.NO),
        ("какая погода завтра", Answer.OTHER),
        ("", Answer.OTHER),
    ],
)
def test_classify_answer(text, expected):
    assert classify_answer(text) is expected
