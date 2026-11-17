import {
  BookOpen,
  Download,
  FileSearch,
  FileText,
  LoaderCircle,
  Sparkles,
} from "lucide-react";
import type { Answer } from "./api";

type Props = {
  answer: Answer | null;
  asking: boolean;
  hasError: boolean;
  downloading: string;
  onDownload: (id: string, filename: string) => void;
  onEdit: () => void;
};

export default function AnswerPanel({
  answer,
  asking,
  hasError,
  downloading,
  onDownload,
  onEdit,
}: Props) {
  if (asking)
    return (
      <div className="answer-loading" role="status">
        <div className="answer-loading-heading">
          <LoaderCircle size={19} className="spin" aria-hidden="true" />
          <strong>Finding the right passages…</strong>
        </div>
        <p>Retrieving your documents and preparing a source-backed response.</p>
        <div className="answer-skeleton" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
      </div>
    );
  if (!answer)
    return hasError ? null : (
      <div className="ask-empty">
        <BookOpen size={23} aria-hidden="true" />
        <div>
          <strong>Follow the answer back to the source.</strong>
          <p>Your answer and source citations will appear here.</p>
        </div>
      </div>
    );
  const sources = answer.citations.flatMap((c) =>
    c.sources.map((source, index) => ({
      ...source,
      citationId: c.citation_id,
      key: `${c.citation_id}-${index}`,
    })),
  );
  return (
    <article className={`answer-card${sources.length ? "" : " no-evidence"}`}>
      <div className="answer-heading">
        <h2>
          {sources.length ? (
            <Sparkles size={17} aria-hidden="true" />
          ) : (
            <FileSearch size={18} aria-hidden="true" />
          )}
          {sources.length
            ? "Answer with sources"
            : "No supporting sources returned"}
        </h2>
        {sources.length > 0 && (
          <small>
            {sources.length} source{" "}
            {sources.length === 1 ? "reference" : "references"}
          </small>
        )}
      </div>
      <div className="answer-text">{answer.answer}</div>
      {sources.length > 0 ? (
        <section
          className="source-section"
          aria-label="Answer source references"
        >
          <div className="source-heading">
            <h3>Sources</h3>
            <span>Download a PDF to verify the cited page.</span>
          </div>
          <div className="citations">
            {sources.map((source) => (
              <button
                key={source.key}
                disabled={!!downloading}
                onClick={() => onDownload(source.document_id, source.filename)}
                aria-label={`Download ${source.filename}, page ${source.page}, citation ${source.citationId}`}
              >
                <span className="citation-id">{source.citationId}</span>
                <span className="citation-copy">
                  <strong>
                    <FileText size={14} aria-hidden="true" />
                    {source.filename}
                  </strong>
                  <small>
                    <span className="page-chip">Page {source.page}</span>
                    Original source PDF
                  </small>
                </span>
                {downloading === source.document_id ? (
                  <LoaderCircle className="spin" size={17} aria-hidden="true" />
                ) : (
                  <Download size={17} aria-hidden="true" />
                )}
              </button>
            ))}
          </div>
        </section>
      ) : (
        <div className="no-evidence-help">
          <p>Try a more specific phrase that appears in your documents.</p>
          <button className="text-button" onClick={onEdit}>
            Edit question
          </button>
        </div>
      )}
    </article>
  );
}
