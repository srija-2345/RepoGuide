from dotenv import load_dotenv
from google import genai
import os

load_dotenv()

api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    raise ValueError("GEMINI_API_KEY is missing from .env")

client = genai.Client(api_key=api_key)

print("Models available to your API key:\n")

for model in client.models.list():
    name = getattr(model, "name", None)

    if name and "gemini" in name.lower():
        print(name)
