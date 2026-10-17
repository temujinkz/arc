"""Local syntax normalization only. Never repairs facts, citation IDs or schemas."""

import re


def parse_model_json(text, schema):
    text = text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*\n([\s\S]*?)\n```", text, flags=re.IGNORECASE
    )
    if fenced:
        text = fenced[1].strip()
    # Strip trailing commas only outside JSON strings. No substring extraction,
    # quote guessing, missing-field insertion or second model call.
    output = []
    quoted = escaped = False
    for i, char in enumerate(text):
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            output.append(char)
        elif char == "," and text[i + 1 :].lstrip().startswith(("}", "]")):
            continue
        else:
            output.append(char)
    return schema.model_validate_json("".join(output))
