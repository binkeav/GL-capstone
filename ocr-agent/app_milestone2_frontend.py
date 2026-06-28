
from pathlib import Path
import os
import re
import tempfile
from typing import Dict, List

import cv2
import numpy as np
import pypdfium2 as pdfium
import streamlit as st

from milestone2_backend import Milestone1NotebookAPI


LAYOUT_CLASS_NAMES = ["text", "title", "list", "table", "figure"]


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _chunk_text(text: str, chunk_size: int = 900, overlap: int = 150) -> List[str]:
    clean = (text or "").strip()
    if not clean:
        return []
    chunks = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        chunks.append(clean[start:end])
        if end >= len(clean):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _build_rag_chunks(docs: List[Dict[str, str]]) -> List[Dict[str, str]]:
    rows = []
    for doc in docs:
        file_name = doc.get("file", "unknown")
        for i, chunk in enumerate(_chunk_text(doc.get("text", ""))):
            rows.append(
                {
                    "file": file_name,
                    "chunk_id": i,
                    "text": chunk,
                }
            )
    return rows


def _retrieve_chunks(query: str, chunks: List[Dict[str, str]], top_k: int = 3) -> List[Dict[str, str]]:
    qset = set(_tokenize(query))
    if not qset or not chunks:
        return []

    scored = []
    for ch in chunks:
        cset = set(_tokenize(ch["text"]))
        overlap = len(qset.intersection(cset))
        if overlap <= 0:
            continue
        scored.append((overlap, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    hits = []
    for score, ch in scored[:top_k]:
        item = dict(ch)
        item["score"] = int(score)
        hits.append(item)
    return hits


def _answer_with_gpt(api: Milestone1NotebookAPI, question: str, hits: List[Dict[str, str]]) -> str:
    if not hits:
        return "No relevant context found in processed OCR documents."

    context = "\n\n---\n\n".join(
        [
            f"FILE: {h['file']} | CHUNK: {h['chunk_id']} | SCORE: {h['score']}\n{h['text']}"
            for h in hits
        ]
    )

    if api.openai_client is None:
        return (
            "GPT client unavailable. Top retrieved context:\n\n"
            + "\n\n".join([f"- {h['file']} (chunk {h['chunk_id']}): {h['text'][:280]}" for h in hits])
        )

    prompt = (
        "Answer the user question using only the retrieved OCR context. "
        "If not present, say it is not available in the uploaded documents.\n\n"
        f"QUESTION:\n{question}\n\nCONTEXT:\n{context}"
    )

    try:
        resp = api.openai_client.chat.completions.create(
            model=api.model_name,
            messages=[
                {"role": "system", "content": "You are an invoice RAG assistant."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=350,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as exc:
        return f"GPT answer error: {exc}"


def _load_preview_image_rgb(file_bytes: bytes, suffix: str):
    suffix = suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        arr = np.frombuffer(file_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return None
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)

    if suffix == ".pdf":
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name
        try:
            doc = pdfium.PdfDocument(tmp_path)
            if len(doc) == 0:
                return None
            return np.array(doc[0].render(scale=2.0).to_pil().convert("RGB"))
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    return None


def _draw_layout_boxes(image_rgb: np.ndarray, regions: List[Dict[str, object]]) -> np.ndarray:
    canvas = image_rgb.copy()
    for r in regions:
        bbox = r.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1, y1, x2, y2 = [int(v) for v in bbox]
        cls = int(r.get("class_id", -1))
        score = float(r.get("score", 0.0))
        color = (0, 255, 0)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = LAYOUT_CLASS_NAMES[cls] if 0 <= cls < len(LAYOUT_CLASS_NAMES) else f"cls{cls}"
        cv2.putText(
            canvas,
            f"{label}:{score:.2f}",
            (x1, max(16, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    return canvas

st.set_page_config(page_title='Milestone 1 EasyOCR + GPT-4o-mini App', layout='wide')
st.title('Milestone 1: EasyOCR Field Extraction')

ROOT = Path(__file__).resolve().parent
api = Milestone1NotebookAPI(ROOT)

if "rag_docs" not in st.session_state:
    st.session_state.rag_docs = []
if "rag_chat_history" not in st.session_state:
    st.session_state.rag_chat_history = []
if "layout_preview" not in st.session_state:
    st.session_state.layout_preview = None

mode_options = ['auto', 'gpt', 'qwen']
default_mode = api.field_extractor_mode if api.field_extractor_mode in mode_options else 'auto'
selected_mode = st.selectbox('Field Extraction Mode', mode_options, index=mode_options.index(default_mode))
api.field_extractor_mode = selected_mode

st.caption(
    " | ".join(
        [
            f"Configured mode: {api.field_extractor_mode}",
            f"GPT Model: {api.model_name}",
            f"Qwen Model: {api.qwen_model_name}",
            f"Qwen Path: {api.qwen_model_dir}",
            f"API Base: {api.openai_api_base}",
        ]
    )
)

uploaded = st.file_uploader('Upload JPG, PDF, or DOCX', type=['jpg', 'jpeg', 'png', 'pdf', 'docx'])
process_clicked = st.button('Upload and Process', type='primary', disabled=uploaded is None)

if process_clicked and uploaded is not None:
    with st.spinner('Running OCR and extracting fields...'):
        try:
            uploaded_bytes = uploaded.getvalue()
            uploaded.seek(0)
            result = api.process_upload(uploaded)
            st.success('Processing complete.')
            fields = result.get('fields', {})
            used_mode = result.get('extraction_mode', 'unknown')
            fallback_reason = fields.get('fallback_reason')
            fallback_detail = fields.get('fallback_detail')

            st.caption(f"Configured mode: {api.field_extractor_mode} | Extraction mode used: {used_mode}")

            layout_info = result.get('layout')
            layout_summary = result.get('layout_summary')
            if layout_info or layout_summary:
                st.subheader('Layout-Aware OCR Summary')
                if layout_info:
                    st.json(layout_info)
                if layout_summary:
                    st.json(layout_summary)

            file_suffix = Path(uploaded.name).suffix.lower()
            if file_suffix in {'.jpg', '.jpeg', '.png', '.pdf'}:
                preview_rgb = _load_preview_image_rgb(uploaded_bytes, file_suffix)
                if preview_rgb is not None:
                    layout_det = api.detect_layout_regions(preview_rgb)
                    overlay_rgb = _draw_layout_boxes(preview_rgb, layout_det.get('regions', []))
                    st.session_state.layout_preview = {
                        "file_name": uploaded.name,
                        "original": preview_rgb,
                        "overlay": overlay_rgb,
                        "layout_status": layout_det.get('status'),
                        "region_count": len(layout_det.get('regions', [])),
                    }

            if fallback_reason:
                st.warning(f"Fallback activated: {fallback_reason}")
                if fallback_detail:
                    with st.expander('Fallback details'):
                        st.text(fallback_detail)

            st.subheader('Extracted Text')
            st.text_area('OCR Output', result['text'], height=320)

            st.subheader('Extracted Fields')
            st.json(fields)

            text = result.get('text', '')
            if text.strip():
                existing = [d for d in st.session_state.rag_docs if d.get('file') != uploaded.name]
                existing.append({'file': uploaded.name, 'type': result.get('type', 'unknown'), 'text': text})
                st.session_state.rag_docs = existing
        except Exception as exc:
            st.error(f'Processing failed: {exc}')

if st.session_state.layout_preview is not None:
    preview = st.session_state.layout_preview
    st.subheader('Layout Output (Box Overlay)')
    st.caption(f"File: {preview['file_name']}")
    col1, col2 = st.columns(2)
    with col1:
        st.image(preview['original'], caption='Original Preview', use_container_width=True)
    with col2:
        st.image(preview['overlay'], caption='LayoutLMv3 Box Overlay', use_container_width=True)
    st.caption(
        f"Layout status: {preview['layout_status']} | regions: {preview['region_count']}"
    )

st.divider()
st.subheader('GPT-4o-mini Aided RAG Chat')

if not st.session_state.rag_docs:
    st.info('Upload and process at least one file to start RAG chat.')
else:
    st.caption(f"Indexed documents: {len(st.session_state.rag_docs)}")
    with st.expander('Indexed Files'):
        for d in st.session_state.rag_docs:
            st.write(f"- {d.get('file')} ({d.get('type')})")

    rag_query = st.text_input('Ask a question about uploaded invoices')
    rag_top_k = st.slider('Retrieved chunks', min_value=1, max_value=6, value=3)
    ask_clicked = st.button('Ask RAG', type='secondary', disabled=not rag_query.strip())

    if ask_clicked:
        chunks = _build_rag_chunks(st.session_state.rag_docs)
        hits = _retrieve_chunks(rag_query, chunks, top_k=rag_top_k)
        answer = _answer_with_gpt(api, rag_query, hits)
        st.session_state.rag_chat_history.append(
            {
                'question': rag_query,
                'answer': answer,
                'hits': hits,
            }
        )

    if st.session_state.rag_chat_history:
        st.subheader('Chat History')
        for i, item in enumerate(reversed(st.session_state.rag_chat_history), start=1):
            st.markdown(f"Question {i}: {item['question']}")
            st.write(item['answer'])
            if item.get('hits'):
                with st.expander(f"Retrieved Context for Question {i}"):
                    for h in item['hits']:
                        st.write(f"File: {h['file']} | chunk: {h['chunk_id']} | score: {h['score']}")
                        st.text(h['text'][:700])
