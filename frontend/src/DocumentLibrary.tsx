import { useRef, useState } from "react";
import type { DragEvent, RefObject } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Download,
  FileText,
  LoaderCircle,
  Search,
  ShieldCheck,
  UploadCloud,
  X,
} from "lucide-react";
import type { Document, Status } from "./api";

const labels: Record<Status, string> = {
  completed: "Ready",
  failed: "Needs attention",
  queued: "Queued",
  processing: "Processing",
  retrying: "Retrying",
  submission_unknown: "Confirming",
};
const pending = (status: Status) =>
  status !== "completed" && status !== "failed";

type Props = {
  documents: Document[];
  total: number;
  offset: number;
  loading: boolean;
  hasLoaded: boolean;
  error: string;
  uploading: boolean;
  providerOff: boolean;
  uploadNote: string;
  downloading: string;
  fileInput: RefObject<HTMLInputElement | null>;
  onFiles: (files: File[]) => void;
  onRefresh: () => void;
  onPage: (offset: number) => void;
  onDismissNote: () => void;
  onDownload: (id: string, filename: string) => void;
};

export default function DocumentLibrary(props: Props) {
  const {
    documents,
    total,
    offset,
    loading,
    hasLoaded,
    error,
    uploading,
    providerOff,
    uploadNote,
    downloading,
    fileInput,
    onFiles,
    onRefresh,
    onPage,
    onDismissNote,
    onDownload,
  } = props;
  const [filter, setFilter] = useState("");
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const disabled = uploading || providerOff;
  const visible = documents.filter((d) =>
    d.filename.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()),
  );
  const summary = [
    {
      label: "Documents",
      value: total,
      scope: "Across this workspace",
      icon: FileText,
      tone: "all",
    },
    {
      label: "Ready to explore",
      value: documents.filter((d) => d.status === "completed").length,
      scope: "On this page",
      icon: CheckCircle2,
      tone: "ready",
    },
    {
      label: "In progress",
      value: documents.filter((d) => pending(d.status)).length,
      scope: "On this page",
      icon: Clock,
      tone: "pending",
    },
    {
      label: "Needs attention",
      value: documents.filter((d) => d.status === "failed").length,
      scope: "On this page",
      icon: AlertCircle,
      tone: "attention",
    },
  ];
  function isFile(event: DragEvent) {
    return Array.from(event.dataTransfer.types ?? []).includes("Files");
  }
  function enter(event: DragEvent) {
    if (!isFile(event)) return;
    event.preventDefault();
    if (!disabled) {
      dragDepth.current++;
      setDragging(true);
    }
  }
  function leave(event: DragEvent) {
    event.preventDefault();
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }
  function drop(event: DragEvent) {
    event.preventDefault();
    event.stopPropagation();
    dragDepth.current = 0;
    setDragging(false);
    if (!disabled) onFiles(Array.from(event.dataTransfer.files));
  }
  function clearFilter() {
    setFilter("");
    searchInput.current?.focus();
  }
  function page(next: number) {
    setFilter("");
    onPage(next);
  }

  return (
    <section className="document-library" aria-label="Document library">
      <div className="summary-grid" role="group" aria-label="Document summary">
        {summary.map(({ label, value, scope, icon: Icon, tone }) => (
          <div className={`summary-card ${tone}`} key={label}>
            <div className="summary-heading">
              <span>{label}</span>
              <Icon size={17} aria-hidden="true" />
            </div>
            <strong>{hasLoaded && !loading ? value : "—"}</strong>
            <small>{scope}</small>
          </div>
        ))}
      </div>
      <p className="summary-scope">
        Status counts reflect the {hasLoaded ? documents.length : "currently"}{" "}
        loaded documents, before filename filtering.
      </p>
      <input
        ref={fileInput}
        aria-label="Select PDF"
        type="file"
        accept="application/pdf,.pdf"
        className="hidden"
        disabled={disabled}
        onChange={(event) => onFiles(Array.from(event.target.files ?? []))}
      />
      <div
        role="region"
        aria-label="PDF upload"
        aria-disabled={disabled}
        className={`upload-zone${dragging && !disabled ? " is-dragging" : ""}${disabled ? " is-disabled" : ""}`}
        onDragEnter={enter}
        onDragLeave={leave}
        onDragOver={(event) => {
          if (isFile(event)) {
            event.preventDefault();
            event.dataTransfer.dropEffect = disabled ? "none" : "copy";
          }
        }}
        onDrop={drop}
      >
        <div className="upload-symbol">
          {uploading ? (
            <LoaderCircle className="spin" size={24} aria-hidden="true" />
          ) : (
            <UploadCloud size={25} aria-hidden="true" />
          )}
        </div>
        <div className="upload-copy">
          <strong>
            {uploading
              ? "Preparing & submitting your PDF"
              : providerOff
                ? "Upload is currently unavailable"
                : dragging
                  ? "Release to upload your PDF"
                  : "A little more knowledge goes a long way."}
          </strong>
          <p>
            {uploading
              ? "Your file is being prepared for validation. Ingestion status follows after acceptance."
              : providerOff
                ? "The demo provider is disabled. Existing source files are still available."
                : "Drag one PDF here, or choose a file. Up to 5 MiB · 10 pages."}
          </p>
        </div>
        <button
          type="button"
          className="secondary"
          disabled={disabled}
          onClick={() => fileInput.current?.click()}
        >
          Choose PDF <ChevronRight size={15} aria-hidden="true" />
        </button>
        {uploading && (
          <div
            className="upload-progress"
            role="progressbar"
            aria-label="Preparing and submitting PDF"
          >
            <span />
          </div>
        )}
      </div>
      {uploadNote && (
        <div className="notice upload-feedback" role="status">
          <span className="feedback-dot" aria-hidden="true" />
          <span>{uploadNote}</span>
          <button aria-label="Dismiss upload feedback" onClick={onDismissNote}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      )}
      <div className="library-toolbar">
        <div className="section-title">
          <h2>
            Document library <span>{hasLoaded ? total : "—"}</span>
          </h2>
          <p>Your original PDFs, ready when you need them.</p>
        </div>
        <div className="library-controls">
          <div
            className="filename-search"
            role="search"
            aria-label="Filter loaded documents"
          >
            <Search size={16} aria-hidden="true" />
            <label className="sr-only" htmlFor="filename-filter">
              Filter filenames on this page
            </label>
            <input
              ref={searchInput}
              id="filename-filter"
              type="search"
              placeholder="Find a filename on this page…"
              value={filter}
              aria-describedby="filter-scope"
              onChange={(event) => setFilter(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape") clearFilter();
              }}
            />
            {filter && (
              <button aria-label="Clear filename filter" onClick={clearFilter}>
                <X size={14} aria-hidden="true" />
              </button>
            )}
          </div>
          <button
            className="text-button"
            onClick={onRefresh}
            disabled={loading}
          >
            {loading ? (
              <LoaderCircle className="spin" size={14} aria-hidden="true" />
            ) : null}
            Refresh
          </button>
        </div>
      </div>
      <div className="library-scope" id="filter-scope">
        <span>Filename filter applies to this page only.</span>
        {hasLoaded && !loading && (
          <span>
            {visible.length} of {documents.length} loaded documents shown
          </span>
        )}
      </div>
      {loading ? (
        <div
          className="document-grid"
          aria-label="Loading documents"
          aria-busy="true"
        >
          {[1, 2, 3].map((n) => (
            <div className="skeleton" key={n} />
          ))}
        </div>
      ) : documents.length ? (
        visible.length ? (
          <div className="document-grid">
            {visible.map((d) => (
              <article className="document-card" key={d.document_id}>
                <div className="card-top">
                  <div className="pdf-icon">
                    <FileText size={25} aria-hidden="true" />
                    <small>PDF</small>
                  </div>
                  <span className={`status ${d.status}`}>
                    {d.status === "completed" ? (
                      <Check size={12} aria-hidden="true" />
                    ) : d.status === "failed" ? (
                      <AlertCircle size={12} aria-hidden="true" />
                    ) : (
                      <span className="status-dot" />
                    )}
                    {labels[d.status]}
                  </span>
                </div>
                <h3 title={d.filename}>{d.filename}</h3>
                <p className="file-meta">
                  <span>
                    {d.page_count} {d.page_count === 1 ? "page" : "pages"}
                  </span>
                  <span aria-hidden="true">·</span>
                  <span>
                    {d.size_bytes >= 1048576
                      ? `${(d.size_bytes / 1048576).toFixed(1)} MiB`
                      : `${(d.size_bytes / 1024).toFixed(1)} KiB`}
                  </span>
                </p>
                <p className="added-date">
                  Added{" "}
                  {new Date(d.created_at).toLocaleDateString(undefined, {
                    month: "short",
                    day: "numeric",
                    year: "numeric",
                  })}
                </p>
                <div className="card-footer">
                  <button
                    aria-label={`Download ${d.filename}`}
                    onClick={() => onDownload(d.document_id, d.filename)}
                    disabled={!!downloading}
                  >
                    {downloading === d.document_id ? (
                      <LoaderCircle
                        className="spin"
                        size={16}
                        aria-hidden="true"
                      />
                    ) : (
                      <Download size={16} aria-hidden="true" />
                    )}
                    <span>Download PDF</span>
                    <ChevronRight size={14} aria-hidden="true" />
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty filter-empty">
            <Search size={28} aria-hidden="true" />
            <h3>No filenames match your search</h3>
            <p>Try a different filename or clear this page’s filter.</p>
            <button className="secondary" onClick={clearFilter}>
              Clear filter
            </button>
          </div>
        )
      ) : error ? (
        <div className="empty">
          <AlertCircle size={30} aria-hidden="true" />
          <h3>Your library couldn’t be loaded</h3>
          <p>Refresh to try loading your documents again.</p>
          <button className="secondary" onClick={onRefresh}>
            Refresh library
          </button>
        </div>
      ) : (
        <div className="empty">
          <div className="empty-orbit">
            <FileText size={30} aria-hidden="true" />
          </div>
          <h3>Your knowledge starts here</h3>
          <p>
            Add your first PDF to this private workspace.
            <br />
            We’ll keep its original source a click away.
          </p>
          <button
            className="primary"
            disabled={disabled}
            onClick={() => fileInput.current?.click()}
          >
            <UploadCloud size={17} aria-hidden="true" />
            Choose your first PDF
          </button>
          <small>PDF only · 5 MiB · 10 pages maximum</small>
        </div>
      )}
      <div className="pagination">
        <span>
          {!hasLoaded
            ? "Waiting for document list"
            : total === 0
              ? "0 documents"
              : `${offset + 1}–${Math.min(offset + documents.length, total)} of ${total} in workspace`}
        </span>
        <button
          aria-label="Previous page"
          disabled={offset === 0 || loading}
          onClick={() => page(Math.max(0, offset - 20))}
        >
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <button
          aria-label="Next page"
          disabled={offset + 20 >= total || loading}
          onClick={() => page(offset + 20)}
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>
      <div className="library-note">
        <ShieldCheck size={15} aria-hidden="true" />
        <span>Tenant-scoped access</span>
        <span>Original source downloads</span>
        <span>Verifiable document IDs</span>
      </div>
    </section>
  );
}
