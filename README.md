# 🎬 CapCut Live Translator

A lightweight **Windows desktop translator for CapCut / JianYing** that detects Chinese text directly from the screen, translates it in real time, and displays the translated text as an overlay.

> **CapCut Live Translator** is an independent community project and is not affiliated with, sponsored by, or endorsed by CapCut, JianYing, or ByteDance.

---

## ✨ Features

* 🔍 **Real-time OCR** — Detects Chinese text directly from the screen.
* 🌐 **Automatic Translation** — Translates detected Chinese text using Google Translate.
* 🖥️ **CapCut-aware overlay** — Detects the CapCut/JianYing window automatically.
* ⚡ **Live translation** — Translation runs continuously while CapCut is being used.
* 💾 **Translation cache** — Previously translated text can be cached to reduce unnecessary requests.
* 🎨 **Desktop GUI** — Built with PyQt6.
* 🖱️ **Click-through overlay** — The translation overlay can stay above CapCut without blocking mouse interaction.
* 🖥️ **DPI-aware** — Designed to work with Windows display scaling.
* ⚙️ **Persistent settings** — Application settings and cache are stored locally.

The project currently contains `main.py`, `run.bat`, and an OCR-language test utility.

---

## 🧠 How It Works

The application follows this basic pipeline:

```text
CapCut / JianYing
       │
       ▼
Screen Capture
       │
       ▼
Chinese Text Detection
       │
       ▼
OCR (RapidOCR)
       │
       ▼
Google Translation
       │
       ▼
Translation Cache
       │
       ▼
Desktop Overlay
```

The application identifies CapCut/JianYing windows through their window titles and uses Windows APIs to work with their screen coordinates.

---

## 🛠️ Tech Stack

| Technology            | Purpose                                                   |
| --------------------- | --------------------------------------------------------- |
| **Python**            | Main programming language                                 |
| **PyQt6**             | Desktop GUI and overlay                                   |
| **OpenCV**            | Image processing                                          |
| **RapidOCR**          | Optical Character Recognition                             |
| **Google Translator** | Chinese → English translation                             |
| **MSS**               | Fast screen capture                                       |
| **NumPy**             | Image/data processing                                     |
| **Windows API**       | Window detection, DPI handling and click-through behavior |
| **Keyboard**          | Keyboard interaction/hotkeys                              |

The current implementation imports and uses PyQt6, OpenCV, NumPy, MSS, RapidOCR and `deep-translator`.

---

## 💻 Requirements

### Operating System

* **Windows 10 / Windows 11**
* CapCut or JianYing installed
* Internet connection for translation

### Python

Python **3.10+** is recommended.

> The project uses Windows-specific APIs, so it is currently intended for Windows.

---

## 📦 Installation

### 1. Clone the repository

