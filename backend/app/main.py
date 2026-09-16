from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pypdf import PdfReader
import httpx
from dotenv import load_dotenv
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from .db import get_connection, init_db

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

app = FastAPI(title="Quiz Question Bank Generator API")
logger = logging.getLogger(__name__)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def normalize_text(raw_text: str) -> str:
    text = raw_text.replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_text_from_content(filename: str, content: bytes) -> str:
    filename = filename.lower()
    if filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content))
            pages = [page.extract_text() or "" for page in reader.pages]
        except Exception as exc:
            raise HTTPException(status_code=400, detail="The uploaded PDF could not be read.") from exc
        return normalize_text("\n".join(pages))

    if filename.endswith((".txt", ".md", ".csv")):
        return normalize_text(content.decode("utf-8", errors="ignore"))

    raise HTTPException(status_code=400, detail="Unsupported file type. Use .txt, .md, .csv, or .pdf")


def extract_text_from_upload(file: UploadFile) -> str:
    return extract_text_from_content(file.filename or "", file.file.read())


def normalize_document_filename(filename: str) -> str:
    return os.path.basename(filename).strip().lower()


def extract_pages_from_upload(file: UploadFile) -> list[str]:
    filename = (file.filename or "").lower()
    content = file.file.read()

    if filename.endswith(".pdf"):
        try:
            reader = PdfReader(io.BytesIO(content))
            return [re.sub(r"[ \t]+", " ", page.extract_text() or "").strip() for page in reader.pages]
        except Exception as exc:
            raise HTTPException(status_code=400, detail="The uploaded PDF could not be read.") from exc

    if filename.endswith((".txt", ".md", ".csv")):
        return [page for page in (re.sub(r"[ \t]+", " ", part).strip() for part in content.decode("utf-8", errors="ignore").split("\f")) if page]

    raise HTTPException(status_code=400, detail="Unsupported file type. Use .txt, .md, .csv, or .pdf")


