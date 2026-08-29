import json
from decimal import Decimal
from typing import Any


SQL_NULL = "NULL"


# ============================================================
# 1. BASIC JSON PARSING
# ============================================================

def parse_json(value: Any) -> Any:
    """
    Convert database-returned JSON/JSONB/VARIANT values
    into Python structures.

    Supported:
        dict
        list
        string containing JSON
        scalar values
    """

    if value is None:
        return None

    if isinstance(value, (dict, list, bool, int, float)):
        return value

    if isinstance(value, Decimal):
        return value

    if isinstance(value, str):

        value = value.strip()

        if not value:
            return ""

        try:
            return json.loads(value)

        except json.JSONDecodeError:
            # Plain string, not JSON text
            return value

    return value


# ============================================================
# 2. NORMALIZE LEAF VALUE
# ============================================================

def normalize_leaf_value(value: Any) -> str:
    """
    Normalize a JSON leaf value.

    Rules are based on the supplied PostgreSQL/Snowflake SQL:

        NULL
            -> NULL

        STRING
            -> string value

        NUMBER
            -> textual number

        BOOLEAN
            -> true / false

    No TRIM is applied to the actual value.
    """

    if value is None:
        return SQL_NULL

    # Boolean must be checked before integer because
    # bool is a subclass of int in Python.
    if isinstance(value, bool):
        return str(value).lower()

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, str):
        return value

    # Fallback for unusual scalar values
    return str(value)


# ============================================================
# 3. JSON PATH
# ============================================================

def object_path(parent_path: str, key: str) -> str:
    """
    Build path for JSON object.

    Example:

        parent = "customer"
        key    = "name"

        result = customer.name
    """

    if not parent_path:
        return str(key)

    return f"{parent_path}.{key}"


def array_path(parent_path: str, index: int) -> str:
    """
    Build path for JSON array.

    Example:

        customer.orders[0]
        customer.orders[1]
    """

    if not parent_path:
        return f"[{index}]"

    return f"{parent_path}[{index}]"


# ============================================================
# 4. RECURSIVE JSON FLATTENER
# ============================================================

def flatten_json(
    value: Any,
    path: str = ""
) -> list[tuple[str, str]]:
    """
    Recursively flatten JSON into leaf path/value pairs.

    Example:

        {
            "name": "Ganesh",
            "age": 22,
            "active": true
        }

    becomes:

        [
            ("active", "true"),
            ("age", "22"),
            ("name", "Ganesh")
        ]

    Nested object:

        {
            "customer": {
                "name": "Ganesh"
            }
        }

    becomes:

        [
            ("customer.name", "Ganesh")
        ]

    Array:

        {
            "roles": ["admin", "user"]
        }

    becomes:

        [
            ("roles[0]", "admin"),
            ("roles[1]", "user")
        ]
    """

    result = []

    # --------------------------------------------------------
    # NULL / SCALAR
    # --------------------------------------------------------

    if not isinstance(value, (dict, list)):

        result.append(
            (
                path,
                normalize_leaf_value(value)
            )
        )

        return result

    # --------------------------------------------------------
    # OBJECT
    # --------------------------------------------------------

    if isinstance(value, dict):

        # Important:
        # Object order does NOT matter.
        #
        # We therefore sort keys.

        for key in sorted(
            value.keys(),
            key=lambda x: str(x)
        ):

            child_path = object_path(
                path,
                str(key)
            )

            result.extend(
                flatten_json(
                    value[key],
                    child_path
                )
            )

        return result

    # --------------------------------------------------------
    # ARRAY
    # --------------------------------------------------------

    if isinstance(value, list):

        # Important:
        # Array order DOES matter.
        #
        # Therefore we preserve the original index.

        for index, item in enumerate(value):

            child_path = array_path(
                path,
                index
            )

            result.extend(
                flatten_json(
                    item,
                    child_path
                )
            )

        return result

    return result


# ============================================================
# 5. CANONICAL FLATTENED REPRESENTATION
# ============================================================

def canonical_flattened_json(
    value: Any
) -> str:
    """
    Convert JSON/JSONB/VARIANT into the same representation
    used by the supplied SQL approach.

    Example:

        {
            "b": 2,
            "a": 1
        }

    becomes:

        a=1|b=2
    """

    parsed = parse_json(value)

    # --------------------------------------------------------
    # SQL NULL
    # --------------------------------------------------------

    if parsed is None:
        return SQL_NULL

    leaves = flatten_json(parsed)

    # --------------------------------------------------------
    # Sort by path
    # --------------------------------------------------------

    leaves.sort(
        key=lambda item: item[0]
    )

    # --------------------------------------------------------
    # path=value
    # --------------------------------------------------------

    return "|".join(
        f"{path}={value}"
        for path, value in leaves
    )


