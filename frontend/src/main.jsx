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

function App() {
  const [file, setFile] = React.useState(null);
  const [questions, setQuestions] = React.useState([]);
  const [loading, setLoading] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [error, setError] = React.useState('');

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

  const handleExport = async (format) => {
    if (questions.length === 0) {
      setError('Generate some questions before exporting the question bank.');
      return;
    }

    setExporting(true);
    setError('');

    try {
      const response = await fetch(`/api/questions/export?format=${format}`);
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
      URL.revokeObjectURL(href);
    } catch (err) {
      setError(err.message || 'Something went wrong while exporting.');
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <h1>Quiz Question Bank Generator</h1>
      </header>

      <main className="container">
        <section className="panel">
          <h2>Upload study material</h2>
          <form onSubmit={handleSubmit} className="upload-form">
            <input type="file" accept=".pdf,.txt,.md,.csv" onChange={(e) => setFile(e.target.files[0])} />
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
              <button type="button" className="secondary" onClick={() => handleExport('json')} disabled={questions.length === 0 || exporting}>
                {exporting ? 'Exporting...' : 'Export JSON'}
              </button>
              <button type="button" className="secondary" onClick={() => handleExport('csv')} disabled={questions.length === 0 || exporting}>
                Export CSV
              </button>
            </div>
          </div>
          {questions.length === 0 ? (
            <p>No questions generated yet.</p>
          ) : (
            <ul className="question-list">
              {questions.map((item) => (
                <li key={item.id} className="question-item">
                  <p className="meta">{item.topic} • {item.difficulty}</p>
                  <h3>{item.question}</h3>
                  <p><strong>Answer:</strong> {item.answer}</p>
                </li>
              ))}
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