def analyze_important_topics(pages: list[str]) -> dict[str, Any]:
    excluded_pattern = re.compile(
        r"^(?:unit|chapter|module|course|subject|syllabus|contents?|index|references?|bibliography|"
        r"acknowledg|objective|outcome|co[- ]?outcome|learning|introduction|conclusion|question paper|"
        r"university|college|department|semester|examination|date|time|page|appendix)\b",
        re.IGNORECASE,
    )
    definition_pattern = re.compile(r"\b(?:is defined as|is known as|refers to|means|defined by)\b", re.IGNORECASE)
    academic_signal_pattern = re.compile(
        r"\b(?:algorithm|architecture|application|advantage|classification|concept|difference|diagram|"
        r"equation|formula|method|model|principle|process|theory|theorem|type|types|uses?|steps?|"
        r"properties|characteristics|examples?|limitations?|disadvantage|calculation|analysis)\b",
        re.IGNORECASE,
    )
    topics: dict[str, dict[str, Any]] = {}

    def clean_topic(candidate: str) -> str:
        candidate = re.sub(r"^(?:unit\s+(?:\d+|[ivxlcdm]+)|chapter\s+\w+|\d+(?:\.\d+)*|[A-Z])\s*[:.)-]\s*", "", candidate, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", candidate).strip(" :.-")

    def is_academic_topic(candidate: str) -> bool:
        words = candidate.split()
        return (
            2 <= len(words) <= 12
            and len(candidate) <= 90
            and not excluded_pattern.search(candidate)
            and not re.fullmatch(r"[\d\W]+", candidate)
            and not candidate.endswith((".", ",", ";", ":"))
        )

    def add_topic(name: str, page_number: int, evidence: list[str], bonus: int = 0) -> None:
        name = clean_topic(name)
        if not is_academic_topic(name):
            return
        key = re.sub(r"[^a-z0-9 ]", "", name.lower())
        existing_key = next((item for item in topics if key in item or item in key), key)
        entry = topics.setdefault(existing_key, {"name": name, "pages": set(), "score": 0, "evidence": []})
        entry["pages"].add(page_number)
        entry["score"] += 1 + bonus + len(evidence) // 2
        entry["evidence"].extend(evidence)

    for page_number, page_text in enumerate(pages, start=1):
        lines = [line.strip(" -*•\t") for line in page_text.splitlines() if line.strip()]
        sentences = [sentence.strip() for sentence in re.split(r"(?<=[.!?])\s+", page_text) if len(sentence.strip()) >= 45]
        for line in lines:
            candidate = clean_topic(line)
            if is_academic_topic(candidate):
                related = [sentence for sentence in sentences if candidate.lower() in sentence.lower()]
                add_topic(candidate, page_number, related[:3], 1 if related else 0)

        for sentence in sentences:
            if definition_pattern.search(sentence):
                label = re.split(r"\s+(?:is defined as|is known as|refers to|means|defined by)\s+", sentence, maxsplit=1, flags=re.IGNORECASE)[0]
                add_topic(label, page_number, [sentence], 3)
            elif academic_signal_pattern.search(sentence):
                label = re.split(r"\s+(?:is|are|includes?|involves?|uses?|has|have|can)\s+", sentence, maxsplit=1, flags=re.IGNORECASE)[0]
                add_topic(label, page_number, [sentence], 1)

    if not topics:
        raise HTTPException(status_code=400, detail="No clear academic topics could be identified in the uploaded PDF.")

    ranked = sorted(topics.values(), key=lambda item: (-item["score"], min(item["pages"])))
    maximum_score = ranked[0]["score"]
    results = []
    for index, item in enumerate(ranked, start=1):
        importance = "Very High" if item["score"] >= max(5, maximum_score * 0.7) else "High" if item["score"] >= 3 else "Medium"
        evidence = list(dict.fromkeys(item["evidence"]))
        combined_text = " ".join(evidence).lower()
        question_types = ["MCQ", "Fill in the Blanks", "Short Answer", "Long Answer"]
        if re.search(r"\b(formula|equation|calculate|calculation|numerical|computation)\b", combined_text):
            question_types.append("Numerical")
        results.append({
            "rank": index,
            "topic": item["name"],
            "important_topic": item["name"],
            "importance": importance,
            "key_concepts_subtopics": evidence[0][:400] if evidence else f"Concepts related to {item['name']} found in the PDF.",
            "covers": evidence[0][:400] if evidence else f"Concepts related to {item['name']} found in the PDF.",
            "question_types": question_types,
            "pages": sorted(item["pages"]),
        })

    top_five = [{"topic": item["topic"], "key_points": item["key_concepts_subtopics"]} for item in results[:5]]
    return {"title": "IMPORTANT TOPICS", "topics": results, "top_priority_topics": [item["topic"] for item in results[:5]], "top_5_most_important_topics": top_five}


def extract_unit_sections(text: str) -> list[tuple[str, str]]:
    unit_pattern = re.compile(r"\bunit\s+(\d+|[ivxlcdm]+)\b", re.IGNORECASE)
    matches = list(unit_pattern.finditer(text))
    if not matches:
        return [("General", text)]

    sections = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        unit_label = match.group(1).upper() if match.group(1).isalpha() else match.group(1)
        section_text = text[start:end].strip(" :-.\n\t")
        if section_text:
            sections.append((f"Unit {unit_label}", section_text))
    return sections


def generate_questions(text: str, difficulty: str = "Medium", question_type: str = "quiz") -> list[dict[str, Any]]:
    difficulty_options = {"Easy", "Medium", "Hard"}
    question_type_options = {"quiz", "fill_blank", "mcq", "mid_pattern", "sem_pattern", "all_mix"}
    if difficulty not in difficulty_options:
        raise HTTPException(status_code=400, detail="Difficulty must be Easy, Medium, or Hard.")
    if question_type not in question_type_options:
        raise HTTPException(status_code=400, detail="Question type must be quiz, fill_blank, or mcq.")

    unit_sections = extract_unit_sections(text)
    if not any(section_text.strip() for _, section_text in unit_sections):
        raise HTTPException(status_code=400, detail="No usable text found in the uploaded document.")

    questions = []
    question_id = 1
    for unit_label, section_text in unit_sections:
        sentences = re.split(r"(?<=[.!?])\s+", section_text)
        sentences = [sentence.strip() for sentence in sentences if len(sentence.strip()) > 40]
        selected = sentences[:10] if question_type in {"mid_pattern", "sem_pattern", "all_mix"} else sentences[:5]

        for position, sentence in enumerate(selected, start=1):
            topic = sentence.split()[0:4]
            generated_type = question_type
            section = "General"
            marks = 2
            if question_type == "mid_pattern":
                section = "Section A" if position <= 5 else "Section B"
                marks = 1 if position <= 5 else 5
                generated_type = "quiz"
            elif question_type == "sem_pattern":
                section = "Section A" if position <= 5 else "Section B"
                marks = 2 if position <= 5 else 8
                generated_type = "quiz"
            elif question_type == "all_mix":
                generated_type = ("quiz", "fill_blank", "mcq")[(position - 1) % 3]
                if position % 2:
                    section = "Mid Section A"
                    marks = 1
                else:
                    section = "Sem Section B"
                    marks = 8

            question: dict[str, Any] = {
                "id": question_id,
                "answer": sentence[:200],
                "source_answer": sentence[:200],
                "difficulty": difficulty,
                "topic": " ".join(topic),
                "unit": unit_label,
                "question_type": question_type,
                "format": generated_type,
                "section": section,
                "marks": marks,
            }

            if generated_type == "fill_blank":
                words = sentence.split()
                answer_word = words[0].strip(".,!?;:")
                question["question"] = sentence.replace(answer_word, "________", 1)
                question["answer"] = answer_word
            elif generated_type == "mcq":
                correct_answer = sentence[:100]
                distractors = [other[:100] for other in selected if other != sentence][:3]
                options = [correct_answer, *distractors]
                question["question"] = f"Which statement best matches this topic in {unit_label}: {' '.join(topic)}?"
                question["options"] = options
                question["answer"] = correct_answer
            else:
                if question_type == "mid_pattern":
                    question["question"] = f"Explain briefly the concept described in this statement: '{sentence[:260]}'"
                elif question_type == "sem_pattern":
                    question["question"] = f"Explain in detail the concept described in this statement, including its key points: '{sentence[:260]}'"
                else:
                    question["question"] = f"What is the main concept explained in this statement from {unit_label}: '{sentence[:260]}'?"

            questions.append(question)
            question_id += 1

    if not questions:
        raise HTTPException(status_code=400, detail="No usable unit content found in the uploaded document.")

    return questions


def save_questions(questions: list[dict[str, str]]) -> None:
    conn: sqlite3.Connection = get_connection()
    try:
        conn.executemany(
            "INSERT INTO questions (text, answer, difficulty, topic) VALUES (?, ?, ?, ?)",
            [
                (question["question"], question["answer"], question["difficulty"], question["topic"])
                for question in questions
            ],
        )
        conn.commit()
    finally:
        conn.close()


def get_cached_questions(cache_key: str) -> list[dict[str, Any]] | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT questions_json FROM generation_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        cached = json.loads(row["questions_json"])
        return cached if isinstance(cached, list) else None
    except json.JSONDecodeError:
        return None
    finally:
        conn.close()


def cache_questions(cache_key: str, questions: list[dict[str, Any]]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO generation_cache (cache_key, questions_json) VALUES (?, ?)",
            (cache_key, json.dumps(questions)),
        )
        conn.commit()
    finally:
        conn.close()


def get_document(document_id: str) -> dict[str, str] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT document_id, filename, content, ollama_context FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_document_by_filename(filename: str) -> dict[str, str] | None:
    init_db()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT document_id, filename, content, ollama_context FROM documents WHERE lower(filename) = ?",
            (normalize_document_filename(filename),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def save_document(document_id: str, filename: str, content: str) -> None:
    init_db()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO documents (document_id, filename, content) VALUES (?, ?, ?)",
            (document_id, normalize_document_filename(filename), content),
        )
        conn.commit()
    finally:
        conn.close()


def save_document_context(document_id: str, context: str) -> None:
    init_db()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE documents SET ollama_context = ? WHERE document_id = ?",
            (context, document_id),
        )
        conn.commit()
    finally:
        conn.close()