# ============================================================
# 6. HSTORE NORMALIZATION
# ============================================================

def normalize_hstore(
    value: Any
) -> str:
    """
    Normalize PostgreSQL HSTORE.

    HSTORE is treated as a flat key/value structure.

    Example:

        {
            "name": "Ganesh",
            "age": "22",
            "active": "true"
        }

    becomes:

        active=true|age=22|name=Ganesh
    """

    if value is None:
        return SQL_NULL

    # PostgreSQL driver may return HSTORE as dict.
    if isinstance(value, dict):

        pairs = []

        for key in sorted(
            value.keys(),
            key=lambda x: str(x)
        ):

            normalized_value = normalize_leaf_value(
                value[key]
            )

            pairs.append(
                f"{key}={normalized_value}"
            )

        return "|".join(pairs)

    # If hstore_to_json() was already executed
    # and the database returned JSON text.
    parsed = parse_json(value)

    if isinstance(parsed, dict):
        return normalize_hstore(parsed)

    return normalize_leaf_value(value)


# ============================================================
# 7. ARRAY-ONLY NORMALIZATION
# ============================================================

def normalize_array(
    value: Any
) -> str:
    """
    Normalize an array using the approach from:

        JSONB_VARIANT_ARRAY_sol.sql

    IMPORTANT:
    The supplied reference sorts array values:

        ORDER BY value::VARCHAR

    Therefore this function sorts the normalized
    array elements as well.

    Use this only when the business rule says
    array ordering is NOT significant.
    """

    if value is None:
        return SQL_NULL

    parsed = parse_json(value)

    if not isinstance(parsed, list):
        raise ValueError(
            "Expected an array value"
        )

    values = [
        normalize_leaf_value(item)
        for item in parsed
    ]

    values.sort()

    return "|".join(values)


# ============================================================
# 8. GENERIC NORMALIZER
# ============================================================

def normalize_value(
    value: Any,
    data_type: str
) -> str:
    """
    Normalize value according to datatype.
    """

    if not data_type:
        raise ValueError(
            "Datatype is required"
        )

    data_type = (
        data_type
        .strip()
        .lower()
    )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    if data_type == "json":
        return canonical_flattened_json(value)

    # --------------------------------------------------------
    # JSONB
    # --------------------------------------------------------

    if data_type == "jsonb":
        return canonical_flattened_json(value)

    # --------------------------------------------------------
    # HSTORE
    # --------------------------------------------------------

    if data_type == "hstore":
        return normalize_hstore(value)

    # --------------------------------------------------------
    # Snowflake VARIANT
    # --------------------------------------------------------

    if data_type in {
        "variant",
        "object",
        "array"
    }:

        parsed = parse_json(value)

        if data_type == "array":
            return normalize_array(parsed)

        return canonical_flattened_json(parsed)

    # --------------------------------------------------------
    # TEXT / VARCHAR
    # --------------------------------------------------------

    if data_type in {
        "text",
        "varchar",
        "character varying",
        "character",
        "char"
    }:

        # IMPORTANT:
        #
        # We do NOT trim here.
        #
        # Trimming is only performed if the datatype
        # mapping explicitly requires it.

        if value is None:
            return SQL_NULL

        return str(value)

    raise ValueError(
        f"Unsupported datatype: {data_type}"
    )


# ============================================================
# 9. COMPARE SOURCE AND TARGET
# ============================================================

def compare_values(
    source_value: Any,
    source_type: str,
    target_value: Any,
    target_type: str
) -> dict:
    """
    Normalize source and target and compare them.
    """

    source_normalized = normalize_value(
        source_value,
        source_type
    )

    target_normalized = normalize_value(
        target_value,
        target_type
    )

    matched = (
        source_normalized ==
        target_normalized
    )

    return {
        "source_type": source_type,
        "target_type": target_type,
        "source_normalized": source_normalized,
        "target_normalized": target_normalized,
        "matched": matched,
        "status": (
            "MATCH"
            if matched
            else "MISMATCH"
        )
    }


# ============================================================
# 10. TEST
# ============================================================

if __name__ == "__main__":

    source = {
        "name": "Ganesh",
        "age": 22,
        "active": True,
        "address": {
            "city": "Nanded",
            "pincode": 431601
        },
        "roles": [
            "admin",
            "developer"
        ]
    }

    target = {
        "roles": [
            "admin",
            "developer"
        ],
        "address": {
            "pincode": 431601,
            "city": "Nanded"
        },
        "active": True,
        "age": 22,
        "name": "Ganesh"
    }

    result = compare_values(
        source_value=source,
        source_type="jsonb",

        target_value=target,
        target_type="variant"
    )

    print("SOURCE:")
    print(result["source_normalized"])

    print()

    print("TARGET:")
    print(result["target_normalized"])

    print()

    print("RESULT:")
    print(result["status"])