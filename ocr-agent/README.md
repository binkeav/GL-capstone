# OCR Agent

Streamlit workbench for invoice OCR and structured field extraction.

## Run

From this directory:

```bash
uv sync --frozen
.venv/bin/streamlit run app_milestone2_frontend.py --server.port 8501
```

Then open:

```text
http://127.0.0.1:8501
```

## Local Assets

The app expects these EasyOCR model files in the project root:

- `craft_mlt_25k.pth`
- `english_g2.pth`

They are now present locally. The backend also uses the project-local `user_network/` directory so EasyOCR does not need to write to `~/.EasyOCR`.

## Optional Configuration

Create `.env` in this directory to enable GPT extraction:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_API_BASE=https://aibe.mygreatlearning.com/openai/v1
FIELD_EXTRACTOR_MODE=auto
QWEN_MAX_NEW_TOKENS=256
QWEN_MAX_TIME_SECONDS=45
QWEN_MODEL_DIR=qwen3-vl-8b
```

Without an API key, the app still runs and falls back to local extraction paths.

## Qwen-VL

The app uses `QWEN_MODEL_DIR` when it is set. The path can be absolute or relative to
this directory. To try Qwen3 8B locally, place the model files in one of these folders:

```text
qwen3-vl-8b/
Qwen3-VL-8B-Instruct/
qwen3-vl-8b-instruct/
```

Or point directly at the folder:

```bash
QWEN_MODEL_DIR=/path/to/Qwen3-VL-8B-Instruct
FIELD_EXTRACTOR_MODE=qwen
```

If no Qwen3 folder is present, the app falls back to the existing local Qwen2-VL model:

```text
qwen-vl/
```

The app can use it by selecting `qwen` in the UI or setting:

```bash
FIELD_EXTRACTOR_MODE=qwen
```

On this machine Torch does not report CUDA or MPS availability, so Qwen runs on CPU. It loads successfully, but generation can be slow; the backend caps Qwen generation with `QWEN_MAX_NEW_TOKENS` and `QWEN_MAX_TIME_SECONDS`.

## Current Optional Gaps

- `.venv-layout/` and `scripts/layout_worker.py` are not present, so layout-aware OCR falls back to full-page EasyOCR.
