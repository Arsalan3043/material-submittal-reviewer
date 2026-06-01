"""
Run this once to confirm LangSmith tracing is working.
After running, open your LangSmith dashboard and check the
'material-submittal-reviewer' project for a trace named 'langsmith_connection_test'.
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

_required = ["OPENAI_API_KEY", "LANGCHAIN_API_KEY", "LANGCHAIN_PROJECT"]
_missing = [k for k in _required if not os.getenv(k)]
if _missing:
    print(f"Missing env vars: {', '.join(_missing)}")
    sys.exit(1)

os.environ["LANGCHAIN_TRACING_V2"] = "true"

from langsmith import traceable
from openai import OpenAI

openai_client = OpenAI()


@traceable(name="langsmith_connection_test", project_name=os.environ["LANGCHAIN_PROJECT"])
def _test_call(question: str) -> str:
    response = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": question}],
        max_tokens=20,
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    print("Sending test trace to LangSmith...")
    answer = _test_call("Reply with only the word: connected")
    print(f"LLM response : {answer}")
    print(f"LangSmith project : {os.environ['LANGCHAIN_PROJECT']}")
    print("Check https://smith.langchain.com — you should see 'langsmith_connection_test'")
