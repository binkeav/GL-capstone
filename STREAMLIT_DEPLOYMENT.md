# Streamlit Community Cloud Deployment

Use these settings when creating the Streamlit app:

- Repository: `binkeav/GL-capstone`
- Branch: `main`
- Main file path: `invoice_processing_app.py`
- Python: `3.11`

Add these secrets in Streamlit Cloud:

```toml
OPENAI_API_KEY = "..."
OPENAI_API_BASE = "https://aibe.mygreatlearning.com/openai/v1"
FIELD_EXTRACTOR_MODE = "gpt"
INVOICE_PROCESSING_LLM_MODEL = "gpt-4o-mini"
VAL_AGENT_LLM_MODEL = "gpt-4o-mini"
EASYOCR_DOWNLOAD_ENABLED = "true"
```

Notes:

- Qwen local mode is not intended for Streamlit Community Cloud because the local model directory is too large for this deployment target.
- EasyOCR model files are downloaded at runtime when not present in the repo.
- Runtime SQLite and RAG files are ephemeral on Streamlit Community Cloud.