QUESTION_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "source_answer": {"type": "string"},
                    "topic": {"type": "string"},
                    "unit": {"type": "string"},
                    "format": {"type": "string", "enum": ["quiz", "fill_blank", "mcq"]},
                    "section": {"type": "string"},
                    "marks": {"type": "integer"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "answer", "source_answer", "topic", "unit", "format", "section", "marks", "options"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["questions"],
    "additionalProperties": False,
}


def call_ollama(
    prompt: str,
    temperature: float,
    max_tokens: int,
    response_schema: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    endpoint = os.getenv("OLLAMA_ENDPOINT", "http://localhost:11434/api/chat").strip().rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "llama3.2:1b").strip()
    timeout = float(os.getenv("OLLAMA_TIMEOUT", "1020"))
    native_ollama = endpoint.endswith("/api/chat")
    try:
        attempts = [(temperature, max_tokens, 1.15, 128)]
        if native_ollama:
            attempts.append((max(temperature, 0.4), max_tokens, 1.25, 256))

        with httpx.Client(timeout=httpx.Timeout(timeout, connect=10.0), trust_env=False) as client:
            for attempt_temperature, attempt_max_tokens, repeat_penalty, repeat_last_n in attempts:
                request_body = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if native_ollama:
                    request_body.update({
                        "stream": False,
                        "format": response_schema or "json",
                        "options": {
                            "temperature": attempt_temperature,
                            "num_predict": attempt_max_tokens,
                            "repeat_penalty": repeat_penalty,
                            "repeat_last_n": repeat_last_n,
                        },
                    })
                else:
                    request_body.update({"temperature": attempt_temperature, "max_tokens": attempt_max_tokens, "response_format": {"type": "json_object"}})

                response = client.post(
                    endpoint,
                    headers={"Authorization": "Bearer ollama", "Content-Type": "application/json"},
                    json=request_body,
                )
                if response.status_code != 500 or "repeat limit" not in response.text.lower() or attempt_max_tokens == attempts[-1][1]:
                    break
        if response.is_error:
            logger.error(
                "Ollama returned HTTP %s from %s: %s",
                response.status_code,
                endpoint,
                response.text[:1000],
            )
        response.raise_for_status()
        response_body = response.json()
        if response_body.get("error"):
            logger.error("Ollama prediction failed: %s", response_body["error"])
            return None
        content = response_body["message"]["content"] if native_ollama else response_body["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            return None
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.IGNORECASE)
        result = json.loads(content)
        return result if isinstance(result, dict) else None
    except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.exception("Ollama request failed: %s", exc)
        return None


