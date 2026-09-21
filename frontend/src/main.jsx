import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles.css';

async function readApiResponse(response) {
  const contentType = response.headers.get('content-type') || '';
  if (contentType.includes('application/json')) {
    return response.json();
  }

  const message = await response.text();
  throw new Error(message || `Request failed with status ${response.status}`);
}

function visibleUnit(unit) {
  if (typeof unit === 'string' && /^\s*[IVXLCDM]+\s+Introduction\s+to\b/i.test(unit)) {
    return '';
  }
  return unit || 'General';
}

function sectionHeading(section, difficulty, questionType) {
  if (questionType === 'mid_pattern') {
    return {
        instruction: section === 'Section A' ? 'Answer all questions' : 'Answer any four questions',
        marks: section === 'Section A' ? '1 x 10 = 10 marks' : '4 x 5 = 20 marks',
      difficulty,
    };
  }
  if (questionType === 'sem_pattern') {
    return {
      instruction: 'Answer all questions',
      marks: section === 'Section A' ? '2 x 5 = 10 marks' : '5 x 8 = 40 marks',
      difficulty,
    };
  }
  return { instruction: 'Answer all questions', marks: '1 x 10 = 10 marks', difficulty };
}

function App() {
  const [file, setFile] = React.useState(null);
  const [questions, setQuestions] = React.useState(() => {
    try {
      return JSON.parse(localStorage.getItem('quiz-question-paper') || '[]');
    } catch {
      return [];
    }
  });
  const [difficulty, setDifficulty] = React.useState('Medium');
  const [questionType, setQuestionType] = React.useState('quiz');
  const [loading, setLoading] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [error, setError] = React.useState('');

  React.useEffect(() => {
    localStorage.setItem('quiz-question-paper', JSON.stringify(questions));
  }, [questions]);

  const handleSubmit = async (event) => {
    event.preventDefault();

    if (!file) {
      setError('Please choose a PDF, TXT, MD, or CSV file.');
      return;
    }

    setLoading(true);
    setError('');

    const formData = new FormData();
    formData.append('file', file);
    formData.append('difficulty', difficulty);
    formData.append('question_type', questionType);
    try {
      const response = await fetch('/api/questions/generate', {
        method: 'POST',
        body: formData,
      });

      const data = await readApiResponse(response);

      if (!response.ok) {
        throw new Error(data.detail || 'Generation failed');
      }

      setQuestions(data.questions || []);
    } catch (err) {
      setError(err.message || 'Something went wrong.');
    } finally {
      setLoading(false);
    }
  };

  const clearSavedPaper = () => {
    setQuestions([]);
    setFile(null);
    localStorage.removeItem('quiz-question-paper');
  };

  const handleExport = async (format) => {
    if (questions.length === 0) {
      setError('Generate some questions before exporting the question bank.');
      return;
    }

    setExporting(true);
    setError('');
    try {
      const request = format === 'pdf'
        ? {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ questions }),
          }
        : { method: 'GET' };
      const response = await fetch(`/api/questions/export?format=${format}`, request);
      if (!response.ok) {
        const errorData = await readApiResponse(response);
        throw new Error(errorData.detail || 'Export failed');
      }
      const blob = await response.blob();
      const href = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = href;
      link.download = `question-bank.${format}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 1000);
    } catch (err) {
      setError(err.message || 'Something went wrong while exporting.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <h2>Quiz Question Bank Generator</h2>
      </header>

      <main className="container">
        <section className="panel">
          <h2>Upload study material</h2>
          <form onSubmit={handleSubmit} className="upload-form">
            <input type="file" accept=".pdf,.txt,.md,.csv" onChange={(e) => setFile(e.target.files[0])} />
            <div className="form-grid">
              <label>
                Difficulty level
                <select value={difficulty} onChange={(e) => setDifficulty(e.target.value)}>
                  <option value="Easy">Easy</option>
                  <option value="Medium">Medium</option>
                  <option value="Hard">Hard</option>
                </select>
              </label>
              <label>
                Question type
                <select value={questionType} onChange={(e) => setQuestionType(e.target.value)}>
                  <option value="quiz">Quiz questions</option>
                  <option value="fill_blank">Fill in the blanks</option>
                  <option value="mcq">Multiple choice (MCQ)</option>
                  <option value="mid_pattern">Mid exam pattern</option>
                  <option value="sem_pattern">Semester exam pattern</option>
                  <option value="all_mix">All mix types</option>
                </select>
              </label>
            </div>
            <button type="submit" disabled={loading}>
              {loading ? 'Generating...' : 'Generate Questions'}
            </button>
          </form>

          {error && <p className="error">{error}</p>}
        </section>

        <section className="panel results-panel">
          <div className="results-header">
            <h2>Generated questions</h2>
            <div className="export-buttons">
              <button type="button" className="secondary" onClick={() => handleExport('json')} disabled={questions.length === 0 || exporting}>{exporting ? 'Exporting...' : 'Export JSON'}</button>
              <button type="button" className="secondary" onClick={() => handleExport('csv')} disabled={questions.length === 0 || exporting}>Export CSV</button>
              <button type="button" className="secondary" onClick={() => handleExport('pdf')} disabled={questions.length === 0 || exporting}>Export PDF</button>
              <button type="button" className="secondary" onClick={clearSavedPaper} disabled={questions.length === 0 || exporting}>Clear Saved Paper</button>
            </div>
          </div>
          {questions.length === 0 ? (
            <p>No questions generated yet.</p>
          ) : (
            <ul className="question-list">
              {questions.map((item, index) => {
                const isExamPattern = item.question_type === 'mid_pattern' || item.question_type === 'sem_pattern';
                const startsSection = isExamPattern && (index === 0 || item.section !== questions[index - 1].section);
                const heading = startsSection ? sectionHeading(item.section, item.difficulty, item.question_type) : null;
                return (
                  <React.Fragment key={item.id}>
                    {heading && <li className="section-heading"><h3>{item.section}</h3><p>{heading.instruction}</p><p>Marks: {heading.marks}</p><p>Difficulty: {heading.difficulty}</p></li>}
                    <li className="question-item">
                      <p className="meta">{visibleUnit(item.unit) && `${visibleUnit(item.unit)} • `}{item.section || 'General'} • {item.marks || 0} marks • {item.format || item.question_type} • {item.topic} • {item.difficulty}</p>
                      <h3>{item.question_number || item.pattern_question_number || index + 1}. {item.question}</h3>
                      {item.options && <ol className="options-list">{item.options.map((option) => <li key={option}>{option}</li>)}</ol>}
                    </li>
                  </React.Fragment>
                );
              })}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
