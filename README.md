# Multi-Format Quiz Question Bank Generator

An AI-powered application for converting study material into structured quiz questions and reusable question banks.

## Overview

This project follows a complete assessment-generation workflow:

1. User uploads educational content
2. The system preprocesses and extracts text from the document
3. NLP and content understanding identify key concepts and relationships
4. The question generation engine creates meaningful questions with answers
5. Generated items are stored in a question bank with metadata
6. The output is exported or formatted as a final question paper or assessment

This design matches the diagram flow: upload content -> process text -> understand material -> generate questions -> store as question bank -> export results.

## Project Goal

The system helps teachers, trainers, and academic teams create question banks quickly from content such as lecture notes, study material, or reference documents. Instead of manually drafting questions, the application automates the workflow using document processing and AI-based question generation.

## Workflow Diagram Mapping

### 1. User Input / Upload
The starting point is where the user provides study material, often in a document format such as PDF or DOCX.

Features include:
- upload study content
- select subject or topic
- choose question type and level
- define difficulty or taxonomy preferences

### 2. Document Processing
The uploaded file is processed to extract plain text and structure the content for analysis.

Tasks include:
- text extraction
- cleaning and normalization
- segmentation of content
- preparation for question generation

### 3. Content Understanding
The system analyzes the document using NLP-based understanding to identify:
- important concepts
- learning objectives
- relevant facts and explanations
- relationships between ideas

This step ensures the generated questions are meaningful and aligned with the source material.

### 4. Question Generation Engine
The core AI layer produces a set of questions based on the analyzed content.

Generated output may include:
- questions
- answers
- difficulty level
- Bloom’s taxonomy classification
- question type

### 5. Question Bank Storage
Generated questions are stored in a structured database or bank for reuse.

Each item can include metadata such as:
- question text
- answer
- topic
- difficulty
- Bloom level
- category or type

This makes the project useful for repeated assessments and academic workflows.

### 6. Export / Final Output
The final question bank can be converted into a usable assessment format such as:
- generated question paper
- PDF
- DOCX
- print-friendly output
- reusable educational content package

## Key Features

- Upload educational documents and study material
- Extract and preprocess text from uploaded files
- Understand content using NLP and concept extraction
- Generate questions automatically from learning material
- Create question-answer pairs with metadata
- Classify difficulty and learning level
- Maintain a reusable question bank
- Export generated assessment material

## Project Structure

```text
Multi-Format-Quiz-Question-Bank-Generator/
├── backend/              # Processing logic, APIs, and generation services
├── frontend/             # UI for upload, configuration, and output viewing
├── dataset/              # Training or source dataset files
├── generated/            # Final question bank and generated assessment outputs
├── models/               # AI model assets or trained resources
├── notebooks/            # Notebook-based experiments and analysis
├── uploads/              # Uploaded files and user content
├── README.md             # Project documentation
├── .gitignore            # Git ignore rules
└── project.png           # Architecture or workflow diagram
```

## Typical Use Case

A teacher uploads a chapter or lecture notes. The system extracts the text, understands the key concepts, generates questions at different difficulty levels, stores them in a question bank, and exports a final test or assessment set.

## Example Workflow

```text
Upload Document
    -> Extract Text
    -> Preprocess Content
    -> Understand Concepts
    -> Generate Questions + Answers
    -> Save to Question Bank
    -> Export Final Assessment
```

## Tech Stack Expectations

This project is likely built using a combination of:

- Frontend: web UI for upload and interaction
- Backend: Python-based API layer for processing and generation
- NLP / AI: language model or question-generation logic
- Data handling: structured storing of generated question data
- Export: document generation for PDF/DOCX or printable formats

## Setup

1. Clone the repository.
2. Create a Python virtual environment.
3. Install backend dependencies.
4. Install frontend dependencies if the web interface is included.
5. Start the backend server.
6. Start the frontend app.
7. Upload study material and generate the question bank.

### Optional local ML generation with Ollama

The backend automatically uses Ollama when it is available at `http://127.0.0.1:11434`. Install Ollama, then download a model:

```bash
ollama pull llama3.2
```

Start the backend with the project virtual environment:

```bash
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
```

The default model is `llama3.2`. To use another installed model, set `OLLAMA_MODEL`. The generator uses Ollama exclusively; there is no built-in fallback generator.

On Windows, install Ollama from `https://ollama.com/download/windows`. The installer normally adds `ollama.exe` to PATH. Open a new Command Prompt after installation, then run:

```bat
ollama pull llama3.2
ollama serve
```

If Ollama uses a non-default host or port, set `OLLAMA_HOST` before starting the backend. The value may be a full URL or `host:port`:

```bat
set OLLAMA_HOST=http://127.0.0.1:11434
set OLLAMA_MODEL=llama3.2
```

For a permanent Windows setting, use `setx OLLAMA_HOST http://127.0.0.1:11434`, then open a new terminal. The Ollama executable location and model storage location are separate: `OLLAMA_MODELS` controls model storage, while `OLLAMA_HOST` controls the API address.

```bash
git clone <repository-url>
cd Multi-Format-Quiz-Question-Bank-Generator
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
```

## Future Enhancements

- Support more question types: MCQ, true/false, short answer, descriptive
- Add multilingual question generation
- Improve Bloom taxonomy mapping
- Add validation for question quality and relevance
- Support LMS export formats
- Add better search and retrieval of question banks

## License

A license file has not been added yet. Add one if the project is intended for public or team distribution.

## Summary

This project is a document-to-assessment generator that transforms learning material into structured, reusable question banks using AI-driven processing and question generation.