def compress_document_context(text: str) -> str | None:
    """Create a reusable factual context once so later generations use a smaller prompt."""
    prompt = f"""
Create a compact but complete study context from the supplied document.
Preserve every important fact, definition, formula, process, example, unit, and relationship.
Do not add outside knowledge. Organize the result with headings and bullet points.
Return ONLY valid JSON in this exact shape: {{"context": "..."}}

Document:
{text}
"""
    result = call_ollama(prompt, temperature=0.1, max_tokens=2500)
    context = result.get("context") if result else None
    return context.strip() if isinstance(context, str) and context.strip() else None


def generate_questions_with_ollama(text: str, difficulty: str, question_type: str) -> list[dict[str, Any]] | None:
    """Ask Ollama for grounded questions; return None when the service fails."""
    prompt = f"""
You are an academic question generator. Use ONLY the supplied study material.
Do not use outside knowledge. Do not invent facts. Keep answers grounded in the source.
Difficulty: {difficulty}
Requested format: {question_type}

Return ONLY valid JSON in this exact shape:
{{"questions": [{{"question": "...", "answer": "...", "source_answer": "...", "topic": "...", "unit": "...", "format": "quiz|fill_blank|mcq", "section": "...", "marks": 1, "options": []}}]}}

Rules:
- Create 5 questions for quiz, fill_blank, or mcq.
- Create up to 10 questions for mid_pattern, sem_pattern, or all_mix.
- For all_mix, combine quiz, fill_blank, and mcq formats.
- For fill_blank, include one blank and put the missing source phrase in answer.
- For mcq, include 2 to 4 options and make answer exactly one option.
- For mid_pattern use Section A (1 mark) and Section B (5 marks).
- For sem_pattern use Section A (2 marks) and Section B (8 marks).
- For all_mix, use a mixture of those section styles.
- Do not mention course titles, unit titles, or generic phrases such as 'Theory in General'.
- Keep each question under 220 characters and each answer under 180 characters.
- Return concise JSON without markdown or extra explanation.

Study material:
{text}
"""

    result = call_ollama(
        prompt,
        temperature=0.2,
        max_tokens=8192,
        response_schema=QUESTION_RESPONSE_SCHEMA,
    )
    generated = result.get("questions") if result else None
    if not isinstance(generated, list) or not generated:
        return None

    questions = []
    for index, item in enumerate(generated, start=1):
        if not isinstance(item, dict) or not item.get("question") or not item.get("answer"):
            continue
        item["id"] = index
        item["difficulty"] = difficulty
        item["question_type"] = question_type
        item.setdefault("source_answer", item["answer"])
        item.setdefault("topic", "Study material")
        item.setdefault("unit", "General")
        item.setdefault("format", question_type if question_type in {"quiz", "fill_blank", "mcq"} else "quiz")
        item.setdefault("section", "General")
        item.setdefault("marks", 2)
        if item.get("format") != "mcq":
            item.pop("options", None)
        questions.append(item)
    return questions or None


def fetch_questions() -> list[dict[str, Any]]:
    conn: sqlite3.Connection = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, text, answer, difficulty, topic, created_at FROM questions ORDER BY created_at DESC, id DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


@app.on_event("startup")
def startup_event() -> None:
    init_db()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "message": "Quiz generator API is running"}


