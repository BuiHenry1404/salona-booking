import re

PINNED = ("langchain", "langchain-core", "langchain-openai", "langgraph",
          "langgraph-checkpoint", "langgraph-checkpoint-mongodb", "pymongo", "motor")


def _reqs():
    # Brief's original regex used a lazy `+?` for the name group together with
    # `[A-Za-z0-9_.\-\[\]]` (brackets in the name class too), which is always
    # satisfiable with a 1-char match (every later group is optional / `.*`),
    # so it degenerated to keying the dict by the first letter of each line
    # (e.g. "motor==3.3.2" -> {"m": (None, "otor==3.3.2")}), colliding every
    # "p*"/"l*" package into one entry and making `reqs["motor"]` etc. always
    # KeyError regardless of requirements.txt content. Fixed by making the
    # name group greedy and dropping `[`/`]` from its class (extras are still
    # captured by the explicit `(\[.*\])?` group).
    out = {}
    for line in open("requirements.txt", encoding="utf-8"):
        line = line.split("#", 1)[0].strip()
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[.*\])?\s*(==|>=|<=|~=|>|<)?\s*(.*)$", line)
        if line and m:
            out[m.group(1).lower()] = (m.group(3), m.group(4))
    return out


def test_agent_and_mongo_packages_are_pinned_exactly():
    reqs = _reqs()
    for name in PINNED:
        assert name in reqs, name
        assert reqs[name][0] == "==", f"{name} phải pin '==' (A-08)"


def test_motor_is_new_enough_for_pymongo_4_19():
    assert _reqs()["motor"][1] == "3.7.1"
    assert _reqs()["pymongo"][1] == "4.19.0"


def test_checkpointer_package_imports():
    from langgraph.checkpoint.mongodb import MongoDBSaver  # noqa: F401
