import asyncio
from winsdk.windows.media.ocr import OcrEngine

async def main():
    langs = OcrEngine.get_available_recognizer_languages()
    print("Available OCR Languages on this Windows machine:")
    for l in langs:
        print("-", l.language_tag)

if __name__ == "__main__":
    asyncio.run(main())
