import os
import sys
from google import genai
from app.check_gemini_api_key import get_gemini_api_key

def main():
    client = genai.Client(api_key=get_gemini_api_key())
    
    print("Testing gemini-3-flash-preview...")
    try:
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents="Hi"
        )
        print("Success with gemini-3-flash-preview!")
    except Exception as e:
        print(f"Error with gemini-3-flash-preview: {e}")

    print("\nTesting gemini-3.5-flash...")
    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash",
            contents="Hi"
        )
        print("Success with gemini-3.5-flash!")
    except Exception as e:
        print(f"Error with gemini-3.5-flash: {e}")

if __name__ == "__main__":
    main()
