from ra_agent.tools import DEFAULT_TOOL_SPECS, TOOL_ARGUMENT_MODELS, ToolRegistry


def test_every_runtime_tool_exposes_and_enforces_one_json_schema() -> None:
    registry = ToolRegistry()
    for spec in DEFAULT_TOOL_SPECS:
        registry.register(spec)
        assert spec.name in TOOL_ARGUMENT_MODELS
        assert spec.input_schema["type"] == "object"
        assert spec.input_schema.get("additionalProperties") is False

    assert registry.validate_arguments("delete_file", {"path": "target.txt"}) == {
        "path": "target.txt"
    }


def test_runtime_tool_schema_rejects_guessed_parameter_names() -> None:
    registry = ToolRegistry()
    for spec in DEFAULT_TOOL_SPECS:
        registry.register(spec)

    try:
        registry.validate_arguments("delete_file", {"filename": "target.txt"})
    except ValueError as error:
        assert "Invalid arguments for delete_file" in str(error)
    else:
        raise AssertionError("guessed delete_file parameter was accepted")