```bash
git clone https://github.com/vishu-y-developer/Translator-for-capcut.git
cd Translator-for-capcut
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

### 3. Activate the virtual environment

```bash
venv\Scripts\activate
```

### 4. Install dependencies

Install the required Python packages:

```bash
pip install PyQt6 numpy opencv-python keyboard mss rapidocr_onnxruntime deep-translator winsdk
```

---

## ▶️ Running the Translator

Make sure your virtual environment is activated:

```bash
venv\Scripts\activate
```

Then run:

```bash
python main.py
```

### Windows shortcut

You can also simply run:

```text
run.bat
```

The included launcher activates the project's virtual environment and starts `main.py`.

---

## 🎯 Usage

1. Start **CapCut**.
2. Open the project containing Chinese text.
3. Start **CapCut Live Translator**.
4. Keep the translator running while working in CapCut.
5. The application detects Chinese text from the screen.
6. Detected text is processed through OCR.
7. The text is translated automatically.
8. The translated result appears through the overlay.

For best results:

* Keep CapCut at a reasonable UI scale.
* Make sure the Chinese text is clearly visible.
* Avoid extremely small text.
* Use a stable internet connection.
* Keep the CapCut window visible.

---

## 🔤 OCR Language Support

OCR availability depends partly on the Windows OCR/language resources installed on your system.

The repository includes `test_ocr.py`, which can be used to check the OCR languages available on your Windows installation.

Run:

```bash
python test_ocr.py
```

It will display the OCR languages available on your machine.

---

## 📁 Project Structure

```text
Translator-for-capcut/
│
├── main.py          # Main application
├── run.bat          # Windows launcher
├── test_ocr.py      # OCR language availability test
├── .gitignore       # Git ignore rules
└── README.md        # Project documentation
```

---

## 💾 Local Files

The application stores local configuration data in your Windows user directory.

### Translation Cache

```text
~/.capcut_translator_cache.json
```

### Settings

```text
~/.capcut_translator_settings.json
```

These files are used to preserve application data between sessions.

---

## ⚠️ Important Notes

### Internet Connection

Translation requires an internet connection because the application uses Google translation services.

### Translation Accuracy

OCR and machine translation are not guaranteed to be perfect. Accuracy can vary depending on:

* Font
* Text size
* Screen resolution
* Image quality
* Chinese characters being displayed
* Translation context

### CapCut Updates

CapCut/JianYing may change its interface or window behavior. Such changes can affect screen detection or overlay positioning.

---

## 🔒 Privacy

The application is designed to process text from your screen locally for OCR and then send detected text to the translation service when translation is required.

Do **not** use the application with sensitive or confidential information unless you understand the privacy implications of the translation service being used.

---

## 🧪 Testing

To check Windows OCR language availability:

```bash
python test_ocr.py
```

The script uses the Windows OCR API to list available recognizer languages.

---

## 🐛 Troubleshooting

### Translator does not start

Make sure the virtual environment exists:

```bash
python -m venv venv
```

Then activate it:

```bash
venv\Scripts\activate
```

And install the dependencies again.

---

### CapCut is not detected

Make sure:

* CapCut is open.
* The CapCut window is visible.
* You are using the Windows version.
* The application has permission to interact with the desktop.

---

### OCR does not detect text

Try:

* Increasing CapCut's UI/text size.
* Making the text clearer.
* Checking available Windows OCR languages.
* Running:

```bash
python test_ocr.py
```

---

### Translation is slow

Translation speed can depend on:

* Internet connection
* OCR processing time
* Amount of text detected
* Translation-service response time

The application includes a local translation cache to avoid repeatedly translating the same text.

---

## 🚀 Future Improvements

Potential improvements include:

* [ ] More translation providers
* [ ] Offline translation models
* [ ] Better OCR accuracy
* [ ] More languages
* [ ] Custom overlay themes
* [ ] Adjustable translation position
* [ ] Custom keyboard shortcuts
* [ ] Translation history
* [ ] Automatic language detection
* [ ] Linux/macOS support
* [ ] Standalone `.exe` release

---

## 🤝 Contributing

Contributions are welcome.

### Fork the repository

```bash
git clone https://github.com/vishu-y-developer/Translator-for-capcut.git
```

Create a new branch:

```bash
git checkout -b feature/my-feature
```

Make your changes, test them, and submit a pull request.

---

## 📜 Disclaimer

This project is an **independent third-party tool**.

It is **not affiliated with, sponsored by, or endorsed by ByteDance, CapCut, or JianYing**.

CapCut and JianYing are trademarks of their respective owners.

Use this software at your own risk.

---

## ⭐ Support the Project

If this project helps you translate or understand the Chinese CapCut interface:

**⭐ Star the repository on GitHub**

**🐛 Report bugs through GitHub Issues**

**💡 Suggest improvements and new features**

---

## 👨‍💻 Author

Created by **Utkarsh Yadav**

GitHub:
https://github.com/vishu-y-developer

Repository:
https://github.com/vishu-y-developer/Translator-for-capcut

---

## ❤️ Made for Creators

**Translate CapCut. Understand the interface. Create without language barriers.**

---
