import re


def clean_llm_output(text: str) -> str:
    if not text:
        return ""

    # remove service labels
    text = re.sub(r"(?im)^\s*(title|post|заголовок|пост)\s*:\s*", "", text)

    # remove markdown headings
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)

    # remove bold / italic markdown
    text = text.replace("**", "")
    text = text.replace("__", "")

    # remove code fences
    text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).replace("```", ""), text)

    # normalize spacing
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()
