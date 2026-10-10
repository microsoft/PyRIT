# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.

import pytest
from pydantic import ValidationError

from pyrit.models.operation import Operation, OperationCreate, operation_name_key


def test_operation_trims_display_name_and_folds_key() -> None:
    operation = Operation(name="  Operation A \t")
    assert operation.name == "Operation A"
    assert Operation.model_validate_json(operation.model_dump_json()) == operation
    assert operation_name_key(" operation a ") == operation_name_key("OPERATION A") == "operation a"
    assert operation_name_key("Straße") == operation_name_key("STRASSE")
    assert operation_name_key("Cáse") != operation_name_key("Case")


@pytest.mark.parametrize("name", ["", " \t"])
def test_operation_rejects_blank_names(name: str) -> None:
    with pytest.raises(ValidationError):
        OperationCreate(name=name)


def test_operation_limits_trimmed_name_by_utf16_code_units() -> None:
    assert OperationCreate(name=" " + "😀" * 64 + " ").name == "😀" * 64
    with pytest.raises(ValidationError):
        OperationCreate(name="😀" * 65)
    with pytest.raises(ValidationError):
        OperationCreate.model_validate({"name": "Case", "id": "unexpected"})