@app.get("/api/questions")
def list_questions() -> dict[str, Any]:
    questions = fetch_questions()
    return {"questions": questions, "count": len(questions)}


@app.get("/api/questions/export")
def export_questions(format: str = "json") -> Response:
    questions = fetch_questions()

    if format == "json":
        return JSONResponse({"questions": questions, "count": len(questions)})

    if format == "csv":
        output = io.StringIO()
        fieldnames = ["id", "question", "answer", "difficulty", "topic", "created_at"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for question in questions:
            writer.writerow({
                "id": question.get("id", ""),
                "question": question.get("text", ""),
                "answer": question.get("answer", ""),
                "difficulty": question.get("difficulty", ""),
                "topic": question.get("topic", ""),
                "created_at": question.get("created_at", ""),
            })
        return Response(
            content=output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=question_bank.csv"},
        )

    if format == "pdf":
        if not questions:
            raise HTTPException(status_code=400, detail="Generate some questions before exporting the question bank.")

        output = io.BytesIO()
        document = SimpleDocTemplate(
            output,
            pagesize=letter,
            rightMargin=0.65 * inch,
            leftMargin=0.65 * inch,
            topMargin=0.65 * inch,
            bottomMargin=0.65 * inch,
        )
        styles = getSampleStyleSheet()
        story = [Paragraph("Quiz Question Bank", styles["Title"]), Spacer(1, 0.2 * inch)]

        for index, question in enumerate(questions, start=1):
            metadata = (
                f"<b>Question {index}</b> | Difficulty: {escape(str(question.get('difficulty', '')))} "
                f"| Topic: {escape(str(question.get('topic', '')))}"
            )
            story.append(Paragraph(metadata, styles["Heading3"]))
            story.append(Paragraph(escape(str(question.get("text", ""))), styles["BodyText"]))
            story.append(Spacer(1, 0.08 * inch))
            story.append(Paragraph(f"<b>Answer:</b> {escape(str(question.get('answer', '')))}", styles["BodyText"]))
            story.append(Spacer(1, 0.18 * inch))

        document.build(story)
        return Response(
            content=output.getvalue(),
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=question_bank.pdf"},
        )

    raise HTTPException(status_code=400, detail="Unsupported export format. Use json or csv.")


@app.post("/api/questions/generate")
async def generate_question_bank(
    file: UploadFile | None = File(None),
    difficulty: str = Form("Medium"),
    question_type: str = Form("quiz"),
    filename: str | None = Form(None),
) -> dict[str, Any]:
    if file is not None:
        upload_filename = file.filename or "uploaded-document"
        document = get_document_by_filename(upload_filename)
        file_content = await file.read()
        if document is None:
            text = extract_text_from_content(file.filename or "", file_content)
            document_id = hashlib.sha256(file_content).hexdigest()
            save_document(document_id, file.filename or "uploaded-document", text)
            document = get_document(document_id)
        else:
            document_id = document["document_id"]
            text = document["content"]
    elif filename:
        document = get_document_by_filename(filename)
        if document is None:
            raise HTTPException(status_code=404, detail="Stored filename was not found. Upload the document first.")
        document_id = document["document_id"]
        text = document["content"]
    else:
        raise HTTPException(status_code=400, detail="Upload a document or provide a stored filename.")

    model = os.getenv("OLLAMA_MODEL", "llama3.2:1b")
    cache_key = hashlib.sha256(
        b"|".join((document_id.encode(), difficulty.encode(), question_type.encode(), model.encode()))
    ).hexdigest()
    cached_questions = get_cached_questions(cache_key)
    if cached_questions:
        return {
            "questions": cached_questions,
            "count": len(cached_questions),
            "cached": True,
            "filename": document["filename"] if file is not None else normalize_document_filename(filename or ""),
        }

    context = document.get("ollama_context")
    if not context:
        context = compress_document_context(text)
        if context:
            save_document_context(document_id, context)

    questions = generate_questions_with_ollama(context or text, difficulty, question_type)
    if not questions:
        raise HTTPException(
            status_code=503,
            detail="Ollama generation failed or is unavailable. Start Ollama, pull the selected model, and try again.",
        )
    save_questions(questions)
    cache_questions(cache_key, questions)
    return {
        "questions": questions,
        "count": len(questions),
        "cached": False,
        "context_cached": bool(context),
        "filename": document["filename"] if file is not None else normalize_document_filename(filename or ""),
    }


@app.post("/api/topics/analyze")
async def analyze_topics(file: UploadFile = File(...)) -> dict[str, Any]:
    pages = extract_pages_from_upload(file)
    return analyze_important_topics(pages)
