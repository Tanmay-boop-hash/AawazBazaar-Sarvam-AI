import os
from pathlib import Path

from sarvamai import SarvamAI

key_name = "SARVAM_API_KEY"
if not os.environ.get(key_name):
    for line in Path(__file__).with_name(".env").read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name.strip() == key_name:
            os.environ[key_name] = value.strip().strip("\"'")
            break

client = SarvamAI(api_subscription_key=os.environ[key_name])
response = client.chat.completions(
    model="sarvam-105b-conversations",
    messages=[{"role": "user", "content": "Say hello in one short sentence."}],
)
print(response.choices[0].message.content)
